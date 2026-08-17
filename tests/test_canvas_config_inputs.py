"""A canvas dashboard's own config surface (#237).

A dashboard declares the settings it asks for (``canvas.inputs``) and gets a
settings page that reads and writes them. The point is that a dashboard an
agent built over MCP arrives configurable: whoever inherits it changes the bin
API's postcode without hunting through element drawers for the one option that
matters.

The declaration is deliberately the same shape the template format uses, so the
settings page and the questions the catalog listing asks an installer are one
declaration rather than two that drift. These lock that: the same input applied
locally and applied on install must land in the same place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import template_export
from app.main import REPO_ROOT, create_app
from app.state.panel_store import CanvasLayout, ConfigInput


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


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _enable_mcp(app: Flask) -> None:
    """The MCP surface is experiment-gated; loopback needs no token."""
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})


def _canvas_with_rest_source(inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """A code element fed by a rest_service source, which is the shape the
    reported dashboard had: an agent wired an API endpoint into a code tile."""
    return {
        "w": 600,
        "h": 400,
        "els": [
            {
                "id": "e1",
                "kind": "code",
                "x": 0,
                "y": 0,
                "w": 600,
                "h": 400,
                "html": "<p id=x></p>",
                "sources": [
                    {
                        "key": "rest_service",
                        "name": "bins",
                        "options": {"url": "https://api.example.com/bins?postcode=3000"},
                    }
                ],
            }
        ],
        "inputs": inputs,
    }


_URL_INPUT = {
    "name": "bin_api",
    "label": "Bin collection API URL",
    "type": "string",
    "secret": True,
    "mask": False,
    "targets": [{"el": "e1", "slot": "source_options", "index": 0, "key": "url"}],
}


def _make_canvas(client: Any, inputs: list[dict[str, Any]]) -> str:
    canvas_id: str = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    resp = client.post(f"/pages/canvas/c/{canvas_id}/save", json=_canvas_with_rest_source(inputs))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return canvas_id


# -- declaration round-trip ----------------------------------------------


def test_declared_inputs_survive_a_save(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    canvas_id = _make_canvas(client, [_URL_INPUT])

    doc = client.get(f"/pages/canvas/c/{canvas_id}/doc.json").get_json()

    assert [item["name"] for item in doc["inputs"]] == ["bin_api"]
    assert doc["inputs"][0]["targets"][0] == {
        "el": "e1",
        "slot": "source_options",
        "index": 0,
        "key": "url",
    }


def test_an_agent_declares_inputs_without_resending_the_layout(app: Flask) -> None:
    """patch_canvas carries inputs, so an agent that has just placed elements
    declares the dashboard's settings in one more call."""
    client = app.test_client()
    _sign_in(client)
    canvas_id = _make_canvas(client, [])
    _enable_mcp(app)

    resp = client.patch(f"/api/mcp/pages/{canvas_id}/canvas", json={"inputs": [_URL_INPUT]})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    doc = client.get(f"/api/mcp/pages/{canvas_id}/canvas").get_json()
    assert [item["name"] for item in doc["inputs"]] == ["bin_api"]
    assert len(doc["els"]) == 1  # elements untouched


