"""Template share + browse/install routes (experiment-gated, online-gated)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app import online
from app.state.page_store import Page
from app.state.panel_store import CanvasLayout, Element

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake"


def _enable(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_TEMPLATES", "1")
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": True})


def _save_canvas(app: Flask, page_id: str = "cv1") -> Page:
    page = Page(
        id=page_id,
        name="Shareable",
        layout_kind="canvas",
        canvas=CanvasLayout(
            w=400,
            h=300,
            els=[Element(id="t1", kind="text", text="hello", x=0, y=0, w=100, h=40)],
        ),
    )
    app.config["PAGE_STORE"].save(page)
    return page


def _doc_payload(requires: list[str] | None = None) -> dict[str, Any]:
    return {
        "slug": "shareable-abc123",
        "author": {"name": "amber-heron-42", "sponsor": False},
        "template": {
            "schema_version": 1,
            "title": "Shareable",
            "requires": requires or [],
            "inputs": [
                {
                    "name": "city",
                    "label": "City",
                    "type": "string",
                    "targets": [{"el": "w1", "slot": "options", "key": "location"}],
                }
            ],
            "canvas": {
                "w": 400,
                "h": 300,
                "els": [
                    {
                        "id": "w1",
                        "kind": "widget",
                        "widget": "clock",
                        "options": {},
                        "x": 0,
                        "y": 0,
                        "w": 200,
                        "h": 200,
                    }
                ],
            },
        },
    }


# -- gating ---------------------------------------------------------------


def test_share_routes_404_when_experiment_off(app: Flask) -> None:
    assert app.test_client().post("/panels/c/cv1/share/prepare").status_code == 404
    assert app.test_client().get("/plugins/templates/index.json").status_code == 404


def test_market_403_when_offline(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_TEMPLATES", "1")
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": False})
    assert app.test_client().get("/plugins/templates/index.json").status_code == 403


# -- share ----------------------------------------------------------------


def test_prepare_returns_dialog_payload(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    _save_canvas(app)
    monkeypatch.setattr(
        "app.template_share_routes._quality", lambda page_id, page: {"available": False}
    )
    monkeypatch.setattr(
        online,
        "fetch_template_author",
        lambda install_id: {"name": "amber-heron-42", "sponsor": False},
    )
    body = app.test_client().post("/panels/c/cv1/share/prepare").get_json()
    assert body["blocking"] == []
    assert body["template"]["title"] == "Shareable"
    assert body["online"] is True
    assert body["author"]["name"] == "amber-heron-42"


def test_submit_rebuilds_and_posts(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    _save_canvas(app)
    sent: dict[str, Any] = {}

    def fake_submit(template: Any, png_b64: str, install_id: Any, version: Any) -> dict[str, Any]:
        sent["template"] = template
        sent["png_b64"] = png_b64
        return {"status": "pending", "id": "s1", "slug": "shareable-s1", "author": {}}

    monkeypatch.setattr(online, "submit_template", fake_submit)
    monkeypatch.setattr("app.mcp_api._render_png", lambda page_id, layout, fresh=False: _FAKE_PNG)
    resp = app.test_client().post(
        "/panels/c/cv1/share/submit",
        json={"title": "My Shared Board", "description": "desc", "tags": ["clock"], "inputs": []},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "pending"
    assert sent["template"]["title"] == "My Shared Board"
    assert sent["template"]["tags"] == ["clock"]
    # The dialog can't smuggle canvas content: it comes from the stored page.
    assert sent["template"]["canvas"]["els"][0]["text"] == "hello"


def test_submit_rejects_bad_inputs(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    _save_canvas(app)
    resp = app.test_client().post(
        "/panels/c/cv1/share/submit",
        json={
            "title": "T",
            "inputs": [
                {
                    "name": "x",
                    "label": "X",
                    "type": "string",
                    "targets": [{"el": "ghost", "slot": "options", "key": "k"}],
                }
            ],
        },
    )
    assert resp.status_code == 400
    assert "unknown element" in resp.get_json()["error"]


def test_submit_surfaces_server_rejection(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    _save_canvas(app)
    monkeypatch.setattr("app.mcp_api._render_png", lambda page_id, layout, fresh=False: _FAKE_PNG)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise online.TemplateSubmitError("daily submission limit reached")

    monkeypatch.setattr(online, "submit_template", boom)
    resp = app.test_client().post("/panels/c/cv1/share/submit", json={"title": "T"})
    assert resp.status_code == 502
    assert "limit" in resp.get_json()["error"]


# -- browse / install -----------------------------------------------------


def test_index_annotates_missing_requires(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    catalog = {
        "templates": [
            {
                "slug": "s1",
                "title": "Board",
                "author": {"name": "a-b-01", "sponsor": True},
                "requires": ["not-installed-widget"],
                "preview_url": "/templates/preview/x.png",
            }
        ]
    }
    monkeypatch.setattr(online, "fetch_template_index", lambda: catalog)
    body = app.test_client().get("/plugins/templates/index.json").get_json()
    entry = body["templates"][0]
    assert entry["missing_requires"] == ["not-installed-widget"]
    assert entry["preview_url"].startswith(online.API_BASE)


def test_install_creates_unbound_page_with_inputs(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(app, monkeypatch)
    monkeypatch.setattr(online, "fetch_template_doc", lambda slug: _doc_payload())
    monkeypatch.setattr(online, "report_template_install", lambda *a, **k: True)
    resp = app.test_client().post(
        "/plugins/templates/install",
        json={"slug": "shareable-abc123", "inputs": {"city": "Melbourne"}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    page_id = resp.get_json()["page_id"]
    page = app.config["PAGE_STORE"].get(page_id)
    assert page.layout_kind == "canvas" and page.device_ids == []
    assert page.canvas.els[0].options["location"] == "Melbourne"


def test_install_409_on_missing_requires(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    monkeypatch.setattr(
        online, "fetch_template_doc", lambda slug: _doc_payload(requires=["fancy-widget"])
    )
    resp = app.test_client().post("/plugins/templates/install", json={"slug": "s"})
    assert resp.status_code == 409
    assert "fancy-widget" in resp.get_json()["error"]


def test_install_410_on_revoked(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)

    def revoked(slug: str) -> Any:
        raise online.TemplateRevokedError(slug)

    monkeypatch.setattr(online, "fetch_template_doc", revoked)
    resp = app.test_client().post("/plugins/templates/install", json={"slug": "s"})
    assert resp.status_code == 410


def test_install_rejects_malformed_canvas(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    payload = _doc_payload()
    payload["template"]["canvas"] = {"w": "not-an-int", "els": "nope"}
    monkeypatch.setattr(online, "fetch_template_doc", lambda slug: payload)
    resp = app.test_client().post("/plugins/templates/install", json={"slug": "s"})
    assert resp.status_code == 502
    assert "validation" in resp.get_json()["error"]


def test_missing_requirements_helper() -> None:
    from app.template_market import missing_requirements

    records = {"have-it": SimpleNamespace(kind="widget", folders=["have_it"])}
    registry = SimpleNamespace(get=lambda pid: object() if pid == "bundled" else None)
    template = {"requires": ["have-it", "bundled", "nope"]}
    assert missing_requirements(template, records, registry) == ["nope"]
