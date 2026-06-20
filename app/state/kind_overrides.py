"""Per-kind defaults overrides (issue #22).

Built-in device kinds (``esp32_client``, ``pi_bin_client``, etc.) ship
with the app and are intentionally not deletable / renamable. But the
*defaults* a kind contributes when a user adds a new instance of it
(panel preset, sleep interval, display-name template) **are** worth
letting the user tune from the UI without editing
``devices/<kind>/device.json`` on disk.

This store layers an override JSON file at
``data/devices/_kind_overrides/<kind_id>.json`` on top of the built-in
manifest. The override merges in at load time (see ``app.device_loader``)
so subsequent ``create_instance`` calls already see the modified
defaults. Removing the override file reverts to the bundled defaults.

Whitelist of editable fields (kept short on purpose; everything else
in a kind manifest is protocol-bound):

* ``display_name`` — string default for new instances, supports a
  ``{room}`` placeholder filled in at registration.
* ``panel_preset`` — string preset id from ``app.panel.PANEL_PRESETS``.
* ``panel_w`` / ``panel_h`` — ints, only meaningful when the preset is
  ``custom``.
* ``panel_orientation`` — one of ``landscape`` / ``portrait`` /
  ``landscape_flipped`` / ``portrait_flipped``.
* ``sleep_interval_s`` — int seconds for the wake cadence.

Any other key in the override file is dropped at load time so a
hand-edit (or a malicious patch) can't widen the surface.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The override directory name. Lives under ``data/devices/`` next to
# the per-instance JSON files; the underscore prefix keeps the
# discover() loop from treating it as an instance file.
OVERRIDES_DIRNAME = "_kind_overrides"

# Fields the override file is allowed to set. Anything else gets
# silently dropped; see the module docstring for rationale.
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "display_name",
        "panel_preset",
        "panel_w",
        "panel_h",
        "panel_orientation",
        "sleep_interval_s",
    }
)


_VALID_ORIENTATIONS: frozenset[str] = frozenset(
    {"landscape", "portrait", "landscape_flipped", "portrait_flipped"}
)


def _coerce(field: str, value: Any) -> Any | None:
    """Coerce a value to the type the field expects. Returns None if
    the value is unusable (caller drops the field)."""
    if field == "display_name":
        if not isinstance(value, str):
            return None
        v = value.strip()
        return v if v else None
    if field == "panel_preset":
        if not isinstance(value, str):
            return None
        v = value.strip()
        return v if v else None
    if field in ("panel_w", "panel_h", "sleep_interval_s"):
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None
    if field == "panel_orientation":
        if not isinstance(value, str):
            return None
        v = value.strip().lower()
        return v if v in _VALID_ORIENTATIONS else None
    return None


class KindOverridesStore:
    """One JSON file per kind under ``<data_root>/_kind_overrides/``.

    Thread-safe (a single lock guards every file write); reads are
    cheap (json.load) so they happen without locking, matching the
    pattern used by the other stores in ``app.state``."""

    def __init__(self, devices_data_root: Path) -> None:
        self._dir = devices_data_root / OVERRIDES_DIRNAME
        self._lock = threading.Lock()

    @property
    def dir(self) -> Path:
        return self._dir

    def get(self, kind_id: str) -> dict[str, Any]:
        """Return the (whitelisted) override for a kind, or an empty
        dict when no override file exists."""
        path = self._dir / f"{kind_id}.json"
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            logger.warning(
                "kind_overrides: failed to read %s (%s); treating as no override",
                path,
                err,
            )
            return {}
        if not isinstance(raw, dict):
            logger.warning(
                "kind_overrides: %s is not a JSON object; treating as no override",
                path,
            )
            return {}
        return _whitelist(raw)

    def set(self, kind_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """Persist a whitelisted, coerced override for ``kind_id``.

        Returns the saved dict (after whitelisting/coercion). Removes
        the override file entirely when the resulting dict is empty
        (so the kind reverts to its bundled defaults instead of
        carrying an empty file the user can't see)."""
        cleaned = _whitelist(values)
        path = self._dir / f"{kind_id}.json"
        with self._lock:
            if not cleaned:
                if path.exists():
                    path.unlink()
                return {}
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")
        return cleaned

    def delete(self, kind_id: str) -> bool:
        """Remove the override file for ``kind_id``. Returns True if a
        file was removed, False if nothing was there."""
        path = self._dir / f"{kind_id}.json"
        with self._lock:
            if path.exists():
                path.unlink()
                return True
        return False

    def has_override(self, kind_id: str) -> bool:
        """Cheap existence check used by the UI's ``MODIFIED`` badge."""
        return (self._dir / f"{kind_id}.json").exists()


def _whitelist(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown keys + coerce values; return only what's storable."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k not in ALLOWED_FIELDS:
            continue
        coerced = _coerce(k, v)
        if coerced is None:
            continue
        out[k] = coerced
    return out
