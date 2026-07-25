"""Interaction manifests (protocol v2, docs/protocol-v2-touch.md).

One JSON document per served frame telling a v2 device everything it
needs to own hit testing and immediate feedback: wire-space region
rects with stable ids, gesture declarations, the action's tier + type
(never its payload — domains/services/URLs stay server-side), the
local feedback mode, and device-rendered text regions with their glyph
atlases. The device reports ``region_id`` + gesture and the server
resolves the payload from the same sidecar this document was built
from.

Region identity: an explicit ``data-touch-id`` (or the canvas element
id stamped as ``data-el-id``) pins ``el:<id>:<gesture>``; everything
else gets ``mk:<hash>`` derived from the action spec + a coarse rect
bucket, which survives pixel-level layout jitter but not markup
rewrites (those regions may churn ids and lose tier-1 optimism, by
design — see the decisions log in docs/protocol-v2-touch.md).

mypy --strict does not apply here; shapes mirror app.overlay_sync.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.overlay_sync import _panel_geometry, rect_to_wire
from app.touch_regions import is_side_effecting

logger = logging.getLogger(__name__)

# Firmware-side pools (the v2 firmware prompt's parsed-manifest budget).
MAX_REGIONS = 32
MAX_TEXT = 8
MAX_ATLASES = 2

_NAV_PREFIXES = ("page:", "step:")
_NAV_BARE = frozenset({"rotate_next", "rotate_prev"})
_TIER2_ACTIONS = frozenset({"refresh", "fetch_latest"})


def _classify(spec: str | dict[str, Any]) -> dict[str, Any]:
    """The ``action`` block for one resolved spec: tier + type (+ nav
    target). The device only ever learns how to behave, never the
    payload."""
    if isinstance(spec, dict):
        return {"tier": 1, "type": "ha"}
    name = spec.split(":", 1)[0].strip()
    if spec.startswith("page:"):
        return {"tier": 0, "type": "nav", "target": spec.strip()}
    if spec.startswith(_NAV_PREFIXES) or name in _NAV_BARE:
        return {"tier": 0, "type": "nav"}
    if name in _TIER2_ACTIONS:
        return {"tier": 2, "type": name}
    if name == "webhook":
        return {"tier": 0, "type": "webhook"}
    # Unknown grammar (plugin-registered actions): safest is round trip.
    return {"tier": 2, "type": name or "unknown"}


def _spec_fingerprint(spec: str | dict[str, Any]) -> str:
    return spec if isinstance(spec, str) else json.dumps(spec, sort_keys=True)


def _region_id(
    region: dict[str, Any],
    gesture: str,
    spec: str | dict[str, Any],
    used: set[str],
) -> str:
    """Stable id for one (region, gesture) pair: pinned ``el:`` when the
    markup carried an id, else ``mk:<8-hex>`` over the action + a 16 px
    rect bucket. Collisions (two identical unpinned regions) get a
    ``~n`` suffix in document order, deterministically."""
    pinned = region.get("touch_id")
    if isinstance(pinned, str) and pinned:
        base = f"el:{pinned}:{gesture}"
    else:
        bucket = (int(region.get("x", 0)) // 16, int(region.get("y", 0)) // 16)
        seed = f"{_spec_fingerprint(spec)}|{gesture}|{bucket[0]},{bucket[1]}"
        base = f"mk:{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:8]}"
    out = base
    n = 2
    while out in used:
        out = f"{base}~{n}"
        n += 1
    used.add(out)
    return out


def _dispatchable(region: dict[str, Any], spec: str | dict[str, Any]) -> bool:
    """Mirror the dispatch provenance gate at build time: side-effecting
    actions from raw markup never enter a manifest, so a device can't be
    told to echo-and-report something the server would refuse anyway."""
    return not (is_side_effecting(spec) and region.get("origin") != "config")


def _gesture_entries(region: dict[str, Any]) -> list[tuple[str, dict[str, Any], Any]]:
    """Explode one sidecar region into manifest entries: (gesture-slot,
    gestures-block, spec). Tap and each swipe direction are separate
    entries (their actions may differ); a slide region absorbs all
    gestures into one entry."""
    slide = region.get("slide")
    if isinstance(slide, dict) and slide.get("action") is not None:
        axis = "y" if str(slide.get("axis") or "y") == "y" else "x"
        return [("slide", {"slide": {"axis": axis}}, slide["action"])]
    out: list[tuple[str, dict[str, Any], Any]] = []
    tap = region.get("tap")
    if tap is not None:
        out.append(("tap", {"tap": True}, tap))
    swipe = region.get("swipe")
    if isinstance(swipe, dict):
        for direction in ("up", "down", "left", "right"):
            spec = swipe.get(direction)
            if spec is not None:
                out.append((f"swipe_{direction}", {"swipe": {direction: True}}, spec))
    return out


def _feedback(gesture: str, action: dict[str, Any], value_text: str | None) -> dict[str, Any]:
    if gesture == "slide":
        fb: dict[str, Any] = {"mode": "slider", "track": "vertical"}
        if value_text:
            fb["value_text"] = value_text
        return fb
    return {"mode": "invert"}


def build_interaction_manifest(
    *,
    frame_digest: str,
    regions: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    panel: dict[str, Any],
    atlas_provider: Any | None = None,
    max_regions: int = MAX_REGIONS,
) -> dict[str, Any] | None:
    """The protocol-v2 interaction manifest for one served frame, or
    None when the panel geometry is unusable. Empty ``regions`` +
    ``text`` is a valid manifest (the caller may prefer a 404 then).

    ``atlas_provider(px, weight)`` returns ``{digest, url, format,
    height, glyphs}`` or None; text regions whose atlas fails are
    dropped (the baked-in render keeps showing). Slots group by
    (px, weight); the two largest groups win the atlas budget."""
    geo = _panel_geometry(panel)
    if geo is None:
        return None

    def to_wire(entry: dict[str, Any]) -> tuple[int, int, int, int] | None:
        try:
            rect = (float(entry["x"]), float(entry["y"]), float(entry["w"]), float(entry["h"]))
        except (KeyError, TypeError, ValueError):
            return None
        return rect_to_wire(
            rect,
            comp_w=geo["comp_w"],
            comp_h=geo["comp_h"],
            native_w=geo["native_w"],
            native_h=geo["native_h"],
            flip=geo["flip"],
            underscan=geo["underscan"],
        )

    used_ids: set[str] = set()
    out_regions: list[dict[str, Any]] = []
    for region in regions:
        wire = to_wire(region)
        if wire is None:
            continue
        for gesture, gestures_block, spec in _gesture_entries(region):
            if not _dispatchable(region, spec):
                continue
            action = _classify(spec)
            entry = {
                "id": _region_id(region, gesture, spec, used_ids),
                "rect": {"x": wire[0], "y": wire[1], "w": wire[2], "h": wire[3]},
                "gestures": gestures_block,
                "action": action,
                "feedback": _feedback(gesture, action, None),
            }
            out_regions.append(entry)
    if len(out_regions) > max_regions:
        # Nav-priority trim, document order within each class: the
        # firmware pool is fixed, and navigation deserves echo most.
        ranked = sorted(
            enumerate(out_regions),
            key=lambda p: (p[1]["action"]["type"] != "nav", p[0]),
        )
        kept = {id(e) for _i, e in ranked[:max_regions]}
        logger.info(
            "manifest: %d regions > budget %d for frame %s; nav wins",
            len(out_regions),
            max_regions,
            frame_digest,
        )
        out_regions = [e for e in out_regions if id(e) in kept]

    out_text: list[dict[str, Any]] = []
    usable = [s for s in slots if isinstance(s, dict)]
    if usable and atlas_provider is not None:
        groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for s in usable:
            try:
                groups.setdefault((int(s["px"]), int(s["weight"])), []).append(s)
            except (KeyError, TypeError, ValueError):
                continue
        ranked_groups = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for (px, weight), members in ranked_groups[:MAX_ATLASES]:
            atlas = atlas_provider(px, weight)
            if atlas is None:
                logger.warning(
                    "manifest: atlas build failed px=%d weight=%d; dropping %d text region(s)",
                    px,
                    weight,
                    len(members),
                )
                continue
            for s in members:
                if len(out_text) >= MAX_TEXT:
                    break
                wire = to_wire(s)
                if wire is None:
                    continue
                key = str(s.get("key") or "")
                tid = f"tx:{hashlib.sha256(f'{key}|{wire[0]},{wire[1]}'.encode()).hexdigest()[:8]}"
                out_text.append(
                    {
                        "id": tid,
                        "rect": {"x": wire[0], "y": wire[1], "w": wire[2], "h": wire[3]},
                        "align": str(s.get("align") or "left"),
                        "atlas": atlas,
                        "key": key,
                        "max_chars": 47,
                    }
                )

    doc: dict[str, Any] = {
        "proto": 2,
        "frame_digest": frame_digest,
        "regions": out_regions,
        "text": out_text,
        "caps": {"max_regions": max_regions, "max_text": MAX_TEXT, "max_atlases": MAX_ATLASES},
    }
    # The manifest digest hashes everything EXCEPT the frame digest, so
    # a patch-only pixel change (same layout) re-anchors on the device
    # without a re-fetch.
    body = {k: v for k, v in doc.items() if k != "frame_digest"}
    doc["manifest_digest"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return doc


def resolve_region_action(
    sidecar_regions: list[dict[str, Any]],
    region_id: str,
    panel: dict[str, Any],
) -> tuple[dict[str, Any], str, Any] | None:
    """Resolve a reported region id back to ``(sidecar_region, gesture,
    spec)`` for dispatch. Rebuilds the id mint deterministically over the
    same sidecar, so no id map needs persisting. None when the id names
    nothing in this frame (stale or forged report)."""
    geo = _panel_geometry(panel)
    if geo is None:
        return None
    used: set[str] = set()
    for region in sidecar_regions:
        try:
            rect = (float(region["x"]), float(region["y"]), float(region["w"]), float(region["h"]))
        except (KeyError, TypeError, ValueError):
            continue
        if (
            rect_to_wire(
                rect,
                comp_w=geo["comp_w"],
                comp_h=geo["comp_h"],
                native_w=geo["native_w"],
                native_h=geo["native_h"],
                flip=geo["flip"],
                underscan=geo["underscan"],
            )
            is None
        ):
            continue
        for gesture, _gestures, spec in _gesture_entries(region):
            if not _dispatchable(region, spec):
                continue
            if _region_id(region, gesture, spec, used) == region_id:
                return (region, gesture, spec)
    return None
