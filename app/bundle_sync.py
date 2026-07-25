"""State bundles (protocol v2): the reachable-state set a device holds
locally for tier-0 navigation.

A bundle is the v2 projection of the deck pre-render cache: every deck
page with a warmed per-device render becomes a ``frame`` state, and the
deck's link graph (buttons + zones + default neighbours) becomes the
``links`` table keyed the way a v2 device navigates (gesture names and
manifest region ids). Content-hash keyed: the bundle digest hashes the
member digests + links, so any re-render or deck edit bumps it and the
device's sync diff fetches only unknown digests.

``tile`` states (pre-shipped toggle alternates) are part of the wire
contract but have no producer yet: they require state declarations on
elements (protocol v2 decisions log), so bundles ship frame-only until
that lands and the firmware's tile machinery simply stays idle.

mypy --strict does not apply here; shapes mirror app.deck_sync.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.deck_sync import default_neighbours
from app.state.deck_model import Deck

logger = logging.getLogger(__name__)

# Frames with no page-level cadence stay navigable this long before a
# device treats them as stale for tier-0 nav (same policy as deck sync).
_DEFAULT_TTL_S = 86400

_BUTTON_GESTURES = {"left": "swipe_right", "right": "swipe_left"}


def _links_for(deck: Deck) -> dict[str, dict[str, str]]:
    """The navigation table: per current page, gesture/button names to
    target state ids. Zones are omitted (a v2 device resolves zone taps
    through its manifest regions and reports them; only whole-gesture
    swipes and buttons navigate bundle-locally)."""
    out: dict[str, dict[str, str]] = {}
    for page in deck.pages:
        entry: dict[str, str] = {}
        for link in page.links:
            if link.button:
                entry[str(link.button)] = f"page:{link.target_page_id}"
                gesture = _BUTTON_GESTURES.get(str(link.button))
                if gesture:
                    entry.setdefault(gesture, f"page:{link.target_page_id}")
        neighbours = default_neighbours(deck, page.page_id)
        if neighbours is not None:
            prev_page, next_page = neighbours
            entry.setdefault("left", f"page:{prev_page}")
            entry.setdefault("right", f"page:{next_page}")
            entry.setdefault("swipe_right", f"page:{prev_page}")
            entry.setdefault("swipe_left", f"page:{next_page}")
        if entry:
            out[f"page:{page.page_id}"] = entry
    return out


def build_bundle(
    deck: Deck,
    device_id: str,
    *,
    push_mgr: Any,
    renders_dir: Path,
) -> dict[str, Any] | None:
    """The device's state bundle, or None when nothing is warmed yet.
    Only pages with a warmed per-device render become states (a cold
    page is simply absent and its nav degrades to tier 2 until the deck
    refresh warms it; the bundle digest then changes and the device
    re-syncs)."""
    states: list[dict[str, Any]] = []
    for page in deck.pages:
        info = push_mgr.deck_render_for(device_id, page.page_id)
        if info is None:
            continue
        digest = str(info.get("digest") or "")
        filename = str(info.get("filename") or "")
        if not digest or not filename:
            continue
        try:
            size = (renders_dir / filename).stat().st_size
        except OSError:
            continue
        states.append(
            {
                "kind": "frame",
                "state_id": f"page:{page.page_id}",
                "frame_digest": digest,
                "bytes": size,
                "ttl_s": _DEFAULT_TTL_S,
                "url": f"/api/v1/device/{device_id}/bundle/frame/{digest}",
            }
        )
    if not states:
        return None
    links = _links_for(deck)
    fingerprint = json.dumps(
        {
            "states": sorted(s["frame_digest"] for s in states),
            "links": links,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "bundle_digest": hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16],
        "states": states,
        "links": links,
    }


def bundle_digest_for(deck: Deck | None, device_id: str, *, push_mgr: Any) -> str:
    """The current bundle version without building the full document
    (the SSE sync event's cheap change detector)."""
    if deck is None or push_mgr is None:
        return ""
    digests = []
    for page in deck.pages:
        info = push_mgr.deck_render_for(device_id, page.page_id)
        if info is not None and info.get("digest"):
            digests.append(str(info["digest"]))
    if not digests:
        return ""
    fingerprint = json.dumps(
        {"states": sorted(digests), "links": _links_for(deck)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
