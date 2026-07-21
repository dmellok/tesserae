"""CLI entry point + backward-compat re-exports.

The Flask app factory now lives in :mod:`app.app_factory` and the MQTT
transport wiring (rebuild, subscriptions, status merge, no-op client
factory for tests) lives in :mod:`app.transport_wiring`. This module
keeps the ``tesserae`` console script entry point and re-exports the
handful of symbols the rest of the codebase still imports by the old
``app.main`` path:

* ``REPO_ROOT``, repo root :class:`pathlib.Path`. Used by tests +
  renderer smoke tests to locate plugins/ renderers/ devices/ on disk.
* ``create_app``, the Flask factory.
* ``_resolve_client_id``, MQTT client-id resolver. Imported by
  ``tests/test_client_id.py``.
* ``status_changed_meaningfully`` / ``merge_status_parsed``, heartbeat
  helpers. Imported by ``tests/test_event_log.py`` and
  ``tests/test_device_routes.py``.

New code should import from :mod:`app.app_factory` or
:mod:`app.transport_wiring` directly.
"""

from __future__ import annotations

import logging
import os

from app.app_factory import REPO_ROOT, create_app
from app.transport_wiring import (
    _resolve_client_id,
    merge_status_parsed,
    status_changed_meaningfully,
)

__all__ = [
    "REPO_ROOT",
    "_resolve_client_id",
    "_serve",
    "create_app",
    "merge_status_parsed",
    "status_changed_meaningfully",
]


def _serve(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m app.main`` and the ``tesserae``
    console script. Defaults to a production WSGI server (waitress);
    pass ``--dev`` to opt into Flask's reload + debugger dev server.
    """
    import argparse

    # Windows-only no-op on POSIX: when we were spawned by the in-app
    # updater's restart(), block until the parent has fully exited so the
    # listening socket is released before waitress tries to bind it.
    from app.updater import wait_for_parent_exit

    wait_for_parent_exit()

    parser = argparse.ArgumentParser(prog="tesserae", description="Tesserae dashboard server.")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Use Flask's dev server (auto-reload, debugger). "
        "Default is waitress, a production WSGI server.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    parser.add_argument(
        "--log-level",
        default=None,
        help="Root log level: trace/debug/info/warning/error (default: info). "
        "Also settable via the TESSERAE_LOG_LEVEL env var (this flag wins). "
        "The Home Assistant add-on uses its own Log level option instead.",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Clear the stored admin password and exit. The next request "
        "drops to /setup so a fresh one can be picked. Resolves the data "
        "root from TESSERAE_DATA_ROOT or <repo>/data.",
    )
    args = parser.parse_args(argv)

    if args.reset_password:
        _reset_password()
        return

    # Record the real internal bind port so the in-process renderer fetches
    # /compose over loopback on the port Flask actually listens on, not the
    # port the browser used. Behind a reverse proxy / k8s Service / Docker
    # port map (external 4567 -> container 8765), request.host carries the
    # external port, and a loopback fetch to it is refused because nothing
    # listens there inside the container (#129). The HA add-on already sets
    # TESSERAE_BIND_PORT in its own config for the same reason; setdefault
    # keeps that value and covers every other CLI-launched deployment.
    os.environ.setdefault("TESSERAE_BIND_PORT", str(args.port))

    # Log level for self-hosted installs (Docker / LXC / bare): --log-level
    # wins, then TESSERAE_LOG_LEVEL, else INFO. The HA add-on's log_level
    # option is applied just below and takes precedence there.
    from app.ha_options import apply_log_level, load_options, resolve_log_level

    level = resolve_log_level(args.log_level) or resolve_log_level(
        os.environ.get("TESSERAE_LOG_LEVEL")
    )
    logging.basicConfig(
        level=level if level is not None else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s, %(message)s",
    )
    # Honour HA's log_level option before create_app so its own startup
    # log lines respect it. apply_log_level is a no-op when there's no
    # options.json (i.e. not running as an HA Add-on).
    _ha_options = load_options()
    if _ha_options is not None:
        apply_log_level(_ha_options)
    app = create_app(dev=args.dev)

    if args.dev:
        logging.getLogger(__name__).info(
            "Starting Flask DEV server on http://%s:%d/ (reload + debugger ON)",
            args.host,
            args.port,
        )
        app.run(host=args.host, port=args.port, debug=True)
        return

    # Production: waitress. Single-process, multi-threaded, fine for
    # the single-user appliance. Avoids the "DO NOT USE IN PRODUCTION"
    # warning Flask's dev server prints on startup.
    from waitress import serve

    # The screenshot pipeline is a self-request: Chromium navigates back to
    # this server's /compose/<id>, which needs a free worker thread to be
    # served. A blocked render holds its caller's thread for up to ~105s, and
    # every open SSE stream (/events/stream, canvas /stream) pins a thread for
    # the life of the connection. With too few threads, a couple of open
    # editors plus a render or two starve the inner /compose request and every
    # render times out. 24 gives generous headroom (renders are serialised to
    # one at a time by the single browser worker, so only ~1 free thread is
    # ever needed to serve the inner compose); override with TESSERAE_THREADS.
    threads = 24
    raw_threads = os.environ.get("TESSERAE_THREADS", "").strip()
    if raw_threads:
        try:
            threads = max(4, int(raw_threads))
        except ValueError:
            logging.getLogger(__name__).warning(
                "ignoring invalid TESSERAE_THREADS=%r; using %d", raw_threads, threads
            )
    logging.getLogger(__name__).info(
        "Starting waitress on http://%s:%d/ with %d threads  (--dev for Flask dev server)",
        args.host,
        args.port,
        threads,
    )
    serve(app, host=args.host, port=args.port, threads=threads, ident="tesserae")


def _reset_password() -> None:
    """Implementation of ``tesserae --reset-password``. Loads the settings
    store from the same data-root resolution the app factory uses
    (``TESSERAE_DATA_ROOT`` env, else ``<repo>/data``), wipes the auth
    section, and prints what happened. Designed to run without booting
    Flask so there's nothing to race with."""
    import os
    from pathlib import Path

    from app import auth
    from app.state.settings_store import SettingsStore

    env_root = os.environ.get("TESSERAE_DATA_ROOT", "").strip()
    data_root = Path(env_root) if env_root else REPO_ROOT / "data"
    settings_path = data_root / "core" / "settings.json"

    if not settings_path.exists():
        print(f"No settings.json at {settings_path}, nothing to reset.")
        return

    store = SettingsStore(settings_path)
    was_set = auth.password_is_set(store)
    auth.clear_password(store)
    if was_set:
        print(f"Cleared admin password in {settings_path}.")
        print("Next page load drops to /setup so a fresh one can be picked.")
    else:
        print(f"No admin password was set in {settings_path}. Nothing to clear.")


if __name__ == "__main__":
    _serve()
