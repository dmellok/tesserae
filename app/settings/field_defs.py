"""Hardcoded "core" section field specs.

These three lists drive the App, Virtual-panel, and Broker cards on
Settings → Server. They use the same field-spec shape as plugin /
renderer ``settings`` so the template can render them through the same
auto-field path.

Kept in their own module (rather than the route handlers) because they
are also imported by tests and were referenced from the original
``app.settings_routes`` module path, the ``app.settings_routes`` shim
re-exports ``APP_FIELDS`` for that reason.
"""

from __future__ import annotations

import zoneinfo
from typing import Any

from app.panel import DEFAULT_PRESET, PANEL_PRESET_CHOICES

# Canonical Area/City zones only, plus a "system" sentinel and explicit UTC.
# ``zoneinfo.available_timezones()`` also returns legacy/compat buckets
# (Etc/* fixed offsets, SystemV/*, country aliases like US/* and Brazil/*,
# and single-word names like GMT or Japan) that just clutter a picker, we
# drop those and keep the modern ``Area/City`` form most pickers show.
# ``tzdata`` is a dependency so the list is complete regardless of host OS.
_LEGACY_TZ_PREFIXES = ("Etc/", "SystemV/", "US/", "Canada/", "Brazil/", "Mexico/", "Chile/")
_TZ_CHOICES: list[dict[str, str]] = [
    {"value": "system", "label": "System (host local time)"},
    {"value": "UTC", "label": "UTC"},
    *(
        {"value": tz, "label": tz}
        for tz in sorted(zoneinfo.available_timezones())
        if "/" in tz and not tz.startswith(_LEGACY_TZ_PREFIXES)
    ),
]


