"""The access-token pop-up gives the address a device reaches the server on.

The browser's own host is the wrong thing to print there: through Home
Assistant ingress it is HA's address and port, and a Bloomin8 Canvas set up
from that text called Home Assistant instead of Tesserae (#344). The modal
reads the same detected LAN address and bind port the frame URLs use.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    app = create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    app.config["BASE_URL_FN"] = lambda: "http://192.168.0.10:8765"
    return app


def _reveal(client, kind: str) -> str:
    with client.session_transaction() as sess:
        sess["_trmnl_token_reveal"] = {
            "device_name": "Hall",
            "token": "jkyuWeCumXO7ihJfQ4Ff",
            "kind": kind,
        }
    resp = client.get("/settings/devices", follow_redirects=True)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_bloomin8_modal_uses_the_device_facing_address(app: Flask) -> None:
    html = _reveal(app.test_client(), "bloomin8_canvas")
    assert "http://192.168.0.10:8765/api/bloomin8" in html
    assert "http://localhost/api/bloomin8" not in html
    # Both shells get a command; the Windows one must not carry the Unix
    # date substitution that PowerShell cannot run.
    assert "Invoke-RestMethod" in html
    assert "ConvertTo-Json" in html


def test_trmnl_modal_uses_the_device_facing_address(app: Flask) -> None:
    html = _reveal(app.test_client(), "trmnl_client")
    assert "Server URL: <code>http://192.168.0.10:8765</code>" in html


def test_request_host_is_the_fallback_without_a_base_url(app: Flask) -> None:
    app.config["BASE_URL_FN"] = lambda: ""
    html = _reveal(app.test_client(), "bloomin8_canvas")
    # The test client's request host, which is what the template printed
    # before it knew the device-facing address.
    assert "http://localhost/api/bloomin8" in html


def test_add_device_kinds_are_listed_by_name(app: Flask) -> None:
    resp = app.test_client().get("/settings/devices", follow_redirects=True)
    html = resp.get_data(as_text=True)
    # The picker's labels, in document order, from the first kind select.
    start = html.index('id="add-device-kind"')
    end = html.index("</select>", start)
    labels = [
        line.split(">", 1)[1].split("<", 1)[0]
        for line in html[start:end].splitlines()
        if "<option" in line
    ]
    assert len(labels) > 3
    assert labels == sorted(labels, key=str.casefold)


def test_pairing_code_modal_uses_the_device_facing_address(app: Flask) -> None:
    """Under Home Assistant ingress the browser's host is HA's own address,
    so the pairing pop-up printed that instead of Tesserae's (#460)."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_rest_pairing_reveal"] = {"code": "123456", "expires_at": 0, "note": ""}
    html = client.get("/settings/devices", follow_redirects=True).get_data(as_text=True)
    modal = html.split('aria-labelledby="pair-reveal-title"', 1)[1].split("</footer>", 1)[0]
    assert "123456" in modal
    assert "Server URL: <code>http://192.168.0.10:8765</code>" in modal
    # The test client's request host, which the pop-up printed before.
    assert "http://localhost" not in modal
