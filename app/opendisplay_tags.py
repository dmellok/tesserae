"""What Home Assistant knows about each OpenDisplay tag.

An OpenDisplay tag never talks to Tesserae, but its firmware config (pixel
size, its own rotation, colour scheme) is read by HA's OpenDisplay
integration when the tag is set up. Two HA endpoints expose it, both over the
ha_core REST connection:

* The device registry, through ``/api/template``. The integration names the
  model ``'<size> <SCHEME>'`` (``'3.5" BWRY'``), so the colour scheme is
  always there; the size is usually a diagonal, not pixels.
* The config entry's diagnostics, ``/api/diagnostics/config_entry/<id>``
  (administrator token). It carries the display block as the tag reported
  it: ``pixel_width`` / ``pixel_height``, ``rotation`` and ``color_scheme``.

From those this module works out, per HA device id, the palette and the
**surface** the tag expects a picture for: its pixel size turned by the tag's
own rotation. The SDK applies that rotation to whatever it is sent before it
fits the picture to the glass, so a frame is right when it is upright on the
surface, never when it is aimed at the raw panel. Several tags are taller
than wide (a Solum 3.5" is 184x384), which is why the surface has to be read
rather than assumed to be landscape.

Everything here is best effort and never raises: with HA unreachable, a
non-admin token, or an integration build that words things differently, the
caller gets less (or nothing) and the operator's own settings stand.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

INTEGRATION = "opendisplay"

# py-opendisplay's ``ColorScheme`` names -> Tesserae panel gamuts. The split
# six-colour scheme is the same inks driven as two half-panels.
SCHEME_GAMUTS: dict[str, str] = {
    "MONO": "mono",
    "BWR": "bwr_3",
    "BWY": "bwy_3",
    "BWRY": "bwry_4",
    "BWGBRY": "waveshare_e6",
    "BWGBRY_SPLIT": "waveshare_e6",
    "GRAYSCALE_4": "gray_4",
    "GRAYSCALE_16": "gray_16",
    "SEVEN_COLOR": "inky_7colour",
}
# The same enum by its wire value, which is what diagnostics carries.
_SCHEME_BY_VALUE: dict[int, str] = {
    0: "MONO",
    1: "BWR",
    2: "BWY",
    3: "BWRY",
    4: "BWGBRY",
    5: "GRAYSCALE_4",
    6: "GRAYSCALE_16",
    7: "SEVEN_COLOR",
    8: "BWGBRY_SPLIT",
}

_SCHEME_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]*$")
_ENTRY_ID_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")

# How long a detection is reused. Tag config changes when someone reflashes
# or reconfigures a tag, so minutes of staleness cost nothing, while the
# device page and the picker would otherwise each pay one HA request per tag.
CACHE_TTL_S = 300.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


def gamut_from_model(model: object) -> str | None:
    """The panel gamut named at the end of an HA model string, or None.

    ``'3.5" BWRY'`` -> ``bwry_4``; ``'296x128 GRAYSCALE_4'`` -> ``gray_4``."""
    if not isinstance(model, str):
        return None
    match = _SCHEME_TOKEN_RE.search(model.strip())
    return SCHEME_GAMUTS.get(match.group(0)) if match else None


def gamut_from_scheme(value: object) -> str | None:
    """The panel gamut for a ``color_scheme`` wire value or enum name."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return SCHEME_GAMUTS.get(_SCHEME_BY_VALUE.get(value, ""))
    if isinstance(value, str):
        return SCHEME_GAMUTS.get(value.strip().upper())
    return None


def facts_from_display(display: object) -> dict[str, Any]:
    """Surface + palette from one diagnostics ``displays[]`` block.

    ``w`` / ``h`` are the surface: the pixel size turned by the tag's own
    rotation, which is the shape of picture the tag wants. Empty when the
    block carries no usable size."""
    if not isinstance(display, dict):
        return {}
    try:
        pw = int(display.get("pixel_width") or 0)
        ph = int(display.get("pixel_height") or 0)
    except (TypeError, ValueError):
        return {}
    if pw <= 0 or ph <= 0:
        return {}
    try:
        rotation = int(display.get("rotation") or 0)
    except (TypeError, ValueError):
        rotation = 0
    # Degrees on current firmware; older data stored a quarter-turn index.
    if rotation in (1, 2, 3):
        rotation *= 90
    rotation %= 360
    w, h = (ph, pw) if rotation in (90, 270) else (pw, ph)
    facts: dict[str, Any] = {"w": w, "h": h, "tag_rotation": rotation}
    gamut = gamut_from_scheme(display.get("color_scheme"))
    if gamut:
        facts["gamut"] = gamut
    return facts


