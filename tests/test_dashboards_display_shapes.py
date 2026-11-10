"""A dashboard shared by displays of different shapes shows, under each display
on the Dashboards list, that display's own ratio, picture and size, not the
largest panel's. The flat list keeps the dashboard's own panel."""

from __future__ import annotations

import hashlib
import io
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app
from app.state.page_store import Page


def _register(app: Flask, client: Any, device_id: str, w: int, h: int) -> None:
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": device_id, "kind": "esp32_client", "panel_w": w, "panel_h": h}
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


@pytest.fixture
def client(app: Flask) -> Any:
    c = app.test_client()
    c.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    # The landscape one is the larger panel, so it sets the dashboard's own shape.
    _register(app, c, "wide", 1600, 900)
    _register(app, c, "tall", 480, 800)
    app.config["PAGE_STORE"].save(Page(id="photos", name="Photos", device_ids=["wide", "tall"]))
    app.config["PAGE_STORE"].save(Page(id="clock", name="Clock", device_ids=["tall"]))
    return c


class _Rows(HTMLParser):
    """For each dashboard row: ``{group: {page id: (size text, thumb src, thumb ratio)}}``."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, dict[str, dict[str, Any]]] = {}
        self._group = ""
        self._row = ""
        self._in_size = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        cls = a.get("class", "").split()
        if "data-dash-group" in a:
            self._group = a["data-dash-group"]
        if "data-dashrow" in a:
            self._row = a["data-page-id"]
            self.rows.setdefault(self._group, {})[self._row] = {"sort": a.get("data-sort-size")}
        if self._row and "dx-dash-thumb" in cls:
            m = re.search(r"--thumb-ar:\s*(\d+)\s*/\s*(\d+)", a.get("style", ""))
            self.rows[self._group][self._row]["ar"] = (int(m[1]), int(m[2])) if m else None
        if self._row and tag == "img" and "data-dash-thumb" in a:
            self.rows[self._group][self._row]["src"] = a["src"]
        if self._row and "dx-dc-size" in cls and tag == "span" and "dx-dash-mono" in cls:
            self._in_size = True

    def handle_data(self, data: str) -> None:
        if self._in_size:
            self.rows[self._group][self._row]["size"] = data.strip()
            self._in_size = False


def _list(client: Any, view: str = "groups") -> dict[str, dict[str, dict[str, Any]]]:
    parser = _Rows()
    parser.feed(client.get(f"/pages?view={view}").get_data(as_text=True))
    return parser.rows


def _keep_previews(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from app import preview_cache

    asked: list[dict[str, Any]] = []
    monkeypatch.setattr(preview_cache, "submit", lambda **kw: asked.append(kw))
    return asked


def _composition(app: Flask, w: int, h: int, *devices: str) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "navy").save(buf, format="PNG")
    digest = hashlib.sha256(buf.getvalue()).hexdigest()[:16]
    (app.config["RENDERS_DIR"] / f"{digest}.png").write_bytes(buf.getvalue())
    app.config["EVENT_LOG"].record(
        type="push",
        source="page",
        target="photos",
        status="sent",
        digest="a" * 16,
        extra={"composition_digest": digest, "device_ids": list(devices)},
    )
    return digest


def test_each_display_group_shows_its_own_size_and_ratio(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keep_previews(monkeypatch)
    rows = _list(client)
    assert rows["wide"]["photos"]["size"] == "1600×900"
    assert rows["tall"]["photos"]["size"] == "480×800"
    assert rows["tall"]["photos"]["ar"] == (480, 800)
    assert rows["wide"]["photos"]["ar"] == (1600, 900)
    assert rows["tall"]["photos"]["sort"] == str(480 * 800)
    # A dashboard on one display is unchanged.
    assert rows["tall"]["clock"]["size"] == "480×800"


def test_each_display_is_drawn_at_its_own_size(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from urllib.parse import parse_qs

    asked = _keep_previews(monkeypatch)
    _list(client)
    drawn = {
        parse_qs(kw["query"].lstrip("&"))["device_id"][0]: (kw["width"], kw["height"])
        for kw in asked
        if kw["page_id"] == "photos" and kw.get("query")
    }
    assert drawn == {"wide": (1600, 900), "tall": (480, 800)}
    assert not [kw for kw in asked if kw["page_id"] == "clock" and kw.get("query")]


def test_the_picture_sent_to_that_display_is_its_thumbnail(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keep_previews(monkeypatch)
    sent_tall = _composition(app, 480, 800, "tall")
    sent_wide = _composition(app, 1600, 900, "wide")
    rows = _list(client)
    assert sent_tall in rows["tall"]["photos"]["src"]
    assert sent_wide in rows["wide"]["photos"]["src"]
    assert sent_tall not in rows["wide"]["photos"]["src"]


def test_the_flat_list_keeps_the_dashboards_own_panel(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keep_previews(monkeypatch)
    rows = _list(client, "all")["all"]
    assert rows["photos"]["size"] == "1600×900"
    assert rows["photos"]["ar"] == (1600, 900)
    assert "/compose/preview/" in rows["photos"]["src"] or "preview" in rows["photos"]["src"]
