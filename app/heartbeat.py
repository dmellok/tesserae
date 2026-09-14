"""Daily heartbeat to api.tesserae.ink.

A once-a-day, best-effort ping that reports low-cardinality, aggregate facts
about this install (version, release channel, platform family, deployment
kind, transport, a bucketed install age, the set of registered device kinds
with per-kind firmware / panel gamut / resolution / wake cadence, a bucketed
device count, a bucketed count of paired companion apps, a bucketed lineup
count, a handful of feature booleans, and a bucketed OTA rollout snapshot) so
the maintainer can see how many installs are active, what firmware and panels
are in the field, and what to prioritise.

Privacy: gated by the master online-features switch (nothing is sent when it's
off); no personal data, no exact device counts, no exact timestamps (the server
buckets to the day). The cadence is deliberately daily, with jitter, and
deduped on disk so a server that restarts many times a day still pings once.
See the privacy page for the exact payload.

Timing: the first heartbeat of an install's life goes out as soon as online
features are on (the wizard's "count me in", the Settings toggle, or the
first UI view), not after the daemon's boot delay. Short-lived trial installs
used to show up in the update check but never in the heartbeat, which skewed
the new-install and retention figures.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import random
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask

from app import install_id as _install_id
from app import online

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 24 * 60 * 60
_JITTER_SECONDS = 2 * 60 * 60
_RETRY_SECONDS = 60 * 60
_CHECK_INTERVAL_SECONDS = 60 * 60
# Boot delay before the very first heartbeat of an install's life. Short on
# purpose: a trial install that is torn down after a few minutes still counts.
_FIRST_DELAY_SECONDS = 5.0

_OS_MAP = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}
# (inclusive upper bound, label); anything above the last bound is "10+".
_DEVICE_BUCKETS = ((0, "0"), (1, "1"), (3, "2-3"), (9, "4-9"))
# Install age in whole days since the install id was minted.
_AGE_BUCKETS = ((0, "0"), (7, "1-7"), (30, "8-30"), (90, "31-90"))
# Device wake cadence, seconds.
_SLEEP_BUCKETS = ((299, "<5m"), (900, "5-15m"), (3600, "15-60m"), (21600, "1-6h"))
_CHANNELS = ("stable", "main", "edge")

# One send at a time: the daemon loop, the UI-triggered kick and the opt-in
# handlers can all decide a heartbeat is due in the same second.
_send_lock = threading.Lock()
_channel_cache: str | None = None


def _state_path(data_root: Path) -> Path:
    return data_root / "core" / "heartbeat.json"


def _next_due(data_root: Path) -> float | None:
    try:
        raw = json.loads(_state_path(data_root).read_text(encoding="utf-8"))
        val = raw.get("next_due")
        return float(val) if isinstance(val, (int, float)) else None
    except Exception:
        return None


def _save_next_due(data_root: Path, ts: float) -> None:
    try:
        path = _state_path(data_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"next_due": ts}), encoding="utf-8")
    except Exception:
        logger.debug("heartbeat: could not persist next_due", exc_info=True)


def _arch() -> str:
    machine = (platform.machine() or "").lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine.startswith("arm"):
        return "arm"
    return "other"


def _bucket(count: int, buckets: tuple[tuple[int, str], ...], top: str) -> str:
    for upper, label in buckets:
        if count <= upper:
            return label
    return top


def _devices_bucket(count: int) -> str:
    return _bucket(count, _DEVICE_BUCKETS, "10+")


def _age_bucket(created_at: str) -> str:
    """Whole days since the install id was minted, bucketed. ``unknown`` when
    the id file carries no usable timestamp (pre-metadata installs)."""
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        days = int((datetime.now(UTC) - created).total_seconds() // 86400)
    except (TypeError, ValueError, AttributeError):
        return "unknown"
    if days < 0:
        return "unknown"
    return _bucket(days, _AGE_BUCKETS, "90+")


def _sleep_bucket(config: Any, schema: Any) -> str:
    """Wake cadence bucket from the stored device config, falling back to the
    kind's declared default. ``always_on`` wins over any interval."""
    cfg = config if isinstance(config, dict) else {}
    if cfg.get("always_on") is True:
        return "always_on"
    raw = cfg.get("sleep_interval_s")
    if raw is None and isinstance(schema, dict):
        spec = schema.get("sleep_interval_s")
        if isinstance(spec, dict):
            raw = spec.get("default")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return "unknown"
    return _bucket(int(raw), _SLEEP_BUCKETS, "6h+")


