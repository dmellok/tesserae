#!/bin/sh
# Tesserae container entrypoint.
#
# Bind-mount UID mismatch is the #1 Docker gotcha for self-hosted apps:
# Docker auto-creates a host-side ``./data`` directory owned by the
# host user that ran ``docker compose up`` (usually uid 1000), but the
# container drops to ``pwuser`` (uid 1001) and EPERMs on the first
# write. Fixing it host-side requires every user to know to chown the
# directory before first boot.
#
# This entrypoint starts as root, chowns the data volume to pwuser,
# then re-execs itself under ``gosu pwuser`` so the actual Tesserae
# process runs unprivileged. Idempotent: on subsequent boots the chown
# is a no-op against an already-correct tree.
#
# Named-volume case still works (Docker creates them as root, this
# fixes them); bind-mount case works (we fix the host directory the
# first time); already-correct case works (chown is a no-op).
set -e

# HA's optional visionect_port option. Existing installs need no manifest
# update: absence leaves the settings-store/default path intact. An explicit
# environment value wins. Do this before dropping privileges (/data/options.json
# is Supervisor-owned). Validation and visible errors live in app.visionect.
if [ -z "${TESSERAE_VISIONECT_PORT:-}" ] && [ -r /data/options.json ]; then
    TESSERAE_VISIONECT_PORT=$(python -c '
import json
with open("/data/options.json") as source:
    value = json.load(source).get("visionect_port")
print("" if value is None else value)
')
    export TESSERAE_VISIONECT_PORT
fi

# Fail with a message instead of a bare "Illegal instruction". numpy's x86-64
# wheels need a 2009-or-newer CPU (x86-64-v2: SSE4.2, POPCNT) from 2.4 on. An
# older CPU, or a VM given a basic CPU model (Proxmox's default kvm64), either
# dies with SIGILL (exit 132) or gets numpy's own "your machine doesn't support"
# error at import (exit 1); both mean the same thing. Anything else is printed
# as numpy reported it, so the log says what actually went wrong (#361).
status=0
numpy_err=$(python -c 'import numpy' 2>&1 >/dev/null) || status=$?
if [ "$status" != "0" ]; then
    case "$status:$numpy_err" in
        132:*|*"machine doesn't support"*|*"Illegal instruction"*)
            cat >&2 <<'MSG'
Tesserae: numpy cannot run on this CPU.
The bundled numpy needs an x86-64-v2 processor (SSE4.2 and POPCNT, CPUs from
2009 on). In a virtual machine (Proxmox, Unraid, ESXi), set the VM's CPU type
to "host" (or x86-64-v2 or newer) and restart it. The published images pin
numpy below 2.4, which still runs on older CPUs; an image built by hand should
do the same:

    docker build --build-arg NUMPY_SPEC='numpy<2.4' -t tesserae .

See docs: Install via Docker -> Limits.
MSG
            ;;
        *)
            echo "Tesserae: numpy failed to import (exit $status):" >&2
            ;;
    esac
    [ -n "$numpy_err" ] && printf '%s\n' "$numpy_err" | tail -n 20 >&2
    exit "$status"
fi

if [ "$(id -u)" = "0" ]; then
    # Only the directory itself needs an unconditional chown so pwuser
    # can write inside it. Anything already owned correctly is skipped
    #, keeps subsequent boots fast on a populated data tree.
    chown pwuser:pwuser /app/data
    # The -R catches existing entries that were created before this
    # entrypoint shipped (or files a previous root-running container
    # wrote). Errors suppressed because read-only mounts will fail
    # here harmlessly.
    chown -R pwuser:pwuser /app/data 2>/dev/null || true
    # Owner bits too: a NAS that keeps share permissions in its own ACL
    # layer (Ugreen OS, Synology) can leave the real mode at 000, which
    # the host's tools display as rwx but the kernel enforces against
    # pwuser once gosu drops root (#372). Only the owner's bits change.
    chmod -R u+rwX /app/data 2>/dev/null || true
    # HA Add-on path: TESSERAE_DATA_ROOT redirects Tesserae's data
    # directory away from /app/data (typically to /data, HA Supervisor's
    # per-add-on persistent volume which is mounted root-owned). Same
    # UID-mismatch fix applies, chown so pwuser can write inside it.
    if [ -n "${TESSERAE_DATA_ROOT:-}" ] \
       && [ -d "${TESSERAE_DATA_ROOT}" ] \
       && [ "${TESSERAE_DATA_ROOT}" != "/app/data" ]; then
        chown pwuser:pwuser "${TESSERAE_DATA_ROOT}"
        chown -R pwuser:pwuser "${TESSERAE_DATA_ROOT}" 2>/dev/null || true
        chmod -R u+rwX "${TESSERAE_DATA_ROOT}" 2>/dev/null || true
    fi
    # HA Add-on path: the OpenDisplay-via-HA device kind writes frames
    # into HA's media folder (/media, mapped media:rw) so it can hand HA
    # a media-source id. Supervisor mounts /media root-owned, so create
    # the tesserae subdir and chown it to pwuser while we're still root;
    # otherwise the unprivileged process EPERMs on the first frame. Root
    # (HA core) can still read it. Skipped harmlessly when /media isn't
    # mapped (plain docker install) or is read-only.
    if [ -d /media ]; then
        mkdir -p /media/tesserae 2>/dev/null \
            && chown pwuser:pwuser /media/tesserae 2>/dev/null || true
    fi
    exec gosu pwuser "$@"
fi

exec "$@"
