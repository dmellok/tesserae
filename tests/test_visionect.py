"""app.visionect: the Visionect sign listener and its glue to the push loop.

The end-to-end tests run the real listener on a free localhost port and dial
it with a fake sign built from pyvisionect's own wire helpers: status packets
and ACK/NACKs in, image and command packets out. No hardware and no Visionect
server involved.
"""

from __future__ import annotations

import io
import json
import os
import socket
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask
from PIL import Image
from pyvisionect.devices.enums import PacketType
from pyvisionect.packets import ControlPacket, StatusPacket
from pyvisionect.wire import (
    BlockHeader,
    Compression,
    DataHeader,
    Direction,
    FrameDecoder,
    ProtocolHeader,
    encode_frame,
)

from app import device_service, visionect, visionect_listener
from app.discovery import DiscoveryCache
from app.state.device_telemetry import TelemetryStore
from app.state.event_log import EventLog
from devices.visionect import device as visionect_device

UUID = "36002000-1247-3830-3933-303600000000"
# What the live 32" sign reported: DisplayType 0xC205012C (falls back to the
# 4 x 1440x640 default), HardwareNameID 8, firmware 6.17.4168, 61 % battery.
STATUS_TAGS = [
    (0, 3),
    (1, 0),
    (8, 0xC205012C),
    (9, 0x7AA8C2E9),
    (10, 61),
    (12, 23),
    (13, 58),
    (15, 164),
    (16, 6),
    (17, 17),
    (18, 4168),
    (22, 8),
    (27, 30),
    (34, 3804),
    (40, 3),
    (43, 585),
    (54, 1),
]


def device_frame(uuid: str, packet_type: int, payload: bytes, packet_id: int) -> bytes:
    return encode_frame(
        DataHeader(
            device_id=visionect.uuid_bytes(uuid),
            type=packet_type,
            id=packet_id,
            length=len(payload),
        ),
        payload,
        direction=Direction.DEVICE_TO_SERVER,
        compression=Compression.NONE,
    )


def status_frame(
    uuid: str = UUID, packet_id: int = 1, tags: list[tuple[int, int]] | None = None
) -> bytes:
    payload = StatusPacket(records=tags or STATUS_TAGS, sentinel=0xDEADBEEF).encode()
    return device_frame(uuid, PacketType.STATUS, payload, packet_id)


def with_tag(tag: int, value: int) -> list[tuple[int, int]]:
    """STATUS_TAGS with one tag's value replaced."""
    return [(t, value if t == tag else v) for t, v in STATUS_TAGS]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FakeSign:
    """A sign over a real socket: send status / ACK / NACK, collect frames."""

    def __init__(
        self,
        port: int,
        uuid: str = UUID,
        *,
        rcvbuf: int | None = None,
        local_port: int | None = None,
    ) -> None:
        self.uuid = uuid
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if rcvbuf is not None:  # a sign that stops reading fills this up
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        if local_port is not None:  # known before connecting (see _fake_addresses)
            self.sock.bind(("127.0.0.1", local_port))
        self.sock.settimeout(5)
        self.sock.connect(("127.0.0.1", port))
        self.decoder = FrameDecoder(Direction.SERVER_TO_DEVICE)
        self.frames: list[Any] = []
        self.next_id = 1

    @property
    def port(self) -> int:
        return int(self.sock.getsockname()[1])

    def status(self, tags: list[tuple[int, int]] | None = None) -> None:
        self.sock.sendall(status_frame(self.uuid, self.next_id, tags))
        self.next_id += 1

    def dropped(self, timeout: float = 5.0) -> bool:
        """Whether the server closes this connection within ``timeout``."""
        self.sock.settimeout(0.2)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = self.sock.recv(1 << 20)
            except TimeoutError:
                continue
            except (ConnectionResetError, BrokenPipeError):
                return True
            if not chunk:
                return True
            self.frames.extend(self.decoder.feed(chunk))
        return False

    def ack(self, packet_id: int) -> None:
        self.sock.sendall(
            device_frame(self.uuid, PacketType.CONTROL, ControlPacket.ack().encode(), packet_id)
        )

    def nack(self, packet_id: int) -> None:
        self.sock.sendall(
            device_frame(self.uuid, PacketType.CONTROL, ControlPacket.nack().encode(), packet_id)
        )

    def of_type(self, packet_type: int) -> list[Any]:
        return [f for f in self.frames if f.type == packet_type]

    def wait_for(self, pred: Callable[[FakeSign], bool], timeout: float = 15.0) -> bool:
        self.sock.settimeout(0.2)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred(self):
                return True
            try:
                chunk = self.sock.recv(1 << 20)
            except TimeoutError:
                continue
            if not chunk:
                return pred(self)
            self.frames.extend(self.decoder.feed(chunk))
        return pred(self)

    def images(self) -> list[Any]:
        return self.of_type(PacketType.IMAGE)

    def commands(self) -> list[Any]:
        return self.of_type(PacketType.COMMAND)

    def close(self) -> None:
        self.sock.close()


class FakeSettings:
    def __init__(self, sections: dict[str, dict[str, Any]]) -> None:
        self.sections = sections

    def get_section(self, name: str) -> dict[str, Any]:
        return self.sections.setdefault(name, {})

    def patch_section(self, name: str, values: dict[str, Any]) -> None:
        self.get_section(name).update(values)

    def update_for_namespace(
        self, namespace: str, item_id: str, values: dict[str, Any], fields: list[dict[str, Any]]
    ) -> None:
        names = {f["name"] for f in fields}
        item = self.sections.setdefault(namespace, {}).setdefault(item_id, {})
        item.update({k: v for k, v in values.items() if k in names})


class FakeDevice:
    def __init__(
        self, device_id: str, kind_of: str | None, panel: dict[str, Any], path: Path
    ) -> None:
        self.id = device_id
        self.kind_of = kind_of
        self.manifest: dict[str, Any] = {"panel": dict(panel)}
        self.path = path
        path.write_text(json.dumps({"id": device_id, "panel": panel}), encoding="utf-8")

    @property
    def panel(self) -> dict[str, Any]:
        return dict(self.manifest.get("panel") or {})

    @staticmethod
    def parse_status(payload: bytes) -> dict[str, Any]:
        return visionect_device.parse_status(payload)


SIGN_PANEL = {"w": 1440, "h": 2560, "orientation": "portrait", "native_w": 1440, "native_h": 2560}


class Harness:
    """A service wired to fake devices, settings and renders."""

    def __init__(
        self, tmp_path: Path, *, uuid: str = "", app_settings: dict[str, Any] | None = None
    ) -> None:
        self.renders = tmp_path / "renders"
        self.renders.mkdir()
        lobby: dict[str, Any] = {"uuid": uuid}
        if not uuid:  # a blank device just added: it binds the next unknown sign
            lobby[visionect.AUTO_BIND_KEY] = time.time()
        self.settings = FakeSettings({"devices": {"lobby": lobby}, "app": dict(app_settings or {})})
        kind = FakeDevice("visionect", None, SIGN_PANEL, tmp_path / "kind.json")
        self.device = FakeDevice("lobby", "visionect", SIGN_PANEL, tmp_path / "lobby.json")
        self.registry = SimpleNamespace(devices={"visionect": kind, "lobby": self.device})
        self.latest: dict[str, Any] | None = None
        self.repaints: list[str] = []
        self.consumed: list[str] = []
        self.busy = False  # the push pipeline is rendering for the device
        self.app = Flask(__name__)
        self.app.config["SETTINGS_STORE"] = self.settings
        self.app.config["DISCOVERY_CACHE"] = DiscoveryCache()
        self.app.config["PUSH_MANAGER"] = SimpleNamespace(
            invalidate_and_repaint=lambda device_id, reencode=True: self.repaints.append(device_id),
            consume_force_refetch=self.consumed.append,
        )
        self.svc = visionect.VisionectService(host="127.0.0.1", port=free_port())
        self.svc.attach(
            visionect._Context(
                app=self.app,
                devices=self.registry,  # type: ignore[arg-type]
                settings=self.settings,
                renders_dir=self.renders,
                latest_render_fn=lambda _device_id: self.latest,
                render_busy_fn=lambda _device_id: self.busy,
            )
        )

    def render(self, digest: str, shade: int = 0, *, noisy: bool = False) -> None:
        if noisy:  # incompressible, so the frame really is ~1.8 MB on the wire
            img = Image.frombytes("L", (1440, 2560), os.urandom(1440 * 2560))
        else:
            img = Image.new("L", (1440, 2560), 255)
            img.paste(shade, (0, 0, 1440, 400))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        (self.renders / f"{digest}.png").write_bytes(buf.getvalue())
        self.latest = {"digest": digest, "filename": f"{digest}.png", "ext": "png"}

    def push(self) -> None:
        self.svc.on_push()


@pytest.fixture(autouse=True)
def _quick_settings_answers(monkeypatch) -> None:
    """The fake frames here mostly don't answer settings reads; don't let
    each one hold the next picture or sleep for the real PARAM_WAIT_S."""
    monkeypatch.setattr(visionect, "PARAM_WAIT_S", 0.2)


@pytest.fixture
def harness(tmp_path: Path):
    h = Harness(tmp_path)
    yield h
    h.svc.stop()


def _window_around_now(sleep: bool) -> dict[str, Any]:
    now = datetime.now()
    return {
        "quiet_hours_enabled": True,
        "quiet_hours_start": (now - timedelta(hours=1)).strftime("%H:%M"),
        "quiet_hours_end": (now + timedelta(hours=1)).strftime("%H:%M"),
        "quiet_hours_sleep": sleep,
        "timezone": "system",
    }


# -- pure helpers -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (UUID, UUID),
        (UUID.upper(), UUID),
        (UUID.replace("-", ""), UUID),
        ("", ""),
        ("36002000-1247", ""),
        (None, ""),
        ("zz" + UUID[2:], ""),
    ],
)
def test_normalise_uuid(raw: object, want: str) -> None:
    assert visionect.normalise_uuid(raw) == want


def test_status_heartbeat_maps_the_sign_fields() -> None:
    fields = StatusPacket(records=STATUS_TAGS, sentinel=0).fields()
    beat = visionect.status_heartbeat(fields, uuid=UUID, connect_reason="heartbeat")
    assert beat["battery_pct"] == 61
    assert beat["battery_mv"] == 3804
    assert beat["rssi"] == -58
    assert beat["temperature_c"] == 23
    assert beat["uptime_s"] == 164 * 60
    assert beat["fw_version"].startswith("6.17")
    assert beat["uuid"] == UUID


def _beat(tags: list[tuple[int, int]] | None = None, **kwargs: Any) -> dict[str, Any]:
    fields = StatusPacket(records=tags or STATUS_TAGS, sentinel=0).fields()
    return visionect.status_heartbeat(fields, uuid=UUID, connect_reason="heartbeat", **kwargs)


