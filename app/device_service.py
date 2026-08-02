"""Device-instance lifecycle: create / update-panel / delete.

A thin service layer over the device + renderer registries and the
``data/devices/<id>.json`` instance files. Flask routes stay thin -
parse the form, call one of these, flash + redirect on the result.

Keeping topic derivation and the write → load → clone dance in one
place is the whole point: the Add-device form and the Discovered
one-click register used to inline near-identical logic and had already
drifted (one swapped the kind's topic prefix, the other hardcoded
``tesserae/<id>/status``). Both now go through ``create_instance``.

Transport re-subscription is deliberately NOT done here, it needs
``app.config`` and is the caller's job after a successful mutation, so
this module stays free of Flask.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.device_loader import Device, DeviceRegistry, load_instance_file
from app.quantizer import canonicalise_gamut
from app.renderer_loader import RendererRegistry, clone_for_instances

logger = logging.getLogger(__name__)

# Canonical instance-id rule, shared with the Settings routes. Lowercase,
# starts with a letter, 2–32 chars of [a-z0-9_-].
DEVICE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


@dataclass(frozen=True)
class InstanceResult:
    """Outcome of a lifecycle op. ``error`` is None on success and the
    ``device`` is the affected record (the new one for create/update,
    the removed one for delete)."""

    device: Device | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def device_config_doc(settings: Any, device: Device) -> dict[str, Any]:
    """Per-device config as it stands right now, context-free.

    The single source every config-delivery path reads: the REST status
    response, the MQTT ``config_topic`` publish, and the relay config
    mailbox all serve exactly this document, so a device sees the same
    config regardless of transport. Stored values win; otherwise the
    kind's schema defaults, so firmware always sees a usable block.
    ``settings`` is a ``SettingsStore`` (typed ``Any`` to keep this module
    free of a store import cycle).
    """
    section = settings.get_section("devices") or {}
    if not isinstance(section, dict):
        return {}
    stored = section.get(device.id)
    if isinstance(stored, dict):
        # Copy so the caller can mutate without disturbing the store.
        out = dict(stored)
    else:
        out = {}
        for key, spec in (device.config_schema or {}).items():
            if isinstance(spec, dict) and "default" in spec:
                out[key] = spec["default"]
    # always_on (touch-v3): a per-device Tesserae setting delivered like the
    # sleep interval. Default false so the firmware always sees the field; a
    # stored true (set only for can_stay_awake devices) overrides.
    out.setdefault("always_on", False)
    return out


def _kind_uses_access_token(kind: Device) -> bool:
    """True for kinds whose protocol uses an HTTP access token instead
    of (or in addition to) an MQTT status/config topic. Currently just
    TRMNL clients; extracted as a helper so a new HTTP-polled kind can
    opt in by appearing in this list rather than threading a new
    manifest field through the loader."""
    return kind.id == "trmnl_client"


# Alphabet for short access tokens. Lowercase letters plus 2–9, with
# the visually ambiguous characters (0 / 1 / i / l / o) removed so a
# user reading the modal and typing on a soft keyboard doesn't have to
# guess which character is which. 30 characters → 30^5 ≈ 24 million
# combinations.
_TOKEN_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
_TOKEN_LENGTH = 5

# Native TRMNL firmware stores its api_key in flash and never asks the
# user to retype it, so we don't need a typeable token length there.
# A 20-char alphanumeric matches Terminus's defaulter (full entropy,
# ~120 bits) and is what the official BYOS contract uses for
# server-minted tokens. The 5-char human-typeable form stays for the
# KOReader path (user types it on the Kindle on-screen keyboard).
_NATIVE_TOKEN_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_NATIVE_TOKEN_LENGTH = 20

# TRMNL convention: a six-character uppercase identifier the device
# can show on its setup screen / about page, picked from a typeable
# alphabet that omits ambiguous glyphs (0/O, 1/I/L). Stable across
# token rotations; lives on the device manifest alongside
# ``access_token`` and gets returned in /api/setup + /api/display
# so any TRMNL-compatible firmware reads the same field.
_FRIENDLY_ID_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_FRIENDLY_ID_LENGTH = 6


def generate_native_access_token(devices: DeviceRegistry) -> str:
    """Generate a high-entropy 20-char alphanumeric access token for a
    native TRMNL device (no user-typed-on-Kindle constraint).

    Matches Terminus's defaulter (SecureRandom.alphanumeric(20)). The
    typeable 5-char form (``generate_access_token``) stays for the
    KOReader path where the user keys the token in by hand."""
    import secrets

    existing = {
        d.manifest.get("access_token")
        for d in devices.all()
        if isinstance(d.manifest.get("access_token"), str)
    }
    for _ in range(64):
        token = "".join(secrets.choice(_NATIVE_TOKEN_ALPHABET) for _ in range(_NATIVE_TOKEN_LENGTH))
        if token not in existing:
            return token
    raise RuntimeError(
        f"could not generate a unique {_NATIVE_TOKEN_LENGTH}-char native token after 64 attempts"
    )


def generate_friendly_id(devices: DeviceRegistry) -> str:
    """Generate a TRMNL-style human-readable device id (e.g. ``7B3X9K``).

    Uniqueness check matches ``generate_access_token`` so two devices
    can't share the same id, the friendly_id surfaces to the user as
    the 'sticker' identifier on TRMNL's setup screens."""
    import secrets

    existing = {
        d.manifest.get("friendly_id")
        for d in devices.all()
        if isinstance(d.manifest.get("friendly_id"), str)
    }
    for _ in range(64):
        candidate = "".join(
            secrets.choice(_FRIENDLY_ID_ALPHABET) for _ in range(_FRIENDLY_ID_LENGTH)
        )
        if candidate not in existing:
            return candidate
    raise RuntimeError(
        f"could not generate a unique {_FRIENDLY_ID_LENGTH}-char friendly id after 64 attempts"
    )


