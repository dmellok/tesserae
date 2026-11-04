"""One app with a display of every kind the device page renders differently.

Shared by the device page tests (the form-field inventory, the rail, the
field rows) and by the screenshot script used while redesigning the page.
Not a test module itself (no ``test_`` prefix): import ``build_app`` and
``KINDS``.

Each entry in ``KINDS`` is (label, device id, what it exercises). The seeded
devices cover: a Visionect sign with a fake bound sign (report, actions, frame
log), REST pull kinds (Pi PNG with its client fields, Pi .bin with the colour
gamut, Pico .bin with buttons, battery and log upload), an MQTT ESP32, TRMNL
(renderer choice, access token), CrossPoint, a touch panel, an offline-album
device, a phone-confirmed Bluetooth device, a grey-capable board with the
colour-depth prompt, and a mains Pi with a pushed render and a queued one.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from flask import Flask

from app.main import REPO_ROOT, create_app

KINDS: list[tuple[str, str, str]] = [
    ("visionect", "frame", "Visionect sign: report, Report in / Full refresh / Reboot, frame log"),
    ("pico", "kitchen", "Pico .bin over REST: buttons, battery, log upload, token"),
    ("pi_png", "study", "Pi PNG over REST: client rotation / scale / letterbox, mains"),
    ("pi_bin", "hall", "Pi .bin over REST: colour gamut"),
    ("esp32_mqtt", "porch", "ESP32 over MQTT: topics, switch to REST"),
    ("trmnl", "trmnl", "TRMNL: renderer choice, access token, refresh rate"),
    ("crosspoint", "reader", "CrossPoint e-reader"),
    ("touch", "tablet", "ESP32 with a touch panel: touch monitor, touch off"),
    ("album", "photos", "ESP32 with an offline album bound"),
    ("bluetooth", "garden", "ESP32 the phone last saw in Bluetooth mode"),
    ("grey", "xiao", "Mono board whose firmware now paints grey: the colour-depth prompt"),
    ("queued", "door", "ESP32 over REST with a fetched render and a newer one queued"),
]

_PNG_CACHE: dict[tuple[int, int, str], bytes] = {}


def _png(w: int, h: int, shade: str = "#f2f1ec") -> bytes:
    key = (w, h, shade)
    if key not in _PNG_CACHE:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (w, h), shade)
        d = ImageDraw.Draw(img)
        d.rectangle([w // 12, h // 12, w - w // 12, h // 5], fill="#1c1b19")
        for i in range(5):
            y = h // 3 + i * h // 10
            d.rectangle([w // 12, y, w // 12 + (w * (7 - i)) // 10, y + h // 30], fill="#8e8b83")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        _PNG_CACHE[key] = buf.getvalue()
    return _PNG_CACHE[key]


def _register(app: Flask, client: Any, device_id: str, kind: str, **extra: Any) -> str:
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code},
        json={"device_id": device_id, "kind": kind, **extra},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return str(resp.get_json()["device_token"])


def _status(client: Any, device_id: str, token: str, body: dict[str, Any]) -> None:
    resp = client.post(
        f"/api/v1/device/{device_id}/status",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _push(app: Flask, device_id: str, digest: str, comp: str, *, page_id: str = "") -> None:
    pm = app.config["PUSH_MANAGER"]
    renders_dir: Path = app.config["RENDERS_DIR"]
    renders_dir.mkdir(parents=True, exist_ok=True)
    dev = app.config["DEVICE_REGISTRY"].get(device_id)
    panel = dev.panel or {}
    (renders_dir / f"{comp}.png").write_bytes(
        _png(int(panel.get("w") or 800), int(panel.get("h") or 480))
    )
    render = {
        "digest": digest,
        "ext": "bin",
        "filename": f"{digest}.bin",
        "renderer_id": "esp32_bin",
        "timestamp": time.time(),
        "composition_digest": comp,
        "page_id": page_id,
    }
    with pm._lock:
        if pm._latest_renders.get(device_id) is None:
            pm._latest_renders[device_id] = render
        else:
            pm._replace_latest_render_locked(device_id, render)


def fake_visionect_service() -> SimpleNamespace:
    """A running Visionect service with one bound, connected sign."""
    now = time.time()
    return SimpleNamespace(
        running=True,
        port=11113,
        start_error=None,
        device_uuid=lambda _d: "36002000-1247-3830-3933-303600000000",
        auto_bind_open=lambda _d: False,
        sign_view=lambda _u: {
            "connected": True,
            "in_sync": True,
            "connected_at": now - 120,
            "connect_reason": "wakeup",
            "status": {
                "firmware": "6.17.4168",
                "battery_pct": 99,
                "battery_mv": 4146,
                "rssi": -58,
                "temperature_c": 21.5,
                "uptime_min": 4560,
            },
            "heartbeat_min": 30,
            "sleep_disabled": 0,
            "wifi": "HomeNet",
            "param_error": "",
            "action": {"kind": "status", "at": now - 600},
            "exchanges": [
                (now - 60 * i, "in" if i % 2 else "out", f"status battery 99% rssi -58 #{i}")
                for i in range(12, 0, -1)
            ],
        },
        request_action=lambda *_a: None,
        on_push=lambda *_a: None,
    )


def build_app(data_root: Path, *, live: bool = False) -> Flask:
    """The seeded app. ``live`` turns auth off (a browser needs no login);
    otherwise the returned app's test client is signed in via /setup."""
    from app.state.deck_model import Deck, DeckPage
    from app.state.page_store import Page

    app: Flask = create_app(
        testing=False,
        data_root=data_root,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    if live:
        store.patch_section("auth", {"disabled": True})

    # Visionect sign.
    client.post("/settings/devices/add", data={"kind": "visionect", "id": "frame", "name": "Frame"})
    # What the sign's status packets put in the status cache.
    app.config["DEVICE_STATUS"]["frame"] = {
        "received_at": time.time() - 120,
        "parsed": {"battery_pct": 99, "battery_mv": 4146, "rssi": -58,
                   "fw_version": "6.17.4168", "temperature_c": 21.5},
    }

    # Pico .bin: buttons, battery, logs, REST token.
    tok = _register(app, client, "kitchen", "pico_bin_client", panel_w=800, panel_h=480,
                    fw_version="1.41.0")
    _status(client, "kitchen", tok, {"battery_pct": 76, "battery_mv": 3980, "rssi": -61,
                                       "logs": {"schema": 1, "ring_bytes": 3072},
                                       "temperature_c": 20.4, "humidity_pct": 48})
    for i in range(5):
        client.post(
            "/api/v1/device/kitchen/log",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "text/plain"},
            data=f"[0.01{i}] wifi: connected rssi=-58\n[0.2{i}] epd: full refresh\n".encode(),
        )

    # Pi PNG and Pi .bin over REST, mains.
    tok = _register(app, client, "study", "pi_png_client", panel_w=800, panel_h=480)
    _status(client, "study", tok, {"rssi": -48})
    tok = _register(app, client, "hall", "pi_bin_client", panel_w=1600, panel_h=1200)
    _status(client, "hall", tok, {"rssi": -70, "panel_w": 1200, "panel_h": 1600})

    # ESP32 on MQTT.
    client.post("/settings/devices/add",
                data={"id": "porch", "kind": "esp32_client", "name": "Porch", "transport": "mqtt"})

    # TRMNL and CrossPoint.
    tok = _register(app, client, "trmnl", "trmnl_client", panel_w=800, panel_h=480)
    _status(client, "trmnl", tok, {"battery_pct": 54})
    tok = _register(app, client, "reader", "crosspoint_gray", panel_w=480, panel_h=800)
    _status(client, "reader", tok, {"battery_pct": 14})

    # Touch panel.
    tok = _register(app, client, "tablet", "esp32_client", panel_w=800, panel_h=480)
    _status(client, "tablet", tok, {"battery_pct": 88, "can_stay_awake": True})
    tablet = app.config["DEVICE_REGISTRY"].get("tablet")
    tablet.manifest["touch"] = True

    # Offline album.
    from app.state.album_store import Album

    tok = _register(app, client, "photos", "esp32_client")
    reg = app.config["PLUGIN_REGISTRY"]
    folder = Path(reg.get("picture_gallery").data_dir) / "family"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.jpg").write_bytes(b"img-a")
    app.config["ALBUM_STORE"].upsert(Album.model_validate({
        "id": "family", "name": "Family photos", "device_ids": ["photos"],
        "source_folder": "family", "fit": "fill",
        "playback": {"mode": "sequential", "interval_s": 1800, "repeat": "loop"},
    }))

    # Bluetooth, phone confirmed.
    client.post("/settings/devices/add",
                data={"id": "garden", "kind": "esp32_client", "name": "Garden", "transport": "rest"})
    app.config["DEVICE_FACTS"].record_mode_observation("garden", {
        "mode": "bluetooth", "source": "ios_ble", "observed_at": "2026-01-02T03:04:05Z",
        "battery": {"percent": 65, "observed_at": "2026-01-02T03:04:06Z"},
    })

    # Grey-capable mono board: the one-time colour-depth prompt.
    tok = _register(app, client, "xiao", "xiao_epaper_75", panel_w=800, panel_h=480)
    _status(client, "xiao", tok, {"battery_pct": 80, "gamuts": ["mono", "gray_4"]})

    # A REST frame that fetched one render and has a newer one queued.
    client.post("/settings/devices/add",
                data={"id": "door", "kind": "esp32_client", "name": "Hallway Frame",
                      "transport": "rest"})
    pages = app.config["PAGE_STORE"]
    pages.save(Page(id="agenda", name="Evening agenda", device_ids=["door", "kitchen"]))
    pages.save(Page(id="weather", name="Weather", device_ids=["door"]))
    app.config["DECK_STORE"].upsert(Deck(
        id="night", name="Night", device_ids=["door", "kitchen"],
        pages=[DeckPage(page_id="agenda"), DeckPage(page_id="weather")],
    ))
    pm = app.config["PUSH_MANAGER"]
    _push(app, "door", "A", "compA", page_id="agenda")
    pm.record_frame_served("door", pm.latest_render_for("door"))
    _push(app, "door", "B", "compB", page_id="weather")
    _push(app, "kitchen", "K", "compK", page_id="agenda")
    pm.record_frame_served("kitchen", pm.latest_render_for("kitchen"))
    return app