def test_status_heartbeat_estimates_the_next_wake_from_the_signs_cadence() -> None:
    # The sign's own announcement (NextStatus, 30 min) until its settings are read.
    assert _beat()["next_sleep_s"] == 1800
    # Its check-in interval (TCLV 29) wins once known.
    assert _beat(interval_min=45)["next_sleep_s"] == 2700
    # A sleep Tesserae just ordered is an absolute time and outranks both.
    beat = _beat(interval_min=45, sleep_until=1_900_000_000.0)
    assert beat["sleep_until"] == 1_900_000_000.0 and "next_sleep_s" not in beat
    # No cadence known at all: no estimate rather than a guess.
    assert "next_sleep_s" not in _beat([(t, v) for t, v in STATUS_TAGS if t != 27])
    # A sign that stays awake has no wake to predict.
    awake = _beat(stays_awake=True, interval_min=30)
    assert awake["stays_awake"] is True and "next_sleep_s" not in awake


def test_the_device_kind_passes_the_estimate_through() -> None:
    beat = _beat(interval_min=30)
    parsed = visionect_device.parse_status(json.dumps(beat).encode())
    assert parsed["next_sleep_s"] == 1800
    parsed = visionect_device.parse_status(
        json.dumps({"next_sleep_s": -5, "sleep_until": "soon", "stays_awake": 1}).encode()
    )
    assert parsed == {}  # junk and negatives are dropped, not trusted


def _app_with_telemetry(tmp_path: Path) -> Flask:
    app = Flask(__name__)
    app.config["SETTINGS_STORE"] = FakeSettings({"devices": {}})
    app.config["DEVICE_STATUS"] = {}
    app.config["EVENT_LOG"] = EventLog(tmp_path / "events.json")
    app.config["DEVICE_TELEMETRY"] = TelemetryStore(tmp_path / "telemetry.json")
    return app


def _check_in(app: Flask, device: FakeDevice, at: float, monkeypatch, **kwargs: Any) -> None:
    monkeypatch.setattr("app.transport_wiring.time.time", lambda: at)
    visionect._record_heartbeat(app, device, _beat(**kwargs))


def test_a_frame_checking_in_every_30_minutes_gets_a_next_wake_and_earns_trust(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app_with_telemetry(tmp_path)
    device = FakeDevice("lobby", "visionect", SIGN_PANEL, tmp_path / "lobby.json")
    telemetry = app.config["DEVICE_TELEMETRY"]
    t0 = 1_900_000_000.0
    _check_in(app, device, t0, monkeypatch, interval_min=30)
    entry = telemetry.get("lobby")
    assert entry.last_heartbeat_at == t0
    assert entry.predicted_next_wake_at == t0 + 1800  # Next wake
    assert entry.last_sleep_interval_s == 1800  # Sleep cycle
    assert entry.last_wake_offset_s is None  # nothing to compare the first check-in with
    assert entry.prediction_source == "firmware" and not entry.always_on
    # A few seconds of drift per cycle is on time; the prediction moves forward each time.
    at = t0
    for drift in (6, -4, 9):
        at += 1800 + drift
        _check_in(app, device, at, monkeypatch, interval_min=30)
    entry = telemetry.get("lobby")
    assert entry.last_wake_offset_s == 9  # Last offset
    assert entry.consecutive_on_time_wakes == 3 and entry.is_trusted
    assert entry.predicted_next_wake_at == at + 1800


def test_a_late_frame_resets_trust_and_the_estimate_moves_forward(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app_with_telemetry(tmp_path)
    device = FakeDevice("lobby", "visionect", SIGN_PANEL, tmp_path / "lobby.json")
    t0 = 1_900_000_000.0
    _check_in(app, device, t0, monkeypatch, interval_min=30)
    _check_in(app, device, t0 + 1800, monkeypatch, interval_min=30)
    late = t0 + 3600 + 600  # ten minutes late
    _check_in(app, device, late, monkeypatch, interval_min=30)
    entry = app.config["DEVICE_TELEMETRY"].get("lobby")
    assert entry.last_wake_offset_s == 600
    assert entry.consecutive_on_time_wakes == 0
    assert entry.predicted_next_wake_at == late + 1800


def test_a_frame_that_stays_awake_has_no_next_wake(tmp_path: Path, monkeypatch) -> None:
    app = _app_with_telemetry(tmp_path)
    device = FakeDevice("lobby", "visionect", SIGN_PANEL, tmp_path / "lobby.json")
    # Even a configured sleep interval is no basis for a wake that never comes.
    app.config["SETTINGS_STORE"].sections["devices"]["lobby"] = {"sleep_interval_s": 600}
    _check_in(app, device, 1_900_000_000.0, monkeypatch, stays_awake=True)
    entry = app.config["DEVICE_TELEMETRY"].get("lobby")
    assert entry.predicted_next_wake_at is None and entry.always_on


def test_a_sleep_through_quiet_window_sets_the_next_wake_to_its_end(
    tmp_path: Path, monkeypatch
) -> None:
    h = Harness(tmp_path, uuid=UUID, app_settings=_window_around_now(sleep=True))
    h.app.config["DEVICE_STATUS"] = {}
    h.app.config["EVENT_LOG"] = EventLog(tmp_path / "events.json")
    telemetry = TelemetryStore(tmp_path / "telemetry.json")
    h.app.config["DEVICE_TELEMETRY"] = telemetry
    try:
        assert h.svc.start()  # no render: straight to the sleep step
        sign = FakeSign(h.svc.port)
        try:
            before = time.time()
            sign.status()
            assert sign.wait_for(lambda s: len(s.commands()) == 1)
            assert _wait(lambda: telemetry.get("lobby") is not None)
            sign.ack(sign.commands()[0].id)
            assert _wait(
                lambda: (
                    (
                        telemetry.get("lobby") or SimpleNamespace(prediction_source="")
                    ).prediction_source
                    == "server"
                )
            )
            entry = telemetry.get("lobby")
            assert entry is not None and entry.predicted_next_wake_at is not None
            # About an hour: the window ends an hour from now (plus the margin).
            wait_s = entry.predicted_next_wake_at - before
            assert 3500 < wait_s < 3600 + 60 * (visionect.SLEEP_MARGIN_MINUTES + 2)
            assert entry.last_sleep_interval_s is not None
            assert abs(entry.last_sleep_interval_s - wait_s) < 30
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_configured_port_env_then_setting_then_default(monkeypatch) -> None:
    settings = FakeSettings({"app": {"visionect_port": 12000}})
    monkeypatch.delenv("TESSERAE_VISIONECT_PORT", raising=False)
    assert visionect.configured_port(settings) == 12000
    monkeypatch.setenv("TESSERAE_VISIONECT_PORT", "12001")
    assert visionect.configured_port(settings) == 12001
    monkeypatch.setenv("TESSERAE_VISIONECT_PORT", "nonsense")
    assert visionect.configured_port(FakeSettings({})) == visionect.DEFAULT_PORT


def test_artifact_path_stays_inside_the_renders_dir(tmp_path: Path) -> None:
    assert visionect.artifact_path(tmp_path, {"filename": "../../etc/passwd"}) == (
        tmp_path / "passwd"
    )
    assert visionect.artifact_path(tmp_path, {"digest": "abc", "ext": "png"}) == (
        tmp_path / "abc.png"
    )
    assert visionect.artifact_path(tmp_path, {}) is None


def test_catalog_skus_count_as_visionect() -> None:
    marker = SimpleNamespace(PROTOCOL="visionect")
    assert visionect.is_visionect_device(SimpleNamespace(id="lobby", kind_of="visionect"))
    # A Joan 6 SKU instance: its kind id differs, the shared module says so.
    assert visionect.is_visionect_device(
        SimpleNamespace(id="hall", kind_of="visionect_joan_6", module=marker)
    )
    assert not visionect.is_visionect_device(
        SimpleNamespace(id="x", kind_of="trmnl_x", module=SimpleNamespace())
    )


def test_conform_panel_adopts_the_signs_buffer(tmp_path: Path) -> None:
    default = {
        "w": 1440,
        "h": 2560,
        "orientation": "landscape",
        "native_w": 1440,
        "native_h": 2560,
    }
    device: Any = FakeDevice("joan", "visionect", default, tmp_path / "joan.json")
    assert visionect.conform_panel(device, (1024, 758), (1440, 2560))
    stored = json.loads((tmp_path / "joan.json").read_text())["panel"]
    assert (stored["native_w"], stored["native_h"]) == (1024, 758)
    assert (stored["w"], stored["h"]) == (1024, 758)  # landscape, re-seeded from defaults
    assert device.panel["native_w"] == 1024
    # Already conformed: nothing to write.
    assert not visionect.conform_panel(device, (1024, 758), (1440, 2560))


def test_conform_panel_never_writes_a_deleted_device_back(tmp_path: Path) -> None:
    device: Any = FakeDevice("joan", "visionect", SIGN_PANEL, tmp_path / "joan.json")
    (tmp_path / "joan.json").unlink()  # deleted while the sign's status was in flight
    assert not visionect.conform_panel(device, (1024, 758), (1440, 2560), lambda: device)
    assert not (tmp_path / "joan.json").exists()
    # Still on disk, but no longer in the registry: also left alone.
    device = FakeDevice("joan", "visionect", SIGN_PANEL, tmp_path / "joan.json")
    before = (tmp_path / "joan.json").read_text()
    assert not visionect.conform_panel(device, (1024, 758), (1440, 2560), lambda: None)
    assert (tmp_path / "joan.json").read_text() == before


def test_conform_panel_waits_for_other_instance_file_writers(tmp_path: Path) -> None:
    """A rename (or any other instance-file save) that runs while the sign
    conforms the panel isn't lost: both hold the instance-file lock."""
    from app.device_service import INSTANCE_FILE_LOCK

    device: Any = FakeDevice("joan", "visionect", SIGN_PANEL, tmp_path / "joan.json")
    done = threading.Event()
    with INSTANCE_FILE_LOCK:
        worker = threading.Thread(
            target=lambda: (
                visionect.conform_panel(device, (1024, 758), (1440, 2560), lambda: device),
                done.set(),
            )
        )
        worker.start()
        assert not done.wait(0.3), "conformed while another writer held the file"
        raw = json.loads(device.path.read_text())
        raw["name"] = "Desk Joan"  # the rename that was in progress
        device.path.write_text(json.dumps(raw), encoding="utf-8")
    assert done.wait(5)
    worker.join()
    stored = json.loads(device.path.read_text())
    assert stored["name"] == "Desk Joan"
    assert (stored["panel"]["native_w"], stored["panel"]["native_h"]) == (1024, 758)


# -- quiet hours --------------------------------------------------------------


def test_sleep_minutes_inside_a_sleep_through_window(tmp_path: Path) -> None:
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    minutes = h.svc._sleep_minutes(h.device)  # type: ignore[arg-type]
    assert minutes is not None
    # Window ends an hour from now (inclusive end minute) plus the margin.
    assert 55 <= minutes <= 65


def test_no_sleep_without_sleep_through(tmp_path: Path) -> None:
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=False))
    assert h.svc._sleep_minutes(h.device) is None  # type: ignore[arg-type]


# -- end to end over a real socket ------------------------------------------


