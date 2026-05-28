"""Push pipeline: render → hand the composition PNG to every renderer →
write each artifact → publish.

Four entry points share the same single-flight + log-the-event tail:

* ``push(page_id)`` — render a saved Page through the composer.
* ``push_image(image_bytes, source_label)`` — hand arbitrary bytes
  directly to the renderers (Send-page file upload + image URL).
* ``push_webpage(url)`` — screenshot an arbitrary URL via Playwright, then
  hand the bytes to the renderers (Send-page webpage tab).
* ``republish(event_id)`` — re-publish a past push from history using the
  stored composition PNG, no re-render.

Concurrency: one push at a time. Concurrent attempts return
``status="busy"`` instead of queueing — simple model, the UI shows what's
actually happening.

The composition PNG is always written to ``data/core/renders/<comp_digest>.png``
as the canonical thumbnail. Per-renderer artifacts go to
``<digest>.<extension>`` next to it (content-addressed, so two renderers
producing identical output dedupe on disk).

Every push attempt — success, failure, or busy — is logged to
``EventLog`` so the Send-page history tab can list / resend / delete.

mypy --strict applies to this module — see pyproject.toml.
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

from app.device_loader import DeviceRegistry
from app.panel import (
    is_flipped_orientation,
    panel_groups_for_push,
    resolve_settings_panel,
)
from app.renderer import RenderRequest, render_to_png, to_loopback_url
from app.renderer_loader import Renderer, RendererRegistry
from app.state.event_log import EventLog
from app.state.page_store import PageStore, Panel
from app.state.settings_store import SettingsStore
from app.transport import MqttTransport


def _disabled_renderer_ids(settings: SettingsStore) -> set[str]:
    """Renderer ids whose per-install ``Enabled`` flag is off.

    Stored in the ``renderers_enabled`` settings section as a flat
    ``{renderer_id: bool}`` dict; missing entries default to enabled."""
    raw = settings.get_section("renderers_enabled")
    return {rid for rid, enabled in raw.items() if enabled is False}


logger = logging.getLogger(__name__)

PushStatus = Literal["sent", "busy", "failed", "not_found"]

# Max bytes we'll pull from a remote image URL (Send-page Image URL tab).
# Larger downloads are rejected before going through Pillow.
_MAX_REMOTE_IMAGE_BYTES: int = 16 * 1024 * 1024
_HTTP_TIMEOUT_S: float = 10.0


@dataclass(frozen=True)
class RendererResult:
    renderer_id: str
    topic: str
    digest: str
    url: str
    bytes_written: int
    error: str | None = None


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
      * ``registry`` — RendererRegistry. Empty registry is allowed; in that
        case ``push()`` renders the PNG (and logs the event) but publishes
        nothing.
      * ``page_store`` — for resolving a saved Page by id.
      * ``transport`` — connected MqttTransport. ``push()`` raises clearly
        if it's not connected and there are renderers to publish.
      * ``settings`` — SettingsStore. Used to pick up per-renderer
        settings + the default panel dims (for non-page pushes).
      * ``event_log`` — EventLog. Every push attempt writes a row.
      * ``renders_dir`` — where to write composition PNGs + per-renderer
        artifacts. Created if missing.
      * ``base_url_fn`` — callable returning the current URL prefix the
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
    ) -> None:
        self._registry = registry
        self._page_store = page_store
        self._transport = transport
        self._settings = settings
        self._event_log = event_log
        self._renders_dir = renders_dir
        self._renders_dir.mkdir(parents=True, exist_ok=True)
        self._base_url_fn = base_url_fn
        # Optional — enables multi-head routing. When a page sets a
        # device_id, the panel comes from that device's manifest and
        # only its renderers fire in _fan_out.
        self._devices = devices
        self._lock = threading.Lock()
        # Listeners fire synchronously after every push attempt (success
        # or failure). HA discovery uses this to follow pushes. Slow
        # listeners block the request — keep them fast. Exceptions are
        # logged and swallowed so a buggy subscriber can't break a push.
        self._listener_lock = threading.Lock()
        self._listeners: list[Callable[[PushResult], None]] = []

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

    # -- public API ------------------------------------------------------

    def push(self, page_id: str) -> PushResult:
        """Render a saved Page through the composer and publish."""
        if not self._lock.acquire(blocking=False):
            result = self._log_busy(source="page", target=page_id)
        else:
            try:
                result = self._push_page_locked(page_id)
            finally:
                self._lock.release()
        self._notify(result)
        return result

    def push_image(
        self, image_bytes: bytes, *, source_label: str, device_id: str | None = None
    ) -> PushResult:
        """Hand arbitrary image bytes to every renderer.

        Used by the Send-page File and Image-URL tabs. Each renderer's
        ``transform()`` is responsible for fitting the input to its panel
        dims (the bundled renderers do this via ``fit_to_panel``).

        ``device_id`` (optional): when set, only that device's renderers
        fire and its panel dims are used — same routing as a page bound
        to the device."""
        if not self._lock.acquire(blocking=False):
            result = self._log_busy(source="file", target=source_label)
        else:
            try:
                result = self._push_bytes_locked(
                    image_bytes, source_label, source="file", device_id=device_id
                )
            finally:
                self._lock.release()
        self._notify(result)
        return result

    def push_url_image(self, url: str, *, device_id: str | None = None) -> PushResult:
        """Download an image URL, then ``push_image``. Networking errors
        surface as failed events with the URL as the target."""
        if not self._lock.acquire(blocking=False):
            result = self._log_busy(source="url", target=url)
        else:
            try:
                try:
                    image_bytes = self._fetch_remote_image(url)
                except Exception as err:
                    result = self._log_failure(source="url", target=url, error=f"fetch: {err}")
                else:
                    result = self._push_bytes_locked(
                        image_bytes, url, source="url", device_id=device_id
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
    ) -> PushResult:
        """Screenshot an arbitrary URL with Playwright, then publish."""
        if not self._lock.acquire(blocking=False):
            result = self._log_busy(source="webpage", target=url)
        else:
            try:
                started = time.monotonic()
                try:
                    composition = render_to_png(
                        RenderRequest(url=url, viewport_w=viewport_w, viewport_h=viewport_h)
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
                        composition, url, source="webpage", started=started, device_id=device_id
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
            elif not self._lock.acquire(blocking=False):
                result = self._log_busy(source="resend", target=record.target)
            else:
                try:
                    result = self._push_bytes_locked(
                        comp_path.read_bytes(),
                        record.target,
                        source="resend",
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

    # -- internals -------------------------------------------------------

    def _push_page_locked(self, page_id: str) -> PushResult:
        started = time.monotonic()
        page = self._page_store.get(page_id)
        if page is None:
            return self._log_failure(
                source="page", target=page_id, status="not_found", error="page not found"
            )
        # Multi-head: the page may target several devices with different
        # panels. Render once per distinct panel (a 4:3 and a portrait
        # panel need different compositions) and fan each frame out only
        # to the devices that share that panel. An empty device list means
        # "no specific device" — render at the virtual panel and fan out
        # to every renderer (legacy single-head).
        groups = panel_groups_for_push(page, self._devices, self._settings)
        base_url = self._base_url_fn().rstrip("/")

        all_renderers: list[RendererResult] = []
        group_results: list[PushResult] = []
        for panel, device_ids in groups:
            compose_url = to_loopback_url(
                f"{base_url}/compose/{page_id}?for_push=1&w={panel.w}&h={panel.h}"
            )
            try:
                composition_png = render_to_png(
                    RenderRequest(url=compose_url, viewport_w=panel.w, viewport_h=panel.h)
                )
            except Exception as err:
                group_results.append(
                    self._log_failure(
                        source="page",
                        target=page_id,
                        error=f"render: {err}",
                        duration_s=time.monotonic() - started,
                    )
                )
                continue
            result = self._fan_out(
                composition_png,
                panel.model_dump(),
                source="page",
                target=page_id,
                started=started,
                device_filters=set(device_ids) if device_ids else None,
            )
            all_renderers.extend(result.renderers)
            group_results.append(result)

        # Aggregate the per-panel pushes into one result for the caller.
        # Each group already logged its own push + renderer events.
        failed = [r for r in group_results if r.status not in ("sent",)]
        status: PushStatus = "sent" if group_results and not failed else "failed"
        if not group_results:
            status = "failed"
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
    ) -> PushResult:
        """Common fanout: thumbnail + per-renderer transform / publish / log.

        ``device_filters`` (multi-head): when set, only renderers whose
        ``.device`` is in the set fire — so a frame rendered for one
        panel lands only on the devices that share that panel. ``None``
        fans out to every renderer (legacy / virtual-panel)."""
        comp_digest = hashlib.sha256(composition_png).hexdigest()[:16]
        thumb_path = self._renders_dir / f"{comp_digest}.png"
        if not thumb_path.exists():
            thumb_path.write_bytes(composition_png)
        else:
            thumb_path.touch()

        panel = Panel(**panel_dims)
        results: list[RendererResult] = []
        disabled = _disabled_renderer_ids(self._settings)
        for renderer in self._registry.all():
            if renderer.id in disabled:
                continue
            if device_filters is not None and renderer.device not in device_filters:
                continue
            renderer_start = time.monotonic()
            try:
                result = self._publish_artifact(renderer, composition_png, panel)
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
        elif all(r.error is None for r in results):
            status = "sent"
            error = None
        else:
            status = "failed"
            error = "one or more renderers failed"

        event_id = self._event_log.record(
            type="push",
            source=source,
            target=target,
            status=status,
            digest=comp_digest,
            error=error,
            duration_s=duration,
            extra={"renderers": [asdict(r) for r in results]},
        )

        return PushResult(
            status=status,
            page_id=target,
            composition_digest=comp_digest,
            duration_s=duration,
            error=error,
            renderers=results,
            event_id=event_id,
        )

    def _publish_artifact(
        self, renderer: Renderer, composition_png: bytes, panel: Panel
    ) -> RendererResult:
        """Run one renderer end-to-end: settings -> transform -> write -> publish."""
        settings = self._settings.get_for_runtime(
            "renderers", renderer.id, renderer.manifest.get("settings", [])
        )
        artifact = renderer.transform(composition_png, panel=panel, settings=settings)
        digest = hashlib.sha256(artifact).hexdigest()[:16]

        path = self._renders_dir / f"{digest}.{renderer.extension}"
        if not path.exists():
            path.write_bytes(artifact)
        else:
            path.touch()

        payload = renderer.payload(digest, self._base_url_fn().rstrip("/"), settings=settings)
        url = str(payload.get("url", ""))
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

    def _panel_dims_for_send(self, device_id: str | None = None) -> dict[str, Any]:
        """Pick panel dims for a Send-page push.

        With a ``device_id`` that names a loaded device declaring a panel,
        use that device's dims + rotation (so a manual send to a specific
        display matches its panel). Otherwise fall back to the virtual
        panel (``resolve_settings_panel``) so the preset / custom dims /
        portrait orientation are honoured identically to every other
        code path."""
        if device_id and self._devices is not None:
            device = self._devices.devices.get(device_id)
            if device is not None and device.panel is not None:
                block = device.panel
                return {
                    "w": int(block["w"]),
                    "h": int(block["h"]),
                    "flip": is_flipped_orientation(block.get("orientation")),
                }
        panel = resolve_settings_panel(self._settings)
        return {"w": panel.w, "h": panel.h}

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
