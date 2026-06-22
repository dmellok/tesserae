#!/usr/bin/env bash
# Background-launch the production Tesserae server on Codespaces /
# devcontainer start. Called from devcontainer.json's
# ``postStartCommand``, which fires every time the container starts
# (first launch from prebuild, plus any subsequent stop/start cycle).
#
# Why this script instead of a one-liner in devcontainer.json:
# detachment has been finicky here. ``postAttachCommand`` with
# ``nohup ... &`` had the dev container runtime occasionally reap the
# child mid-attach. A VS Code task with ``runOn: folderOpen`` failed
# to fire because Codespaces doesn't reliably honour
# ``task.allowAutomaticTasks: "on"`` via customizations. The
# combination here, ``setsid`` (new session, no parent SIGHUP
# propagation) + ``nohup`` (mask the signal anyway) + redirect every
# fd to /dev/null or a log file + background with ``&`` + a ``pgrep``
# guard so a second run is a no-op, is the most bulletproof pattern
# we've tried.

set -euo pipefail

# Already running? Skip. Prevents double-bind on container
# stop/start when the launcher fires again but the daemon survived.
if pgrep -f "^.*/tesserae$" > /dev/null 2>&1; then
  echo "tesserae already running (pid $(pgrep -f '^.*/tesserae$' | head -n1)), skipping launch"
  exit 0
fi

# ``setsid`` creates a new session so the process has no parent or
# controlling terminal, the dev container runtime can't reap it as a
# child even if it tries. ``nohup`` is belt and braces. ``< /dev/null``
# closes stdin so any input the runtime tries to pipe doesn't block.
setsid nohup tesserae > /tmp/tesserae.log 2>&1 < /dev/null &

# Tiny sleep + verify so the log shows a sensible message rather than
# the script silently returning before the daemon binds.
sleep 1
if pgrep -f "^.*/tesserae$" > /dev/null 2>&1; then
  echo "tesserae launched, log: tail -f /tmp/tesserae.log"
else
  echo "tesserae failed to launch, check /tmp/tesserae.log"
  tail -n 20 /tmp/tesserae.log 2>/dev/null || true
  exit 1
fi
