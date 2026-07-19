"""List a Home Assistant integration's devices for a config-form picker.

The OpenDisplay-via-HA device kind needs Home Assistant's internal device
id (a long hex string) to target a tag. Rather than have the user copy it
by hand (an easy mismatch with the tag's own name/serial), this queries HA
for the integration's devices and returns ``{device_id, name, model}`` for
a dropdown. The tag's pixel resolution isn't exposed cleanly by HA, so we
best-effort parse it out of the ``model`` string (``"296x128 E Ink"``) to
auto-fill the panel size when it's there.

The query rides on HA's ``/api/template`` endpoint (via ``ha_core``), whose
Jinja context has the device-registry helpers the plain state API lacks.
No WebSocket needed.
"""

from __future__ import annotations

import json
import re
from typing import Any

# HA integration domains are lowercase ``[a-z0-9_]``; validate before
# interpolating into the template string so a bad value can't inject Jinja.
_DOMAIN_RE = re.compile(r"^[a-z0-9_]+$")
# Pixel dimensions as they appear inside a model string, e.g. "296x128".
_WXH_RE = re.compile(r"(\d{2,5})\s*[x×]\s*(\d{2,5})")


def build_template(integration: str) -> str:
    """Jinja that renders a JSON array of the integration's devices."""
    return (
        "{%- set ns = namespace(rows=[]) -%}"
        f"{{%- for d in integration_entities('{integration}')"
        " | map('device_id') | reject('none') | unique -%}"
        "{%- set ns.rows = ns.rows + [{"
        "'device_id': d,"
        " 'name': device_attr(d, 'name_by_user') or device_attr(d, 'name'),"
        " 'model': device_attr(d, 'model')}] -%}"
        "{%- endfor -%}"
        "{{ ns.rows | tojson }}"
    )


def parse_resolution(model: str | None) -> tuple[int, int] | None:
    """Pull (w, h) out of a model string like ``"296x128 E Ink"``; None if
    the model carries no pixel dimensions (most tags report a diagonal)."""
    if not isinstance(model, str):
        return None
    m = _WXH_RE.search(model)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_devices(rendered: str) -> list[dict[str, Any]]:
    """Turn the rendered template JSON into picker rows, adding ``w``/``h``
    when the model string carries a resolution."""
    try:
        rows = json.loads(rendered)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("device_id"):
            continue
        model = row.get("model")
        entry: dict[str, Any] = {
            "device_id": str(row["device_id"]),
            "name": str(row.get("name") or row["device_id"]),
            "model": str(model) if model else "",
        }
        res = parse_resolution(model if isinstance(model, str) else None)
        if res:
            entry["w"], entry["h"] = res
        out.append(entry)
    out.sort(key=lambda r: r["name"].lower())
    return out


def list_integration_devices(mod: Any, integration: str) -> tuple[list[dict[str, Any]], str | None]:
    """Return (devices, error). ``mod`` is the ha_core server module. A
    missing / unconfigured HA yields an empty list with an error string the
    UI can show, so the picker degrades to manual entry rather than 500."""
    if not _DOMAIN_RE.match(integration):
        return [], "invalid integration"
    if mod is None or not hasattr(mod, "render_template"):
        return [], "Home Assistant Core plugin unavailable"
    try:
        rendered = mod.render_template(build_template(integration))
    except Exception as exc:
        # Surface any HA / transport error to the UI so the picker can fall
        # back to manual entry instead of failing the whole config form.
        return [], str(exc)
    return parse_devices(rendered), None
