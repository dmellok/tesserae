"""End-to-end /send page via the test client."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app
from app.push import PushResult


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


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _register_device(client, device_id: str = "esp32_demo", kind: str = "esp32_client") -> str:
    """Register an instance so the Send page's target-device picker has
    something to tick. ``send_routes`` now refuses image-sends without
    a chosen device, calling this once at the top of each test that
    posts to /send/file|url|webpage|gallery keeps the tests honest."""
    resp = client.post(
        "/settings/devices/add",
        data={"id": device_id, "kind": kind},
        follow_redirects=False,
    )
    assert resp.status_code == 302, f"device add failed: {resp.status_code} {resp.data!r}"
    return device_id


def _png_bytes(w: int = 50, h: int = 50) -> bytes:
    img = Image.new("RGB", (w, h), (0, 128, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_send_index_renders_all_tabs(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/send")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # History moved to its own /history page (top-level nav). Saved-
    # dashboard sends live on the Dashboards page (per-row Send + the
    # editor Push-now button), so only the arbitrary-input tabs live
    # on /send now.
    for slug in ("file", "url", "webpage"):
        assert f"tab-{slug}" in body
        assert f"tab={slug}" in body


def test_file_upload_invokes_push_image(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    # Mock must be installed AFTER device registration, devices_add
    # calls _rebuild_transport_fn() which constructs a fresh PushManager
    # and overwrites whatever was in app.config["PUSH_MANAGER"].
    pm = MagicMock()
    pm.push_image.return_value = PushResult(status="sent", page_id="x.png", composition_digest="d")
    app.config["PUSH_MANAGER"] = pm
    resp = client.post(
        "/send/file",
        data={"image": (io.BytesIO(_png_bytes()), "x.png"), "device_id": dev},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    pm.push_image.assert_called_once()
    _args, kwargs = pm.push_image.call_args
    assert kwargs["source_label"] == "x.png"


def test_file_upload_rejects_missing_file(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/send/file", data={}, follow_redirects=True)
    assert b"No file selected" in resp.data


def test_saved_dashboard_invokes_push(app: Flask) -> None:
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home")
    app.config["PUSH_MANAGER"] = pm

    client = app.test_client()
    _sign_in(client)
    client.post("/send/page", data={"page_id": "home"}, follow_redirects=False)
    # v0.69: user-initiated Send-page click passes bypass_coalesce=True
    # so a schedule tick for the same device can't silently supersede
    # the manual click. v0.71.x (issue #81): also force_publish=True so
    # the panel repaints even when the composition digest is bit-
    # identical to the last render.
    pm.push.assert_called_once_with("home", bypass_coalesce=True, force_publish=True)


def test_bulk_push_sends_each_selected(app: Flask) -> None:
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="x")
    app.config["PUSH_MANAGER"] = pm

    client = app.test_client()
    _sign_in(client)
    client.post("/send/pages", data={"page_ids": ["a", "b", "c"]}, follow_redirects=False)
    assert pm.push.call_count == 3
    assert {c.args[0] for c in pm.push.call_args_list} == {"a", "b", "c"}


def test_bulk_push_empty_selection_redirects(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/send/pages", data={}, follow_redirects=False)
    assert resp.status_code == 302  # error flash + back to dashboards, no 500


def test_url_invokes_push_url_image(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    pm.push_url_image.return_value = PushResult(status="sent", page_id="https://x")
    app.config["PUSH_MANAGER"] = pm
    client.post(
        "/send/url",
        data={"url": "https://example.com/img.png", "device_id": dev},
        follow_redirects=False,
    )
    pm.push_url_image.assert_called_once_with(
        "https://example.com/img.png", device_id=dev, fit=None, rotate=None
    )


def test_webpage_invokes_push_webpage_with_viewport(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    pm.push_webpage.return_value = PushResult(status="sent", page_id="https://x")
    app.config["PUSH_MANAGER"] = pm
    client.post(
        "/send/webpage",
        data={
            "url": "https://example.com",
            "viewport_w": "800",
            "viewport_h": "600",
            "device_id": dev,
        },
        follow_redirects=False,
    )
    pm.push_webpage.assert_called_once_with(
        "https://example.com",
        viewport_w=800,
        viewport_h=600,
        device_id=dev,
        fit=None,
        headers=None,
    )


def test_webpage_passes_validated_headers_through(app: Flask) -> None:
    """#234: a page behind a bearer token. The parsed map reaches the push
    manager, which scopes it to the URL's own origin."""
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    pm.push_webpage.return_value = PushResult(status="sent", page_id="https://x")
    app.config["PUSH_MANAGER"] = pm
    client.post(
        "/send/webpage",
        data={
            "url": "https://example.com",
            "viewport_w": "800",
            "viewport_h": "600",
            "device_id": dev,
            "headers": '{"Authorization": "Bearer abc"}',
        },
        follow_redirects=False,
    )
    assert pm.push_webpage.call_args.kwargs["headers"] == {"Authorization": "Bearer abc"}


