"""Settings index: ``GET /settings`` + ``GET /settings/<area>``.

The big ``_build_sections`` walker lives here too, it composes one
section dict per editable card across plugins, renderers, devices, and
the hardcoded core (App / Panel / Broker). The template walks the same
shape regardless of source. Helpers below (``_values_for_core``,
``_broker_mqtt_url``, ``_status_view``) exist solely to support that.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from flask import current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.wrappers import Response

from app import backup as _backup_mod
from app import device_timetable, palette_profiles, test_patterns
from app import firmware_check as firmware_check_module
from app import install_id as install_id_module
from app import updater as _updater_mod
from app.button_actions import DEFAULT_BUTTON_MAP, registered_actions
from app.device_loader import Device
from app.ha_options import is_ha_addon
from app.network import detect_local_ip, docker_bridge_ip_warning, is_docker_bridge_ip
from app.panel import PANEL_PRESET_CHOICES, PANEL_PRESETS
from app.state.settings_store import SECRET_MASK

from ._shared import (
    AREA_KINDS,
    AREAS,
    STATUS_FRESH_S,
    STATUS_WARN_S,
    bp,
    config_fields_from_schema,
    device_kinds,
    device_status,
    devices,
    discovery_cache,
    format_discovered,
    format_relative,
    plugins,
    render_for_admin,
    renderers,
    settings_store,
)
from .field_defs import APP_FIELD_GROUPS, APP_FIELDS, BROKER_FIELDS, PANEL_FIELDS


def _pending_pairings() -> list[dict[str, Any]]:
    """Snapshot of currently-valid REST pairing codes for the Devices
    tab's Pair card. The store lives in app.config under
    ``PAIRING_STORE`` and is installed by app_factory. Defensive
    against the store not being installed (test paths without REST
    wiring) so the existing Settings page keeps rendering."""
    store = current_app.config.get("PAIRING_STORE")
    if store is None:
        return []
    return [
        {
            "code": p.code,
            "issued_at": p.issued_at,
            "expires_at": p.expires_at,
            "note": p.note,
            "seconds_left": max(0, int(p.expires_at - time.time())),
        }
        for p in store.list_pending()
    ]


@bp.get("/settings")
def settings() -> Response:
    """Land on the Server sub-page by default."""
    return redirect(url_for("auth.settings_area", area="server"))


@bp.get("/settings/<area>", endpoint="settings_area")
def settings_area(area: str) -> str | Response:
    """Render one sub-page of /settings, scoped to a single area
    (server / renderers / devices / plugins)."""
    if area not in AREA_KINDS:
        return Response(f"unknown settings area {area!r}", status=404)
    sections = [s for s in _build_sections() if s["kind"] in AREA_KINDS[area]]
    # Server tab: replace the single "App" card with the seven grouped
    # section cards (Network / Location / Quiet hours / Low-battery /
    # Display / Marketplace / Privacy). The Broker + Virtual-panel
    # cards stay where they are. ``app_field_groups`` is None on
    # other tabs so the template's conditional skips it.
    app_field_groups: list[dict[str, Any]] | None = None
    if area == "server":
        app_field_groups = _build_app_field_groups()
        sections = [s for s in sections if s["kind"] != "app"]
    # Devices area needs the kinds list (for the Add-device form) so the
    # template doesn't have to dig into the registry directly.
    device_kinds_list = (
        [{"id": d.id, "name": d.name, "panel": d.panel} for d in device_kinds()]
        if area == "devices"
        else []
    )
    discovered = (
        [d for d in format_discovered(discovery_cache().all()) if d["id"] not in devices().devices]
        if area == "devices"
        else []
    )
    # Issue #17: the Discovered strip splits by transport so the
    # structural distinction (REST auto-claim by MAC vs MQTT
    # publishes-on-topic) is visible at a glance, not buried in a
    # single green pill. ``parsed.transport == "rest"`` flags
    # auto-claim rows; everything else (no parsed.transport, or
    # an explicit ``"mqtt"``) lands in the MQTT group.
    discovered_rest = [d for d in discovered if d.get("parsed", {}).get("transport") == "rest"]
    discovered_mqtt = [d for d in discovered if d.get("parsed", {}).get("transport") != "rest"]
    # Signature of what we're rendering, so the client-side poller knows
    # the baseline and can auto-refresh when the discovered set changes.
    discovered_sig = ",".join(sorted(f"{d['id']}:{d.get('kind') or ''}" for d in discovered))

    # System page payload: current update state + cached check + recent
    # history + on-disk backups. The check is NOT auto-refreshed (it hits
    # the network); the user clicks "Check for updates" to refresh.
    system_state = None
    system_last_check = None
    system_history: list[Any] = []
    system_backups: list[Any] = []
    system_webhook_token_set = False
    # One-shot reveal after /settings/system/webhook/regenerate, pop
    # so a refresh doesn't re-show the token. Only honoured on the
    # System tab, which is the only place the modal renders.
    system_webhook_reveal_token = (
        session.pop("_webhook_token_reveal", "") if area == "system" else ""
    )
    # MCP API card: whether the experiment is on, whether a token is set, and a
    # one-shot token reveal (same pattern as the webhook token above).
    from app import experiments as _experiments

    system_mcp_enabled = _experiments.is_enabled("mcp")
    system_mcp_token_set = False
    system_mcp_reveal_token = session.pop("_mcp_token_reveal", "") if area == "system" else ""
    # Experiments card: one row per catalogued flag, with the resolved state
    # and whether an env var pins it (row renders read-only then).
    # ``needs_online`` marks a row whose feature is hosted on api.tesserae.ink
    # and is therefore inert while the master online switch is off. Enabling
    # such a flag used to change nothing with no explanation anywhere (#224).
    _online_on = _firmware_check_enabled()
    system_experiments = [
        {
            **entry,
            "enabled": _experiments.is_enabled(entry["name"]),
            "env_forced": _experiments.env_override(entry["name"]) is not None,
            "needs_online": entry["name"] in _experiments.REQUIRES_ONLINE and not _online_on,
        }
        for entry in _experiments.CATALOG
    ]
    # Same one-shot pattern for TRMNL access tokens after devices_add
    # creates a trmnl_client instance. Only honoured on the Devices
    # tab, that's where the modal lives and where the user is when
    # the redirect lands.
    trmnl_token_reveal: dict[str, Any] | None = (
        session.pop("_trmnl_token_reveal", None) if area == "devices" else None
    )
    in_container = bool(os.environ.get("TESSERAE_IN_DOCKER"))
    system_release_check = None
    system_git_available = True
    if area == "system":
        upd = current_app.config["UPDATER"]
        # Three install shapes:
        #  - Docker (``TESSERAE_IN_DOCKER`` set): no .git in the image,
        #    use release API.
        #  - Bare non-git (pip wheel, release tarball, etc.): also no
        #    .git, same release-API view. Without this branch the page
        #    flashed "Update state unavailable: not a git repository"
        #    on every load.
        #  - Git checkout: full in-app updater (check / apply / rollback).
        system_git_available = upd.has_git_repo()
        if in_container or not system_git_available:
            # Skip the live API call under ``app.testing`` so tests
            # don't depend on network reachability, the rest of the
            # branch (the docker / bare-install hint) still renders.
            if not current_app.testing:
                system_release_check = upd.latest_release_via_api(
                    current_app.config.get("APP_VERSION", "0.0.0")
                )
        else:
            try:
                system_state = upd.current_state()
            except _updater_mod.UpdaterError as err:
                flash(f"Update state unavailable: {err}", "error")
            system_last_check = upd.last_check
            system_history = list(reversed(upd.history()))
        system_backups = _backup_mod.list_all(current_app.config["DATA_ROOT"])
        # Surface only whether a webhook token is set, never the value
        # itself, so a screenshot of Settings → System doesn't leak it.
        # The disk key is ``webhook_token_secret`` (``_secret`` suffix
        # is the convention for masked fields); ``get_section`` returns
        # raw on-disk keys so we look up the suffixed form here.
        _app_raw = settings_store().get_section("app")
        system_webhook_token_set = bool(
            (_app_raw.get("webhook_token_secret") or _app_raw.get("webhook_token") or "").strip()
        )
        system_mcp_token_set = bool((_app_raw.get("mcp_token_secret") or "").strip())

    # Auth state for the System → Authentication card. Cheap to compute,
    # so we read it for any tab, the template only uses it on System.
    from app import auth as _auth

    system_password_set = _auth.password_is_set(settings_store())
    system_password_required = _auth.password_required(settings_store())

    # Issue #16 unified Add-device card needs the default transport
    # (REST or MQTT) + a "broker_configured" flag so the MQTT branch
    # can show a "set up the broker first" warning band when no
    # external host is set. ``broker.host`` is the canonical signal
    # for the warning (per the design handoff); the built-in
    # embedded broker doesn't satisfy it, so the user has to point
    # at a configured external host before adding MQTT devices.
    if area == "devices":
        _app_raw = settings_store().get_section("app")
        _broker_raw = settings_store().get_section("broker")
        default_transport = (_app_raw.get("default_transport") or "rest").strip().lower()
        if default_transport not in ("rest", "mqtt"):
            default_transport = "rest"
        broker_configured = bool((_broker_raw.get("host") or "").strip())
        kind_default_rows = _build_kind_default_rows()
    else:
        default_transport = "rest"
        broker_configured = False
        kind_default_rows = []

    return render_template(
        "settings.html",
        sections=sections,
        app_field_groups=app_field_groups,
        active_area=area,
        areas=AREAS,
        device_kinds=device_kinds_list,
        panel_preset_choices=PANEL_PRESET_CHOICES if area == "devices" else [],
        panel_presets=PANEL_PRESETS if area == "devices" else {},
        discovered_devices=discovered,
        discovered_rest=discovered_rest,
        discovered_mqtt=discovered_mqtt,
        discovered_sig=discovered_sig,
        default_transport=default_transport,
        broker_configured=broker_configured,
        # Drives the OpenDisplay transport branch: HA add-on gets the
        # push (opendisplay_ha) form, otherwise the bridge instructions.
        in_ha=is_ha_addon(),
        kind_default_rows=kind_default_rows,
        # When set (via ?calibrating=<id>), the matching device card shows
        # the "which number is in the top-left?" answer form.
        calibrating=request.args.get("calibrating") or "",
        system_state=system_state,
        system_last_check=system_last_check,
        system_history=system_history,
        system_backups=system_backups,
        system_dev_mode=bool(current_app.debug),
        # The official Docker image sets TESSERAE_IN_DOCKER=1 so the
        # Settings → System tab can hide the in-app self-update card
        # (a layered filesystem would lose changes on the next image
        # rebuild) and show a "docker pull" hint instead.
        system_in_container=in_container,
        system_git_available=system_git_available,
        system_release_check=system_release_check,
        system_webhook_token_set=system_webhook_token_set,
        system_webhook_reveal_token=system_webhook_reveal_token,
        system_mcp_enabled=system_mcp_enabled,
        system_mcp_token_set=system_mcp_token_set,
        system_mcp_reveal_token=system_mcp_reveal_token,
        system_experiments=system_experiments,
        system_password_set=system_password_set,
        system_password_required=system_password_required,
        # v0.70.0: install-identifier metadata for the Settings -> System
        # "Install identifier" section (regeneration button + display).
        install_id_meta=(
            install_id_module.read_metadata(Path(current_app.config["DATA_ROOT"]))
            if area == "system"
            else None
        ),
        # Master online-features switch state (on by default), so the System
        # tab can render the "Online features" switch. Governs every outbound
        # api.tesserae.ink call: update checks + the marketplace install count.
        online_features_enabled=(_firmware_check_enabled() if area == "system" else True),
        trmnl_token_reveal=trmnl_token_reveal,
        # Pending REST pairing codes for the Devices area's Pair card.
        # Only computed on the devices tab; the template doesn't reference
        # it elsewhere. Empty list when no codes are live OR when the
        # store isn't wired (the testing harness doesn't always install
        # one).
        pairing_codes=_pending_pairings() if area == "devices" else [],
        pairing_reveal=session.pop("_rest_pairing_reveal", None),
        # About tab: version. Community + sponsor links come from the
        # app-wide context processor (community_*_url) since the footer and
        # the onboarding wrap-up step share the same set. ``APP_VERSION`` is
        # the version app_factory resolved at boot (from importlib.metadata
        # or the git tag fallback); reusing it keeps a single source of truth
        # across the About card and other surfaces.
        about_app_version=str(current_app.config.get("APP_VERSION") or "unknown"),
    )


# -- internals: section building ---------------------------------------


_SLEEP_INTERVAL_CHOICES: list[dict[str, Any]] = [
    {"value": 60, "label": "1 minute"},
    {"value": 300, "label": "5 minutes"},
    {"value": 900, "label": "15 minutes"},
    {"value": 1800, "label": "30 minutes"},
    {"value": 3600, "label": "1 hour"},
]


def _build_kind_default_rows() -> list[dict[str, Any]]:
    """Build the per-kind defaults rows for the "Built-in device
    kinds" card on the Devices tab (issue #22).

    One entry per built-in kind (``device.kind_of is None``), in the
    same order the Add-device "Kind" dropdown surfaces them.
    ``has_override`` drives the MODIFIED badge; the rest of the dict
    is what the inline form needs to render preselected values.
    Plugin-defined kinds get a ``plugin_source`` annotation; that
    branch is deliberately empty for now (out of scope per the
    handoff) but the column is reserved so the template doesn't
    need to grow when it lands."""
    from app.state.kind_overrides import KindOverridesStore

    store = KindOverridesStore(current_app.config["DEVICE_DATA_ROOT"])
    rows: list[dict[str, Any]] = []
    for d in device_kinds():
        panel = d.panel or {}
        rows.append(
            {
                "id": d.id,
                "name": d.name,
                "icon": d.icon or "cpu",
                "current": {
                    "display_name": str(d.manifest.get("display_name_default") or d.name or ""),
                    "panel_preset": str(panel.get("preset") or "custom"),
                    "panel_w": int(panel.get("w") or 0),
                    "panel_h": int(panel.get("h") or 0),
                    "panel_orientation": str(panel.get("orientation") or "landscape"),
                    "sleep_interval_s": int(d.manifest.get("sleep_interval_s_default") or 300),
                },
                "has_override": store.has_override(d.id),
                "plugin_source": None,
                "save_endpoint": url_for("auth.devices_kind_defaults_save", kind_id=d.id),
                "reset_endpoint": url_for("auth.devices_kind_defaults_reset", kind_id=d.id),
            }
        )
    return rows


def _build_app_field_groups() -> list[dict[str, Any]]:
    """Compose the seven grouped section cards for the redesigned
    Settings → Server page. Iterates ``APP_FIELD_GROUPS`` for the
    visual order + master-toggle wiring, then fans out the matching
    ``APP_FIELDS`` entries by their ``group`` key. Each group dict
    carries everything the template needs:

    * ``title`` / ``description`` / ``icon`` – the section header.
    * ``master_field`` (or None) – the field with ``group_role:
      "master"`` whose switch lives in the header row.
    * ``master_state`` – the current value of the master, so dependent
      controls below can render dimmed on the first paint.
    * ``fields`` – dependent + plain fields, in declared order.
    * ``meta_label`` / ``meta_value`` – read-only chip pinned to the
      header (currently Network gets the NETWORK IP).

    The shape mirrors a regular section dict (``id`` / ``state`` /
    ``endpoint``) so the template can render the group with the same
    form-post path the legacy App card uses; the save handler still
    receives a flat ``{name: value}`` POST.
    """
    store = settings_store()
    app_raw = store.get_section("app")
    state = _values_for_core("app", APP_FIELDS, app_raw)
    endpoint = url_for("auth.settings_update", section_kind="app")
    network_ip = detect_local_ip()

    groups: list[dict[str, Any]] = []
    for spec in APP_FIELD_GROUPS:
        group_fields = [f for f in APP_FIELDS if f.get("group") == spec["id"]]
        master_field = next(
            (f for f in group_fields if f.get("group_role") == "master"),
            None,
        )
        # Dependent + plain fields render under the section body. The
        # master keeps its place in the header so it isn't duplicated.
        # ``hidden: True`` marks legacy fields that still load off disk
        # but no longer render in the UI (v0.69.6 retired the flat
        # ``latitude`` / ``longitude`` pair on the location group in
        # favour of the ``location_search`` picker; keeping them in
        # ``APP_FIELDS`` avoids "unknown key" grumbles from a pre-v0.69.6
        # settings.json).
        body_fields = [
            f for f in group_fields if f.get("group_role") != "master" and not f.get("hidden")
        ]
        master_state = state.get(master_field["name"]) if master_field else None
        meta_value = None
        if spec.get("meta_label") == "NETWORK IP":
            meta_value = network_ip
        groups.append(
            {
                "id": spec["id"],
                "title": spec["title"],
                "description": spec["description"],
                "icon": spec["icon"],
                "master_field": master_field,
                "master_state": master_state,
                "fields": body_fields,
                "state": state,
                "endpoint": endpoint,
                "meta_label": spec.get("meta_label"),
                "meta_value": meta_value,
            }
        )
    return groups


_TEST_PATTERN_GAMUT_LABELS: dict[str, tuple[str, ...]] = {
    "waveshare_e6": ("black", "white", "yellow", "red", "blue", "green"),
    # BWRY has no blue or green ink. Without this entry the lookup below
    # falls back to the E6 row and the solid-fill picker offers a PicPak
    # two colours it physically cannot paint.
    "bwry_4": ("black", "white", "yellow", "red"),
    # Grayscale panels have levels, not inks. Without these the lookup
    # falls back to the E6 row and offers a grey panel six colours it
    # cannot paint.
    "gray_4": ("black", "dark grey", "light grey", "white"),
    "gray_16": tuple(f"level {i}" for i in range(16)),
    "inky_7colour": (
        "black",
        "white",
        "green",
        "blue",
        "red",
        "yellow",
        "orange",
    ),
}

_TEST_PATTERN_GAMUT_HEXES: dict[str, tuple[str, ...]] = {
    "waveshare_e6": (
        "#000000",
        "#ffffff",
        "#ffff00",
        "#ff0000",
        "#0000ff",
        "#00ff00",
    ),
    # Nominal (not measured) primaries: this picker drives a solid-fill
    # test pattern, whose point is to send the panel a pure palette index
    # and see what the ink actually does. Feeding it measured values
    # would beg the question.
    "bwry_4": ("#000000", "#ffffff", "#ffff00", "#ff0000"),
    # Nominal ramps for the same reason as BWRY above: the solid-fill
    # pattern's job is to send one pure level and see what the panel
    # actually prints, which is exactly the measurement a calibrated
    # ramp is derived from. Feeding it the calibrated values would beg
    # the question.
    "gray_4": ("#000000", "#555555", "#aaaaaa", "#ffffff"),
    "gray_16": tuple(f"#{i * 17:02x}{i * 17:02x}{i * 17:02x}" for i in range(16)),
    "inky_7colour": (
        "#000000",
        "#ffffff",
        "#00ff00",
        "#0000ff",
        "#ff0000",
        "#ffff00",
        "#ff8c00",
    ),
}


def _orphan_state_counts_for(device: Device) -> dict[str, Any]:
    """Peek at how much per-device state a delete-with-wipe would touch.
    Returns counts for the modal's checkbox row; the template hides
    the row when ``total`` is 0. Cheap to compute; the page store +
    event log queries are indexed."""
    from pathlib import Path

    from app import device_cleanup as _cleanup

    summary = _cleanup.list_orphan_state(
        device_id=device.id,
        page_store=current_app.config["PAGE_STORE"],
        event_log=current_app.config["EVENT_LOG"],
        settings_store=settings_store(),
        data_root=Path(current_app.config["DATA_ROOT"]),
        devices=current_app.config.get("DEVICE_REGISTRY"),
    )
    return {
        "page_count": len(summary.page_ids),
        "event_count": summary.event_count,
        "settings_count": summary.setting_keys_devices + summary.setting_keys_renderers,
        "has_calibration_image": summary.has_calibration_image,
        # Pages kept for a live co-owner, with this device dropped from their
        # binding. Counted separately so the modal doesn't imply they're deleted.
        "unbound_page_count": len(summary.unbound_page_ids),
        # History rows kept because they also went to another device, with this
        # one dropped from their delivery chips. Counted separately for the same
        # reason: they aren't being deleted (#229).
        "relabelled_event_count": summary.relabelled_event_count,
        "total": summary.total,
    }


def _has_custom_image(instance_id: str) -> bool:
    """True when the device has a user-uploaded calibration image on
    disk. Used to conditionally surface the "Your uploaded image"
    pattern in the picker + the delete button."""
    from pathlib import Path

    path = Path(current_app.config["DATA_ROOT"]) / "calibration_images" / f"{instance_id}.png"
    return path.exists()


def _palette_profile_slug_for(device: Device) -> str:
    """Read the device's active palette profile slug from the settings
    store.

    Self-heals for panels whose gamut has a matching profile family
    (spectra6 / inky_7colour / bwry_4): when the stored slug is empty OR
    points at a profile that no longer resolves, backfill the family's
    default bundled slug and persist it. This keeps the Calibration
    tab's tone / palette section rendered across saves that would
    otherwise leave the slug in a broken state (issue #52 follow-up,
    the "section vanishes after saving a slider" symptom).

    Devices whose gamut has no matching family (mono, rgb24, rgb16)
    still return "" so the section stays hidden as intended. ``bwry_4``
    joined the families with profiles in v0.282.0, and self-heals to its
    NOMINAL preset, which renders identically to no profile at all, so
    unlocking the tab doesn't restyle a PicPak that already looks right.
    """
    raw = settings_store().get_for_runtime(
        "devices",
        device.id,
        [{"name": "palette_profile_slug", "type": "string", "default": ""}],
    )
    slug = str(raw.get("palette_profile_slug") or "").strip()

    family = _palette_family_for(device)
    if not family:
        return slug

    if slug and _resolves_to_profile(slug):
        return slug

    from app.palette_profiles.bundled import default_slug_for

    fallback = default_slug_for(family)
    if slug != fallback:
        settings_store().update_for_namespace(
            "devices",
            device.id,
            {"palette_profile_slug": fallback},
            [{"name": "palette_profile_slug", "type": "string", "default": ""}],
        )
    return fallback


def _resolves_to_profile(slug: str) -> bool:
    """True when a slug resolves to a bundled or on-disk user profile.
    Used by the slug self-heal to detect stale slugs that would otherwise
    hide the tone editor."""
    if palette_profiles.bundled_profile(slug) is not None:
        return True
    from pathlib import Path

    store = palette_profiles.PaletteProfileStore(Path(current_app.config["DATA_ROOT"]))
    return store.load(slug) is not None


def _palette_family_for(device: Device) -> str:
    """Which palette-profile family a device's picker filters against.

    Maps panel gamut to the bundled profile family: ``waveshare_e6`` /
    ``spectra_6`` → ``spectra6``, ``inky_7colour`` / ``acep_7colour``
    → ``inky_7colour``, ``bwry_4`` → ``bwry_4``. Panels without a
    matching profile family (mono, ``rgb24``, ``rgb16``, unknown gamut)
    return empty so the section builder can null out every palette
    endpoint for them; the whole Calibration-tab palette + tone editor
    then hides on the template side.

    Empty gamut is treated as ``spectra6`` (legacy default for the
    fleet majority) so devices that haven't declared a gamut yet
    still see the picker.
    """
    panel = device.panel or {}
    gamut = str(panel.get("gamut") or "").strip()
    if gamut in ("inky_7colour", "acep_7colour"):
        return "inky_7colour"
    if gamut in ("waveshare_e6", "spectra_6", ""):
        return "spectra6"
    if gamut == "bwry_4":
        return "bwry_4"
    # Grayscale ramps. These profiles carry no ``palette`` at all, only
    # a ``gray`` ramp of what the panel's levels actually print, which
    # is what the grey packers quantise against.
    if gamut in ("gray_4", "gray_16"):
        return gamut
    # mono, rgb24, rgb16, unknown: no bundled profile family applies,
    # hide the picker rather than showing incompatible Spectra 6
    # profiles as the pre-v0.69.11 default did.
    return ""


def _palette_profile_colors_for(device: Device) -> list[dict[str, str]] | None:
    """Ordered list of the active profile's palette colours for the
    colour-picker grid on the Calibration tab. Returns None when no
    profile is applied (the template hides the picker in that case)."""
    slug = _palette_profile_slug_for(device)
    if not slug:
        return None
    profile = palette_profiles.bundled_profile(slug)
    if profile is None:
        from pathlib import Path

        store = palette_profiles.PaletteProfileStore(Path(current_app.config["DATA_ROOT"]))
        profile = store.load(slug)
    if profile is None:
        return None
    # Grayscale panels edit a ramp, not named inks. Same editor grid and
    # the same ``<input type="color">`` cells, keyed ``level0..levelN``,
    # so one level per cell and the live preview's query params line up.
    # Without this a grey panel was offered Spectra 6 ink pickers that
    # its profile has no slots for and its packer would never read.
    family = _palette_family_for(device)
    if family in ("gray_4", "gray_16"):
        levels = 4 if family == "gray_4" else 16
        ramp = profile.gray.as_tuples(levels) or _test_pattern_gray_ramp(levels)
        return [
            {
                "name": f"level{i}",
                "label": f"Level {i}" + (" (black)" if i == 0 else ""),
                "hex": f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}",
            }
            for i, rgb in enumerate(ramp)
        ]
    out = [
        {"name": "black", "label": "Black", "hex": profile.palette.black},
        {"name": "white", "label": "White", "hex": profile.palette.white},
        {"name": "yellow", "label": "Yellow", "hex": profile.palette.yellow},
        {"name": "red", "label": "Red", "hex": profile.palette.red},
    ]
    # A 4-ink BWRY panel stops here. ``PaletteColors`` keeps six slots so
    # the stored schema is identical for every family, but the quantizer
    # slices a profile palette to the gamut's length, so blue / green are
    # discarded before dithering. Offering them would be two swatches
    # that silently do nothing.
    #
    # Scoped to bwry_4 on purpose. Trimming by the gamut's palette length
    # generalises better, but it is not behaviour-neutral for the other
    # families (a spectra6 profile carrying an orange would lose it), and
    # this change is meant to touch PicPak only. Every other gamut takes
    # the original path below unchanged.
    if family == "bwry_4":
        return out
    out.append({"name": "blue", "label": "Blue", "hex": profile.palette.blue})
    out.append({"name": "green", "label": "Green", "hex": profile.palette.green})
    if profile.palette.orange:
        out.append({"name": "orange", "label": "Orange", "hex": profile.palette.orange})
    return out


def _test_pattern_gray_ramp(levels: int) -> tuple[tuple[int, int, int], ...]:
    """Evenly-spaced fallback so the ramp editor still populates on a
    profile that carries no ``gray`` block (a colour profile applied to a
    grey panel, or one saved before ramps existed)."""
    from app.test_patterns import gray_ramp_palette

    return gray_ramp_palette(levels)


def _palette_profile_tone_for(device: Device) -> dict[str, Any]:
    """Active profile's tone + dither values, or the neutral defaults
    when no profile is applied. Feeds the tone-editor sliders in the
    Calibration tab so they pre-populate on render."""
    slug = _palette_profile_slug_for(device)
    if not slug:
        return {
            "exposure": 0,
            "s_curve": 0,
            "serpentine": False,
            "diffusion_strength": 100,
            "bundled": False,
            "editable": False,
        }
    profile = palette_profiles.bundled_profile(slug)
    if profile is None:
        from pathlib import Path

        store = palette_profiles.PaletteProfileStore(Path(current_app.config["DATA_ROOT"]))
        profile = store.load(slug)
    if profile is None:
        return {
            "exposure": 0,
            "s_curve": 0,
            "serpentine": False,
            "diffusion_strength": 100,
            "bundled": False,
            "editable": False,
        }
    return {
        "exposure": profile.tone.exposure,
        "s_curve": profile.tone.s_curve,
        "serpentine": profile.dither.serpentine,
        "diffusion_strength": profile.dither.diffusion_strength,
        "smoothing_radius": profile.edges.smoothing_radius,
        "preserve_line_art": profile.edges.preserve_line_art,
        "protect_native_colours": profile.edges.protect_native_colours,
        "lab_compress_min": profile.tone.lab_compress_min,
        "lab_compress_max": profile.tone.lab_compress_max,
        "color_match": profile.dither.color_match,
        "bundled": profile.bundled,
        "editable": True,
    }


def _palette_profile_choices_for(device: Device) -> list[dict[str, Any]]:
    """Bundled + user profiles offered to this device, scoped to the
    matching palette family. Empty for panels whose gamut has no
    bundled profile family (mono, ``bwry_4``, ``rgb24``, ``rgb16``);
    the section builder null-gates the palette endpoints in that case
    so the picker hides entirely (v0.69.11)."""
    from app.settings.palette_routes import profile_choices_for

    family = _palette_family_for(device)
    if not family:
        return []
    return profile_choices_for(family)


def _test_pattern_colors_for(device: Device) -> list[dict[str, Any]]:
    """Palette entries for the solid-fill colour picker on the
    Calibration tab. Snap to the device's declared gamut so the labels
    line up with the panel's actual colours; unknown gamuts fall back
    to the E6 default palette. Returns a list of ``{index, label, hex}``
    dicts keyed for the template. Empty list on mono panels: the
    solid-fill pattern isn't offered there, so the picker has nothing
    to gate."""
    panel = device.panel or {}
    gamut = str(panel.get("gamut") or "waveshare_e6")
    if gamut == "mono":
        return []
    labels = _TEST_PATTERN_GAMUT_LABELS.get(gamut, _TEST_PATTERN_GAMUT_LABELS["waveshare_e6"])
    hexes = _TEST_PATTERN_GAMUT_HEXES.get(gamut, _TEST_PATTERN_GAMUT_HEXES["waveshare_e6"])
    return [
        {"index": i, "label": labels[i], "hex": hexes[i]}
        for i in range(min(len(labels), len(hexes)))
    ]


def _build_sections() -> list[dict[str, Any]]:
    store = settings_store()
    sections: list[dict[str, Any]] = []

    app_raw = store.get_section("app")
    sections.append(
        {
            "id": "app",
            "kind": "app",
            "title": "App",
            "blurb": "Where Tesserae lives on the network.",
            "fields": APP_FIELDS,
            "state": _values_for_core("app", APP_FIELDS, app_raw),
            "endpoint": url_for("auth.settings_update", section_kind="app"),
            "meta": {"Network IP": detect_local_ip()},
        }
    )

    broker_raw = store.get_section("broker")
    broker_blurb = "Tesserae publishes frames here; devices subscribe."
    if current_app.config.get("HA_INGRESS_MODE"):
        broker_blurb = (
            "Tesserae publishes frames here; devices subscribe. "
            "Host, port, and credentials are managed in the Tesserae "
            "App's Configuration tab, changes there apply on the "
            "next App restart."
        )
    sections.append(
        {
            "id": "broker",
            "kind": "broker",
            "title": "MQTT broker",
            "blurb": broker_blurb,
            "fields": _broker_fields_with_client_id_hint(),
            "state": _values_for_core("broker", BROKER_FIELDS, broker_raw),
            "endpoint": url_for("auth.settings_update", section_kind="broker"),
            "meta": {"MQTT URL": _broker_mqtt_url(broker_raw)},
        }
    )

    # Virtual panel: the fallback canvas size for pages with no target
    # device (the "(any)" option in the page editor). Registered devices
    # bring their own panel, so this only matters before you've added a
    # device or for deliberately device-agnostic pages, hence it sits
    # below the broker rather than up top.
    sections.append(
        {
            "id": "panel",
            "kind": "panel",
            "title": "Virtual panel",
            "blurb": "Fallback canvas size for pages with no target device. Pick a preset or set a custom size; Portrait flips width and height. Devices you register override this with their own panel.",
            "fields": PANEL_FIELDS,
            "state": _values_for_core("app", PANEL_FIELDS, app_raw),
            "endpoint": url_for("auth.settings_update", section_kind="panel"),
        }
    )

    enabled_map = store.get_section("renderers_enabled")
    for renderer in renderers().all():
        # Per-instance clones inherit the base renderer's settings; the
        # cards add no UI value and just create N rows of the same
        # form. Filter them out, clone ids always contain '__'
        # (see renderer_loader.clone_for_instances).
        if "__" in renderer.id:
            continue
        # Renderer-wide fields only. Fields flagged ``device_setting:
        # true`` (e.g. pi_bin's dither/saturation/contrast) live on the
        # device card instead so each panel can be tuned independently.
        all_fields = list(renderer.manifest.get("settings", []))
        fields = [f for f in all_fields if not f.get("device_setting")]
        has_device_fields = len(fields) != len(all_fields)
        sid = f"renderer-{renderer.id}"
        rid = renderer.id
        is_enabled = enabled_map.get(rid)
        if is_enabled is None:
            is_enabled = True
        blurb = renderer.manifest.get("description") or ""
        if has_device_fields:
            blurb = (
                (blurb + " " if blurb else "")
                + "Per-display settings live on each device card under "
                + "Settings → Devices so every panel can be tuned independently."
            ).strip()
        sections.append(
            {
                "id": sid,
                "kind": "renderer",
                "title": f"Renderer: {renderer.name}",
                "blurb": blurb,
                "fields": fields,
                "state": render_for_admin("renderers", renderer.id, fields),
                "endpoint": url_for("auth.settings_update", section_kind=sid),
                "enabled": bool(is_enabled),
                "supports_toggle": True,
                "meta": {
                    "Topic": renderer.topic,
                    "Retain": "yes" if renderer.retain else "no",
                    "Device": renderer.device,
                },
            }
        )

    # v0.69.17: sort device cards by display name (case-insensitive,
    # falling back to id for stability) so the list order stays
    # predictable across renders. ``devices().all()`` returns entries
    # in registry insertion order, which shifts as devices are added,
    # renamed, or re-registered from the Discovered strip; a user
    # scrolling Settings → Devices would otherwise see cards jump
    # around between saves.
    _instances = [d for d in devices().all() if d.kind_of is not None]
    _instances.sort(key=lambda d: ((d.name or d.id).casefold(), d.id))
    for device in _instances:
        # Built-in kinds are templates, not bindable devices, they
        # never appear on the Devices tab. Every physical display is
        # represented by an instance (added manually or auto-registered
        # from the Discovered strip).
        if device.kind_of is None:
            continue
        sid = f"device-{device.id}"
        fields = _visible_config_fields(device)
        is_instance = device.kind_of is not None
        # Picture-quality (dither / saturation / contrast) lives on the
        # clone renderer keyed ``<base_id>__<device_id>``, one clone
        # per renderer the device's kind consumes. Surface each clone's
        # device_setting-flagged fields as a "Picture quality" subsection;
        # the template renders them inside the combined form with the
        # name pattern ``<clone_id>:<field_name>`` so the save handler
        # can route each value back to the right clone's namespace.
        # Contrast + saturation moved to the Calibration tab in v0.67,
        # dither joined them in v0.68 (all three are colour-tuning
        # concerns, not hardware setup). ``calibrated`` was retired in
        # v0.68 too: the palette-profile picker now owns "which palette
        # does this device paint"; storage is preserved for backward
        # compat with older configs but the toggle no longer surfaces
        # in either tab.
        _CALIBRATION_TAB_FIELDS = {"contrast", "saturation", "dither"}
        _HIDDEN_TAB_FIELDS = {"calibrated"}
        picture_quality: list[dict[str, Any]] = []
        calibration_picture_quality: list[dict[str, Any]] = []
        if is_instance:
            for clone in renderers().for_device(device.id):
                all_dev_fields = [
                    f for f in clone.manifest.get("settings", []) if f.get("device_setting")
                ]
                if not all_dev_fields:
                    continue
                rendering_fields = [
                    f
                    for f in all_dev_fields
                    if f["name"] not in _CALIBRATION_TAB_FIELDS
                    and f["name"] not in _HIDDEN_TAB_FIELDS
                ]
                calibration_fields = [
                    f for f in all_dev_fields if f["name"] in _CALIBRATION_TAB_FIELDS
                ]
                base_id = clone.id.split("__", 1)[0]
                base_name = clone.name.split(" (", 1)[0]
                state = store.get_for_runtime("renderers", clone.id, all_dev_fields)
                if rendering_fields:
                    picture_quality.append(
                        {
                            "clone_id": clone.id,
                            "base_id": base_id,
                            "base_name": base_name,
                            "fields": rendering_fields,
                            "state": state,
                        }
                    )
                if calibration_fields:
                    calibration_picture_quality.append(
                        {
                            "clone_id": clone.id,
                            "base_id": base_id,
                            "base_name": base_name,
                            "fields": calibration_fields,
                            "state": state,
                        }
                    )
        sections.append(
            {
                "id": sid,
                "kind": "device",
                "title": f"Device: {device.name}",
                "icon": device.icon,
                "blurb": device.manifest.get("description") or "",
                "fields": fields,
                "state": (store.get_for_runtime("devices", device.id, fields) if fields else {}),
                "endpoint": (url_for("auth.settings_update", section_kind=sid) if fields else None),
                # Single Save for the whole device card, the template
                # wraps the renderer-config + panel + quiet-hours fields
                # in one form posting here, and this handler fans out to
                # the same service helpers the per-subsection endpoints
                # call. Only present on instances (kinds aren't editable
                # in the UI). The per-subsection endpoints above stay
                # available for programmatic callers / direct hits.
                "combined_endpoint": (
                    url_for("auth.devices_update_combined", instance_id=device.id)
                    if is_instance
                    else None
                ),
                "meta": _device_meta_block(device, is_instance),
                "connection_details": _device_connection_details(device, is_instance),
                "transport_badge": _transport_badge(device),
                "status": _status_view(device),
                # The colour-gamut control only matters for the .bin Pi path
                # (pi_bin packs server-side to a fixed palette). PNG clients
                # project their own gamut on-device; the ESP32 firmware is
                # always E6. Gate on the device's base renderer being pi_bin.
                "gamut_capable": any(
                    rid.split("__", 1)[0] == "pi_bin" for rid in device.renderer_ids
                ),
                "delete_endpoint": (
                    url_for("auth.devices_delete", instance_id=device.id) if is_instance else None
                ),
                # Counts for the delete-confirm modal (v0.69.2, issue
                # #48): what a checked "Also wipe" tickbox would drop.
                # Zero everywhere means the delete is already clean and
                # the UI can skip the checkbox row.
                "orphan_state": _orphan_state_counts_for(device) if is_instance else None,
                # Regenerate token, only present on devices that use
                # access tokens (TRMNL). The template gates the button
                # on this being non-None so a Pi/ESP32 card doesn't
                # grow a meaningless control.
                "regenerate_token_endpoint": (
                    url_for("auth.devices_regenerate_token", instance_id=device.id)
                    if is_instance and "access_token" in device.manifest
                    else None
                ),
                # Reveal-token endpoint (issue #20). The Connection details
                # row shows the masked token; the Reveal button POSTs here
                # with an explicit confirmation and the route logs the
                # reveal to the EventLog for audit. Only present on devices
                # that have a token (REST + TRMNL); MQTT devices skip it.
                "reveal_token_endpoint": (
                    url_for("auth.devices_reveal_token", instance_id=device.id)
                    if is_instance and "access_token" in device.manifest
                    else None
                ),
                # Transport flip (v0.52 Phase 1b). Instance-only; flips
                # between MQTT and REST without losing the device's id /
                # panel / per-clone renderer settings. ``transport`` is
                # the CURRENT transport, the template uses it to decide
                # the button label and the new value to POST. None on
                # kinds (only instances flip).
                # Push devices (OpenDisplay-via-HA) don't flip: there's no
                # broker to switch to and no REST poll to mint a token for,
                # the frame always goes out through the HA service call.
                "set_transport_endpoint": (
                    url_for("auth.devices_set_transport", instance_id=device.id)
                    if is_instance and device.transport in ("mqtt", "rest")
                    else None
                ),
                "transport": device.transport if is_instance else None,
                # OpenDisplay setup helper: detects whether this Tesserae is
                # the HA add-on and points at the integration (HA path) or
                # the bridge (standalone). None for non-OpenDisplay kinds.
                "opendisplay_setup": _opendisplay_setup(device),
                # The Display-name field reads ``device_name`` (raw) for
                # the input's value, separately from ``title`` (which gets
                # a "Device: " prefix for the card heading). Instance-only;
                # built-in kinds aren't editable, and ``None`` hides the
                # field via a Jinja ``is not none`` check.
                "device_name": device.name if is_instance else None,
                # Panel edit (orientation + dims) is only offered on
                # instances, kinds aren't shown here at all.
                "panel": device.panel if is_instance else None,
                "panel_rotation_options": (
                    _rotation_options(device.panel) if is_instance else None
                ),
                "panel_endpoint": (
                    url_for("auth.devices_update_panel", instance_id=device.id)
                    if is_instance
                    else None
                ),
                "device_id": device.id,
                "calibrate_endpoint": (
                    url_for("auth.devices_calibrate", instance_id=device.id)
                    if is_instance
                    else None
                ),
                "calibrate_apply_endpoint": (
                    url_for("auth.devices_calibrate_apply", instance_id=device.id)
                    if is_instance
                    else None
                ),
                # Colour test-pattern picker (Calibration tab). The
                # endpoint POSTs pattern_id + optional color_index and
                # pushes the resulting PNG through the device's real
                # renderer; the preview URL returns the same bytes as
                # an <img> source so the tab can show what will be
                # sent. Both are None on kinds since the push path is
                # instance-only. ``test_pattern_colors`` lists palette
                # entries as (index, label, hex) tuples for the solid-
                # fill picker; snap to the device's declared gamut so
                # the labels match the panel.
                "test_pattern_endpoint": (
                    url_for("auth.devices_send_test_pattern", instance_id=device.id)
                    if is_instance
                    else None
                ),
                "test_pattern_preview_url": (
                    url_for("auth.devices_test_pattern_preview", instance_id=device.id)
                    if is_instance
                    else None
                ),
                "test_patterns": (
                    test_patterns.list_patterns(
                        has_custom_image=_has_custom_image(device.id),
                        gamut=str((device.panel or {}).get("gamut") or ""),
                    )
                    if is_instance
                    else []
                ),
                "custom_image_uploaded": _has_custom_image(device.id) if is_instance else False,
                "custom_image_upload_endpoint": (
                    url_for("auth.devices_custom_image_upload", instance_id=device.id)
                    if is_instance
                    else None
                ),
                "custom_image_delete_endpoint": (
                    url_for("auth.devices_custom_image_delete", instance_id=device.id)
                    if is_instance
                    else None
                ),
                "test_pattern_colors": (_test_pattern_colors_for(device) if is_instance else []),
                # Palette profile picker (Calibration tab, Phase 1 of the
                # v0.67 palette-profile work). ``palette_profile_slug`` is
                # the currently-applied slug (empty when none);
                # ``palette_profile_choices`` is the ordered dropdown
                # scoped to this device's gamut so a Spectra 6 panel doesn't
                # see the Inky 7-colour presets and vice versa. Endpoints
                # are None on kinds (the picker is instance-only), and
                # v0.69.11 also null-gates the whole palette section when
                # the device's gamut has no matching profile family
                # (mono, bwry_4, rgb24, rgb16) so the picker doesn't
                # falsely offer Spectra 6 profiles to a mono panel.
                "palette_profile_slug": (_palette_profile_slug_for(device) if is_instance else ""),
                "palette_profile_choices": (
                    _palette_profile_choices_for(device) if is_instance else []
                ),
                "palette_apply_endpoint": (
                    url_for("auth.devices_palette_apply", instance_id=device.id)
                    if is_instance and _palette_family_for(device)
                    else None
                ),
                "palette_save_endpoint": (
                    url_for("auth.devices_palette_save", instance_id=device.id)
                    if is_instance and _palette_family_for(device)
                    else None
                ),
                "palette_reset_endpoint": (
                    url_for("auth.devices_palette_reset", instance_id=device.id)
                    if is_instance and _palette_family_for(device)
                    else None
                ),
                "palette_import_endpoint": (
                    url_for("auth.palette_profile_import")
                    if is_instance and _palette_family_for(device)
                    else None
                ),
                # Tone / dither editor (v0.67.1). ``palette_profile_tone``
                # carries the active profile's current values so the
                # sliders pre-populate; ``palette_update_tone_endpoint``
                # is the POST target for the editor.
                "palette_profile_tone": (_palette_profile_tone_for(device) if is_instance else {}),
                "palette_update_tone_endpoint": (
                    url_for("auth.devices_palette_update_tone", instance_id=device.id)
                    if is_instance and _palette_family_for(device)
                    else None
                ),
                # Per-colour palette editor (v0.67.3). Endpoint takes 6-7
                # hex values keyed by colour name. ``palette_profile_colors``
                # carries the active profile's current values so the
                # ``<input type="color">`` fields pre-populate on render;
                # None when no profile is applied so the template hides
                # the editor block.
                "palette_update_palette_endpoint": (
                    url_for("auth.devices_palette_update_palette", instance_id=device.id)
                    if is_instance and _palette_family_for(device)
                    else None
                ),
                "palette_profile_colors": (
                    _palette_profile_colors_for(device) if is_instance else None
                ),
                # Contrast + saturation live in the Calibration tab now.
                # Shape mirrors ``picture_quality`` so the same template
                # macros render them; storage is the same per-clone
                # ``renderers.<clone_id>.<field>`` path (the fields are
                # just hidden from the Rendering tab in v0.67+).
                "calibration_picture_quality": calibration_picture_quality,
                # Per-device quiet-hours override. Read from the
                # manifest so the form can preselect the user's
                # current setting; ``quiet_hours_endpoint`` is None on
                # kinds (only instances can override).
                "picture_quality": picture_quality,
                "quiet_hours": (device.manifest.get("quiet_hours") or {} if is_instance else {}),
                "quiet_hours_endpoint": (
                    url_for("auth.devices_update_quiet_hours", instance_id=device.id)
                    if is_instance
                    else None
                ),
                # Per-device battery-display offset (mV + %). Manifest
                # block is ``battery_offset: {mv, pct}``; both default to
                # 0 and the block drops when both are 0. Like quiet
                # hours, this is an instances-only knob; kinds don't get
                # the form.
                "battery_offset": (
                    device.manifest.get("battery_offset") or {} if is_instance else {}
                ),
                "battery_offset_endpoint": (
                    url_for("auth.devices_update_battery_offset", instance_id=device.id)
                    if is_instance
                    else None
                ),
                # Per-device button map (physical button wakes). The
                # textarea shows the stored per-device override (empty
                # when nothing is set); the "effective map" fold-out
                # shows the resolved merge of default + global + per-
                # device so an admin can see what buttons actually
                # resolve to right now. Registered action names come
                # from the runtime registry so third-party plugins
                # that call ``button_actions.register`` at import time
                # show up in the help text automatically.
                "button_map_capable": is_instance,
                "button_map_json": (
                    _button_map_stored_json(store, device.id) if is_instance else ""
                ),
                "button_map_effective": (
                    _button_map_effective_json(store, device.id) if is_instance else ""
                ),
                "button_actions_available": (registered_actions() if is_instance else ()),
                # Per-device rotation view: every Schedule whose target
                # page binds to this device, sorted by window start.
                # Pure read view, each row deep-links to the Schedules
                # editor where the user can actually change it.
                "timetable_entries": (
                    device_timetable.timetable_for_device(
                        device.id,
                        devices=devices(),
                        pages=current_app.config["PAGE_STORE"],
                        schedules=current_app.config["SCHEDULE_STORE"],
                        unbound_broadcast=bool(
                            settings_store().get_section("app").get("unbound_broadcast", False)
                        ),
                    )
                    if is_instance
                    else []
                ),
                # Read-only diagnostic block: app version, resolved
                # kind + renderer clone ids, panel details, on-disk
                # instance file, raw JSON with secrets masked. Renders
                # under a collapsed <details> at the bottom of the
                # General tab so it stays out of the way until needed.
                "debug_info": _device_debug_info(device, is_instance),
                # Touch-capable panels (digitizer, e.g. reTerminal E1003)
                # get a "Touch monitor" link to the per-device visualiser
                # (issue #49).
                "touch": bool(is_instance and device.manifest.get("touch")),
            }
        )

    for plugin in plugins().plugins.values():
        fields = list(plugin.manifest.get("settings", []))
        if not fields:
            continue
        sid = f"plugin-{plugin.id}"
        sections.append(
            {
                "id": sid,
                "kind": "plugin",
                "title": f"Plugin: {plugin.name}",
                "blurb": plugin.manifest.get("description") or "",
                "fields": fields,
                "state": render_for_admin("plugins", plugin.id, fields),
                "endpoint": url_for("auth.settings_update", section_kind=sid),
            }
        )

    return sections


# Config fields that only make sense on a panel the firmware says can hold
# a connection open. Offering "Stay awake" on a battery display is an
# invitation to flatten it overnight, and nothing about the model name
# answers the question: a reTerminal on USB and the same board on its
# battery are the same kind. So the switch follows the advertised
# ``can_stay_awake`` capability, which the firmware decides.
_ALWAYS_ON_FIELDS = frozenset({"always_on", "awake_poll_s"})


def _can_stay_awake(device_id: str) -> bool:
    """Whether this device has told us it can stay powered and awake.

    Live status cache first, then the persisted facts, so a server
    restart doesn't hide the switch from an already-configured panel
    until its next heartbeat.
    """
    status = (current_app.config.get("DEVICE_STATUS") or {}).get(device_id)
    if isinstance(status, dict) and status.get("can_stay_awake") is not None:
        return bool(status.get("can_stay_awake"))
    facts = current_app.config.get("DEVICE_FACTS")
    entry = facts.get(device_id) if facts is not None else None
    return bool(entry.get("can_stay_awake")) if isinstance(entry, dict) else False


def _visible_config_fields(device: Any) -> list[dict[str, Any]]:
    """The device kind's config fields, minus any this particular panel
    can't act on.

    Hiding is presentation only: the save handlers still walk the full
    schema, so a device that stops advertising the capability has
    ``always_on`` written back to its default on the next save rather
    than holding a setting its firmware no longer honours.
    """
    fields = config_fields_from_schema(device.config_schema)
    if _can_stay_awake(device.id):
        return fields
    return [f for f in fields if f.get("name") not in _ALWAYS_ON_FIELDS]


def _button_map_stored_json(store: Any, device_id: str) -> str:
    """Serialise the per-device ``button_map`` (as stored in
    ``settings.devices.<id>.button_map``) for the textarea. Empty
    string when nothing is stored so the textarea shows its
    placeholder default map."""
    try:
        section = store.get_section("devices") or {}
        raw = section.get(device_id, {}).get("button_map")
    except Exception:
        raw = None
    if not isinstance(raw, dict) or not raw:
        return ""
    return json.dumps(raw, indent=2, sort_keys=True)


def _button_map_effective_json(store: Any, device_id: str) -> str:
    """Serialise the resolved effective button map: hardcoded default,
    merged with the global ``settings.app.button_map``, merged with the
    per-device override. The fold-out under the textarea shows this so
    an admin can see what each button actually resolves to right now
    without opening a shell."""
    try:
        devices_section = store.get_section("devices") or {}
        per_device = devices_section.get(device_id, {}).get("button_map")
    except Exception:
        per_device = None
    try:
        global_map = store.get_section("app").get("button_map")
    except Exception:
        global_map = None
    result: dict[str, str] = dict(DEFAULT_BUTTON_MAP)
    if isinstance(global_map, dict):
        for k, v in global_map.items():
            if isinstance(k, str) and isinstance(v, str) and v:
                result[k] = v
    if isinstance(per_device, dict):
        for k, v in per_device.items():
            if isinstance(k, str) and isinstance(v, str) and v:
                result[k] = v
    return json.dumps(result, indent=2, sort_keys=True)


def _device_debug_info(device: Device, is_instance: bool) -> dict[str, Any] | None:
    """Compose the read-only diagnostic block shown under the device
    card's Debug section. Instances only; kinds don't get one because
    the raw kind manifest is already visible on the compatibility
    matrix page.

    Surfaces the exact runtime state so "wrong renderer" / "wrong
    panel" / "wrong kind" bugs are one glance away: the resolved
    kind id, the renderer clone ids the push pipeline will use, the
    panel block (dims + gamut + orientation), transport + topics,
    the on-disk instance file path, and the raw JSON with secret-
    suffixed keys masked. The app version is stamped too so a user
    reporting "colour renderer not working" can eyeball whether the
    server is actually on the version that ships the fix."""
    if not is_instance:
        return None

    import json as _json

    from flask import current_app as _app

    app_version = str(_app.config.get("APP_VERSION") or "unknown")
    instance_path = str(device.path)
    raw_manifest: dict[str, Any] = {}
    try:
        raw_manifest = _json.loads(device.path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        raw_manifest = {}
    if not isinstance(raw_manifest, dict):
        raw_manifest = {}
    masked_manifest = _mask_secrets(raw_manifest)
    try:
        raw_pretty = _json.dumps(masked_manifest, indent=2, sort_keys=False)
    except (TypeError, ValueError):
        raw_pretty = "<unserialisable>"

    return {
        "app_version": app_version,
        "kind_of": device.kind_of,
        "device_id": device.id,
        "renderer_ids": list(device.renderer_ids),
        "transport": device.transport,
        "status_topic": device.status_topic,
        "config_topic": device.config_topic,
        "panel": device.panel,
        "instance_file": instance_path,
        "raw_manifest_json": raw_pretty,
    }


def _mask_secrets(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``manifest`` with obviously-sensitive
    values redacted. Applies to the tesserae convention of a
    ``_secret``-suffixed key (secretbox-encrypted at rest, still
    shouldn't ship in a debug pane screenshot) and to the well-known
    ``access_token`` field on TRMNL / REST devices. Nested dicts
    (e.g. ``protocol_config``) are recursed once so a nested
    ``access_token`` under ``protocol_config`` is still masked."""
    out: dict[str, Any] = {}
    for k, v in manifest.items():
        if isinstance(k, str) and (k.endswith("_secret") or k == "access_token"):
            out[k] = "***"
            continue
        if isinstance(v, dict):
            out[k] = _mask_secrets(v)
            continue
        out[k] = v
    return out


