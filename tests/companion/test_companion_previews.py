"""Companion API previews (additive `previews` feature, discussion #147).

Two read-only PNG endpoints, exercised through the real Flask app. No
Playwright: the device preview serves an existing logical-screen PNG (seeded
on disk + a fake PushManager pointing at it), and the dashboard preview's
cached path is pre-seeded while the cache-miss path only needs to return
202 (the background render is a no-op without a browser pool).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Page

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)

_CLIENT = {
    "name": "Test iPhone",
    "platform": "ios",
    "app_version": "0.1.0",
    "installation_id": "A1B2C3D4-E5F6-47A8-9012-3456789ABCDE",
}


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _token(app: Flask) -> str:
    code = app.config["COMPANION_PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/app/v1/pair",
        data=json.dumps({"code": code, "client": _CLIENT}),
        content_type="application/json",
    )
    return str(resp.get_json()["token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_device(app: Flask, device_id: str = "kitchen") -> str:
    code = app.config["PAIRING_STORE"].issue(note="d").code
    resp = app.test_client().post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": device_id, "kind": "pico_bin_client", "panel_w": 1600, "panel_h": 1200}
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return device_id


class _FakePush:
    """Returns a fixed served render pointing at a seeded device preview."""

    def __init__(
        self,
        preview_digest: object | None,
        *,
        latest_preview_digest: object | None = None,
        latest_revision: str = "latest000000001",
        previous_preview_digest: object | None = None,
        previous_revision: str = "previous0000001",
    ) -> None:
        self._served_digest = preview_digest
        self._latest_digest = (
            preview_digest if latest_preview_digest is None else latest_preview_digest
        )
        self._latest_revision = latest_revision
        self._previous_digest = previous_preview_digest
        self._previous_revision = previous_revision

    def latest_render_for(self, device_id: str) -> Any:
        if self._latest_digest is None:
            return None
        return {
            "digest": self._latest_revision,
            "preview_digest": self._latest_digest,
        }

    def last_served_render_for(self, device_id: str) -> Any:
        if self._served_digest is None:
            return None
        return {"preview_digest": self._served_digest}

    def previous_render_for(self, device_id: str) -> Any:
        if self._previous_digest is None:
            return None
        return {
            "digest": self._previous_revision,
            "preview_digest": self._previous_digest,
        }


class _FakeLegacyPush:
    def last_served_render_for(self, device_id: str) -> Any:
        return {"composition_digest": "deadbeefcafe0000"}

    def latest_render_for(self, device_id: str) -> Any:
        return {"composition_digest": "deadbeefcafe0000"}


def _seed_render(app: Flask, digest: str) -> None:
    renders = Path(app.config["RENDERS_DIR"])
    renders.mkdir(parents=True, exist_ok=True)
    (renders / f"{digest}.png").write_bytes(_PNG)


# -- device preview ------------------------------------------------------


def test_device_preview_serves_logical_frame_with_etag(app: Flask) -> None:
    device = _seed_device(app)
    _seed_render(app, "deadbeefcafe0001")
    app.config["PUSH_MANAGER"] = _FakePush("deadbeefcafe0001")
    token = _token(app)
    resp = app.test_client().get(f"/api/app/v1/devices/{device}/preview", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.get_data() == _PNG
    assert resp.headers["ETag"].strip('"') == "deadbeefcafe0001"
    assert resp.headers["Cache-Control"] == "private, no-cache"


def test_device_preview_prefers_last_served_over_newer_server_render(app: Flask) -> None:
    device = _seed_device(app)
    _seed_render(app, "deadbeefcafe0010")
    _seed_render(app, "deadbeefcafe0011")
    app.config["PUSH_MANAGER"] = _FakePush(
        "deadbeefcafe0010",
        latest_preview_digest="deadbeefcafe0011",
    )
    token = _token(app)

    resp = app.test_client().get(f"/api/app/v1/devices/{device}/preview", headers=_auth(token))

    assert resp.status_code == 200
    assert resp.headers["ETag"].strip('"') == "deadbeefcafe0010"


def test_device_preview_serves_exact_pending_revision(app: Flask) -> None:
    device = _seed_device(app)
    _seed_render(app, "deadbeefcafe0010")
    _seed_render(app, "deadbeefcafe0011")
    app.config["PUSH_MANAGER"] = _FakePush(
        "deadbeefcafe0010",
        latest_preview_digest="deadbeefcafe0011",
        latest_revision="latest000000011",
    )
    token = _token(app)

    resp = app.test_client().get(
        f"/api/app/v1/devices/{device}/preview?revision=latest000000011",
        headers=_auth(token),
    )

    assert resp.status_code == 200
    assert resp.headers["ETag"].strip('"') == "deadbeefcafe0011"


def test_device_preview_serves_superseded_advertised_revision_during_grace_window(
    app: Flask,
) -> None:
    device = _seed_device(app)
    _seed_render(app, "deadbeefcafe0014")
    app.config["PUSH_MANAGER"] = _FakePush(
        "deadbeefcafe0010",
        latest_preview_digest="deadbeefcafe0011",
        latest_revision="latest000000011",
        previous_preview_digest="deadbeefcafe0014",
        previous_revision="previous0000014",
    )
    token = _token(app)

    resp = app.test_client().get(
        f"/api/app/v1/devices/{device}/preview?revision=previous0000014",
        headers=_auth(token),
    )

    assert resp.status_code == 200
    assert resp.headers["ETag"].strip('"') == "deadbeefcafe0014"


def test_device_preview_rejects_unknown_revision(app: Flask) -> None:
    device = _seed_device(app)
    _seed_render(app, "deadbeefcafe0011")
    app.config["PUSH_MANAGER"] = _FakePush(
        "deadbeefcafe0010",
        latest_preview_digest="deadbeefcafe0011",
        latest_revision="latest000000011",
    )
    token = _token(app)

    resp = app.test_client().get(
        f"/api/app/v1/devices/{device}/preview?revision=unknown",
        headers=_auth(token),
    )

    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_device_preview_uses_server_latest_for_non_rest_transport(app: Flask) -> None:
    device_id = _seed_device(app)
    device = app.config["DEVICE_REGISTRY"].get(device_id)
    device.manifest["transport"] = "mqtt"
    _seed_render(app, "deadbeefcafe0012")
    _seed_render(app, "deadbeefcafe0013")
    app.config["PUSH_MANAGER"] = _FakePush(
        "deadbeefcafe0012",
        latest_preview_digest="deadbeefcafe0013",
    )
    token = _token(app)

    resp = app.test_client().get(
        f"/api/app/v1/devices/{device_id}/preview",
        headers=_auth(token),
    )

    assert resp.status_code == 200
    assert resp.headers["ETag"].strip('"') == "deadbeefcafe0013"


@pytest.mark.parametrize(
    "if_none_match",
    ['"deadbeefcafe0002"', 'W/"deadbeefcafe0002"', '"other", "deadbeefcafe0002"', "*"],
)
def test_device_preview_304_on_matching_etag(app: Flask, if_none_match: str) -> None:
    device = _seed_device(app)
    _seed_render(app, "deadbeefcafe0002")
    app.config["PUSH_MANAGER"] = _FakePush("deadbeefcafe0002")
    token = _token(app)
    resp = app.test_client().get(
        f"/api/app/v1/devices/{device}/preview",
        headers={**_auth(token), "If-None-Match": if_none_match},
    )
    assert resp.status_code == 304
    assert resp.headers["ETag"].strip('"') == "deadbeefcafe0002"
    assert resp.headers["Cache-Control"] == "private, no-cache"


def test_device_preview_missing_file_is_404_even_when_etag_matches(app: Flask) -> None:
    device = _seed_device(app)
    app.config["PUSH_MANAGER"] = _FakePush("deadbeefcafe0003")
    token = _token(app)
    resp = app.test_client().get(
        f"/api/app/v1/devices/{device}/preview",
        headers={**_auth(token), "If-None-Match": '"deadbeefcafe0003"'},
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("digest", ["../deadbeefcafe0004", "not-a-digest", 42])
def test_device_preview_rejects_untrusted_digest_metadata(app: Flask, digest: object) -> None:
    device = _seed_device(app)
    app.config["PUSH_MANAGER"] = _FakePush(digest)
    token = _token(app)
    resp = app.test_client().get(f"/api/app/v1/devices/{device}/preview", headers=_auth(token))
    assert resp.status_code == 404


def test_device_preview_rejects_symlink(app: Flask, tmp_path: Path) -> None:
    device = _seed_device(app)
    renders = Path(app.config["RENDERS_DIR"])
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG)
    (renders / "deadbeefcafe0005.png").symlink_to(outside)
    app.config["PUSH_MANAGER"] = _FakePush("deadbeefcafe0005")
    token = _token(app)
    resp = app.test_client().get(f"/api/app/v1/devices/{device}/preview", headers=_auth(token))
    assert resp.status_code == 404


def test_device_preview_404_when_no_frame(app: Flask) -> None:
    device = _seed_device(app)
    app.config["PUSH_MANAGER"] = _FakePush(None)
    token = _token(app)
    resp = app.test_client().get(f"/api/app/v1/devices/{device}/preview", headers=_auth(token))
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_device_preview_404_for_legacy_latest_without_logical_preview(app: Flask) -> None:
    device = _seed_device(app)
    _seed_render(app, "deadbeefcafe0000")
    app.config["PUSH_MANAGER"] = _FakeLegacyPush()
    token = _token(app)
    resp = app.test_client().get(f"/api/app/v1/devices/{device}/preview", headers=_auth(token))
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_device_preview_404_for_unknown_device(app: Flask) -> None:
    app.config["PUSH_MANAGER"] = _FakePush("x")
    token = _token(app)
    resp = app.test_client().get("/api/app/v1/devices/ghost/preview", headers=_auth(token))
    assert resp.status_code == 404


def test_device_preview_requires_devices_read_scope(app: Flask) -> None:
    device = _seed_device(app)
    token = _token(app)
    app.config["COMPANION_TOKENS"].list_active()[0].scopes = ["dashboards:read"]
    resp = app.test_client().get(f"/api/app/v1/devices/{device}/preview", headers=_auth(token))
    assert resp.status_code == 401


def test_device_preview_requires_auth(app: Flask) -> None:
    resp = app.test_client().get("/api/app/v1/devices/kitchen/preview")
    assert resp.status_code == 401


# -- dashboard preview ---------------------------------------------------


def _seed_page(app: Flask, page_id: str = "pantry") -> Page:
    page = Page(id=page_id, name="Pantry", layout_kind="grid", device_ids=[])
    app.config["PAGE_STORE"].save(page)
    return page


def _cache_preview(app: Flask, page: Page) -> str:
    """Pre-seed the composer preview cache so the cached path is exercised."""
    from app.composer import page_preview_token, preview_dims

    dims = preview_dims(page, app.config.get("DEVICE_REGISTRY"), app.config["SETTINGS_STORE"])
    token = page_preview_token(page, dims)
    cache_dir = Path(app.config["DATA_ROOT"]) / "core" / "previews"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{page.id}__{token}.png").write_bytes(_PNG)
    return token


def test_dashboard_preview_202_while_rendering(app: Flask) -> None:
    _seed_page(app)
    token = _token(app)
    resp = app.test_client().get("/api/app/v1/dashboards/pantry/preview", headers=_auth(token))
    assert resp.status_code == 202
    assert resp.headers["Retry-After"] == "2"


def test_dashboard_preview_serves_cached_png(app: Flask) -> None:
    page = _seed_page(app)
    etag = _cache_preview(app, page)
    token = _token(app)
    resp = app.test_client().get("/api/app/v1/dashboards/pantry/preview", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.get_data() == _PNG
    assert resp.headers["ETag"].strip('"') == etag


def test_dashboard_preview_304_on_matching_etag(app: Flask) -> None:
    page = _seed_page(app)
    etag = _cache_preview(app, page)
    token = _token(app)
    resp = app.test_client().get(
        "/api/app/v1/dashboards/pantry/preview",
        headers={**_auth(token), "If-None-Match": f'"{etag}"'},
    )
    assert resp.status_code == 304


def test_dashboard_preview_404_for_unknown_dashboard(app: Flask) -> None:
    token = _token(app)
    resp = app.test_client().get("/api/app/v1/dashboards/nope/preview", headers=_auth(token))
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_dashboard_preview_invalid_device_query_is_invalid_target(app: Flask) -> None:
    _seed_page(app)
    token = _token(app)
    resp = app.test_client().get(
        "/api/app/v1/dashboards/pantry/preview?device_id=ghost", headers=_auth(token)
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_target"


def test_dashboard_preview_requires_dashboards_read_scope(app: Flask) -> None:
    _seed_page(app)
    token = _token(app)
    app.config["COMPANION_TOKENS"].list_active()[0].scopes = ["devices:read"]
    resp = app.test_client().get("/api/app/v1/dashboards/pantry/preview", headers=_auth(token))
    assert resp.status_code == 401


# -- capability advertisement --------------------------------------------


def test_previews_feature_gated_on_a_browser_pool(app: Flask) -> None:
    # A pool exists (created unstarted under testing=False), so previews is
    # offered. (Not schema-validated: the vendored 0.2.0 contract predates
    # the previews feature, which is the agreed additive extension.)
    assert app.config.get("BROWSER_POOL") is not None
    body = app.test_client().get("/api/app/v1").get_json()
    assert "previews" in body["features"]
    # Without a renderer, the whole preview surface drops from the probe so
    # the client keeps its placeholder.
    app.config["BROWSER_POOL"] = None
    body = app.test_client().get("/api/app/v1").get_json()
    assert "previews" not in body["features"]
