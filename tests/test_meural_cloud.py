"""Meural authentication and persistent cloud transport contracts."""

from __future__ import annotations

import io
import json
import time
import urllib.error

import pytest

from app.meural_auth import (
    AuthenticationBlocked,
    AuthResult,
    InvalidAuth,
    NetgearAuthenticator,
)
from app.meural_cloud import CloudDelivery, MeuralCloudClient, save_session
from app.secret_box import SecretBox
from app.state.settings_store import SettingsStore


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def test_exchanges_browser_cognito_result_for_meural_tokens(monkeypatch):
    requests = []
    replies = [
        {"data": {"authorizationCode": "one-use-code"}},
        {"data": {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}},
    ]

    def urlopen(request, timeout):
        requests.append(request)
        return response(replies.pop(0))

    monkeypatch.setattr("app.meural_auth.open_request", urlopen)
    result = NetgearAuthenticator("trusted-install").exchange_cognito_token("browser-token")
    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    assert result.trust_id == "trusted-install"
    assert result.expires_at > time.time() + 3500
    assert requests[0].get_header("Authorization") == "Bearer browser-token"
    assert requests[0].full_url.startswith("https://accounts2.netgear.com/api/oauth/authorize?")
    assert requests[1].full_url == "https://accounts2.netgear.com/api/oauth/token?code=one-use-code"


def test_refresh_keeps_existing_refresh_token_when_not_rotated(monkeypatch):
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        return response({"accessToken": "renewed", "expiresIn": 1800})

    monkeypatch.setattr("app.meural_auth.open_request", urlopen)
    result = NetgearAuthenticator("trusted-install").refresh("old-refresh")
    assert result.access_token == "renewed"
    assert result.refresh_token == "old-refresh"
    assert requests[0].full_url == "https://accounts2.netgear.com/api/getAccessToken"
    assert requests[0].get_header("Authorization") == "Bearer old-refresh"
    assert requests[0].get_header("Appkey") == "3ui6nklcaqoij8inrkm06gfk4s"


@pytest.mark.parametrize(
    "status,body,error",
    [
        (403, b"<html>CloudFront request blocked</html>", AuthenticationBlocked),
        (401, b'{"message":"expired"}', InvalidAuth),
    ],
)
def test_refresh_errors_are_classified_without_exposing_tokens(monkeypatch, status, body, error):
    handle = io.BytesIO(body)

    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, status, "failed", {}, handle)

    monkeypatch.setattr("app.meural_auth.open_request", urlopen)
    with pytest.raises(error) as caught:
        NetgearAuthenticator().refresh("private-refresh-token")
    assert "private-refresh-token" not in str(caught.value)
    assert handle.closed


def test_incomplete_oauth_result_is_not_accepted(monkeypatch):
    monkeypatch.setattr(
        "app.meural_auth.open_request", lambda *a, **kw: response({"access_token": "a"})
    )
    with pytest.raises(InvalidAuth):
        NetgearAuthenticator().refresh("")


@pytest.fixture
def store(tmp_path):
    return SettingsStore(tmp_path / "settings.json", secret_box=SecretBox(b"x" * 32))


def session(store, *, expiry=None):
    save_session(
        store,
        "test_frame",
        AuthResult("access", "refresh", expiry or time.time() + 3600, "trusted"),
        email="owner@example.com",
        account_id="17",
        devices=[{"id": 101, "alias": "Example frame", "localIp": "192.0.2.10"}],
    )


def test_session_is_encrypted_and_does_not_store_password(store, tmp_path):
    session(store)
    raw = (tmp_path / "settings.json").read_text()
    assert '"access"' not in raw and '"refresh"' not in raw
    cfg = store.get_section("devices")["test_frame"]
    assert cfg["meural_access_token_secret"] == "access"
    assert cfg["meural_refresh_token_secret"] == "refresh"
    assert cfg["cloud_device_id"] == "101"
    assert cfg["host"] == "192.0.2.10"
    assert "password" not in raw


def test_cloud_401_refreshes_once_and_repeats_the_original_request(store, monkeypatch):
    session(store)
    sent = []

    def urlopen(request, timeout):
        sent.append(request)
        if len(sent) == 1:
            raise urllib.error.HTTPError(request.full_url, 401, "expired", {}, io.BytesIO(b"{}"))
        if "getAccessToken" in request.full_url:
            return response({"access_token": "new-access", "refresh_token": "new-refresh"})
        return response({"data": {"id": 22}})

    monkeypatch.setattr("app.meural_auth.open_request", urlopen)
    client = MeuralCloudClient(store, "test_frame")
    assert client.upload(b"jpeg", "tesserae-test.jpg") == "22"
    assert [r.get_method() for r in sent] == ["POST", "GET", "POST"]
    assert sent[0].data == sent[2].data
    assert sent[2].get_header("Authorization") == "Token new-access"
    assert (
        store.get_section("devices")["test_frame"]["meural_refresh_token_secret"] == "new-refresh"
    )


