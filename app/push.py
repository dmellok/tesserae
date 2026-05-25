"""Push pipeline: render the page, hand the composition PNG to every loaded
renderer, write each artifact to disk, publish the renderer's payload.

The contract is intentionally small — about twenty lines of work split
across ``push()`` and ``_publish_artifact()``. The complexity that used to
live in ``inky-dash/app/push.py`` (per-listener branches, payload-shape
hardcoding, publish-precedence rules) is gone: each renderer owns its own
wire format and the loop just iterates.

Concurrency: one push at a time. A second concurrent attempt returns
``status="busy"`` rather than queueing — keeps the model simple and the UI
honest about what's actually happening.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.renderer import RenderRequest, render_to_png, to_loopback_url
from app.renderer_loader import Renderer, RendererRegistry
from app.state.page_store import PageStore
from app.transport import MqttTransport

logger = logging.getLogger(__name__)

PushStatus = Literal["sent", "busy", "failed", "not_found"]


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
    duration_s: float = 0.0
    error: str | None = None
    renderers: list[RendererResult] = field(default_factory=list)


class PushManager:
    """Single-flight render → transform → publish loop.

    Constructor wiring:
      * ``registry`` — the RendererRegistry. Empty registry is allowed; in
        that case ``push()`` renders the PNG (and caches it) but publishes
        nothing.
      * ``page_store`` — for resolving the saved Page by id.
      * ``transport`` — connected MqttTransport. ``push()`` raises clearly
        if it isn't connected and there are renderers to publish.
      * ``renders_dir`` — where to write per-renderer artifacts. Created if
        it doesn't exist.
      * ``base_url`` — the URL prefix the panel listener uses to fetch
        artifacts. The renderer's ``payload()`` builds the final URL from
        this + digest + extension.
    """

    def __init__(
        self,
        *,
        registry: RendererRegistry,
        page_store: PageStore,
        transport: MqttTransport,
        renders_dir: Path,
        base_url: str,
    ) -> None:
        self._registry = registry
        self._page_store = page_store
        self._transport = transport
        self._renders_dir = renders_dir
        self._renders_dir.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url.rstrip("/")
        self._lock = threading.Lock()

    # -- public API --------------------------------------------------------

    def push(self, page_id: str) -> PushResult:
        if not self._lock.acquire(blocking=False):
            return PushResult(status="busy", page_id=page_id, error="another push in flight")
        try:
            return self._push_locked(page_id)
        finally:
            self._lock.release()

    # -- internals ---------------------------------------------------------

    def _push_locked(self, page_id: str) -> PushResult:
        started = time.monotonic()
        page = self._page_store.get(page_id)
        if page is None:
            return PushResult(status="not_found", page_id=page_id, error="page not found")

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
            return PushResult(
                status="failed",
                page_id=page_id,
                duration_s=time.monotonic() - started,
                error=f"render: {err}",
            )

        results: list[RendererResult] = []
        for renderer in self._registry.all():
            try:
                results.append(
                    self._publish_artifact(renderer, composition_png, page.panel.model_dump())
                )
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
        status: PushStatus = (
            "sent" if results and all(r.error is None for r in results) else "failed"
        )
        return PushResult(
            status=status,
            page_id=page_id,
            duration_s=duration,
            renderers=results,
            error=None if status == "sent" else "one or more renderers failed",
        )

    def _publish_artifact(
        self, renderer: Renderer, composition_png: bytes, panel_dict: dict[str, int]
    ) -> RendererResult:
        """Run one renderer end-to-end: transform → write → publish."""
        from app.state.page_store import Panel

        panel = Panel(**panel_dict)
        # Renderer settings will be wired via SettingsStore in M3; for now
        # use the manifest defaults.
        settings = renderer.settings_defaults()
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