def test_unknown_sign_binds_and_gets_exactly_one_frame(harness: Harness) -> None:
    harness.render("frame-a")
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status()
        assert sign.wait_for(lambda s: len(s.images()) == 1)
        # The first status raises both DeviceConnected and StatusReceived; a
        # second status before the ACK must not resend either.
        sign.status()
        sign.wait_for(lambda s: len(s.images()) > 1, timeout=1.5)
        assert len(sign.images()) == 1
        assert all(f.device_id == visionect.uuid_bytes(UUID) for f in sign.images())
    finally:
        sign.close()
    assert harness.settings.sections["devices"]["lobby"]["uuid"] == UUID


def test_newer_frame_waits_for_the_ack_of_the_older_one(harness: Harness) -> None:
    harness.render("frame-a", shade=0)
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status()
        assert sign.wait_for(lambda s: len(s.images()) == 1)
        first = sign.images()[0]
        harness.render("frame-b", shade=128)
        harness.push()
        # One image in flight at a time: B waits for A's ACK.
        sign.wait_for(lambda s: len(s.images()) > 1, timeout=1.5)
        assert len(sign.images()) == 1
        sign.ack(first.id)
        assert sign.wait_for(lambda s: len(s.images()) == 2)
        assert sign.images()[1].id != first.id
    finally:
        sign.close()


def test_refused_frame_is_retried(harness: Harness, monkeypatch) -> None:
    monkeypatch.setattr(visionect, "NACK_RETRY_S", 0.3)
    harness.render("frame-a")
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status()
        assert sign.wait_for(lambda s: len(s.images()) == 1)
        sign.nack(sign.images()[0].id)
        assert sign.wait_for(lambda s: len(s.images()) == 2, timeout=5)
    finally:
        sign.close()


def test_sleep_only_after_the_frame_is_acked(tmp_path: Path) -> None:
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    try:
        h.render("frame-a")
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.images()) == 1)
            sign.wait_for(lambda s: len(s.commands()) > 0, timeout=1.5)
            assert sign.commands() == []
            sign.ack(sign.images()[0].id)
            assert sign.wait_for(lambda s: len(s.commands()) == 1)
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_refused_sleep_is_not_replayed_after_quiet_hours(tmp_path: Path) -> None:
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    try:
        assert h.svc.start()  # no render: straight to the sleep step
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.commands()) == 1)
            h.settings.sections["app"]["quiet_hours_enabled"] = False
            sign.nack(sign.commands()[0].id)
            sign.status()
            sign.wait_for(lambda s: len(s.commands()) > 1, timeout=1.5)
            assert len(sign.commands()) == 1
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_sign_without_a_free_device_is_listed_as_discovered(tmp_path: Path) -> None:
    h = Harness(tmp_path, uuid="11112222-3333-4444-5555-666677778888")
    try:
        h.render("frame-a")
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            sign.wait_for(lambda s: len(s.images()) > 0, timeout=1.5)
            assert sign.images() == []
        finally:
            sign.close()
        entry = h.app.config["DISCOVERY_CACHE"].get(visionect.discovery_id(UUID))
        assert entry is not None
        assert entry.kind == "visionect"  # no catalog model in this registry
        assert (entry.panel_w, entry.panel_h) == (1440, 2560)
        assert entry.parsed["uuid"] == UUID
        assert h.settings.sections["devices"]["lobby"]["uuid"] != UUID
    finally:
        h.svc.stop()


