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
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.device_loader import DeviceRegistry
from app.device_preview import retained_device_preview, write_device_preview
from app.dither_regions import has_nearest_region, regions_from_page
from app.http_headers import header_summary, split_user_agent
from app.image_upload import orient_for_panel
from app.net_guard import (
    BlockedURLError,
    assert_operator_url,
    assert_public_url,
    fetch_bytes,
)
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
from app.renderer import (
    BrowserPool,
    CaptureRequest,
    RenderRequest,
    capture_composed,
    origin_of,
    render_to_png,
    to_loopback_url,
)
from app.renderer_loader import Renderer, RendererRegistry
from app.state.event_log import EventLog
from app.state.page_store import PageStore, Panel
from app.state.settings_store import SettingsStore
from app.touch_regions import (
    EXTRACT_INTERACTIVE_JS,
    load_regions,
    load_slots,
    normalize_regions,
    normalize_slots,
    save_regions,
    split_capture_result,
)
from app.transport import MqttTransport
from app.webpage_headers import headers_by_origin_for_page

# Speculative pre-compose cache bounds (issue #49 linger). TTL keeps a
# prewarmed composition usable across a linger window's tap cadence but
# never lets a scheduled push minutes later serve widget data captured at
# touch time; the cap bounds memory (a 1872x1404 composition PNG is a few
# hundred KB).
_PRECOMPOSE_TTL_S = 60.0
_PRECOMPOSE_CAP = 6
_IMAGE_FIT_MODES = frozenset({"fit", "fill", "blur", "stretch", "center"})


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


def _page_needs_per_device_render(page: Any, registry: Any = None) -> bool:
    """True when ``page`` carries a widget that declares
    ``render.per_device_id: true`` in its manifest.

    v0.71.x introduced this flag so the push pipeline knows to fan out
    per bound device instead of per panel group. The tesserae_status
    bar needs it (per-device battery + Wi-Fi chips); other widgets that
    render identically for every device on a shared panel don't. Both
    page shapes are scanned: grid ``cells`` (each ``cell.plugin``) and
    canvas ``canvas.els`` (each ``el.widget`` for a placed widget, plus a
    code element's data ``sources[].key``), so a canvas dashboard's
    status bar is per-device too (#125).

    ``registry`` is the plugin registry to consult. Callers that run
    off the request thread (the scheduler / rotation push loops) must
    pass it explicitly, otherwise the ``current_app`` fallback raises
    outside an app context and the page silently loses per-device
    fan-out, showing a min-across-all-devices status bar (#125). When
    omitted, fall back to ``current_app.config["PLUGIN_REGISTRY"]``.

    Falls back to False when no registry is available (unit-test
    setups), which keeps the pre-v0.71.x panel-group behaviour intact
    for callers that don't care."""
    if registry is None:
        try:
            from flask import current_app

            registry = current_app.config.get("PLUGIN_REGISTRY")
        except Exception:
            return False
    if registry is None:
        return False

    def _wants(plugin_id: Any) -> bool:
        if not plugin_id:
            return False
        plugin = registry.get(str(plugin_id))
        return bool(plugin and plugin.manifest.get("render", {}).get("per_device_id"))

    # Grid pages: each cell names its widget in ``plugin``.
    for cell in getattr(page, "cells", []) or []:
        if _wants(getattr(cell, "plugin", None)):
            return True
    # Canvas pages: a placed widget (``el.widget``), or a code element that
    # pulls a per-device widget as a data source (``el.sources[].key``).
    canvas = getattr(page, "canvas", None)
    for el in (getattr(canvas, "els", None) or []) if canvas is not None else []:
        if _wants(getattr(el, "widget", None)):
            return True
        for src in getattr(el, "sources", None) or []:
            if _wants(getattr(src, "key", None)):
                return True
    return False


logger = logging.getLogger(__name__)


