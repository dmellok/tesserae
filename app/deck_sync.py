"""Device-facing deck cache sync: manifest building + version digests.

The on-device deck cache (SD card) lets capable firmware navigate a deck
locally: paint the target frame from the card instead of waking WiFi.
The server side of that contract is a per-device manifest describing the
bound deck (pages, frame digests, byte sizes, TTLs, and the link graph)
plus a version digest the ``/status`` response repeats so the firmware
knows when to re-sync. This module builds both; the REST endpoints live
in :mod:`app.rest_api`, and the wire contract is documented in
``docs/dev/client-protocol.md``.

Version semantics: the version is the digest of the manifest content
(graph + frame digests + TTLs), so ANY user edit, background refresh, or
re-render bumps it. It is per-device (frame digests are per-device
renders). A page that hasn't been warmed yet contributes an empty
digest, so the version naturally changes as warming completes and the
device converges over its next sync cycles.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore

logger = logging.getLogger(__name__)

# Fallback TTL when neither the page nor the deck has a periodic refresh
# (refresh_interval_minutes == 0 means "warmed once, no cadence"): the
# cached frame stays navigable for a day before the firmware treats it
# as stale and falls back to a network fetch.
_NO_REFRESH_TTL_S = 86400


class _DeckRenderSource(Protocol):
    """The slice of PushManager the manifest builder needs."""

    def deck_render_for(self, device_id: str, page_id: str) -> dict[str, Any] | None: ...

    def warm_deck_page(self, page_id: str, device_id: str) -> bool: ...


def advertised_deck_cache(payload: bytes | str | dict[str, Any]) -> dict[str, int] | None:
    """The deck-cache capability a device advertises in its register /
    heartbeat body (``{"deck_cache": {"schema": 1, "capacity_bytes": N}}``),
    validated, or None.

    Unlike the OTA schema this is CURRENT-STATE, never carried forward:
    the firmware only advertises while an SD card is present and
    mounted, and the server must stop offering deck syncs the moment
    the capability disappears from a heartbeat."""
    if isinstance(payload, (bytes, bytearray, str)):
        try:
            body = json.loads(payload)
        except (ValueError, TypeError):
            return None
    else:
        body = payload
    if not isinstance(body, dict):
        return None
    cap = body.get("deck_cache")
    if not isinstance(cap, dict):
        return None
    schema = cap.get("schema")
    capacity = cap.get("capacity_bytes")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        return None
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 0:
        return None
    return {"schema": schema, "capacity_bytes": capacity}


def bound_deck_for(store: DeckStore, device_id: str) -> Deck | None:
    """The enabled deck bound to a device (first when several), or None.
    Same rule as ``ButtonService._bound_deck`` so the manifest a device
    syncs and the deck the server navigates for it can never diverge."""
    decks = store.for_device(device_id)
    return decks[0] if decks else None


def page_ttl_s(deck: Deck, page: DeckPage) -> int:
    """How long a cached frame of this page may be served for a local
    nav, in seconds: the page's effective refresh cadence, or a one-day
    fallback for pages with no periodic refresh at all."""
    minutes = page.effective_refresh_minutes(deck.refresh_interval_minutes)
    return minutes * 60 if minutes > 0 else _NO_REFRESH_TTL_S


def _links_view(page: DeckPage) -> list[dict[str, Any]]:
    return [
        {
            "button": link.button,
            "zone": link.zone.model_dump() if link.zone is not None else None,
            "target_page_id": link.target_page_id,
        }
        for link in page.links
    ]


def default_neighbours(deck: Deck, page_id: str) -> tuple[str, str] | None:
    """``(prev, next)`` page ids in deck order, wrapping, or None for a
    single-page deck / unknown page. The default navigation for pages
    whose graph doesn't say otherwise."""
    ids = [p.page_id for p in deck.pages]
    if len(ids) < 2 or page_id not in ids:
        return None
    i = ids.index(page_id)
    return ids[(i - 1) % len(ids)], ids[(i + 1) % len(ids)]