def _device_meta_block(device: Device, is_instance: bool) -> dict[str, Any]:
    """Compose the meta key/value block shown at the top of each
    device card. Branches on transport:

    * REST devices (``Device.transport == "rest"``, set by the pairing
      flow): surface the per-device access token + the server URL the
      firmware should point at. Skip MQTT topics; they're not used.
    * MQTT instances declaring an access_token (TRMNL clients):
      backward-compat shape, HTTP polling + token + server URL.
    * Other MQTT devices: status / config topic pair.
    """
    meta: dict[str, Any] = {"Renderers": ", ".join(device.renderer_ids)}
    access_token = device.manifest.get("access_token")
    if device.transport == "rest":
        # v0.52 REST device. Mask the token (first 4 + "...") so a
        # screenshot of the Devices page doesn't leak it; the user can
        # still copy the full thing via the soon-to-land "show token"
        # action on the device card.
        masked = f"{access_token[:4]}…" if isinstance(access_token, str) and access_token else "-"
        meta["Transport"] = "REST"
        meta["Access token"] = masked
        meta["Server URL"] = f"http://{request.host}"
    elif isinstance(access_token, str) and access_token:
        # Pre-v0.52 HTTP-polled device (TRMNL). Same shape as before,
        # except the transport label is more specific.
        meta["Transport"] = "HTTP polling"
        meta["Access token"] = access_token
        meta["Server URL"] = f"http://{request.host}"
    else:
        # MQTT device, keep the topic-pair display.
        meta["Transport"] = "MQTT"
        meta["Status topic"] = device.status_topic or "-"
        meta["Config topic"] = device.config_topic or "-"
    if is_instance:
        meta["Instance of"] = str(device.kind_of)
    return meta


