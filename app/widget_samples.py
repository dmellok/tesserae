"""Hand-written dummy payloads for the dev widget gallery.

The ``/_test/widgets`` gallery passes ``?sample=1`` through to
``/_test/render`` so a reviewer can scan every variant of every widget
without configuring HA, Spotify, or whatever upstream a given widget
talks to. When the flag is set, ``_fetch_plugin_data`` looks the
plugin id up in ``SAMPLES`` first and returns that frozen payload
instead of calling the widget's own ``fetch()``.

Widgets that already talk to a public API in their default config
(weather → Open-Meteo, news → reddit/HN, F1 → Ergast, etc.) don't
need an entry here, their normal ``fetch()`` produces useful output
in the gallery already. Only add a sample when the widget would
otherwise be blank or error-stating in the gallery.

The payload shape must match the widget's ``fetch()`` return, same
keys, same nesting. If you change a widget's data contract, update
its sample too or the gallery will render a stale frame that masks
the regression.
"""

from __future__ import annotations

import copy
from typing import Any


def _ha_energy() -> dict[str, Any]:
    return {
        "label": "Home",
        "place": "Home",
        "time": "14:32",
        "solar_w": 3480.0,
        "grid_w": -1240.0,
        "battery_w": 820.0,
        "house_w": 1420.0,
        "battery_soc": 78.5,
        "solar_today_kwh": 22.4,
        "flow": "solar",
        "sparkline": [
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            120.0,
            480.0,
            1100.0,
            2050.0,
            2840.0,
            3320.0,
            3680.0,
            3920.0,
            4080.0,
            4120.0,
            3980.0,
            3760.0,
            3480.0,
            3120.0,
            2680.0,
            2240.0,
            1820.0,
            1380.0,
            980.0,
            640.0,
            360.0,
            180.0,
            60.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
    }


def _ha_battery() -> dict[str, Any]:
    low_t, crit_t = 30, 15
    raw: list[tuple[str, int]] = [
        ("Front Door Lock", 87),
        ("Living Room Sensor", 64),
        ("Kitchen Motion", 42),
        ("Back Door Sensor", 28),  # should flag low
        ("Garage Tilt", 91),
        ("Mailbox Sensor", 55),
    ]
    # The client reads ``low`` / ``critical`` as booleans (statusAccent
    # returns red/yellow/green from them), so the sample has to compute
    # them against the thresholds the same way the real server does -
    # otherwise every tile renders as "ok" green.
    items: list[dict[str, Any]] = [
        {"name": name, "level": level, "low": level < low_t, "critical": level < crit_t}
        for name, level in raw
    ]
    levels: list[int] = [level for _name, level in raw]
    # 10 buckets of 10 % each, that's what the d4 Data view paints
    # as its histogram. Without it the chart area stays blank.
    histogram = [0] * 10
    for lvl in levels:
        histogram[min(9, lvl // 10)] += 1
    return {
        "label": "Batteries",
        "time": "14:32",
        "low_threshold": 30,
        "critical_threshold": 15,
        "summary": {
            "count": len(items),
            "shown": len(items),
            "avg": sum(levels) / len(levels),
            "low": sum(1 for lvl in levels if lvl < 30),
            "critical": sum(1 for lvl in levels if lvl < 15),
            "histogram": histogram,
        },
        "items": items,
    }


_CAMERA_SAMPLE_DATA_URL = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 180'>"
    "<rect width='320' height='180' fill='%231f2933'/>"
    "<rect x='30' y='110' width='90' height='40' fill='%23556070'/>"
    "<polygon points='30,110 75,80 120,110' fill='%23394353'/>"
    "<rect x='160' y='90' width='130' height='60' fill='%23485061'/>"
    "<polygon points='160,90 225,55 290,90' fill='%23394353'/>"
    "<circle cx='250' cy='40' r='12' fill='%23e8c98a'/>"
    "<text x='10' y='20' font-family='monospace' font-size='10' fill='%23e8c98a'>FRONT DOOR · LIVE</text>"
    "</svg>"
)


def _ha_camera() -> dict[str, Any]:
    # Matches the server shape: ``{label, items: [{...}, ...]}``. The
    # client unwraps ``items[0]`` to drive the hero, multi-camera
    # rendering is a future iteration.
    return {
        "label": "Cameras",
        "items": [
            {
                "entity_id": "camera.front_door",
                "name": "Front Door",
                "image_url": _CAMERA_SAMPLE_DATA_URL,
                "last_updated": "2026-06-03T14:30:12+00:00",
                "last_changed": "2026-06-03T14:30:12+00:00",
                "state": "recording",
                "motion": True,
            },
        ],
    }


def _ha_climate() -> dict[str, Any]:
    return {
        "title": "Climate",
        "items": [
            {
                "name": "Living Room",
                "mode": "heat",
                "mode_label": "Heat",
                "action": "heating",
                "icon": "thermometer-hot",
                "current": "20.5",
                "target": "21.0",
                "target_high": None,
                "target_low": None,
                "unit": "°C",
            },
            {
                "name": "Bedroom",
                "mode": "auto",
                "mode_label": "Auto",
                "action": "idle",
                "icon": "thermometer",
                "current": "19.8",
                "target_low": "18.0",
                "target_high": "22.0",
                "target": None,
                "unit": "°C",
            },
        ],
    }


def _ha_entities() -> dict[str, Any]:
    return {
        "title": "Entities",
        "items": [
            {"name": "Living Room Lamp", "label": "On", "status": "on", "icon": "lightbulb"},
            {"name": "Front Door", "label": "Locked", "status": "secure", "icon": "lock"},
            {"name": "Outdoor Temp", "label": "16.4 °C", "status": "info", "icon": "thermometer"},
            {
                "name": "Washing Machine",
                "label": "Running",
                "status": "on",
                "icon": "washing-machine",
            },
            {"name": "Garage Door", "label": "Closed", "status": "secure", "icon": "garage"},
        ],
    }


def _ha_history() -> dict[str, Any]:
    def _curve(base: float, amp: float, phase: float, n: int = 48) -> list[float]:
        import math

        return [round(base + amp * math.sin(phase + i / n * math.pi * 2), 2) for i in range(n)]

    return {
        "title": "History",
        "hours": 24,
        "items": [
            {
                "name": "Living Room Temp",
                "unit": "°C",
                "current": "20.5",
                "values": _curve(20.4, 0.9, 0.4),
                "min": "19.5",
                "max": "21.3",
                "trend": "up",
                "sparse": False,
            },
            {
                "name": "Humidity",
                "unit": "%",
                "current": "48",
                "values": _curve(48, 4.0, 1.8),
                "min": "44",
                "max": "52",
                "trend": "flat",
                "sparse": False,
            },
        ],
    }


def _ha_lights() -> dict[str, Any]:
    return {
        "place": "Home",
        "time": "14:32",
        "total": 8,
        "on_count": 3,
        "lights": [
            {
                "entity_id": "light.living_room",
                "name": "Living Room",
                "on": True,
                "brightness_pct": 80,
            },
            {"entity_id": "light.kitchen", "name": "Kitchen", "on": True, "brightness_pct": 100},
            {"entity_id": "light.hallway", "name": "Hallway", "on": True, "brightness_pct": 35},
            {"entity_id": "light.bedroom", "name": "Bedroom", "on": False, "brightness_pct": 0},
            {"entity_id": "light.bathroom", "name": "Bathroom", "on": False, "brightness_pct": 0},
            {"entity_id": "light.office", "name": "Office", "on": False, "brightness_pct": 0},
            {"entity_id": "light.porch", "name": "Porch", "on": False, "brightness_pct": 0},
            {"entity_id": "light.garage", "name": "Garage", "on": False, "brightness_pct": 0},
        ],
    }


def _ha_locks() -> dict[str, Any]:
    return {
        "place": "Home",
        "time": "14:32",
        "summary": {"secured": 4, "unsecured": 1, "total": 5},
        "entries": [
            {"name": "Front Door", "kind": "lock", "state": "locked", "secured": True},
            {"name": "Back Door", "kind": "lock", "state": "locked", "secured": True},
            {"name": "Garage", "kind": "garage", "state": "closed", "secured": True},
            {"name": "Office Window", "kind": "window", "state": "open", "secured": False},
            {"name": "Patio Door", "kind": "door", "state": "closed", "secured": True},
        ],
    }


def _ha_media() -> dict[str, Any]:
    return {
        "entity_id": "media_player.living_room",
        "name": "Living Room",
        "state": "playing",
        "source": "Spotify",
        "title": "Light Years",
        "artist": "The National",
        "album": "Sleep Well Beast",
        "art_url": "",
        "media_duration": 248,
        "media_position": 92,
        "position_pct": 37,
        "show_progress": True,
        "volume_pct": 64,
    }


def _ha_sensor() -> dict[str, Any]:
    return {
        "title": "Sensors",
        "items": [
            {
                "name": "Outdoor Temp",
                "value": "16.4",
                "unit": "°C",
                "icon": "thermometer",
                "unavailable": False,
            },
            {
                "name": "Indoor Humidity",
                "value": "48",
                "unit": "%",
                "icon": "drop",
                "unavailable": False,
            },
            {"name": "CO₂", "value": "612", "unit": "ppm", "icon": "wind", "unavailable": False},
            {
                "name": "Living Room Lux",
                "value": "284",
                "unit": "lx",
                "icon": "sun",
                "unavailable": False,
            },
        ],
    }


def _ha_zones() -> dict[str, Any]:
    return {
        "label": "Family",
        "time": "14:32",
        "summary": {"home": 2, "away": 2, "total": 4},
        "items": [
            {
                "entity_id": "person.alex",
                "name": "Alex",
                "state": "home",
                "entity_picture": "",
                "last_changed": "2026-06-03T08:12:00+00:00",
                "history": ["home"] * 16 + ["not_home"] * 8,
            },
            {
                "entity_id": "person.jordan",
                "name": "Jordan",
                "state": "Work",
                "entity_picture": "",
                "last_changed": "2026-06-03T09:04:00+00:00",
                "history": ["home"] * 9 + ["Work"] * 15,
            },
            {
                "entity_id": "person.sam",
                "name": "Sam",
                "state": "home",
                "entity_picture": "",
                "last_changed": "2026-06-03T13:51:00+00:00",
                "history": ["School"] * 9 + ["home"] * 15,
            },
            {
                "entity_id": "person.casey",
                "name": "Casey",
                "state": "not_home",
                "entity_picture": "",
                "last_changed": "2026-06-03T11:22:00+00:00",
                "history": ["home"] * 6 + ["not_home"] * 18,
            },
        ],
    }


def _octoprint_status() -> dict[str, Any]:
    return {
        "label": "OctoPrint",
        "time": "14:32",
        "state": {"text": "Printing", "tone": "printing"},
        "job": {
            "name": "benchy.gcode",
            "completion": 47.3,
            "elapsed": 3600,
            "remaining": 5400,
            "eta": "15:32",
        },
        "temps": {
            "tool": {"actual": 208.4, "target": 210.0},
            "bed": {"actual": 60.1, "target": 60.0},
        },
    }


def _ha_todo() -> dict[str, Any]:
    # Mix of needs-action and completed items + a couple of due dates
    # so all four variants have something to render in the gallery.
    # Dates are seeded relative to a fixed near-term so OVERDUE / TODAY /
    # TOMORROW labels render in the sample even though the gallery
    # snapshot is taken at one moment in time.
    items: list[dict[str, Any]] = [
        {"uid": "1", "summary": "Milk", "status": "needs_action", "due": None, "description": ""},
        {
            "uid": "2",
            "summary": "Pay the electric bill",
            "status": "needs_action",
            "due": "2026-06-04",
            "description": "",
        },
        {
            "uid": "3",
            "summary": "Vacuum the rug",
            "status": "needs_action",
            "due": "2026-06-03",
            "description": "",
        },
        {
            "uid": "4",
            "summary": "Refill prescription",
            "status": "needs_action",
            "due": "2026-06-10",
            "description": "",
        },
        {
            "uid": "5",
            "summary": "Order birthday card",
            "status": "needs_action",
            "due": None,
            "description": "",
        },
        {
            "uid": "6",
            "summary": "Replace smoke-alarm battery",
            "status": "completed",
            "due": None,
            "description": "",
        },
    ]
    return {
        "title": "Shopping list",
        "entity_id": "todo.shopping_list",
        "items": items,
        "needs_action_count": sum(1 for it in items if it["status"] != "completed"),
        "completed_count": sum(1 for it in items if it["status"] == "completed"),
        "total_count": len(items),
    }


def _sky_moon() -> dict[str, Any]:
    return {
        "label": "Melbourne",
        "lat": -37.8,
        "phase_name": "Waning Gibbous",
        "age_days": 18.4,
        "fraction": 0.62,
        "illumination": 93.0,
        "waxing": False,
        "next_new": "2026-06-15T00:00:00+00:00",
        "next_first_quarter": "2026-06-22T00:00:00+00:00",
        "next_full": "2026-06-30T00:00:00+00:00",
        "next_last_quarter": "2026-06-07T00:00:00+00:00",
        "sunrise": "2026-06-03T07:27:00",
        "sunset": "2026-06-03T17:09:00",
        "moonrise": "2026-06-03T22:14:00",
        "moonset": "2026-06-03T10:48:00",
        "place": "Melbourne",
        "time": "09:41",
        "rise": "07:27",
        "set": "17:09",
        "riseMin": 7 * 60 + 27,
        "setMin": 17 * 60 + 9,
        "nowMin": 9 * 60 + 41,
        "dayLength": "9h 42m",
        "solarNoon": "12:18",
        "sun": {
            "rise": "07:27",
            "set": "17:09",
            "riseMin": 7 * 60 + 27,
            "setMin": 17 * 60 + 9,
            "nowMin": 9 * 60 + 41,
            "dayLength": "9h 42m",
            "solarNoon": "12:18",
        },
        "moon": {
            "phase": "Waning Gibbous",
            "illum": 93,
            "age": 18.4,
            "fraction": 0.62,
            "waxing": False,
            "rise": "22:14",
            "set": "10:48",
            "next": "Next last quarter · Sun 7 Jun",
        },
    }


def _todo() -> dict[str, Any]:
    return {
        "list_name": "Today",
        "items": [
            {"text": "Walk the dog", "completed_at": "2026-06-05T07:42:00"},
            {"text": "Review the v0.20 plan", "completed_at": None},
            {"text": "Order new MicroSD for the dev panel", "completed_at": None},
            {"text": "Reply to home insurance email", "completed_at": None},
            {"text": "Pick up dry-cleaning", "completed_at": "2026-06-05T11:18:00"},
        ],
    }


_GALLERY_SAMPLE_DATA_URL = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 400'>"
    "<defs><radialGradient id='g' cx='30%25' cy='35%25' r='75%25'>"
    "<stop offset='0%25' stop-color='%23f4dbb1'/>"
    "<stop offset='60%25' stop-color='%23c97a4a'/>"
    "<stop offset='100%25' stop-color='%231e1b2e'/></radialGradient></defs>"
    "<rect width='600' height='400' fill='url(%23g)'/>"
    "<polygon points='0,400 240,180 380,260 600,140 600,400' fill='%231e1b2e' opacity='0.85'/>"
    "<polygon points='0,400 160,290 320,330 480,260 600,310 600,400' fill='%23120f1f'/>"
    "<circle cx='160' cy='110' r='38' fill='%23f4dbb1' opacity='0.85'/>"
    "</svg>"
)


def _picture_gallery() -> dict[str, Any]:
    # Single inline SVG so the widget paints its hero image instead of
    # the "no images" placeholder. The widget reads from `data.url`
    # (the public URL of the chosen file) and an optional `filename`
    # for the overlay caption.
    return {
        "url": _GALLERY_SAMPLE_DATA_URL,
        "filename": "dusk-over-the-range.png",
    }


def _weather_pollen_count() -> dict[str, Any]:
    # Open-Meteo returns nulls outside Europe; the gallery is shot at
    # Melbourne which falls outside CAMS coverage, so the upstream
    # path lands with `breakdown[i].level == None` and the tiles
    # render em-dashes. This fixture gives all three categories a
    # plausible mid-spring reading so the visual treatment is
    # actually exercised.
    return {
        "place": "Melbourne",
        "label": "Melbourne",
        "level": "Moderate",
        "breakdown": [
            {"label": "Tree", "icon": "tree", "level": "Low", "value": 1.2},
            {"label": "Grass", "icon": "grass", "level": "High", "value": 28.4},
            {"label": "Weed", "icon": "weed", "level": "Moderate", "value": 9.1},
        ],
    }


def _glances_status() -> dict[str, Any]:
    """Healthy server snapshot, CPU + RAM + disk in the safe zone so
    the state pill paints `OK`. ``uptime`` is multi-day to exercise
    the ``Nd`` formatting branch in the client."""
    return {
        "label": "nas",
        "time": "14:32",
        "hostname": "nas",
        "cpu": 18.4,
        "mem": 42.7,
        "disk": {"percent": 63.2, "mnt_point": "/"},
        "load": 0.51,
        "uptime": 4 * 86400 + 3 * 3600 + 17 * 60,
        "state": {"text": "OK", "tone": "ok"},
    }


SAMPLES: dict[str, Any] = {
    "glances_status": _glances_status,
    "ha_battery": _ha_battery,
    "ha_camera": _ha_camera,
    "ha_climate": _ha_climate,
    "ha_energy": _ha_energy,
    "ha_entities": _ha_entities,
    "ha_history": _ha_history,
    "ha_lights": _ha_lights,
    "ha_locks": _ha_locks,
    "ha_media": _ha_media,
    "ha_sensor": _ha_sensor,
    "ha_todo": _ha_todo,
    "ha_zones": _ha_zones,
    "octoprint_status": _octoprint_status,
    "picture_gallery": _picture_gallery,
    "sky_moon": _sky_moon,
    "todo": _todo,
    "weather_pollen_count": _weather_pollen_count,
}


def get_sample(plugin_id: str) -> dict[str, Any] | None:
    """Return a deep-copied sample payload for ``plugin_id`` or None.

    Deep-copy so the cell hydration pipeline can mutate the dict
    (it sometimes adds ``_fetched_at``-style fields) without leaking
    between gallery rows."""
    builder = SAMPLES.get(plugin_id)
    if builder is None:
        return None
    payload: dict[str, Any] = copy.deepcopy(builder())
    return payload
