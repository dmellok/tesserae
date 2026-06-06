"""ha_camera, snapshot from one or more HA ``camera.*`` entities.

Thin widget over ha_core. Each camera entity exposes an
``entity_picture`` attribute, a relative path like
``/api/camera_proxy/camera.front_door?token=…``, which we resolve to an
absolute URL by prepending HA's configured base URL so the panel (which
renders headlessly via Tesserae's loopback) can actually fetch the
bytes.

Shape per entity::

    {
        "entity_id":    "camera.front_door",
        "name":         "Front Door",
        "image_url":    "http://homeassistant.local:8123/api/camera_proxy/...",
        "last_updated": "2026-06-03T10:14:22.198+00:00",
        "state":        "recording" | "idle" | "streaming" | …,
        "motion":       bool | None,   # if HA surfaces a motion attribute
    }
"""

from __future__ import annotations

from typing import Any

from flask import current_app


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    """Entity picker for the editor, restricted to ``camera.*``."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices(domains=("camera",))
    return []


def _entity_lines(raw: Any) -> list[str]:
    """Parse the optional textarea of extra cameras: one entity_id per
    line, blanks and whitespace stripped."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [line.strip() for line in str(raw).splitlines() if line.strip()]


def _absolute(core: Any, url: str) -> str:
    """Resolve HA's ``entity_picture`` to an absolute URL.

    HA returns it as a site-relative path; the panel needs a fully
    qualified one. If the entity already produced an absolute URL
    (some integrations do), pass it through untouched."""
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    base = core.base_url()
    if not base:
        return url
    if not url.startswith("/"):
        url = "/" + url
    return base + url


def _motion(attrs: dict[str, Any]) -> bool | None:
    """Best-effort read of a motion flag from a camera's attributes.
    Different HA integrations use different keys; check the common
    ones and only return a value when it's actually present."""
    for key in ("motion_detected", "motion", "is_motion", "motion_sensor"):
        if key in attrs:
            value = attrs[key]
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("on", "true", "detected", "yes", "1")
    return None


def _shape(core: Any, st: dict[str, Any]) -> dict[str, Any]:
    attrs = st.get("attributes") or {}
    image_url = _absolute(core, str(attrs.get("entity_picture") or ""))
    return {
        "entity_id": str(st.get("entity_id") or ""),
        "name": core.friendly_name(st),
        "image_url": image_url,
        "last_updated": str(st.get("last_updated") or st.get("last_changed") or ""),
        "last_changed": str(st.get("last_changed") or ""),
        "state": str(st.get("state") or ""),
        "motion": _motion(attrs),
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": "Install the Home Assistant Core plugin to use this widget."}

    primary = str(options.get("entity_id") or "").strip()
    extras = _entity_lines(options.get("entities"))
    # De-duplicate while preserving order so the primary always shows first.
    seen: set[str] = set()
    wanted: list[str] = []
    for eid in [primary, *extras]:
        if eid and eid not in seen:
            seen.add(eid)
            wanted.append(eid)

    if not wanted:
        return {"error": "Set a camera entity (e.g. camera.front_door)."}

    items: list[dict[str, Any]] = []
    last_err: str | None = None
    for eid in wanted:
        try:
            st = core.get_state(eid)
        except Exception as err:
            # Keep going so a single bad entity doesn't blank the whole
            # grid; surface the error only if nothing worked.
            last_err = core.coerce_error(err)
            items.append(
                {
                    "entity_id": eid,
                    "name": eid,
                    "image_url": "",
                    "last_updated": "",
                    "last_changed": "",
                    "state": "unavailable",
                    "motion": None,
                }
            )
            continue
        if not st:
            items.append(
                {
                    "entity_id": eid,
                    "name": eid,
                    "image_url": "",
                    "last_updated": "",
                    "last_changed": "",
                    "state": "unavailable",
                    "motion": None,
                }
            )
            continue
        items.append(_shape(core, st))

    if not items:
        return {"error": last_err or "No camera entities resolved."}

    return {
        "label": options.get("label", "Camera"),
        "items": items,
    }