# Per-field ``group`` keys put each App-section field into one of the
# seven section cards on the redesigned Settings → Server page. The
# field-name / label / help text / disk layout are all unchanged; only
# the rendering order + visual grouping reads from ``group`` +
# ``APP_FIELD_GROUPS`` below. Legacy callers iterating APP_FIELDS keep
# working.
APP_FIELDS: list[dict[str, Any]] = [
    {
        "name": "default_transport",
        "type": "select",
        "label": "Default transport for new devices",
        "default": "rest",
        "group": "network",
        "choices": [
            {"value": "rest", "label": "REST API (recommended, no broker needed)"},
            {"value": "mqtt", "label": "MQTT (broker required, lower latency)"},
        ],
        "help": (
            "Which transport new device instances default to. REST means devices poll "
            "Tesserae over HTTP (no broker setup, simpler new-install path). MQTT means "
            "devices subscribe to a broker. You can pick the transport per device on "
            "Settings → Devices regardless of this default; this only seeds the choice "
            "in the onboarding wizard + the Pair vs Add-device entry points."
        ),
    },
    {
        "name": "public_url",
        "type": "string",
        "label": "Public URL",
        "default": "",
        "group": "network",
        "placeholder": "https://tesserae.example.org:8443",
        "help": (
            "Override the URL Tesserae uses when building external links "
            "(plugin OAuth callbacks, HA discovery image URLs, etc.). Set "
            "this when running behind a reverse proxy (NGINX Proxy Manager, "
            "Caddy, Cloudflare Tunnel) whose forwarded headers don't reach "
            "Flask cleanly. Leave blank to auto-detect from the request. "
            "Include scheme + host + port if non-standard, no trailing slash."
        ),
    },
    {
        "name": "timezone",
        "type": "select",
        "label": "Timezone",
        "default": "system",
        "group": "location",
        "choices": _TZ_CHOICES,
        "help": (
            "Used by the scheduler when interpreting daily fire times and "
            "time-of-day windows. 'System' uses the host's local time."
        ),
    },
    {
        # v0.69.6 (issue #52 item 5): app-level location picker replaces
        # the flat lat/lon pair below. Stores the same
        # ``{latitude, longitude, name}`` dict that per-cell widget
        # locations use, so the composer's ``_resolved_options`` can
        # fall back to it when a cell has no location of its own.
        # The flat ``latitude`` / ``longitude`` fields below are kept
        # so an on-disk value from before v0.69.6 still round-trips
        # through the settings page and can be re-entered here in the
        # new picker; a small on-read seed in the composer promotes the
        # legacy pair into a ``{latitude, longitude, name}`` dict if
        # ``location`` isn't set, so upgrades don't silently blank
        # anyone's weather.
        "name": "location",
        "type": "location_search",
        "label": "Default location",
        "default": "",
        "group": "location",
        "help": (
            "Search a city or pick your coordinates. Weather, sky, and any "
            "other location-aware widget will use this when its own cell "
            "hasn't picked a location of its own, so you can set your city "
            "once and reuse it across the whole dashboard."
        ),
    },
    {
        # Legacy flat-lat/lon fields, kept so a pre-v0.69.6 settings.json
        # still parses. Not surfaced on the settings page any more (the
        # picker above owns the UX); a data migration in
        # ``_resolved_options`` promotes them into ``location`` on read
        # so weather widgets keep working through an upgrade. Once the
        # migration has run for every install we can drop them; leaving
        # them declared for now so ``settings.json`` doesn't grow an
        # unknown-key warning on load.
        "name": "latitude",
        "type": "number",
        "label": "Latitude",
        "default": "",
        "group": "location",
        "hidden": True,
        "step": "any",
    },
    {
        "name": "longitude",
        "type": "number",
        "label": "Longitude",
        "default": "",
        "group": "location",
        "hidden": True,
        "step": "any",
    },
    {
        "name": "ha_discovery_enabled",
        "type": "switch",
        "label": "Home Assistant MQTT discovery",
        "default": False,
        "group": "network",
        "help": (
            "Publish HA autodiscovery configs so a button per saved dashboard, "
            "an image entity for the most-recent render, and diagnostic sensors "
            "appear under a 'Tesserae' device in HA. Default off."
        ),
    },
    {
        "name": "mdns_enabled",
        "type": "switch",
        "label": "Advertise tesserae.local over mDNS",
        "default": False,
        "group": "network",
        "help": (
            "Announce this server as tesserae.local (and an _http._tcp service) "
            "over mDNS / Bonjour so you can reach it by name without changing "
            "the host machine's hostname. Default off."
        ),
    },
    {
        "name": "quiet_hours_enabled",
        "type": "switch",
        "label": "Quiet hours",
        "default": False,
        "group": "quiet_hours",
        "group_role": "master",
        "help": (
            "Suppress automated pushes (scheduler firings, webhook calls) during "
            "a daily time window, typical use is to stop the panel waking the room "
            "overnight. Manual pushes from the Send page or Push-now buttons still go "
            "through; quiet hours filter automation, not deliberate user intent. "
            "Each device can override the window in Settings → Devices."
        ),
    },
    {
        "name": "quiet_hours_start",
        "type": "time",
        "label": "Quiet hours start",
        "default": "22:00",
        "group": "quiet_hours",
        "group_role": "dependent",
        "help": "When the daily quiet window begins. Honours Settings → Server → App → Timezone.",
    },
    {
        "name": "quiet_hours_end",
        "type": "time",
        "label": "Quiet hours end",
        "default": "07:00",
        "group": "quiet_hours",
        "group_role": "dependent",
        "help": (
            "When the daily quiet window ends. If end < start, the window wraps "
            "across midnight (e.g. 22:00 → 07:00 = overnight)."
        ),
    },
    {
        "name": "low_battery_overlay",
        "type": "switch",
        "label": "Low-battery overlay on device pushes",
        "default": True,
        "group": "low_battery",
        "group_role": "master",
        "help": (
            "When a device with a battery (TRMNL, ESP32) reports a charge "
            "below the threshold, paint a small low-battery chip in the "
            "top-right corner of the composition before it's dithered and "
            "sent. Devices without a battery reading (Pi, virtual panels) "
            "are ignored. The chip has a solid white background + dark "
            "border so it stays readable on any dashboard content."
        ),
    },
    {
        "name": "low_battery_threshold",
        "type": "slider",
        "label": "Low-battery threshold",
        "default": 15,
        "min": 5,
        "max": 50,
        "step": 1,
        "unit": "%",
        "group": "low_battery",
        "group_role": "dependent",
        "help": (
            "Battery percent at or below which the overlay paints. Default "
            "15%, raise if you want earlier warning, lower if 15% already "
            "fires too often for your setup."
        ),
    },
    {
        "name": "mobile_zoom_lock",
        "type": "switch",
        "label": "Lock mobile zoom",
        "default": True,
        "group": "display",
        "help": (
            "Prevents pinch-to-zoom and double-tap-to-zoom on the admin UI "
            "when accessed from a phone. iOS Safari ignores the standard "
            "viewport `user-scalable=no`, so this also installs a JS "
            "gesture blocker that catches Safari-specific gesturestart "
            "events. Turn off if you rely on browser zoom for "
            "accessibility, the page already scales fluidly with CSS, but "
            "browser zoom adds magnification beyond that."
        ),
    },
    {
        "name": "keep_browser_warm",
        "type": "switch",
        "label": "Keep the renderer browser warm",
        "default": True,
        "group": "display",
        "help": (
            "Holds a single Chromium process resident between renders, so each "
            "push reuses it rather than launching cold. Cuts per-push render time "
            "from ~1–2 s to ~200 ms, noticeable on schedule fires and the editor. "
            "Costs ~150 MB of idle RAM; turn off if you're on a constrained host "
            "(1 GB Pi, tight VM). Each render still runs in a fresh browser context "
            "so cookies / localStorage never leak between dashboards."
        ),
    },
    {
        "name": "marketplace_index_url",
        "type": "string",
        "label": "Marketplace catalog URL",
        "default": ("https://raw.githubusercontent.com/dmellok/tesserae-widgets/main/widgets.json"),
        "group": "marketplace",
        "help": (
            "Where Settings → Plugins → Browse pulls the community widget "
            "catalog from. Defaults to the official catalog (audit-only, "
            "every entry PR-reviewed). Point at a fork to use your own "
            "catalog; leave blank to hide the Browse page entirely."
        ),
    },
]