def test_switching_account_updates_lan_address_for_the_selected_frame(store):
    session(store)
    store.update_item("devices", "test_frame", {"host": "192.0.2.99"})
    save_session(
        store,
        "test_frame",
        AuthResult("other-access", "other-refresh", time.time() + 3600, "other-trust"),
        email="other@example.com",
        account_id="18",
        devices=[{"id": 102, "alias": "Other frame", "localIp": "192.0.2.11"}],
    )
    cfg = store.get_section("devices")["test_frame"]
    assert cfg["cloud_device_id"] == "102"
    assert cfg["host"] == "192.0.2.11"
    assert cfg["meural_account_id"] == "18"


def test_expiring_session_refreshes_before_upload(store, monkeypatch):
    session(store, expiry=time.time() + 10)
    urls = []

    def urlopen(request, timeout):
        urls.append(request.full_url)
        if "getAccessToken" in request.full_url:
            return response({"access_token": "fresh"})
        return response({"data": {"id": 23}})

    monkeypatch.setattr("app.meural_auth.open_request", urlopen)
    assert MeuralCloudClient(store, "test_frame").upload(b"jpeg", "t.jpg") == "23"
    assert "getAccessToken" in urls[0]
    assert urls[1] == "https://api.meural.com/v1/items"


def test_showing_one_cloud_image_synchronizes_without_creating_a_gallery(store, monkeypatch):
    session(store)
    requests = []

    def urlopen(request, timeout):
        requests.append((request.get_method(), request.full_url))
        return response({})

    monkeypatch.setattr("app.meural_auth.open_request", urlopen)
    MeuralCloudClient(store, "test_frame").show_item("101", "30")
    assert requests == [
        ("POST", "https://api.meural.com/v1/devices/101/items/30"),
        ("POST", "https://api.meural.com/v1/devices/101/sync"),
    ]


class Cloud:
    account_id = "17"

    def __init__(self):
        self.uploads = []
        self.loaded = []
        self.deleted = []
        self.fail_load = False

    def upload(self, image, name):
        item = str(100 + len(self.uploads))
        self.uploads.append((item, image, name))
        return item

    def find_upload(self, name):
        return next((i for i, _, n in self.uploads if n == name), None)

    def show_item(self, target, item):
        if self.fail_load:
            raise OSError("offline")
        self.loaded.append((target, item))

    def delete_item(self, item):
        self.deleted.append(item)


def test_cloud_holds_one_image_without_periodic_uploads_and_survives_restart(tmp_path):
    client = Cloud()
    path = tmp_path / "delivery.json"
    delivery = CloudDelivery(path, client, min_interval_s=0)
    assert delivery.deliver(b"first", "first", "101") is True
    for _ in range(3):
        assert (
            CloudDelivery(path, client, min_interval_s=0).deliver(b"first", "first", "101") is False
        )
    assert len(client.uploads) == 1
    assert client.loaded == [("101", "100")]


def test_new_pages_show_one_item_and_only_clean_up_owned_uploads(tmp_path):
    client = Cloud()
    delivery = CloudDelivery(tmp_path / "delivery.json", client, min_interval_s=0)
    for page in ("first", "second", "third"):
        assert delivery.deliver(page.encode(), page, "101")
    assert client.deleted == ["100"]
    assert client.loaded[-1] == ("101", "102")


def test_interrupted_cloud_delivery_reuses_pending_image(tmp_path):
    client = Cloud()
    path = tmp_path / "delivery.json"
    client.fail_load = True
    with pytest.raises(OSError):
        CloudDelivery(path, client, min_interval_s=0).deliver(b"jpeg", "first", "101")
    client.fail_load = False
    assert CloudDelivery(path, client, min_interval_s=0).deliver(b"jpeg", "first", "101")
    assert len(client.uploads) == 1
    assert client.loaded == [("101", "100")]


def test_corrupt_delivery_journal_is_not_overwritten(tmp_path):
    client = Cloud()
    path = tmp_path / "delivery.json"
    path.write_text("{broken")
    with pytest.raises(ValueError, match="saved Meural delivery"):
        CloudDelivery(path, client, min_interval_s=0).deliver(b"jpeg", "first", "101")
    assert path.read_text() == "{broken"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Meural tests must replace the HTTP boundary")

    monkeypatch.setattr("app.meural_auth.open_request", blocked)


def test_release_removes_all_owned_uploads(tmp_path):
    client = Cloud()
    delivery = CloudDelivery(tmp_path / "delivery.json", client, min_interval_s=0)
    delivery.deliver(b"first", "first", "101")
    delivery.deliver(b"second", "second", "101")
    delivery.release("101")
    assert client.deleted == ["100", "101"]
    assert json.loads(delivery.path.read_text())["owned"] == []


