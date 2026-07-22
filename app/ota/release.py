"""Manage per-kind OTA releases: set a target, canary, promote, pause, clear.

Set a kind's release from a signed descriptor (kind + firmware version are read
from it), offered first to the canary devices you list::

    python -m app.ota.release set --data-root data --descriptor d.json \\
        --canary hall_esp

Promote it to every device of that kind once the canary looks good::

    python -m app.ota.release promote --data-root data --kind seeed_reterminal_e1001

Pause / clear / list::

    python -m app.ota.release pause  --data-root data --kind seeed_reterminal_e1001
    python -m app.ota.release clear  --data-root data --kind seeed_reterminal_e1001
    python -m app.ota.release list   --data-root data

The descriptor is verified against the trusted key for its ``key_id`` before it
is set (``--insecure-skip-verify`` to override). The running server picks up the
change on the next heartbeat.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.semver import is_strictly_newer
from app.state.ota_release import OtaReleaseStore

from ._codec import MANIFEST_FIELDS, b64u_decode
from .keys import default_keys_dir, load_trusted_keys
from .verify import OtaVerificationError, verify


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a strictly newer firmware version than
    ``current`` (plain SemVer). If either is unparseable, fall back to a
    conservative "offer only when the strings differ" so we never suppress a
    genuine update, but also never loop on an equal version."""
    newer = is_strictly_newer(candidate, current)
    if newer is None:
        return candidate.strip() != current.strip() and bool(candidate.strip())
    return newer


def _store(data_root: Path) -> OtaReleaseStore:
    return OtaReleaseStore(data_root / "core" / "ota_releases.json")


def _manifest_from_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(descriptor, dict) or "payload" not in descriptor:
        raise ValueError("descriptor must be an object with a 'payload'")
    manifest = json.loads(b64u_decode(str(descriptor["payload"])))
    if not isinstance(manifest, dict):
        raise ValueError("decoded payload is not a manifest object")
    missing = [k for k in MANIFEST_FIELDS if k not in manifest]
    if missing:
        raise ValueError(f"manifest missing keys: {missing}")
    return manifest


def _cmd_set(args: argparse.Namespace) -> int:
    try:
        descriptor = json.loads(args.descriptor.read_text(encoding="utf-8"))
        manifest = _manifest_from_descriptor(descriptor)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"release error: {exc}", file=sys.stderr)
        return 2

    if not args.insecure_skip_verify:
        keys = load_trusted_keys(args.keys_dir)
        key_id = str(manifest["key_id"])
        public_key = keys.get(key_id)
        if public_key is None:
            where = args.keys_dir or default_keys_dir()
            print(
                f"release error: no trusted key for key_id {key_id!r} in {where}; "
                f"publish its .pub or pass --insecure-skip-verify",
                file=sys.stderr,
            )
            return 2
        try:
            verify(descriptor, public_key)
        except OtaVerificationError as exc:
            print(f"release error: descriptor failed verification ({exc.reason})", file=sys.stderr)
            return 2

    kind = str(manifest["device_kind"])
    fw = str(manifest["fw_version"])
    entry = _store(args.data_root).set_target(
        kind, descriptor, fw_version=fw, canary_device_ids=args.canary or []
    )
    canary = ", ".join(entry["canary_device_ids"]) or "(none — promote to ship)"
    print(f"release set: kind={kind} fw={fw} state=canary canary={canary}")
    return 0


def _cmd_state(args: argparse.Namespace, action: str) -> int:
    store = _store(args.data_root)
    ok = store.promote(args.kind) if action == "promote" else store.pause(args.kind)
    if not ok:
        print(f"release error: no release set for kind {args.kind!r}", file=sys.stderr)
        return 2
    print(f"release {action}d: kind={args.kind}")
    return 0


def _cmd_clear(args: argparse.Namespace) -> int:
    removed = _store(args.data_root).clear(args.kind)
    print(f"{'cleared' if removed else 'no release for'} {args.kind}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    releases = _store(args.data_root).all()
    if not releases:
        print("no releases set")
        return 0
    for kind, entry in sorted(releases.items()):
        canary = ", ".join(entry.get("canary_device_ids") or []) or "-"
        print(f"{kind}: fw={entry.get('fw_version')} state={entry.get('state')} canary={canary}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ota.release", description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path, help="server data directory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_set = sub.add_parser("set", help="set a kind's release from a signed descriptor")
    p_set.add_argument("--descriptor", required=True, type=Path)
    p_set.add_argument("--canary", action="append", help="canary device id (repeatable)")
    p_set.add_argument("--keys-dir", type=Path, default=None)
    p_set.add_argument("--insecure-skip-verify", action="store_true")

    for name in ("promote", "pause", "clear"):
        p = sub.add_parser(name, help=f"{name} a kind's release")
        p.add_argument("--kind", required=True)

    sub.add_parser("list", help="list all releases")

    args = parser.parse_args(argv)
    if args.cmd == "set":
        return _cmd_set(args)
    if args.cmd in ("promote", "pause"):
        return _cmd_state(args, args.cmd)
    if args.cmd == "clear":
        return _cmd_clear(args)
    return _cmd_list(args)


if __name__ == "__main__":
    raise SystemExit(main())