def generate_access_token(devices: DeviceRegistry) -> str:
    """Generate a short access token that isn't already in use.

    Five characters from a 30-char typeable alphabet gives ~25 bits
    of entropy, fine for a Tesserae that's only reachable on the
    LAN (homelab + firewall + Tailscale are the typical setup), NOT
    fine for a publicly-exposed instance. The tradeoff is deliberate:
    TRMNL clients are paired by entering the token on a Kindle
    on-screen keyboard or button-by-button on the native hardware,
    and a 32-hex-character token there is brutal. If you're putting
    Tesserae on the public internet, stack additional access control
    (reverse-proxy auth, Tailscale, etc.) on top, the token alone
    is not sufficient at this length.

    Uniqueness within the registry is checked so a regenerate can't
    silently collide with an existing device's token (a 1-in-24M
    chance per attempt, but the loop costs nothing and removes the
    foot-gun)."""
    import secrets

    existing = {
        d.manifest.get("access_token")
        for d in devices.all()
        if isinstance(d.manifest.get("access_token"), str)
    }
    for _ in range(64):
        token = "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))
        if token not in existing:
            return token
    # Astronomically unlikely (would need millions of TRMNL devices
    # already registered). Raise loudly rather than recurse, the
    # caller will see the error and bump _TOKEN_LENGTH.
    raise RuntimeError(
        f"could not generate a unique {_TOKEN_LENGTH}-char access token after 64 attempts"
    )


def derive_topic(kind_topic: str, new_id: str, *, suffix: str) -> str:
    """Derive an instance topic from the kind's by swapping the prefix
    segment: ``tesserae/<kind_prefix>/status`` → ``tesserae/<id>/status``.
    Falls back to ``tesserae/<id>/<suffix>`` if the kind's topic doesn't
    fit the expected shape (so an odd kind topic can't make two
    instances collide)."""
    parts = kind_topic.split("/")
    if len(parts) >= 2 and parts[0] == "tesserae":
        parts[1] = new_id
        return "/".join(parts)
    return f"tesserae/{new_id}/{suffix}"


