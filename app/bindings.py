"""Live data bindings for canvas shape elements.

Data elements (``kind:"data"``) re-evaluate their binding every render, which is
why numbers and text stay live. Every other kind (rect, ellipse, icon, line,
text) is static geometry: the renderer redraws it but never recomputes it. A
:class:`app.state.panel_store.Binding` closes that gap declaratively: read a
widget field each render and map it through a transform to patch the element's
props (x / y / w / h / color / icon / text). The composer applies these in the
same pass that resolves data elements, so a bound shape updates in lockstep with
the live data on the same canvas, with no agent tick.

Transforms (all pure functions of the resolved value + params):

* ``position`` -- scalar to a coordinate along a segment (a moving marker).
* ``length``   -- scalar to a size (a gauge / bar that grows).
* ``pick``     -- integer index selects props from arrays (hop between states).
* ``color``    -- scalar to a colour by ascending thresholds (a step function).
* ``gradient`` -- scalar interpolated smoothly along colour stops. On e-ink the
                  result is quantised to the panel palette at render, this is a
                  value-driven gradient, not a temporal animation.
* ``icon``     -- code / string to a Phosphor glyph via a lookup table.

A binding that can't resolve its value (missing field, wrong type) yields an
empty patch, the element keeps its authored props, so a stale upstream never
corrupts the layout.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any


def _resolve_path(data: Any, path: str) -> Any:
    """Read a dotted / indexed path from a data payload. Supports ``a.b``,
    ``a.0.b`` and ``a[0].b``. Returns None on any missing segment."""
    if not path:
        return None
    cur = data
    for part in path.replace("[", ".").replace("]", "").split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _bound(value: Any, data: Any) -> float | None:
    """A transform bound that may be a constant or another field path (e.g. the
    sun-arc's ``in:["sun.riseMin","sun.setMin"]`` maps against two other
    fields)."""
    if isinstance(value, str):
        return _num(_resolve_path(data, value))
    return _num(value)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _parse_hex(color: str) -> tuple[int, int, int] | None:
    if not isinstance(color, str):
        return None
    h = color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return None


def _to_hex(r: float, g: float, b: float) -> str:
    ri = max(0, min(255, round(r)))
    gi = max(0, min(255, round(g)))
    bi = max(0, min(255, round(b)))
    return f"#{ri:02X}{gi:02X}{bi:02X}"


def _lerp_hex(a: str, b: str, t: float) -> str:
    pa, pb = _parse_hex(a), _parse_hex(b)
    if pa is None or pb is None:
        return a  # non-hex stop (e.g. a token): fall back to the near end
    return _to_hex(
        pa[0] + (pb[0] - pa[0]) * t,
        pa[1] + (pb[1] - pa[1]) * t,
        pa[2] + (pb[2] - pa[2]) * t,
    )


def _t_position(v: float, params: dict[str, Any], data: Any) -> dict[str, Any]:
    axis = params.get("axis")
    if axis not in ("x", "y"):
        return {}
    in_ = params.get("in") or [0, 1]
    out = params.get("out") or [0, 0]
    lo, hi = _bound(in_[0], data), _bound(in_[1], data)
    p0, p1 = _num(out[0]), _num(out[1])
    if None in (lo, hi, p0, p1) or hi == lo:
        return {}
    center = _num(params.get("center")) or 0.0
    t = _clamp((v - lo) / (hi - lo), 0.0, 1.0)  # type: ignore[operator]
    return {axis: round(p0 + t * (p1 - p0) - center / 2)}  # type: ignore[operator]


def _t_length(v: float, params: dict[str, Any], data: Any) -> dict[str, Any]:
    dim = params.get("dim")
    if dim not in ("w", "h"):
        return {}
    in_ = params.get("in") or [0, 1]
    out = params.get("out") or [1, 1]
    lo, hi = _bound(in_[0], data), _bound(in_[1], data)
    lo_px, hi_px = _num(out[0]), _num(out[1])
    if None in (lo, hi, lo_px, hi_px) or hi == lo:
        return {}
    t = _clamp((v - lo) / (hi - lo), 0.0, 1.0)  # type: ignore[operator]
    size = max(1, round(lo_px + t * (hi_px - lo_px)))  # type: ignore[operator]
    patch: dict[str, Any] = {dim: size}
    anchor = _num(params.get("anchorMax"))
    if anchor is not None:
        patch["x" if dim == "w" else "y"] = round(anchor - size)
    return patch


def _t_pick(v: float, params: dict[str, Any], data: Any) -> dict[str, Any]:
    idx = int(v)
    chosen = params.get("set") or {}
    center = _num(params.get("center"))
    patch: dict[str, Any] = {}
    for key, arr in chosen.items():
        if not isinstance(arr, list) or not (0 <= idx < len(arr)):
            continue
        val = arr[idx]
        if key in ("x", "y") and center is not None and isinstance(val, (int, float)):
            val = round(val - center / 2)
        patch[key] = val
    return patch


def _t_color(v: float, params: dict[str, Any], data: Any) -> dict[str, Any]:
    for stop in params.get("stops") or []:
        if (
            isinstance(stop, (list, tuple))
            and len(stop) == 2
            and _num(stop[0]) is not None
            and v <= float(stop[0])
        ):
            return {"color": stop[1]}
    fallback = params.get("else")
    return {"color": fallback} if fallback else {}


def _t_gradient(v: float, params: dict[str, Any], data: Any) -> dict[str, Any]:
    pts = [
        (float(s[0]), s[1])
        for s in (params.get("stops") or [])
        if isinstance(s, (list, tuple)) and len(s) == 2 and _num(s[0]) is not None
    ]
    if len(pts) < 2:
        return {}
    pts.sort(key=lambda p: p[0])
    if v <= pts[0][0]:
        return {"color": pts[0][1]}
    if v >= pts[-1][0]:
        return {"color": pts[-1][1]}
    for (a_val, a_hex), (b_val, b_hex) in pairwise(pts):
        if a_val <= v <= b_val:
            t = (v - a_val) / (b_val - a_val) if b_val != a_val else 0.0
            return {"color": _lerp_hex(a_hex, b_hex, t)}
    return {}


def _t_icon(v: Any, params: dict[str, Any], data: Any) -> dict[str, Any]:
    table = params.get("table") or {}
    name = table.get(str(v), params.get("default", ""))
    return {"icon": name} if name else {}


# Transforms that need a numeric driving value.
_NUMERIC = {"position", "length", "color", "gradient"}


def apply_binding(binding: Any, data: Any) -> dict[str, Any]:
    """Resolve a binding against a widget data payload and return the prop patch
    to merge onto the element (``{}`` when it can't resolve). Never raises: a bad
    binding degrades to no change rather than breaking the render."""
    try:
        value = _resolve_path(data, getattr(binding, "field", ""))
        if value is None:
            return {}
        transform = getattr(binding, "transform", "")
        params = getattr(binding, "params", None) or {}
        if transform in _NUMERIC:
            num = _num(value)
            if num is None:
                return {}
            value = num
        if transform == "position":
            return _t_position(value, params, data)
        if transform == "length":
            return _t_length(value, params, data)
        if transform == "pick":
            num = _num(value)
            return _t_pick(num, params, data) if num is not None else {}
        if transform == "color":
            return _t_color(value, params, data)
        if transform == "gradient":
            return _t_gradient(value, params, data)
        if transform == "icon":
            return _t_icon(value, params, data)
        return {}
    except Exception:
        return {}
