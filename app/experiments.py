"""Experimental feature flags.

Opt-in, off by default. A flag reads on when either the ``experiments``
settings section has it truthy, or the matching environment variable is set.
The env var wins so a deployment can force-enable a flag without editing
settings.json (and so tests can flip one per-process).

Env var convention: ``TESSERAE_EXPERIMENT_<NAME_UPPER>`` (e.g.
``TESSERAE_EXPERIMENT_COMPOSER`` for :func:`is_enabled("composer")`).

Flags gate unfinished features (the Panels canvas editor, issue #60) behind
a route/UI that stays hidden until switched on. Route guards call
:func:`is_enabled` per request, so toggling the settings flag takes effect
without an app restart.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import os

from flask import current_app

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_flag(name: str) -> bool | None:
    """The env override for ``name``, or None when the var is unset."""
    raw = os.environ.get(f"TESSERAE_EXPERIMENT_{name.upper()}")
    if raw is None:
        return None
    return raw.strip().lower() in _TRUTHY


def is_enabled(name: str) -> bool:
    """True when experiment ``name`` is switched on. Env var wins over the
    ``experiments`` settings section; both default off."""
    env = _env_flag(name)
    if env is not None:
        return env
    store = current_app.config.get("SETTINGS_STORE")
    if store is None:
        return False
    section = store.get_section("experiments") or {}
    return bool(section.get(name))