# Server tab section cards. Order here is the render order; each entry
# names a card. ``master`` is the field name whose switch lives in the
# section header and dims the rest of the card when off (Quiet hours +
# Low-battery use this); None for cards with no master toggle.
# ``meta`` is a small read-only chip pinned to the section header
# (currently used by the Network card for ``NETWORK IP``).
APP_FIELD_GROUPS: list[dict[str, Any]] = [
    {
        "id": "network",
        "title": "Network & integrations",
        "description": "How devices reach this server and which integrations are advertised.",
        "icon": "globe",
        "master": None,
        "meta_label": "NETWORK IP",
    },
    {
        "id": "location",
        "title": "Location & time",
        "description": "Default coordinates for weather widgets and the scheduler's timezone.",
        "icon": "compass",
        "master": None,
    },
    {
        "id": "quiet_hours",
        "title": "Quiet hours",
        "description": "Pause automated pushes during a nightly window. Devices can override.",
        "icon": "moon",
        "master": "quiet_hours_enabled",
    },
    {
        "id": "low_battery",
        "title": "Low-battery warnings",
        "description": "Overlay a warning badge on pushes to battery-powered devices running low.",
        "icon": "battery-warning",
        "master": "low_battery_overlay",
    },
    {
        "id": "display",
        "title": "Display & performance",
        "description": "Tune how the admin UI and renderer behave.",
        "icon": "gauge",
        "master": None,
    },
    {
        "id": "marketplace",
        "title": "Widget marketplace",
        "description": "Where the Browse page pulls community widget metadata from.",
        "icon": "storefront",
        "master": None,
    },
]

