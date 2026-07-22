"""Minimal plain-SemVer comparison, no third-party dependency.

Firmware and app versions are plain ``X.Y.Z`` (optionally ``v``-prefixed), so
the appliance image doesn't need a PEP 440 parser (``packaging``) at runtime,
one absent from the slim Docker image. Anything with a pre-release / build
suffix (``1.6.0-rc1``) is unparseable here and returns ``None`` so each caller
applies its own conservative fallback.
"""

from __future__ import annotations


def parse_version(value: str) -> tuple[int, ...] | None:
    """Plain ``X.Y.Z`` (optional leading ``v``) as a comparable int tuple, or
    ``None`` when any component isn't a bare integer."""
    parts = value.strip().lstrip("vV").split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def is_strictly_newer(candidate: str, current: str) -> bool | None:
    """``True`` / ``False`` when both parse as plain SemVer (padded so ``1.6``
    and ``1.6.0`` compare equal); ``None`` when either doesn't, leaving the
    fallback policy to the caller."""
    cand = parse_version(candidate)
    cur = parse_version(current)
    if cand is None or cur is None:
        return None
    width = max(len(cand), len(cur))
    cand += (0,) * (width - len(cand))
    cur += (0,) * (width - len(cur))
    return cand > cur
