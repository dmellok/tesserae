"""Push pipeline: render → hand the composition PNG to every renderer →
write each artifact → publish.

Four entry points share the same single-flight + log-the-event tail:

* ``push(page_id)``, render a saved Page through the composer.
* ``push_image(image_bytes, source_label)``, hand arbitrary bytes
  directly to the renderers (Send-page file upload + image URL).
* ``push_webpage(url)``, screenshot an arbitrary URL via Playwright, then
  hand the bytes to the renderers (Send-page webpage tab).
* ``republish(event_id)``, re-publish a past push from history using the
  stored composition PNG, no re-render.

Concurrency: one push at a time, with latest-wins coalescing per
device (v0.69+). Callers wait on a shared ``_lock``; before waiting
they bump a per-device generation counter, and on lock acquire a
stale caller (its generation is no longer the latest for that
device) returns ``status="superseded"`` without firing so an already-
queued newer push wins. User-initiated pushes (Send-page,
test patterns, Push-now, republish) pass ``bypass_coalesce=True`` so
they always fire; scheduler / auto-refresh flows leave the flag
False so back-to-back schedule ticks don't paint twice. The old
``status="busy"`` drop path is retired.

The composition PNG is always written to ``data/core/renders/<comp_digest>.png``
as the canonical thumbnail. Per-renderer artifacts go to
``<digest>.<extension>`` next to it (content-addressed, so two renderers
producing identical output dedupe on disk).

Every push attempt, success, failure, or busy, is logged to
``EventLog`` so the Send-page history tab can list / resend / delete.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.device_loader import DeviceRegistry
from app.palette_profiles import (
    PaletteProfile,
    PaletteProfileStore,
    bundled_profile,
)
from app.panel import (
    device_panel,
    panel_groups_for_push,
    resolve_settings_panel,
)
from app.quiet_hours import device_is_quiet
from app.renderer import BrowserPool, RenderRequest, render_to_png, to_loopback_url
from app.renderer_loader import Renderer, RendererRegistry
from app.state.event_log import EventLog
from app.state.page_store import PageStore, Panel
from app.state.settings_store import SettingsStore
from app.transport import MqttTransport


def _resolve_full_profile(
    *,
    device_id: str,
    settings_store: SettingsStore,
    profile_store: PaletteProfileStore,
) -> PaletteProfile | None:
    """Resolve the full :class:`PaletteProfile` for a device, or None
    when the device has no profile applied. Bundled profiles win over
    user profiles when slugs collide (bundled are read-only + version-
    controlled, so they win the race)."""
    slug_field = [{"name": "palette_profile_slug", "type": "string", "default": ""}]
    raw = settings_store.get_for_runtime("devices", device_id, slug_field)
    slug = str(raw.get("palette_profile_slug") or "").strip()
    if not slug:
        return None
    bundled = bundled_profile(slug)
    if bundled is not None:
        return bundled
    return profile_store.load(slug)


def resolve_render_timezone_id(settings: SettingsStore) -> str | None:
    """Return the IANA zone name (e.g. ``"Europe/London"``) the
    renderer's Chromium should use for ``new Date()``, or ``None`` to
    leave Chromium on its default behaviour.

    Reads ``settings.app.timezone`` on every call so a settings change
    picks up without restarting Playwright. ``"system"`` and
    unparseable values return ``None``, which means the container's
    ``TZ`` env var still applies (pre-fix behaviour).

    Without this, a Docker / HA add-on deployment runs Chromium in a
    UTC container while the user's Tesserae settings say
    ``Europe/London``. The clock widget's ``new Date()`` honours UTC,
    so the rendered frame is an hour behind during BST. Forwarding
    the resolved name via ``new_context(timezone_id=...)`` aligns the
    rendered frame with what the in-browser preview shows."""
    try:
        raw = str(settings.get_section("app").get("timezone") or "system").strip()
    except Exception:
        return None
    if not raw or raw.lower() == "system":
        return None
    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        logger.warning(
            "render timezone: settings.app.timezone=%r is not a known IANA zone, "
            "falling back to container TZ",
            raw,
        )
        return None
    return raw


def _disabled_renderer_ids(settings: SettingsStore) -> set[str]:
    """Renderer ids whose per-install ``Enabled`` flag is off.

    Stored in the ``renderers_enabled`` settings section as a flat
    ``{renderer_id: bool}`` dict; missing entries default to enabled."""
    raw = settings.get_section("renderers_enabled")
    return {rid for rid, enabled in raw.items() if enabled is False}


def _page_needs_per_device_render(page: Any) -> bool:
    """True when ``page`` carries a cell whose widget declares
    ``render.per_device_id: true`` in its manifest.

    v0.71.x introduced this flag so the push pipeline knows to fan out
    per bound device instead of per panel group. The tesserae_status
    bar needs it (per-device battery + Wi-Fi chips); other widgets that
    render identically for every device on a shared panel don't.

    Falls back to False when there's no Flask app context or no
    ``PLUGIN_REGISTRY`` on it (unit-test setups), which keeps the
    pre-v0.71.x panel-group behaviour intact for callers that don't
    care."""
    try:
        from flask import current_app

        registry = current_app.config.get("PLUGIN_REGISTRY")
    except Exception:
        return False
    if registry is None:
        return False
    for cell in getattr(page, "cells", []) or []:
        plugin_id = getattr(cell, "plugin", None)
        if not plugin_id:
            continue
        plugin = registry.get(plugin_id)
        if plugin is None:
            continue
        if plugin.manifest.get("render", {}).get("per_device_id"):
            return True
    return False


logger = logging.getLogger(__name__)

PushStatus = Literal[
    "sent", "busy", "failed", "not_found", "quiet", "held", "superseded", "no_change"
]

# Max bytes we'll pull from a remote image URL (Send-page Image URL tab).
# Larger downloads are rejected before going through Pillow.
_MAX_REMOTE_IMAGE_BYTES: int = 16 * 1024 * 1024
_HTTP_TIMEOUT_S: float = 10.0
# Sweep orphaned render artifacts every N renders (in addition to a sweep
# at startup) so the dir stays bounded on long-running instances.
_PRUNE_EVERY_RENDERS: int = 50

# Vendored Phosphor regular TTF, used by the low-battery overlay to paint
# the warning glyph. Resolved relative to the repo root so it works under
# both ``pip install -e .`` and a packaged install where ``app/`` and
# ``static/`` are siblings.
_PHOSPHOR_TTF_PATH = (
    Path(__file__).resolve().parent.parent
    / "static"
    / "icons"
    / "phosphor"
    / "regular"
    / "Phosphor.ttf"
)

# Font caches keyed by pixel size. Pillow's truetype loader is not free
# (it re-reads the TTF on each call); a busy fan-out hits this twice per
# device, so cache to avoid the I/O.
_phosphor_font_cache: dict[int, Any] = {}
_ui_font_cache: dict[int, Any] = {}


def _phosphor_font(size: int) -> Any | None:
    """Return a cached Pillow truetype handle for the Phosphor TTF at
    the requested pixel size, or ``None`` if the TTF can't be loaded
    (file missing, Pillow lacks freetype). Used by the low-battery
    overlay; callers fall back to a hand-drawn block when this is
    ``None``."""
    size = max(8, int(size))
    cached = _phosphor_font_cache.get(size)
    if cached is not None:
        return cached
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    try:
        font = ImageFont.truetype(str(_PHOSPHOR_TTF_PATH), size=size)
    except (OSError, ValueError):
        return None
    _phosphor_font_cache[size] = font
    return font


def _ui_font(size: int) -> Any | None:
    """Return a cached Pillow truetype handle for a UI text font at the
    requested pixel size. Tries a small set of common system paths so
    the percent text in the low-battery chip renders crisply; falls
    back to Pillow's bitmap default if none are present (still
    readable, just less polished)."""
    size = max(8, int(size))
    cached = _ui_font_cache.get(size)
    if cached is not None:
        return cached
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    # Common DejaVu / Liberation paths across Linux distros, plus macOS
    # system fallbacks. First hit wins; missing files raise OSError and
    # we move on to the next candidate.
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    )
    font: Any
    for path in candidates:
        try:
            font = ImageFont.truetype(path, size=size)
            _ui_font_cache[size] = font
            return font
        except (OSError, ValueError):
            continue
    try:
        font = ImageFont.load_default()
    except Exception:
        return None
    _ui_font_cache[size] = font
    return font


@dataclass(frozen=True)
class RendererResult:
    renderer_id: str
    topic: str
    digest: str
    url: str
    bytes_written: int
    error: str | None = None
    # v0.71.x (r/eink launch feedback): True when the composition_digest
    # matched the device's last-served digest and publish was skipped
    # to save the panel a re-paint. The renderer artifact still lives
    # in the renders dir (or was refreshed via ``touch`` there) so a
    # future HTTP-polled fetch can still serve it; only the outbound
    # publish is skipped.
    unchanged: bool = False


@dataclass(frozen=True)
class PushResult:
    status: PushStatus
    page_id: str
    composition_digest: str | None = None
    duration_s: float = 0.0
    error: str | None = None
    renderers: list[RendererResult] = field(default_factory=list)
    event_id: int | None = None


class PushManager:
    """Single-flight render -> transform -> publish loop with event logging.

    Constructor wiring:
      * ``registry``, RendererRegistry. Empty registry is allowed; in that
        case ``push()`` renders the PNG (and logs the event) but publishes
        nothing.
      * ``page_store``, for resolving a saved Page by id.
      * ``transport``, connected MqttTransport. ``push()`` raises clearly
        if it's not connected and there are renderers to publish.
      * ``settings``, SettingsStore. Used to pick up per-renderer
        settings + the default panel dims (for non-page pushes).
      * ``event_log``, EventLog. Every push attempt writes a row.
      * ``renders_dir``, where to write composition PNGs + per-renderer
        artifacts. Created if missing.
      * ``base_url_fn``, callable returning the current URL prefix the
        panel listener uses to fetch artifacts. Called on every push so
        the port can be captured from the first incoming HTTP request
        rather than hard-coded at construction time.
    """

    def __init__(
        self,
        *,
        registry: RendererRegistry,
        page_store: PageStore,
        transport: MqttTransport,
        settings: SettingsStore,
        event_log: EventLog,
        renders_dir: Path,
        base_url_fn: Callable[[], str],
        devices: DeviceRegistry | None = None,
        browser_pool_fn: Callable[[], BrowserPool | None] | None = None,
        device_status_fn: Callable[[], dict[str, dict[str, Any]]] | None = None,
        palette_profile_store: PaletteProfileStore | None = None,
    ) -> None:
        self._registry = registry
        self._page_store = page_store
        self._transport = transport
        self._settings = settings
        self._event_log = event_log
        self._renders_dir = renders_dir
        self._renders_dir.mkdir(parents=True, exist_ok=True)
        self._base_url_fn = base_url_fn
        # Lazy lookup so the App-settings toggle (``keep_browser_warm``)
        # can flip the warm path on/off without a PushManager rebuild.
        # Returning ``None`` falls back to the cold per-render Chromium
        # spin-up; returning a pool reuses the warm browser.
        self._browser_pool_fn = browser_pool_fn or (lambda: None)
        # Same trick for the device-status cache: read it lazily so each
        # push sees the fresh heartbeat data (battery_pct in particular,
        # which the low-battery overlay reads). Constructor-snapshot
        # would freeze the value at boot.
        self._device_status_fn = device_status_fn or (lambda: {})
        # Optional, enables multi-head routing. When a page sets a
        # device_id, the panel comes from that device's manifest and
        # only its renderers fire in _fan_out.
        self._devices = devices
        # Optional, enables Calibration-tab palette overrides. When None,
        # renderers fall back to the module-level ``_CALIBRATED_PALETTES``
        # or the nominal palette (behaviour before v0.67).
        self._palette_profile_store = palette_profile_store
        self._lock = threading.Lock()
        # Per-device latest-wins coalescing (v0.69). Each call to
        # ``_acquire_or_supersede`` bumps this map before waiting on
        # ``_lock``; on acquire, if the caller's generation is stale
        # relative to the current entry, its render is skipped as
        # "superseded" and the newer push wins. The map is keyed by
        # device_id, with ``None`` bucketing pushes that aren't
        # scoped to a device (they never supersede each other and
        # always process FIFO). Callers can opt out with
        # ``bypass_coalesce=True`` (user-initiated pushes so a
        # panic-click Send never gets silently dropped).
        self._gen_lock = threading.Lock()
        self._device_gens: dict[str | None, int] = {}
        # Renders accumulate in renders_dir as events age out (the event-log
        # cap evicts rows but not their artifacts). Sweep orphans on a
        # rolling basis so the dir stays bounded without a restart.
        self._renders_since_prune = 0
        # Listeners fire synchronously after every push attempt (success
        # or failure). HA discovery uses this to follow pushes. Slow
        # listeners block the request, keep them fast. Exceptions are
        # logged and swallowed so a buggy subscriber can't break a push.
        self._listener_lock = threading.Lock()
        self._listeners: list[Callable[[PushResult], None]] = []
        # Most-recent render per device-id. Populated by ``_fan_out``
        # after each successful publish; read by pull-based transports
        # (``app.trmnl_api.display``) that need to answer "what's the
        # latest frame for this device?" without subscribing to MQTT.
        # Persisted to ``data/core/latest_renders.json`` (lives next to
        # the renders directory so the artifact files and their pointer
        # share a lifecycle) so a Tesserae restart between pushes
        # doesn't leave the Kindle staring at a placeholder until the
        # next scheduled push lands.
        self._latest_renders_path = self._renders_dir.parent / "latest_renders.json"
        self._latest_renders: dict[str, dict[str, Any]] = self._load_latest_renders()

    # -- listeners -------------------------------------------------------

    def add_listener(self, callback: Callable[[PushResult], None]) -> None:
        with self._listener_lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[PushResult], None]) -> None:
        with self._listener_lock, contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    def _notify(self, result: PushResult) -> None:
        with self._listener_lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(result)
            except Exception:
                logger.exception("push listener %r raised", cb)

    # -- latest-render lookup --------------------------------------------

    def latest_render_for(self, device_id: str) -> dict[str, Any] | None:
        """The most recently published render for a device, or ``None``
         if nothing has been pushed since startup.

         Returns a dict ``{digest, ext, filename, timestamp, renderer_id}``
        , same shape ``_fan_out`` writes into the in-memory map. Used
         by pull-based transports (``app.trmnl_api.display``) so a TRMNL
         client polling ``/api/display`` gets the URL of the actual
         frame the renderer just wrote, not a stale guess."""
        return self._latest_renders.get(device_id)

    def _load_latest_renders(self) -> dict[str, dict[str, Any]]:
        """Read the persisted latest-render map from disk if present.

        Survives Tesserae restarts so an HTTP-polled device (TRMNL
        client) gets the actual frame it should have, not a placeholder,
        on the first ``/api/display`` after a server reboot. Any read
        error (missing file, bad JSON, corrupt entry) silently falls
        back to an empty map, the worst case is one placeholder
        render until the next push repopulates."""
        path = self._latest_renders_path
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("latest_renders: failed to read %s, starting empty", path)
            return {}
        if not isinstance(raw, dict):
            return {}
        # Drop entries whose underlying artifact has been swept by the
        # orphan-prune. ``/api/display`` would 404 on that URL otherwise.
        out: dict[str, dict[str, Any]] = {}
        for device_id, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            filename = entry.get("filename")
            if not isinstance(filename, str):
                continue
            if (self._renders_dir / filename).exists():
                out[str(device_id)] = dict(entry)
        return out

    def _save_latest_renders(self) -> None:
        """Atomically persist the latest-render map.

        Writes to a sibling temp file and renames so a crash mid-write
        can't corrupt the live file (one of the few times rename's
        cross-platform atomicity actually matters here). Errors are
        logged but not raised, the cost of an out-of-disk write is
        less than the cost of breaking a push because the disk filled."""
        path = self._latest_renders_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._latest_renders, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            logger.exception("latest_renders: failed to persist to %s", path)

    # -- public API ------------------------------------------------------

    def push(
        self,
        page_id: str,
        *,
        device_ids: set[str] | None = None,
        respect_quiet_hours: bool = False,
        source: str = "page",
        bypass_coalesce: bool = False,
    ) -> PushResult:
        """Render a saved Page through the composer and publish.

        ``device_ids`` (optional): restrict the fan-out to these devices.
        The page renders at each matching device's panel and only that
        device's renderers fire, used to push a dashboard to one display
        even when it's bound to several. ``None`` keeps the default
        behaviour (every device the page is bound to).

        ``respect_quiet_hours`` (default ``False``): when ``True``, the
        device set is filtered against each device's effective
        quiet-hours window before render. If every bound device is
        quiet the push is logged as ``status="quiet"`` and skipped.
        Scheduler firings and webhook calls pass ``True``; manual Send
        / Push-now flows leave it ``False`` so user intent always
        goes through.

        ``source`` (default ``"page"``): the trigger label written to the
        event log so History can chip what kicked it off. Callers pass
        ``"scheduler"``, ``"webhook"``, ``"home_assistant"``, etc."""
        # v0.69 coalescing: page pushes with a single-device target
        # coalesce against that device_id (later pushes for the same
        # device supersede earlier ones). Multi-device or unbound
        # pushes coalesce against the ``None`` bucket, so a schedule
        # firing twice in a row for the same page doesn't paint
        # twice. Manual / user-initiated pushes should pass
        # ``bypass_coalesce=True`` so they always fire.
        coalesce_key: str | None = None
        if device_ids and len(device_ids) == 1:
            coalesce_key = next(iter(device_ids))
        supersede = self._acquire_or_supersede(
            device_id=coalesce_key,
            source=source,
            target=page_id,
            bypass_coalesce=bypass_coalesce,
        )
        if supersede is not None:
            result = PushResult(
                status="superseded",
                page_id=page_id,
                error="newer push for the same device took priority",
            )
        else:
            try:
                result = self._push_page_locked(
                    page_id,
                    device_ids=device_ids,
                    respect_quiet_hours=respect_quiet_hours,
                    source=source,
                )
            finally:
                self._lock.release()
        self._notify(result)
        return result

    def push_image(
        self,
        image_bytes: bytes,
        *,
        source_label: str,
        device_id: str | None = None,
        fit: str | None = None,
        bypass_coalesce: bool = True,
    ) -> PushResult:
        """Hand arbitrary image bytes to every renderer.

        Used by the Send-page File / Image-URL / Gallery flows. Each
        renderer's ``transform()`` fits the input to its panel dims (the
        bundled .bin renderers via ``fit_to_panel``).

        ``device_id`` (optional): when set, only that device's renderers
        fire and its panel dims are used, same routing as a page bound
        to the device. ``fit`` (optional): the fit mode for non-panel-sized
        input (``fit``/``fill``/``stretch``/``center``/``blur``)."""
        supersede = self._acquire_or_supersede(
            device_id=device_id,
            source="file",
            target=source_label,
            bypass_coalesce=bypass_coalesce,
        )
        if supersede is not None:
            result = PushResult(
                status="superseded",
                page_id=source_label,
                error="newer push for the same device took priority",
            )
        else:
            try:
                result = self._push_bytes_locked(
                    image_bytes, source_label, source="file", device_id=device_id, fit=fit
                )
            finally:
                self._lock.release()
        self._notify(result)
        return result

    def push_url_image(
        self,
        url: str,
        *,
        device_id: str | None = None,
        fit: str | None = None,
        bypass_coalesce: bool = True,
    ) -> PushResult:
        """Download an image URL, then ``push_image``. Networking errors
        surface as failed events with the URL as the target."""
        supersede = self._acquire_or_supersede(
            device_id=device_id,
            source="url",
            target=url,
            bypass_coalesce=bypass_coalesce,
        )
        if supersede is not None:
            result = PushResult(
                status="superseded",
                page_id=url,
                error="newer push for the same device took priority",
            )
        else:
            try:
                try:
                    image_bytes = self._fetch_remote_image(url)
                except Exception as err:
                    result = self._log_failure(source="url", target=url, error=f"fetch: {err}")
                else:
                    result = self._push_bytes_locked(
                        image_bytes, url, source="url", device_id=device_id, fit=fit
                    )
            finally:
                self._lock.release()
        self._notify(result)
        return result

    def push_webpage(
        self,
        url: str,
        *,
        viewport_w: int = 1600,
        viewport_h: int = 1200,
        device_id: str | None = None,
        fit: str | None = None,
        bypass_coalesce: bool = True,
    ) -> PushResult:
        """Screenshot an arbitrary URL with Playwright, then publish."""
        supersede = self._acquire_or_supersede(
            device_id=device_id,
            source="webpage",
            target=url,
            bypass_coalesce=bypass_coalesce,
        )
        if supersede is not None:
            result = PushResult(
                status="superseded",
                page_id=url,
                error="newer push for the same device took priority",
            )
        else:
            try:
                started = time.monotonic()
                try:
                    composition = render_to_png(
                        RenderRequest(
                            url=url,
                            viewport_w=viewport_w,
                            viewport_h=viewport_h,
                            timezone_id=self._render_timezone_id(),
                            # External URL render, not our /compose/ page.
                            # Tells the renderer to skip the 15s composer-
                            # mount wait and to use networkidle for the
                            # initial goto so SPAs hydrate before screenshot.
                            is_composer=False,
                        ),
                        pool=self._browser_pool_fn(),
                    )
                except Exception as err:
                    result = self._log_failure(
                        source="webpage",
                        target=url,
                        error=f"render: {err}",
                        duration_s=time.monotonic() - started,
                    )
                else:
                    result = self._push_bytes_locked(
                        composition,
                        url,
                        source="webpage",
                        started=started,
                        device_id=device_id,
                        fit=fit,
                    )
            finally:
                self._lock.release()
        self._notify(result)
        return result

    def republish(self, event_id: int) -> PushResult:
        """Re-publish a past push from its stored composition PNG. No
        re-render, no re-download. Records a new event row tagged
        ``source="resend"`` so history keeps the link to the original."""
        record = self._event_log.get(event_id)
        if record is None:
            result = PushResult(status="not_found", page_id="", error="history record not found")
        elif not record.digest:
            result = PushResult(
                status="failed", page_id=record.target, error="record has no composition digest"
            )
        else:
            comp_path = self._renders_dir / f"{record.digest}.png"
            if not comp_path.exists():
                result = PushResult(
                    status="failed",
                    page_id=record.target,
                    composition_digest=record.digest,
                    error="composition PNG evicted from disk",
                )
            else:
                # Republish is a user-initiated resend from the History
                # page; treat it as user intent (bypass coalescing) so
                # the user's re-send never gets silently superseded.
                self._lock.acquire()
                try:
                    # ``force_publish=True``: resend is an explicit user
                    # action from the History page; even when the same
                    # composition is already the panel's current frame,
                    # honour the click and re-publish (v0.71.x content-
                    # checksum skip otherwise would collapse this into
                    # ``no_change`` and swallow the user's intent).
                    result = self._push_bytes_locked(
                        comp_path.read_bytes(),
                        record.target,
                        source="resend",
                        force_publish=True,
                    )
                finally:
                    self._lock.release()
        self._notify(result)
        return result

    def delete_history(self, event_id: int) -> bool:
        """Delete a history row and, if no other rows still reference the
        composition PNG, drop the PNG too. Returns True if the row was
        deleted. Per-renderer artifacts are LRU-evicted separately."""
        record = self._event_log.get(event_id)
        if record is None:
            return False
        deleted = self._event_log.delete(event_id)
        if deleted and record.digest and not self._event_log.digest_in_use(record.digest):
            for suffix in (".png", ".bin"):
                path = self._renders_dir / f"{record.digest}{suffix}"
                try:
                    path.unlink(missing_ok=True)
                except OSError as err:
                    logger.warning("Could not delete artifact %s: %s", path, err)
        return deleted

    def prune_orphan_renders(self) -> int:
        """Delete render artifacts no longer referenced by any event (and
        any leftover thumbnails). Renders are content-addressed: a file
        whose digest isn't on an event row is dead weight left behind when
        the event-log cap evicted the row that owned it. Safe to call any
        time; returns the number of files removed."""
        keep = self._event_log.referenced_digests()
        try:
            entries = [p for p in self._renders_dir.iterdir() if p.is_file()]
        except OSError:
            return 0
        removed = 0
        for path in entries:
            if path.name.startswith("thumb_") or path.stem not in keep:
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError as err:
                    logger.warning("could not prune render %s: %s", path, err)
        if removed:
            logger.info("pruned %d orphaned render artifact(s)", removed)
        return removed

    # -- internals -------------------------------------------------------

    def _push_page_locked(
        self,
        page_id: str,
        device_ids: set[str] | None = None,
        respect_quiet_hours: bool = False,
        source: str = "page",
    ) -> PushResult:
        started = time.monotonic()
        page = self._page_store.get(page_id)
        if page is None:
            return self._log_failure(
                source=source, target=page_id, status="not_found", error="page not found"
            )
        # Multi-head: the page may target several devices with different
        # panels. Render once per distinct panel (a 4:3 and a portrait
        # panel need different compositions) and fan each frame out only
        # to the devices that share that panel. An empty device list means
        # "no specific device", render at the virtual panel and fan out
        # to every renderer (legacy single-head).
        groups = panel_groups_for_push(page, self._devices, self._settings)
        if device_ids is not None:
            # Restrict each panel group to the requested devices; drop the
            # virtual-panel group (empty device list) and any group with no
            # surviving devices. Lets HA push a dashboard to one display.
            groups = [(panel, [d for d in dids if d in device_ids]) for panel, dids in groups]
            groups = [(panel, dids) for panel, dids in groups if dids]
            if not groups:
                return self._log_failure(
                    source=source,
                    target=page_id,
                    status="failed",
                    error="dashboard targets none of the requested device(s)",
                )
        if respect_quiet_hours:
            groups = self._filter_quiet_devices(groups)
            if not groups:
                # Every bound device is currently quiet. Log a soft skip
                # and return, not a failure (the user intent is
                # respected) but not a successful push either.
                return self._log_quiet_skip(page_id, source=source)
        base_url = self._base_url_fn().rstrip("/")

        all_renderers: list[RendererResult] = []
        group_results: list[PushResult] = []
        # v0.71.x: pages carrying a widget that declares
        # ``render.per_device_id: true`` (e.g. the tesserae_status bar
        # with its per-device battery + wifi chips) must render once per
        # bound device so each device's frame reflects its own status,
        # not a min-across-all-devices aggregate. Everything else keeps
        # the panel-group fan-out (one render → many devices).
        per_device_render = _page_needs_per_device_render(page)
        for panel, group_dids in groups:
            # When per-device-render is on and there ARE bound devices,
            # iterate over each device; a compose URL with ``device_id``
            # tells the composer to expose it via ctx. Otherwise a
            # single render + fan-out per group, the pre-v0.71.x path.
            if per_device_render and group_dids:
                render_targets: list[tuple[str, set[str] | None]] = [
                    (did, {did}) for did in group_dids
                ]
            else:
                render_targets = [("", set(group_dids) if group_dids else None)]
            for target_id, device_filter in render_targets:
                base = f"{base_url}/compose/{page_id}?for_push=1&w={panel.w}&h={panel.h}"
                compose_url = to_loopback_url(
                    f"{base}&device_id={target_id}" if target_id else base
                )
                try:
                    composition_png = render_to_png(
                        RenderRequest(
                            url=compose_url,
                            viewport_w=panel.w,
                            viewport_h=panel.h,
                            timezone_id=self._render_timezone_id(),
                        ),
                        pool=self._browser_pool_fn(),
                    )
                except Exception as err:
                    err_msg = str(err) or type(err).__name__
                    group_results.append(
                        self._log_failure(
                            source=source,
                            target=page_id,
                            error=f"render: {err_msg}",
                            duration_s=time.monotonic() - started,
                        )
                    )
                    continue
                result = self._fan_out(
                    composition_png,
                    panel.model_dump(),
                    source=source,
                    target=page_id,
                    started=started,
                    device_filters=device_filter,
                )
                all_renderers.extend(result.renderers)
                group_results.append(result)

        # Aggregate the per-panel pushes into one result for the caller.
        # Each group already logged its own push + renderer events.
        # ``no_change`` groups count as success from the pipeline's POV;
        # the aggregate is ``no_change`` only when every group was
        # unchanged, ``sent`` when at least one group actually pushed,
        # ``failed`` when any group failed.
        good = [r for r in group_results if r.status in ("sent", "no_change")]
        failed = [r for r in group_results if r not in good]
        if not group_results:
            status: PushStatus = "failed"
        elif failed:
            status = "failed"
        elif all(r.status == "no_change" for r in group_results):
            status = "no_change"
        else:
            status = "sent"
        digest = next((r.composition_digest for r in group_results if r.composition_digest), "")
        return PushResult(
            status=status,
            page_id=page_id,
            composition_digest=digest,
            duration_s=time.monotonic() - started,
            renderers=all_renderers,
            error=None if status == "sent" else "one or more panels failed to render/publish",
        )

    def _push_bytes_locked(
        self,
        image_bytes: bytes,
        source_label: str,
        *,
        source: str,
        started: float | None = None,
        device_id: str | None = None,
        fit: str | None = None,
        force_publish: bool = False,
    ) -> PushResult:
        """Shared tail end of push_image / push_webpage / republish."""
        started = started if started is not None else time.monotonic()
        panel_dims = self._panel_dims_for_send(device_id)
        return self._fan_out(
            image_bytes,
            panel_dims,
            source=source,
            target=source_label,
            started=started,
            device_filters={device_id} if device_id else None,
            image_fit=fit,
            force_publish=force_publish,
        )

    def _fan_out(
        self,
        composition_png: bytes,
        panel_dims: dict[str, Any],
        *,
        source: str,
        target: str,
        started: float,
        device_filters: set[str] | None = None,
        image_fit: str | None = None,
        force_publish: bool = False,
    ) -> PushResult:
        """Common fanout: thumbnail + per-renderer transform / publish / log.

        ``device_filters`` (multi-head): when set, only renderers whose
        ``.device`` is in the set fire, so a frame rendered for one
        panel lands only on the devices that share that panel. ``None``
        fans out to every renderer (legacy / virtual-panel). ``image_fit``
        (optional): fit mode for non-panel-sized input, passed through to
        each renderer's transform; ``None`` keeps each renderer's default."""
        comp_digest = hashlib.sha256(composition_png).hexdigest()[:16]
        thumb_path = self._renders_dir / f"{comp_digest}.png"
        if not thumb_path.exists():
            thumb_path.write_bytes(composition_png)
        else:
            thumb_path.touch()

        panel = Panel(**panel_dims)
        results: list[RendererResult] = []
        disabled = _disabled_renderer_ids(self._settings)
        # v0.71.x content-checksum skip: when the newly-rendered
        # composition matches the last-served composition for a given
        # bound device, don't publish. Saves the panel a re-paint on
        # every scheduled tick, which is a real battery win on the
        # bigger e-ink panels. HTTP-polled devices still get the frame
        # via their next /api/display or /api/v1/device/<id>/frame poll
        # (ETag 304 handles the "same frame" case there already);
        # MQTT-only devices would otherwise re-paint on every retained
        # publish.
        for renderer in self._registry.all():
            if renderer.id in disabled:
                continue
            if device_filters is not None and renderer.device not in device_filters:
                continue
            renderer_start = time.monotonic()
            last = self._latest_renders.get(renderer.device)
            if not force_publish and last and last.get("composition_digest") == comp_digest:
                result = RendererResult(
                    renderer_id=renderer.id,
                    topic=renderer.topic,
                    digest=str(last.get("digest") or ""),
                    url="",
                    bytes_written=0,
                    unchanged=True,
                )
                results.append(result)
                # Bump the freshness timestamp so latest_render_for()
                # callers still see a recent tick, but leave every other
                # field unchanged; the digest / filename / renderer_id
                # already point at the correct artefact.
                last["timestamp"] = time.time()
                self._save_latest_renders()
                self._event_log.record(
                    type="renderer",
                    source=renderer.id,
                    target=renderer.topic,
                    status="no_change",
                    digest=str(last.get("digest") or "") or None,
                    error=None,
                    duration_s=time.monotonic() - renderer_start,
                    extra={
                        "url": "",
                        "bytes_written": 0,
                        "retain": renderer.retain,
                        "composition_digest": comp_digest,
                        "skipped": True,
                    },
                )
                continue
            try:
                result = self._publish_artifact(
                    renderer, composition_png, panel, image_fit=image_fit
                )
            except Exception as err:
                logger.exception("renderer %s failed", renderer.id)
                result = RendererResult(
                    renderer_id=renderer.id,
                    topic=renderer.topic,
                    digest="",
                    url="",
                    bytes_written=0,
                    error=f"{type(err).__name__}: {err}",
                )
            results.append(result)
            # Stamp the latest-render map for the device this renderer
            # is bound to (clones set ``renderer.device`` to the
            # instance id). HTTP-polled transports (TRMNL) read from
            # this map to answer "what's the latest frame for me?"
            # without subscribing to MQTT. MQTT-only devices still
            # populate it harmlessly, useful for future debug / REST
            # access to a device's most recent frame.
            if result.error is None and result.digest:
                self._latest_renders[renderer.device] = {
                    "digest": result.digest,
                    "ext": renderer.extension,
                    "filename": f"{result.digest}.{renderer.extension}",
                    "renderer_id": renderer.id,
                    "timestamp": time.time(),
                    # The composition PNG (always a viewable .png, what
                    # Playwright wrote before the per-renderer transform).
                    # Used by /preview/<device>.png so HA's generic
                    # camera / dashboards / wallboards can poll a stable
                    # URL that always serves a viewable frame, even when
                    # the per-renderer artifact is a packed binary buffer.
                    "composition_digest": comp_digest,
                }
                self._save_latest_renders()
            # One event per renderer per push: lets /events filter for a
            # single renderer's history without scanning every push's
            # nested extras.
            self._event_log.record(
                type="renderer",
                source=renderer.id,
                target=renderer.topic,
                status="sent" if result.error is None else "failed",
                digest=result.digest or None,
                error=result.error,
                duration_s=time.monotonic() - renderer_start,
                extra={
                    "url": result.url,
                    "bytes_written": result.bytes_written,
                    "retain": renderer.retain,
                    "composition_digest": comp_digest,
                },
            )

        duration = time.monotonic() - started
        if not results:
            status: PushStatus = "sent"  # nothing to publish, but render worked
            error: str | None = None
        elif all(r.error is not None for r in results):
            status = "failed"
            error = "one or more renderers failed"
        elif all(r.error is None and r.unchanged for r in results):
            # Every bound renderer's device already had this exact
            # composition; nothing published, log as no_change so the
            # timeline can chip it distinctly from a real push.
            status = "no_change"
            error = None
        elif any(r.error is not None for r in results):
            status = "failed"
            error = "one or more renderers failed"
        else:
            status = "sent"
            error = None

        # Snapshot which devices this push targeted so the History
        # timeline can chip each row with the actual delivery targets
        # instead of falling back to the page's current bindings (which
        # drift over time as users add / remove devices). The set is
        # derived from ``device_filters`` when set, otherwise from every
        # renderer that actually fired (renderer.device on the base
        # kind, clone id on a user-created instance) so that a virtual-
        # panel fan-out still labels the row.
        if device_filters:
            targeted_ids = sorted(device_filters)
        else:
            targeted_ids = sorted(
                {
                    getattr(self._registry.get(r.renderer_id), "device", "")
                    for r in results
                    if r.error is None
                }
                - {""}
            )
        event_id = self._event_log.record(
            type="push",
            source=source,
            target=target,
            status=status,
            digest=comp_digest,
            error=error,
            duration_s=duration,
            extra={
                "renderers": [asdict(r) for r in results],
                "device_ids": targeted_ids,
            },
        )

        # Roll the orphan-render sweep on a cadence (the just-written
        # artifacts are already referenced by the events above, so they're
        # safe). Keeps the renders dir bounded on long-running instances
        # without waiting for a restart.
        self._renders_since_prune += 1
        if self._renders_since_prune >= _PRUNE_EVERY_RENDERS:
            self._renders_since_prune = 0
            self.prune_orphan_renders()

        return PushResult(
            status=status,
            page_id=target,
            composition_digest=comp_digest,
            duration_s=duration,
            error=error,
            renderers=results,
            event_id=event_id,
        )

    def _overlay_low_battery_if_needed(self, composition_png: bytes, device_id: str) -> bytes:
        """Paint a small low-battery chip in the top-right corner when
        the target device is on battery and the level is at or below
        the threshold. Returns the input unchanged when the feature is
        disabled, the device has no heartbeat yet, the device has no
        battery_pct (Pi / mains-powered), the battery is above the
        threshold, or Pillow can't decode the input PNG.

        The icon comes from the vendored Phosphor TTF at
        ``static/icons/phosphor/regular/Phosphor.ttf`` (battery-warning
        glyph, U+E0C8); rendering happens BEFORE the per-renderer
        transform (resize / dither / quantize) so the chip survives the
        panel-specific pipeline and reaches the device exactly as
        painted here.
        """
        if not device_id:
            return composition_png
        app_settings = self._settings.get_section("app") or {}
        if not bool(app_settings.get("low_battery_overlay", True)):
            return composition_png
        try:
            threshold = int(app_settings.get("low_battery_threshold", 15))
        except (TypeError, ValueError):
            threshold = 15
        status = self._device_status_fn().get(device_id) or {}
        parsed = status.get("parsed") or {}
        battery_pct = parsed.get("battery_pct")
        if battery_pct is None:
            return composition_png
        try:
            pct = int(battery_pct)
        except (TypeError, ValueError):
            return composition_png
        if pct > threshold:
            return composition_png
        try:
            from io import BytesIO

            from PIL import Image, ImageDraw
        except ImportError:
            return composition_png
        try:
            img = Image.open(BytesIO(composition_png)).convert("RGB")
        except Exception:
            return composition_png

        # Chip sized as a fraction of panel width so it scales across
        # 400x300 (small TRMNL) through 1872x1404 (big ESP32).
        w, h = img.size
        chip_w = max(64, min(int(w * 0.13), 150))
        chip_h = max(26, min(int(chip_w * 0.42), 52))
        margin = max(6, int(min(w, h) * 0.012))
        x0 = w - margin - chip_w
        y0 = margin
        x1 = x0 + chip_w
        y1 = y0 + chip_h

        draw = ImageDraw.Draw(img)
        # Plain white rectangle. We dropped the black outline because
        # the white-on-dashboard contrast already reads cleanly on
        # every theme we ship, and the 2px stroke picked up dither
        # artifacts on some panel gamuts where the border quantised
        # to a checker pattern.
        draw.rectangle((x0, y0, x1, y1), fill=(255, 255, 255))

        # Phosphor battery-warning glyph, loaded lazily and cached on
        # the class so a busy fan-out doesn't re-open the TTF for every
        # renderer.
        icon_glyph = ""  # ph-battery-warning
        icon_font = _phosphor_font(int(chip_h * 0.72))
        text_font = _ui_font(int(chip_h * 0.46))
        icon_x = x0 + max(4, chip_h // 6)
        # Anchor the icon's ink bottom to the percentage text's
        # baseline so the two read as "on the same line". Centring
        # each glyph in the chip independently left the Phosphor
        # icon visibly floating above the digits because its em-box
        # is taller than the text's; a shared baseline reads cleanly.
        text = f"{pct}%"
        if text_font is not None:
            tbbox = text_font.getbbox(text)
            # Centre the text's ink in the chip first, then read the
            # baseline (= text_y + tbbox[3], the ink descender in em
            # coords). Both glyphs hang from this Y.
            th = tbbox[3] - tbbox[1]
            text_y = y0 + (chip_h - th) // 2 - tbbox[1]
            baseline_y = text_y + tbbox[3]
        else:
            baseline_y = y0 + chip_h - max(4, chip_h // 5)

        if icon_font is not None:
            ibbox = icon_font.getbbox(icon_glyph)
            # Drop the icon so its ink bottom lands on the text
            # baseline. PIL's draw.text uses (x, y) as the top of
            # the em-box, so back out by ibbox[3].
            icon_y = baseline_y - ibbox[3]
            draw.text((icon_x, icon_y), icon_glyph, fill=(208, 56, 28), font=icon_font)
            icon_advance = ibbox[2] - ibbox[0]
        else:
            # Phosphor TTF missing, draw a fallback square block so the
            # chip still communicates SOMETHING. Should never trigger
            # in normal installs since the TTF is vendored.
            block = chip_h // 2
            icon_y = baseline_y - block
            draw.rectangle(
                (icon_x, icon_y, icon_x + block, icon_y + block),
                fill=(208, 56, 28),
            )
            icon_advance = block

        # Percentage text follows the icon, drawn at the baseline-
        # derived top so both share their visual bottom edge.
        text_x = icon_x + icon_advance + 6
        if text_font is not None:
            draw.text((text_x, text_y), text, fill=(0, 0, 0), font=text_font)
        else:
            draw.text((text_x, y0 + chip_h // 3), text, fill=(0, 0, 0))

        out = BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()

    def _publish_artifact(
        self,
        renderer: Renderer,
        composition_png: bytes,
        panel: Panel,
        *,
        image_fit: str | None = None,
    ) -> RendererResult:
        """Run one renderer end-to-end: settings -> transform -> write -> publish."""
        settings = self._settings.get_for_runtime(
            "renderers", renderer.id, renderer.manifest.get("settings", [])
        )
        if image_fit:
            # Per-push fit override for non-panel-sized input. .bin renderers
            # read ``image_fit`` (server-side fit_to_panel); pi_png passes it
            # to the client via its ``scale`` payload field.
            settings = {**settings, "image_fit": image_fit, "scale": image_fit}
        # Calibration-tab palette override: when the device has a profile
        # applied, inject the resolved RGB tuples into settings under the
        # ``_palette_override`` key. .bin renderers read it and pass to
        # :func:`app.quantizer.pack_to_panel_bin` as ``palette_override``,
        # which wins over the module-level ``_CALIBRATED_PALETTES`` lookup
        # when the clone's ``calibrated`` toggle is also on.
        #
        # Phase 2 (v0.67.1) adds ``_profile_tone`` (exposure, s_curve)
        # and ``_profile_dither`` (serpentine, diffusion_strength) side
        # channels for the renderer to pick up. Contrast + saturation
        # stay on the per-clone renderer settings so existing configs
        # keep working; the profile's contrast/saturation get merged in
        # only when they're actively different from their defaults.
        if self._palette_profile_store is not None and renderer.device is not None:
            profile = _resolve_full_profile(
                device_id=renderer.device,
                settings_store=self._settings,
                profile_store=self._palette_profile_store,
            )
            if profile is not None:
                extras: dict[str, Any] = {"_palette_override": profile.palette.as_tuples()}
                extras["_profile_tone"] = {
                    "exposure": profile.tone.exposure,
                    "s_curve": profile.tone.s_curve,
                    "lab_compress_min": profile.tone.lab_compress_min,
                    "lab_compress_max": profile.tone.lab_compress_max,
                }
                extras["_profile_dither"] = {
                    "serpentine": profile.dither.serpentine,
                    "diffusion_strength": profile.dither.diffusion_strength,
                    "color_match": profile.dither.color_match,
                }
                # Phase 3 edge knobs (v0.67.2). Ignored by renderers
                # that don't opt in; wired through esp32_bin / pi_bin /
                # pico_bin. ``preserve_line_art`` costs zero on all-
                # photo dashboards (edge mask is empty).
                extras["_profile_edges"] = {
                    "smoothing_radius": profile.edges.smoothing_radius,
                    "preserve_line_art": profile.edges.preserve_line_art,
                }
                settings = {**settings, **extras}
        # Device-aware low-battery chip: per-renderer so each device's
        # last-known battery decides whether its push wears the warning.
        # A composition fanned out to a Pi + a TRMNL only paints the
        # chip on the TRMNL artifact when its battery is low.
        composition_png = self._overlay_low_battery_if_needed(composition_png, renderer.device)
        artifact = renderer.transform(composition_png, panel=panel, settings=settings)
        digest = hashlib.sha256(artifact).hexdigest()[:16]

        path = self._renders_dir / f"{digest}.{renderer.extension}"
        if not path.exists():
            path.write_bytes(artifact)
        else:
            path.touch()

        payload = renderer.payload(digest, self._base_url_fn().rstrip("/"), settings=settings)
        url = str(payload.get("url", ""))
        # HTTP-polled devices (TRMNL clients) don't subscribe to the MQTT
        # topic; ``/api/display`` reads the frame straight from the
        # latest-renders map populated by ``_fan_out`` after this method
        # returns. Skipping the publish here makes the broker truly
        # optional for TRMNL-only setups, which is how it always should
        # have been. The signal is the device's manifest: HTTP-polled
        # devices declare no ``status_topic`` (per devices/<id>/device.json
        # and ``Device.status_topic``); MQTT devices do.
        if not self._renderer_is_http_polled(renderer):
            self._transport.publish(
                renderer.topic,
                json.dumps(payload).encode("utf-8"),
                qos=1,
                retain=renderer.retain,
            )
        return RendererResult(
            renderer_id=renderer.id,
            topic=renderer.topic,
            digest=digest,
            url=url,
            bytes_written=len(artifact),
        )

    def _renderer_is_http_polled(self, renderer: Renderer) -> bool:
        """Return True when the renderer's bound device fetches frames
        over HTTP rather than subscribing to a broker. Two paths:

        * ``device.status_topic is None`` — the legacy signal for
          TRMNL / KOReader / anything that hits ``/api/display``.
          Kinds opt in by declaring no MQTT topics at all.
        * ``device.transport == "rest"`` — the new per-instance signal
          for the v0.52 REST API. Same kind can have MQTT instances
          AND REST instances; the transport field on each instance
          decides.

        Clones set ``renderer.device`` to the instance id, which the
        device registry indexes the same way as the kind, so the same
        lookup works for both.

        Fails closed: when the device registry isn't wired (test paths
        without a registry) or the device id doesn't resolve, we treat
        the renderer as MQTT-bound and let the publish run. The cost of
        a false-negative (publish on a REST device with no broker) is
        the pre-v0.52 behaviour, so we're never worse off."""
        if self._devices is None:
            return False
        device = self._devices.devices.get(renderer.device)
        if device is None:
            return False
        if device.status_topic is None:
            return True
        return device.transport == "rest"

    def _panel_dims_for_send(self, device_id: str | None = None) -> dict[str, Any]:
        """Pick panel dims for a Send-page push.

        With a ``device_id`` that names a loaded device declaring a panel,
        use that device's dims + rotation (so a manual send to a specific
        display matches its panel). Otherwise fall back to the virtual
        panel (``resolve_settings_panel``) so the preset / custom dims /
        portrait orientation are honoured identically to every other
        code path.

        Crucially: copy ``native_w / native_h`` through. Skipping them
        sends the renderer (e.g. esp32_bin) back to the pre-v0.19.19
        "pack at panel (w, h)" path, which paints ghosts on any panel
        whose composition orientation doesn't match the firmware's
        hardware stride (the PhotoPainter calibration symptom)."""
        if device_id and self._devices is not None:
            device = self._devices.devices.get(device_id)
            if device is not None and device.panel is not None:
                # device_panel handles the preset / manifest matching
                # that lifts the firmware-native stride into Panel -
                # reuse it instead of rebuilding the dict by hand.
                resolved = device_panel(device)
                if resolved is not None:
                    out: dict[str, Any] = {
                        "w": resolved.w,
                        "h": resolved.h,
                        "flip": resolved.flip,
                        # v0.69.18: the push pipeline rebuilds a Panel
                        # dict from the resolved model, then reconstructs
                        # ``Panel(**dict)`` for the renderer. Without
                        # ``vflip`` here the flag round-trips through
                        # ``device_panel()`` correctly but gets dropped
                        # again on the send-side rebuild (issue #65).
                        "vflip": resolved.vflip,
                        "gamut": resolved.gamut,
                        "underscan": resolved.underscan,
                    }
                    if resolved.native_w is not None and resolved.native_h is not None:
                        out["native_w"] = resolved.native_w
                        out["native_h"] = resolved.native_h
                    return out
        panel = resolve_settings_panel(self._settings)
        out2: dict[str, Any] = {"w": panel.w, "h": panel.h}
        if panel.native_w is not None and panel.native_h is not None:
            out2["native_w"] = panel.native_w
            out2["native_h"] = panel.native_h
        return out2

    def _fetch_remote_image(self, url: str) -> bytes:
        """Download an image URL with bounded size + timeout."""
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("URL must be http:// or https://")
        req = urllib.request.Request(url, headers={"User-Agent": "tesserae/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
                data = resp.read(_MAX_REMOTE_IMAGE_BYTES + 1)
        except urllib.error.URLError as err:
            raise RuntimeError(f"download failed: {err}") from err
        if len(data) > _MAX_REMOTE_IMAGE_BYTES:
            raise RuntimeError(f"image exceeds {_MAX_REMOTE_IMAGE_BYTES // (1024 * 1024)} MiB cap")
        return bytes(data)

    def _render_timezone_id(self) -> str | None:
        """See :func:`resolve_render_timezone_id`."""
        return resolve_render_timezone_id(self._settings)

    # -- event-log shortcuts --------------------------------------------

    def _log_busy(self, *, source: str, target: str) -> PushResult:
        event_id = self._event_log.record(
            type="push",
            source=source,
            target=target,
            status="busy",
            error="another push in flight",
        )
        return PushResult(
            status="busy",
            page_id=target,
            error="another push in flight",
            event_id=event_id,
        )

    def _log_superseded(self, *, source: str, target: str) -> PushResult:
        """Record a "superseded" event when this push was coalesced by
        a newer arrival for the same device. Semantically distinct from
        "busy" (which meant "we couldn't queue it at all") and "sent"
        (which means "the panel painted our frame"); History and HA
        callers key off the string, so keep it stable."""
        event_id = self._event_log.record(
            type="push",
            source=source,
            target=target,
            status="superseded",
            error="newer push for the same device took priority",
        )
        return PushResult(
            status="superseded",
            page_id=target,
            error="newer push for the same device took priority",
            event_id=event_id,
        )

    def _acquire_or_supersede(
        self,
        *,
        device_id: str | None,
        source: str,
        target: str,
        bypass_coalesce: bool,
    ) -> str | None:
        """Coalesce + acquire pattern shared across the push entry
        points. Returns ``None`` when the caller can proceed (lock is
        held; caller must ``self._lock.release()`` on the way out).
        Returns ``"superseded"`` when a newer push for the same
        device stole this caller's slot; the event is already logged,
        no lock is held, and the caller should return immediately.

        ``bypass_coalesce=True`` skips the generation check entirely so
        user-initiated pushes (Send-file, test patterns, buttons) always
        fire; they still serialise on ``_lock`` so we never paint two
        frames simultaneously.

        The old non-blocking ``self._lock.acquire(blocking=False)``
        pattern that returned ``"busy"`` on contention is retired
        here: a coalescing wait is more forgiving for the "user hits
        Send while a schedule fires" race."""
        if bypass_coalesce:
            self._lock.acquire()
            return None
        with self._gen_lock:
            my_gen = self._device_gens.get(device_id, 0) + 1
            self._device_gens[device_id] = my_gen
        self._lock.acquire()
        with self._gen_lock:
            latest = self._device_gens.get(device_id, 0)
        if my_gen < latest:
            self._lock.release()
            self._log_superseded(source=source, target=target)
            return "superseded"
        return None

    def _log_failure(
        self,
        *,
        source: str,
        target: str,
        error: str,
        status: PushStatus = "failed",
        duration_s: float = 0.0,
    ) -> PushResult:
        event_id = self._event_log.record(
            type="push",
            source=source,
            target=target,
            status=status,
            error=error,
            duration_s=duration_s,
        )
        return PushResult(
            status=status,
            page_id=target,
            duration_s=duration_s,
            error=error,
            event_id=event_id,
        )

    def _filter_quiet_devices(
        self, groups: list[tuple[Panel, list[str]]]
    ) -> list[tuple[Panel, list[str]]]:
        """Drop devices currently in their effective quiet-hours window
        from each panel group, then drop any group that's now empty.

        Resolves timezone the same way the scheduler does, from
        ``app.timezone`` settings, falling back to host local time when
        unset or ``"system"``."""
        from datetime import datetime
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        app_settings = self._settings.get_section("app")
        tz: Any | None = None
        tz_raw = str(app_settings.get("timezone") or "system").strip()
        if tz_raw and tz_raw.lower() != "system":
            try:
                tz = ZoneInfo(tz_raw)
            except ZoneInfoNotFoundError:
                tz = None
        now = datetime.now(tz) if tz else datetime.now()

        kept: list[tuple[Panel, list[str]]] = []
        for panel, dids in groups:
            surviving: list[str] = []
            for did in dids:
                device = self._devices.devices.get(did) if self._devices is not None else None
                if device is None or not device_is_quiet(app_settings, device, now, tz):
                    surviving.append(did)
            if surviving:
                kept.append((panel, surviving))
        return kept

    def _log_quiet_skip(self, page_id: str, *, source: str = "page") -> PushResult:
        """Record a soft skip when every bound device is currently in
        quiet hours. Not a failure, the user's "no pushes overnight"
        intent is being honoured, but worth surfacing in the Events
        tab so a head-scratching "why didn't this fire?" turns into a
        one-look answer."""
        event_id = self._event_log.record(
            type="push",
            source=source,
            target=page_id,
            status="quiet",
            error="all bound devices in quiet hours",
        )
        return PushResult(
            status="quiet",
            page_id=page_id,
            error="all bound devices in quiet hours",
            event_id=event_id,
        )