# Short transport label used by the device card's header badge.
_TRANSPORT_BADGE = {"rest": "REST", "mqtt": "MQTT", "push": "HA"}


def _transport_badge(device: Device) -> str:
    """One-word transport label for the device card header badge.
    Returns "HTTP" for legacy TRMNL devices (access-token + MQTT)
    so the header still reads cleanly when the card is collapsed, and
    "HA" for push devices (OpenDisplay-via-HA) delivered through a Home
    Assistant service call rather than a broker."""
    if device.transport == "rest":
        return "REST"
    if device.transport == "push":
        return "HA"
    if device.transport == "relay":
        # Before the access-token branch: relay devices carry a device token
        # too, but it authenticates against the relay, not this server.
        return "Relay"
    if isinstance(device.manifest.get("access_token"), str) and device.manifest.get("access_token"):
        return "HTTP"
    return _TRANSPORT_BADGE.get(device.transport or "mqtt", "MQTT")


_OPENDISPLAY_BRIDGE = {
    "label": "tesserae-opendisplay bridge",
    "url": "https://github.com/dmellok/tesserae-opendisplay",
}
_OPENDISPLAY_INTEGRATION = {
    "label": "OpenDisplay integration for Home Assistant",
    "url": "https://opendisplay.org/",
}


def _rotation_options(panel: dict[str, Any] | None) -> list[dict[str, str]]:
    """Rotation choices for a device card: orientation value + degrees label.

    The degrees are the turn from the panel's own framebuffer, not from
    landscape. Those agree for the landscape-native panels that make up
    most of the hardware list, so this changes nothing for them.

    They part company on a portrait-native panel. A client reporting
    720x1280 with ``rotation: 0`` is saying "compose at my buffer, no
    turn", which is stored as the portrait orientation; labelling that
    90 degrees told the operator their client had asked for something it
    hadn't, and correcting it to 0 then transposed the dimensions. The
    label now matches what the client declared (issue #200)."""
    portrait_native = False
    if panel:
        try:
            portrait_native = int(panel["native_h"]) > int(panel["native_w"])
        except (KeyError, TypeError, ValueError):
            portrait_native = False
    # Index i is the orientation reached by turning i quarter-turns from
    # the framebuffer: 90 and 270 transpose it, 180 and 270 flip it.
    order = (
        ("portrait", "landscape", "portrait_flipped", "landscape_flipped")
        if portrait_native
        else ("landscape", "portrait", "landscape_flipped", "portrait_flipped")
    )
    return [
        {"value": value, "label": f"{degrees}°"}
        for value, degrees in zip(order, (0, 90, 180, 270), strict=True)
    ]


