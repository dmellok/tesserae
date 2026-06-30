"""Per-device battery offset.

Devices report ``battery_mv`` and ``battery_pct`` over MQTT or REST,
both of which the firmware derives from its on-board ADC. Two real-
world failure modes make those values disagree with a benchtop
voltmeter:

1. **ADC reference drift.** The MCU's voltage reference is rarely
   factory-calibrated; a 3.86 V cell reads as 3.71 V on a Pico-W's
   default 3.3 V reference, which then derives a too-low percent.
2. **Voltage-to-percent curve mismatch.** Firmware ships with a
   generic LiPo curve that doesn't match the user's specific cell
   chemistry (LiFePO4 vs LiPo vs LiHV all need different curves).

Rather than push a calibration screen to every device, Tesserae lets
the user record a **display offset** on the server side: "this device
reports 3.85 V but my voltmeter says 4.20 V; add 350 mV before
display." The offset applies at read time only; raw values stay in
the SQLite store unchanged so a recalibration tomorrow doesn't lose
the historical record.

The offset block lives on the device's instance manifest under
``battery_offset``:

    {
        "battery_offset": {
            "mv": 350,  # added to reported mV before display
            "pct": 0    # added to displayed pct after mV-derived bump
        }
    }

Missing keys default to 0; a manifest with no ``battery_offset`` at
all behaves identically to the pre-offset world.
"""

from __future__ import annotations

from typing import Any

# Mapping voltage to percent on a 1S LiPo cell. Coarse but matches
# the curve most firmware ships; used only as a fallback when we have
# a mV offset but no pct offset and need to translate one to the
# other. The widget never sees this curve directly; it's a server-
# side display detail.
_LIPO_CURVE: list[tuple[int, int]] = [
    (3200, 0),
    (3450, 5),
    (3550, 10),
    (3650, 20),
    (3750, 40),
    (3850, 60),
    (3950, 80),
    (4050, 95),
    (4200, 100),
]


def get_offset(device_manifest: dict[str, Any]) -> tuple[int, int]:
    """Return the configured ``(mv_offset, pct_offset)`` for a device
    instance. Defaults to ``(0, 0)`` when the manifest doesn't carry
    a ``battery_offset`` block or carries an unparseable one."""
    block = device_manifest.get("battery_offset") if isinstance(device_manifest, dict) else None
    if not isinstance(block, dict):
        return (0, 0)
    try:
        mv = int(block.get("mv") or 0)
    except (TypeError, ValueError):
        mv = 0
    try:
        pct = int(block.get("pct") or 0)
    except (TypeError, ValueError):
        pct = 0
    return (mv, pct)


def apply_to_mv(raw_mv: int | None, mv_offset: int) -> int | None:
    """Add ``mv_offset`` to the raw reading. Negative results clamp to
    0 (a meaningfully-calibrated cell never reads below 0 mV; if the
    offset accidentally pushes the displayed value negative the user
    sees the floor rather than a sign-flipped value)."""
    if raw_mv is None:
        return None
    return max(0, int(raw_mv) + int(mv_offset))


def apply_to_pct(
    raw_pct: int | None,
    mv_offset: int,
    pct_offset: int,
    *,
    raw_mv: int | None = None,
) -> int | None:
    """Translate the user-configured offsets into a corrected percent.

    Two contributions, in order:

    1. **mV-derived bump.** If the user configured a ``mv_offset`` and
       we have a raw mV reading, the LiPo curve gives us the corrected
       percent directly (more accurate than scaling the firmware-
       reported percent because the firmware's voltage-to-percent
       lookup may itself be wrong). Falls through to raw_pct when we
       don't have a mV reading.
    2. **Direct pct offset.** Added on top. Useful when the user
       knows the offset they want as a percent ("UI says 85% but
       voltmeter says 100%, add 15%") without having to back into a
       mV adjustment.

    Result is clamped to ``0..100``. ``None`` raw_pct stays ``None``.
    """
    if raw_pct is None and raw_mv is None:
        return None
    base: float
    if mv_offset != 0 and raw_mv is not None:
        corrected_mv = max(0, int(raw_mv) + int(mv_offset))
        base = float(_mv_to_pct(corrected_mv))
    elif raw_pct is not None:
        base = float(raw_pct)
    else:
        # No raw_pct and no mv offset to apply: nothing to display.
        return None
    base += float(pct_offset)
    return max(0, min(100, round(base)))


def _mv_to_pct(mv: int) -> int:
    """Piecewise-linear lookup against ``_LIPO_CURVE``. Clamps at the
    curve's endpoints so a wildly out-of-range voltage (e.g. a sensor
    glitch) doesn't extrapolate to a nonsensical percent."""
    if mv <= _LIPO_CURVE[0][0]:
        return _LIPO_CURVE[0][1]
    if mv >= _LIPO_CURVE[-1][0]:
        return _LIPO_CURVE[-1][1]
    for i in range(len(_LIPO_CURVE) - 1):
        v0, p0 = _LIPO_CURVE[i]
        v1, p1 = _LIPO_CURVE[i + 1]
        if v0 <= mv <= v1:
            if v1 == v0:
                return p0
            t = (mv - v0) / (v1 - v0)
            return round(p0 + t * (p1 - p0))
    return _LIPO_CURVE[-1][1]