def test_a_differently_sized_sign_conforms_the_panel_and_repaints(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    try:
        # A device first set up for a 13" board, then bound to the 32" sign.
        thirteen = {
            "w": 1600,
            "h": 1200,
            "orientation": "landscape",
            "native_w": 1600,
            "native_h": 1200,
        }
        h.device.manifest["panel"] = dict(thirteen)
        h.device.path.write_text(json.dumps({"id": "lobby", "panel": thirteen}), encoding="utf-8")
        h.render("frame-a")
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            deadline = time.monotonic() + 10
            while not h.repaints and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            sign.close()
        assert h.repaints == ["lobby"]
        assert (h.device.panel["native_w"], h.device.panel["native_h"]) == (1440, 2560)
    finally:
        h.svc.stop()


def test_stop_closes_a_connected_signs_socket(harness: Harness) -> None:
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status()
        sign.wait_for(lambda s: False, timeout=0.5)
        started = time.monotonic()
        harness.svc.stop()
        assert time.monotonic() - started < 5
        sign.sock.settimeout(3)
        closed = False
        try:
            while True:
                if not sign.sock.recv(65536):
                    closed = True  # EOF
                    break
        except ConnectionResetError:
            closed = True
        except TimeoutError:
            closed = False  # still open: the listener left the socket behind
        assert closed
    finally:
        sign.close()


def test_register_moves_the_listener_when_the_port_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(visionect, "_SERVICE", None)
    monkeypatch.delenv("TESSERAE_VISIONECT_PORT", raising=False)
    h = Harness(tmp_path)
    listeners: list[Any] = []
    push_mgr = SimpleNamespace(
        latest_render_for=lambda _d: None,
        add_listener=listeners.append,
        remove_listener=listeners.remove,
    )
    h.app.config.update(PUSH_MANAGER=push_mgr, DEVICE_REGISTRY=h.registry, RENDERS_DIR=h.renders)
    h.settings.sections["app"]["visionect_port"] = 12000
    visionect.register(h.app)
    first = h.app.config["VISIONECT_SERVICE"]
    assert first.port == 12000
    assert not first.running  # never binds under pytest
    h.settings.sections["app"]["visionect_port"] = 12001
    visionect.register(h.app)
    assert h.app.config["VISIONECT_SERVICE"] is not first
    assert h.app.config["VISIONECT_SERVICE"].port == 12001
    # The retired listener is off the push manager and never restarts itself.
    assert listeners == [h.app.config["VISIONECT_SERVICE"].on_push]
    assert not first.autostart


def test_the_listener_binds_the_web_servers_address(tmp_path: Path, monkeypatch) -> None:
    """``TESSERAE_BIND_HOST`` (which the web server's bind address comes
    from) is where the listener binds too, all interfaces when it is unset,
    and a change of address moves the listener like a change of port."""
    monkeypatch.setattr(visionect, "_SERVICE", None)
    monkeypatch.delenv("TESSERAE_VISIONECT_PORT", raising=False)
    monkeypatch.delenv("TESSERAE_BIND_HOST", raising=False)
    assert visionect.configured_host() == "0.0.0.0"
    monkeypatch.setenv("TESSERAE_BIND_HOST", "  ")
    assert visionect.configured_host() == "0.0.0.0"
    h = Harness(tmp_path)
    listeners: list[Any] = []
    push_mgr = SimpleNamespace(
        latest_render_for=lambda _d: None,
        add_listener=listeners.append,
        remove_listener=listeners.remove,
    )
    h.app.config.update(PUSH_MANAGER=push_mgr, DEVICE_REGISTRY=h.registry, RENDERS_DIR=h.renders)
    visionect.register(h.app)
    first = h.app.config["VISIONECT_SERVICE"]
    assert first.host == "0.0.0.0"
    monkeypatch.setenv("TESSERAE_BIND_HOST", "192.168.1.5")
    visionect.register(h.app)
    second = h.app.config["VISIONECT_SERVICE"]
    assert second is not first and second.host == "192.168.1.5"
    assert not first.autostart
    visionect.register(h.app)  # same address: the listener stays
    assert h.app.config["VISIONECT_SERVICE"] is second


def test_register_does_not_start_the_listener_when_autostart_is_off(
    tmp_path: Path, monkeypatch
) -> None:
    """Tests switch ``app.visionect.AUTOSTART`` off (see tests/conftest.py);
    nothing in the production code looks at pytest."""
    monkeypatch.setattr(visionect, "_SERVICE", None)
    h = Harness(tmp_path)
    push_mgr = SimpleNamespace(
        latest_render_for=lambda _d: None,
        add_listener=lambda _cb: None,
        remove_listener=lambda _cb: None,
    )
    h.app.config.update(PUSH_MANAGER=push_mgr, DEVICE_REGISTRY=h.registry, RENDERS_DIR=h.renders)
    assert visionect.AUTOSTART is False
    visionect.register(h.app)
    assert not h.app.config["VISIONECT_SERVICE"].running
    assert "PYTEST_CURRENT_TEST" not in Path(visionect.__file__).read_text()


def test_the_device_kind_offers_no_flash_writing_settings() -> None:
    """The check-in interval and sleep mode writes are untested on hardware
    (a bad flash-save write persists on the sign): the kind has no fields for
    them, so a device's config never asks for either and nothing is written."""
    kind = json.loads(
        (Path(visionect.__file__).parent.parent / "devices/visionect/device.json").read_text()
    )
    assert set(kind["config_schema"]) == {"uuid"}
    assert visionect.desired_params({"uuid": UUID}) == {}


def test_refused_sleep_backs_off(tmp_path: Path) -> None:
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    try:
        assert h.svc.start()  # no render: straight to the sleep step
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.commands()) == 1)
            sign.nack(sign.commands()[0].id)
            sign.status()
            sign.wait_for(lambda s: len(s.commands()) > 1, timeout=1.5)
            assert len(sign.commands()) == 1  # no NACK/sleep ping-pong
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_sleep_waits_for_the_repaint_after_a_size_change(tmp_path: Path) -> None:
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    try:
        thirteen = {
            "w": 1600,
            "h": 1200,
            "orientation": "landscape",
            "native_w": 1600,
            "native_h": 1200,
        }
        h.device.manifest["panel"] = dict(thirteen)
        h.device.path.write_text(json.dumps({"id": "lobby", "panel": thirteen}), encoding="utf-8")
        h.render("frame-a")

        def _invalidate(device_id: str, reencode: bool = True) -> None:
            h.repaints.append(device_id)
            h.latest = None  # the real repaint drops the render until it's redone

        h.app.config["PUSH_MANAGER"] = SimpleNamespace(invalidate_and_repaint=_invalidate)
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            deadline = time.monotonic() + 10
            while not h.repaints and time.monotonic() < deadline:
                time.sleep(0.05)
            assert h.repaints == ["lobby"]
            sign.status()
            sign.wait_for(lambda s: len(s.commands()) > 0, timeout=1.5)
            assert sign.commands() == []  # not asleep before the new frame
            h.render("frame-b")
            h.push()
            assert sign.wait_for(lambda s: len(s.images()) == 1)
            sign.ack(sign.images()[0].id)
            assert sign.wait_for(lambda s: len(s.commands()) == 1)
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_disconnected_sign_stops_reading_as_connected(harness: Harness) -> None:
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    sign.status()
    deadline = time.monotonic() + 5
    while not (harness.svc.sign_view(UUID) or {}).get("connected"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    sign.close()
    time.sleep(0.3)
    harness.svc._call_soon(harness.svc._rescan)
    deadline = time.monotonic() + 5
    while (harness.svc.sign_view(UUID) or {}).get("connected"):
        assert time.monotonic() < deadline, "still reads as connected"
        time.sleep(0.05)


def test_glass_check_resends_once_after_the_crc_has_had_time() -> None:
    svc = visionect.VisionectService()
    sign = visionect._Sign(uuid=UUID)
    sign.shown_digest, sign.shown_checksum = "frame-a", 111
    status = SimpleNamespace(display_state_crc=222)
    sign.shown_at = time.time()
    svc._check_glass(sign, status)
    assert sign.shown_digest == "frame-a"  # too soon: 6.17 reports a status late
    sign.shown_at = time.time() - visionect.CRC_CONVERGE_S - 1
    svc._check_glass(sign, status)
    assert sign.shown_digest == ""  # one corrective resend
    sign.shown_digest, sign.shown_at = "frame-a", time.time() - visionect.CRC_CONVERGE_S - 1
    svc._check_glass(sign, status)
    assert sign.shown_digest == "frame-a"  # never a loop
    svc._check_glass(sign, SimpleNamespace(display_state_crc=111))
    assert sign.crc_corrected  # a match doesn't re-arm it (only a new frame's ACK does)


def test_a_slow_stop_never_lets_a_new_run_share_its_state(harness: Harness, monkeypatch) -> None:
    """stop() gives up joining after its timeout. The old run's teardown must
    not then wipe a new run's loop, readiness, signs and snapshot: start()
    refuses until that thread is gone, and a run only clears its own."""
    import asyncio

    real_shutdown = harness.svc._shutdown

    async def _slow_shutdown(server: Any) -> None:
        await asyncio.sleep(1.0)
        await real_shutdown(server)

    monkeypatch.setattr(harness.svc, "_shutdown", _slow_shutdown)
    assert harness.svc.start()
    old_thread = harness.svc._thread
    harness.svc.stop(timeout=0.1)
    assert old_thread is not None and old_thread.is_alive()
    assert not harness.svc.running
    assert not harness.svc.start()  # refused while the old run winds down
    assert "still shutting down" in (harness.svc.start_error or "")
    listeners = [t for t in threading.enumerate() if t.name == "visionect-listener"]
    assert listeners == [old_thread]
    old_thread.join(5)
    monkeypatch.setattr(harness.svc, "_shutdown", real_shutdown)
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status()
        deadline = time.monotonic() + 5
        while not (harness.svc.sign_view(UUID) or {}).get("connected"):
            assert time.monotonic() < deadline, "the new run lost its state"
            time.sleep(0.05)
        assert harness.svc.running
    finally:
        sign.close()


def test_concurrent_starts_run_one_listener(harness: Harness) -> None:
    results: list[bool] = []
    starters = [
        threading.Thread(target=lambda: results.append(harness.svc.start())) for _ in range(4)
    ]
    for t in starters:
        t.start()
    for t in starters:
        t.join(10)
    assert results == [True] * 4
    assert len([t for t in threading.enumerate() if t.name == "visionect-listener"]) == 1


def test_a_retry_armed_before_stop_does_not_restart_the_listener(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(visionect, "START_RETRY_S", 0.2)
    h = Harness(tmp_path)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", h.svc.port))
    blocker.listen()
    try:
        assert not h.svc.maybe_start()  # arms the retry timer
        gen = h.svc._lifecycle_gen
        h.svc.stop()
    finally:
        blocker.close()
    h.svc._retry_fire(gen)  # a timer that fired just as stop() ran
    time.sleep(0.5)
    assert not h.svc.running
    h.svc.stop()


def test_busy_port_is_retried_on_a_timer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(visionect, "START_RETRY_S", 0.3)
    h = Harness(tmp_path)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", h.svc.port))
    blocker.listen()
    try:
        assert not h.svc.maybe_start()  # port held, as by the old VSS
        assert h.svc.start_error
    finally:
        blocker.close()
    try:
        deadline = time.monotonic() + 5
        while not h.svc.running:
            assert time.monotonic() < deadline, "listener never retried"
            time.sleep(0.05)
    finally:
        h.svc.stop()


# -- untrusted LAN traffic ----------------------------------------------------


def _closed_soon(sock: socket.socket, timeout: float = 3.0) -> bool:
    """Whether the server drops ``sock`` within ``timeout``."""
    sock.settimeout(timeout)
    try:
        while True:
            if not sock.recv(65536):
                return True
    except (ConnectionResetError, BrokenPipeError):
        return True
    except TimeoutError:
        return False


def _raw(port: int, local_port: int | None = None) -> socket.socket:
    source = ("127.0.0.1", local_port) if local_port is not None else None
    return socket.create_connection(("127.0.0.1", port), timeout=5, source_address=source)


def test_the_listener_hooks_pyvisionect_where_it_expects() -> None:
    """GuardedServer overrides two private methods; a pyvisionect release that
    renames or reshapes them must fail here, not silently drop the bounds."""
    import inspect

    from pyvisionect.io import VisionectServer

    for name, params in (
        ("_handle_client", ["self", "reader", "writer"]),
        ("_run_timers", ["self", "conn", "writer"]),
    ):
        method = getattr(VisionectServer, name)
        assert inspect.iscoroutinefunction(method), name
        assert list(inspect.signature(method).parameters) == params, name


def test_a_decompression_bomb_never_reaches_the_decoder(harness: Harness, monkeypatch) -> None:
    import pyvisionect.wire.framing as framing

    def _boom(*_a: Any, **_k: Any) -> bytes:
        raise AssertionError("the bomb reached pyvisionect's block decoder")

    monkeypatch.setattr(framing, "decode_blocks", _boom)
    assert harness.svc.start()
    # Two blocks declared, so the library's one total-size check passes; the
    # first block's one LZ4 byte "inflates" to 2 GiB.
    body = BlockHeader(0, 1, 1, 1 << 31, 0).pack() + b"\x00"
    frame = ProtocolHeader(version=3, compression=Compression.LZ4, length=len(body)).pack() + body
    sock = _raw(harness.svc.port)
    try:
        sock.sendall(frame)
        assert _closed_soon(sock)
    finally:
        sock.close()
    assert harness.svc._server.rejected_frames == 1


def test_a_huge_frame_is_refused_from_its_header(harness: Harness) -> None:
    assert harness.svc.start()
    sock = _raw(harness.svc.port)
    try:
        # Claims 50 MB; the library would buffer all of it before deciding.
        sock.sendall(ProtocolHeader(version=3, length=50_000_000).pack() + b"\0" * 1000)
        assert _closed_soon(sock)
    finally:
        sock.close()
    assert harness.svc._server.rejected_frames == 1


def test_a_small_compressed_frame_still_reaches_the_library(harness: Harness) -> None:
    harness.render("frame-a")
    assert harness.svc.start()
    payload = StatusPacket(records=STATUS_TAGS, sentinel=0xDEADBEEF).encode()
    frame = encode_frame(
        DataHeader(
            device_id=visionect.uuid_bytes(UUID),
            type=PacketType.STATUS,
            id=1,
            length=len(payload),
        ),
        payload,
        direction=Direction.DEVICE_TO_SERVER,
        compression=Compression.LZ4,
    )
    sign = FakeSign(harness.svc.port)
    try:
        for i in range(len(frame)):  # one byte at a time: frames reassemble
            sign.sock.sendall(frame[i : i + 1])
        assert sign.wait_for(lambda s: len(s.images()) == 1)
    finally:
        sign.close()


def test_a_connection_that_never_identifies_is_dropped_on_time(
    harness: Harness, monkeypatch
) -> None:
    """The library's identify timeout is per read, and stops applying once a
    frame carries a device id even if it never yields a valid status. The
    listener's deadline is for the whole connection."""
    monkeypatch.setattr(visionect_listener, "IDENTIFY_TIMEOUT_S", 0.6)
    assert harness.svc.start()
    assert harness.svc._server.identify_timeout == 0.6
    sock = _raw(harness.svc.port)
    try:
        # A status-typed frame whose payload doesn't decode: the library
        # registers the UUID but no sign ever identifies.
        sock.sendall(device_frame(UUID, PacketType.STATUS, b"\x01\x02\x03", 1))
        started = time.monotonic()
        assert _closed_soon(sock, timeout=5)
        assert time.monotonic() - started < 3
    finally:
        sock.close()
    assert harness.svc._server.identify_timeouts == 1


def test_a_sign_is_not_dropped_by_the_identify_deadline(harness: Harness, monkeypatch) -> None:
    monkeypatch.setattr(visionect_listener, "IDENTIFY_TIMEOUT_S", 0.4)
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status()
        assert not _closed_soon(sign.sock, timeout=1.0)
    finally:
        sign.close()


def test_the_listener_caps_connections_per_address(harness: Harness, monkeypatch) -> None:
    monkeypatch.setattr(visionect_listener, "MAX_CONNECTIONS_PER_PEER", 2)
    assert harness.svc.start()
    socks = [_raw(harness.svc.port) for _ in range(2)]
    try:
        time.sleep(0.2)
        extra = _raw(harness.svc.port)
        socks.append(extra)
        assert _closed_soon(extra)
        assert not _closed_soon(socks[0], timeout=0.3)
    finally:
        for sock in socks:
            sock.close()
    assert harness.svc._server.refused_connections == 1


def test_a_timer_task_write_error_does_not_escape() -> None:
    """pyvisionect awaits the timer task suppressing only CancelledError, so
    an error out of it would skip the connection's removal and close."""
    import asyncio

    aborted: list[bool] = []

    class _Writer:
        transport = SimpleNamespace(abort=lambda: aborted.append(True), is_closing=lambda: True)

        def get_extra_info(self, _name: str) -> Any:
            return ("10.0.0.9", 1234)

        def write(self, _data: bytes) -> None:
            pass

        async def drain(self) -> None:
            raise ConnectionResetError("Connection lost")

    conn = SimpleNamespace(closed=False, advance=lambda _now: [], drain=lambda: b"\x01")

    async def _run() -> None:
        server = visionect_listener.GuardedServer(on_events=lambda c, e: None)
        await asyncio.wait_for(server._run_timers(conn, _Writer()), timeout=5)

    asyncio.run(_run())  # returns instead of raising ConnectionResetError
    assert aborted == [True]


# -- delivery: deadlines, stuck writes, whose connection it is -----------------


def _fake_addresses(monkeypatch, by_port: dict[int, str]) -> None:
    """Make connections from these local ports look like they come from
    these addresses (every test connection is 127.0.0.1)."""
    real = visionect_listener.peer_ip

    def _peer_ip(writer: Any) -> str:
        peer = writer.get_extra_info("peername") if writer is not None else None
        if isinstance(peer, tuple) and len(peer) > 1 and peer[1] in by_port:
            return by_port[peer[1]]
        return real(writer)

    monkeypatch.setattr(visionect_listener, "peer_ip", _peer_ip)


def _wait(pred: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_an_unacked_frame_expires_and_the_latest_is_sent_on_redial(
    harness: Harness, monkeypatch
) -> None:
    """A lost ACK used to block every later frame, the sleep and the glass
    check until the sign re-dialed, which a plugged-in sign may not do for
    days."""
    monkeypatch.setattr(visionect, "IMAGE_ACK_TIMEOUT_S", 0.5)
    harness.render("frame-a")
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status()
        assert sign.wait_for(lambda s: len(s.images()) == 1)
        harness.render("frame-b", shade=128)
        harness.push()  # waits behind the unanswered frame A
        assert sign.dropped(timeout=5), "the connection outlived the ACK deadline"
    finally:
        sign.close()
    assert "never confirmed the picture" in (harness.svc.sign_view(UUID) or {}).get(
        "last_error", ""
    )
    again = FakeSign(harness.svc.port)
    try:
        again.status()
        assert again.wait_for(lambda s: len(s.images()) == 1)
        again.ack(again.images()[0].id)
        assert _wait(lambda: (harness.svc.sign_view(UUID) or {}).get("in_sync") is True)
    finally:
        again.close()
    sign_state = harness.svc._signs[UUID]
    assert sign_state.shown_digest == "frame-b"


def test_an_unacked_sleep_expires(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(visionect, "SLEEP_ACK_TIMEOUT_S", 0.5)
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    try:
        assert h.svc.start()  # no render: straight to the sleep step
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.commands()) == 1)
            assert sign.dropped(timeout=5)
        finally:
            sign.close()
    finally:
        h.svc.stop()


@pytest.fixture
def stuck_sign_send_buffer(monkeypatch) -> None:
    """Make a sign that stops reading back up the write on any OS.

    A 1.8 MB frame only blocks if it can't be parked in the kernel. macOS caps
    a socket's send buffer near 128 KB, but Linux autotunes it up to 4 MB, so
    on Linux the whole frame was accepted at once, the write never blocked and
    the stuck sign was left for the ACK timeout instead. Pinning the send
    buffer (which also turns autotuning off) makes the write block everywhere."""
    real_write = visionect.VisionectService._write

    async def write(self, conn, writer):
        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        return await real_write(self, conn, writer)

    monkeypatch.setattr(visionect.VisionectService, "_write", write)


def test_a_write_that_never_drains_is_dropped(
    harness: Harness, monkeypatch, stuck_sign_send_buffer
) -> None:
    monkeypatch.setattr(visionect, "WRITE_TIMEOUT_S", 0.5)
    harness.render("frame-a", noisy=True)
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port, rcvbuf=4096)
    try:
        sign.status()  # and then never reads the 1.8 MB frame
        assert _wait(
            lambda: (
                "stopped taking data" in (harness.svc.sign_view(UUID) or {}).get("last_error", "")
            )
        )
        assert not (harness.svc.sign_view(UUID) or {}).get("connected")
    finally:
        sign.close()


def test_a_redial_supersedes_a_connection_stuck_in_a_write(
    harness: Harness, stuck_sign_send_buffer
) -> None:
    """Sol's blocked-writer probe: the pass writing to a half-open socket held
    the sign's lock, so the sign's new connection was never served."""
    harness.render("frame-a", noisy=True)
    assert harness.svc.start()
    stuck = FakeSign(harness.svc.port, rcvbuf=4096)
    try:
        stuck.status()  # the frame's write to this socket blocks
        assert _wait(lambda: (harness.svc.sign_view(UUID) or {}).get("sending") is True)
        time.sleep(0.3)
        fresh = FakeSign(harness.svc.port)
        try:
            fresh.status()
            assert fresh.wait_for(lambda s: len(s.images()) == 1, timeout=10)
            assert stuck.dropped(timeout=5)
        finally:
            fresh.close()
    finally:
        stuck.close()


def test_another_address_cannot_take_over_a_bound_sign(tmp_path: Path, monkeypatch) -> None:
    h = Harness(tmp_path, uuid=UUID)
    try:
        h.render("frame-a")
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        intruder: FakeSign | None = None
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.images()) == 1)
            sign.ack(sign.images()[0].id)
            intruder = FakeSign(h.svc.port)
            _fake_addresses(monkeypatch, {sign.port: "10.0.0.5", intruder.port: "10.0.0.66"})
            # (The sign's address was read before the patch; set it to match.)
            h.svc._signs[UUID].remote = "10.0.0.5"
            # Claims the sign's UUID, with a Joan 6's DisplayType.
            intruder.status(with_tag(8, 0x6))
            assert intruder.dropped(timeout=5)
            view = h.svc.sign_view(UUID) or {}
            assert (view.get("refused") or {}).get("remote") == "10.0.0.66"
            assert view.get("connected") and view.get("remote") == "10.0.0.5"
            # The panel isn't conformed to the intruder's geometry, and the
            # next frame still goes to the sign.
            h.render("frame-b", shade=128)
            h.push()
            assert sign.wait_for(lambda s: len(s.images()) == 2)
            assert (h.device.panel["native_w"], h.device.panel["native_h"]) == (1440, 2560)
            assert intruder.images() == []
        finally:
            sign.close()
            if intruder is not None:
                intruder.close()
    finally:
        h.svc.stop()