PANEL_FIELDS: list[dict[str, Any]] = [
    {
        "name": "panel_preset",
        "type": "select",
        "label": "Panel size",
        "default": DEFAULT_PRESET,
        "choices": PANEL_PRESET_CHOICES,
        "help": "Common Inky / Waveshare panels. Pick Custom to set width + height manually.",
    },
    {
        "name": "panel_orientation",
        "type": "switch",
        "label": "Portrait orientation",
        "default": False,
        "help": "Swap the panel width + height. Default off renders the panel landscape-native.",
    },
    {
        "name": "panel_w",
        "type": "slider",
        "label": "Panel width (px)",
        "default": 1600,
        "min": 100,
        "max": 3000,
        "step": 1,
        "unit": "px",
        "help": "Only used when Panel size is Custom.",
    },
    {
        "name": "panel_h",
        "type": "slider",
        "label": "Panel height (px)",
        "default": 1200,
        "min": 100,
        "max": 3000,
        "step": 1,
        "unit": "px",
    },
]

# Field order matters: the settings template groups the external-broker
# fields (host/port/username/password) and the built-in-broker fields
# (embedded_*) into two contiguous blocks it can show/hide. The
# ``embedded_enabled`` switch leads the card; flipping it hides whichever
# block is irrelevant. ``keepalive``/``client_id`` configure the client
# connection either way, so they stay visible at the bottom.
BROKER_FIELDS: list[dict[str, Any]] = [
    {
        "name": "embedded_enabled",
        "type": "switch",
        "label": "Built-in broker",
        "default": False,
        "help": (
            "Run an in-process MQTT broker (amqtt). Convenient when you "
            "don't have a Mosquitto host handy; leave off to point Tesserae "
            "at an external broker instead. "
            "Heads-up: amqtt only speaks MQTT v3.1.1. Tesserae's own Pi / "
            "ESP32 clients are fine (paho-mqtt defaults to 3.1.1), but if "
            "you connect with MQTT Explorer / MQTTX / Home Assistant / "
            "Node-RED you'll need to set their protocol version to 3.1.1, "
            'v5 clients get rejected with "Invalid protocol". Need full '
            "v5 support? Install Mosquitto (apt/brew) and point Tesserae "
            "at it via the Host / Port fields below."
        ),
    },
    {"name": "host", "type": "string", "label": "Host", "default": ""},
    {
        "name": "port",
        "type": "number",
        "label": "Port",
        "default": 1883,
        "min": 1,
        "max": 65535,
    },
    {"name": "username", "type": "string", "label": "Username", "default": ""},
    {"name": "password", "type": "string", "label": "Password", "default": "", "secret": True},
    {
        "name": "embedded_port",
        "type": "number",
        "label": "Built-in broker port",
        "default": 1883,
        "min": 1024,
        "max": 65535,
        "step": 1,
        "help": "Port the built-in broker listens on. Tesserae's transport auto-connects here when host is empty.",
    },
    {
        "name": "embedded_bind",
        "type": "string",
        "label": "Built-in broker bind address",
        "default": "127.0.0.1",
        "help": (
            "127.0.0.1 keeps the broker loopback-only (only this host can "
            "reach it). Set to 0.0.0.0 to accept connections from any LAN "
            "client, set a username + password below if you do."
        ),
    },
    {
        "name": "embedded_username",
        "type": "string",
        "label": "Built-in broker username",
        "default": "",
        "help": (
            "Optional. When set with a password, anonymous logins are "
            "rejected. Leave both blank for an open broker."
        ),
    },
    {
        "name": "embedded_password",
        "type": "string",
        "label": "Built-in broker password",
        "default": "",
        "secret": True,
        "help": "Stored on disk in a hashed password file the broker reads on start.",
    },
    {
        "name": "keepalive",
        "type": "slider",
        "label": "Keepalive (seconds)",
        "default": 60,
        "min": 10,
        "max": 600,
        "step": 5,
        "unit": "s",
    },
    {
        "name": "client_id",
        "type": "string",
        "label": "MQTT client id",
        "default": "",
        "help": (
            "Must be unique per instance, a broker evicts a duplicate client "
            "id the moment another connects with it, which causes an endless "
            "reconnect loop. Leave blank to auto-use 'tesserae-<hostname>'; the "
            "--dev server appends '-dev'."
        ),
    },
]