def facts_from_diagnostics(payload: object) -> dict[str, Any]:
    """Surface + palette from a diagnostics download, or ``{}``."""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    config = data.get("device_config") if isinstance(data, dict) else None
    displays = config.get("displays") if isinstance(config, dict) else None
    if not isinstance(displays, list) or not displays:
        return {}
    return facts_from_display(displays[0])


def build_registry_template() -> str:
    """Jinja that renders one JSON row per OpenDisplay device: its id, config
    entry and model.

    ``config_entry_id`` is used as a function inside its own guarded template
    so an HA build without it costs the diagnostics lookup, never the list."""
    return (
        "{%- set ns = namespace(rows=[], seen=[]) -%}"
        f"{{%- for e in integration_entities('{INTEGRATION}') -%}}"
        "{%- set did = device_id(e) -%}"
        "{%- if did and did not in ns.seen -%}"
        "{%- set ns.seen = ns.seen + [did] -%}"
        "{%- set ns.rows = ns.rows + [{"
        "'device_id': did,"
        " 'entry_id': config_entry_id(e),"
        " 'model': device_attr(did, 'model')}] -%}"
        "{%- endif -%}"
        "{%- endfor -%}"
        "{{ ns.rows | tojson }}"
    )


def _registry_rows(mod: Any) -> list[dict[str, Any]]:
    try:
        rows = json.loads(mod.render_template(build_registry_template()))
    except Exception as exc:
        logger.debug("opendisplay tags: registry query failed (%s)", exc)
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _diagnostics_facts(mod: Any, entry_id: object) -> dict[str, Any]:
    if not isinstance(entry_id, str) or not _ENTRY_ID_RE.match(entry_id):
        return {}
    if not hasattr(mod, "request_json"):
        return {}
    try:
        payload = mod.request_json(f"/api/diagnostics/config_entry/{entry_id}")
    except Exception as exc:
        # A non-admin token answers 401 here; the model string still gives
        # the palette, so this is a lesser detection, not a failure.
        logger.debug("opendisplay tags: diagnostics for %s failed (%s)", entry_id, exc)
        return {}
    return facts_from_diagnostics(payload)


def detect(mod: Any, *, fresh: bool = False) -> dict[str, dict[str, Any]]:
    """Facts for every OpenDisplay tag HA knows, keyed by HA device id.

    Each value may hold ``gamut``, ``w`` / ``h`` (the surface),
    ``tag_rotation`` and ``model``; any of them can be missing. Call inside
    an app context (ha_core reads its settings through ``current_app``)."""
    if mod is None or not hasattr(mod, "render_template"):
        return {}
    try:
        key = str(mod.base_url())
    except Exception:
        key = ""
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    found: dict[str, dict[str, Any]] = {}
    for row in _registry_rows(mod):
        device_id = row.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            continue
        facts = _diagnostics_facts(mod, row.get("entry_id"))
        model = row.get("model")
        if isinstance(model, str) and model:
            facts["model"] = model
            facts.setdefault("gamut", gamut_from_model(model))
            if facts.get("gamut") is None:
                facts.pop("gamut", None)
        if facts:
            found[device_id] = facts
    # An empty answer is not cached: it is what an unreachable HA looks
    # like, and the next caller should try again rather than wait it out.
    if found:
        with _cache_lock:
            _cache[key] = (now, found)
    return found


def lookup(mod: Any, ha_device_id: str, *, fresh: bool = False) -> dict[str, Any]:
    """Facts for one tag, or ``{}``."""
    if not ha_device_id:
        return {}
    return dict(detect(mod, fresh=fresh).get(ha_device_id) or {})


def enrich(rows: list[dict[str, Any]], mod: Any) -> list[dict[str, Any]]:
    """Add each picker row's detected ``gamut`` and surface ``w`` / ``h``.

    A size read from the tag replaces one guessed from the model string."""
    if not rows:
        return rows
    facts = detect(mod)
    for row in rows:
        known = facts.get(str(row.get("device_id") or ""))
        if not known:
            continue
        if known.get("gamut"):
            row["gamut"] = known["gamut"]
        if known.get("w") and known.get("h"):
            row["w"], row["h"] = known["w"], known["h"]
    return rows