def test_a_malformed_input_is_refused_with_field_errors(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    canvas_id = _make_canvas(client, [])
    _enable_mcp(app)

    resp = client.patch(
        f"/api/mcp/pages/{canvas_id}/canvas",
        json={"inputs": [{"name": "Not Valid", "label": "x"}]},
    )

    assert resp.status_code == 422
    assert resp.get_json()["error"] == "invalid config inputs"


# -- the settings page ---------------------------------------------------


def test_settings_page_prefills_from_what_the_dashboard_renders(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    canvas_id = _make_canvas(client, [_URL_INPUT])

    body = client.get(f"/pages/canvas/c/{canvas_id}/configure").get_data(as_text=True)

    assert "Bin collection API URL" in body
    assert "https://api.example.com/bins?postcode=3000" in body
    # secret + mask:false, the #237 combination: sensitive but readable.
    assert 'type="password"' not in body


def test_saving_settings_writes_through_the_declared_target(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    canvas_id = _make_canvas(client, [_URL_INPUT])
    replacement = "https://api.example.com/bins?postcode=3121"

    resp = client.post(
        f"/pages/canvas/c/{canvas_id}/configure",
        data={"opt_bin_api": replacement},
    )

    assert resp.status_code == 302
    page = app.config["PAGE_STORE"].get(canvas_id)
    assert page.canvas.els[0].sources[0].options["url"] == replacement
    # The declaration is the page's, not something an answer can rewrite.
    assert [item.name for item in page.canvas.inputs] == ["bin_api"]


def test_a_dashboard_with_no_declarations_offers_to_derive_them(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    canvas_id = _make_canvas(client, [])

    empty = client.get(f"/pages/canvas/c/{canvas_id}/configure").get_data(as_text=True)
    assert "No settings yet" in empty

    assert client.post(f"/pages/canvas/c/{canvas_id}/configure/suggest").status_code == 302

    page = app.config["PAGE_STORE"].get(canvas_id)
    # rest_service.url is a secret option, so the export sanitizer would redact
    # it and ask an installer for it; that is exactly the question worth asking
    # the owner here.
    assert page.canvas.inputs
    assert any(
        target.slot == "source_options" and target.key == "url"
        for item in page.canvas.inputs
        for target in item.targets
    )


def test_suggest_never_overwrites_an_existing_declaration(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    canvas_id = _make_canvas(client, [_URL_INPUT])

    client.post(f"/pages/canvas/c/{canvas_id}/configure/suggest")

    page = app.config["PAGE_STORE"].get(canvas_id)
    mine = [item for item in page.canvas.inputs if item.name == "bin_api"]
    assert len(mine) == 1
    assert mine[0].label == "Bin collection API URL"  # not the derived label


def test_configure_404s_for_anything_that_is_not_a_canvas(app: Flask) -> None:
    """A grid dashboard has no canvas to target, so it has no config surface."""
    from app.state.page_store import Page

    client = app.test_client()
    _sign_in(client)
    app.config["PAGE_STORE"].save(Page(id="gridpage", name="Grid", layout_kind="grid"))

    assert client.get("/pages/canvas/c/gridpage/configure").status_code == 404
    assert client.get("/pages/canvas/c/nope/configure").status_code == 404
    assert client.post("/pages/canvas/c/gridpage/configure").status_code == 404


# -- read/apply symmetry -------------------------------------------------


@pytest.mark.parametrize(
    ("slot", "extra", "read_from"),
    [
        ("options", {"key": "title"}, lambda el: el["options"]["title"]),
        ("source_url", {"index": 0}, lambda el: el["sources"][0]["url"]),
        (
            "source_header",
            {"index": 0, "key": "Authorization"},
            lambda el: el["sources"][0]["headers"]["Authorization"],
        ),
    ],
)
def test_read_inputs_is_the_inverse_of_apply_inputs(
    slot: str, extra: dict[str, Any], read_from: Any
) -> None:
    """Every slot the applier writes, the reader has to find again, or a
    settings page silently shows a blank for a value that is really set."""
    template = {
        "canvas": {
            "els": [
                {"id": "e1", "kind": "code", "options": {}, "sources": [{"key": "rest_service"}]}
            ]
        },
        "inputs": [
            {
                "name": "answer",
                "label": "Answer",
                "type": "string",
                "targets": [{"el": "e1", "slot": slot, **extra}],
            }
        ],
    }

    canvas = template_export.apply_inputs(template, {"answer": "written"})

    assert read_from(canvas["els"][0]) == "written"
    assert template_export.read_inputs(canvas, template["inputs"]) == {"answer": "written"}


def test_read_inputs_falls_back_to_the_declared_default() -> None:
    canvas = {"els": [{"id": "e1", "options": {}}]}
    inputs = [
        {
            "name": "city",
            "label": "City",
            "type": "string",
            "default": "Melbourne",
            "targets": [{"el": "e1", "slot": "options", "key": "city"}],
        }
    ]

    assert template_export.read_inputs(canvas, inputs) == {"city": "Melbourne"}


def test_inputs_are_not_part_of_the_render_payload() -> None:
    """The renderer reads resolved element options; a config declaration is
    authoring metadata and must not change what a panel draws."""
    layout = CanvasLayout(
        inputs=[ConfigInput(name="a", label="A", targets=[{"el": "e1", "key": "x"}])]
    )

    assert layout.inputs[0].name == "a"
    assert layout.els == []
