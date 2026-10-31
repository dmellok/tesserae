"""Visionect listener port configuration through its public settings form."""

import socket
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import visionect
from app.main import create_app
from app.state.settings_store import SettingsStore


@pytest.fixture(autouse=True)
def port_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESSERAE_VISIONECT_PORT", raising=False)
    monkeypatch.delenv("TESSERAE_BIND_PORT", raising=False)
    monkeypatch.setattr(visionect, "_SERVICE", None)


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return create_app(testing=True, data_root=tmp_path)


def test_default_and_boundaries(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    assert visionect.configured_port(store) == 11113
    for port in (1024, 65535):
        store.patch_section("app", {"visionect_port": port})
        assert visionect.configured_port(store) == port


@pytest.mark.parametrize("port", ["abc", "12.5", 0, 80, 1023, 65536, -1, True, 12000.5])
def test_invalid_ports_are_refused(tmp_path: Path, port: Any) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.patch_section("app", {"visionect_port": port})
    with pytest.raises(ValueError, match=r"1024.*65535"):
        visionect.configured_port(store)


@pytest.mark.parametrize("web_port", [8765, 12000])
def test_web_port_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, web_port: int) -> None:
    if web_port != 8765:
        monkeypatch.setenv("TESSERAE_BIND_PORT", str(web_port))
    store = SettingsStore(tmp_path / "settings.json")
    store.patch_section("app", {"visionect_port": web_port})
    with pytest.raises(ValueError, match="web"):
        visionect.configured_port(store)


@pytest.mark.parametrize("port", [11113, 12457])
def test_setup_instructions_and_serial_command_use_configured_port(app: Flask, port: int) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"visionect_port": port})
    body = app.test_client().get("/settings/devices").get_data(as_text=True)
    assert "Server IP / Domain" in body
    assert f"Server port</strong>: {port}" in body
    assert f"server_tcp_set &lt;tesserae-ip&gt; {port}" in body
    assert f'value="{port}"' in body


def test_save_port_and_refuse_occupied_port(app: Flask) -> None:
    client = app.test_client()
    with socket.socket() as busy:
        busy.bind(("0.0.0.0", 0))
        port = busy.getsockname()[1]
        response = client.post(
            "/settings/visionect/port", data={"port": port}, follow_redirects=True
        )
        assert "in use" in response.get_data(as_text=True)
        assert "visionect_port" not in app.config["SETTINGS_STORE"].get_section("app")
    response = client.post("/settings/visionect/port", data={"port": port}, follow_redirects=True)
    assert response.status_code == 200
    assert app.config["SETTINGS_STORE"].get_section("app")["visionect_port"] == port
    assert "reconnect" in response.get_data(as_text=True)


def test_env_overrides_ui_setting(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_VISIONECT_PORT", "12458")
    response = app.test_client().post(
        "/settings/visionect/port", data={"port": 12459}, follow_redirects=True
    )
    assert "TESSERAE_VISIONECT_PORT" in response.get_data(as_text=True)
    assert visionect.configured_port(app.config["SETTINGS_STORE"]) == 12458
    assert "visionect_port" not in app.config["SETTINGS_STORE"].get_section("app")


def test_invalid_environment_keeps_settings_page_available(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESSERAE_VISIONECT_PORT", "bad")
    response = app.test_client().get("/settings/devices")
    assert response.status_code == 200
    assert "1024" in response.get_data(as_text=True)


def test_quickstart_documents_configurator_and_mapping() -> None:
    docs = (Path(__file__).parents[1] / "docs/quickstart/visionect.md").read_text()
    for text in (
        "Server IP / Domain",
        "Server port",
        "rebind",
        "1024",
        "visionect_port",
    ):
        assert text in docs


@pytest.mark.parametrize(
    "option,override,expected",
    [(None, "", ""), (12000, "", "12000"), (12000, "12001", "12001"), ("bad", "", "bad")],
)
def test_optional_addon_option(tmp_path: Path, option: Any, override: str, expected: str) -> None:
    import json
    import os
    import subprocess
    import sys

    options = tmp_path / "options.json"
    options.write_text(json.dumps({} if option is None else {"visionect_port": option}))
    entrypoint = (Path(__file__).parents[1] / "docker-entrypoint.sh").read_text()
    block = entrypoint.split("set -e", 1)[1].split("# Fail with a message", 1)[0]
    block = block.replace("/data/options.json", str(options))
    env = dict(
        os.environ,
        TESSERAE_VISIONECT_PORT=override,
        PATH=f"{Path(sys.executable).parent}:{os.environ['PATH']}",
    )
    result = subprocess.run(
        ["sh", "-c", block + '\nprintf "%s" "$TESSERAE_VISIONECT_PORT"'],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout == expected


@pytest.mark.parametrize("content", ["{not json", "[12000]"])
def test_malformed_addon_options_do_not_stop_the_entrypoint(tmp_path: Path, content: str) -> None:
    import os
    import subprocess
    import sys

    options = tmp_path / "options.json"
    options.write_text(content)
    entrypoint = (Path(__file__).parents[1] / "docker-entrypoint.sh").read_text()
    block = entrypoint.split("set -e", 1)[1].split("# Fail with a message", 1)[0]
    block = "set -e\n" + block.replace("/data/options.json", str(options))
    env = {k: v for k, v in os.environ.items() if k != "TESSERAE_VISIONECT_PORT"}
    env["PATH"] = f"{Path(sys.executable).parent}:{os.environ['PATH']}"
    result = subprocess.run(
        ["sh", "-c", block + '\nprintf "ok:%s" "${TESSERAE_VISIONECT_PORT:-}"'],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout == "ok:"


@pytest.mark.parametrize("width", [390, 1440])
def test_visionect_setup_in_browser(app: Flask, tmp_path: Path, width: int) -> None:
    import os
    import threading

    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        with sync_playwright() as playwright:
            executable = os.environ.get(
                "PLAYWRIGHT_CHROMIUM_EXECUTABLE", playwright.chromium.executable_path
            )
            if not Path(executable).exists():
                pytest.skip("Playwright Chromium not installed")
            browser = playwright.chromium.launch(executable_path=executable)
            try:
                page = browser.new_page(viewport={"width": width, "height": 1000})
                page.set_default_timeout(5000)
                page.goto(f"http://127.0.0.1:{server.server_port}/settings/devices")
                page.locator("[data-add-toggle]").first.click()
                page.locator('[data-segmented-btn="visionect"]').click()
                branch = page.locator('[data-transport-branch="visionect"]')
                field = branch.locator("#visionect-server-port")
                assert field.input_value() == "11113"
                field.fill("80")
                assert not field.evaluate("el => el.checkValidity()")
                field.fill(str(port))
                branch.get_by_role("button", name="Save Server port", exact=True).click()
                page.wait_for_load_state("networkidle")
                page.locator("[data-add-toggle]").first.click()
                page.locator('[data-segmented-btn="visionect"]').click()
                assert field.input_value() == str(port)
                assert f"Server port: {port}" in branch.inner_text()
                assert f"server_tcp_set <tesserae-ip> {port}" in branch.inner_text()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(tmp_path / f"visionect-{width}.png"), full_page=True)
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