def test_a_silent_old_connection_is_probed_so_a_moved_sign_gets_back_in(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(visionect, "PROBE_QUIET_S", 0.0)
    monkeypatch.setattr(visionect, "PROBE_TIMEOUT_S", 0.5)
    h = Harness(tmp_path, uuid=UUID)
    try:
        assert h.svc.start()
        old = FakeSign(h.svc.port)  # half-open: never answers again
        old.status()
        assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("connected") is True)
        moved = FakeSign(h.svc.port)
        _fake_addresses(monkeypatch, {old.port: "10.0.0.5", moved.port: "10.0.0.77"})
        h.svc._signs[UUID].remote = "10.0.0.5"
        moved.status()
        assert moved.dropped(timeout=5)  # refused while the old one may live
        assert old.wait_for(lambda s: len(s.commands()) == 1)  # asked for a status
        assert old.dropped(timeout=5)  # no answer: dropped
        back = FakeSign(h.svc.port)
        _fake_addresses(monkeypatch, {back.port: "10.0.0.77"})
        back.status()
        assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("remote") == "10.0.0.77")
        assert not back.dropped(timeout=0.5)
        for s in (old, moved, back):
            s.close()
    finally:
        h.svc.stop()


def test_a_redial_from_the_same_address_replaces_the_old_connection(harness: Harness) -> None:
    assert harness.svc.start()
    first = FakeSign(harness.svc.port)
    second: FakeSign | None = None
    try:
        first.status()
        assert _wait(lambda: (harness.svc.sign_view(UUID) or {}).get("connected") is True)
        second = FakeSign(harness.svc.port)
        second.status()
        assert first.dropped(timeout=5)
        assert not second.dropped(timeout=0.5)
    finally:
        first.close()
        if second is not None:
            second.close()


def test_a_closing_socket_reads_as_disconnected() -> None:
    """pyvisionect can leave a dead connection registered; its closing
    transport is what tells."""
    svc = visionect.VisionectService()
    sign = visionect._Sign(uuid=UUID)
    sign.conn = SimpleNamespace(closed=False)
    sign.writer = SimpleNamespace(transport=SimpleNamespace(is_closing=lambda: True))
    assert not svc._alive(sign)
    svc._publish(sign)
    assert (svc.sign_view(UUID) or {}).get("connected") is False
    sign.writer = SimpleNamespace(transport=SimpleNamespace(is_closing=lambda: False))
    assert svc._alive(sign)


# -- sleep vs renders, the glass check, resends ---------------------------------


def test_sleep_waits_for_a_render_in_progress(tmp_path: Path, monkeypatch) -> None:
    """Frame A acked, render B under way as the window starts: the sign used
    to sleep all night on A."""
    monkeypatch.setattr(visionect, "RENDER_HOLD_POLL_S", 0.1)
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    try:
        h.render("frame-a")
        h.busy = True
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.images()) == 1)
            sign.ack(sign.images()[0].id)
            sign.wait_for(lambda s: len(s.commands()) > 0, timeout=1.5)
            assert sign.commands() == []  # B is still rendering
            h.render("frame-b", shade=128)
            h.busy = False
            h.push()
            assert sign.wait_for(lambda s: len(s.images()) == 2)
            sign.wait_for(lambda s: len(s.commands()) > 0, timeout=1.0)
            assert sign.commands() == []  # and B isn't acked yet
            sign.ack(sign.images()[1].id)
            assert sign.wait_for(lambda s: len(s.commands()) == 1)
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_sleep_waits_for_a_render_even_with_no_frame_yet(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(visionect, "RENDER_HOLD_POLL_S", 0.1)
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    try:
        h.busy = True  # latest_render_for() is None, but a render is coming
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            sign.wait_for(lambda s: len(s.commands()) > 0, timeout=1.5)
            assert sign.commands() == []
            h.render("frame-a")
            h.busy = False
            h.push()
            assert sign.wait_for(lambda s: len(s.images()) == 1)
            sign.ack(sign.images()[0].id)
            assert sign.wait_for(lambda s: len(s.commands()) == 1)
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_a_render_that_never_finishes_holds_the_sleep_only_so_long(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(visionect, "RENDER_HOLD_POLL_S", 0.1)
    monkeypatch.setattr(visionect, "RENDER_HOLD_MAX_S", 0.6)
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    try:
        h.busy = True
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.commands()) == 1, timeout=5)
        finally:
            sign.close()
    finally:
        h.svc.stop()


@pytest.mark.parametrize("failure", ["missing", "encode"])
def test_an_unreadable_render_is_noted_once_and_the_sign_still_sleeps(
    tmp_path: Path, monkeypatch, caplog, failure: str
) -> None:
    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    try:
        h.render("frame-a")
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.images()) == 1)
            if failure == "missing":
                h.latest = {"digest": "frame-gone", "filename": "frame-gone.png", "ext": "png"}
            else:
                h.render("frame-b", shade=128)

                def _broken(*_a: Any) -> Any:
                    raise ValueError("bad PNG")

                monkeypatch.setattr(visionect, "_encode", _broken)
            caplog.clear()
            sign.ack(sign.images()[0].id)
            # The sign keeps frame A and sleeps on it.
            assert sign.wait_for(lambda s: len(s.commands()) == 1)
            for _ in range(3):  # later passes don't retry or log it again
                h.push()
                sign.status()
            sign.wait_for(lambda s: len(s.images()) > 1, timeout=1.0)
            assert len(sign.images()) == 1
        finally:
            sign.close()
        notes = [r for r in caplog.records if "can't send render" in r.getMessage()]
        assert len(notes) == 1
        view = h.svc.sign_view(UUID) or {}
        assert "couldn't be read" in view.get("last_error", "")
    finally:
        h.svc.stop()


def test_a_frame_adopted_after_a_restart_keeps_its_glass_check(harness: Harness) -> None:
    """At startup a frame the sign already shows (its CRC matches) is
    adopted without a resend; its checksum must arm the glass check too."""
    harness.render("frame-a")
    status = StatusPacket(records=STATUS_TAGS, sentinel=0)
    path = harness.renders / "frame-a.png"
    checksum = int(visionect._encode(path, visionect.sign_panel(status)).state_checksum)
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status(with_tag(9, checksum))
        assert _wait(lambda: (harness.svc.sign_view(UUID) or {}).get("in_sync") is True)
        state = harness.svc._signs[UUID]
        assert state.shown_checksum == checksum and state.shown_at > 0
        assert sign.images() == []  # adopted, not resent
        # Much later the sign reports something else: one corrective resend.
        state.shown_at = time.time() - visionect.CRC_CONVERGE_S - 1
        sign.status(with_tag(9, checksum ^ 1))
        assert sign.wait_for(lambda s: len(s.images()) == 1)
    finally:
        sign.close()