def test_webpage_refuses_a_browser_managed_header_without_pushing(app: Flask) -> None:
    """The validator's message goes to the operator; nothing is rendered, so a
    typo can't burn a render or reach the panel."""
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    app.config["PUSH_MANAGER"] = pm
    resp = client.post(
        "/send/webpage",
        data={
            "url": "https://example.com",
            "device_id": dev,
            "headers": '{"Host": "evil.test"}',
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    pm.push_webpage.assert_not_called()


def test_webpage_refuses_malformed_header_json_without_pushing(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    app.config["PUSH_MANAGER"] = pm
    client.post(
        "/send/webpage",
        data={"url": "https://example.com", "device_id": dev, "headers": "not json"},
        follow_redirects=False,
    )
    pm.push_webpage.assert_not_called()


def test_the_webpage_form_renders_an_empty_headers_field(app: Flask) -> None:
    """A token echoed into the page source is how it ends up in a screenshot
    or a browser cache, so the field is deliberately never pre-filled. The
    placeholder shows the shape; the element itself has no content."""
    client = app.test_client()
    _sign_in(client)
    _register_device(client)
    body = client.get("/send?tab=webpage").get_data(as_text=True)
    assert 'name="headers"' in body
    # The textarea closes immediately: no value between the tags.
    assert "></textarea>" in body
    assert "Bearer …" in body  # placeholder only, not a stored value


def test_send_picker_lists_registered_instances_only(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add", data={"id": "esp32_lab", "kind": "esp32_client", "name": "Lab"}
    )
    body = client.get("/send").get_data(as_text=True)
    # Instance shows up in the multi-select checklist with its custom
    # display name and a checkbox carrying its id.
    assert ">Lab</span>" in body
    assert 'name="device_id"' in body
    assert 'value="esp32_lab"' in body
    assert "ESP32 client" not in body  # kind name, not offered


def test_send_with_device_id_routes_to_that_device(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/settings/devices/add", data={"id": "esp32_lab", "kind": "esp32_client"})
    pm = MagicMock()
    pm.push_url_image.return_value = PushResult(status="sent", page_id="https://x")
    app.config["PUSH_MANAGER"] = pm
    client.post(
        "/send/url",
        data={"url": "https://example.com/img.png", "device_id": "esp32_lab"},
        follow_redirects=False,
    )
    pm.push_url_image.assert_called_once_with(
        "https://example.com/img.png", device_id="esp32_lab", fit=None, rotate=None
    )


def test_send_with_only_unknown_device_ids_is_refused(app: Flask) -> None:
    """An unknown device id is dropped from the target set; if nothing
    valid survives, the send is refused with a flash error rather than
    silently falling through to the old virtual-panel fan-out (which
    rendered at the global panel preset and shipped the wrong-sized
    frame to every device)."""
    client = app.test_client()
    _sign_in(client)
    pm = MagicMock()
    app.config["PUSH_MANAGER"] = pm
    resp = client.post(
        "/send/url",
        data={"url": "https://example.com/img.png", "device_id": "ghost_device"},
        follow_redirects=True,
    )
    pm.push_url_image.assert_not_called()
    body = resp.get_data(as_text=True)
    # Either "Pick at least one" (when other devices exist but none
    # were ticked) or "No devices registered" (when none at all).
    assert "device" in body.lower() and ("Pick" in body or "No devices" in body)


def test_history_lists_event_log_rows(app: Flask, tmp_path: Path) -> None:
    log = app.config["EVENT_LOG"]
    log.record(type="push", source="page", target="home", status="sent", digest="abc")
    log.record(type="push", source="file", target="x.png", status="failed", error="boom")

    client = app.test_client()
    _sign_in(client)
    # History page moved off /send to its own top-level route in 0.29.7.
    body = client.get("/history").get_data(as_text=True)
    assert "home" in body
    assert "x.png" in body
    assert "boom" in body


def test_fetch_latest_history_shows_preview_but_disables_resend(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    log.record(
        type="push",
        source="button",
        target="panel_one",
        status="fetched",
        digest=None,
        extra={
            "button": "refresh",
            "action_spec": "fetch_latest",
            "composition_digest": "comp123",
        },
    )

    client = app.test_client()
    _sign_in(client)
    body = client.get("/history").get_data(as_text=True)

    assert "/renders/comp123.png" in body
    assert "preview only; resend from the original push" in body
    assert "fetch_latest" in body


def test_resend_invokes_republish(app: Flask) -> None:
    pm = MagicMock()
    pm.republish.return_value = PushResult(status="sent", page_id="resent")
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    _sign_in(client)
    client.post("/send/history/42/resend", follow_redirects=False)
    pm.republish.assert_called_once_with(42)


def test_delete_invokes_delete_history(app: Flask) -> None:
    pm = MagicMock()
    pm.delete_history.return_value = True
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    _sign_in(client)
    client.post("/send/history/7/delete", follow_redirects=False)
    pm.delete_history.assert_called_once_with(7)


def test_clear_history_all(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    for i in range(3):
        log.record(type="push", source="page", target=f"p{i}", status="sent")
    pm = MagicMock()
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    _sign_in(client)
    client.post("/send/history/clear", data={"older_than": ""}, follow_redirects=False)
    assert log.count() == 0
    pm.prune_orphan_renders.assert_called_once()


def test_clear_history_older_than(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    import time as _time

    import app.state.event_log as el

    log = app.config["EVENT_LOG"]
    now = _time.time()
    # An ancient row (10 days ago) and a fresh one (now).
    monkeypatch.setattr(el.time, "time", lambda: now - 10 * 86400)
    log.record(type="push", source="page", target="old", status="sent")
    monkeypatch.setattr(el.time, "time", lambda: now)
    log.record(type="push", source="page", target="fresh", status="sent")
    app.config["PUSH_MANAGER"] = MagicMock()
    client = app.test_client()
    _sign_in(client)
    # 1 day cutoff drops the ancient row, keeps the fresh one.
    client.post("/send/history/clear", data={"older_than": "1"}, follow_redirects=False)
    assert {r.target for r in log.list(type="push", limit=10)} == {"fresh"}


def test_bulk_delete_history(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    ids = [log.record(type="push", source="page", target=f"p{i}", status="sent") for i in range(3)]
    pm = MagicMock()
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/send/history/bulk-delete",
        data={"event_ids": [str(ids[0]), str(ids[2])]},
        follow_redirects=False,
    )
    assert {r.id for r in log.list(type="push", limit=10)} == {ids[1]}
    pm.prune_orphan_renders.assert_called_once()


def test_bulk_delete_history_none_selected(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    log.record(type="push", source="page", target="keep", status="sent")
    app.config["PUSH_MANAGER"] = MagicMock()
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/send/history/bulk-delete", data={}, follow_redirects=False)
    assert resp.status_code == 302
    assert log.count(type="push") == 1  # nothing deleted


def test_nav_links_to_send(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings", follow_redirects=True).get_data(as_text=True)
    assert "/send" in body


def test_root_redirects_to_send(app: Flask) -> None:
    """Once onboarded, the brand logo + a bare URL hit / and land on
    Send, the most common post-login destination. (Before onboarding,
    / lands in the setup wizard, covered in test_onboarding.)"""
    client = app.test_client()
    _sign_in(client)
    client.post("/onboarding/finish")  # mark setup complete
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location.endswith("/send")


def test_fit_threaded_through_url(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    pm.push_url_image.return_value = PushResult(status="sent", page_id="x")
    app.config["PUSH_MANAGER"] = pm
    client.post(
        "/send/url",
        data={"url": "https://example.com/i.png", "fit": "blur", "device_id": dev},
    )
    pm.push_url_image.assert_called_once_with(
        "https://example.com/i.png", device_id=dev, fit="blur", rotate=None
    )


def test_rotate_threaded_through_url(app: Flask) -> None:
    """The Send page's turn picker takes the same vocabulary as the
    webhook image push, and "none" (the default) reaches the push as
    ``None`` rather than a no-op turn the pipeline has to decode for."""
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    pm.push_url_image.return_value = PushResult(status="sent", page_id="x")
    app.config["PUSH_MANAGER"] = pm
    client.post(
        "/send/url",
        data={"url": "https://example.com/i.png", "rotate": "auto", "device_id": dev},
    )
    pm.push_url_image.assert_called_once_with(
        "https://example.com/i.png", device_id=dev, fit=None, rotate="auto"
    )
    pm.push_url_image.reset_mock()
    client.post(
        "/send/url",
        data={"url": "https://example.com/i.png", "rotate": "0", "device_id": dev},
    )
    assert pm.push_url_image.call_args.kwargs["rotate"] is None


def test_invalid_fit_falls_back_to_none(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    pm.push_url_image.return_value = PushResult(status="sent", page_id="x")
    app.config["PUSH_MANAGER"] = pm
    client.post(
        "/send/url",
        data={"url": "https://example.com/i.png", "fit": "bogus", "device_id": dev},
    )
    pm.push_url_image.assert_called_once_with(
        "https://example.com/i.png", device_id=dev, fit=None, rotate=None
    )


def test_gallery_query_shows_section(app: Flask, tmp_path: Path, monkeypatch) -> None:
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    gal = app.config["PLUGIN_REGISTRY"].get("picture_gallery").server_module
    monkeypatch.setattr(gal, "resolve_image_path", lambda f, n: img if n == "p.jpg" else None)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/send?g_folder=trips&g_file=p.jpg").get_data(as_text=True)
    assert 'id="tab-gallery"' in body
    assert 'name="g_file" value="p.jpg"' in body
    assert 'name="fit"' in body  # fit picker present


def test_send_gallery_pushes_resolved_image(app: Flask, tmp_path: Path, monkeypatch) -> None:
    img = tmp_path / "pic.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    gal = app.config["PLUGIN_REGISTRY"].get("picture_gallery").server_module
    monkeypatch.setattr(gal, "resolve_image_path", lambda f, n: img if n == "pic.jpg" else None)
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    pm.push_image.return_value = PushResult(status="sent", page_id="x")
    app.config["PUSH_MANAGER"] = pm
    client.post(
        "/send/gallery",
        data={"g_folder": "trips", "g_file": "pic.jpg", "fit": "fill", "device_id": dev},
    )
    pm.push_image.assert_called_once()
    args, kwargs = pm.push_image.call_args
    assert args[0] == b"\xff\xd8\xff"
    assert kwargs["fit"] == "fill"
    assert kwargs["device_id"] == dev


def test_send_page_survives_device_with_invalid_panel(app: Flask) -> None:
    """v0.53.2 regression: a single device with a corrupted panel
    (w=0 / h=0) used to raise pydantic ValidationError out of
    ``device_panel`` and 500 the whole /send page. After the fix,
    that one device is skipped with a warning and the rest of the
    fleet remains usable.

    Repros the 'after installing a widget, navigating to the send page
    gives me Internal Server Error' report where a discover-claim
    flow had registered an instance with panel_w=0 / panel_h=0."""
    client = app.test_client()
    _sign_in(client)
    # A good device first so we can tell apart "empty list" from "the
    # good device survived the bad one".
    good = _register_device(client, device_id="good_esp32", kind="esp32_client")

    # Inject a corrupted instance by hand. ``create_instance`` won't
    # accept w=0 today (Panel validation in panel_overrides_from_form),
    # so monkey-patch the manifest dict in place instead.
    devices = app.config["DEVICE_REGISTRY"]
    # Add a synthetic broken instance.
    from pathlib import Path as _Path

    from app.device_loader import Device

    broken_path = _Path(app.config["DEVICE_DATA_ROOT"]) / "broken.json"
    broken_manifest = {
        "id": "broken",
        "kind": "esp32_client",
        "name": "Broken device",
        "tesserae_compat": "1.x",
        "version": "0.1.0",
        "renderers": ["esp32_bin"],
        "panel": {"w": 0, "h": 0},
    }
    import json as _json

    broken_path.write_text(_json.dumps(broken_manifest))
    broken = Device(
        id="broken",
        path=broken_path,
        manifest=broken_manifest,
        module=devices.get("esp32_client").module,
        data_dir=app.config["DEVICE_DATA_ROOT"],
        kind_of="esp32_client",
    )
    devices.devices["broken"] = broken

    # GET /send should NOT 500. The good device should appear in the
    # picker; the broken one should be skipped silently.
    resp = client.get("/send", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert good in body
    # The broken device's id mustn't appear as a checkbox value.
    assert 'value="broken"' not in body
