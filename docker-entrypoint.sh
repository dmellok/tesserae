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

if [ "$(id -u)" = "0" ]; then
    # Only the directory itself needs an unconditional chown so pwuser
    # can write inside it. Anything already owned correctly is skipped
    # — keeps subsequent boots fast on a populated data tree.
    chown pwuser:pwuser /app/data
    # The -R catches existing entries that were created before this
    # entrypoint shipped (or files a previous root-running container
    # wrote). Errors suppressed because read-only mounts will fail
    # here harmlessly.
    chown -R pwuser:pwuser /app/data 2>/dev/null || true
    exec gosu pwuser "$@"
fi

exec "$@"