def test_an_ack_rearms_the_corrective_resend() -> None:
    from pyvisionect.packets import ControlPacket as Control
    from pyvisionect.session import Acked

    svc = visionect.VisionectService()
    sign = svc._sign(UUID)
    conn = SimpleNamespace(device_id=visionect.uuid_bytes(UUID), closed=False)
    sign.conn = conn
    sign.crc_corrected = True  # spent on the previous frame
    sign.inflight = visionect._Inflight(
        kind="image", packet_id=7, conn=conn, writer=None, digest="frame-b", checksum=5
    )
    event = Acked(packet_id=7, control=Control.ack(), device_id=visionect.uuid_bytes(UUID))
    assert svc._handle_event(conn, event) == UUID
    assert sign.shown_digest == "frame-b" and sign.shown_checksum == 5
    assert not sign.crc_corrected


def test_one_sleep_lasts_at_most_a_day(tmp_path: Path, monkeypatch) -> None:
    from app import quiet_hours

    h = Harness(tmp_path, app_settings=_window_around_now(sleep=True))
    monkeypatch.setattr(
        quiet_hours,
        "quiet_ends_at",
        lambda _window, now, _tz: now + timedelta(days=5),  # a long "away" window
    )
    assert h.svc._sleep_minutes(h.device) == 24 * 60  # type: ignore[arg-type]


def test_a_history_resend_repaints_the_frame_once(harness: Harness) -> None:
    from pyvisionect.packets import ImagePacket

    harness.render("frame-a")
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status()
        assert sign.wait_for(lambda s: len(s.images()) == 1)
        sign.ack(sign.images()[0].id)
        assert _wait(lambda: (harness.svc.sign_view(UUID) or {}).get("in_sync") is True)
        normal = ImagePacket.decode(sign.images()[0].payload).checksum
        # History -> resend: the same frame, flagged for a one-shot refetch.
        harness.latest = {**harness.latest, "force_refetch": True, "timestamp": 1000.0}
        harness.push()
        assert sign.wait_for(lambda s: len(s.images()) == 2)
        resent = ImagePacket.decode(sign.images()[1].payload).checksum
        assert resent != normal  # a checksum the sign can't hold: it repaints
        sign.ack(sign.images()[1].id)
        assert _wait(lambda: harness.consumed == ["lobby"])
        assert harness.svc._signs[UUID].shown_checksum == resent
        harness.push()  # the flag is still set, but that resend was honoured
        sign.wait_for(lambda s: len(s.images()) > 2, timeout=1.0)
        assert len(sign.images()) == 2
        harness.latest = {**harness.latest, "timestamp": 2000.0}  # another resend
        harness.push()
        assert sign.wait_for(lambda s: len(s.images()) == 3)
        # Again a checksum other than the one the sign now holds.
        assert ImagePacket.decode(sign.images()[2].payload).checksum != resent
    finally:
        sign.close()


# -- unknown signs: binding, discovery, bounded state ----------------------------


