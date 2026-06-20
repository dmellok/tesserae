"""Onboarding v0.52 Phase 2: transport choice + REST device branch.

The broker step gained a transport radio (REST default, MQTT advanced)
and the device step now shows a Pair-card flow when the user picked REST.
These tests exercise the new server-side behaviour:

- POST /onboarding/broker with transport=rest saves
  app.default_transport but does NOT touch the broker section
- POST /onboarding/broker with transport=mqtt + builtin keeps the
  existing path
- _broker_done() and is_onboarded() recognise REST users as having
  completed the broker step
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.onboarding import is_onboarded


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    # ``testing=True`` is what tests/test_onboarding.py uses. It skips
    # the embedded-amqtt-broker startup that ``save_broker`` triggers
    # via ``_rebuild_transport()``, so the MQTT-path tests don't try to
    # bind 1883 on a CI runner. The transport's wire-shape persistence
    # is what these tests are actually exercising; the broker side
    # already has its own tests.
    a = create_app(testing=True, data_root=tmp_path, devices_dir=REPO_ROOT / "devices")
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_broker_step_with_transport_rest_persists_choice_skips_broker(app: Flask) -> None:
    """REST path: save the transport choice, do NOT touch the broker
    section, redirect to the device step."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/onboarding/broker",
        data={"transport": "rest"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/onboarding/device" in resp.location

    store = app.config["SETTINGS_STORE"]
    assert store.get_section("app").get("default_transport") == "rest"
    broker = store.get_section("broker") or {}
    # No broker host, no embedded broker enabled.
    assert not broker.get("host")
    assert not broker.get("embedded_enabled")


def test_broker_step_with_transport_mqtt_builtin_still_works(app: Flask) -> None:
    """MQTT path keeps the existing behaviour: choosing the built-in
    broker enables it AND persists default_transport=mqtt for future
    device-add flows."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/onboarding/broker",
        data={"transport": "mqtt", "use_builtin": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    store = app.config["SETTINGS_STORE"]
    assert store.get_section("app").get("default_transport") == "mqtt"
    broker = store.get_section("broker")
    assert broker.get("embedded_enabled") is True


def test_broker_step_with_transport_mqtt_external_validates_host(app: Flask) -> None:
    """External MQTT broker path: missing host flashes an error and
    bounces back to the broker step (matches pre-0.52 behaviour)."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/onboarding/broker",
        data={"transport": "mqtt"},  # no use_builtin, no host
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/onboarding/broker" in resp.location


def test_is_onboarded_recognises_rest_user(app: Flask) -> None:
    """A REST install with default_transport set should not see the
    wizard again on the next visit. The legacy signal (broker host or
    embedded broker) doesn't apply because REST users never touch
    broker config."""
    store = app.config["SETTINGS_STORE"]
    assert is_onboarded(store) is False  # fresh install
    store.patch_section("app", {"default_transport": "rest"})
    assert is_onboarded(store) is True


def test_broker_step_renders_with_default_rest_for_fresh_install(app: Flask) -> None:
    """A fresh install (no settings yet) renders the broker step with
    REST pre-selected on the transport radio."""
    client = app.test_client()
    _sign_in(client)
    page = client.get("/onboarding/broker", follow_redirects=False)
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    # The transport=rest radio is checked.
    assert 'value="rest"' in body
    assert "data-transport-rest" in body
    # And the MQTT fields wrapper is hidden by default.
    assert "wizard-broker-mqtt-fields" in body
    assert "hidden" in body  # the hidden attribute on the MQTT wrapper


def test_device_step_shows_pair_card_when_transport_is_rest(app: Flask) -> None:
    """REST users see the pair-flow on the device step, NOT the
    classic discovery + add-device form."""
    client = app.test_client()
    _sign_in(client)
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"default_transport": "rest"})
    page = client.get("/onboarding/device", follow_redirects=False)
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Pair a device" in body
    assert "Issue pairing code" in body
    # And the MQTT add-by-hand form is NOT shown.
    assert "Add a device by hand" not in body
