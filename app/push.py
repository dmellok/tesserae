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

import hashlib
import io
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image

from app.renderer import RenderRequest, render_to_png, to_loopback_url
from app.renderer_loader import Renderer, RendererRegistry
from app.state.event_log import EventLog
from app.state.page_store import PageStore, Panel
from app.state.settings_store import SettingsStore
from app.transport import MqttTransport

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
      * ``base_url`` — the URL prefix the panel listener uses to fetch
        artifacts. Each renderer's ``payload()`` builds the final URL
        from this + digest + extension.
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
        base_url: str,
    ) -> None:
        self._registry = registry
        self._page_store = page_store
        self._transport = transport
        self._settings = settings
        self._event_log = event_log
        self._renders_dir = renders_dir
        self._renders_dir.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url.rstrip("/")
        self._lock = threading.Lock()

    # -- public API ------------------------------------------------------

    def push(self, page_id: str) -> PushResult:
        """Render a saved Page through the composer and publish."""
        if not self._lock.acquire(blocking=False):
            return self._log_busy(source="page", target=page_id)
        try:
            return self._push_page_locked(page_id)
        finally:
            self._lock.release()

    def push_image(self, image_bytes: bytes, *, source_label: str) -> PushResult:
        """Hand arbitrary image bytes to every renderer.

        Used by the Send-page File and Image-URL tabs. Each renderer's
        ``transform()`` is responsible for fitting the input to its panel
        dims (the bundled renderers do this via ``fit_to_panel``)."""
        if not self._lock.acquire(blocking=False):
            return self._log_busy(source="file", target=source_label)
        try:
            return self._push_bytes_locked(image_bytes, source_label, source="file")
        finally:
            self._lock.release()

    def push_url_image(self, url: str) -> PushResult:
        """Download an image URL, then ``push_image``. Networking errors
        surface as failed events with the URL as the target."""
        if not self._lock.acquire(blocking=False):
            return self._log_busy(source="url", target=url)
        try:
            try:
                image_bytes = self._fetch_remote_image(url)
            except Exception as err:
                return self._log_failure(source="url", target=url, error=f"fetch: {err}")
            return self._push_bytes_locked(image_bytes, url, source="url")
        finally:
            self._lock.release()

    def push_webpage(
        self, url: str, *, viewport_w: int = 1600, viewport_h: int = 1200
    ) -> PushResult:
        """Screenshot an arbitrary URL with Playwright, then publish."""
        if not self._lock.acquire(blocking=False):
            return self._log_busy(source="webpage", target=url)
        try:
            started = time.monotonic()
            try:
                composition = render_to_png(
                    RenderRequest(url=url, viewport_w=viewport_w, viewport_h=viewport_h)
                )
            except Exception as err:
                return self._log_failure(
                    source="webpage",
                    target=url,
                    error=f"render: {err}",
                    duration_s=time.monotonic() - started,
                )
            return self._push_bytes_locked(composition, url, source="webpage", started=started)
        finally:
            self._lock.release()

    def republish(self, event_id: int) -> PushResult:
        """Re-publish a past push from its stored composition PNG. No
        re-render, no re-download. Records a new event row tagged
        ``source="resend"`` so history keeps the link to the original."""
        record = self._event_log.get(event_id)
        if record is None:
            return PushResult(status="not_found", page_id="", error="history record not found")
        if not record.digest:
            return PushResult(
                status="failed", page_id=record.target, error="record has no composition digest"
            )
        comp_path = self._renders_dir / f"{record.digest}.png"
        if not comp_path.exists():
            return PushResult(
                status="failed",
                page_id=record.target,
                composition_digest=record.digest,
                error="composition PNG evicted from disk",
            )
        if not self._lock.acquire(blocking=False):
            return self._log_busy(source="resend", target=record.target)
        try:
            return self._push_bytes_locked(
                comp_path.read_bytes(),
                record.target,
                source="resend",
            )
        finally:
            self._lock.release()

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

        compose_url = to_loopback_url(f"{self._base_url}/compose/{page_id}?for_push=1")
        try:
            composition_png = render_to_png(
                RenderRequest(
                    url=compose_url,
                    viewport_w=page.panel.w,
                    viewport_h=page.panel.h,
                )
            )
        except Exception as err:
            return self._log_failure(
                source="page",
                target=page_id,
                error=f"render: {err}",
                duration_s=time.monotonic() - started,
            )

        return self._fan_out(
            composition_png,
            page.panel.model_dump(),
            source="page",
            target=page_id,
            started=started,
        )

    def _push_bytes_locked(
        self,
        image_bytes: bytes,
        source_label: str,
        *,
        source: str,
        started: float | None = None,
    ) -> PushResult:
        """Shared tail end of push_image / push_webpage / republish."""
        started = started if started is not None else time.monotonic()
        panel_dims = self._panel_dims_for_send(image_bytes)
        return self._fan_out(
            image_bytes,
            panel_dims,
            source=source,
            target=source_label,
            started=started,
        )

    def _fan_out(
        self,
        composition_png: bytes,
        panel_dims: dict[str, int],
        *,
        source: str,
        target: str,
        started: float,
    ) -> PushResult:
        """Common fanout: thumbnail + per-renderer transform / publish / log."""
        comp_digest = hashlib.sha256(composition_png).hexdigest()[:16]
        thumb_path = self._renders_dir / f"{comp_digest}.png"
        if not thumb_path.exists():
            thumb_path.write_bytes(composition_png)
        else:
            thumb_path.touch()

        panel = Panel(**panel_dims)
        results: list[RendererResult] = []
        for renderer in self._registry.all():
            try:
                results.append(self._publish_artifact(renderer, composition_png, panel))
            except Exception as err:
                logger.exception("renderer %s failed", renderer.id)
                results.append(
                    RendererResult(
                        renderer_id=renderer.id,
                        topic=renderer.topic,
                        digest="",
                        url="",
                        bytes_written=0,
                        error=f"{type(err).__name__}: {err}",
                    )
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

        payload = renderer.payload(digest, self._base_url, settings=settings)
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

    def _panel_dims_for_send(self, image_bytes: bytes) -> dict[str, int]:
        """Pick panel dims for a Send-page push.

        Order of preference: explicit app.panel_w / app.panel_h settings,
        then the image's own dimensions (the source is canonical when no
        panel is configured)."""
        app = self._settings.get_section("app")
        w_raw = app.get("panel_w")
        h_raw = app.get("panel_h")
        if (
            isinstance(w_raw, int | float)
            and isinstance(h_raw, int | float)
            and int(w_raw) > 0
            and int(h_raw) > 0
        ):
            return {"w": int(w_raw), "h": int(h_raw)}
        img = Image.open(io.BytesIO(image_bytes))
        return {"w": img.size[0], "h": img.size[1]}

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
