"""OTA logs (docs/ota/contract.md "OTA logs"): a log batch sent with
``X-Tesserae-Log-Kind: ota`` is filed apart from routine uploads, pruned on
its own caps, noted in Events with the attempt, and shown under "OTA logs"
on the device page, even for a client that never advertised ``logs``."""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from app.state.device_logs import (
    KEEP_OTA_UPLOADS,
    KEEP_UPLOADS,
    KIND_LOG,
    KIND_OTA,
    DeviceLogStore,
)
from tests.test_device_logs import CAP, DEV, _paired, _status, _store, _upload, app  # noqa: F401


def _ota_upload(client, token: str, text: str, **headers: str):
    return client.post(
        f"/api/v1/device/{DEV}/log",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain; charset=utf-8",
            "X-Tesserae-Log-Kind": "ota",
            **headers,
        },
        data=text.encode("utf-8"),
    )


def test_store_keeps_kinds_apart(tmp_path: Path) -> None:
    store = DeviceLogStore(tmp_path)
    routine = store.save_upload(DEV, b"wake\n", now=1_000.0)
    ota = store.save_upload(DEV, b"ota\n", kind=KIND_OTA, now=1_000.0)
    assert routine.name == "19700101T001640Z.log"
    assert ota.name == "ota-19700101T001640Z.log"
    assert [u.kind for u in store.list_uploads(DEV, KIND_OTA)] == [KIND_OTA]
    assert [u.kind for u in store.list_uploads(DEV, KIND_LOG)] == [KIND_LOG]
    assert store.read_upload(DEV, ota.name) == "ota\n"


def test_routine_uploads_never_evict_ota_logs(tmp_path: Path) -> None:
    store = DeviceLogStore(tmp_path)
    store.save_upload(DEV, b"the update that broke\n", kind=KIND_OTA, now=1.0)
    for i in range(KEEP_UPLOADS + 5):
        store.save_upload(DEV, f"wake {i}\n".encode(), now=10.0 + i)
    assert len(store.list_uploads(DEV, KIND_LOG)) == KEEP_UPLOADS
    (kept,) = store.list_uploads(DEV, KIND_OTA)
    assert store.read_upload(DEV, kept.name) == "the update that broke\n"


def test_ota_logs_prune_on_their_own_count(tmp_path: Path) -> None:
    store = DeviceLogStore(tmp_path)
    store.save_upload(DEV, b"routine\n", now=1.0)
    for i in range(KEEP_OTA_UPLOADS + 3):
        store.save_upload(DEV, f"ota {i}\n".encode(), kind=KIND_OTA, now=10.0 + i)
    assert len(store.list_uploads(DEV, KIND_OTA)) == KEEP_OTA_UPLOADS
    assert len(store.list_uploads(DEV, KIND_LOG)) == 1


def test_unknown_kind_is_refused_by_the_store(tmp_path: Path) -> None:
    store = DeviceLogStore(tmp_path)
    try:
        store.save_upload(DEV, b"x\n", kind="../evil")
    except ValueError:
        pass
    else:  # pragma: no cover - the assertion is the point
        raise AssertionError("an unknown kind was stored")


def test_ota_header_files_an_ota_log_with_an_events_row(app: Flask) -> None:  # noqa: F811
    client, token = _paired(app)
    resp = _ota_upload(
        client,
        token,
        "I (1) ota_install: downloading\n",
        **{
            "X-Tesserae-Ota-Phase": "failed",
            "X-Tesserae-Ota-Target": "1.63.1",
            "X-Tesserae-Ota-Attempt": "98980c",
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    (up,) = _store(app).list_uploads(DEV)
    assert up.kind == KIND_OTA and up.name.startswith("ota-")

    rows = [
        r
        for r in app.config["EVENT_LOG"].list(type="device", limit=200)
        if (r.extra or {}).get("log_upload")
    ]
    assert rows, "no Events row for the OTA log"
    extra = rows[0].extra
    assert extra["msg"] == "OTA log uploaded (1.63.1, failed), 1 line"
    assert extra["log_upload"]["kind"] == KIND_OTA
    assert extra["log_upload"]["ota"] == {
        "phase": "failed",
        "target_fw": "1.63.1",
        "attempt_id": "98980c",
    }


def test_unknown_kind_header_is_a_routine_batch(app: Flask) -> None:  # noqa: F811
    client, token = _paired(app)
    resp = client.post(
        f"/api/v1/device/{DEV}/log",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain",
            "X-Tesserae-Log-Kind": "nonsense",
        },
        data=b"x\n",
    )
    assert resp.status_code == 200
    (up,) = _store(app).list_uploads(DEV)
    assert up.kind == KIND_LOG


def test_device_page_shows_ota_logs_without_the_logs_capability(app: Flask) -> None:  # noqa: F811
    client, token = _paired(app)
    _status(client, token, {})
    body = client.get(f"/settings/devices/{DEV}").get_data(as_text=True)
    assert 'id="logs"' not in body

    _ota_upload(client, token, "I (1) <b>ota</b>\n")
    name = _store(app).list_uploads(DEV)[0].name
    body = client.get(f"/settings/devices/{DEV}").get_data(as_text=True)
    assert 'id="logs"' in body
    assert "OTA logs" in body
    assert f"log={name}" in body
    # No collection controls: this client never said it can collect.
    assert "Not collecting" not in body

    body = client.get(f"/settings/devices/{DEV}?log={name}").get_data(as_text=True)
    assert "OTA log from" in body
    assert "&lt;b&gt;ota&lt;/b&gt;" in body


def test_device_page_lists_ota_logs_apart_from_uploads(app: Flask) -> None:  # noqa: F811
    client, token = _paired(app)
    _status(client, token, CAP)
    _upload(client, token, "routine\n")
    _ota_upload(client, token, "ota\n")
    body = client.get(f"/settings/devices/{DEV}").get_data(as_text=True)
    assert "Not collecting" in body
    ota_box = body[body.index('class="dx-ota-logs"') :]
    routine = _store(app).list_uploads(DEV, KIND_LOG)[0].name
    ota = _store(app).list_uploads(DEV, KIND_OTA)[0].name
    assert f"log={ota}" in ota_box
    assert f"log={routine}" not in ota_box
