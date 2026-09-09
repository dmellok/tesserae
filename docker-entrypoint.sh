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

# Fail with a message instead of a bare "Illegal instruction". numpy's x86-64
# wheels need a 2009-or-newer CPU (x86-64-v2: SSE4.2, POPCNT) from 2.4 on, and
# a SIGILL at import is all an older machine gets. Exit status 132 is SIGILL.
status=0
python -c 'import numpy' >/dev/null 2>&1 || status=$?
if [ "$status" != "0" ]; then
    if [ "$status" = "132" ]; then
        cat >&2 <<'MSG'
Tesserae: numpy cannot run on this CPU (illegal instruction at import).
The bundled numpy needs an x86-64-v2 processor (SSE4.2 and POPCNT, CPUs from
2009 on). Rebuild the image with the last numpy line that runs on older CPUs:

    docker build --build-arg NUMPY_SPEC='numpy<2.4' -t tesserae .

See docs: Install via Docker -> Limits.
MSG
        exit 132
    fi
    echo "Tesserae: numpy failed to import (exit $status); the image is broken." >&2
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
    # HA Add-on path: TESSERAE_DATA_ROOT redirects Tesserae's data
    # directory away from /app/data (typically to /data, HA Supervisor's
    # per-add-on persistent volume which is mounted root-owned). Same
    # UID-mismatch fix applies, chown so pwuser can write inside it.
    if [ -n "${TESSERAE_DATA_ROOT:-}" ] \
       && [ -d "${TESSERAE_DATA_ROOT}" ] \
       && [ "${TESSERAE_DATA_ROOT}" != "/app/data" ]; then
        chown pwuser:pwuser "${TESSERAE_DATA_ROOT}"
        chown -R pwuser:pwuser "${TESSERAE_DATA_ROOT}" 2>/dev/null || true
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