def _opendisplay_setup(device: Device) -> dict[str, Any] | None:
    """Setup guidance for OpenDisplay device cards. Detects whether this
    Tesserae is the Home Assistant add-on and points the user at the
    right delivery path: HA's OpenDisplay integration (the HA kind) or
    the standalone bridge (the REST kind). Returns None for every other
    kind so the card only grows the section for OpenDisplay devices."""
    kind = device.kind_of or device.id
    if kind not in ("opendisplay", "opendisplay_ha"):
        return None
    in_ha = is_ha_addon()
    if kind == "opendisplay_ha":
        if in_ha:
            return {
                "title": "OpenDisplay via Home Assistant",
                "in_ha": True,
                "ok": True,
                "body": (
                    "Running as the Home Assistant add-on. Install the OpenDisplay "
                    "integration in HA and pair your tag, then set its HA device id "
                    "above. On each render Tesserae writes the frame to /media and "
                    "calls opendisplay.upload_image; HA pushes it to the tag over "
                    "Bluetooth LE."
                ),
                "link": _OPENDISPLAY_INTEGRATION,
            }
        return {
            "title": "OpenDisplay via Home Assistant",
            "in_ha": False,
            "ok": False,
            "body": (
                "This kind delivers frames through Home Assistant's "
                "opendisplay.upload_image action, but this Tesserae is not running "
                "as the HA add-on (no shared /media, no Supervisor). Run the HA "
                "add-on, or use the OpenDisplay tag (bridge) kind with the "
                "standalone bridge instead."
            ),
            "link": _OPENDISPLAY_BRIDGE,
        }
    return {
        "title": "OpenDisplay bridge",
        "in_ha": in_ha,
        "ok": True,
        "body": (
            "Driven by the standalone bridge over Bluetooth LE. Install it on a "
            "machine near your tags (pip install tesserae-opendisplay), then pair "
            "it with a code from Settings then Devices. One bridge drives many tags."
        ),
        "link": _OPENDISPLAY_BRIDGE,
    }


