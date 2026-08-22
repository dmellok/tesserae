"""The relay's refusal reason has to reach the operator (#254 follow-up).

A self-hosted relay refusing an install registration reported only
``POST https://... -> 400``. The relay had already said why, in the JSON error
body it returns, and the client parsed that body for its ``code`` and then
discarded the human-readable ``message``. The operator was left with a status
and no reason, on a component whose whole selling point is that it is small
enough to run yourself.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from app import relay_client


def _http_error(status: int, payload: object) -> urllib.error.HTTPError:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b"not json"
    return urllib.error.HTTPError(
        "https://relay.example/v1/install/register", status, "Bad Request", {}, io.BytesIO(body)
    )


def _raise(monkeypatch: pytest.MonkeyPatch, err: urllib.error.HTTPError) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise err

    monkeypatch.setattr(relay_client.urllib.request, "urlopen", _boom)


def test_the_relays_message_reaches_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _raise(
        monkeypatch,
        _http_error(
            400, {"error": {"code": "invalid_request", "message": "install_pubkey required"}}
        ),
    )
    with pytest.raises(relay_client.RelayError) as caught:
        relay_client.register_install("https://relay.example", "PUB")

    assert "install_pubkey required" in str(caught.value)
    assert "400" in str(caught.value)
    assert caught.value.code == "invalid_request"


def test_the_code_is_used_when_there_is_no_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _raise(monkeypatch, _http_error(403, {"error": {"code": "forbidden"}}))
    with pytest.raises(relay_client.RelayError) as caught:
        relay_client.register_install("https://relay.example", "PUB")

    assert "forbidden" in str(caught.value)


def test_a_non_json_error_body_still_reports_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reverse proxy refusing before the relay sees the request returns HTML,
    not our error shape. That must degrade to the status rather than raising
    inside the error handler."""
    _raise(monkeypatch, _http_error(502, None))
    with pytest.raises(relay_client.RelayError) as caught:
        relay_client.register_install("https://relay.example", "PUB")

    assert "502" in str(caught.value)


def test_a_long_message_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The message is rendered into a flash on the Settings page; an unbounded
    string from a relay someone else operates does not belong there."""
    _raise(monkeypatch, _http_error(400, {"error": {"code": "x", "message": "y" * 5000}}))
    with pytest.raises(relay_client.RelayError) as caught:
        relay_client.register_install("https://relay.example", "PUB")

    assert len(str(caught.value)) < 400