def renderer_id_for_format(
    renderers: RendererRegistry, kind: Any, wire_format: str | None
) -> str | None:
    """Resolve a client-declared wire format (``"png"`` / ``"bmp"``) to
    one of the kind's renderer ids by matching the renderer's file
    extension.

    Used by the discover-and-register flow so a memory-constrained
    CircuitPython client can ask for the uncompressed-BMP renderer.
    Returns ``None`` (leave the kind default) when the format is empty,
    unknown, or the kind has no renderer with that extension."""
    if not wire_format:
        return None
    fmt = wire_format.strip().lower().lstrip(".")
    if not fmt:
        return None
    for rid in getattr(kind, "renderer_ids", []):
        renderer = renderers.get(rid)
        if renderer is not None and renderer.extension == fmt:
            return str(rid)
    return None


def _drop_clones(renderers: RendererRegistry, instance_id: str) -> None:
    """Remove any renderer clones whose resolved device is this instance.

    Match only clone records (ids of the form ``<base>__<instance>``),
    never a base renderer. When an instance id collides with a base
    renderer's topic prefix (e.g. a device literally named ``pi_bin``),
    the base ``pi_bin`` renderer's ``device`` is also ``pi_bin``, so a
    bare ``device == instance_id`` match would delete the base itself.
    ``clone_for_instances`` then has no base to clone from and the device
    is left with zero renderers until the next process restart rebuilds
    the registry from disk. That surfaced as the Calibration tab's tone &
    dither block vanishing after every combined-form save (issue #52)."""
    for rid in list(renderers.renderers):
        if "__" in rid and renderers.renderers[rid].device == instance_id:
            renderers.renderers.pop(rid, None)


