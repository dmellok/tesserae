"""Device log upload and failure reporting (notes/device-log-upload-plan.md,
"Protocol v1"): the sticky ``logs`` capability, ``diag`` dedup + Events rows,
``logs.upload`` on the /status response, text/plain batch storage + pruning,
and the device page's Logs section."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.device_logs import KEEP_BYTES, KEEP_UPLOADS, MAX_UPLOAD_BYTES, DeviceLogStore

DEV = "log_panel"


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


def _paired(app: Flask, device_id: str = DEV) -> tuple[Any, str]:
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": "pico_bin_client",
                "panel_w": 800,
                "panel_h": 480,
                "fw_version": "1.41.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return client, resp.get_json()["device_token"]


def _status(client, token: str, body: dict[str, Any], device_id: str = DEV) -> dict[str, Any]:
    resp = client.post(
        f"/api/v1/device/{device_id}/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 80, **body}),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _upload(client, token: str, text: str | bytes, device_id: str = DEV):
    data = text.encode("utf-8") if isinstance(text, str) else text
    return client.post(
        f"/api/v1/device/{device_id}/log",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        data=data,
    )


CAP = {"logs": {"schema": 1, "ring_bytes": 3072}}


def _store(app: Flask) -> DeviceLogStore:
    return app.config["DEVICE_LOGS"]


def _diag_rows(app: Flask) -> list[Any]:
    rows = app.config["EVENT_LOG"].list(type="device", limit=200)
    return [r for r in rows if r.source == DEV and (r.extra or {}).get("diag")]


# -- capability --------------------------------------------------------------


def test_logs_capability_is_sticky_and_persisted(app: Flask) -> None:
    client, token = _paired(app)
    _status(client, token, CAP)
    _status(client, token, {})
    assert app.config["DEVICE_STATUS"][DEV]["logs_schema"] == 1
    assert app.config["DEVICE_FACTS"].get(DEV)["logs_schema"] == 1

    # A restart forgets the live cache; the persisted fact still gates the
    # upload request on the first beat that omits the capability.
    app.config["DEVICE_STATUS"].pop(DEV)
    _store(app).set_collect(DEV, 1)
    assert _status(client, token, {}).get("logs") == {"upload": True}


# -- diag --------------------------------------------------------------------


def test_diag_writes_one_error_row_per_failure_and_dedups_resends(app: Flask) -> None:
    client, token = _paired(app)
    diag = {"id": 1234, "paint_error": "refresh_timeout", "at": 1790000000}
    _status(client, token, {**CAP, "diag": diag})
    _status(client, token, {**CAP, "diag": diag})  # re-sent until a 2xx lands

    rows = _diag_rows(app)
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error == "paint failed: refresh_timeout"

    _status(client, token, {**CAP, "diag": {"id": 1235, "reset": "brownout", "at": 0}})
    rows = _diag_rows(app)
    assert len(rows) == 2
    assert rows[0].error == "reset: brownout"

    entry = app.config["DEVICE_STATUS"][DEV]
    assert entry["diag"]["id"] == 1235
    assert isinstance(entry["diag"]["received_at"], float)
    # Kept out of the merged heartbeat, so it can't read as a live reading.
    assert "diag" not in entry["parsed"]
    # Survives a beat without a diag, and a restart (persisted last diag).
    _status(client, token, CAP)
    assert app.config["DEVICE_STATUS"][DEV]["diag"]["id"] == 1235
    assert _store(app).last_diag(DEV)["id"] == 1235


def test_diag_without_an_id_is_ignored(app: Flask) -> None:
    client, token = _paired(app)
    resp = _status(client, token, {**CAP, "diag": {"paint_error": "init_failed"}})
    assert _diag_rows(app) == []
    assert "logs" not in resp


# -- logs.upload on /status --------------------------------------------------


def test_upload_requested_for_each_wake_of_the_collection_window(app: Flask) -> None:
    client, token = _paired(app)
    _status(client, token, CAP)
    _store(app).set_collect(DEV, 2)

    first = _status(client, token, CAP)
    assert first["logs"] == {"upload": True}
    assert "logs" not in first["config"]
    assert _store(app).remaining(DEV) == 1
    assert _status(client, token, CAP)["logs"] == {"upload": True}
    assert _store(app).remaining(DEV) == 0
    assert "logs" not in _status(client, token, CAP)


def test_upload_requested_once_for_a_new_diag(app: Flask) -> None:
    client, token = _paired(app)
    diag = {"id": 9, "paint_error": "ready_timeout", "at": 0}
    resp = _status(client, token, {**CAP, "diag": diag})
    assert resp["logs"] == {"upload": True}
    assert "logs" not in resp["config"]
    # The same report re-sent is not new: no second request.
    assert "logs" not in _status(client, token, {**CAP, "diag": diag})


def test_no_auto_upload_when_the_setting_is_off(app: Flask) -> None:
    client, token = _paired(app)
    app.config["SETTINGS_STORE"].patch_section("app", {"device_logs_auto_on_error": False})
    resp = _status(client, token, {**CAP, "diag": {"id": 3, "reset": "panic"}})
    assert "logs" not in resp
    # Still reported in Events; only the upload request is switched off.
    assert len(_diag_rows(app)) == 1


def test_no_upload_request_without_the_capability(app: Flask) -> None:
    client, token = _paired(app)
    _store(app).set_collect(DEV, 5)
    resp = _status(client, token, {"diag": {"id": 4, "paint_error": "init_failed"}})
    assert "logs" not in resp
    assert "logs" not in resp["config"]
    # The window isn't spent on a device that can't act on it.
    assert _store(app).remaining(DEV) == 5


# -- upload ------------------------------------------------------------------


def test_text_plain_batch_is_stored_with_an_events_row(app: Flask) -> None:
    client, token = _paired(app)
    batch = "# tesserae-log v1 fw=1.41.0 boot=12 reset=brownout wake=timer epoch=0\nI (12) a\nE (40) b\n"
    resp = _upload(client, token, batch)
    assert resp.status_code == 200
    assert resp.get_json() == {"status": 200, "bytes": len(batch.encode()), "stored": True}

    log_dir = Path(app.config["DATA_ROOT"]) / "core" / "device_logs" / DEV
    files = list(log_dir.glob("*.log"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == batch

    rows = app.config["EVENT_LOG"].list(type="device", limit=20)
    uploads = [r for r in rows if (r.extra or {}).get("log_upload")]
    assert uploads and uploads[0].extra["msg"] == "log uploaded, 3 lines"


def test_oversized_batch_is_413(app: Flask) -> None:
    client, token = _paired(app)
    resp = _upload(client, token, b"x" * (MAX_UPLOAD_BYTES + 1))
    assert resp.status_code == 413
    assert resp.get_json()["status"] == 413
    assert not (Path(app.config["DATA_ROOT"]) / "core" / "device_logs" / DEV).exists()


def test_json_log_line_keeps_its_single_row_meaning(app: Flask) -> None:
    client, token = _paired(app)
    resp = client.post(
        f"/api/v1/device/{DEV}/log",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"level": "warn", "msg": "panel busy timeout"}),
    )
    assert resp.status_code == 200
    assert "stored" not in resp.get_json()
    assert not (Path(app.config["DATA_ROOT"]) / "core" / "device_logs" / DEV).exists()
    rows = app.config["EVENT_LOG"].list(type="device", limit=10)
    line = [r for r in rows if r.target == "client_log" and r.source == DEV]
    assert line[0].extra["msg"] == "panel busy timeout"


def test_store_prunes_by_count_and_by_bytes(tmp_path: Path) -> None:
    store = DeviceLogStore(tmp_path)
    for i in range(KEEP_UPLOADS + 5):
        store.save_upload(DEV, f"line {i}\n".encode(), now=1_790_000_000 + i * 60)
    kept = store.list_uploads(DEV)
    assert len(kept) == KEEP_UPLOADS
    assert store.read_upload(DEV, kept[0].name) == f"line {KEEP_UPLOADS + 4}\n"
    assert store.read_upload(DEV, kept[-1].name) == "line 5\n"

    big = DeviceLogStore(tmp_path / "big")
    chunk = b"y" * (60 * 1024)
    for i in range(20):
        big.save_upload(DEV, chunk, now=1_790_000_000 + i)
    total = sum(u.bytes for u in big.list_uploads(DEV))
    assert total <= KEEP_BYTES
    assert len(big.list_uploads(DEV)) == KEEP_BYTES // len(chunk)


def test_same_second_uploads_do_not_overwrite(tmp_path: Path) -> None:
    store = DeviceLogStore(tmp_path)
    store.save_upload(DEV, b"first\n", now=1_790_000_000)
    store.save_upload(DEV, b"second\n", now=1_790_000_000)
    names = [u.name for u in store.list_uploads(DEV)]
    assert len(names) == 2
    assert store.read_upload(DEV, names[0]) == "second\n"


def test_read_upload_refuses_unsafe_names(tmp_path: Path) -> None:
    store = DeviceLogStore(tmp_path)
    store.save_upload(DEV, b"x\n")
    assert store.read_upload(DEV, "../device_log_collect.json") is None
    assert store.read_upload("../core", "x.log") is None


# -- device page -------------------------------------------------------------


def test_device_page_shows_logs_only_when_advertised(app: Flask) -> None:
    client, token = _paired(app)
    _status(client, token, {})
    body = client.get(f"/settings/devices/{DEV}").get_data(as_text=True)
    assert 'id="logs"' not in body

    _status(client, token, {**CAP, "diag": {"id": 5, "paint_error": "refresh_timeout"}})
    body = client.get(f"/settings/devices/{DEV}").get_data(as_text=True)
    assert 'id="logs"' in body
    # The Logs sub-group lives inside Troubleshooting (#logs still resolves).
    trouble = body[body.index('id="troubleshooting"') :]
    assert 'id="logs"' in trouble
    assert "No logs uploaded yet" in body
    assert "paint failed: refresh_timeout" in body


def test_device_page_lists_and_views_uploads_escaped(app: Flask) -> None:
    client, token = _paired(app)
    _status(client, token, CAP)
    _upload(client, token, "I (1) <script>alert(1)</script>\n")
    name = _store(app).list_uploads(DEV)[0].name

    body = client.get(f"/settings/devices/{DEV}").get_data(as_text=True)
    assert f"log={name}" in body
    assert "1 line" in body
    assert f"/settings/devices/{DEV}/logs/{name}" in body

    body = client.get(f"/settings/devices/{DEV}?log={name}").get_data(as_text=True)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>alert(1)</script>" not in body

    dl = client.get(f"/settings/devices/{DEV}/logs/{name}")
    assert dl.status_code == 200
    assert dl.headers["Content-Disposition"].startswith("attachment;")
    assert dl.mimetype == "text/plain"
    assert dl.get_data(as_text=True) == "I (1) <script>alert(1)</script>\n"


def test_collect_route_sets_and_stops_the_window(app: Flask) -> None:
    client, token = _paired(app)
    _status(client, token, CAP)
    resp = client.post(f"/settings/devices/{DEV}/logs/collect", data={"wakes": "5"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("#logs")
    assert _store(app).remaining(DEV) == 5
    body = client.get(f"/settings/devices/{DEV}").get_data(as_text=True)
    assert "Collecting: 5 wakes left" in body

    client.post(f"/settings/devices/{DEV}/logs/collect", data={"wakes": "3"})
    assert _store(app).remaining(DEV) == 5  # not an offered choice
    client.post(f"/settings/devices/{DEV}/logs/collect", data={"wakes": "0"})
    assert _store(app).remaining(DEV) == 0
    # Never written into the device's config section.
    devices = app.config["SETTINGS_STORE"].get_section("devices") or {}
    assert "remaining" not in json.dumps(devices.get(DEV) or {})


def test_log_routes_need_a_session(app: Flask) -> None:
    client, token = _paired(app)
    _upload(client, token, "x\n")
    name = _store(app).list_uploads(DEV)[0].name
    anon = app.test_client()
    assert anon.get(f"/settings/devices/{DEV}/logs/{name}").status_code in (302, 401)
    anon.post(f"/settings/devices/{DEV}/logs/collect", data={"wakes": "5"})
    assert _store(app).remaining(DEV) == 0


def test_deleting_the_device_removes_its_logs(app: Flask) -> None:
    client, token = _paired(app)
    _status(client, token, CAP)
    _upload(client, token, "x\n")
    _store(app).set_collect(DEV, 5)
    log_dir = Path(app.config["DATA_ROOT"]) / "core" / "device_logs" / DEV
    assert log_dir.is_dir()

    resp = client.post(f"/settings/devices/{DEV}/delete")
    assert resp.status_code == 302
    assert not log_dir.exists()
    assert _store(app).remaining(DEV) == 0
