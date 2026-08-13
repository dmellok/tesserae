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
    # MCP API (app.mcp_api): lets an agent build canvas dashboards over a
    # token-authed surface. Off by default since it opens a new (auth-gated)
    # API; opt in from Settings → System → MCP.
    "mcp": False,
    # Template marketplace (share + browse community dashboard templates via
    # api.tesserae.ink). Off by default while the hosted review pipeline is
    # settling in; gates the Share dialog, the share routes, and the Browse
    # Templates tab in one switch.
    "templates": False,
}


# Settings → System → Experiments card: one row per flag. Kept here beside
# _DEFAULTS so adding a flag and describing it happen in the same file.
CATALOG: tuple[dict[str, str], ...] = (
    {
        "name": "composer",
        "label": "Panels canvas editor",
        "description": (
            "The freeform WYSIWYG dashboard editor at /experiments/composer/. "
            "On by default but unlinked from the nav."
        ),
    },
    {
        "name": "mcp",
        "label": "MCP API (agent access)",
        "description": (
            "The token-authed /api/mcp surface AI agents use to build canvas "
            "dashboards. Managed in detail by the MCP card below."
        ),
    },
    {
        "name": "templates",
        "label": "Template marketplace",
        "description": (
            "Share dashboards to the community catalog and install templates "
            "from Browse. Submissions are reviewed before they go public."
        ),
    },
)


# Flags whose feature is hosted on api.tesserae.ink, so switching them on does
# nothing until the master online-features opt-in is also on. The Settings row
# says so rather than leaving the toggle a silent no-op (#224).
REQUIRES_ONLINE: frozenset[str] = frozenset({"templates"})


def _env_flag(name: str) -> bool | None:
    """The env override for ``name``, or None when the var is unset."""
    raw = os.environ.get(f"TESSERAE_EXPERIMENT_{name.upper()}")
    if raw is None:
        return None
    return raw.strip().lower() in _TRUTHY


def env_override(name: str) -> bool | None:
    """The forced env-var value for ``name`` (None = not forced). The Settings
    card disables a row's toggle when a deployment pins the flag this way."""
    return _env_flag(name)


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