def create_instance(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    kind_id: str,
    name: str = "",
    panel_overrides: dict[str, Any] | None = None,
    orientation: str | None = None,
    access_token: str | None = None,
    mac: str | None = None,
    api_key_strength: str = "typeable",
    transport: str | None = None,
    renderer_id: str | None = None,
    relay_frame_key: str | None = None,
) -> InstanceResult:
    """Validate, persist, load, and clone-renderers for a new instance.

    ``panel_overrides`` (``{"w":…, "h":…}``) layer on top of the kind's
    panel; ``orientation`` is one of the 4-way display orientations
    (``landscape`` / ``landscape_flipped`` / ``portrait`` /
    ``portrait_flipped``). Portrait variants swap w/h once so the stored
    canvas matches the chosen aspect; the ``_flipped`` part is a
    renderer-side 180° turn, not a dims change.

    ``renderer_id`` pins the instance to one of its kind's renderers when
    the kind lists more than one (e.g. ``circuitpython_generic`` offering
    both ``circuitpython_png`` and ``circuitpython_bmp``). Ignored when it
    isn't one of the kind's renderers. When unset, the kind's first
    renderer is used, so single-renderer kinds are unaffected. Caller
    rebuilds the transport on success."""
    instance_id = instance_id.strip().lower()
    if not DEVICE_ID_RE.match(instance_id):
        return InstanceResult(
            None,
            "Device id must be 2-32 chars, start with a letter, and use only "
            "lowercase letters / digits / underscore / hyphen.",
        )
    if instance_id in devices.devices:
        return InstanceResult(None, f"Device id {instance_id!r} is already in use.")
    kind = devices.get(kind_id)
    if kind is None or kind.kind_of is not None:
        return InstanceResult(None, f"Unknown device kind {kind_id!r}.")

    # Normalise transport: "rest" | "mqtt" | None (=> "mqtt" default).
    # Stored on the manifest so the push pipeline knows whether to
    # publish to MQTT or skip in favour of the REST poll path. Only
    # written when explicitly "rest" so existing v0.51-and-earlier
    # instances continue to read as MQTT without any field at all.
    normalised_transport: str | None = None
    forced_transport = transport.strip().lower() if isinstance(transport, str) else ""
    if forced_transport in ("rest", "relay"):
        # "relay" is an out-of-band transport like "push", but chosen
        # per-instance (a remote panel paired through the cloud relay). It
        # skips the broker the way "rest" does and mints its own token.
        normalised_transport = forced_transport
    else:
        # A kind can declare its own transport (OpenDisplay-via-HA is
        # "push"; the OpenDisplay bridge kind is "rest"). Inherit it when
        # the caller didn't force one, so manually-added instances match
        # the kind instead of falling back to the MQTT default.
        kind_transport = str(kind.manifest.get("transport") or "").strip().lower()
        if kind_transport in ("rest", "push"):
            normalised_transport = kind_transport

    manifest: dict[str, Any] = {
        "id": instance_id,
        "kind": kind_id,
        "name": name.strip() or f"{kind.name} ({instance_id})",
    }
    if normalised_transport in ("rest", "push", "relay"):
        manifest["transport"] = normalised_transport
    # Pin a renderer when the kind offers a choice (multi-renderer kinds
    # like circuitpython_generic). Only recorded when it names one of the
    # kind's renderers, so a bad value falls back to the kind's default
    # rather than orphaning the instance with no renderer at all.
    if renderer_id and renderer_id in getattr(kind, "renderer_ids", []):
        manifest["renderer_id"] = renderer_id
    # Kinds that don't speak MQTT (e.g. HTTP-polled TRMNL) declare no
    # status/config topic at all. REST instances ALSO skip these: the
    # status/config flows happen over /api/v1/device/<id>/*, not topics.
    # Keep the topics derivable on the manifest so a user can convert a
    # REST instance back to MQTT later without re-registering.
    if kind.status_topic:
        manifest["status_topic"] = derive_topic(kind.status_topic, instance_id, suffix="status")
    if kind.config_topic:
        manifest["config_topic"] = derive_topic(kind.config_topic, instance_id, suffix="config")

    panel = dict(kind.panel or {})
    if panel_overrides:
        panel.update(panel_overrides)
    _apply_orientation(panel, orientation)
    if panel:
        manifest["panel"] = panel

    # TRMNL-style clients identify themselves by a token rather than a
    # MAC or topic. Two paths:
    #
    #  * Manual add: ``access_token`` not supplied → generate one. The
    #    user copies the new token from the reveal modal into their
    #    client config.
    #  * Discovered register: caller hands us the token the client is
    #    already polling with, preserve it so the user doesn't have
    #    to update the client config after registering.
    if _kind_uses_access_token(kind) or normalised_transport in ("rest", "relay"):
        # api_key_strength="native" mints the high-entropy 20-char
        # alphanumeric used by the official Terminus BYOS contract,
        # safe to use when the device stores its key in flash. The
        # default "typeable" keeps the 5-char form for the KOReader
        # path where the user types the token on the Kindle's
        # on-screen keyboard. REST devices use the native strength
        # because they store the token in NVS/flash + match it via
        # bearer header; no human typing involved after pairing.
        effective_strength = api_key_strength
        if normalised_transport in ("rest", "relay") and not _kind_uses_access_token(kind):
            effective_strength = "native"
        if access_token:
            manifest["access_token"] = access_token
        elif effective_strength == "native":
            manifest["access_token"] = generate_native_access_token(devices)
        else:
            manifest["access_token"] = generate_access_token(devices)
        # TRMNL clients expect a six-character ``friendly_id`` they can
        # show on the setup screen as a stable, human-readable id.
        # Skip for non-TRMNL REST devices, the field has no protocol
        # meaning for them.
        if _kind_uses_access_token(kind):
            manifest["friendly_id"] = generate_friendly_id(devices)
        # MAC address (when auto-provisioning from /api/setup) becomes
        # the device's primary identity for /api/display auth, matching
        # the Terminus BYOS model. REST devices include it when
        # firmware reports it so the admin UI can show "MAC = ..."
        # alongside battery / IP.
        if mac:
            manifest["mac"] = mac.strip()

    # Relay panels carry their per-panel frame key (base64url of the 32-byte
    # X25519+HKDF shared secret) on the manifest, stored like access_token so
    # the publisher can seal each frame. Only written for relay transport.
    if normalised_transport == "relay" and relay_frame_key:
        manifest["relay_frame_key"] = relay_frame_key

    data_root.mkdir(parents=True, exist_ok=True)
    inst_file = data_root / f"{instance_id}.json"
    if inst_file.exists():
        return InstanceResult(None, f"An instance file at {inst_file.name} already exists.")
    inst_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    device = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if device is None:
        last_err = devices.errors[-1] if devices.errors else None
        inst_file.unlink(missing_ok=True)
        return InstanceResult(None, last_err.message if last_err else "unknown error")
    clone_for_instances(renderers, devices)
    return InstanceResult(device)


