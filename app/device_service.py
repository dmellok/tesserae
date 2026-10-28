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

import functools
import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from app.device_loader import Device, DeviceRegistry, colour_depths, load_instance_file
from app.quantizer import canonicalise_gamut, refine_reported_gamut
from app.renderer_loader import RendererRegistry, clone_for_instances

logger = logging.getLogger(__name__)

# Instance files are rewritten whole (read, patch, write back) from request
# threads and from background ones: TRMNL size adoption, a firmware-driven
# renderer switch, a transport adopting the size a device reports.
# Every read-modify-write of an instance file, and every delete, holds this
# lock, so two writers can't interleave and drop each other's change and a
# writer that read the file before a delete can't bring the device back.
INSTANCE_FILE_LOCK = threading.RLock()

_P = ParamSpec("_P")
_R = TypeVar("_R")


def holds_instance_file_lock(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run ``fn`` holding :data:`INSTANCE_FILE_LOCK`."""

    @functools.wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with INSTANCE_FILE_LOCK:
            return fn(*args, **kwargs)

    return wrapper


# Canonical instance-id rule, shared with the Settings routes. Lowercase,
# starts with a letter, 2–32 chars of [a-z0-9_-].
DEVICE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

# Always-on defaults. Mirrored in the ESP32 kinds' config_schema; kept
# here too so a kind whose schema predates the fields still delivers a
# complete config block, on the same "firmware always sees the field"
# rule ``always_on`` itself follows.
DEFAULT_AWAKE_POLL_S = 15
AWAKE_POLL_MIN_S = 5
AWAKE_POLL_MAX_S = 300


def awake_poll_interval_s(stored: Any) -> int | None:
    """The poll cadence a device set to stay awake is actually on, or
    ``None`` when it deep-sleeps like everything else.

    ``stored`` is the device's ``settings.devices.<id>`` block. An
    always-on panel is not on the sleep grid at all, so every surface that
    asks "how long until this device comes back" (``next_poll_s``, the
    wake prediction, the mirror page's auto-refresh) has to read the
    awake cadence instead of ``sleep_interval_s``, which is why this is
    shared rather than inlined three times.

    The result is clamped: the value reaches here from a settings file
    that a user may have hand-edited past the form's bounds, and every
    caller turns it straight into a poll interval.
    """
    if not isinstance(stored, dict) or stored.get("always_on") is not True:
        return None
    raw = stored.get("awake_poll_s", DEFAULT_AWAKE_POLL_S)
    try:
        poll = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_AWAKE_POLL_S
    return max(AWAKE_POLL_MIN_S, min(poll, AWAKE_POLL_MAX_S))


# -- per-kind hooks ----------------------------------------------------------
#
# A device kind that has rules of its own (a protocol where one physical
# device may be bound to only one Tesserae device, say) registers a
# :class:`KindHooks` here, from its own module. The settings routes, the Home
# Assistant config setter and the add / register / remove flows then call the
# functions below for every device and never name the kind.

# Held around a device's config check + store, so two saves can't both pass a
# check that only one of them should, and so a background writer that must
# agree with the checks (the Visionect auto-bind) can take it too.
CONFIG_SAVE_LOCK = threading.RLock()


@dataclass(frozen=True)
class KindHooks:
    """Callbacks for the devices (and kinds) ``applies_to`` accepts. Every
    callback is optional. The ``store`` is the settings store.

    * ``config_conflict(devices, store, device, values, exclude)``: why
      ``values`` can't be stored for ``device`` (a device or a kind), or None.
      ``exclude`` is the id of the device being saved.
    * ``config_saved(store, device, values, previous)``: ``values`` were
      stored; ``previous`` is the device's stored config from before.
    * ``device_added(store, device)``: a device of this kind was just created.
    * ``discovered_config(parsed)``: the config values a discovery entry
      (its parsed payload) announces for the device registered from it.
    * ``device_removed(device)``: the device was deleted.
    """

    applies_to: Callable[[Any], bool]
    config_conflict: (
        Callable[[DeviceRegistry, Any, Any, dict[str, Any], str], str | None] | None
    ) = None
    config_saved: Callable[[Any, Any, dict[str, Any], dict[str, Any]], None] | None = None
    device_added: Callable[[Any, Any], None] | None = None
    discovered_config: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    device_removed: Callable[[Any], None] | None = None


_KIND_HOOKS: dict[str, KindHooks] = {}


def register_kind_hooks(name: str, hooks: KindHooks) -> None:
    """Register (or replace) the hooks called ``name``."""
    _KIND_HOOKS[name] = hooks


def _hooks_for(device: Any) -> list[KindHooks]:
    return [h for h in list(_KIND_HOOKS.values()) if h.applies_to(device)]


def config_conflict(
    devices: DeviceRegistry,
    store: Any,
    device: Any,
    values: dict[str, Any],
    *,
    exclude: str = "",
) -> str | None:
    """Why ``values`` can't be stored for ``device`` (a device or a kind), or
    None. For an early refusal before anything is created; ``save_device_config``
    checks again, atomically."""
    for hook in _hooks_for(device):
        if hook.config_conflict is not None:
            problem = hook.config_conflict(devices, store, device, values, exclude)
            if problem:
                return problem
    return None


def save_device_config(
    store: Any,
    devices: DeviceRegistry,
    device: Any,
    values: dict[str, Any],
    fields: list[dict[str, Any]],
) -> str | None:
    """Store a device's validated config values, as
    ``store.update_for_namespace("devices", ...)`` does, after its kind's
    ``config_conflict`` hooks agree, atomically with that check. Returns why
    nothing was stored, or None."""
    with CONFIG_SAVE_LOCK:
        problem = config_conflict(devices, store, device, values, exclude=device.id)
        if problem:
            return problem
        hooks = _hooks_for(device)
        previous: dict[str, Any] = {}
        if any(h.config_saved for h in hooks):
            stored = (store.get_section("devices") or {}).get(device.id)
            previous = dict(stored) if isinstance(stored, dict) else {}
        store.update_for_namespace("devices", device.id, values, fields)
        for hook in hooks:
            if hook.config_saved is not None:
                _run_hook(hook.config_saved, store, device, values, previous)
    return None


def device_added(store: Any, device: Any) -> None:
    """Tell the device's kind hooks it was just created."""
    for hook in _hooks_for(device):
        if hook.device_added is not None:
            _run_hook(hook.device_added, store, device)


def discovered_config(kind: Any, parsed: dict[str, Any]) -> dict[str, Any]:
    """The config values a discovery entry announces for a ``kind`` device."""
    out: dict[str, Any] = {}
    for hook in _hooks_for(kind):
        if hook.discovered_config is not None:
            out.update(hook.discovered_config(parsed))
    return out


def _run_hook(fn: Callable[..., None], *args: Any) -> None:
    """A hook that runs after the fact must not undo what it follows."""
    try:
        fn(*args)
    except Exception:
        logger.exception("device kind hook %s failed", getattr(fn, "__name__", fn))


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
    # The cadence that pairs with it. Delivered unconditionally for the same
    # reason: firmware reads one config block and shouldn't have to carry a
    # default for a field the server knows the answer to.
    out.setdefault("awake_poll_s", DEFAULT_AWAKE_POLL_S)
    _resolve_beep_pattern(out, device)
    return out


# Buzzer tones (#258), as the notes themselves: ``freq:ms`` steps, comma
# separated. The named tones live here rather than in firmware so retuning
# one is a settings change instead of a firmware release, and so an operator
# can write their own without waiting for a build. Frequencies sit where a
# small piezo is loudest; the device clamps anything silly.
BEEP_PATTERNS: dict[str, str] = {
    "click": "2400:12",
    "beep": "2000:60",
    "chirp": "1800:30,2600:40",
    "low": "1200:60",
    "double": "2000:40,0:60,2000:40",
    "rising": "1500:40,2000:40,2600:60",
}
BEEP_PATTERN_FALLBACK = BEEP_PATTERNS["beep"]


def _resolve_beep_pattern(out: dict[str, Any], device: Device) -> None:
    """Turn the stored tone choice into the notes the panel plays.

    The device is never sent a tone NAME: names are a server-side
    convenience, and a firmware that knew them would have to ship a new
    build to change one. ``custom`` hands over whatever the operator typed,
    validated by the device kind before it was stored.
    """
    if "beep_tone" not in (device.config_schema or {}):
        return
    tone = str(out.get("beep_tone") or "beep")
    if tone == "custom":
        pattern = str(out.get("beep_pattern") or "").strip() or BEEP_PATTERN_FALLBACK
    else:
        pattern = BEEP_PATTERNS.get(tone, BEEP_PATTERN_FALLBACK)
    out["beep_pattern"] = pattern


def _kind_uses_access_token(kind: Device) -> bool:
    """True for kinds whose protocol uses an HTTP access token instead
    of (or in addition to) an MQTT status/config topic. Currently just
    TRMNL clients and the Bloomin8 Canvas; extracted as a helper so a
    new HTTP-polled kind can opt in by appearing in this list rather than
    threading a new manifest field through the loader.

    Matched on the kind's wire protocol, so a hardware-catalog SKU on the
    TRMNL protocol (``trmnl_x``, ``seeed_xiao_75``) mints a token the same
    way the bare ``trmnl_client`` kind does; without one, ``app.trmnl_api``
    has nothing to authenticate the device by."""
    return kind_protocol(kind) in ("trmnl_client", "bloomin8_canvas")


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


def _renderer_gamuts(renderers: RendererRegistry, renderer_id: Any) -> frozenset[str]:
    """The gamuts a renderer's manifest says it exists for (``gamuts``), or
    an empty set for the ordinary renderers that serve whatever gamut the
    panel declares. ``kaleido_png`` declares ``["kaleido3"]``: it is the
    only output a colour e-reader can paint, and useless for any other
    panel."""
    renderer = renderers.get(str(renderer_id or ""))
    if renderer is None:
        return frozenset()
    raw = renderer.manifest.get("gamuts")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(g) for g in raw if isinstance(g, str))


def renderer_id_for_gamut(renderers: RendererRegistry, kind: Any, gamut: str | None) -> str | None:
    """The renderer of ``kind`` dedicated to a declared panel gamut, or
    ``None`` when the gamut is empty / unknown or no renderer of the kind
    declares itself for it (the common case: the kind's default renderer
    handles the gamut by reading it off the panel block).

    A client that pairs as ``koreader_client`` with ``gamut: "kaleido3"``
    (a Kobo Libra Colour / Clara Colour) lands on ``kaleido_png`` this way
    rather than on the 4-bpp grey packer the kind defaults to, so its
    ``/frame`` descriptor says ``format: "png"`` and carries colour."""
    if not isinstance(gamut, str) or not gamut.strip():
        return None
    wanted = canonicalise_gamut(gamut.strip())
    for rid in getattr(kind, "renderer_ids", []):
        if wanted in _renderer_gamuts(renderers, rid):
            return str(rid)
    return None


@holds_instance_file_lock
def update_instance_gamut(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    gamut: str | None,
) -> tuple[InstanceResult, bool]:
    """Move an already-registered instance to a gamut its client now
    declares, when that gamut (or the one it leaves) has a dedicated
    renderer on the kind.

    The idempotent re-register path leaves a device's panel alone, which
    is right for dims but strands a reader whose plugin has learned to
    say ``kaleido3`` since it first paired as ``gray_16``: it would keep
    receiving packed grey frames. So a declared gamut is honoured here
    in exactly two cases: the kind has a renderer dedicated to it
    (:func:`renderer_id_for_gamut`), which is pinned; or the instance is
    pinned to a renderer dedicated to a gamut it no longer declares (a
    colour reader whose owner turned KOReader's colour rendering off and
    re-paired), which is unpinned back to the kind's default. Any other
    redeclaration is ignored, as before, so a client restating a gamut
    the default renderer already serves never rewrites the panel block.

    Returns ``(result, changed)``; ``changed`` is True only when the
    instance file was rewritten, so the caller can drop the device's
    now-stale render (produced by the old renderer, in the old format)."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}."), False
    if not isinstance(gamut, str) or not gamut.strip():
        return InstanceResult(device), False
    kind = devices.get(device.kind_of)
    wanted = canonicalise_gamut(gamut.strip())
    current_renderer = device.manifest.get("renderer_id")
    target = renderer_id_for_gamut(renderers, kind, wanted)
    pinned_gamuts = _renderer_gamuts(renderers, current_renderer)
    stored_gamut = str((device.manifest.get("panel") or {}).get("gamut") or "")
    if target is not None:
        if target == current_renderer and stored_gamut == wanted:
            return InstanceResult(device), False
    elif not pinned_gamuts or wanted in pinned_gamuts:
        return InstanceResult(device), False  # nothing dedicated either side

    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return InstanceResult(None, f"Couldn't read {inst_file.name}: {err}"), False
    panel_block = dict(raw.get("panel") or {})
    panel_block["gamut"] = wanted
    raw["panel"] = panel_block
    if target is not None:
        raw["renderer_id"] = target
    else:
        raw.pop("renderer_id", None)  # back to the kind default
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error"), False
    clone_for_instances(renderers, devices)
    return InstanceResult(reloaded), True


VALID_ROTATIONS: tuple[int, ...] = (0, 90, 180, 270)


def parse_rotation(value: Any) -> int | None:
    """Coerce a client-reported ``rotation`` to one of 0 / 90 / 180 / 270.

    ``None`` means "the client didn't declare one", which is a different
    state from ``0`` and keeps the caller on the derive-from-dims path.
    Off-quadrant, non-numeric and boolean values are treated as
    undeclared rather than rejected: a firmware sending nonsense should
    land the same way one sending nothing does, not fail to pair."""
    if value is None or isinstance(value, bool):
        return None
    try:
        rotation = int(value)
    except (TypeError, ValueError):
        return None
    rotation %= 360
    return rotation if rotation in VALID_ROTATIONS else None


# Values that reach the server in the ``mac`` slot but carry no device
# identity. Mostly a client's null formatted into the body as a string
# (``f'"mac": "{mac}"'`` with ``mac = None`` yields ``"None"``), plus the
# all-zero / broadcast MACs a driver reports before the radio is up.
# Compared after separators and case are normalised away.
_MAC_SENTINELS: frozenset[str] = frozenset(
    {
        "none",
        "null",
        "nil",
        "nan",
        "na",
        "undefined",
        "unknown",
        "unset",
        "000000000000",
        "ffffffffffff",
    }
)


def usable_mac(value: Any) -> str | None:
    """The announced MAC, or ``None`` when it can't identify a device.

    ``/discover`` hands a firmware its access token by matching this
    value against a registered instance, which makes a placeholder worse
    than nothing: two clients that both send ``"None"`` or an all-zero
    MAC collide on one instance, and the second one is handed the first
    one's token. So a sentinel is treated exactly like an absent MAC
    (issue #226), both when deciding whether an announce can claim and
    when persisting a MAC onto a new instance.

    The caller's spelling is preserved on the way out (colons, case) so
    the value shown on the device card matches what the client sent;
    only the comparison is normalised."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalised = re.sub(r"[^a-z0-9]", "", text.lower())
    if not normalised or normalised in _MAC_SENTINELS:
        return None
    return text


def panel_geometry_from_report(
    *, w: int | None, h: int | None, rotation: int | None
) -> tuple[dict[str, int], str | None]:
    """Map a client's reported panel geometry onto panel overrides and a
    composition orientation.

    Two contracts meet here, and ``rotation`` is what tells them apart
    (issue #200).

    Without a ``rotation``, the reported dims *are* the composition
    canvas, which is all any client could express before the field
    existed. The only thing to infer is the aspect, so a client painting
    a tall panel stops landing with portrait dims and the kind's
    landscape orientation, a contradiction that the next save of the
    panel form used to resolve by rewriting the dims.

    With a ``rotation``, the reported dims are the client's
    *framebuffer* and the rotation is the turn from that buffer to the
    canvas: 0 leaves them equal, 90 / 270 transpose, 180 keeps the dims
    and flips. The buffer is recorded as ``native_w / native_h`` so a
    renderer can rotate the finished composition back onto it, and the
    client always receives an image shaped like the panel it declared.

    Returns the extra panel overrides plus the orientation to stamp;
    ``_apply_orientation`` does the w/h swap from there."""
    if not w or not h or w <= 0 or h <= 0:
        return ({}, None)
    if rotation is None:
        return ({}, "portrait" if h > w else "landscape")
    transposed = rotation in (90, 270)
    canvas_landscape = (w >= h) != transposed
    aspect = "landscape" if canvas_landscape else "portrait"
    flipped = rotation in (180, 270)
    return ({"native_w": w, "native_h": h}, f"{aspect}_flipped" if flipped else aspect)


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
    kind_gamut = panel.get("gamut")
    if panel_overrides:
        _drop_foreign_native_stride(panel, panel_overrides)
        panel.update(panel_overrides)
        reported_gamut = panel_overrides.get("gamut")
        if isinstance(reported_gamut, str) and reported_gamut:
            # A firmware reporting its frame's depth (the Inkplate 10 says
            # gray_16 for a 4-bpp frame) must not widen a kind that knows
            # the glass paints fewer levels (gray_8).
            panel["gamut"] = refine_reported_gamut(
                kind_gamut if isinstance(kind_gamut, str) else None, reported_gamut
            )
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
        # The Canvas token is pasted once into its pull settings, never
        # typed on a device keyboard, and it is the only thing guarding
        # /api/bloomin8/eink_pull, so it always gets the long form.
        if kind.id == "bloomin8_canvas":
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
        if kind_protocol(kind) == "trmnl_client":
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


# The two CrossInk Sticky kinds retired in v0.344.0, and their panel blocks
# as they were copied into instance files at create_instance. Hard-coded
# because this migration runs before the loader scan, so the catalog isn't
# available to look them up, and the entries no longer exist to look up.
_RETIRED_STICKY_PANELS: dict[str, dict[str, Any]] = {
    "seeed_sticky": {
        "w": 480,
        "h": 800,
        "orientation": "portrait_flipped",
        "name": "3.97 inch monochrome ePaper (800x480 native)",
        "gamut": "mono",
        "native_w": 800,
        "native_h": 480,
    },
    "seeed_sticky_gray": {
        "w": 480,
        "h": 800,
        "orientation": "portrait_flipped",
        "name": "3.97 inch ePaper, 4-level grayscale (800x480 native)",
        "gamut": "gray_4",
        "native_w": 800,
        "native_h": 480,
    },
}

_STICKY_SUCCESSOR = "seeed_reterminal_sticky"
_STICKY_SUCCESSOR_PANEL: dict[str, Any] = {
    "w": 480,
    "h": 800,
    "orientation": "portrait",
    "name": "ePaper, 4-level grayscale (480x800 portrait)",
    "gamut": "gray_4",
    "native_w": 480,
    "native_h": 800,
}

# v0.344.0 shipped the successor declaring the controller's 800x480 landscape
# scan as the firmware stride. The Sticky's firmware transposes on the device
# and reads 120-byte portrait rows, so the server rotated and packed 200-byte
# rows instead: still exactly 96000 bytes, so nothing errored, and every frame
# painted as the image repeated sideways. Instances created or migrated under
# that version carry the wrong pair in their own panel block, which overrides
# the corrected kind, so fixing the manifest alone would not reach them.
_STICKY_WRONG_NATIVE = (800, 480)


def _fix_sticky_native_dims(raw: dict[str, Any]) -> bool:
    """Correct a Sticky instance still carrying v0.344.0's landscape stride.

    Returns True when the file changed. Only the exact wrong pair is touched:
    an operator who set something else did so deliberately, and the panel
    ``name`` moves with it only if it is still the one that shipped alongside
    the wrong dims.
    """
    panel = raw.get("panel")
    if not isinstance(panel, dict):
        return False
    if (panel.get("native_w"), panel.get("native_h")) != _STICKY_WRONG_NATIVE:
        return False
    panel["native_w"] = _STICKY_SUCCESSOR_PANEL["native_w"]
    panel["native_h"] = _STICKY_SUCCESSOR_PANEL["native_h"]
    if panel.get("name") == "ePaper, 4-level grayscale (800x480 native, composed 480x800 portrait)":
        panel["name"] = _STICKY_SUCCESSOR_PANEL["name"]
    return True


def migrate_retired_sticky_kinds(data_root: Path) -> list[str]:
    """One-shot migration: move Sticky instances off the retired CrossInk
    kinds onto ``seeed_reterminal_sticky``.

    ``seeed_sticky`` and ``seeed_sticky_gray`` described the same hardware
    running CrossInk, from the V01 schematic rather than a validated unit.
    Both were retired in v0.344.0 in favour of the hardware-confirmed kind.
    An instance left pointing at a kind the catalog no longer has fails to
    load and the device 404s, so the pointer has to move here.

    The panel block moves with it. Instances carry a copy of their kind's
    panel, and the retired kinds declared ``portrait_flipped`` where the
    confirmed one is ``portrait``: keeping that value paints every frame 180
    degrees out. Fields the operator changed are left alone, same rule as a
    kind heal. A retired mono instance also picks up the successor's
    ``gray_4`` gamut, since the successor packs 2-bpp and there is no 1-bpp
    variant of it.

    Also corrects the firmware stride on instances that already reached the
    successor under v0.344.0, which declared the controller's 800x480 scan
    where this firmware wants 480x800 portrait rows. Their own panel block
    overrides the fixed kind, so the manifest correction alone would never
    reach them and they would keep painting the frame repeated sideways.

    Idempotent, and does nothing on a fresh install."""
    migrated: list[str] = []
    if not data_root.is_dir():
        return migrated
    for path in sorted(data_root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        kind = str(raw.get("kind") or "")
        old_panel = _RETIRED_STICKY_PANELS.get(kind)
        if old_panel is None:
            if kind != _STICKY_SUCCESSOR or not _fix_sticky_native_dims(raw):
                continue
        else:
            raw["kind"] = _STICKY_SUCCESSOR
            # The successor offers esp32_gray2_bin only; a mono instance's pin
            # would be silently ignored at load, so drop it rather than leave a
            # stale field on disk.
            if raw.get("renderer_id") not in (None, "esp32_gray2_bin"):
                raw.pop("renderer_id", None)
            _refresh_inherited_panel(raw, old_panel=old_panel, new_panel=_STICKY_SUCCESSOR_PANEL)
        try:
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        except OSError:
            continue
        migrated.append(str(raw.get("id") or path.stem))
    return migrated


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


@holds_instance_file_lock
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
        return InstanceResult(None, "Logical panel width and height must be at least 1px.")
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


@holds_instance_file_lock
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
    # Already on a renderer of the declared format: nothing to switch. A
    # mono / grey board declaring ``bin`` must not be dragged off a grey
    # ``.bin`` renderer onto the first ``.bin`` the kind lists.
    in_use = device.manifest.get("renderer_pick") or device.manifest.get("renderer_id")
    in_use = in_use or (kind.renderer_ids[0] if kind is not None and kind.renderer_ids else None)
    current_renderer = renderers.get(str(in_use or ""))
    target_renderer = renderers.get(target)
    if (
        current_renderer is not None
        and target_renderer is not None
        and current_renderer.extension == target_renderer.extension
    ):
        return InstanceResult(device), False

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


@holds_instance_file_lock
def set_instance_renderer_id(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    renderer_id: str | None,
) -> tuple[InstanceResult, bool]:
    """Pin an instance to a specific one of its kind's renderers, chosen
    by the operator on the device card (Settings -> Devices -> Rendering).

    The kind-list wire-up: :func:`renderer_id_for_format` disambiguates by
    file extension, which can't tell two same-extension renderers apart
    (``trmnl_png`` vs ``trmnl_png_gray16``, both ``.png``). This is the
    explicit pick for that case.

    ``renderer_id`` of ``None`` or the kind's primary (first) renderer
    clears the pin so the instance follows the kind default again. A value
    the kind doesn't offer is an error. A no-op change returns
    ``(result, False)``.

    Returns ``(result, changed)``; ``changed`` is True only when the
    renderer actually moved, so the caller can invalidate the device's
    now-stale render (produced by the old renderer, possibly a different
    format).
    """
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}."), False
    kind = devices.get(device.kind_of)
    kind_renderer_ids = list(getattr(kind, "renderer_ids", []))
    if not kind_renderer_ids:
        return InstanceResult(None, f"{device.name} has no configurable renderers."), False

    primary = kind_renderer_ids[0]
    target = renderer_id or primary
    if target not in kind_renderer_ids:
        return InstanceResult(None, f"Unknown renderer {target!r} for this device."), False

    # The stored pick, else the renderer in use. On a ``colour_depths`` kind
    # these differ when a grey pick is waiting on firmware that advertises
    # grey; comparing against the pick keeps that choice from reading as
    # "already mono" and being dropped.
    current = device.manifest.get("renderer_pick") or device.manifest.get("renderer_id") or primary
    if target == current:
        return InstanceResult(device), False

    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return InstanceResult(None, f"Couldn't read {inst_file.name}: {err}"), False
    # An operator pick always replaces an automatic one.
    raw.pop("renderer_auto", None)
    keeps_explicit_pin = bool(renderer_min_firmware(kind)) or bool(
        colour_depths(getattr(kind, "manifest", {}) or {})
    )
    if target == primary and not keeps_explicit_pin:
        raw.pop("renderer_id", None)  # back to the kind default; don't pin
    else:
        # A kind whose renderer follows the firmware version, or whose
        # colour depth has a catalog default, keeps even a pick of its
        # primary as an explicit pin, so neither the next poll's firmware
        # check nor a later default flip moves the device off it.
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


_MAX_ADVERTISED_GAMUTS = 8


def parse_advertised_gamuts(payload: Any) -> list[str] | None:
    """The ``gamuts`` array a device advertises in its register / status
    body (``["mono", "gray_4"]``): the colour depths its firmware can paint.
    Canonicalised, unknown values dropped, order kept, de-duplicated.

    ``None`` when the body has no such key, or it isn't a list: the caller
    keeps the last known value, the same sticky rule as ``can_stay_awake``
    (a firmware property, not something that comes and goes between wakes).
    """
    if isinstance(payload, (bytes, bytearray, str)):
        try:
            body = json.loads(payload)
        except (ValueError, TypeError):
            return None
    else:
        body = payload
    if not isinstance(body, dict):
        return None
    raw = body.get("gamuts")
    if not isinstance(raw, list):
        return None
    from app.quantizer import ACCEPTED_GAMUTS

    out: list[str] = []
    for value in raw:
        if not isinstance(value, str) or value.strip() not in ACCEPTED_GAMUTS:
            continue
        gamut = canonicalise_gamut(value.strip())
        if gamut not in out:
            out.append(gamut)
        if len(out) >= _MAX_ADVERTISED_GAMUTS:
            break
    return out


@holds_instance_file_lock
def update_advertised_gamuts(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    gamuts: list[str] | None,
) -> tuple[InstanceResult, bool]:
    """Persist the gamuts a device advertises (``advertised_gamuts`` on its
    instance file) when they change, and reload it so the colour-depth gate
    sees them.

    The renderer an instance of a ``colour_depths`` kind uses depends on
    this list (see :func:`app.device_loader.effective_renderer_pick`), so it
    lives on the instance file, read at every load, rather than only in the
    status cache. Written only on change, so steady beats cost nothing.

    Returns ``(result, render_changed)``: ``render_changed`` is True only
    when the renderer in use moved (a grey pick becoming paintable after a
    firmware update, or falling back to mono after a rollback), which is
    when the caller must drop the cached frame so its digest and ETag move
    with it."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}."), False
    if gamuts is None or device.manifest.get("advertised_gamuts") == gamuts:
        return InstanceResult(device), False
    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return InstanceResult(device), False
    if not isinstance(raw, dict):
        return InstanceResult(device), False
    before = list(device.renderer_ids)
    raw["advertised_gamuts"] = list(gamuts)
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error"), False
    clone_for_instances(renderers, devices)
    return InstanceResult(reloaded), list(reloaded.renderer_ids) != before


def advertises_gamut(device: Any, gamut: str) -> bool:
    """Whether the device's firmware has advertised it can paint ``gamut``."""
    advertised = getattr(device, "manifest", {}).get("advertised_gamuts")
    return isinstance(advertised, list) and gamut in advertised


@holds_instance_file_lock
def set_grey_prompt_dismissed(
    *,
    devices: DeviceRegistry,
    instance_id: str,
) -> InstanceResult:
    """Record "Keep mono" on the 4-level grey upgrade prompt for one device
    (``grey_prompt_dismissed`` on its instance file), so the prompt never
    returns for it. Patched in memory too; nothing about rendering changes,
    so no reload."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}.")
    try:
        raw = json.loads(device.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return InstanceResult(None, f"Couldn't read {device.path.name}: {err}")
    if not isinstance(raw, dict):
        return InstanceResult(None, f"{device.path.name} is not a JSON object.")
    raw["grey_prompt_dismissed"] = True
    device.path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    device.manifest["grey_prompt_dismissed"] = True
    return InstanceResult(device)


def parse_firmware_version(value: Any) -> tuple[int, ...] | None:
    """``"1.6.0"`` -> ``(1, 6, 0)``; a leading ``v`` and any suffix after
    the numeric part (``1.6.0-rc1``, ``1.6.0+abc``) are ignored. ``None``
    for anything with no leading number, so an odd version string never
    reads as new enough."""
    if not isinstance(value, str):
        return None
    text = value.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in text.split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
        if len(digits) != len(chunk):
            break  # suffix reached ("0-rc1")
    return tuple(parts) if parts else None


def renderer_min_firmware(kind: Any) -> dict[str, tuple[int, ...]]:
    """The kind's ``protocol_config.renderer_min_firmware``: renderer id ->
    the lowest firmware version that can paint its output, parsed. Only
    renderers the kind actually offers are kept. Empty for every kind that
    does not declare one."""
    manifest = getattr(kind, "manifest", None)
    config = manifest.get("protocol_config") if isinstance(manifest, dict) else None
    rules = config.get("renderer_min_firmware") if isinstance(config, dict) else None
    if not isinstance(rules, dict):
        return {}
    offered = set(getattr(kind, "renderer_ids", []))
    out: dict[str, tuple[int, ...]] = {}
    for rid, version in rules.items():
        parsed = parse_firmware_version(version)
        if isinstance(rid, str) and rid in offered and parsed is not None:
            out[rid] = parsed
    return out


def _pad_version(v: tuple[int, ...], n: int) -> tuple[int, ...]:
    return v + (0,) * (n - len(v))


def renderer_for_firmware(kind: Any, fw_version: Any) -> str | None:
    """The renderer a kind's firmware rules pick for ``fw_version``: the
    first of the kind's renderers (in listed order, after the primary)
    whose minimum the firmware meets, else the primary. ``None`` when the
    kind has no rules or the version can't be read, meaning "leave the
    device as it is"."""
    rules = renderer_min_firmware(kind)
    if not rules:
        return None
    fw = parse_firmware_version(fw_version)
    if fw is None:
        return None
    kind_renderer_ids = list(getattr(kind, "renderer_ids", []))
    if not kind_renderer_ids:
        return None
    for rid in kind_renderer_ids[1:]:
        minimum = rules.get(rid)
        if minimum is None:
            continue
        width = max(len(fw), len(minimum))
        if _pad_version(fw, width) >= _pad_version(minimum, width):
            return str(rid)
    return str(kind_renderer_ids[0])


@holds_instance_file_lock
def sync_renderer_for_firmware(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    fw_version: Any,
) -> tuple[InstanceResult, bool]:
    """Move a device onto the renderer its reported firmware can paint, for
    kinds that list ``renderer_min_firmware`` in their catalog
    ``protocol_config``.

    The XIAO 7.5" running TRMNL firmware is the case: firmware 1.6.0 and
    later decodes a 2-bit greyscale PNG and paints 4 shades, while earlier
    builds cannot decode one at all. The TRMNL firmware sends its version
    in the ``FW-Version`` header on every poll, so the device follows its
    own firmware up (and back down after a downgrade) without the operator
    choosing.

    Only an automatic choice moves: an instance with no renderer pin, or
    one pinned by this function (``renderer_auto`` on the instance file).
    A renderer the operator picked on the device card stays. An unreadable
    version is a no-op. Returns ``(result, changed)``; on ``changed`` the
    caller drops the device's cached render, which is in the old format."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}."), False
    kind = devices.get(device.kind_of)
    target = renderer_for_firmware(kind, fw_version)
    if target is None or kind is None:
        return InstanceResult(device), False
    if (device.manifest.get("renderer_id") or kind.renderer_ids[0]) == target:
        return InstanceResult(device), False  # already there; skip the file read
    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return InstanceResult(device), False
    if not isinstance(raw, dict):
        return InstanceResult(device), False
    pinned = raw.get("renderer_id")
    automatic = pinned is None or raw.get("renderer_auto") is True
    if not automatic:
        return InstanceResult(device), False
    primary = kind.renderer_ids[0]
    current = pinned or primary
    if current == target:
        return InstanceResult(device), False
    if target == primary:
        raw.pop("renderer_id", None)
        raw.pop("renderer_auto", None)
    else:
        raw["renderer_id"] = target
        raw["renderer_auto"] = True
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error"), False
    clone_for_instances(renderers, devices)
    logger.info(
        "Device %s renderer follows firmware %s: %s -> %s",
        instance_id,
        fw_version,
        current,
        target,
    )
    return InstanceResult(reloaded), True


def kind_protocol(kind: Device) -> str:
    """The wire protocol a kind speaks. Hardware-catalog SKUs carry it
    under ``_catalog_entry.protocol``; a folder-defined protocol kind is
    its own protocol (its id)."""
    ce = kind.manifest.get("_catalog_entry")
    if isinstance(ce, dict) and isinstance(ce.get("protocol"), str) and ce["protocol"]:
        return str(ce["protocol"])
    return kind.id


# Protocols with a client that subscribes to the broker. The generic
# ``esp32_client`` / ``esp32_bw_client`` folder kinds keep the MQTT flip
# because the legacy bin / bw MQTT clients exist for them, but every
# hardware-catalog SKU on those protocols runs tesserae-device-firmware,
# which has spoken REST only since July 2026 (its MQTT transport was
# replaced outright, nothing from esp_mqtt links into the image). The
# other protocols with topics on their manifest (KOReader, PicPak) ship
# REST-only clients too.
_MQTT_CAPABLE_PROTOCOLS = frozenset(
    {"esp32_client", "esp32_bw_client", "pi_bin_client", "pi_png_client", "pico_bin_client"}
)
_NATIVE_FIRMWARE_PROTOCOLS = frozenset({"esp32_client", "esp32_bw_client"})


def is_hardware_sku(kind: Device) -> bool:
    """True for a kind derived from the hardware catalog (a SKU), as
    opposed to a protocol folder kind."""
    return isinstance(kind.manifest.get("_catalog_entry"), dict)


def transport_switchable(kind: Device) -> bool:
    """Whether an instance of ``kind`` can follow a switch to MQTT.

    The Settings card's transport flip only rewrites the instance
    manifest; nothing is sent to the device. So it is only offered where
    the client on the device could be one that subscribes to the broker.
    A REST-only client flipped to MQTT keeps polling REST while the card
    badge reads "HTTP" and every render is published to the broker for
    nothing (#341). A switch *to* REST stays allowed for every kind: for
    a REST-only client it is the only correct state."""
    protocol = kind_protocol(kind)
    if protocol not in _MQTT_CAPABLE_PROTOCOLS:
        return False
    return not (protocol in _NATIVE_FIRMWARE_PROTOCOLS and is_hardware_sku(kind))


@holds_instance_file_lock
def restore_rest_only_transports(registry: DeviceRegistry) -> list[str]:
    """Put instances of REST-only kinds that read as MQTT back on REST.

    Before #341 the card offered "Switch to MQTT" on every instance, and
    taking it on a REST-only client left the instance with no transport
    field (read as MQTT) and its access token still on the manifest. The
    device carried on over REST regardless, so that pair is the
    signature of the mistake: a REST-only client never earns a token any
    other way. Kinds with no MQTT topics at all (TRMNL-protocol clients)
    never publish, so they are left alone. Idempotent; returns the ids
    healed."""
    healed: list[str] = []
    for device in list(registry.devices.values()):
        if device.kind_of is None:
            continue
        kind = registry.get(str(device.kind_of))
        if kind is None or transport_switchable(kind) or kind.status_topic is None:
            continue
        if device.transport != "mqtt":
            continue
        token = device.manifest.get("access_token")
        if not isinstance(token, str) or not token:
            continue
        try:
            raw = json.loads(device.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        raw["transport"] = "rest"
        try:
            device.path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        except OSError:
            continue
        device.manifest["transport"] = "rest"
        healed.append(device.id)
    return healed


@holds_instance_file_lock
def refine_inherited_gamuts(registry: DeviceRegistry) -> list[str]:
    """Move instances onto their kind's coarser gamut where the stored one
    is a report that gamut refines (see
    :func:`app.quantizer.refine_reported_gamut`).

    The Inkplate 10 kind declared ``gray_16`` until 0.469.0 and now says
    ``gray_8``, the 8 levels its firmware actually drives; its firmware
    also reports ``gray_16`` at pairing. Every instance created before the
    change carries ``gray_16`` in its own panel block, which overrides the
    kind's, so without this the packer would keep dithering against 16
    levels that the panel merges in pairs. Idempotent; returns the ids
    moved."""
    moved: list[str] = []
    for device in list(registry.devices.values()):
        if device.kind_of is None:
            continue
        kind = registry.get(str(device.kind_of))
        kind_gamut = (kind.panel or {}).get("gamut") if kind is not None else None
        if not isinstance(kind_gamut, str) or not kind_gamut:
            continue
        stored = (device.manifest.get("panel") or {}).get("gamut")
        if not isinstance(stored, str) or not stored:
            continue
        refined = refine_reported_gamut(kind_gamut, stored)
        if refined == canonicalise_gamut(stored):
            continue
        try:
            raw = json.loads(device.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict) or not isinstance(raw.get("panel"), dict):
            continue
        raw["panel"]["gamut"] = refined
        try:
            device.path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        except OSError:
            continue
        panel = device.manifest.get("panel")
        if isinstance(panel, dict):
            panel["gamut"] = refined
        moved.append(device.id)
    return moved


def _refresh_inherited_panel(
    raw: dict[str, Any],
    *,
    old_panel: dict[str, Any] | None,
    new_panel: dict[str, Any] | None,
) -> None:
    """Move panel fields the instance inherited from its old kind onto the
    new kind's values, in place.

    ``create_instance`` copies the kind's panel block into the instance
    file, and an instance's panel overrides its kind's. So after a heal the
    instance still describes the *old* board until something rewrites it.

    Usually harmless between same-protocol siblings that share a panel, but
    not always. ``seeed_reterminal_sticky`` is ``portrait`` where the
    CrossInk ``seeed_sticky_gray`` it heals from is ``portrait_flipped``:
    keeping the stale value paints every frame 180 degrees out, on a panel
    that is otherwise working. Geometry moves too, so a device healing from
    a generic protocol kind onto its SKU (``esp32_client`` ->
    ``seeed_reterminal_e1004``, the case this heal was written for) stops
    rendering at the generic kind's 800x480 default.

    Only fields still equal to the old kind's value are moved, so a
    deliberate per-device override survives. Same reasoning as the stale
    ``renderer_id`` drop in the caller: a value the user never chose should
    not outlive the kind it came from, and one they did choose should not be
    silently reverted.
    """
    panel = raw.get("panel")
    if not isinstance(panel, dict):
        return
    old = old_panel or {}
    new = new_panel or {}
    for key, inherited in old.items():
        if key not in panel or panel[key] != inherited:
            continue  # absent, or the user moved it off the kind default
        if key in new:
            panel[key] = new[key]
        else:
            # The new kind doesn't describe this field; carrying the old
            # kind's answer for it is worse than falling back to the new
            # kind's own default at load.
            panel.pop(key, None)
    for key, value in new.items():
        panel.setdefault(key, value)


@holds_instance_file_lock
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
    _refresh_inherited_panel(raw, old_panel=current_kind.panel, new_panel=new_kind.panel)
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


@holds_instance_file_lock
def update_instance_quiet_hours(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    enabled: bool,
    start: str | None,
    end: str | None,
    days: list[str] | None = None,
    all_day: list[str] | None = None,
    sleep: bool | None = None,
    touch_off: bool | None = None,
) -> InstanceResult:
    """Patch a registered instance's ``quiet_hours`` block on disk and
    hot-reload it in place. Empty or invalid times disable the timed
    part of the override (the helper resolver treats both as "no
    window"); all-day days still count.

    The block on disk is the shape :mod:`app.quiet_hours` reads:
    ``{enabled: bool, start: 'HH:MM', end: 'HH:MM', days: [...],
    all_day: [...], sleep: bool, touch_off: bool}``. ``days``,
    ``all_day``, ``sleep`` and ``touch_off`` passed as ``None`` keep whatever the block already holds, so a
    caller that only knows about the times (the Home Assistant switch)
    never wipes the weekday choice. When the user clears every field we
    drop the block entirely so the device falls back to the app-level
    setting on next reload."""
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
    previous = raw.get("quiet_hours") if isinstance(raw.get("quiet_hours"), dict) else {}
    keep_days = previous.get("days") if days is None else days
    keep_all_day = previous.get("all_day") if all_day is None else all_day
    keep_sleep = previous.get("sleep") if sleep is None else sleep
    keep_touch_off = previous.get("touch_off") if touch_off is None else touch_off
    if not enabled and not clean_start and not clean_end and not keep_all_day:
        # Fully cleared, drop the block entirely so the next reload
        # sees a manifest with no override and uses the app setting.
        raw.pop("quiet_hours", None)
    else:
        from app.quiet_hours import ALL_DAYS, NO_DAYS, days_to_keys, parse_days

        block: dict[str, Any] = {
            "enabled": bool(enabled),
            "start": clean_start,
            "end": clean_end,
        }
        if keep_days is not None:
            block["days"] = days_to_keys(parse_days(keep_days, ALL_DAYS))
        if keep_all_day is not None:
            block["all_day"] = days_to_keys(parse_days(keep_all_day, NO_DAYS))
        if keep_sleep is not None:
            block["sleep"] = bool(keep_sleep)
        if keep_touch_off is not None:
            block["touch_off"] = bool(keep_touch_off)
        raw["quiet_hours"] = block
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error")
    clone_for_instances(renderers, devices)
    return InstanceResult(reloaded)


@holds_instance_file_lock
def update_instance_locale(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    locale: str | None,
) -> InstanceResult:
    """Patch a registered instance's ``locale`` field on disk and
    hot-reload it in place. A blank value clears the override so the
    device falls back to the app-wide default on next reload (see
    app.locale_resolve.resolve_locale). Not format-validated here, same
    trade-off update_instance_quiet_hours already accepts for HH:MM: a
    malformed value just fails to match any of the widget's strings at
    read time and the panel renders in English, rather than a write-time
    rejection the admin has to puzzle out."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}.")

    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return InstanceResult(None, f"Couldn't read {inst_file.name}: {err}")

    clean_locale = (locale or "").strip()
    if not clean_locale:
        raw.pop("locale", None)
    else:
        raw["locale"] = clean_locale
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error")
    clone_for_instances(renderers, devices)
    return InstanceResult(reloaded)


@holds_instance_file_lock
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


@holds_instance_file_lock
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
    for hook in _hooks_for(device):
        if hook.device_removed is not None:
            _run_hook(hook.device_removed, device)
    return InstanceResult(device)


def _drop_foreign_native_stride(panel: dict[str, Any], overrides: dict[str, Any]) -> None:
    """Forget a kind's default ``native_w / native_h`` (and ``col_offset``)
    when the overrides size a different panel and bring no stride of
    their own.

    A kind's panel block describes its default glass; the stride is a
    fact about that glass. A client that reports another size (a Kobo
    Clara pairing as ``koreader_client``, whose default panel is the
    758x1024 Paperwhite 2) is a different panel, and inheriting the
    default's stride made every renderer that packs at native dims
    produce a 758x1024 frame for a 1072x1448 screen. With the stride
    gone, ``device_panel`` falls back to the preset table or to the
    composition dims, which for a client that reported its own screen is
    the screen. Overrides that match the kind's dims in either axis order
    keep the stride (same glass, possibly turned), as do overrides that
    carry their own (a preset pick, or a declared ``rotation``).
    """
    if "native_w" in overrides or "native_h" in overrides:
        return
    try:
        new_w, new_h = int(overrides["w"]), int(overrides["h"])
        old_w, old_h = int(panel["w"]), int(panel["h"])
    except (KeyError, TypeError, ValueError):
        return
    if {new_w, new_h} == {old_w, old_h}:
        return
    for key in ("native_w", "native_h", "col_offset"):
        panel.pop(key, None)


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