def manifest_links(
    deck: Deck,
    page: DeckPage,
    *,
    touch: bool,
    page_has_touch_regions: bool,
) -> list[dict[str, Any]]:
    """The page's link table for the sync manifest: explicit graph links
    plus synthesized defaults, so a deck authored without a graph (the
    common management-page flow) still navigates locally on-device.

    Defaults, only where the graph is silent:

    * ``left`` / ``right`` button links to the previous / next page in
      deck order, wrapping -- mirroring the rotation button convention.
    * On touch panels, left-half / right-half tap zones to prev / next,
      but ONLY when the page declares no explicit zones AND its
      composition has no markup touch regions: a default zone would
      otherwise swallow taps meant for the page's own tap targets
      (device-local zone hits never reach the server's region map)."""
    links = _links_view(page)
    neighbours = default_neighbours(deck, page.page_id)
    if neighbours is None:
        return links
    prev_id, next_id = neighbours
    explicit_buttons = {link.button for link in page.links if link.button is not None}
    if "left" not in explicit_buttons:
        links.append({"button": "left", "zone": None, "target_page_id": prev_id})
    if "right" not in explicit_buttons:
        links.append({"button": "right", "zone": None, "target_page_id": next_id})
    # Swipe triggers (additive manifest field, firmware v1.9+): explicit
    # direction-named button links mirror as swipe entries (the graph
    # stores authored swipes as direction-named buttons), and where the
    # author was silent the defaults follow paging convention: swiping
    # LEFT pulls the NEXT page in, swiping RIGHT goes back.
    directions = {"left", "right", "up", "down"}
    for link in page.links:
        if link.button in directions:
            links.append(
                {"swipe": link.button, "zone": None, "target_page_id": link.target_page_id}
            )
    mirrored = {entry.get("swipe") for entry in links if entry.get("swipe")}
    if "left" not in mirrored:
        links.append({"swipe": "left", "zone": None, "target_page_id": next_id})
    if "right" not in mirrored:
        links.append({"swipe": "right", "zone": None, "target_page_id": prev_id})
    has_explicit_zones = any(link.zone is not None for link in page.links)
    if touch and not has_explicit_zones and not page_has_touch_regions:
        links.append(
            {
                "button": None,
                "zone": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0},
                "target_page_id": prev_id,
            }
        )
        links.append(
            {
                "button": None,
                "zone": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0},
                "target_page_id": next_id,
            }
        )
    return links


def _artifact_size(renders_dir: Path, filename: str) -> int:
    try:
        return (renders_dir / filename).stat().st_size
    except OSError:
        return 0


