"""MQTT client id: a configured value wins; blank defaults to
tesserae-<hostname> so two instances on one broker don't collide; --dev
appends -dev."""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from app.main import _resolve_client_id, create_app


def test_resolve_client_id_logic() -> None:
    # A configured value wins (and gets the -dev suffix in dev mode).
    assert _resolve_client_id("mqtt-box", dev=False) == "mqtt-box"
    assert _resolve_client_id("mqtt-box", dev=True) == "mqtt-box-dev"
    # Blank / None / whitespace -> host-based default.
    default = _resolve_client_id("", dev=False)
    assert default.startswith("tesserae-") and default != "tesserae-"
    assert _resolve_client_id(None, dev=True) == f"{default}-dev"
    assert _resolve_client_id("   ", dev=False) == default


def test_transport_uses_host_client_id(app: Flask) -> None:
    cid = app.config["MQTT_TRANSPORT"]._config.client_id
    assert cid.startswith("tesserae-")
    assert not cid.endswith("-dev")


def test_dev_appends_suffix(tmp_path: Path) -> None:
    a = create_app(testing=True, dev=True, data_root=tmp_path)
    assert a.config["MQTT_TRANSPORT"]._config.client_id.endswith("-dev")
