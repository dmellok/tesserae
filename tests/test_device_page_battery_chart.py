"""The device page's Power section embeds the Batteries page's chart for that
device (same component), and leaves it out when there is no history."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


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
def client(app: Flask) -> Any:
    c = app.test_client()
    c.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = c.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "porch", "kind": "esp32_client"}),
    )
    assert resp.status_code == 201
    return c


def _power(client: Any) -> str:
    body = str(client.get("/settings/devices/porch").get_data(as_text=True))
    start = body.index('id="power"')
    return body[start : body.index("</section>", start)]


def test_power_section_shows_the_battery_chart_with_history(app: Flask, client: Any) -> None:
    store = app.config["BATTERY_HISTORY"]
    now = time.time()
    for i in range(12):
        store.record("porch", pct=90 - i, battery_mv=4100 - i * 5, timestamp=now - (12 - i) * 1800)
    power = _power(client)
    assert 'id="battery-history"' in power
    assert "battery-chart" in power
    assert "All batteries" in power
    body = client.get("/settings/devices/porch").get_data(as_text=True)
    assert "battery-chart.js" in body


def test_power_section_without_history_has_no_chart(client: Any) -> None:
    power = _power(client)
    assert 'id="battery-history"' not in power
