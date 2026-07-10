"""Experimental feature flags.

Resolution order for a flag: the ``TESSERAE_EXPERIMENT_<NAME>`` env var wins
(so a deployment can force it on/off without editing settings.json, and tests
can flip one per-process); otherwise an explicit value in the ``experiments``
settings section; otherwise the flag's built-in default in ``_DEFAULTS``.

Most flags default off. ``composer`` (the Panels canvas editor, issue #60)
defaults ON but is deliberately UNLINKED, no nav entry points at it, so it's
reachable only by an admin who knows ``/experiments/composer/``. That's a
soft-launch posture: dogfoodable without a switch, still hideable by setting
``experiments.composer`` false (or the env var to 0). Route guards call
:func:`is_enabled` per request, so a settings change takes effect with no
restart.

Env var convention: ``TESSERAE_EXPERIMENT_<NAME_UPPER>`` (e.g.
``TESSERAE_EXPERIMENT_COMPOSER`` for :func:`is_enabled("composer")`).

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import os

from flask import current_app

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Built-in defaults for flags with no explicit env/settings value. Absent
# names default off.
_DEFAULTS: dict[str, bool] = {
    "composer": True,
}


def _env_flag(name: str) -> bool | None:
    """The env override for ``name``, or None when the var is unset."""
    raw = os.environ.get(f"TESSERAE_EXPERIMENT_{name.upper()}")
    if raw is None:
        return None
    return raw.strip().lower() in _TRUTHY


def is_enabled(name: str) -> bool:
    """True when experiment ``name`` is switched on. Env var wins, then an
    explicit ``experiments`` settings value, then the built-in default."""
    env = _env_flag(name)
    if env is not None:
        return env
    store = current_app.config.get("SETTINGS_STORE")
    if store is not None:
        section = store.get_section("experiments") or {}
        if name in section:
            return bool(section[name])
    return _DEFAULTS.get(name, False)
