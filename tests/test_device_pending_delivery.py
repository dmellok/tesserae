"""A pull (REST) frame fetches its picture at its own check-in, so after a
push the newest render and the one on the glass differ.

Settings > Devices and the device page used to show the newest render under
"Showing", with the fetch time of the OLD one, and nothing said a new image
was waiting; a page left open kept the older thumbnail after a push. Now the
thumbnail is what the frame last fetched, the queued image sits under it, a
Queued line says when the frame picks it up, and thumbnail URLs carry the
render digest."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import next_update
from app.main import REPO_ROOT, create_app
from app.state.page_store import Page

DEVICE = "door"


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


@pytest.fixture
def client(app: Flask):
    c = app.test_client()
    c.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = c.post(
        "/settings/devices/add",
        data={"id": DEVICE, "kind": "esp32_client", "name": "Hallway Frame", "transport": "rest"},
    )
    assert resp.status_code == 302
    return c


def _render(
    digest: str, comp: str, *, ts: float | None = None, page_id: str = ""
) -> dict[str, Any]:
    return {
        "digest": digest,
        "ext": "bin",
        "filename": f"{digest}.bin",
        "renderer_id": "esp32_bin",
        "timestamp": time.time() if ts is None else ts,
        "composition_digest": comp,
        "page_id": page_id,
    }


def _push(app: Flask, render: dict[str, Any]) -> None:
    pm = app.config["PUSH_MANAGER"]
    renders_dir: Path = app.config["RENDERS_DIR"]
    renders_dir.mkdir(parents=True, exist_ok=True)
    (renders_dir / f"{render['composition_digest']}.png").write_bytes(b"\x89PNG\r\n")
    with pm._lock:
        if pm._latest_renders.get(DEVICE) is None:
            pm._latest_renders[DEVICE] = render
        else:
            pm._replace_latest_render_locked(DEVICE, render)


def _fetch(app: Flask) -> None:
    pm = app.config["PUSH_MANAGER"]
    pm.record_frame_served(DEVICE, pm.latest_render_for(DEVICE))


def _pending(app: Flask) -> dict[str, Any]:
    with app.test_request_context():
        return next_update.pending_delivery(app.config["DEVICE_REGISTRY"].get(DEVICE))


def _devices_html(client) -> str:
    return client.get("/settings/devices").get_data(as_text=True)


# -- pending_delivery(device) ---------------------------------------------


def test_served_equals_latest_is_not_queued(app: Flask, client) -> None:
    _push(app, _render("A", "compA"))
    _fetch(app)
    out = _pending(app)
    assert out["queued"] is False
    assert out["queued_at"] is None


def test_latest_newer_than_served_is_queued(app: Flask, client) -> None:
    _push(app, _render("A", "compA"))
    _fetch(app)
    _push(app, _render("B", "compB", ts=1_700_000_000.0))
    out = _pending(app)
    assert out["queued"] is True
    assert out["queued_at"] == 1_700_000_000.0
    assert out["live"] is False


def test_never_served_is_not_queued(app: Flask, client) -> None:
    # Only /frame records a fetch; a device on /frame.bmp or a deck route
    # never would, so without one there is nothing to call queued.
    _push(app, _render("A", "compA"))
    assert _pending(app)["queued"] is False


def test_nothing_pushed_is_not_queued(app: Flask, client) -> None:
    assert _pending(app)["queued"] is False


def test_live_device_is_delivering_now(app: Flask, client, monkeypatch: pytest.MonkeyPatch) -> None:
    _push(app, _render("A", "compA"))
    monkeypatch.setattr(
        next_update,
        "reach_for_device",
        lambda device, now=None: next_update.Reach(next_update.MODE_CONNECTED, live=True),
    )
    out = _pending(app)
    assert out["queued"] is True
    assert out["live"] is True
    assert out["mode"] == next_update.MODE_CONNECTED
    html = _devices_html(client)
    assert "Delivering now" in html


def test_sleeping_device_uses_the_next_checkin(
    app: Flask, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _push(app, _render("A", "compA"))
    checkin = time.time() + 3000
    monkeypatch.setattr(
        next_update,
        "reach_for_device",
        lambda device, now=None: next_update.Reach(
            next_update.MODE_SLEEPS, next_checkin_at=checkin, gap_s=3600
        ),
    )
    out = _pending(app)
    assert out["expected_at"] == pytest.approx(checkin)
    assert out["expected_when"].startswith("~")
    assert f"next check-in {out['expected_when']}" in _devices_html(client)


def test_a_non_rest_device_is_never_queued(app: Flask, client) -> None:
    client.post(
        "/settings/devices/add",
        data={"id": "kitchen", "kind": "esp32_client", "name": "Kitchen"},
    )
    pm = app.config["PUSH_MANAGER"]
    pm._latest_renders["kitchen"] = _render("A", "compA")
    with app.test_request_context():
        device = app.config["DEVICE_REGISTRY"].get("kitchen")
        assert next_update.pending_delivery(device)["queued"] is False


# -- push manager keeps what the frame fetched -----------------------------


def test_fetched_composition_survives_a_newer_render(app: Flask, client) -> None:
    _push(app, _render("A", "compA", page_id="p1"))
    _fetch(app)
    _push(app, _render("B", "compB"))
    served = app.config["PUSH_MANAGER"].last_served_render_for(DEVICE)
    assert served is not None
    assert served["digest"] == "A"
    assert served["composition_digest"] == "compA"
    assert served["page_id"] == "p1"


def test_render_prune_keeps_the_fetched_frame(app: Flask, client) -> None:
    _push(app, _render("A", "compA"))
    _fetch(app)
    _push(app, _render("B", "compB"))
    live = app.config["PUSH_MANAGER"]._live_digests()
    assert {"A", "compA", "B", "compB"} <= live


# -- the settings row -------------------------------------------------------


def test_row_in_sync_shows_one_digest_versioned_thumbnail(app: Flask, client) -> None:
    _push(app, _render("A", "compA"))
    _fetch(app)
    html = _devices_html(client)
    assert 'src="/preview/door.png?v=compA"' in html
    assert "data-device-queued" not in html
    assert "Waiting for the frame" not in html


def test_row_queued_shows_the_glass_and_the_queued_image(app: Flask, client) -> None:
    _push(app, _render("A", "compA", ts=time.time() - 3600))
    _fetch(app)
    _push(app, _render("B", "compB"))
    html = _devices_html(client)
    # Big thumbnail: what the frame holds. Small one: what is waiting.
    assert re.search(r'src="/renders/compA\.png\?w=\d+"', html)
    assert 'src="/preview/door.png?v=compB"' in html
    assert "data-device-queued-thumb" in html
    assert "Waiting for the frame" in html
    assert "New image queued at" in html
    # Showing describes the glass, not the queued image.
    showing = re.search(r"Showing</span>\s*<span[^>]*>([^<]+)</span>\s*<span[^>]*>([^<]*)", html)
    assert showing is not None
    assert showing.group(1) == "Last image"
    assert showing.group(2).startswith("fetched ")


def test_row_never_fetched_shows_the_latest_render(app: Flask, client) -> None:
    _push(app, _render("A", "compA"))
    html = _devices_html(client)
    assert 'src="/preview/door.png?v=compA"' in html
    assert "Queued, not fetched yet" not in html
    assert "Waiting for the frame" not in html


def test_row_names_the_page_that_is_on_the_glass(app: Flask, client) -> None:
    page = Page(id="album01", name="iCloud photos")
    app.config["PAGE_STORE"].save(page)
    _push(app, _render("A", "compA", page_id=page.id))
    _fetch(app)
    _push(app, _render("B", "compB"))
    html = _devices_html(client)
    assert re.search(rf"Showing</span>\s*<span[^>]*>{re.escape(page.name)}", html)


def test_device_page_head_shares_the_queued_view(app: Flask, client) -> None:
    _push(app, _render("A", "compA"))
    _fetch(app)
    _push(app, _render("B", "compB"))
    html = client.get(f"/settings/devices/{DEVICE}").get_data(as_text=True)
    assert "data-device-queued-thumb" in html
    assert "Waiting for the frame" in html
    assert 'data-render-sync="thumb:door"' in html


def test_render_state_signature_moves_with_push_and_fetch(app: Flask, client) -> None:
    def sig() -> str:
        return client.get("/settings/devices/render-state.json").get_json()["devices"][DEVICE]

    assert sig() == ""
    _push(app, _render("A", "compA"))
    first = sig()
    _fetch(app)
    second = sig()
    _push(app, _render("B", "compB"))
    third = sig()
    assert len({first, second, third}) == 3
    assert third == "B|A"
    # The same value the rendered thumbnail carries, so the poll can compare.
    assert f'data-render-sig="{third}"' in _devices_html(client)
