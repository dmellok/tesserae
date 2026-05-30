"""Backward-compatibility shim.

The routes that used to live here now span :mod:`app.settings`. This
shim re-exports the symbols the rest of the codebase still imports by
the original name:

* ``register`` — called by :mod:`app.main` to attach the blueprint.
* ``bp``       — the shared ``Blueprint("auth", ...)`` object.
* ``APP_FIELDS`` — imported by ``tests/test_mdns.py``.

New code should import from ``app.settings`` directly.
"""

from __future__ import annotations

from app.settings import APP_FIELDS, BROKER_FIELDS, PANEL_FIELDS, bp, register

__all__ = ["APP_FIELDS", "BROKER_FIELDS", "PANEL_FIELDS", "bp", "register"]
