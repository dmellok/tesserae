"""Auto-refreshing mirror page, ``GET /mirror/<device_id>``.

Returns a tiny HTML wrapper that embeds ``/preview/<id>.png`` with a
``<meta http-equiv="refresh">`` so a browser-based "client" (iPad,
repurposed Kindle, kiosk Chromebook, any screen with a URL bar) keeps
auto-pulling the latest frame without speaking MQTT or the
``/api/v1/device`` REST surface.

The wrapper is a few dozen lines of HTML, but the route earns its keep
by reading the device's ``sleep_interval_s`` for the default refresh
cadence, sanitising the ``refresh`` + ``rotate`` query knobs, and
sharing the same LAN-bypass auth path as ``/preview/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

from flask import Flask


@dataclass
class _StubDevice:
    """Minimum-viable shape that matches the real ``Device`` surface
    the mirror handler reads (``id``, ``name``, ``config_schema``).
    A bare ``MagicMock`` would happily provide any attribute and
    paper over real bugs (we previously had a ``device.settings``
    lookup that never existed on the real class but tests passed
    against the mock). A real dataclass with declared fields catches
    that class of drift at test time."""

    id: str
    name: str
    config_schema: dict[str, Any]


def _stub_device(
    app: Flask,
    device_id: str,
    *,
    name: str | None = None,
    sleep_interval_s: int | None = None,
) -> None:
    """Inject a fake device into the registry + sleep_interval into
    the settings store so the mirror endpoint resolves the same way
    a real install would. The sleep interval is read via the settings
    store (devices section), NOT directly off the device object."""
    device = _StubDevice(
        id=device_id,
        name=name or device_id,
        config_schema={},
    )
    registry = MagicMock()
    registry.get.side_effect = lambda did: device if did == device_id else None
    app.config["DEVICE_REGISTRY"] = registry

    if sleep_interval_s is not None:
        settings_store = app.config["SETTINGS_STORE"]
        settings_store.patch_section(
            "devices",
            {device_id: {"sleep_interval_s": sleep_interval_s}},
        )


def test_mirror_returns_html_with_meta_refresh(app: Flask) -> None:
    """Happy path: the response is HTML, carries a meta-refresh tag,
    and embeds the ``/preview/<id>.png`` URL so the actual frame
    serving stays delegated to the existing endpoint."""
    _stub_device(app, "kitchen_inky", name="Kitchen Inky", sleep_interval_s=300)
    client = app.test_client()
    resp = client.get("/mirror/kitchen_inky")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    # Refresh cadence pulled from device settings; the page reloads
    # itself every 300s without the firmware doing anything.
    assert '<meta http-equiv="refresh" content="300">' in body
    # Frame serving delegated to /preview/, not re-implemented here.
    assert "/preview/kitchen_inky.png" in body
    # Cache-busting timestamp so iOS Safari refetches even when it
    # ignores ``Cache-Control: no-store``.
    assert "?_=" in body
    # Device name surfaces in the <title> so a tab-switched user knows
    # which panel they're looking at.
    assert "Kitchen Inky" in body


def test_mirror_default_refresh_when_no_sleep_interval(app: Flask) -> None:
    """If the device has no ``sleep_interval_s`` configured, the
    refresh defaults to 60s (a sensible "fast enough to feel live but
    not hammer the server" cadence)."""
    _stub_device(app, "no_setting", sleep_interval_s=None)
    client = app.test_client()
    resp = client.get("/mirror/no_setting")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'content="60"' in body


def test_mirror_refresh_query_overrides_device_setting(app: Flask) -> None:
    """A user who wants a different cadence than the firmware (e.g.
    polish the URL in a kiosk bookmark) can pass ``?refresh=N``."""
    _stub_device(app, "kit", sleep_interval_s=900)
    client = app.test_client()
    resp = client.get("/mirror/kit?refresh=30")
    body = resp.get_data(as_text=True)
    assert 'content="30"' in body


def test_mirror_clamps_pathological_refresh_values(app: Flask) -> None:
    """``?refresh=1`` would hammer the server; ``?refresh=99999999``
    is the same as no refresh and probably a typo. Clamp both."""
    _stub_device(app, "kit")
    client = app.test_client()
    # Below the 5s floor.
    body_low = client.get("/mirror/kit?refresh=1").get_data(as_text=True)
    assert 'content="5"' in body_low
    # Above the 24h ceiling.
    body_high = client.get("/mirror/kit?refresh=99999999").get_data(as_text=True)
    assert 'content="86400"' in body_high


def test_mirror_refresh_non_int_falls_back_to_default(app: Flask) -> None:
    """A garbled ``?refresh=fast`` shouldn't 500; fall through to the
    device default (60s when nothing's set)."""
    _stub_device(app, "kit")
    client = app.test_client()
    resp = client.get("/mirror/kit?refresh=fast")
    assert resp.status_code == 200
    assert 'content="60"' in resp.get_data(as_text=True)


def test_mirror_rotate_applies_css_transform(app: Flask) -> None:
    """An iPad mounted sideways (portrait panel, landscape iPad)
    benefits from a 90° rotate. The transform happens client-side via
    CSS so the underlying frame stays untouched."""
    _stub_device(app, "kit")
    client = app.test_client()
    body = client.get("/mirror/kit?rotate=90").get_data(as_text=True)
    assert "rotate(90deg)" in body


def test_mirror_rotate_rejects_non_standard_angles(app: Flask) -> None:
    """Only the four cardinal rotations are sensible for a flat panel;
    any other value drops back to 0 (no rotation) silently."""
    _stub_device(app, "kit")
    client = app.test_client()
    body = client.get("/mirror/kit?rotate=45").get_data(as_text=True)
    assert "rotate(" not in body


def test_mirror_returns_404_for_unknown_device(app: Flask) -> None:
    """No device by that id, no mirror page. Falls through cleanly
    rather than serving an empty wrapper."""
    _stub_device(app, "exists")  # only "exists" registered
    client = app.test_client()
    resp = client.get("/mirror/does_not_exist")
    assert resp.status_code == 404


def test_mirror_rejects_invalid_device_ids(app: Flask) -> None:
    """Mirrors the validation used by ``/preview/<id>.png``: anything
    outside ``DEVICE_ID_RE`` is path-traversal / out-of-spec and gets
    a clean 404."""
    client = app.test_client()
    # Uppercase isn't allowed by DEVICE_ID_RE.
    assert client.get("/mirror/UPPER").status_code == 404


def test_mirror_carries_no_cache_header(app: Flask) -> None:
    """The HTML page itself must not be cached; the whole point is
    that the meta-refresh keeps loading a fresh page."""
    _stub_device(app, "kit")
    client = app.test_client()
    resp = client.get("/mirror/kit")
    assert resp.headers.get("Cache-Control") == "no-store, max-age=0"