def _device_connection_details(device: Device, is_instance: bool) -> list[dict[str, Any]]:
    """Structured rows for the Connection details disclosure on the
    device card. Each row is {label, value, monospace, action}; the
    template renders ``action == 'reveal'`` rows with a Reveal button
    next to the masked value (issue #20). Branches by transport so
    REST devices don't show dormant MQTT topic rows (issue #21)."""
    rows: list[dict[str, Any]] = [
        {"label": "Device id", "value": device.id, "monospace": True},
        {"label": "Renderer", "value": ", ".join(device.renderer_ids), "monospace": True},
    ]
    if is_instance:
        rows.append({"label": "Instance of", "value": str(device.kind_of), "monospace": True})
    # MAC is what /discover matches on to hand a firmware its token back,
    # so surfacing it (and its absence) makes a stuck pairing or a pair of
    # devices sharing a MAC diagnosable from the card (issue #226).
    stored_mac = device.manifest.get("mac")
    if isinstance(stored_mac, str) and stored_mac.strip():
        rows.append({"label": "MAC", "value": stored_mac.strip(), "monospace": True})
    elif device.transport == "rest":
        rows.append(
            {
                "label": "MAC",
                "value": "not recorded (this device can't auto-claim via /discover)",
                "monospace": False,
            }
        )
    access_token = device.manifest.get("access_token")
    if device.transport == "relay":
        # Remote panel: it never reaches this server directly, so a local
        # Server URL / broker topic row would be misleading. The token
        # authenticates the panel against the relay mailbox.
        rows.append(
            {"label": "Delivery", "value": "Cloud relay (sealed frames)", "monospace": False}
        )
        from app.relay_config import base_url as _relay_base_url
        from app.relay_config import relay_config

        relay_url = _relay_base_url(relay_config(settings_store()))
        if relay_url:
            rows.append({"label": "Relay", "value": relay_url, "monospace": True})
        if isinstance(access_token, str) and access_token:
            rows.append(
                {
                    "label": "Device token (relay)",
                    "value": f"{access_token[:4]}··············",
                    "monospace": True,
                    "action": "reveal",
                }
            )
        paired = bool(device.manifest.get("relay_frame_key"))
        rows.append({"label": "Pairing", "value": "Paired" if paired else "Waiting for panel"})
    elif device.transport == "rest":
        rows.append({"label": "Server URL", "value": f"http://{request.host}", "monospace": True})
        if isinstance(access_token, str) and access_token:
            rows.append(
                {
                    "label": "Access token",
                    "value": f"{access_token[:4]}··············",
                    "monospace": True,
                    "action": "reveal",
                }
            )
    elif isinstance(access_token, str) and access_token:
        rows.append({"label": "Server URL", "value": f"http://{request.host}", "monospace": True})
        rows.append({"label": "Access token", "value": access_token, "monospace": True})
    else:
        if device.status_topic:
            rows.append({"label": "Status topic", "value": device.status_topic, "monospace": True})
        if device.config_topic:
            rows.append({"label": "Config topic", "value": device.config_topic, "monospace": True})
    return rows