# Map of esp32_client panel dims → firmware-native (w, h). Used by the
# startup migration to backfill ``panel.native_w / panel.native_h`` on
# instance manifests that predate the v0.20.x PanelPreset refactor.
#
# Why is this hardcoded here instead of inferred from PANEL_PRESETS?
# Inky 13.3" (1600×1200 landscape-native) and Waveshare 13.3" Spectra 6
# (1200×1600 portrait-native) share dims, so a generic "match by
# dims" lookup is ambiguous for ESP32 devices. Restricting the lookup
# to esp32_client and hard-coding the two ESP32 panels that ship with
# Tesserae makes the inference deterministic.
_ESP32_NATIVE_BY_DIMS: dict[tuple[int, int], tuple[int, int]] = {
    (1200, 1600): (1200, 1600),  # Waveshare 13.3" Spectra 6 (portrait-native)
    (1600, 1200): (1200, 1600),  # ditto, mounted sideways
    (800, 480): (800, 480),  # Waveshare 7.3" PhotoPainter / 7.5" / Inky 7.3"
    (480, 800): (800, 480),  # ditto, mounted sideways
}


def backfill_native_panel_dims(data_root: Path) -> list[str]:
    """One-shot migration: add ``panel.native_w / panel.native_h`` to
    ESP32 instance manifests that were created before the v0.20.x
    PanelPreset refactor.

    Without these fields, ``device_panel`` falls back to a dims-only
    preset lookup that gets the wrong row stride on Waveshare 13.3"
    devices (it picks the Inky 13.3" preset, which shares dims but is
    landscape-native). The esp32_bin renderer then packs at the wrong
    stride and the panel paints a distorted, tile-looking frame.

    Idempotent: manifests already carrying both keys are left alone.
    Returns the list of instance ids that were patched, for logging.
    Non-esp32 devices are skipped because pi_bin / pi_png / trmnl_png
    don't read native dims."""
    patched: list[str] = []
    if not data_root.exists():
        return patched
    for path in sorted(data_root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if raw.get("kind") != "esp32_client":
            continue
        panel = raw.get("panel")
        if not isinstance(panel, dict):
            continue
        if "native_w" in panel and "native_h" in panel:
            continue
        try:
            w = int(panel.get("w", 0))
            h = int(panel.get("h", 0))
        except (TypeError, ValueError):
            continue
        native = _ESP32_NATIVE_BY_DIMS.get((w, h))
        if native is None:
            continue
        panel["native_w"], panel["native_h"] = native
        raw["panel"] = panel
        path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        patched.append(str(raw.get("id") or path.stem))
    return patched


def relocate_orphan_instance_files(*, data_root: Path, device_data_root: Path) -> list[str]:
    """One-shot migration: move stray instance manifests out of the data
    root and into ``data/devices/`` where the loader scans.

    A REST ``/register`` before this fix wrote the manifest to
    ``data/<id>.json`` instead of ``data/devices/<id>.json`` (issue #127).
    The file never loaded on restart (the device 404'd) and blocked
    re-pairing with an "instance file already exists" 400. Nothing else
    keeps ``*.json`` directly at the data root, so any file there is such
    an orphan.

    Skips a file when a manifest with the same name already exists in
    ``data/devices/`` (a re-pair created a fresh one) so the newer copy
    isn't clobbered; the stray is left in place for the operator to
    inspect. Idempotent. Returns the ids that were moved, for logging."""
    moved: list[str] = []
    if not data_root.exists():
        return moved
    device_data_root.mkdir(parents=True, exist_ok=True)
    for path in sorted(data_root.glob("*.json")):
        if not path.is_file():
            continue
        target = device_data_root / path.name
        if target.exists():
            logger.warning(
                "device migration: leaving orphan instance file %s in place "
                "(a manifest already exists at %s)",
                path,
                target,
            )
            continue
        path.rename(target)
        moved.append(path.stem)
    return moved


def update_instance_panel(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    w: int,
    h: int,
    orientation: str,
    gamut: str | None = None,
    underscan: int | None = None,
    icon: str | None = None,
) -> InstanceResult:
    """Patch an instance's panel block on disk + reload in place.

    w/h are stored exactly as given (the edit form's JS already swaps
    them live when the aspect flips). ``orientation`` is one of the
    4-way display orientations; the ``_flipped`` part is a renderer-side
    180° turn, the landscape/portrait part is the canvas aspect.
    ``gamut`` is the panel's colour gamut (see ``PANEL_GAMUTS``); the .bin
    packer keys its palette off it. ``underscan`` insets the frame by N
    pixels per edge to clear a mat/bezel. ``icon`` is a top-level Phosphor
    slug for device pickers. ``None`` leaves any of these untouched."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}.")
    if w < 1 or h < 1:
        return InstanceResult(None, "Panel width and height must be at least 1px.")
    o = orientation.strip().lower()
    if o not in ("landscape", "landscape_flipped", "portrait", "portrait_flipped"):
        o = "landscape"

    # Normalise so portrait → tall canvas, landscape → wide, regardless
    # of which dims the form happened to submit. The settings page has
    # client-side JS that swaps w/h when the orientation dropdown moves
    # between portrait and landscape variants, but a hand-crafted POST
    # (or the JS not having fired before submit) can land here with a
    # mismatch. The renderers derive the rotation from ``panel.w <
    # panel.h``, so a mismatch silently keeps the panel rendering at
    # the wrong orientation, exactly the bug the user hits when
    # "even after setting rotation" the frame stays landscape.
    is_portrait = o.startswith("portrait")
    if (is_portrait and w > h) or (not is_portrait and h > w):
        w, h = h, w

    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return InstanceResult(None, f"Couldn't read {inst_file.name}: {err}")
    panel_block = dict(raw.get("panel") or {})
    panel_block["w"], panel_block["h"], panel_block["orientation"] = w, h, o
    if gamut is not None:
        # Normalise through the canonical resolver rather than clamping to the
        # .bin packer targets: that dropped metadata gamuts the packer doesn't
        # own (``mono``, ``gray_4``, ``gray_16``, ``rgb*``) to ``waveshare_e6``,
        # which would silently turn a grayscale panel into a fake 6-colour one
        # on any panel-settings edit. ``canonicalise_gamut`` maps chemistry
        # aliases, preserves accepted values, and still defaults garbage to a
        # safe packer target.
        panel_block["gamut"] = canonicalise_gamut(gamut)
    if underscan is not None:
        # Clamp so the inset can't swallow the panel (keep at least half).
        panel_block["underscan"] = max(0, min(underscan, min(w, h) // 2 - 1))
    raw["panel"] = panel_block
    if icon is not None:
        # Top-level (device-wide), not a panel attribute. Blank clears the
        # override so the device falls back to its kind's default icon.
        cleaned = icon.strip()
        if cleaned:
            raw["icon"] = cleaned
        else:
            raw.pop("icon", None)
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error")
    clone_for_instances(renderers, devices)
    return InstanceResult(reloaded)


def update_instance_renderer(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    wire_format: str | None,
) -> tuple[InstanceResult, bool]:
    """Repin an instance to the renderer matching a client-declared wire
    format (``"png"`` / ``"bmp"``), rewritten on disk + reloaded in place.

    Lets a device switch its frame format after it's already registered:
    the client re-declares ``format`` on ``/register`` or ``/discover`` and
    the server moves it to the matching renderer of its kind (e.g.
    ``circuitpython_png`` -> ``circuitpython_bmp``) without a delete +
    re-create. A format that's empty, unknown to the kind, or already
    active is a no-op, not an error.

    Returns ``(result, changed)``. ``changed`` is True only when the
    renderer actually moved, so the caller can invalidate the device's
    now-stale render (it was produced by the old renderer, in the old
    format) rather than serving it to a client that asked for the other
    format."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}."), False
    kind = devices.get(device.kind_of)
    target = renderer_id_for_format(renderers, kind, wire_format)
    if not target or target == device.manifest.get("renderer_id"):
        return InstanceResult(device), False  # empty / unknown / already active

    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return InstanceResult(None, f"Couldn't read {inst_file.name}: {err}"), False
    raw["renderer_id"] = target
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error"), False
    clone_for_instances(renderers, devices)
    return InstanceResult(reloaded), True