def _other_uuid(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-000000000000"


UUID_FIELDS = [{"name": "uuid"}]


def test_a_blank_device_binds_only_within_its_arming_window(tmp_path: Path) -> None:
    """The window is a stored time, set when the device is added or its UUID
    cleared. Renaming or saving an old blank device (which rewrites its
    instance file) doesn't reopen it."""
    h = Harness(tmp_path)
    lobby = h.settings.sections["devices"]["lobby"]
    lobby[visionect.AUTO_BIND_KEY] = time.time() - visionect.AUTO_BIND_WINDOW_S - 60
    os.utime(h.device.path, None)  # renamed just now
    save = device_service.save_device_config
    try:
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert _wait(lambda: h.app.config["DISCOVERY_CACHE"].get(visionect.discovery_id(UUID)))
            assert lobby["uuid"] == ""  # not bound: listed as discovered
            # Saving it blank again doesn't reopen the window either.
            assert save(h.settings, h.registry, h.device, {"uuid": ""}, UUID_FIELDS) is None
            assert not h.svc.auto_bind_open(h.device)  # type: ignore[arg-type]
            # Clearing a UUID does.
            assert (
                save(h.settings, h.registry, h.device, {"uuid": _other_uuid(1)}, UUID_FIELDS)
                is None
            )
            assert save(h.settings, h.registry, h.device, {"uuid": ""}, UUID_FIELDS) is None
            h.svc._call_soon(h.svc._rescan)
            assert _wait(lambda: lobby["uuid"] == UUID)
            assert lobby.get(visionect.AUTO_BIND_KEY) is None  # bound: the window closed
            assert h.app.config["DISCOVERY_CACHE"].get(visionect.discovery_id(UUID)) is None
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_one_lock_checks_and_stores_a_sign_binding(tmp_path: Path) -> None:
    """Two saves binding one sign to two devices at once: exactly one wins,
    and the listener's auto-bind doesn't take a sign another device has."""

    class _SlowSettings(FakeSettings):
        def update_for_namespace(self, *args: Any) -> None:
            time.sleep(0.2)  # widen the gap between the check and the write
            super().update_for_namespace(*args)

    h = Harness(tmp_path, uuid=_other_uuid(1))
    hall = FakeDevice("hall", "visionect", SIGN_PANEL, tmp_path / "hall.json")
    h.registry.devices["hall"] = hall
    settings = _SlowSettings(h.settings.sections)
    results: list[str | None] = []
    savers = [
        threading.Thread(
            target=lambda d=d: results.append(
                device_service.save_device_config(
                    settings, h.registry, d, {"uuid": UUID}, UUID_FIELDS
                )
            )
        )
        for d in (h.device, hall)
    ]
    for t in savers:
        t.start()
    for t in savers:
        t.join(5)
    assert sorted(r is None for r in results) == [False, True]
    section = h.settings.sections["devices"]
    bound = [d for d in ("lobby", "hall") if section.get(d, {}).get("uuid") == UUID]
    assert len(bound) == 1
    other = "hall" if bound == ["lobby"] else "lobby"
    h.settings.sections["devices"].setdefault(other, {})["uuid"] = ""
    assert not visionect.bind_sign(settings, h.registry, h.registry.devices[other], UUID)


def test_discovery_ids_are_long_and_never_merge_two_signs(tmp_path: Path) -> None:
    from app.device_service import DEVICE_ID_RE

    entry_id = visionect.discovery_id(UUID)
    assert DEVICE_ID_RE.match(entry_id) and len(entry_id) == len("visionect_") + 20
    assert entry_id != visionect.discovery_id(_other_uuid(1))
    # An entry under this id from another sign is replaced, not merged into.
    h = Harness(tmp_path, uuid=_other_uuid(2))
    cache = h.app.config["DISCOVERY_CACHE"]
    cache.record(entry_id, json.dumps({"uuid": _other_uuid(3), "mac": "aa:bb"}).encode())
    h.svc._signs[UUID] = visionect._Sign(uuid=UUID)
    h.svc._announce_unknown(UUID, StatusPacket(records=STATUS_TAGS, sentinel=0))
    parsed = cache.get(entry_id).parsed
    assert parsed["uuid"] == UUID and "mac" not in parsed


def test_unknown_signs_are_capped_and_forgotten_when_they_leave(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(visionect, "MAX_UNKNOWN_SIGNS", 2)
    h = Harness(tmp_path, uuid=_other_uuid(99))  # no device free to bind
    try:
        assert h.svc.start()
        signs = [FakeSign(h.svc.port, uuid=_other_uuid(n)) for n in range(3)]
        try:
            for sign in signs[:2]:
                sign.status()
            assert _wait(lambda: len(h.svc._signs) == 2)
            signs[2].status()
            assert signs[2].dropped(timeout=5)  # one unknown sign too many
            assert len(h.svc._signs) == 2
        finally:
            for sign in signs:
                sign.close()
        time.sleep(0.3)
        h.svc._call_soon(h.svc._rescan)
        assert _wait(lambda: not h.svc._signs)
        assert h.svc.sign_view(_other_uuid(0)) is None
        assert visionect.uuid_bytes(_other_uuid(0)) not in h.svc._store
        assert visionect.uuid_bytes(_other_uuid(0)) not in h.svc._server.stats.last_seen
        # Their discovered entries stay, so a sleeping sign can still be registered.
        assert h.app.config["DISCOVERY_CACHE"].get(visionect.discovery_id(_other_uuid(0)))
    finally:
        h.svc.stop()


def test_discovered_signs_are_capped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(visionect, "MAX_DISCOVERED", 2)
    h = Harness(tmp_path, uuid=_other_uuid(99))
    try:
        assert h.svc.start()
        for n in range(3):
            sign = FakeSign(h.svc.port, uuid=_other_uuid(n))
            sign.status()
            assert _wait(
                lambda n=n: h.app.config["DISCOVERY_CACHE"].get(
                    visionect.discovery_id(_other_uuid(n))
                )
            )
            sign.close()
        cache = h.app.config["DISCOVERY_CACHE"]
        listed = [n for n in range(3) if cache.get(visionect.discovery_id(_other_uuid(n)))]
        assert listed == [1, 2]
    finally:
        h.svc.stop()


def test_a_status_flood_records_one_heartbeat_at_a_time_latest_wins(
    tmp_path: Path, monkeypatch
) -> None:
    recorded: list[int] = []

    def _slow_record(_app: Any, _device: Any, heartbeat: dict[str, Any]) -> None:
        time.sleep(0.3)
        recorded.append(int(heartbeat["battery_pct"]))

    monkeypatch.setattr(visionect, "_record_heartbeat", _slow_record)
    h = Harness(tmp_path, uuid=UUID)
    try:
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            for pct in range(10, 60):
                sign.status(with_tag(10, pct))
            assert _wait(lambda: recorded and recorded[-1] == 59, timeout=10)
            time.sleep(0.5)
            assert len(recorded) <= 3  # not one executor job per packet
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_binding_keeps_device_settings_saved_meanwhile(tmp_path: Path) -> None:
    """_bind used to copy the device's settings, set the uuid and write the
    copy back, losing anything a request saved in between."""

    class _RacingSettings(FakeSettings):
        def get_section(self, name: str) -> dict[str, Any]:
            section = json.loads(json.dumps(super().get_section(name)))  # a copy, like the store
            if name == "devices":  # a request saves picture quality right after the read
                super().get_section("devices")["lobby"]["dither"] = "floyd"
            return section

    h = Harness(tmp_path)
    h.settings.__class__ = _RacingSettings
    assert h.svc._bind(UUID) is h.device
    stored = FakeSettings.get_section(h.settings, "devices")["lobby"]
    assert stored["uuid"] == UUID and stored["dither"] == "floyd"


def test_a_connection_already_replaced_in_the_library_is_ignored_not_refused() -> None:
    """If a newer same-UUID connection registered before this one's
    DeviceConnected was dispatched, its writer is unknown: ignore it (the
    newer one's event follows) rather than log it as a hijack."""
    svc = visionect.VisionectService()
    newer, stale = object(), SimpleNamespace(device_id=visionect.uuid_bytes(UUID))
    svc._server = SimpleNamespace(connection_for=lambda _key: newer, _writers={})
    sign = svc._sign(UUID)
    sign.conn = SimpleNamespace(closed=False)
    sign.writer = SimpleNamespace(transport=SimpleNamespace(is_closing=lambda: False))
    sign.remote = "10.0.0.5"
    status = StatusPacket(records=STATUS_TAGS, sentinel=0)
    assert svc._connected(stale, UUID, status) == ""
    assert sign.refused is None and sign.status is None


def test_rejected_and_unidentified_identities_leave_no_library_state(
    tmp_path: Path, monkeypatch
) -> None:
    """The library allocates store and stats entries for any UUID a frame
    names, before Tesserae admits a sign. Malformed first statuses and signs
    refused for being one unknown too many never reach the tracked signs, so
    they are pruned as their connections close."""
    monkeypatch.setattr(visionect, "MAX_UNKNOWN_SIGNS", 1)
    h = Harness(tmp_path, uuid=_other_uuid(999))  # no device free to bind
    try:
        assert h.svc.start()
        server = h.svc._server
        for n in range(100):
            sock = _raw(h.svc.port)
            sock.sendall(device_frame(_other_uuid(n), PacketType.STATUS, b"\x01\x02\x03", 1))
            sock.close()
            assert _wait(lambda n=n: server.stats.accepted == n + 1 and server._open == 0)
        assert server.stats.identified == 100  # each one registered a UUID
        assert len(h.svc._store) == 0 and server.stats.last_seen == {}
        # One unknown sign is tracked; the next is refused, and leaves nothing.
        kept = FakeSign(h.svc.port, uuid=_other_uuid(500))
        try:
            kept.status()
            assert _wait(lambda: _other_uuid(500) in h.svc._signs)
            extra = FakeSign(h.svc.port, uuid=_other_uuid(501))
            extra.status()
            assert extra.dropped(timeout=5)
            extra.close()
            assert _wait(lambda: server._open == 1)
            assert set(server.stats.last_seen) == {visionect.uuid_bytes(_other_uuid(500))}
            assert len(h.svc._store) == 1
        finally:
            kept.close()
        assert _wait(lambda: server._open == 0)
        h.svc._call_soon(h.svc._rescan)  # notices it left: an unbound sign is forgotten
        assert _wait(lambda: not h.svc._signs and len(h.svc._store) == 0)
        assert server.stats.last_seen == {}
    finally:
        h.svc.stop()


def test_a_corrective_resend_is_spent_until_a_new_frame_is_acked() -> None:
    """A sign whose CRC never matches gets one corrective resend per frame,
    not one every convergence period: the resend's own ACK doesn't re-arm."""
    from pyvisionect.packets import ControlPacket as Control
    from pyvisionect.session import Acked

    svc = visionect.VisionectService()
    sign = svc._sign(UUID)
    conn = SimpleNamespace(device_id=visionect.uuid_bytes(UUID), closed=False)
    sign.conn = conn
    sign.shown_digest, sign.shown_checksum = "frame-a", 111
    wrong = SimpleNamespace(display_state_crc=222)
    long_ago = time.time() - visionect.CRC_CONVERGE_S - 1

    def _ack(packet_id: int, digest: str) -> None:
        sign.inflight = visionect._Inflight(
            kind="image", packet_id=packet_id, conn=conn, writer=None, digest=digest, checksum=111
        )
        svc._handle_event(
            conn,
            Acked(packet_id=packet_id, control=Control.ack(), device_id=visionect.uuid_bytes(UUID)),
        )

    sign.shown_at = long_ago
    svc._check_glass(sign, wrong)
    assert sign.shown_digest == "" and sign.crc_corrected  # the one corrective resend
    _ack(1, "frame-a")  # its ACK
    assert sign.crc_corrected
    sign.shown_at = long_ago
    svc._check_glass(sign, wrong)
    assert sign.shown_digest == "frame-a"  # no second correction cycle
    _ack(2, "frame-b")  # a new desired frame re-arms it
    assert not sign.crc_corrected


@pytest.mark.parametrize("how", ["published again", "back-off"])
def test_a_render_that_was_missing_is_sent_once_it_is_back(
    harness: Harness, monkeypatch, caplog, how: str
) -> None:
    monkeypatch.setattr(visionect, "FAILED_RETRY_S", 0.3)
    harness.render("frame-a")
    target = harness.renders / "frame-a.png"
    saved = target.read_bytes()
    target.unlink()
    harness.latest = {**harness.latest, "timestamp": 1.0}  # type: ignore[dict-item]
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status()
        assert _wait(
            lambda: "couldn't be read" in (harness.svc.sign_view(UUID) or {}).get("last_error", "")
        )
        harness.push()  # inside the back-off, nothing new: not retried, not logged again
        target.write_bytes(saved)  # the artifact is back, same digest
        if how == "published again":
            harness.latest = {**harness.latest, "timestamp": 2.0}  # type: ignore[dict-item]
        else:
            time.sleep(0.4)
        harness.push()
        assert sign.wait_for(lambda s: len(s.images()) == 1)
    finally:
        sign.close()
    notes = [r for r in caplog.records if "can't send render" in r.getMessage()]
    assert len(notes) == 1


def test_a_status_during_an_encode_doesnt_discard_it(harness: Harness, monkeypatch) -> None:
    calls: list[int] = []
    real = visionect._encode

    def _slow_encode(path: Path, panel: Any) -> Any:
        calls.append(1)
        time.sleep(0.6)
        return real(path, panel)

    monkeypatch.setattr(visionect, "_encode", _slow_encode)
    harness.render("frame-a")
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status()
        time.sleep(0.2)
        sign.status()  # a heartbeat (or a probe's answer) mid-encode, same geometry
        assert sign.wait_for(lambda s: len(s.images()) == 1)
        assert len(calls) == 1
    finally:
        sign.close()


def test_a_frame_left_by_a_history_resend_is_adopted_after_a_restart(harness: Harness) -> None:
    """After a resend the sign holds the deliberately altered checksum; a
    restart must still see that it shows the frame, not repaint it."""
    harness.render("frame-a")
    status = StatusPacket(records=STATUS_TAGS, sentinel=0)
    path = harness.renders / "frame-a.png"
    checksum = int(visionect._encode(path, visionect.sign_panel(status)).state_checksum)
    held = visionect.perturbed_checksum(checksum)
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status(with_tag(9, held))
        assert _wait(lambda: (harness.svc.sign_view(UUID) or {}).get("in_sync") is True)
        assert harness.svc._signs[UUID].shown_checksum == held
        sign.wait_for(lambda s: len(s.images()) > 0, timeout=1.0)
        assert sign.images() == []
    finally:
        sign.close()


def test_the_signs_own_address_takes_its_uuid_back(tmp_path: Path, monkeypatch) -> None:
    """A LAN host claims a sleeping sign's UUID from another address and keeps
    the connection busy (so it is never probed). When the sign wakes, from the
    address that last acknowledged its frames, it takes the UUID back; the
    host's own ACKs meanwhile don't make its address the trusted one."""
    h = Harness(tmp_path, uuid=UUID)
    ports = {name: free_port() for name in ("sign", "host", "wake", "again")}
    _fake_addresses(
        monkeypatch,
        {
            ports["sign"]: "10.0.0.5",
            ports["host"]: "10.0.0.66",
            ports["wake"]: "10.0.0.5",
            ports["again"]: "10.0.0.66",
        },
    )
    try:
        h.render("frame-a")
        assert h.svc.start()
        sign = FakeSign(h.svc.port, local_port=ports["sign"])
        sign.status()
        assert sign.wait_for(lambda s: len(s.images()) == 1)
        sign.ack(sign.images()[0].id)
        assert _wait(lambda: h.svc._signs[UUID].trusted_ip == "10.0.0.5")
        sign.close()  # asleep
        assert _wait(lambda: h.svc._server._open == 0)
        host = FakeSign(h.svc.port, local_port=ports["host"])
        host.status()  # nothing holds the UUID: accepted
        h.render("frame-b", shade=128)
        h.push()
        assert host.wait_for(lambda s: len(s.images()) == 1)
        host.ack(host.images()[0].id)
        host.status()
        assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("remote") == "10.0.0.66")
        assert h.svc._signs[UUID].trusted_ip == "10.0.0.5"
        woke = FakeSign(h.svc.port, local_port=ports["wake"])
        woke.status()
        assert host.dropped(timeout=5)
        assert not woke.dropped(timeout=0.5)
        view = h.svc.sign_view(UUID) or {}
        assert view.get("remote") == "10.0.0.5"
        assert (view.get("refused") or {}).get("remote") == "10.0.0.66"
        again = FakeSign(h.svc.port, local_port=ports["again"])
        again.status()
        assert again.dropped(timeout=5)  # and while it lives, the host is refused
        for s in (host, woke, again):
            s.close()
    finally:
        h.svc.stop()


def test_busy_addresses_cant_lock_the_sign_out(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(visionect_listener, "MAX_CONNECTIONS", 2)
    h = Harness(tmp_path, uuid=UUID)
    ports = [free_port() for _ in range(4)]
    _fake_addresses(
        monkeypatch,
        {ports[0]: "10.0.0.5", ports[1]: "10.0.0.66", ports[2]: "10.0.0.67", ports[3]: "10.0.0.5"},
    )
    try:
        h.render("frame-a")
        assert h.svc.start()
        sign = FakeSign(h.svc.port, local_port=ports[0])
        sign.status()
        assert sign.wait_for(lambda s: len(s.images()) == 1)
        sign.ack(sign.images()[0].id)
        assert _wait(lambda: h.svc._signs[UUID].trusted_ip == "10.0.0.5")
        sign.close()
        assert _wait(lambda: h.svc._server._open == 0)
        squatters = [_raw(h.svc.port, ports[1]), _raw(h.svc.port, ports[2])]
        assert _wait(lambda: h.svc._server._open == 2)  # the cap is full
        back = FakeSign(h.svc.port, local_port=ports[3])
        back.status()
        assert back.wait_for(lambda s: len(s.images()) >= 0) and not back.dropped(timeout=0.5)
        assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("connected") is True)
        stranger = _raw(h.svc.port)
        assert _closed_soon(stranger)  # anyone else is still refused
        for s in [*squatters, stranger, back.sock]:
            s.close()
    finally:
        h.svc.stop()


def test_restart_adoption_uses_the_status_that_arrived_during_the_encode(
    harness: Harness, monkeypatch
) -> None:
    """The first status reports another CRC; one with the same geometry,
    arriving while the frame encodes, reports the frame's. Adoption must
    read the newer one (it used to read the first: a needless resend)."""
    harness.render("frame-a")
    status = StatusPacket(records=STATUS_TAGS, sentinel=0)
    path = harness.renders / "frame-a.png"
    checksum = int(visionect._encode(path, visionect.sign_panel(status)).state_checksum)
    real = visionect._encode

    def _slow_encode(path: Path, panel: Any) -> Any:
        time.sleep(0.6)
        return real(path, panel)

    monkeypatch.setattr(visionect, "_encode", _slow_encode)
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status(with_tag(9, checksum ^ 1))
        time.sleep(0.2)
        sign.status(with_tag(9, checksum))
        assert _wait(lambda: (harness.svc.sign_view(UUID) or {}).get("in_sync") is True)
        sign.wait_for(lambda s: len(s.images()) > 0, timeout=1.0)
        assert sign.images() == []
    finally:
        sign.close()


def test_a_matching_status_doesnt_rearm_the_correction_of_the_same_frame() -> None:
    """Corrective resend acked, then a matching status, then a mismatch
    again: still no second correction of that frame."""
    from pyvisionect.packets import ControlPacket as Control
    from pyvisionect.session import Acked

    svc = visionect.VisionectService()
    sign = svc._sign(UUID)
    conn = SimpleNamespace(device_id=visionect.uuid_bytes(UUID), closed=False)
    sign.conn = conn
    sign.shown_digest, sign.shown_checksum = "frame-a", 111
    long_ago = time.time() - visionect.CRC_CONVERGE_S - 1
    sign.shown_at = long_ago
    svc._check_glass(sign, SimpleNamespace(display_state_crc=222))
    assert sign.crc_corrected and sign.shown_digest == ""
    sign.inflight = visionect._Inflight(
        kind="image", packet_id=1, conn=conn, writer=None, digest="frame-a", checksum=111
    )
    svc._handle_event(
        conn, Acked(packet_id=1, control=Control.ack(), device_id=visionect.uuid_bytes(UUID))
    )
    svc._check_glass(sign, SimpleNamespace(display_state_crc=111))  # it matches for a while
    sign.shown_at = long_ago
    svc._check_glass(sign, SimpleNamespace(display_state_crc=222))  # then drifts again
    assert sign.shown_digest == "frame-a"


def test_a_long_run_of_render_failures_doesnt_overflow_the_backoff() -> None:
    svc = visionect.VisionectService()
    sign = svc._sign(UUID)
    device = SimpleNamespace(id="lobby")
    sign.failed_digest, sign.failed_tries = "frame-a", 1024
    svc._render_failed(sign, device, "frame-a", 1.0, FileNotFoundError("gone"))  # type: ignore[arg-type]
    assert sign.failed_retry_at - time.monotonic() <= visionect.FAILED_RETRY_MAX_S + 1


# -- the sign's own settings and actions ---------------------------------------


def _params(sign: FakeSign) -> list[list[tuple[int, int, bytes]]]:
    """Each PARAM packet the server sent, as (id, control, value) items."""
    from pyvisionect.packets import decode_payload

    out = []
    for frame in sign.of_type(PacketType.PARAM):
        packet = decode_payload(PacketType.PARAM, frame.payload)
        out.append([(i.id, i.control, i.value) for i in packet.items])
    return out


def _answer(sign: FakeSign, values: dict[int, bytes], control: int = 0) -> None:
    """The sign's answer to a settings read (or, with control 3, a refused write)."""
    from pyvisionect.packets import ParamItem, ParamPacket

    payload = ParamPacket(items=[ParamItem(k, control, v) for k, v in values.items()]).encode()
    sign.sock.sendall(device_frame(sign.uuid, PacketType.PARAM, payload, sign.next_id))
    sign.next_id += 1


def _u32(n: int) -> bytes:
    return n.to_bytes(4, "little")


@pytest.mark.parametrize(
    ("cfg", "want"),
    [
        ({}, {}),
        ({"heartbeat_min": "", "frame_sleep": ""}, {}),
        ({"heartbeat_min": "5"}, {visionect.PARAM_HEARTBEAT: 5}),
        ({"heartbeat_min": "0"}, {}),
        ({"heartbeat_min": "99999"}, {}),
        ({"heartbeat_min": "nope"}, {}),
        ({"frame_sleep": "awake"}, {visionect.PARAM_SLEEP_DISABLE: 1}),
        ({"frame_sleep": "sleep"}, {visionect.PARAM_SLEEP_DISABLE: 0}),
        ({"frame_sleep": "whatever"}, {}),
    ],
)
def test_desired_params(cfg: dict[str, Any], want: dict[int, int]) -> None:
    assert visionect.desired_params(cfg) == want


def test_sign_settings_are_read_then_written_saved_and_read_back(tmp_path: Path) -> None:
    h = Harness(tmp_path, uuid=UUID)
    h.settings.sections["devices"]["lobby"].update(heartbeat_min="5", frame_sleep="awake")
    try:
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            # First the read, and nothing written before the answer.
            assert sign.wait_for(lambda s: len(_params(s)) == 1)
            assert {i[0] for i in _params(sign)[0]} == set(visionect.READ_PARAMS)
            assert all(control == 0 for _, control, _ in _params(sign)[0])
            _answer(sign, {29: _u32(1), 52: _u32(0), 65: b"home-wifi"})
            # Then the two changed settings, a flash save, and a read back.
            assert sign.wait_for(lambda s: len(_params(s)) == 4)
            write, save, reread = _params(sign)[1:]
            assert sorted((i[0], i[1], int.from_bytes(i[2], "little")) for i in write) == [
                (29, 1, 5),
                (52, 1, 1),
            ]
            assert [(i[0], i[1]) for i in save] == [(53, 1)]
            assert {i[0] for i in reread} == set(visionect.READ_PARAMS)
            _answer(sign, {29: _u32(5), 52: _u32(1), 65: b"home-wifi"})
            assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("heartbeat_min") == 5)
            view = h.svc.sign_view(UUID) or {}
            assert view["sleep_disabled"] == 1 and view["wifi"] == "home-wifi"
            # In step now: nothing more is written.
            h.push()
            sign.wait_for(lambda s: len(_params(s)) > 4, timeout=1.5)
            assert len(_params(sign)) == 4
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_a_refused_setting_is_reported_and_not_written_again(tmp_path: Path) -> None:
    h = Harness(tmp_path, uuid=UUID)
    h.settings.sections["devices"]["lobby"].update(heartbeat_min="5")
    try:
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(_params(s)) == 1)
            _answer(sign, {29: _u32(1), 52: _u32(0)})
            assert sign.wait_for(lambda s: len(_params(s)) == 4)
            _answer(sign, {29: _u32(0x005A0000)}, control=3)
            _answer(sign, {29: _u32(1), 52: _u32(0)})  # the read back: unchanged
            assert _wait(lambda: "refused" in (h.svc.sign_view(UUID) or {}).get("param_error", ""))
            h.push()
            sign.wait_for(lambda s: len(_params(s)) > 4, timeout=1.5)
            assert len(_params(sign)) == 4
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_blank_settings_leave_the_sign_alone(tmp_path: Path) -> None:
    h = Harness(tmp_path, uuid=UUID)
    try:
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(_params(s)) == 1)
            _answer(sign, {29: _u32(1), 52: _u32(0)})
            h.push()
            sign.wait_for(lambda s: len(_params(s)) > 1, timeout=1.5)
            assert len(_params(sign)) == 1  # read only
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_actions_need_a_connected_sign(tmp_path: Path) -> None:
    h = Harness(tmp_path, uuid=UUID)
    try:
        assert h.svc.start()
        assert "isn't connected" in (h.svc.request_action("lobby", "reboot") or "")
        assert h.svc.request_action("lobby", "explode") == "Unknown action."
    finally:
        h.svc.stop()