def _humanize_signal(rssi: object) -> dict[str, Any] | None:
    """Map raw RSSI (negative dBm) to a {bars, label, sub} tile. Returns
    None when no value is reported so the template can fall back to a
    'no heartbeat' tile instead of a misleading 0-bar reading."""
    if not isinstance(rssi, (int, float, str)):
        return None
    try:
        value = int(rssi)
    except (TypeError, ValueError):
        return None
    if value >= -55:
        bars, label = 4, "Excellent"
    elif value >= -65:
        bars, label = 3, "Good"
    elif value >= -75:
        bars, label = 2, "Fair"
    else:
        bars, label = 1, "Poor"
    return {"bars": bars, "label": label, "sub": f"{value} dBm"}


def _humanize_power(parsed: dict[str, Any]) -> dict[str, Any]:
    """Map battery_mv / battery_pct to a {label, sub} tile. Treats both
    fields being absent or zero as a mains device so a Pi or ESP32 dev
    board doesn't read as a dead battery (regression #/the-pico)."""
    pct = parsed.get("battery_pct")
    mv = parsed.get("battery_mv")
    if pct is None and mv is None:
        return {"label": "Mains", "sub": "No battery"}
    if isinstance(pct, (int, float)) and pct <= 0 and isinstance(mv, (int, float)) and mv <= 0:
        return {"label": "Mains", "sub": "No battery"}
    if isinstance(pct, (int, float)):
        sub = f"{int(mv)} mV" if isinstance(mv, (int, float)) else None
        return {"label": f"{int(pct)}%", "sub": sub}
    if isinstance(mv, (int, float)):
        return {"label": f"{int(mv)} mV", "sub": None}
    return {"label": "Unknown", "sub": None}


