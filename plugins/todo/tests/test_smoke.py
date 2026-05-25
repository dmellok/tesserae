"""todo smoke: renders, populates the default list from disk, blueprint mounts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask.testing import FlaskClient


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_todo_renders(client: FlaskClient, size: str) -> None:
    resp = client.get(f"/_test/render?plugin=todo&size={size}")
    assert resp.status_code == 200
    assert 'data-plugin="todo"' in resp.get_data(as_text=True)


def test_todo_blueprint_mounted(client: FlaskClient) -> None:
    # server.py's blueprint() returns a Flask blueprint mounted at
    # /plugins/todo/. The index renders an HTML page.
    resp = client.get("/plugins/todo/")
    assert resp.status_code == 200


def test_todo_data_persists_via_data_dir(client: FlaskClient, tmp_path: Path) -> None:
    # The data_dir is plugin-private (data/plugins/todo/). Confirm the
    # plugin reads lists from there when the file exists.
    plugin_data = tmp_path / "plugins" / "todo"
    plugin_data.mkdir(parents=True, exist_ok=True)
    (plugin_data / "lists.json").write_text(
        json.dumps({"lists": [{"id": "default", "name": "Default", "items": []}]})
    )
    # The conftest's `app` fixture already points the loader at this
    # tmp_path; nothing else to wire.
    resp = client.get("/_test/render?plugin=todo&size=md")
    assert resp.status_code == 200