def kind_protocol(kind: Device) -> str:
    """The wire protocol a kind speaks. Hardware-catalog SKUs carry it
    under ``_catalog_entry.protocol``; a folder-defined protocol kind is
    its own protocol (its id)."""
    ce = kind.manifest.get("_catalog_entry")
    if isinstance(ce, dict) and isinstance(ce.get("protocol"), str) and ce["protocol"]:
        return str(ce["protocol"])
    return kind.id


def update_instance_kind(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    kind_id: str | None,
) -> tuple[InstanceResult, bool]:
    """Move a registered instance to the kind its firmware now declares,
    rewritten on disk + reloaded in place.

    Heals the stale-kind case: a device that first registered under a
    generic protocol kind (``esp32_client``) later comes back running a
    board build that declares its hardware-catalog SKU
    (``seeed_reterminal_e1004``). Re-registration is otherwise idempotent
    and would pin the instance to the old kind forever, which breaks
    per-kind OTA rollouts (release descriptors are keyed by the SKU kind
    and the firmware rejects a descriptor for any other kind).

    Restricted to kinds that share the current kind's wire protocol, so
    a heal can only refine *which board* on the same wire contract, never
    move a device across protocols. An empty, unknown, cross-protocol or
    already-current kind is a no-op, not an error.

    Returns ``(result, changed)``. ``changed`` is True only when the
    instance actually moved, so the caller can invalidate the device's
    cached render (the new kind may carry different panel dims or a
    different renderer)."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}."), False
    wanted = (kind_id or "").strip()
    if not wanted or wanted == device.kind_of:
        return InstanceResult(device), False
    current_kind = devices.get(device.kind_of)
    new_kind = devices.get(wanted)
    if (
        current_kind is None
        or new_kind is None
        or new_kind.kind_of is not None
        or kind_protocol(new_kind) != kind_protocol(current_kind)
    ):
        return InstanceResult(device), False

    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return InstanceResult(None, f"Couldn't read {inst_file.name}: {err}"), False
    raw["kind"] = wanted
    # A renderer pick made under the old kind that the new kind doesn't
    # offer would be silently ignored at load; drop it so the file
    # doesn't carry a stale field forever.
    if raw.get("renderer_id") not in new_kind.renderer_ids:
        raw.pop("renderer_id", None)
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error"), False
    clone_for_instances(renderers, devices)
    logger.info("Device %s kind healed: %s -> %s", instance_id, device.kind_of, wanted)
    return InstanceResult(reloaded), True


def update_instance_quiet_hours(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    enabled: bool,
    start: str | None,
    end: str | None,
) -> InstanceResult:
    """Patch a registered instance's ``quiet_hours`` block on disk and
    hot-reload it in place. Empty or invalid times disable the
    override (the helper resolver treats both as "no window").

    The block on disk is the shape :mod:`app.quiet_hours` reads:
    ``{enabled: bool, start: 'HH:MM', end: 'HH:MM'}``. When the user
    clears every field we drop the block entirely so the device falls
    back to the app-level setting on next reload."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}.")

    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return InstanceResult(None, f"Couldn't read {inst_file.name}: {err}")

    clean_start = (start or "").strip()
    clean_end = (end or "").strip()
    if not enabled and not clean_start and not clean_end:
        # Fully cleared, drop the block entirely so the next reload
        # sees a manifest with no override and uses the app setting.
        raw.pop("quiet_hours", None)
    else:
        raw["quiet_hours"] = {
            "enabled": bool(enabled),
            "start": clean_start,
            "end": clean_end,
        }
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error")
    clone_for_instances(renderers, devices)
    return InstanceResult(reloaded)