def test_report_in_and_reboot_send_their_commands(tmp_path: Path) -> None:
    h = Harness(tmp_path, uuid=UUID)
    try:
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("connected"))
            assert h.svc.request_action("lobby", "status") is None
            assert h.svc.request_action("lobby", "reboot") is None
            assert sign.wait_for(lambda s: len(s.commands()) == 2)
            kinds = [int.from_bytes(f.payload[:4], "little") for f in sign.commands()]
            assert kinds == [11, 1]  # status request, reboot
            assert (h.svc.sign_view(UUID) or {})["action"]["kind"] == "reboot"
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_full_refresh_repaints_the_frame_the_sign_shows(tmp_path: Path) -> None:
    h = Harness(tmp_path, uuid=UUID)
    try:
        h.render("frame-a")
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.images()) == 1)
            sign.ack(sign.images()[0].id)
            assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("in_sync"))
            assert h.svc.request_action("lobby", "refresh") is None
            assert sign.wait_for(lambda s: len(s.images()) == 2)
            # Once: acking it doesn't send a third.
            sign.ack(sign.images()[1].id)
            sign.wait_for(lambda s: len(s.images()) > 2, timeout=1.5)
            assert len(sign.images()) == 2
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_settings_are_read_only_with_the_picture_shown_and_hold_the_next_picture(
    tmp_path: Path, monkeypatch
) -> None:
    """The settings read never goes out on top of a picture: first the
    picture and its ACK, then the read; a new picture waits for the
    frame's answer (the 32" frame dropped the connection with "protocol
    error" when a picture followed an unanswered settings packet)."""
    monkeypatch.setattr(visionect, "PARAM_WAIT_S", 3.0)
    h = Harness(tmp_path, uuid=UUID)
    try:
        h.render("frame-a")
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.images()) == 1)
            assert _params(sign) == []  # nothing before the ACK
            sign.ack(sign.images()[0].id)
            assert sign.wait_for(lambda s: len(_params(s)) == 1)
            h.render("frame-b", shade=128)
            h.push()
            sign.wait_for(lambda s: len(s.images()) > 1, timeout=1.0)
            assert len(sign.images()) == 1  # held for the answer
            _answer(sign, {29: _u32(30), 52: _u32(0)})
            assert sign.wait_for(lambda s: len(s.images()) == 2, timeout=2.0)
        finally:
            sign.close()
    finally:
        h.svc.stop()


def test_the_exchange_log_records_what_went_each_way(tmp_path: Path) -> None:
    h = Harness(tmp_path, uuid=UUID)
    try:
        h.render("frame-a")
        assert h.svc.start()
        sign = FakeSign(h.svc.port)
        try:
            sign.status()
            assert sign.wait_for(lambda s: len(s.images()) == 1)
            sign.ack(sign.images()[0].id)
            assert sign.wait_for(lambda s: len(_params(s)) == 1)
            _answer(sign, {29: _u32(30), 52: _u32(0), 65: b"home-wifi"})

            def lines() -> list[str]:
                view = h.svc.sign_view(UUID) or {}
                return [f"{way} {text}" for _at, way, text in view.get("exchanges") or []]

            assert _wait(lambda: any("Wi-Fi home-wifi" in line for line in lines()))
            log = lines()
            assert log[0].startswith("in connected from 127.0.0.1")
            assert any(line.startswith("in status (") for line in log)
            assert any(line.startswith("out picture frame-a") for line in log)
            assert "in ack: picture" in log and "out read settings" in log
            assert any("check-in interval 30" in line for line in log)
        finally:
            sign.close()
    finally:
        h.svc.stop()
