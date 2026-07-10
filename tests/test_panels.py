"""Panels canvas editor, phase 0 (issue #60).

Covers the experiment gating, the widget-catalog endpoint, and the
data-schema helpers (declared-block catalog + fetch-result introspection).
The interactive editor lands in later phases; these lock the vertical slice
(flag -> route -> catalog -> schema) and the schema contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.panels_schema import build_catalog, catalog_entry, derive_schema


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    """App with the real bundled plugins (the catalog reads their
    data_schema) and the auth gate installed."""
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


# -- experiment gating ---------------------------------------------------


def test_editor_and_catalog_404_when_flag_off(app: Flask) -> None:
    """With the composer experiment off (the default), both routes 404, so
    the feature is invisible."""
    client = app.test_client()
    _sign_in(client)
    assert client.get("/experiments/composer/").status_code == 404
    assert client.get("/experiments/composer/catalog.json").status_code == 404


def test_editor_loads_when_flag_on(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/experiments/composer/")
    assert resp.status_code == 200
    assert b"panels/editor.js" in resp.data


def test_catalog_lists_widgets_with_data_schema(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    payload = client.get("/experiments/composer/catalog.json").get_json()
    keys = {w["key"] for w in payload["widgets"]}
    # The seeded worked examples show up; a widget without a data_schema
    # (clock_word is client-only, no fetch) does not.
    assert {"weather_now", "device_battery"} <= keys
    assert "clock_word" not in keys
    weather = next(w for w in payload["widgets"] if w["key"] == "weather_now")
    names = {f["name"] for f in weather["fields"]}
    assert {"temp", "cond", "icon"} <= names
    assert weather["sample"]["temp"] == 21


def test_catalog_requires_auth(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with the flag on, the endpoint is behind the admin gate."""
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()  # no sign-in
    resp = client.get("/experiments/composer/catalog.json", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


# -- schema helpers ------------------------------------------------------


def test_derive_schema_infers_types_and_skips_error() -> None:
    result = {
        "temp": 21,
        "ratio": 0.5,
        "cond": "Sunny",
        "flag": True,  # bool -> str (renders as state text, not a number)
        "hourly": [1, 2, 3],
        "error": "boom",  # transport channel, not a field
    }
    schema = derive_schema(result)
    by_name = {f["name"]: f["type"] for f in schema["fields"]}
    assert by_name == {
        "temp": "num",
        "ratio": "num",
        "cond": "str",
        "flag": "str",
        "hourly": "arr",
    }
    assert "error" not in by_name
    assert schema["sample"]["hourly"] == [1, 2, 3]


class _FakePlugin:
    def __init__(self, pid: str, manifest: dict[str, Any]) -> None:
        self.id = pid
        self.manifest = manifest

    @property
    def name(self) -> str:
        return str(self.manifest["name"])


class _FakeRegistry:
    def __init__(self, plugins: list[_FakePlugin]) -> None:
        self._plugins = plugins

    def widgets(self) -> list[_FakePlugin]:
        return self._plugins


def test_catalog_entry_requires_valid_fields() -> None:
    ok = _FakePlugin(
        "w",
        {
            "name": "W",
            "icon": "ph-x",
            "description": "d",
            "data_schema": {
                "color": "#123456",
                "fields": [{"name": "a", "type": "num"}, {"bad": 1}],
                "sample": {"a": 5},
            },
        },
    )
    entry = catalog_entry(ok)  # type: ignore[arg-type]
    assert entry is not None
    assert entry["key"] == "w" and entry["color"] == "#123456"
    assert [f["name"] for f in entry["fields"]] == ["a"]  # malformed field dropped

    no_schema = _FakePlugin("n", {"name": "N"})
    assert catalog_entry(no_schema) is None  # type: ignore[arg-type]

    empty = _FakePlugin("e", {"name": "E", "data_schema": {"fields": []}})
    assert catalog_entry(empty) is None  # type: ignore[arg-type]


def test_build_catalog_sorts_and_omits_schemaless() -> None:
    registry = _FakeRegistry(
        [
            _FakePlugin(
                "z", {"name": "Zed", "data_schema": {"fields": [{"name": "a", "type": "num"}]}}
            ),
            _FakePlugin(
                "a", {"name": "Alpha", "data_schema": {"fields": [{"name": "b", "type": "str"}]}}
            ),
            _FakePlugin("n", {"name": "NoSchema"}),
        ]
    )
    catalog = build_catalog(registry)  # type: ignore[arg-type]
    assert [c["key"] for c in catalog] == ["a", "z"]  # sorted by name, schemaless omitted