def update_instance_battery_offset(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    mv: int,
    pct: int,
) -> InstanceResult:
    """Patch a registered instance's ``battery_offset`` block on disk
    and hot-reload it in place. Both values are signed (positive bumps
    the displayed reading up, negative bumps it down). A ``(0, 0)``
    submission drops the block entirely so the manifest stays clean
    when the user clears the override.

    Bounds: mV is clamped to ``±2000`` (the realistic ADC drift range
    for any LiPo / LiFePO4 / LiHV cell tesserae targets); pct is
    clamped to ``±100`` (the only valid range for a percent
    adjustment). Out-of-range submissions saturate at the boundary
    rather than rejecting, so a typoed "350" instead of "35" still
    produces a sensible result instead of erroring."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}.")

    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return InstanceResult(None, f"Couldn't read {inst_file.name}: {err}")

    mv_clamped = max(-2000, min(2000, int(mv)))
    pct_clamped = max(-100, min(100, int(pct)))
    if mv_clamped == 0 and pct_clamped == 0:
        raw.pop("battery_offset", None)
    else:
        raw["battery_offset"] = {"mv": mv_clamped, "pct": pct_clamped}
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error")
    clone_for_instances(renderers, devices)
    return InstanceResult(reloaded)


def delete_instance(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    instance_id: str,
) -> InstanceResult:
    """Remove an instance: delete its file, drop its registry record and
    renderer clones. Built-in kinds are refused."""
    device = devices.get(instance_id)
    if device is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}.")
    if device.kind_of is None:
        return InstanceResult(None, f"Cannot delete built-in device kind {instance_id!r}.")
    if device.path.exists():
        device.path.unlink()
    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    return InstanceResult(device)


def _apply_orientation(panel: dict[str, Any], orientation: str | None) -> None:
    """Stamp a 4-way display orientation onto a panel dict. Portrait
    variants swap w/h once so the stored canvas is tall; the ``_flipped``
    part is a renderer-side 180° turn and doesn't touch dims."""
    o = (orientation or "").strip().lower()
    if o not in ("landscape", "landscape_flipped", "portrait", "portrait_flipped"):
        return
    panel["orientation"] = o
    is_portrait = o.startswith("portrait")
    w, h = panel.get("w"), panel.get("h")
    # Normalise so portrait → tall, landscape → wide, regardless of the
    # dims the override came in as.
    if w and h and ((is_portrait and w > h) or (not is_portrait and h > w)):
        panel["w"], panel["h"] = h, w