def _git_exact_tag(repo_root: Path) -> bool | None:
    """True when HEAD sits exactly on a tag, False when it does not, None when
    git is unavailable or the answer can't be determined."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "describe", "--tags", "--exact-match", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 128 and "no tag exactly matches" in (proc.stderr or ""):
        return False
    if proc.returncode == 128 and "fatal: No names found" in (proc.stderr or ""):
        return False
    return None


def channel() -> str:
    """The release channel this build came from.

    ``TESSERAE_CHANNEL`` wins (the Docker image build stamps it: ``edge`` for
    main-branch pushes, ``stable`` for tag builds, and the HA App inherits it
    from the image). A source checkout reports ``stable`` when HEAD is exactly
    on a tag and ``main`` otherwise. Everything else (pip, an old image without
    the stamp) is ``stable``. Cached: the answer can't change while running.
    """
    global _channel_cache
    if _channel_cache is not None:
        return _channel_cache
    env = (os.environ.get("TESSERAE_CHANNEL") or "").strip().lower()
    if env in _CHANNELS:
        _channel_cache = env
        return env
    result = "stable"
    try:
        from app.main import REPO_ROOT

        if (REPO_ROOT / ".git").exists():
            on_tag = _git_exact_tag(REPO_ROOT)
            if on_tag is False:
                result = "main"
    except Exception:
        pass
    _channel_cache = result
    return result


def _deploy() -> str:
    """Coarse deployment substrate. Most specific wins."""
    from app import ha_options

    try:
        if ha_options.is_ha_addon():
            return "ha_addon"
    except Exception:
        pass
    if Path("/.dockerenv").exists():
        return "docker"
    try:
        if "lxc" in Path("/proc/1/environ").read_bytes().decode("utf-8", "ignore"):
            return "lxc"
    except Exception:
        pass
    try:
        from app.main import REPO_ROOT

        if (REPO_ROOT / ".git").exists():
            return "source"
    except Exception:
        pass
    return "pip"


def _experiment_enabled(settings: Any, name: str) -> bool:
    """``experiments.is_enabled`` without a request context: env var, then the
    settings section, then the built-in default."""
    from app import experiments

    env = experiments._env_flag(name)
    if env is not None:
        return env
    try:
        section = settings.get_section("experiments") if settings is not None else {}
        if isinstance(section, dict) and name in section:
            return bool(section[name])
    except Exception:
        pass
    return bool(experiments._DEFAULTS.get(name, False))


def _features(app: Flask, settings: Any, instances: list[Any]) -> dict[str, bool]:
    """Coarse feature booleans: is the thing in use at all. Never a count."""
    relay = False
    try:
        from app import relay_config

        relay = bool(relay_config.is_linked(relay_config.relay_config(settings)))
    except Exception:
        relay = False
    touch = False
    quiet = False
    for device in instances:
        manifest = getattr(device, "manifest", None)
        if not isinstance(manifest, dict):
            continue
        if manifest.get("touch") is True:
            touch = True
        qh = manifest.get("quiet_hours")
        if isinstance(qh, dict) and qh:
            quiet = True
    mcp = False
    with contextlib.suppress(Exception):
        mcp = _experiment_enabled(settings, "mcp")
    return {"relay": relay, "touch": touch, "mcp": mcp, "quiet_hours": quiet}


def _lineups_bucket(app: Flask) -> str:
    try:
        store = app.config.get("DECK_STORE")
        if store is None:
            return "0"
        return _devices_bucket(len(store.all()))
    except Exception:
        return "0"


def _panel_facts(device: Any) -> tuple[str | None, str | None]:
    """(gamut, resolution) from the instance's declared panel, or Nones.
    Resolution is normalised to landscape ``WxH`` so a rotated instance of
    the same panel doesn't read as a different SKU."""
    panel = None
    with contextlib.suppress(Exception):
        panel = device.panel
    if not isinstance(panel, dict):
        return None, None
    gamut = panel.get("gamut")
    gamut = gamut if isinstance(gamut, str) and gamut else None
    res = None
    try:
        w, h = int(panel["w"]), int(panel["h"])
        res = f"{max(w, h)}x{min(w, h)}"
    except (KeyError, TypeError, ValueError):
        res = None
    return gamut, res