def test_dead_refresh_is_persisted_and_not_retried_after_restart(store, monkeypatch):
    session(store, expiry=1)
    attempts = []

    def expired(self, token):
        attempts.append(token)
        raise InvalidAuth("expired")

    monkeypatch.setattr(NetgearAuthenticator, "refresh", expired)
    for _ in range(2):
        with pytest.raises(InvalidAuth):
            MeuralCloudClient(store, "test_frame").upload(b"jpeg", "t.jpg")
    assert store.get_section("devices")["test_frame"]["meural_auth_failed"] is True
    assert len(attempts) == 1
    session(store)
    assert not store.get_section("devices")["test_frame"]["meural_auth_failed"]


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationBlocked("blocked"),
        __import__("app.meural_auth", fromlist=["CannotConnect"]).CannotConnect("offline"),
    ],
)
def test_temporary_refresh_failure_backs_off_across_restart(store, monkeypatch, error):
    session(store, expiry=1)
    attempts = []

    def unavailable(self, token):
        attempts.append(token)
        raise error

    monkeypatch.setattr(NetgearAuthenticator, "refresh", unavailable)
    for _ in range(2):
        with pytest.raises(type(error)):
            MeuralCloudClient(store, "test_frame").upload(b"jpeg", "t.jpg")
    cfg = store.get_section("devices")["test_frame"]
    assert cfg["meural_auth_retry_at"] >= time.time() + 290
    assert not cfg.get("meural_auth_failed")
    assert len(attempts) == 1


def test_cloud_rate_limit_backs_off_all_requests(store, monkeypatch):
    session(store)
    attempts = []

    def limited(request, **kw):
        attempts.append(request)
        raise urllib.error.HTTPError(request.full_url, 429, "slow down", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr("app.meural_auth.open_request", limited)
    from app.meural_auth import CannotConnect

    for _ in range(2):
        with pytest.raises(CannotConnect):
            MeuralCloudClient(store, "test_frame").upload(b"jpeg", "t.jpg")
    assert len(attempts) == 1


def test_cloud_reply_has_a_size_limit(monkeypatch):
    from app.meural_auth import CannotConnect, request_json

    monkeypatch.setattr(
        "app.meural_auth.open_request",
        lambda *a, **kw: io.BytesIO(b'{"padding":"' + b" " * (1024 * 1024) + b'"}'),
    )
    with pytest.raises(CannotConnect):
        request_json("GET", "https://example.com")


@pytest.mark.parametrize("operation", ["refresh", "exchange"])
def test_auth_errors_never_expose_credentials_in_logs_or_tracebacks(monkeypatch, caplog, operation):
    import traceback

    from app.meural_auth import CannotConnect

    private = "example-password example-mfa-code example-auth-code example-access-token example-refresh-token"

    def unreachable(*args, **kwargs):
        raise urllib.error.URLError(private)

    monkeypatch.setattr("app.meural_auth.open_request", unreachable)
    with pytest.raises(CannotConnect) as caught:
        auth = NetgearAuthenticator()
        if operation == "refresh":
            auth.refresh("example-refresh-token")
        else:
            auth.exchange_cognito_token("example-access-token")
    displayed = str(caught.value) + "".join(traceback.format_exception(caught.value)) + caplog.text
    # Traceback source contains literal fixture argument names; raw upstream text must not appear.
    assert private not in displayed
    assert "URLError:" not in displayed
    assert "example-auth-code" not in str(caught.value) + caplog.text


def test_http_error_does_not_keep_provider_echoes_of_credentials(monkeypatch, caplog):
    from app.meural_auth import HttpError, request_json

    private = "example-private-password-code-token"

    def failed(request, **kw):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "rejected",
            {},
            io.BytesIO(json.dumps({"message": private}).encode()),
        )

    monkeypatch.setattr("app.meural_auth.open_request", failed)
    with pytest.raises(HttpError) as caught:
        request_json("GET", "https://example.com")
    assert private not in repr(vars(caught.value)) + str(caught.value) + caplog.text


def test_cloud_delivery_interval_persists_and_coalesces_latest(tmp_path):
    class Cloud:
        account_id = "17"

        def __init__(self):
            self.uploads = []

        def upload(self, jpeg, name):
            self.uploads.append(jpeg)
            return str(len(self.uploads))

        def show_item(self, target, item):
            pass

        def delete_item(self, item):
            pass

    cloud = Cloud()
    clock = [1000.0]

    def delivery():
        return CloudDelivery(tmp_path / "journal.json", cloud, clock=lambda: clock[0])

    assert delivery().deliver(b"first", "first", "101")
    clock[0] += 60
    assert not delivery().deliver(b"second", "second", "101")
    clock[0] += 240
    assert delivery().deliver(b"latest", "latest", "101")
    assert cloud.uploads == [b"first", b"latest"]


def test_cloud_interval_is_configurable(tmp_path):
    from unittest.mock import Mock

    cloud = Mock(account_id="17")
    cloud.upload.return_value = "1"
    clock = [1000.0]
    delivery = CloudDelivery(
        tmp_path / "journal.json", cloud, clock=lambda: clock[0], min_interval_s=600
    )
    assert delivery.deliver(b"first", "first", "101")
    clock[0] += 300
    assert not delivery.deliver(b"next", "next", "101")
    clock[0] += 300
    assert delivery.deliver(b"next", "next", "101")