def _humanize_firmware(parsed: dict[str, Any]) -> dict[str, Any] | None:
    """Map parsed fw_version + ip into a {value, sub} tile. Returns None
    when neither field was reported so the template can collapse the
    tile rather than render an empty box."""
    fw = parsed.get("fw_version") or parsed.get("firmware") or parsed.get("version")
    ip = parsed.get("ip") or parsed.get("ip_addr") or parsed.get("address")
    if fw is None and ip is None:
        return None
    return {"value": str(fw) if fw is not None else "Unknown", "sub": str(ip) if ip else None}


def _humanize_environment(parsed: dict[str, Any]) -> dict[str, str | None] | None:
    """Format optional environmental telemetry for one compact status tile."""

    def finite_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    temperature = finite_float(parsed.get("temperature_c"))
    humidity = finite_float(parsed.get("humidity_pct"))
    if temperature is None and humidity is None:
        return None
    if temperature is not None:
        return {
            "value": f"{temperature:.1f} °C",
            "sub": f"{humidity:.0f}% RH" if humidity is not None else None,
        }
    return {"value": f"{humidity:.0f}% RH", "sub": None}


def _status_tiles(parsed: dict[str, Any]) -> dict[str, Any]:
    """Humanized summary used on the Status tab of the device card."""
    return {
        "signal": _humanize_signal(parsed.get("rssi")),
        "power": _humanize_power(parsed),
        "firmware": _humanize_firmware(parsed),
        "environment": _humanize_environment(parsed),
    }


def _smart_sync_header(ss: dict[str, Any]) -> dict[str, Any]:
    """Build the inline header pill + meter fields for the Smart Sync
    panel: a short tag ("Trusted" / "Warming up · 2 of 3 wakes" /
    "Waiting") and a 0-10 confidence meter percent."""
    state = ss.get("state")
    confidence = ss.get("confidence") or 0
    confidence_pct = min(100, max(0, int(confidence) * 10))
    if state == "active":
        return {
            "tag": "Trusted",
            "tone": "ok",
            "confidence_pct": confidence_pct,
            "explainer": "This device's wakes land within ±60 s; the scheduler is JIT-rendering for it.",
        }
    if state == "warming":
        wakes = int(confidence)
        return {
            "tag": f"Warming up · {wakes} of 3 wakes",
            "tone": "warn",
            "confidence_pct": confidence_pct,
            "explainer": "One more on-time wake will flip this device to Trusted and widen its sync window.",
        }
    if state == "waiting":
        return {
            "tag": "Waiting for heartbeat",
            "tone": "muted",
            "confidence_pct": 0,
            "explainer": "Once the device wakes and publishes its heartbeat you'll see a prediction here.",
        }
    return {
        "tag": "Unavailable",
        "tone": "muted",
        "confidence_pct": 0,
        "explainer": ss.get("reason") or "",
    }


def _reported_panel_hint(device: Device, parsed: dict[str, Any]) -> str | None:
    """Reconcile the panel's reported width/height with the editable
    panel.w/h so the user understands why the numbers don't match when
    they're rotated. Returns a sentence like "Device reports 1600 × 1200
    — values are swapped here because rotation is 90°." or None when
    nothing's been reported yet."""
    reported_w = parsed.get("panel_w") or parsed.get("reported_w") or parsed.get("width")
    reported_h = parsed.get("panel_h") or parsed.get("reported_h") or parsed.get("height")
    if reported_w is None or reported_h is None:
        return None
    try:
        rw, rh = int(reported_w), int(reported_h)
    except (TypeError, ValueError):
        return None
    panel = device.panel or {}
    cur_w, cur_h = int(panel.get("w") or 0), int(panel.get("h") or 0)
    orientation = panel.get("orientation", "landscape")
    rotated = orientation in ("portrait", "portrait_flipped")
    if rotated and (cur_w, cur_h) == (rh, rw):
        suffix = " — values are swapped here because rotation is 90° / 270°."
    else:
        suffix = ""
    return f"Device reports {rw} × {rh}{suffix}"


def _status_view(device: Device) -> dict[str, Any]:
    """Build the status-block dict the template renders above the config
    form: freshness class (ok / warn / stale / unknown), relative time,
    the parsed key/value pairs, humanized status tiles, the smart-sync
    telemetry summary, and a reconcile hint for the panel dims."""
    cache = device_status().get(device.id)
    smart_sync = _smart_sync_view(device.id)
    base: dict[str, Any] = {
        "health": "unknown",
        "relative": "no heartbeat received yet",
        "parsed": {},
        "tiles": _status_tiles({}),
        "smart_sync": smart_sync,
        "smart_sync_header": _smart_sync_header(smart_sync),
        "reported_panel_hint": None,
        "firmware": _firmware_view(device, {}),
        "ota": None,
        # Bound albums show even with no heartbeat on record: a binding that
        # has never reported is when you most need to see it, and the line
        # carries the Resync control (#247).
        "collection": _collection_view(device, {}),
    }
    if cache is None:
        return base
    age = max(0.0, time.time() - float(cache.get("received_at", 0)))
    if age <= STATUS_FRESH_S:
        health = "ok"
    elif age <= STATUS_WARN_S:
        health = "warn"
    else:
        health = "stale"
    parsed = cache.get("parsed", {})
    base.update(
        {
            "health": health,
            "relative": format_relative(age),
            "parsed": parsed,
            "tiles": _status_tiles(parsed),
            "reported_panel_hint": _reported_panel_hint(device, parsed),
            "firmware": _firmware_view(device, parsed),
            "ota": _ota_view(cache.get("ota")),
            "collection": _collection_view(device, cache),
        }
    )
    return base


# Reported playback state → pill colour for the Devices-card album line.
_COLLECTION_PILL_CLASS = {
    "playing": "is-ok",
    "syncing": "is-warn",
    "paused": "is-neutral",
    "error": "is-danger",
}


def _collection_view(device: Device, cache: dict[str, Any]) -> dict[str, Any] | None:
    """Shape the offline-album playback line for the Devices card, or None.

    Shown while an enabled album is bound to this device. The *reported* half
    (state pill, counts, age) is filled only when the device's last report
    describes that album, so a leftover report from an unbound album doesn't
    linger; the line itself stays either way, because a binding that has never
    produced a report is exactly when you want to see it, and it carries the
    Resync control (#247). Deliberately never names a current frame: once
    playback is local the report is an observation, not a live "current
    screen" (frame-cache contract, "Reporting and truthful state")."""
    store = current_app.config.get("ALBUM_STORE")
    if store is None:
        return None
    from app.collection_sync import bound_album_for

    album = bound_album_for(store, device.id)
    if album is None:
        return None
    report = cache.get("collection_report")
    if not isinstance(report, dict) or report.get("id") != f"album:{album.id}":
        return {
            "album_name": album.name,
            "state": "",
            "pill_class": None,
            "counts": None,
            "relative": None,
        }
    cached = report.get("cached")
    total = report.get("total")
    counts = None
    if isinstance(cached, int) and isinstance(total, int):
        counts = f"{cached} of {total} frames cached"
    received = report.get("received_at")
    relative = None
    if isinstance(received, (int, float)):
        relative = format_relative(max(0.0, time.time() - float(received)))
    state = str(report.get("state") or "")
    return {
        "album_name": album.name,
        "state": state,
        "pill_class": _COLLECTION_PILL_CLASS.get(state, "is-neutral"),
        "counts": counts,
        "relative": relative,
    }


# Phase → pill colour for the Devices-card OTA chip. In-progress phases fall
# through to the neutral accent pill.
_OTA_PILL_CLASS = {
    "confirmed": "is-ok",
    "failed": "is-danger",
    "rolled_back": "is-danger",
    "rejected": "is-warn",
}


def _ota_view(report: Any) -> dict[str, Any] | None:
    """Shape the OTA-report chip for the Devices card, or None when the device
    has reported no OTA lifecycle state (contract: "State reporting")."""
    if not isinstance(report, dict) or not report.get("phase"):
        return None
    phase = str(report["phase"])
    return {
        "phase": phase,
        "reason": report.get("reason"),
        "target_fw": report.get("target_fw"),
        "detail": report.get("detail"),
        "pill_class": _OTA_PILL_CLASS.get(phase, "is-accent"),
    }