def _kind_facts(
    instances: list[Any], status_cache: dict[str, Any], devices_section: dict[str, Any]
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """Per-kind firmware list (legacy ``fw_by_kind`` shape) and the richer
    per-kind object list (``kinds``): firmware, panel gamut, resolution and
    wake cadence. One object per kind; when instances of a kind disagree the
    first instance's panel facts win and the firmware list carries every
    distinct version. Low-cardinality: capped kinds + capped versions."""
    fw_by_kind: dict[str, list[str]] = {}
    facts: dict[str, dict[str, Any]] = {}
    for device in instances:
        kind = device.kind_of
        entry = facts.setdefault(kind, {"kind": kind})
        parsed = (status_cache.get(getattr(device, "id", "")) or {}).get("parsed") or {}
        fw = parsed.get("fw_version")
        fw_str = str(fw).strip() if fw is not None else ""
        if fw_str:
            versions = fw_by_kind.setdefault(kind, [])
            if fw_str not in versions and len(versions) < 16:
                versions.append(fw_str)
        if "gamut" not in entry:
            gamut, res = _panel_facts(device)
            if gamut or res:
                entry["gamut"] = gamut or "unknown"
                entry["res"] = res or "unknown"
        if "sleep" not in entry:
            stored = devices_section.get(str(getattr(device, "id", "")))
            schema = None
            with contextlib.suppress(Exception):
                schema = device.config_schema
            sleep = _sleep_bucket(stored, schema)
            if sleep != "unknown":
                entry["sleep"] = sleep
    fw_by_kind = {k: sorted(v) for k, v in sorted(fw_by_kind.items())[:32]}
    kinds: list[dict[str, Any]] = []
    for kind in sorted(facts)[:32]:
        entry = facts[kind]
        versions = fw_by_kind.get(kind) or []
        # The newest reported version stands for the kind (the API keeps one).
        entry["fw_version"] = versions[-1] if versions else None
        entry.setdefault("gamut", "unknown")
        entry.setdefault("res", "unknown")
        entry.setdefault("sleep", "unknown")
        kinds.append(entry)
    return fw_by_kind, kinds


def _ota_snapshot(app: Flask, instances: list[Any], status_cache: dict[str, Any]) -> dict[str, str]:
    """Bucketed OTA rollout snapshot: how many devices are currently offered
    the kind's release, how many already run it, how many last reported a
    failed outcome. A snapshot, not a flow; the day grain upstream makes it a
    funnel over time. Never names a device or a version."""
    offered = applied = failed = 0
    try:
        store = app.config.get("OTA_RELEASE")
        releases = store.all() if store is not None else {}
    except Exception:
        releases = {}
    if not isinstance(releases, dict):
        releases = {}
    try:
        from app.ota import report as _report

        failure_phases = set(_report.FAILURE_PHASES)
    except Exception:
        failure_phases = {"failed", "rolled_back"}
    for kind, rel in releases.items():
        if not isinstance(rel, dict) or rel.get("state") == "paused":
            continue
        target = str(rel.get("fw_version") or "").strip().lstrip("vV")
        if not target:
            continue
        canary = {str(x) for x in (rel.get("canary_device_ids") or [])}
        promoted = rel.get("state") == "promoted"
        for device in instances:
            if device.kind_of != kind:
                continue
            device_id = str(getattr(device, "id", ""))
            if not promoted and device_id not in canary:
                continue
            offered += 1
            entry = status_cache.get(device_id) or {}
            fw = str((entry.get("parsed") or {}).get("fw_version") or "").strip().lstrip("vV")
            if fw and fw == target:
                applied += 1
            phase = (entry.get("ota") or {}).get("phase")
            if phase in failure_phases:
                failed += 1
    return {
        "offered": _devices_bucket(offered),
        "applied": _devices_bucket(applied),
        "failed": _devices_bucket(failed),
    }


def build_payload(app: Flask) -> dict[str, Any]:
    """Assemble the heartbeat body from app state. Every value is a family,
    enum, bucket, or boolean; nothing here identifies a person or a schedule."""
    settings = app.config.get("SETTINGS_STORE")
    devices: list[Any] = []
    try:
        registry = app.config.get("DEVICE_REGISTRY")
        if registry is not None:
            devices = registry.all()
    except Exception:
        devices = []

    # ``registry.all()`` returns the built-in device *kinds* and hardware
    # SKUs (``kind_of is None``) alongside the operator's actual *instances*
    # (``kind_of`` set to the kind they inherit from). There are 20+ catalog
    # kinds, so counting them made every install report "10+" devices no
    # matter how many panels the operator owns. Only instances are real
    # hardware, so the count + transport + kinds all key off them.
    instances = [d for d in devices if getattr(d, "kind_of", None)]

    kinds = sorted({d.kind_of for d in instances})[:32]

    status_cache: dict[str, Any] = app.config.get("DEVICE_STATUS") or {}
    devices_section: dict[str, Any] = {}
    try:
        section = settings.get_section("devices") if settings is not None else {}
        if isinstance(section, dict):
            devices_section = section
    except Exception:
        devices_section = {}
    fw_by_kind, kind_objects = _kind_facts(instances, status_cache, devices_section)

    transports: set[str] = set()
    for device in instances:
        with contextlib.suppress(Exception):
            transports.add(device.transport)
    if {"mqtt", "rest"} <= transports:
        transport = "both"
    elif "mqtt" in transports:
        transport = "mqtt"
    elif "rest" in transports:
        transport = "rest"
    else:
        transport = "none"

    ha = False
    try:
        app_section = settings.get_section("app") if settings is not None else {}
        ha = bool(app_section.get("ha_discovery_enabled"))
    except Exception:
        ha = False

    # Companion-app adoption: a bucketed count of paired companion clients
    # (the community iOS app, issue #147). Derived from persistent state (the
    # live tokens in CompanionTokenStore), not from any companion request, so
    # nothing fires on the API call itself. Bucketed + anonymous: only the
    # count leaves, never a client's name, installation id, or app version.
    companion_count = 0
    try:
        companion_store = app.config.get("COMPANION_TOKENS")
        if companion_store is not None:
            companion_count = len(companion_store.list_active())
    except Exception:
        companion_count = 0

    age = "unknown"
    data_root = app.config.get("DATA_ROOT")
    if data_root is not None:
        with contextlib.suppress(Exception):
            meta = _install_id.read_metadata(Path(data_root))
            if meta and meta.get("created_at"):
                age = _age_bucket(meta["created_at"])

    return {
        "install": app.config.get("INSTALL_ID") or "",
        "version": app.config.get("APP_VERSION") or "",
        "channel": channel(),
        "os": _OS_MAP.get(platform.system(), "other"),
        "arch": _arch(),
        "py": f"{sys.version_info.major}.{sys.version_info.minor}",
        "deploy": _deploy(),
        "transport": transport,
        "devices": _devices_bucket(len(instances)),
        "device_kinds": kinds,
        "fw_by_kind": fw_by_kind,
        "kinds": kind_objects,
        "ha": ha,
        "companion": _devices_bucket(companion_count),
        "age": age,
        "lineups": _lineups_bucket(app),
        "features": _features(app, settings, instances),
        "ota": _ota_snapshot(app, instances, status_cache),
    }


def is_due(app: Flask, *, now: float | None = None) -> bool:
    """Whether a heartbeat would go out right now: online features on, a data
    root to persist the gate in, and no future ``next_due`` on disk."""
    settings = app.config.get("SETTINGS_STORE")
    if not online.online_enabled(settings):
        return False
    data_root = app.config.get("DATA_ROOT")
    if data_root is None:
        return False
    now = time.time() if now is None else now
    due = _next_due(data_root)
    return due is None or now >= due


def maybe_send(app: Flask, *, now: float | None = None) -> bool:
    """Send a heartbeat if online features are on and one is due.

    Best-effort and idempotent per day (the on-disk ``next_due`` gate means a
    restart storm sends at most one). Returns whether a heartbeat was sent.
    """
    with _send_lock:
        if not is_due(app, now=now):
            return False
        data_root = app.config["DATA_ROOT"]
        now = time.time() if now is None else now

        payload = build_payload(app)
        sent = online.send_heartbeat(payload)
        # ~daily with jitter on success; retry sooner on a transient failure.
        if sent:
            nxt = now + _INTERVAL_SECONDS + random.uniform(-_JITTER_SECONDS, _JITTER_SECONDS)
        else:
            nxt = now + _RETRY_SECONDS
        _save_next_due(data_root, nxt)

    event_log = app.config.get("EVENT_LOG")
    if event_log is not None:
        with contextlib.suppress(Exception):
            event_log.record(
                type="telemetry",
                source="heartbeat",
                target="api.tesserae.ink",
                status="sent" if sent else "failed",
                extra={
                    "endpoint": "heartbeat",
                    "version": payload["version"],
                    "deploy": payload["deploy"],
                    "devices": payload["devices"],
                },
            )
    return sent


def kick(app: Flask) -> bool:
    """Send a due heartbeat off the calling thread. Cheap when nothing is due
    (one file read), so callers on the request path can fire it freely: the
    opt-in handlers and the header's version check use it so an install's
    first heartbeat leaves within seconds of consent rather than after the
    daemon's boot delay. Returns whether a send was scheduled."""
    if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        if not is_due(app):
            return False
    except Exception:
        return False

    def _run() -> None:
        try:
            maybe_send(app)
        except Exception:
            logger.debug("heartbeat: kick failed", exc_info=True)

    threading.Thread(target=_run, name="tesserae-heartbeat-kick", daemon=True).start()
    return True


def start(app: Flask, *, check_interval: float = _CHECK_INTERVAL_SECONDS) -> None:
    """Start the daily-heartbeat daemon thread. No-op under testing.

    ``PYTEST_CURRENT_TEST`` is checked as well as the ``TESTING`` flag: several
    test fixtures build the app with ``create_app(testing=False)`` (to keep the
    auth gate) and only set ``app.config["TESTING"]`` afterwards, so without the
    env check the daemon would start and fire a real heartbeat once the per-test
    network guard is gone.
    """
    if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
        return

    def _loop() -> None:
        # Small random initial delay so a fleet that upgrades at once doesn't
        # ping in lockstep at boot. An install that has never heartbeated skips
        # the spread: its first ping should not lose a race with a short trial.
        data_root = app.config.get("DATA_ROOT")
        first = data_root is None or _next_due(data_root) is None
        time.sleep(_FIRST_DELAY_SECONDS if first else random.uniform(30, 300))
        while True:
            try:
                maybe_send(app)
            except Exception:
                logger.debug("heartbeat: loop iteration failed", exc_info=True)
            time.sleep(check_interval)

    threading.Thread(target=_loop, name="tesserae-heartbeat", daemon=True).start()
