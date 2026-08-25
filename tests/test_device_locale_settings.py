"""Per-device locale override (Settings -> Devices -> General), the
"let me alternate and see the diff" control: app.locale_resolve reads
Device.locale, this is where a human sets it."""

from __future__ import annotations

import json
from pathlib import Path

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
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _add_device(client, instance_id: str = "kitchen") -> None:
    client.post(
        "/settings/devices/add",
        data={"kind": "circuitpython_generic", "id": instance_id, "name": "Kitchen"},
    )


def test_saving_a_locale_persists_and_reloads(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_device(client)
    assert app.config["DEVICE_REGISTRY"].get("kitchen").locale is None

    resp = client.post("/settings/devices/kitchen/save", data={"locale": "fr"})
    assert resp.status_code == 302
    assert app.config["DEVICE_REGISTRY"].get("kitchen").locale == "fr"

    # Persisted to disk, not just the in-memory registry -- survives a
    # full reload the way a restart would exercise.
    on_disk = (Path(app.config["DATA_ROOT"]) / "devices" / "kitchen.json").read_text()
    assert '"locale": "fr"' in on_disk


def test_clearing_the_locale_reverts_to_the_app_default(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_device(client)
    client.post("/settings/devices/kitchen/save", data={"locale": "de"})
    assert app.config["DEVICE_REGISTRY"].get("kitchen").locale == "de"

    client.post("/settings/devices/kitchen/save", data={"locale": ""})
    assert app.config["DEVICE_REGISTRY"].get("kitchen").locale is None


def test_alternating_locales_changes_a_real_compose_render(app: Flask) -> None:
    """The actual "alternate and see the diff" workflow: bind a page to
    the device, flip the device's locale, and watch calendar_day's
    resolved strings change between renders without touching the page
    or the app-wide setting."""
    from app.state.page_store import Cell, Page

    client = app.test_client()
    _sign_in(client)
    _add_device(client)
    app.config["PAGE_STORE"].save(
        Page(
            id="agenda",
            name="Agenda",
            layout_kind="grid",
            device_ids=["kitchen"],
            cells=[Cell(id="c1", plugin="calendar_day", x=0, y=0, w=400, h=300, options={})],
        )
    )

    def _cell_dataset(body: str, attr: str) -> str:
        marker = f"data-{attr}="
        idx = body.index(marker) + len(marker)
        quote = body[idx]
        start = idx + 1
        end = body.index(quote, start)
        return body[start:end]

    client.post("/settings/devices/kitchen/save", data={"locale": "fr"})
    body_fr = client.get("/compose/agenda").get_data(as_text=True)
    assert _cell_dataset(body_fr, "locale") == "fr"
    assert json.loads(_cell_dataset(body_fr, "strings"))["event"] == "événement"

    client.post("/settings/devices/kitchen/save", data={"locale": "en"})
    body_en = client.get("/compose/agenda").get_data(as_text=True)
    assert _cell_dataset(body_en, "locale") == "en"
    assert json.loads(_cell_dataset(body_en, "strings"))["event"] == "event"