def _firmware_view(device: Device, parsed: dict[str, Any]) -> dict[str, Any]:
    """Compose the firmware-version chip data for the Devices card.

    Combines the ``fw_version`` value the client reported in its most
    recent heartbeat with the latest known version for its kind from the
    firmware-check module (which polls api.tesserae.ink lazily with a
    60-minute cache).

    The api.tesserae.ink lookup is off by default and only runs when
    ``settings.app.check_firmware_updates`` is enabled; when it's off,
    the chip shows the reported ``fw_version`` with ``state="no_data"``
    so the "update available" pill never fires. Keeps the app's default
    posture "no outbound calls" honest.

    Returns a dict with:
      * ``current``: the reported fw_version, or None.
      * ``latest``: the latest known version, or None.
      * ``state``: one of "current", "outdated", "unknown", "no_data",
        matching :func:`app.firmware_check.compare_versions`.
      * ``release_url``: URL to the release notes for ``latest``.
      * ``notes_headline``: short release summary for ``latest``.
    """
    kind_id = device.kind_of or device.id
    current = parsed.get("fw_version")
    current_str = str(current) if isinstance(current, (str, int, float)) else None
    latest = None
    if _firmware_check_enabled():
        latest = firmware_check_module.latest_for_kind(kind_id, current=current_str or "")
    state = firmware_check_module.compare_versions(current_str, latest)
    return {
        "current": current_str,
        "latest": latest.version if latest else None,
        "state": state,
        "release_url": latest.url if latest else None,
        "notes_headline": latest.notes_headline if latest else None,
    }


def _firmware_check_enabled() -> bool:
    """Whether outbound api.tesserae.ink calls are allowed.

    Reads the master ``settings.app.online_features`` switch (on by default;
    see :func:`app.online.online_enabled`). Firmware lookups, the app update
    check, and the marketplace install count all ride this one switch."""
    from app import online

    return online.online_enabled(current_app.config.get("SETTINGS_STORE"))


def _format_duration(seconds: float) -> str:
    """Tense-neutral duration formatter for smart-sync display, so the
    caller can prefix with 'in' / 'ago' without doubling-up the
    helper's existing 'ago' suffix."""
    if seconds < 5:
        return "a moment"
    if seconds < 60:
        return f"{int(seconds)} s"
    if seconds < 3600:
        return f"{int(seconds / 60)} min"
    if seconds < 86400:
        return f"{int(seconds / 3600)} h"
    return f"{int(seconds / 86400)} d"


def _smart_sync_view(device_id: str) -> dict[str, Any]:
    """Build the smart-sync sub-block for a device card. Always
    returns a dict; the ``reason`` field explains in plain English
    why the device is in its current state so the user can diagnose
    why their schedule's smart-sync dot isn't green yet.

    Pulls from the TelemetryStore (issue #10). The smart-sync
    section is always rendered on the device card so the user can
    see the diagnostic state even before any heartbeat has been
    recorded."""
    telemetry = current_app.config.get("DEVICE_TELEMETRY")
    if telemetry is None:
        return {
            "state": "off",
            "reason": "Smart-sync telemetry store isn't wired (this should not happen in production).",
        }
    entry = telemetry.get(device_id)
    if entry is None or entry.last_heartbeat_at is None:
        return {
            "state": "waiting",
            "reason": (
                "No heartbeat with telemetry recorded yet. Once the device wakes and "
                "publishes its heartbeat, you'll see the prediction and confidence here."
            ),
        }

    now_ts = time.time()
    predicted_rel: str | None = None
    if entry.predicted_next_wake_at is not None:
        delta = entry.predicted_next_wake_at - now_ts
        if delta > 0:
            # format_relative is past-only ("32 s ago"); build the
            # future-tense ourselves so we don't end up with the
            # broken "in 30 s ago" sandwich.
            predicted_rel = f"in {_format_duration(delta)}"
        else:
            predicted_rel = f"{_format_duration(-delta)} ago (overdue)"
    offset_text: str | None = None
    if entry.last_wake_offset_s is not None:
        signed = entry.last_wake_offset_s
        offset_text = f"{'+' if signed >= 0 else ''}{signed}s"

    # Reason text by current state. Goal: tell the user exactly what
    # to do next when they see the dot stuck on warming. The most
    # common failure mode is that the firmware doesn't publish
    # sleep_until / next_sleep_s AND the configured sleep_interval_s
    # doesn't match the firmware's actual cycle, so every wake
    # arrives way off the prediction and confidence never accrues.
    if entry.always_on:
        # Nothing to time a render against: the panel is reachable
        # continuously, so smart sync stops holding fires for it rather
        # than aiming at a wake that never comes.
        reason = (
            "This device stays awake, so there's no wake to sync to. Smart sync "
            "fires immediately for pages bound to it and the frame is waiting on "
            "the device's next poll."
        )
    elif entry.predicted_next_wake_at is None:
        reason = (
            "No prediction possible. The firmware isn't publishing "
            "'sleep_until' or 'next_sleep_s' AND no sleep cycle is configured "
            "for this device. Either update the firmware (handover prompt in "
            "the smart-sync issue), or set a sleep cycle in this device's "
            "settings to match the firmware's actual deep-sleep duration."
        )
    elif entry.is_trusted:
        reason = (
            f"Trusted. The scheduler will JIT-render for this device when a "
            f"bound schedule has smart sync on. Last {entry.consecutive_on_time_wakes} "
            f"consecutive wake(s) landed within ±60s of prediction."
        )
    elif entry.consecutive_on_time_wakes > 0:
        reason = (
            f"Warming up: {entry.consecutive_on_time_wakes}/3 consecutive on-time "
            "wakes. One more on-time wake will flip this device to trusted."
        )
    elif entry.last_wake_offset_s is not None:
        # We made predictions, but none landed within ±60s of the
        # actual wake. Almost always: configured sleep interval !=
        # actual firmware cycle.
        reason = (
            f"Last wake missed the prediction by {entry.last_wake_offset_s}s "
            "(tolerance is ±60s). The configured sleep cycle probably doesn't "
            "match the firmware's actual deep-sleep duration. Adjust the "
            "device's 'sleep interval' setting to match, or have the firmware "
            "publish 'sleep_until' / 'next_sleep_s' for a perfect match."
        )
    else:
        # Edge case: prediction exists but no previous prediction to
        # compare against (this is the very first heartbeat after the
        # store learned of the device). Next wake will start the
        # confidence ramp.
        reason = (
            "Prediction recorded. The next wake will start the confidence "
            "counter; 3 consecutive on-time wakes flip this device to trusted."
        )

    return {
        # An always-on device needs no confidence ramp to be useful to
        # smart sync, so it reads as active rather than sitting on
        # "warming" forever.
        "state": "active" if (entry.always_on or entry.is_trusted) else "warming",
        "is_trusted": entry.is_trusted,
        "confidence": entry.consecutive_on_time_wakes,
        "last_heartbeat_rel": format_relative(max(0.0, now_ts - entry.last_heartbeat_at)),
        "predicted_rel": predicted_rel,
        "predicted_at": entry.predicted_next_wake_at,
        "interval_s": entry.last_sleep_interval_s,
        "offset_text": offset_text,
        "reason": reason,
    }


def _values_for_core(
    section_name: str, fields: list[dict[str, Any]], raw: dict[str, Any]
) -> dict[str, Any]:
    """Translate raw disk values for a core section (app / broker) into
    UI-facing values, applying defaults + masking secrets."""
    out: dict[str, Any] = {}
    for field in fields:
        name = str(field["name"])
        is_secret = bool(field.get("secret"))
        disk = f"{name}_secret" if is_secret else name
        if disk in raw:
            out[name] = SECRET_MASK if is_secret else raw[disk]
        else:
            out[name] = field.get("default", "")
    return out


def _truthy_setting(value: object) -> bool:
    """Loose truthiness for stored switch values (bool, ``"true"``/``"on"``, 1)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _broker_mqtt_url(raw: dict[str, Any]) -> str:
    """The ``mqtt://host:port`` clients should point at, from the saved
    broker config. For the built-in broker a 0.0.0.0 bind resolves to the
    host's LAN IP (what other machines actually connect to) and a loopback
    bind stays 127.0.0.1; for an external broker it's the configured
    host:port. Returns ``-`` when no external host is set.

    Suffixes a hint when the resolved address is a Docker bridge, that
    means we're running inside the official Docker image, the user hasn't
    set ``TESSERAE_HOST_IP``, and the URL would be useless to clients on
    the LAN. Surfaces the same warning the onboarding wizard shows."""
    if _truthy_setting(raw.get("embedded_enabled")):
        bind = str(raw.get("embedded_bind") or "127.0.0.1").strip() or "127.0.0.1"
        port = raw.get("embedded_port") or 1883
        if bind in ("0.0.0.0", "::"):
            host = detect_local_ip()
        elif bind in ("127.0.0.1", "localhost", "::1"):
            host = "127.0.0.1"
        else:
            host = bind
    else:
        host = str(raw.get("host") or "").strip()
        port = raw.get("port") or 1883
        if not host:
            return "-"
    url = f"mqtt://{host}:{port}"
    if docker_bridge_ip_warning() and is_docker_bridge_ip(host):
        url += ", set TESSERAE_HOST_IP"
    return url


_EMBEDDED_BROKER_FIELD_NAMES = frozenset(
    {
        "embedded_enabled",
        "embedded_port",
        "embedded_bind",
        "embedded_username",
        "embedded_password",
    }
)

# Connection fields HA's Configuration tab owns, see ``app.ha_options``.
# Hidden from the Settings card so the user has one place to manage
# them; the card's ``MQTT URL`` meta line still shows the effective
# host:port so they can verify what's resolved.
_HA_MANAGED_BROKER_FIELD_NAMES = frozenset({"host", "port", "username", "password"})


def _broker_fields_with_client_id_hint() -> list[dict[str, Any]]:
    """BROKER_FIELDS with the client_id field's placeholder set to the live
    (auto) client id, so a blank field shows what it actually connects as
    (e.g. ``tesserae-<hostname>`` / ``…-dev``) rather than looking unset.

    Under HA Ingress two groups of fields are stripped:
      * embedded broker fields (the bundled Mosquitto add-on already owns
        1883, see ``transport_wiring._rebuild_transport``).
      * host / port / username / password, managed by HA's Configuration
        tab and applied at every container start by ``app.ha_options``.
    """
    fields = BROKER_FIELDS
    if current_app.config.get("HA_INGRESS_MODE"):
        hidden = _EMBEDDED_BROKER_FIELD_NAMES | _HA_MANAGED_BROKER_FIELD_NAMES
        fields = [f for f in fields if f.get("name") not in hidden]
    transport_obj = current_app.config.get("MQTT_TRANSPORT")
    auto = getattr(transport_obj, "client_id", "") or ""
    if not auto:
        return list(fields)
    return [
        {**f, "placeholder": f"{auto} (auto)"} if f.get("name") == "client_id" else f
        for f in fields
    ]