def version_digest(manifest: dict[str, Any]) -> str:
    """Digest of the manifest content, excluding the version field
    itself. Same truncation convention as frame digests (sha256[:16])."""
    body = {k: v for k, v in manifest.items() if k != "version"}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_manifest(
    deck: Deck,
    device_id: str,
    *,
    push_mgr: _DeckRenderSource,
    renders_dir: Path,
    warm_missing: bool,
    touch: bool = False,
    regions_lookup: Any | None = None,
    capacity_bytes: int | None = None,
) -> dict[str, Any]:
    """The deck sync manifest for one device, contract shape:

    ``{deck_id, version, entry_page_id, pages: [{page_id, digest, bytes,
    ttl_s, links: [{button, zone, target_page_id}]}]}``

    With ``warm_missing`` (the manifest endpoint), pages without a
    warmed render are rendered now so the manifest ships complete and
    its version is stable until the next refresh; this can take a few
    seconds per cold page. Without it (the ``/status`` version check),
    the manifest reflects only what's warmed, which is cheap and
    converges to the same version once warming completes."""
    pages: list[dict[str, Any]] = []
    for page in deck.pages:
        info = push_mgr.deck_render_for(device_id, page.page_id)
        if info is None and warm_missing:
            if not push_mgr.warm_deck_page(page.page_id, device_id):
                logger.warning(
                    "deck manifest: warm failed deck=%s page=%s device=%s",
                    deck.id,
                    page.page_id,
                    device_id,
                )
            info = push_mgr.deck_render_for(device_id, page.page_id)
        digest = str(info.get("digest") or "") if info else ""
        filename = str(info.get("filename") or "") if info else ""
        comp_digest = str(info.get("composition_digest") or "") if info else ""
        has_regions = False
        if callable(regions_lookup) and comp_digest:
            try:
                has_regions = bool(regions_lookup(comp_digest))
            except Exception:
                has_regions = False
        pages.append(
            {
                "page_id": page.page_id,
                "digest": digest,
                "bytes": _artifact_size(renders_dir, filename) if filename else 0,
                "ttl_s": page_ttl_s(deck, page),
                "links": manifest_links(
                    deck, page, touch=touch, page_has_touch_regions=has_regions
                ),
            }
        )
    # Capacity guard: when the device advertised how much room its card
    # has, mark overflow pages ``cache: false`` instead of letting the
    # firmware discover mid-sync that the stack doesn't fit. Priority is
    # ring distance from the home card in deck order (home and its
    # neighbours cache first); uncached pages keep their links and fall
    # back to a network fetch when navigated to. Logged, never silent.
    if capacity_bytes is not None and capacity_bytes > 0 and pages:
        home_id = deck.resolved_home_page_id
        ids = [p["page_id"] for p in pages]
        home_idx = ids.index(home_id) if home_id in ids else 0
        n = len(ids)

        def ring_distance(i: int) -> int:
            d = abs(i - home_idx)
            return min(d, n - d)

        budget = capacity_bytes
        keep: set[str] = set()
        for i in sorted(range(n), key=lambda i: (ring_distance(i), i)):
            size = int(pages[i].get("bytes") or 0)
            if size <= budget:
                keep.add(ids[i])
                budget -= size
        dropped = [pid for pid in ids if pid not in keep]
        if dropped:
            logger.info(
                "deck manifest: %d page(s) exceed device capacity %d for %s; "
                "marked cache=false: %s",
                len(dropped),
                capacity_bytes,
                device_id,
                ",".join(dropped),
            )
            for page_view in pages:
                if page_view["page_id"] not in keep:
                    page_view["cache"] = False

    manifest: dict[str, Any] = {
        "deck_id": deck.id,
        "entry_page_id": deck.resolved_entry_page_id,
        "pages": pages,
    }
    # Home card: shipped so SD-cache firmware can return to it locally
    # after the idle timeout, radio off. Absent when the feature is off.
    if deck.home_timeout_minutes > 0:
        manifest["home"] = {
            "page_id": deck.resolved_home_page_id,
            "timeout_s": deck.home_timeout_minutes * 60,
        }
    manifest["version"] = version_digest(manifest)
    return manifest


def current_version(
    deck: Deck,
    device_id: str,
    *,
    push_mgr: _DeckRenderSource,
    renders_dir: Path,
) -> str:
    """The deck version for a device right now, without warming
    anything. Cheap enough for every ``/status`` response."""
    return str(
        build_manifest(
            deck,
            device_id,
            push_mgr=push_mgr,
            renders_dir=renders_dir,
            warm_missing=False,
        )["version"]
    )


def frame_entry_by_digest(
    deck: Deck,
    device_id: str,
    digest: str,
    *,
    push_mgr: _DeckRenderSource,
) -> dict[str, Any] | None:
    """The warmed render info whose digest matches, scanning the bound
    deck's pages for this device. None when no warmed page carries the
    digest (stale manifest on the client: it should re-sync)."""
    if not digest:
        return None
    for page in deck.pages:
        info = push_mgr.deck_render_for(device_id, page.page_id)
        if info is not None and str(info.get("digest") or "") == digest:
            return info
    return None