def _unbound_broadcast_enabled(settings: Any) -> bool:
    """True when the legacy 'an unbound Send broadcasts to every base
    renderer' path is explicitly opted into (single-head MQTT setups
    that never bind a device). Off by default: binding is the delivery
    model, so an unbound Send no-ops instead of broadcasting."""
    value = (settings.get_section("app") or {}).get("unbound_broadcast", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


PushStatus = Literal[
    "sent", "busy", "failed", "not_found", "quiet", "held", "superseded", "no_change", "unbound"
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


def _apply_framing(
    image_bytes: bytes,
    panel_dims: dict[str, Any],
    framing: dict[str, float],
) -> bytes:
    """Apply a Companion ``image_framing`` intent to uploaded image bytes.

    Resolves the normalized focus + zoom against this push's composition
    dims and returns a panel-sized PNG. Producing the final frame here
    (rather than threading the crop into every renderer's transform) keeps
    the adapter in one place: the crop's aspect matches the panel by
    construction, so each renderer's ``fill`` fit degenerates to a no-op,
    including pi_png whose fit runs client-side. Orientation is normalized
    before the crop so the focus addresses the image as displayed
    (``fit_to_panel`` owns both steps in that order).
    """
    import io

    from PIL import Image, ImageOps

    from app.quantizer import fit_to_panel, resolve_framing_crop

    target_w = int(panel_dims.get("w") or 0)
    target_h = int(panel_dims.get("h") or 0)
    if target_w <= 0 or target_h <= 0:
        return image_bytes
    with Image.open(io.BytesIO(image_bytes)) as img:
        normalized = ImageOps.exif_transpose(img)
        crop = resolve_framing_crop(
            source_w=normalized.width,
            source_h=normalized.height,
            target_w=target_w,
            target_h=target_h,
            focus_x=float(framing["focus_x"]),
            focus_y=float(framing["focus_y"]),
            zoom=float(framing["zoom"]),
        )
        framed = fit_to_panel(
            normalized,
            target_w=target_w,
            target_h=target_h,
            scale="fill",
            crop=crop,
        )
    out = io.BytesIO()
    framed.save(out, format="PNG")
    return out.getvalue()


@dataclass(frozen=True)
class RendererResult:
    renderer_id: str
    topic: str
    digest: str
    url: str
    bytes_written: int
    preview_digest: str | None = None
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
    event_ids: tuple[int, ...] = ()


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
        plugin_registry_fn: Callable[[], Any] | None = None,
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
        # Plugin registry accessor, used to decide per-device render
        # fan-out (#125). The push loops run off the request thread
        # (scheduler / rotation), where the ``current_app`` fallback in
        # ``_page_needs_per_device_render`` raises and silently disables
        # fan-out; supplying it here keeps the decision correct on every
        # push path. Lazy so a plugin reload swaps the registry without a
        # PushManager rebuild.
        self._plugin_registry_fn = plugin_registry_fn or (lambda: None)
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
        # Speculative composition cache (issue #49 linger). ``prewarm_page``
        # captures the compositions a touch session is likely to ask for
        # next (the rotation's prev/next pages) so the synchronous push a
        # touch action triggers skips the Playwright capture, which is
        # where the post-touch latency lives. Keyed by compose URL +
        # content token; entries are consume-once and short-TTL so a
        # scheduled push minutes later can never serve stale widget data.
        self._precompose_lock = threading.Lock()
        self._precompose: OrderedDict[
            str, tuple[float, bytes, list[dict[str, Any]], list[dict[str, Any]]]
        ] = OrderedDict()
        # Deck pre-render cache (Decks feature): device_id -> {page_id -> render
        # info}. Holds several fully-rendered frames per device ready to promote
        # into the live ``_latest_renders`` slot the instant navigation asks for
        # one, so a button / touch that moves between a deck's pages skips the
        # on-the-fly render. Warmed silently (see ``warm_deck_page``) so it never
        # disturbs the frame the device is currently showing; kept in memory
        # (re-warmed on restart) but its digests are GC-protected via
        # ``_live_digests`` so the prune can't delete a warmed artifact.
        self._deck_renders: dict[str, dict[str, dict[str, Any]]] = {}
        # Offline-album frame cache (frame-cache collections, #177): the same
        # silent-warm mechanism as decks, keyed device_id -> frame_id -> render
        # info. An album frame is one gallery image rendered to the device's
        # panel; warmed via ``warm_album_frame`` so it never disturbs the live
        # frame, and served through the ``/collection`` manifest endpoints.
        self._album_renders: dict[str, dict[str, dict[str, Any]]] = {}
        # Post-action frame patches (hybrid render mode, schema 2):
        # device_id -> patch document, each anchored to the frame digest
        # the device is showing and superseded whenever a newer frame
        # lands in ``_latest_renders``. In-memory only (a patch outlives
        # its usefulness in seconds); blob files are content-addressed
        # ``overlay-patch-<digest>.bin`` in the renders dir, deleted on
        # supersede, and swept here at startup since a restart forgets
        # the documents that reference them.
        self._patch_docs: dict[str, dict[str, Any]] = {}
        self._patch_seq = 0
        # Grace slot: the entry each device's live frame most recently
        # replaced, kept answering digest-addressed lookups for ~60 s so
        # a device mid-linger on the old digest isn't orphaned by a
        # re-render (see previous_render_for). In-memory only.
        self._previous_renders: dict[str, dict[str, Any]] = {}
        # Frame-digest lineage: digest -> composition digest for the last
        # N frames each device was served. Region reports against a
        # superseded digest resolve their layout through this (protocol
        # v2 staleness is anchored to LAYOUT, not pixels), so a dashboard
        # whose pixels re-render every 30 s doesn't drop every tap that
        # races a render. In-memory only; N=10 generations.
        self._digest_history: dict[str, OrderedDict[str, str]] = {}
        for stale in self._renders_dir.glob("overlay-patch-*.bin"):
            with contextlib.suppress(OSError):
                stale.unlink()

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

    def previous_render_for(
        self, device_id: str, *, max_age_s: float = 60.0
    ) -> dict[str, Any] | None:
        """The render a device's live frame most recently replaced,
        while it is still inside the grace window. A device mid-linger
        on the old digest (1 s /frame/data polls, a manifest fetch it
        was about to make) must not be orphaned the instant a re-render
        lands; digest-addressed lookups fall back to this entry so the
        old frame keeps answering until the device's next /frame poll
        moves it forward."""
        with self._lock:
            entry = self._previous_renders.get(device_id)
            if entry is None:
                return None
            superseded = float(entry.get("superseded_at") or 0)
            if time.time() - superseded > max_age_s:
                return None
            return dict(entry)

    def _retire_live_locked(self, device_id: str, new_digest: str) -> None:
        """Move the current live entry into the grace slot before a new
        frame replaces it (no-op when the digest isn't changing), and
        record its digest -> composition lineage for layout-anchored
        staleness checks."""
        old = self._latest_renders.get(device_id)
        if old is None or str(old.get("digest") or "") == new_digest:
            return
        self._previous_renders[device_id] = {**old, "superseded_at": time.time()}
        old_digest = str(old.get("digest") or "")
        old_comp = str(old.get("composition_digest") or "")
        if old_digest and old_comp:
            history = self._digest_history.setdefault(device_id, OrderedDict())
            history[old_digest] = old_comp
            history.move_to_end(old_digest)
            while len(history) > 10:
                history.popitem(last=False)

    def composition_for_digest(self, device_id: str, digest: str) -> str | None:
        """The composition digest a frame digest rendered from: the live
        frame, the grace slot, or one of the device's last ~10 retired
        frames. None = unknown / too old to resolve."""
        if not digest:
            return None
        with self._lock:
            live = self._latest_renders.get(device_id)
            if live and str(live.get("digest") or "") == digest:
                return str(live.get("composition_digest") or "") or None
            prev = self._previous_renders.get(device_id)
            if prev and str(prev.get("digest") or "") == digest:
                return str(prev.get("composition_digest") or "") or None
            return self._digest_history.get(device_id, OrderedDict()).get(digest)

    def latest_render_for(self, device_id: str) -> dict[str, Any] | None:
        """The most recently published render for a device, or ``None``
        if nothing has been pushed since startup.

        Returns a dict ``{digest, ext, filename, preview_digest, timestamp,
        renderer_id}``, same shape ``_fan_out`` writes into the in-memory
        map. Used
        by pull-based transports (``app.trmnl_api.display``) so a TRMNL
        client polling ``/api/display`` gets the URL of the actual
        frame the renderer just wrote, not a stale guess."""
        return self._latest_renders.get(device_id)

    def forget_device(self, device_id: str) -> bool:
        """Drop every per-device frame slot this manager holds.

        Called when a device is deleted with the wipe option (issue #199).
        Without it the persisted latest-render entry outlives the device, and
        because renders are content-addressed the artifact is still on disk, so
        registering the same device id again gets served the frame from before
        the wipe instead of a 204. Returns whether anything was held.

        The warmed deck / album caches and the patch document go too: they are
        keyed by device id and would otherwise be promoted into the live slot by
        the first navigation after a re-register. Artifact files are left to the
        render prune, which is content-addressed and shared across devices.
        """
        with self._lock:
            had = self._latest_renders.pop(device_id, None) is not None
            self._previous_renders.pop(device_id, None)
            self._deck_renders.pop(device_id, None)
            self._album_renders.pop(device_id, None)
            self._patch_docs.pop(device_id, None)
            if had:
                self._save_latest_renders()
        return had

    def last_served_render_for(self, device_id: str) -> dict[str, Any] | None:
        """The full frame most recently handed to a REST device.

        This is deliberately a delivery-side snapshot, not a claim that the
        physical panel completed its download or refresh.  The fields live on
        the persisted latest-render entry so they survive restarts without a
        second state store.

        ``served_at`` is the POSIX moment of the handover, and is absent for a
        device last served before the field started being written.
        """
        entry = self._latest_renders.get(device_id)
        if not isinstance(entry, dict):
            return None
        digest = entry.get("last_served_digest")
        if not isinstance(digest, str) or not digest:
            return None
        served_at = entry.get("last_served_at")
        return {
            "digest": digest,
            "preview_digest": entry.get("last_served_preview_digest"),
            "served_at": float(served_at) if isinstance(served_at, (int, float)) else None,
        }

    def record_frame_served(self, device_id: str, render: dict[str, Any]) -> None:
        """Persist the render snapshot returned by a REST ``/frame`` poll.

        ``render`` is the response's captured latest-render entry.  A newer
        render may land while that response is being assembled, so record the
        captured digest on the current entry rather than assuming it is still
        the current digest.

        ``last_served_at`` is stamped only when the served digest *changes*.
        This route runs on every poll, 304s included, so stamping every call
        would rewrite the file constantly and would answer "when did the
        device last check in" rather than "when did this frame become the one
        it holds", which is what the Companion timeline measures its progress
        interval from.  A device whose frame predates this field therefore
        carries no timestamp until its next new frame, which is the honest
        answer rather than a backfilled guess.
        """
        digest = render.get("digest")
        if not isinstance(digest, str) or not digest:
            return
        preview_digest = render.get("preview_digest")
        with self._lock:
            current = self._latest_renders.get(device_id)
            if current is None:
                return
            changed = current.get("last_served_digest") != digest
            current["last_served_digest"] = digest
            if changed:
                current["last_served_at"] = time.time()
            if isinstance(preview_digest, str) and preview_digest:
                changed = changed or current.get("last_served_preview_digest") != preview_digest
                current["last_served_preview_digest"] = preview_digest
            else:
                changed = changed or "last_served_preview_digest" in current
                current.pop("last_served_preview_digest", None)
            if changed:
                self._save_latest_renders()

    def has_pending_render(self, device_id: str) -> bool:
        """Whether the latest render differs from the last REST-served frame."""
        entry = self._latest_renders.get(device_id)
        if not isinstance(entry, dict):
            return False
        latest_digest = entry.get("digest")
        if not isinstance(latest_digest, str) or not latest_digest:
            return False
        return latest_digest != entry.get("last_served_digest")

    def _replace_latest_render_locked(self, device_id: str, info: dict[str, Any]) -> None:
        """Replace a live render while carrying its delivery-side snapshot.

        Callers hold ``_lock``.  A render promotion changes what the server
        wants to serve next; it must not rewrite what the device fetched last.
        """
        previous = self._latest_renders.get(device_id)
        replacement = dict(info)
        for key in (
            "last_served_digest",
            "last_served_preview_digest",
            "last_served_at",
        ):
            replacement.pop(key, None)
            if previous is not None and key in previous:
                replacement[key] = previous[key]
        self._retire_live_locked(device_id, str(replacement.get("digest") or ""))
        self._latest_renders[device_id] = replacement
        self._save_latest_renders()

    def consume_force_refetch(self, device_id: str) -> None:
        """Clear the one-shot resend refetch flag (#119) after a REST
        client has been served a 200 for it, so subsequent polls of the
        same unchanged frame go back to 304 (deep-sleep battery win)."""
        with self._lock:
            entry = self._latest_renders.get(device_id)
            if entry and entry.get("force_refetch"):
                entry["force_refetch"] = False
                self._save_latest_renders()

    def touch_regions_for(self, comp_digest: str) -> list[dict[str, Any]]:
        """Touch region map extracted when this composition rendered
        (issue #49). Empty when the composition had no touch annotations
        or the digest is unknown."""
        if not comp_digest:
            return []
        return load_regions(self._renders_dir, comp_digest)

    def overlay_slots_for(self, comp_digest: str) -> list[dict[str, Any]]:
        """Overlay value slots extracted when this composition rendered
        (hybrid render mode). Empty when the composition had no
        ``data-overlay-key`` annotations or the digest is unknown."""
        if not comp_digest:
            return []
        return load_slots(self._renders_dir, comp_digest)

    def invalidate_latest_render(self, device_id: str) -> bool:
        """Forget a device's latest render so ``/frame`` reports 204 (no
        frame yet) until the next push repaints it.

        Used when a device's renderer changes under it (e.g. a wire-format
        switch png -> bmp): the cached artifact was written by the old
        renderer, in the old format, so it must not keep being served to a
        client that now asks for the other format. Drops only the pointer,
        not the artifact file (another device may share the digest); the
        next push regenerates in the new format and repopulates the map.
        Returns True when an entry was actually removed."""
        with self._lock:
            existed = self._latest_renders.pop(device_id, None) is not None
            if existed:
                self._save_latest_renders()
        return existed

    # -- deck pre-render cache (Decks feature) ---------------------------

    def warm_deck_page(self, page_id: str, device_id: str) -> bool:
        """Render a deck page for a device into the pre-render cache WITHOUT
        changing the frame the device is currently serving. Returns True when a
        frame was cached; best-effort, a render miss / failure returns False.

        Renders under the push lock (like a normal push), so warms serialise
        with real pushes rather than racing them."""
        stamp: dict[str, dict[str, Any]] = {}
        with self._lock:
            try:
                self._push_page_locked(
                    page_id,
                    device_ids={device_id},
                    source="deck_warm",
                    force_publish=True,
                    stamp_into=stamp,
                )
            except Exception:
                logger.exception("deck warm failed page=%s device=%s", page_id, device_id)
                return False
            info = stamp.get(device_id)
            if info is None:
                return False
            self._deck_renders.setdefault(device_id, {})[page_id] = info
        return True

    def promote_deck_page(self, device_id: str, page_id: str) -> bool:
        """Swap a pre-warmed deck frame into the device's live slot so the next
        ``/frame`` serves it with no render. Returns False on a cache miss, the
        caller should then fall back to an on-the-fly push."""
        with self._lock:
            info = self._deck_renders.get(device_id, {}).get(page_id)
            if info is None:
                return False
            self._replace_latest_render_locked(device_id, info)
            # The live frame changed; a patch anchored to the old frame
            # must not survive it.
            self._drop_patches_locked(device_id, keep_digest=str(info.get("digest") or ""))
        return True

    def has_warm_deck_page(self, device_id: str, page_id: str) -> bool:
        return page_id in self._deck_renders.get(device_id, {})

    def deck_render_for(self, device_id: str, page_id: str) -> dict[str, Any] | None:
        """The warmed render info for one deck page of a device
        (``{digest, ext, filename, ...}``, same shape as
        :meth:`latest_render_for`), or None when not warmed. Used by the
        device-facing deck manifest / frame endpoints (``app.deck_sync``)."""
        with self._lock:
            info = self._deck_renders.get(device_id, {}).get(page_id)
            return dict(info) if info is not None else None

    def clear_deck_cache(self, device_id: str, *, keep_pages: set[str] | None = None) -> None:
        """Drop warmed frames for a device: all of them, or all except
        ``keep_pages``. Used when a deck's page set changes or a device unbinds
        so stale warmed frames don't linger (and stop pinning their artifacts)."""
        with self._lock:
            if keep_pages is None:
                self._deck_renders.pop(device_id, None)
                return
            per = self._deck_renders.get(device_id)
            if per is None:
                return
            for pid in [p for p in per if p not in keep_pages]:
                per.pop(pid, None)
            if not per:
                self._deck_renders.pop(device_id, None)

    # -- offline-album frame cache (frame-cache collections, #177) -------

    def warm_album_frame(
        self, frame_id: str, device_id: str, image_bytes: bytes, *, fit: str
    ) -> bool:
        """Render one album image for a device into the pre-render cache WITHOUT
        changing the frame the device is currently serving. ``frame_id`` is the
        album's stable id for the image; ``fit`` is the panel fit mode
        (``fit`` / ``fill``). Returns True when a frame was cached; best-effort,
        a render miss / failure returns False.

        Renders under the push lock (like ``warm_deck_page``), so warms
        serialise with real pushes rather than racing them."""
        stamp: dict[str, dict[str, Any]] = {}
        with self._lock:
            try:
                panel_dims = self._panel_dims_for_send(device_id)
                self._fan_out(
                    image_bytes,
                    panel_dims,
                    source="album_warm",
                    target=frame_id,
                    started=time.monotonic(),
                    device_filters={device_id},
                    image_fit=fit,
                    force_publish=True,
                    stamp_into=stamp,
                )
            except Exception:
                logger.exception("album warm failed frame=%s device=%s", frame_id, device_id)
                return False
            info = stamp.get(device_id)
            if info is None:
                return False
            self._album_renders.setdefault(device_id, {})[frame_id] = info
        return True

    def album_render_for(self, device_id: str, frame_id: str) -> dict[str, Any] | None:
        """The warmed render info for one album frame of a device
        (``{digest, ext, filename, ...}``, same shape as
        :meth:`latest_render_for`), or None when not warmed. Used by the
        device-facing collection manifest / frame endpoints
        (``app.collection_sync``)."""
        with self._lock:
            info = self._album_renders.get(device_id, {}).get(frame_id)
            return dict(info) if info is not None else None

    def clear_album_cache(self, device_id: str, *, keep_frames: set[str] | None = None) -> None:
        """Drop warmed album frames for a device: all of them, or all except
        ``keep_frames``. Used when an album's image set / order changes or a
        device unbinds so stale warmed frames don't linger."""
        with self._lock:
            if keep_frames is None:
                self._album_renders.pop(device_id, None)
                return
            per = self._album_renders.get(device_id)
            if per is None:
                return
            for fid in [f for f in per if f not in keep_frames]:
                per.pop(fid, None)
            if not per:
                self._album_renders.pop(device_id, None)

    # -- post-action frame patches (hybrid render mode, schema 2) --------

    def frame_patches_for(self, device_id: str, frame_digest: str) -> dict[str, Any] | None:
        """The pending patch document for a device, but only when it is
        anchored to exactly ``frame_digest`` (the frame the client says
        it is showing). Anything else returns None: a patch applied to
        any other frame would paint the wrong pixels."""
        with self._lock:
            doc = self._patch_docs.get(device_id)
            if doc is None or not frame_digest or doc.get("frame_digest") != frame_digest:
                return None
            return {k: v for k, v in doc.items() if k != "blob_digest"}

    def _drop_patches_locked(self, device_id: str, *, keep_digest: str | None = None) -> None:
        """Discard a device's patch document (and its blob file) unless
        it is anchored to ``keep_digest``. Called under ``_lock`` at
        every point the live frame changes: a patch must never survive
        the frame it was diffed against (recency-guard discipline)."""
        doc = self._patch_docs.get(device_id)
        if doc is None or (keep_digest is not None and doc.get("frame_digest") == keep_digest):
            return
        self._patch_docs.pop(device_id, None)
        blob = str(doc.get("blob_digest") or "")
        if blob:
            with contextlib.suppress(OSError):
                (self._renders_dir / f"overlay-patch-{blob}.bin").unlink()

    def shadow_render_page(self, page_id: str, device_id: str) -> dict[str, Any] | None:
        """Render ``page_id`` for one device WITHOUT touching the live
        slot: the artifact + sidecars land on disk and the render info is
        returned, but the device keeps serving its current frame. The
        patch reconcile diffs this against the served frame. Serialises
        with real pushes under the push lock, same as a deck warm."""
        stamp: dict[str, dict[str, Any]] = {}
        with self._lock:
            try:
                self._push_page_locked(
                    page_id,
                    device_ids={device_id},
                    source="reconcile",
                    force_publish=True,
                    stamp_into=stamp,
                )
            except Exception:
                logger.exception("shadow render failed page=%s device=%s", page_id, device_id)
                return None
        return stamp.get(device_id)

    def _promote_shadow(self, device_id: str, anchor_digest: str, info: dict[str, Any]) -> str:
        """Swap a shadow render into the live slot so the device's next
        poll downloads the full frame (the patch path's fallback).
        Recency-guarded: if the live slot moved past ``anchor_digest``
        while we rendered, a newer push already repainted the panel and
        promoting would silently revert it."""
        with self._lock:
            cur = self._latest_renders.get(device_id)
            if cur is not None and cur.get("digest") != anchor_digest:
                return "superseded"
            self._replace_latest_render_locked(device_id, info)
            self._drop_patches_locked(device_id, keep_digest=str(info.get("digest") or ""))
        return "promoted"

    def _build_patch_payload(
        self,
        anchor: dict[str, Any],
        info: dict[str, Any],
        panel: dict[str, Any],
    ) -> tuple[bytes, list[dict[str, int]]] | str:
        """The patch blob + rect entries turning the ``anchor`` frame
        into the ``info`` frame, or a fallback-reason string when
        patching isn't the right delivery (caller then ships a full
        frame).

        The diff runs in COMPOSITION space (the pre-dither PNGs) and the
        rects map through the same transform chain as tap targets. That
        matters because the .bin family's default dither is
        error-diffusion: a one-tile change perturbs the dithered bytes
        of everything after it in scan order, so a wire-space byte diff
        of two honest renders is nearly global and would always blow the
        budget. The composition diff stays tight; the blob content still
        comes from the new dithered artifact, so a painted rect is
        byte-exact with a full download of the new frame (the dither
        seam at rect edges is sub-noise on the gray panels)."""
        from app import frame_patch
        from app.overlay_sync import _panel_geometry, rect_to_wire

        try:
            native_w = int(panel.get("native_w") or panel.get("w") or 0)
            native_h = int(panel.get("native_h") or panel.get("h") or 0)
            old = (self._renders_dir / str(anchor.get("filename") or "")).read_bytes()
            new = (self._renders_dir / str(info.get("filename") or "")).read_bytes()
        except (OSError, TypeError, ValueError):
            return "artifact_unreadable"
        bpp = frame_patch.infer_bpp(len(new), native_w, native_h)
        if bpp is None or len(old) != len(new):
            return "not_a_packed_framebuffer"

        rects: list[tuple[int, int, int, int]] | None = None
        comp_old = str(anchor.get("composition_digest") or "")
        comp_new = str(info.get("composition_digest") or "")
        if comp_old and comp_new and comp_old == comp_new:
            # Identical composition but a different artifact means the
            # renderer settings changed (gamut, dither, contrast); the
            # whole frame legitimately repaints.
            return "render_settings_changed"
        if comp_old and comp_new:
            geo = _panel_geometry(panel)
            try:
                png_old = (self._renders_dir / f"{comp_old}.png").read_bytes()
                png_new = (self._renders_dir / f"{comp_new}.png").read_bytes()
            except OSError:
                png_old = png_new = b""
            if geo is not None and png_old and png_new:
                comp_rects = frame_patch.diff_composition_rects(
                    png_old,
                    png_new,
                    expected_w=geo["comp_w"],
                    expected_h=geo["comp_h"],
                    tolerance=frame_patch.COMP_DIFF_TOLERANCE,
                )
                if comp_rects == []:
                    # Different composition digests but no visible change
                    # (sub-tolerance anti-aliasing jitter): there is
                    # nothing worth painting at all. The caller holds the
                    # digest and stages nothing.
                    return "no_visual_change"
                if comp_rects:
                    aligned: list[tuple[int, int, int, int]] = []
                    for r in comp_rects:
                        wire = rect_to_wire(
                            (float(r[0]), float(r[1]), float(r[2]), float(r[3])),
                            comp_w=geo["comp_w"],
                            comp_h=geo["comp_h"],
                            native_w=geo["native_w"],
                            native_h=geo["native_h"],
                            flip=geo["flip"],
                            underscan=geo["underscan"],
                        )
                        if wire is None:
                            continue
                        snapped = frame_patch.align_rect(
                            wire, width=native_w, height=native_h, bpp=bpp
                        )
                        if snapped is not None:
                            aligned.append(snapped)
                    rects = aligned or None
        if rects is None:
            # No composition PNGs (image push, pruned thumbnail): the raw
            # byte diff still works for local changes; error-diffusion
            # content lands in over_budget and ships as a full frame.
            rects = frame_patch.diff_rects(old, new, width=native_w, height=native_h)
            if not rects:
                return "wire_diff_unavailable"
        built = frame_patch.build_patch_blob(new, rects, width=native_w, height=native_h)
        if built is None:
            return "blob_build_failed"
        if len(built[0]) > frame_patch.MAX_PATCH_BYTES:
            return f"over_budget:{len(built[0])}"
        return built

    def _stage_patches_locked(
        self,
        device_id: str,
        anchor_digest: str,
        blob: bytes,
        entries: list[dict[str, int]],
    ) -> bool:
        """Install a patch document for a device (caller holds
        ``_lock``). False when the live frame moved past the anchor (the
        newer frame already covers the change) or the blob can't be
        written; the previous document and blob are replaced atomically
        from the client's point of view (old blob 404s afterwards)."""
        cur = self._latest_renders.get(device_id)
        if cur is None or cur.get("digest") != anchor_digest:
            return False
        blob_digest = hashlib.sha256(blob).hexdigest()[:16]
        try:
            (self._renders_dir / f"overlay-patch-{blob_digest}.bin").write_bytes(blob)
        except OSError:
            logger.exception("frame patch: blob write failed for device=%s", device_id)
            return False
        self._drop_patches_locked(device_id)
        self._patch_seq = max(self._patch_seq + 1, int(time.time() * 1000))
        self._patch_docs[device_id] = {
            "schema": 2,
            "frame_digest": anchor_digest,
            "seq": self._patch_seq,
            "format": "fb-rect",
            "url": f"/api/v1/device/{device_id}/frame/patch/{blob_digest}",
            "bytes": len(blob),
            "rects": entries,
            "blob_digest": blob_digest,
        }
        logger.info(
            "frame patch: %d rect(s), %d bytes staged for device=%s (anchor=%s)",
            len(entries),
            len(blob),
            device_id,
            anchor_digest,
        )
        return True

    def _divert_to_patches_locked(
        self, renderer: Renderer, render_info: dict[str, Any], panel: Panel
    ) -> bool:
        """Deliver a freshly-rendered frame as patches on the CURRENT
        digest instead of stamping it live, when the target is a
        patch-capable REST device showing the same page and the visual
        diff is small (the header-clock case). Holds the digest stable,
        which both avoids a full e-ink repaint for chrome-only changes
        and stops digest churn from invalidating in-flight taps. Caller
        holds ``_lock``. False = stamp normally."""
        device_id = renderer.device
        prev = self._latest_renders.get(device_id)
        if prev is None or prev.get("digest") == render_info.get("digest"):
            return False
        prev_page = str(prev.get("page_id") or "")
        if not prev_page or prev_page != str(render_info.get("page_id") or ""):
            # Different (or unknown) page: the on-glass touch regions
            # would no longer match what the patches paint.
            return False
        if not self._renderer_is_http_polled(renderer):
            return False
        status = (self._device_status_fn() or {}).get(device_id)
        cap = status.get("overlay") if isinstance(status, dict) else None
        schema = cap.get("schema") if isinstance(cap, dict) else None
        proto = status.get("proto") if isinstance(status, dict) else None
        proto_v = proto.get("v") if isinstance(proto, dict) else None
        schema_ok = isinstance(schema, int) and not isinstance(schema, bool) and schema >= 2
        proto_ok = isinstance(proto_v, int) and not isinstance(proto_v, bool) and proto_v >= 2
        if not (schema_ok or proto_ok):
            return False
        payload = self._build_patch_payload(prev, render_info, panel.model_dump())
        if payload == "no_visual_change":
            # Sub-tolerance jitter only: hold the digest, stage nothing.
            prev["timestamp"] = time.time()
            self._save_latest_renders()
            return True
        if isinstance(payload, str):
            # A failed divert costs the panel a full-frame download and a
            # full e-ink flash, so the reason deserves a warning.
            logger.warning("push not diverted to patches for device=%s (%s)", device_id, payload)
            return False
        blob, entries = payload
        if not self._stage_patches_locked(device_id, str(prev.get("digest") or ""), blob, entries):
            return False
        # Freshness bump only; digest / filename / signature stay the
        # anchor's, so the device keeps 304ing and the next render
        # re-diffs against the same base.
        prev["timestamp"] = time.time()
        self._save_latest_renders()
        return True

    def reconcile_via_patches(self, device_id: str, page_id: str, *, panel: dict[str, Any]) -> str:
        """Post-action reconcile for a patch-capable device: re-render
        the page headless, diff against the frame the device is showing,
        and stage the changed rects as a patch document the device picks
        up on its next ``/frame/data`` poll (or ``/status`` beat). The
        device's frame digest never changes, so its ETag polling keeps
        304ing and no full download or full e-ink flash happens.

        Returns ``patched`` (document staged), ``no_change`` (re-render
        produced the identical artifact), ``superseded`` (a newer frame
        landed while rendering; it already covers this), ``promoted``
        (patching wasn't the right delivery, the shadow render was
        promoted so the next poll full-paints), or ``failed``."""
        anchor = self.latest_render_for(device_id)
        anchor_digest = str(anchor.get("digest") or "") if anchor else ""
        if not anchor_digest or anchor is None:
            return "failed"
        info = self.shadow_render_page(page_id, device_id)
        if info is None:
            return "failed"
        if str(info.get("digest") or "") == anchor_digest:
            return "no_change"
        payload = self._build_patch_payload(anchor, info, panel)
        if payload == "no_visual_change":
            return "no_change"
        if isinstance(payload, str):
            logger.info(
                "frame patch: full frame instead of patches for device=%s (%s)",
                device_id,
                payload,
            )
            return self._promote_shadow(device_id, anchor_digest, info)
        blob, entries = payload
        with self._lock:
            if not self._stage_patches_locked(device_id, anchor_digest, blob, entries):
                return "superseded"
        return "patched"

    # -- speculative pre-compose (issue #49 linger) ----------------------

    def _compose_url_for(self, page_id: str, panel: Panel, target_id: str) -> str:
        """The compose URL a push would capture for this page / panel /
        per-device target. Shared with ``prewarm_page`` so a prewarmed
        capture and the push that consumes it agree on the key."""
        base_url = self._base_url_fn().rstrip("/")
        base = f"{base_url}/compose/{page_id}?for_push=1&w={panel.w}&h={panel.h}"
        return to_loopback_url(f"{base}&device_id={target_id}" if target_id else base)

    @staticmethod
    def _precompose_key(compose_url: str, page: Any, panel: Panel) -> str:
        """Cache key: the exact capture input (URL encodes page, dims,
        per-device target) plus the page's content token, so an edit
        between prewarm and consume misses instead of serving the
        pre-edit composition."""
        from app.composer import page_preview_token

        return f"{compose_url}|{page_preview_token(page, (panel.w, panel.h))}"

    def _take_precomposed(
        self, key: str
    ) -> tuple[bytes, list[dict[str, Any]], list[dict[str, Any]]] | None:
        """Pop a fresh prewarmed composition, or None. Consume-once: a
        hit removes the entry so one speculative capture can never serve
        two pushes (widget data would drift further each time)."""
        with self._precompose_lock:
            entry = self._precompose.pop(key, None)
        if entry is None:
            return None
        stamped, png, regions, slots = entry
        if time.monotonic() - stamped > _PRECOMPOSE_TTL_S:
            return None
        return png, regions, slots

    def _store_precomposed(
        self,
        key: str,
        png: bytes,
        regions: list[dict[str, Any]],
        slots: list[dict[str, Any]],
    ) -> None:
        with self._precompose_lock:
            now = time.monotonic()
            # Drop expired entries opportunistically, then bound the size.
            for k in [
                k for k, entry in self._precompose.items() if now - entry[0] > _PRECOMPOSE_TTL_S
            ]:
                del self._precompose[k]
            self._precompose[key] = (now, png, regions, slots)
            self._precompose.move_to_end(key)
            while len(self._precompose) > _PRECOMPOSE_CAP:
                self._precompose.popitem(last=False)

    def prewarm_page(self, page_id: str, *, device_id: str) -> bool:
        """Speculatively capture the composition a push of ``page_id`` to
        ``device_id`` would render, so that push skips its Playwright
        capture (the dominant share of post-touch latency during a linger
        session). Best-effort: any failure is logged and swallowed, a
        missed prewarm only costs the latency it would have saved. Runs
        without the push lock; the browser pool serialises captures on
        its own worker, so a prewarm queues behind (never races) a real
        push's capture. Returns True when a composition was captured or
        was already cached fresh."""
        try:
            page = self._page_store.get(page_id)
            if page is None:
                return False
            groups = panel_groups_for_push(page, self._devices, self._settings)
            match = next(
                ((panel, dids) for panel, dids in groups if device_id in dids),
                None,
            )
            if match is None:
                return False
            panel, _dids = match
            target_id = (
                device_id if _page_needs_per_device_render(page, self._plugin_registry_fn()) else ""
            )
            compose_url = self._compose_url_for(page_id, panel, target_id)
            key = self._precompose_key(compose_url, page, panel)
            with self._precompose_lock:
                cached = self._precompose.get(key)
                if cached is not None and time.monotonic() - cached[0] <= _PRECOMPOSE_TTL_S:
                    return True
            composition_png, raw_result = capture_composed(
                CaptureRequest(
                    render=RenderRequest(
                        url=compose_url,
                        viewport_w=panel.w,
                        viewport_h=panel.h,
                        timezone_id=self._render_timezone_id(),
                        headers_by_origin=headers_by_origin_for_page(page),
                    ),
                    script=EXTRACT_INTERACTIVE_JS,
                ),
                pool=self._browser_pool_fn(),
            )
            raw_regions, raw_slots = split_capture_result(raw_result)
            self._store_precomposed(
                key,
                composition_png,
                normalize_regions(raw_regions),
                normalize_slots(raw_slots),
            )
            logger.info("prewarm: cached composition for page=%s device=%s", page_id, device_id)
            return True
        except Exception:
            logger.exception("prewarm failed for page=%s device=%s", page_id, device_id)
            return False

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
        force_publish: bool = False,
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
                    force_publish=force_publish,
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
        rotate: str | None = None,
        bypass_coalesce: bool = True,
        force_publish: bool = True,
        source: str = "file",
        framing: dict[str, float] | None = None,
    ) -> PushResult:
        """Hand arbitrary image bytes to every renderer.

        Used by the Send-page File / Image-URL / Gallery flows. Each
        renderer's ``transform()`` fits the input to its panel dims (the
        bundled .bin renderers via ``fit_to_panel``).

        ``device_id`` (optional): when set, only that device's renderers
        fire and its panel dims are used, same routing as a page bound
        to the device. ``fit`` (optional): the fit mode for non-panel-sized
        input (``fit``/``fill``/``stretch``/``center``/``blur``). ``source``
        tags the History row: the Companion link-send routes fetch / render
        once and fan the resulting bytes out through here with ``"url"`` /
        ``"webpage"`` so History keeps the real origin (default ``"file"``).

        ``rotate`` (optional): ``auto`` / ``90`` / ``180`` / ``270``, a
        clockwise turn applied after EXIF normalization and before the fit,
        for when the image's orientation and the panel's disagree. ``auto``
        turns a quarter only when their aspects are opposite. Omitted (the
        default) the image keeps the orientation it was shot in.

        ``framing`` (optional, Companion 0.6 ``image_framing``): validated
        ``{focus_x, focus_y, zoom}`` intent, resolved against this push's
        panel dims into a :class:`~app.quantizer.SourceCrop` and applied
        ahead of the fan-out, so the retained composition is the framed,
        panel-sized frame and resend stays pixel-exact. The intent itself
        rides the History row's ``extra`` for reproduce / re-target."""
        supersede = self._acquire_or_supersede(
            device_id=device_id,
            source=source,
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
                    image_bytes,
                    source_label,
                    source=source,
                    device_id=device_id,
                    fit=fit,
                    rotate=rotate,
                    force_publish=force_publish,
                    framing=framing,
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
        rotate: str | None = None,
        bypass_coalesce: bool = True,
        allow_local: bool = True,
    ) -> PushResult:
        """Download an image URL, then ``push_image``. Networking errors
        surface as failed events with the URL as the target.

        ``allow_local`` defaults to operator trust (loopback / LAN allowed);
        the Companion route passes ``False`` for the strict public-only
        policy, refusing private / loopback / link-local / reserved hosts,
        including on redirect hops."""
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
                    image_bytes = self._fetch_remote_image(url, allow_local=allow_local)
                except Exception as err:
                    result = self._log_failure(source="url", target=url, error=f"fetch: {err}")
                else:
                    result = self._push_bytes_locked(
                        image_bytes,
                        url,
                        source="url",
                        device_id=device_id,
                        fit=fit,
                        rotate=rotate,
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
        allow_local: bool = True,
        headers: Mapping[str, str] | None = None,
    ) -> PushResult:
        """Screenshot an arbitrary URL with Playwright, then publish.

        ``allow_local`` defaults to operator trust (loopback / LAN capture
        allowed). The Companion route passes ``False``: the pre-flight refuses
        non-public hosts, and the strict flag rides into the renderer so a
        request interceptor also blocks every redirect hop / subresource
        Chromium follows internally (a pre-check alone can't see those).

        ``headers`` (#234) are validated operator-supplied request headers,
        scoped to ``url``'s origin by the renderer so nothing off-origin sees
        them. Only the count and names reach the event log; the values are
        never recorded, which is why the failure paths below format the URL but
        never the header map."""
        # SSRF pre-flight. Playwright follows redirects internally, so this is
        # only the initial-URL check; the renderer's interceptor (via
        # RenderRequest.allow_local) enforces the policy on each hop.
        try:
            assert_operator_url(url) if allow_local else assert_public_url(url)
        except BlockedURLError as err:
            return self._log_failure(source="webpage", target=url, error=str(err))
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
                extra_headers, header_user_agent = split_user_agent(headers or {})
                target_origin = origin_of(url)
                if extra_headers or header_user_agent:
                    logger.info(
                        "webpage push %s: applying %s%s",
                        url,
                        header_summary(extra_headers) or "no extra headers",
                        ", user-agent override" if header_user_agent else "",
                    )
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
                            # Strict callers (Companion) refuse non-public hosts
                            # on every hop the browser follows internally.
                            allow_local=allow_local,
                            headers_by_origin=(
                                {target_origin: extra_headers}
                                if (extra_headers and target_origin)
                                else None
                            ),
                            user_agent=header_user_agent,
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

    def fetch_remote_image_strict(self, url: str) -> bytes:
        """Fetch an image URL under the strict public-only policy, once.

        The Companion ``/image-urls`` route fetches here a single time, then
        fans the bytes out per target through :meth:`push_image`, so a
        redirect-to-private is refused (fetch_bytes re-validates each hop) and
        the source is fetched once rather than per display."""
        return self._fetch_remote_image(url, allow_local=False)

    def render_webpage_png(
        self,
        url: str,
        *,
        viewport_w: int,
        viewport_h: int = 1200,
        allow_local: bool = True,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        """Render a webpage to a composition PNG once (raises on failure).

        The Companion ``/webpages`` route renders here a single time at the
        logical viewport, then fans the bytes out per target through
        :meth:`push_image` (no per-target re-render). ``allow_local=False``
        installs the renderer's request interceptor so every hop Chromium
        follows is held to the strict public-only policy.

        ``headers`` (#234) are operator-supplied request headers, already
        parsed and validated by :mod:`app.http_headers`. They are scoped to
        ``url``'s own origin, so a redirect off-origin or a third-party
        subresource never receives them; ``User-Agent`` is lifted out and
        applied to the browser context instead, so page JavaScript reading
        ``navigator.userAgent`` agrees with the wire. Never log or persist the
        values; :func:`app.http_headers.header_summary` is the log-safe form.
        """
        extra, user_agent = split_user_agent(headers or {})
        origin = origin_of(url)
        return render_to_png(
            RenderRequest(
                url=url,
                viewport_w=viewport_w,
                viewport_h=viewport_h,
                timezone_id=self._render_timezone_id(),
                is_composer=False,
                allow_local=allow_local,
                headers_by_origin={origin: extra} if (extra and origin) else None,
                user_agent=user_agent,
            ),
            pool=self._browser_pool_fn(),
        )

    def republish(
        self,
        event_id: int,
        *,
        device_ids: set[str] | None = None,
    ) -> PushResult:
        """Re-publish a past push from its stored composition PNG. No
        re-render, no re-download. Records a new event row tagged
        ``source="resend"`` so history keeps the link to the original.

        ``device_ids`` optionally narrows the original target snapshot. The
        Companion resend route uses that to respect per-device quiet hours;
        callers that omit it retain the History page's existing behaviour.
        """
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
                # Replay the original push's delivery targets. The push
                # row snapshots ``device_ids`` (renderer.device of every
                # renderer that fired: instance ids for clones, topic
                # prefixes for base kinds), so passing them back as
                # ``device_filters`` re-fires exactly the same renderers.
                # Without this a resend fans out unbound, which skips
                # per-device clone renderers (#83) entirely, so the
                # device's latest-render entry never updates and its
                # /frame poll keeps 304-ing on the OLD frame (#119).
                extra = record.extra if isinstance(record.extra, dict) else {}
                original_device_ids = [
                    d for d in (extra.get("device_ids") or []) if isinstance(d, str) and d
                ]
                if device_ids is not None:
                    selected_ids = [d for d in original_device_ids if d in device_ids]
                    if not selected_ids:
                        result = PushResult(
                            status="failed",
                            page_id=record.target,
                            composition_digest=record.digest,
                            error="no original targets remain eligible for resend",
                        )
                        self._notify(result)
                        return result
                else:
                    selected_ids = original_device_ids
                fit_candidate = extra.get("fit", extra.get("image_fit"))
                original_fit = (
                    fit_candidate
                    if isinstance(fit_candidate, str) and fit_candidate in _IMAGE_FIT_MODES
                    else None
                )
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
                    # Devices in one push row share a panel group, so the
                    # first id resolves the group's dims (base-kind topic
                    # prefixes miss the device lookup and fall back to the
                    # virtual panel, same as the original unbound push).
                    result = self._fan_out(
                        comp_path.read_bytes(),
                        self._panel_dims_for_send(selected_ids[0] if selected_ids else None),
                        source="resend",
                        target=record.target,
                        started=time.monotonic(),
                        device_filters=set(selected_ids) if selected_ids else None,
                        image_fit=original_fit,
                        force_publish=True,
                        force_client_refetch=True,
                    )
                finally:
                    self._lock.release()
        self._notify(result)
        return result

    def _live_digests(self) -> set[str]:
        """Digests that must survive artifact GC for every live frame.

        This includes the device artifact, source composition, and logical
        Companion preview. Deleting them while a frame is live breaks device
        fetches, phone thumbnails, or touch-region resolution.
        """
        live: set[str] = set()
        # Shallow copy: callers may or may not hold self._lock.
        entries = list(self._latest_renders.values())
        # Grace-window entries stay lookupable for ~60 s after being
        # superseded; their artifacts + sidecars must survive a prune
        # that races the window.
        entries.extend(list(self._previous_renders.values()))
        # Warmed deck frames are live too: they're ready to promote onto a
        # panel, so their artifact + regions must survive the prune even though
        # no device is serving them yet and their event row may have been capped.
        for per_page in list(self._deck_renders.values()):
            entries.extend(per_page.values())
        # Warmed album frames (#177) are protected the same way: they back the
        # /collection manifest and firmware fetches them by digest.
        for per_frame in list(self._album_renders.values()):
            entries.extend(per_frame.values())
        for entry in entries:
            for key in (
                "digest",
                "composition_digest",
                "preview_digest",
                "last_served_preview_digest",
            ):
                value = entry.get(key)
                if isinstance(value, str) and value:
                    live.add(value)
        return live

    def delete_history(self, event_id: int) -> bool:
        """Delete a history row and, if no other rows still reference the
        composition PNG, drop the PNG too. Returns True if the row was
        deleted. Per-renderer artifacts are LRU-evicted separately. The
        artifacts of any device's current frame are kept regardless (see
        ``_live_digests``)."""
        record = self._event_log.get(event_id)
        if record is None:
            return False
        deleted = self._event_log.delete(event_id)
        if (
            deleted
            and record.digest
            and not self._event_log.digest_in_use(record.digest)
            and record.digest not in self._live_digests()
        ):
            for suffix in (".png", ".bin", ".regions.json"):
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
        time; returns the number of files removed.

        The latest render for every device is always kept, whether or not
        an event row still references it. Event rows are capped (and the
        History page's Clear deletes them outright), so a long-lived frame
        could otherwise lose its artifact, thumbnail, and touch-region
        sidecar while still on the panel; with the sidecar gone every tap
        resolved ``no_target`` and the touch monitor had no regions to
        overlay."""
        keep = self._event_log.referenced_digests() | self._live_digests()
        try:
            entries = [p for p in self._renders_dir.iterdir() if p.is_file()]
        except OSError:
            return 0
        removed = 0
        for path in entries:
            # Patch blobs are referenced by an in-memory document, never
            # an event row; they're deleted on supersede and swept at
            # startup, so the prune must not race a pending fetch.
            if path.name.startswith("overlay-patch-"):
                continue
            # First-dot split, not ``.stem``: the touch-region sidecar is
            # ``<digest>.regions.json`` (double suffix), and its lifecycle
            # must follow its composition digest.
            digest = path.name.split(".", 1)[0]
            if path.name.startswith("thumb_") or digest not in keep:
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
        force_publish: bool = False,
        stamp_into: dict[str, dict[str, Any]] | None = None,
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
        # Binding is the delivery model (#84). A dashboard is delivered
        # only to devices it's explicitly bound to. The legacy path where
        # an unbound Send fans out to every base renderer over the retained
        # MQTT topics (a single-head leftover) is gated behind an explicit
        # opt-in. By default, drop empty-dids virtual groups: bound groups
        # still deliver, a wholly-unbound Send no-ops with a clear message.
        if not _unbound_broadcast_enabled(self._settings):
            bound_groups = [(panel, dids) for panel, dids in groups if dids]
            if len(bound_groups) != len(groups):
                if not bound_groups:
                    return self._log_unbound_skip(page_id, source=source)
                groups = bound_groups
        if respect_quiet_hours:
            groups = self._filter_quiet_devices(groups)
            if not groups:
                # Every bound device is currently quiet. Log a soft skip
                # and return, not a failure (the user intent is
                # respected) but not a successful push either.
                return self._log_quiet_skip(page_id, source=source)

        all_renderers: list[RendererResult] = []
        group_results: list[PushResult] = []
        # v0.71.x: pages carrying a widget that declares
        # ``render.per_device_id: true`` (e.g. the tesserae_status bar
        # with its per-device battery + wifi chips) must render once per
        # bound device so each device's frame reflects its own status,
        # not a min-across-all-devices aggregate. Everything else keeps
        # the panel-group fan-out (one render → many devices).
        per_device_render = _page_needs_per_device_render(page, self._plugin_registry_fn())
        # Per-cell dither map (issue #86): collect the cells that opted out
        # of dithering (Advanced pane -> Flat colour) once for the whole
        # page. Geometry is composition-space (cell pixels), so it's shared
        # across panel groups; the .bin renderers rasterise + transform it to
        # match each panel. Skip entirely unless some cell opts out, so every
        # other page packs byte-identically to before.
        candidate = regions_from_page(page)
        dither_regions = candidate if has_nearest_region(candidate) else None
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
                compose_url = self._compose_url_for(page_id, panel, target_id)
                # Prewarmed composition (issue #49 linger): a touch session
                # speculatively captured this page already; consume it and
                # skip the Playwright capture entirely.
                cached = self._take_precomposed(self._precompose_key(compose_url, page, panel))
                if cached is not None:
                    composition_png, touch_regions, overlay_slots = cached
                else:
                    try:
                        # Screenshot + touch-region and overlay-slot
                        # extraction from the same page session (issue #49):
                        # both maps must be measured against the exact DOM
                        # the frame captured.
                        composition_png, raw_result = capture_composed(
                            CaptureRequest(
                                render=RenderRequest(
                                    url=compose_url,
                                    viewport_w=panel.w,
                                    viewport_h=panel.h,
                                    timezone_id=self._render_timezone_id(),
                                    # Webpage cells behind header auth (#234).
                                    # Resolved server-side so the credential
                                    # never enters the composed DOM.
                                    headers_by_origin=headers_by_origin_for_page(page),
                                ),
                                script=EXTRACT_INTERACTIVE_JS,
                            ),
                            pool=self._browser_pool_fn(),
                        )
                        raw_regions, raw_slots = split_capture_result(raw_result)
                        touch_regions = normalize_regions(raw_regions)
                        overlay_slots = normalize_slots(raw_slots)
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
                    force_publish=force_publish,
                    dither_regions=dither_regions,
                    touch_regions=touch_regions,
                    overlay_slots=overlay_slots,
                    stamp_into=stamp_into,
                    page_id=page_id,
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
        event_ids = tuple(
            dict.fromkeys(
                event_id
                for result in group_results
                for event_id in (
                    result.event_ids or ((result.event_id,) if result.event_id is not None else ())
                )
                if event_id > 0
            )
        )
        return PushResult(
            status=status,
            page_id=page_id,
            composition_digest=digest,
            duration_s=time.monotonic() - started,
            renderers=all_renderers,
            # Only a genuine failure carries the error. ``status`` here is one
            # of sent / no_change / failed per the aggregation above, and
            # no_change is a success: every panel already shows this frame, so
            # there was nothing to publish. Attaching the render/publish error
            # to it made callers that branch on the error string report an
            # unchanged panel as a failed one.
            error=("one or more panels failed to render/publish" if status == "failed" else None),
            event_id=event_ids[0] if len(event_ids) == 1 else None,
            event_ids=event_ids,
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
        rotate: str | None = None,
        force_publish: bool = False,
        force_client_refetch: bool = False,
        framing: dict[str, float] | None = None,
    ) -> PushResult:
        """Shared tail end of push_image / push_webpage / republish."""
        started = started if started is not None else time.monotonic()
        panel_dims = self._panel_dims_for_send(device_id)
        # Orientation is settled once, here, so every renderer downstream
        # sees upright pixels: the .bin renderers EXIF-transpose inside
        # their own fit, but pi_png fits client-side and never does, which
        # landed phone photos on their side (discussion #231). No-op for a
        # composition PNG with no orientation tag and no requested turn.
        image_bytes = orient_for_panel(
            image_bytes,
            rotate=rotate,
            target_w=int(panel_dims.get("w") or 0),
            target_h=int(panel_dims.get("h") or 0),
        )
        if framing is not None:
            image_bytes = _apply_framing(image_bytes, panel_dims, framing)
        return self._fan_out(
            image_bytes,
            panel_dims,
            source=source,
            target=source_label,
            started=started,
            device_filters={device_id} if device_id else None,
            image_fit=fit,
            force_publish=force_publish,
            force_client_refetch=force_client_refetch,
            framing=framing,
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
        force_client_refetch: bool = False,
        framing: dict[str, float] | None = None,
        dither_regions: list[dict[str, Any]] | None = None,
        touch_regions: list[dict[str, Any]] | None = None,
        overlay_slots: list[dict[str, Any]] | None = None,
        stamp_into: dict[str, dict[str, Any]] | None = None,
        page_id: str | None = None,
    ) -> PushResult:
        """Common fanout: thumbnail + per-renderer transform / publish / log.

        ``stamp_into`` (Decks silent warm, optional): when set, the per-device
        render info is written into this map instead of the live
        ``_latest_renders``, so a warm render produces the artifact + regions on
        disk without changing what any device is currently serving. Callers pass
        ``force_publish=True`` alongside it so the content-skip path (which reads
        and bumps ``_latest_renders``) is bypassed.

        ``device_filters`` (multi-head): when set, only renderers whose
        ``.device`` is in the set fire, so a frame rendered for one
        panel lands only on the devices that share that panel. ``None``
        fans out to every renderer (legacy / virtual-panel). ``image_fit``
        (optional): fit mode for non-panel-sized input, passed through to
        each renderer's transform; ``None`` keeps each renderer's default.

        ``dither_regions`` (issue #86, optional): per-cell ``render.dither``
        map for this composition, injected into each renderer's settings as
        ``_dither_regions``. .bin renderers rasterise it into a nearest-
        colour mask; others ignore the key. It rides the resolved settings,
        so it also folds into the render signature, a widget's dither hint
        changing repaints even when the composition pixels don't move."""
        comp_digest = hashlib.sha256(composition_png).hexdigest()[:16]
        thumb_path = self._renders_dir / f"{comp_digest}.png"
        if not thumb_path.exists():
            thumb_path.write_bytes(composition_png)
        else:
            thumb_path.touch()
        # Touch-region sidecar (issue #49). Rewritten on every render, not
        # write-once like the PNG: the digest is content-addressed on
        # pixels, and an annotation edit can leave pixels (and digest)
        # unchanged, so the latest render's actions must win. ``None``
        # (image / webpage pushes, no DOM) leaves any existing map alone.
        #
        # EXCEPTION: an EMPTY extraction never overwrites a non-empty
        # sidecar for the same composition. Identical pixels = identical
        # DOM, so regions cannot legitimately vanish while the digest
        # stays put — an empty result against a populated sidecar is the
        # code-element mirror race (the sandbox hadn't posted its regions
        # by capture time), and overwriting served 0-region manifests
        # that killed touch until the next redraw (bench, 2026-07-25).
        if touch_regions is not None:
            if (
                not touch_regions
                and not overlay_slots
                and load_regions(self._renders_dir, comp_digest)
            ):
                logger.warning(
                    "render extracted no interactive regions for comp=%s but its "
                    "sidecar is populated; keeping the existing sidecar "
                    "(extraction race)",
                    comp_digest,
                )
            else:
                save_regions(self._renders_dir, comp_digest, touch_regions, slots=overlay_slots)

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
            if device_filters is not None:
                if renderer.device not in device_filters:
                    continue
            elif "__" in renderer.id:
                # Unbound / virtual-panel push (no targeted device). Skip
                # per-device clone renderers (id ``<base>__<instance>``):
                # each clone belongs to a bound device that has its own
                # bound dashboard. Firing a clone here would stamp THIS
                # (unrelated) dashboard's frame into that device's
                # ``_latest_renders`` entry, so the device's next
                # /api/v1/device/<id>/frame poll would paint the wrong
                # dashboard (#83). Only base renderers fan out on an
                # unbound push (legacy single-head / retained MQTT topic).
                continue
            renderer_start = time.monotonic()
            # Resolve settings up front so the skip signature sees the same
            # inputs the render would (gamut, calibration, saturation, …).
            # Reused by _publish_artifact below, so no double resolution.
            render_settings = self._resolve_render_settings(renderer, image_fit=image_fit)
            # Per-cell dither map (issue #86) only reaches the server-side
            # quantise packers (the .bin family). PNG renderers (pi_png,
            # trmnl_*) quantise on the client, so the key is meaningless to
            # them and is left off to keep their render signature stable.
            if dither_regions is not None and renderer.extension == "bin":
                render_settings = {**render_settings, "_dither_regions": dither_regions}
            signature = self._render_signature(
                comp_digest=comp_digest, panel=panel, settings=render_settings
            )
            last = self._latest_renders.get(renderer.device)
            if not force_publish and last and last.get("render_signature") == signature:
                # Upgrade/backfill path for servers whose latest-render record
                # predates logical device previews, or whose preview PNG was
                # pruned independently.  Do not repaint the e-ink panel merely
                # to restore a phone thumbnail.
                retained_preview = retained_device_preview(
                    self._renders_dir, last.get("preview_digest")
                )
                if retained_preview is None:
                    preview_source = self._overlay_low_battery_if_needed(
                        composition_png, renderer.device
                    )
                    preview_digest = self._write_device_preview_best_effort(
                        renderer,
                        preview_source,
                        panel,
                        settings=render_settings,
                    )
                    if preview_digest is not None:
                        last["preview_digest"] = preview_digest
                result = RendererResult(
                    renderer_id=renderer.id,
                    topic=renderer.topic,
                    digest=str(last.get("digest") or ""),
                    url="",
                    bytes_written=0,
                    preview_digest=(
                        str(last.get("preview_digest"))
                        if isinstance(last.get("preview_digest"), str)
                        else None
                    ),
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
                    renderer, composition_png, panel, image_fit=image_fit, settings=render_settings
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
                render_info = {
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
                    "preview_digest": result.preview_digest,
                    # Full render-input fingerprint (composition + panel +
                    # settings). The next push skips re-rendering only when
                    # this matches, so a gamut / calibration change repaints
                    # even against an unchanged composition (issue #81).
                    "render_signature": signature,
                    # Resend intent (#119): a user clicking "resend" in the
                    # History page wants the panel to re-paint even when the
                    # frame is byte-identical to what it already shows. MQTT
                    # honours this via force_publish; for REST clients the
                    # content-addressed ETag would otherwise 304, so we flag
                    # the entry and /frame serves one 200 before clearing it.
                    "force_refetch": force_client_refetch,
                    # The page this frame renders, when it came from a page
                    # push. The post-action reconcile uses it to re-render
                    # "whatever the device is showing" for devices bound to
                    # no rotation and no deck (a directly-pushed page).
                    "page_id": page_id,
                }
                if stamp_into is not None:
                    # Silent warm: record the frame for the caller (Decks) without
                    # touching the live map, so the device keeps serving its
                    # current frame until navigation promotes this one.
                    stamp_into[renderer.device] = render_info
                elif (
                    not force_publish
                    and not force_client_refetch
                    and self._divert_to_patches_locked(renderer, render_info, panel)
                ):
                    # Patch-capable REST device, same page, small visual
                    # diff (a header clock tick): the change shipped as
                    # partial-refresh patches on the CURRENT digest, so
                    # the live entry deliberately does not move. Explicit
                    # repaint intents (force_publish / resend refetch)
                    # skip the divert above and stamp normally.
                    logger.info(
                        "push diverted to patches for device=%s (digest held at %s)",
                        renderer.device,
                        self._latest_renders.get(renderer.device, {}).get("digest"),
                    )
                else:
                    self._replace_latest_render_locked(renderer.device, render_info)
                    # The live frame changed; a pending patch document
                    # anchored to the previous frame must not survive it.
                    self._drop_patches_locked(renderer.device, keep_digest=result.digest)
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
        event_extra: dict[str, Any] = {
            "renderers": [asdict(r) for r in results],
            "device_ids": targeted_ids,
        }
        if image_fit in _IMAGE_FIT_MODES:
            event_extra["fit"] = image_fit
        if framing is not None:
            # Original Companion framing intent (never the resolved rect):
            # History returns it for reproduce / re-target; the retained
            # composition is already framed, so resend needs nothing more.
            event_extra["framing"] = dict(framing)
        # A successful warm (deck / album background refresh) rendered into
        # the side cache and never repainted a panel, so it must not be
        # logged with the same status as a push a display actually showed.
        # Only the recorded row changes: callers still see ``sent``.
        recorded_status: str = status
        if status == "sent" and source in ("deck_warm", "album_warm"):
            recorded_status = "warmed"
        event_id = self._event_log.record(
            type="push",
            source=source,
            target=target,
            status=recorded_status,
            digest=comp_digest,
            error=error,
            duration_s=duration,
            extra=event_extra,
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
            event_ids=(event_id,),
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

    def _resolve_render_settings(
        self, renderer: Renderer, *, image_fit: str | None = None
    ) -> dict[str, Any]:
        """Resolve the effective settings for one renderer push: base
        runtime settings, the per-push fit override, and any Calibration-
        tab palette / tone / dither / edge profile applied to the device.

        Pulled out of :meth:`_publish_artifact` so the content-skip in
        :meth:`_fan_out` can fold the same inputs into its render
        signature without rendering. A gamut / calibration / saturation
        change alters the packed bytes even when the composition PNG is
        byte-identical, so the skip has to see them too (issue #81).

        Calibration-tab palette override: when the device has a profile
        applied, the resolved RGB tuples land under ``_palette_override``.
        .bin renderers read it and pass it to
        :func:`app.quantizer.pack_to_panel_bin` as ``palette_override``,
        which wins over the module-level ``_CALIBRATED_PALETTES`` lookup
        when the clone's ``calibrated`` toggle is also on.

        Phase 2 (v0.67.1) adds ``_profile_tone`` (exposure, s_curve) and
        ``_profile_dither`` (serpentine, diffusion_strength) side channels
        for the renderer to pick up. Contrast + saturation stay on the
        per-clone renderer settings so existing configs keep working; the
        profile's contrast/saturation get merged only when actively
        different from their defaults.
        """
        settings = self._settings.get_for_runtime(
            "renderers", renderer.id, renderer.manifest.get("settings", [])
        )
        if image_fit:
            # Per-push fit override for non-panel-sized input. .bin renderers
            # read ``image_fit`` (server-side fit_to_panel); pi_png passes it
            # to the client via its ``scale`` payload field.
            settings = {**settings, "image_fit": image_fit, "scale": image_fit}
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
                    "protect_native_colours": profile.edges.protect_native_colours,
                }
                # Grayscale calibration. Carried as the profile's raw
                # anchors rather than a resolved ramp: only the renderer
                # knows how many levels its gamut has (4 for
                # esp32_gray2_bin, 16 for esp32_gray_bin), and one set of
                # measured patches should drive either. Absent on colour
                # profiles, which is every profile until someone
                # calibrates a grey panel.
                if profile.gray.levels:
                    extras["_gray_ramp"] = tuple(profile.gray.levels)
                settings = {**settings, **extras}
        return settings

    def _render_signature(self, *, comp_digest: str, panel: Panel, settings: dict[str, Any]) -> str:
        """Fingerprint every input that changes a renderer's packed bytes
        for a device: the composition, the panel geometry + gamut, and the
        resolved renderer settings (saturation / contrast / dither /
        calibration palette). :meth:`_fan_out`'s content-skip compares this
        instead of the bare composition digest, so a gamut or calibration
        change repaints even when the dashboard pixels didn't move. Before
        this the skip keyed on the composition alone, so switching a
        pi_bin device's panel from Spectra 6 to ACeP left it serving the
        old palette's ``.bin`` (a stale 304) until the composition itself
        changed (issue #81)."""
        payload = {
            "comp": comp_digest,
            "panel": panel.model_dump(),
            "settings": settings,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def _publish_artifact(
        self,
        renderer: Renderer,
        composition_png: bytes,
        panel: Panel,
        *,
        image_fit: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> RendererResult:
        """Run one renderer end-to-end: settings -> transform -> write ->
        publish. ``settings`` may be pre-resolved by the caller (the skip
        check in :meth:`_fan_out` already resolves them to build the render
        signature); when ``None`` we resolve them here."""
        if settings is None:
            settings = self._resolve_render_settings(renderer, image_fit=image_fit)
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
        # Keep a separate logical-screen PNG for Companion display cards.
        # This is deliberately best-effort: a disk/PNG failure must never
        # turn a successful physical-device publish into a failed send.
        preview_digest = self._write_device_preview_best_effort(
            renderer,
            composition_png,
            panel,
            settings=settings,
        )
        return RendererResult(
            renderer_id=renderer.id,
            topic=renderer.topic,
            digest=digest,
            url=url,
            bytes_written=len(artifact),
            preview_digest=preview_digest,
        )

    def _write_device_preview_best_effort(
        self,
        renderer: Renderer,
        composition_png: bytes,
        panel: Panel,
        *,
        settings: dict[str, Any],
    ) -> str | None:
        """Retain a logical device preview without affecting delivery."""

        try:
            return write_device_preview(
                self._renders_dir,
                composition_png,
                panel=panel,
                settings=settings,
            )
        except Exception:
            logger.exception(
                "logical device preview failed for renderer=%s device=%s",
                renderer.id,
                renderer.device,
            )
            return None

    def _renderer_is_http_polled(self, renderer: Renderer) -> bool:
        """Return True when the renderer's bound device fetches frames
        over HTTP rather than subscribing to a broker. Two paths:

        * ``device.status_topic is None`` — the legacy signal for
          TRMNL / KOReader / anything that hits ``/api/display``.
          Kinds opt in by declaring no MQTT topics at all.
        * ``device.transport in ("rest", "relay")`` — per-instance
          out-of-band transports. ``rest`` devices poll ``/api/display``;
          ``relay`` devices have their sealed frame uploaded to a cloud
          mailbox they poll (app/relay_publisher.py). Neither wants a
          broker publish. Same kind can mix MQTT / REST / relay instances;
          the transport field on each instance decides.

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
        return device.transport in ("rest", "relay")

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

    def _fetch_remote_image(self, url: str, *, allow_local: bool = True) -> bytes:
        """Download an image URL through the SSRF guard, bounded size + timeout.

        Routes through ``net_guard`` so the scheme allowlist, per-redirect-hop
        host check, and size cap match the widget fetch path. ``allow_local``
        keeps same-host / LAN image sources usable while still refusing
        link-local / cloud metadata (operator flows); untrusted callers (the
        Companion route) pass ``allow_local=False`` so loopback / RFC1918 /
        reserved hosts are refused, including on redirect hops (fetch_bytes
        re-validates each). ``BlockedURLError`` (a ``ValueError``) and the
        oversize ``ValueError`` propagate to the caller, which logs them as a
        failed ``url`` push."""
        try:
            data, _ = fetch_bytes(
                url,
                headers={"User-Agent": "tesserae/0.1"},
                timeout=_HTTP_TIMEOUT_S,
                max_bytes=_MAX_REMOTE_IMAGE_BYTES,
                allow_local=allow_local,
            )
        except urllib.error.URLError as err:
            raise RuntimeError(f"download failed: {err}") from err
        return data

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

    def device_in_quiet_hours(self, device_id: str) -> bool:
        """True when the device is currently inside its effective
        quiet-hours window. Public so timer-driven callers that bypass
        ``push()`` (the deck home-return's promote fast path) can apply
        the same gate a respectful push would."""
        from datetime import datetime
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        device = self._devices.devices.get(device_id) if self._devices is not None else None
        if device is None:
            return False
        app_settings = self._settings.get_section("app")
        tz: Any | None = None
        tz_raw = str(app_settings.get("timezone") or "system").strip()
        if tz_raw and tz_raw.lower() != "system":
            try:
                tz = ZoneInfo(tz_raw)
            except ZoneInfoNotFoundError:
                tz = None
        now = datetime.now(tz) if tz else datetime.now()
        return device_is_quiet(app_settings, device, now, tz)

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

    def _log_unbound_skip(self, page_id: str, *, source: str = "page") -> PushResult:
        """Record a soft skip when a Send targets a dashboard bound to no
        device and the legacy unbound-broadcast opt-in is off. Not a
        failure: binding is the delivery model, so the fix is to bind the
        dashboard to a device. Surfaced in Events so 'why didn't it send?'
        is a one-look answer."""
        msg = "dashboard isn't bound to any device"
        event_id = self._event_log.record(
            type="push",
            source=source,
            target=page_id,
            status="unbound",
            error=msg,
        )
        return PushResult(
            status="unbound",
            page_id=page_id,
            error=msg,
            event_id=event_id,
        )
