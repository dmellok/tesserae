"""Admin auth + settings routes, split by concern.

The original ``app.settings_routes`` was a single ~2000-line module
that grew unmanageable. The same routes now live across eight files
in this package, all decorating one shared ``Blueprint("auth", ...)``
object so every ``url_for("auth.xxx")`` reference in the templates
keeps working unchanged.

External callers (``app.main``, a handful of tests) import from the
``app.settings_routes`` shim, which re-exports ``bp``, ``register``,
and ``APP_FIELDS`` from here.

Layout:

* ``_shared``      , blueprint object + ``current_app`` getters +
                      small form / coercion / redirect helpers.
* ``field_defs``   , hardcoded APP_FIELDS / PANEL_FIELDS / BROKER_FIELDS.
* ``auth_routes``  , ``/setup``, ``/login``, ``/logout``.
* ``index_routes`` , ``GET /settings`` + ``GET /settings/<area>``
                      and the big ``_build_sections`` walker.
* ``update_routes``, generic ``POST /settings/<section_kind>`` save
                      handler for app / panel / broker / renderer-*
                      / plugin-* / device-*.
* ``system_routes``, ``/settings/system/*`` (updates, backups,
                      webhook, telemetry).
* ``devices_routes``, ``/settings/devices/*`` (every device CRUD).
* ``diagnostics_routes``, ``/settings/diagnostics/*`` (test broker,
                           test push).
"""

from __future__ import annotations

from flask import Flask

# Importing each route module triggers its @bp.* decorators, which
# attach the route functions to the shared blueprint object before
# we register it with the app. Re-importing is cheap (Python caches
# the module) so the call site (``register``) is the only place that
# needs to know about the ordering.
from ._shared import bp
from .field_defs import APP_FIELDS, BROKER_FIELDS, PANEL_FIELDS

__all__ = ["APP_FIELDS", "BROKER_FIELDS", "PANEL_FIELDS", "bp", "register"]


def register(app: Flask) -> None:
    # Order doesn't matter, each module just adds routes to ``bp`` -
    # but listing them explicitly here makes the wiring discoverable.
    from . import (  # noqa: F401, imported for side effect (route registration)
        auth_routes,
        devices_routes,
        diagnostics_routes,
        index_routes,
        palette_routes,
        system_routes,
        update_routes,
    )

    app.register_blueprint(bp)
