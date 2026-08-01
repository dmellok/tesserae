"""Template marketplace client: browse the hosted catalog, install as pages.

The counterpart to :mod:`app.marketplace` for community dashboard templates.
Deliberately NOT wired into ``Marketplace.install()``'s tarball pipeline:
templates are data, not code, so installing one just creates a new (unbound)
canvas Page after the declared inputs are patched in. Revocation removes a
template from the catalog only; pages already created from it are the
installer's own and are never touched.

Safety on install: the canvas is validated through the real
``CanvasLayout`` pydantic model (unknown/malformed structure is rejected),
and the doc is re-fetched server-side from api.tesserae.ink (never trusted
from the browser).
"""

from __future__ import annotations

import uuid
from typing import Any

from app import online, template_export
from app.state.page_store import Page
from app.state.panel_store import CanvasLayout


class TemplateInstallError(Exception):
    """Install failed; ``str(err)`` is safe to show in the browse UI."""


def missing_requirements(
    template: dict[str, Any], installed_records: dict[str, Any], registry: Any
) -> list[str]:
    """Catalog ids from ``requires[]`` that this install doesn't have yet.

    A requirement is satisfied when a marketplace record exists for it OR the
    id happens to resolve as a bundled plugin (older exports listed bundled
    ids defensively)."""
    missing: list[str] = []
    for catalog_id in template.get("requires") or []:
        if catalog_id in installed_records:
            continue
        if registry is not None and registry.get(catalog_id) is not None:
            continue
        missing.append(catalog_id)
    return missing


def install_template(
    slug: str,
    inputs: dict[str, Any],
    *,
    pages_store: Any,
    installed_records: dict[str, Any],
    registry: Any,
) -> Page:
    """Fetch an approved template, patch the installer's input values in, and
    save it as a new unbound canvas Page. Returns the page. Raises
    :class:`TemplateInstallError` (or ``online.TemplateRevokedError``) with a
    user-facing message on any failure."""
    payload = online.fetch_template_doc(slug)
    if payload is None:
        raise TemplateInstallError("template could not be fetched; try again later")
    template = payload.get("template")
    if not isinstance(template, dict):
        raise TemplateInstallError("template payload is malformed")

    missing = missing_requirements(template, installed_records, registry)
    if missing:
        raise TemplateInstallError(
            "missing required marketplace items: " + ", ".join(sorted(missing))
        )

    canvas_dict = template_export.apply_inputs(template, inputs)
    try:
        layout = CanvasLayout.model_validate(canvas_dict)
    except Exception as err:
        raise TemplateInstallError(f"template canvas failed validation: {err}") from err

    page = Page(
        id=uuid.uuid4().hex[:12],
        name=str(template.get("title") or "Template"),
        layout_kind="canvas",
        device_ids=[],
        theme=layout.theme,
        style=layout.style,
        font=layout.font or None,
        canvas=layout,
    )
    pages_store.save(page)
    return page


def _strip_dims_suffix(label: str) -> str:
    """PANEL_PRESETS labels embed dims ("Inky Impression 7.3\", 800x480");
    the resolution group header already shows the dims, so drop the suffix."""
    head, _, tail = label.rpartition(", ")
    if head and "x" in tail and tail.replace("x", "").isdigit():
        return head
    return label


def resolution_device_labels() -> dict[str, list[str]]:
    """ "WxH" -> known device/panel names at that resolution, from the curated
    panel presets. Drives the resolution > device grouping on the Templates
    page (which devices a template natively fits)."""
    from app.panel import PANEL_PRESETS

    out: dict[str, list[str]] = {}
    for preset in PANEL_PRESETS.values():
        key = f"{preset.w}x{preset.h}"
        name = _strip_dims_suffix(preset.label)
        if name not in out.setdefault(key, []):
            out[key].append(name)
    return out


def registered_device_resolutions(devices: Any) -> list[str]:
    """ "WxH" for each panel the user actually has registered, so their
    resolutions group first on the Templates page. Best-effort."""
    from app.panel import device_panel

    out: set[str] = set()
    for device in (getattr(devices, "devices", None) or {}).values():
        try:
            panel = device_panel(device)
        except Exception:
            continue
        if panel is not None:
            out.add(f"{panel.w}x{panel.h}")
    return sorted(out)


# -- install-time input editors -----------------------------------------

# Slots that address a widget's ``cell_options``, mapped to how the target's
# widget id is found on the element. ``source_header`` / ``source_url`` are
# deliberately absent: they're transport fields on a raw URL source with no
# option schema behind them, so they stay plain text.
_SCHEMA_SLOTS = ("options", "source_options", "bind_options")


def _target_widget_id(element: dict[str, Any], target: dict[str, Any]) -> str:
    """The widget whose option schema governs this target, or ``""``."""
    slot = target.get("slot")
    index = target.get("index")
    if slot == "options":
        return str(element.get("widget") or element.get("source") or "")
    if slot == "source_options":
        sources = element.get("sources") or []
        if isinstance(index, int) and 0 <= index < len(sources):
            return str(sources[index].get("key") or "")
    elif slot == "bind_options":
        binds = element.get("bind") or []
        if isinstance(index, int) and 0 <= index < len(binds):
            return str(binds[index].get("source") or "")
    return ""


def resolve_input_specs(template: dict[str, Any], registry: Any) -> list[dict[str, Any]]:
    """Turn a template's declared inputs into field specs for the install form.

    The author's declared ``type`` is only a fallback. Where an input targets a
    widget option, the option's schema is looked up **on this install** and
    materialised (``choices_from`` resolved against the installer's own Home
    Assistant, calendars, and plugins), so the installer gets the same control
    the widget's own config form would show, populated with their entities
    rather than the author's. That's the whole point: the author cannot know
    what is valid here.

    Secret inputs keep a masked text field regardless; an API key has no
    picker. Unresolvable targets (widget not installed, transport slots) fall
    back to the declared type."""
    from app.page_routes import _materialize_cell_options

    canvas = template.get("canvas") or {}
    els = {
        el.get("id"): el for el in canvas.get("els") or [] if isinstance(el, dict) and el.get("id")
    }
    materialised: dict[str, list[dict[str, Any]]] = {}

    def options_for(widget_id: str) -> list[dict[str, Any]]:
        if widget_id not in materialised:
            plugin = registry.get(widget_id) if registry is not None else None
            materialised[widget_id] = (
                _materialize_cell_options([plugin]).get(widget_id, []) if plugin else []
            )
        return materialised[widget_id]

    specs: list[dict[str, Any]] = []
    for item in template.get("inputs") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        # Start from the author's declaration, then let the local schema win.
        spec: dict[str, Any] = {
            "name": name,
            "type": str(item.get("type") or "string"),
            "label": str(item.get("label") or name),
            "default": item.get("default", ""),
            "secret": bool(item.get("secret")),
            "required": bool(item.get("required")),
            "resolved": False,
        }
        if item.get("choices"):
            spec["choices"] = item["choices"]
        if not spec["secret"]:
            for target in item.get("targets") or []:
                if not isinstance(target, dict) or target.get("slot") not in _SCHEMA_SLOTS:
                    continue
                element = els.get(target.get("el"))
                key = target.get("key")
                if not isinstance(element, dict) or not key:
                    continue
                widget_id = _target_widget_id(element, target)
                match = next(
                    (o for o in options_for(widget_id) if str(o.get("name")) == str(key)), None
                )
                if match is None:
                    continue
                # Local schema wins on control type and choices; the author's
                # label survives, since they wrote it to explain the template.
                spec.update({k: v for k, v in match.items() if k not in ("name", "label")})
                spec["name"] = name
                spec["label"] = str(item.get("label") or match.get("label") or name)
                spec["resolved"] = True
                spec["widget"] = widget_id
                break
        if spec["secret"]:
            # Force a masked text field whatever the schema said.
            spec["type"] = "string"
        specs.append(spec)
    return specs


def coerce_input_values(specs: list[dict[str, Any]], form: Any) -> dict[str, Any]:
    """Parse submitted ``opt_<name>`` fields into input values, reusing the
    per-cell coercion so complex controls (multiselect, location search,
    entity overrides) demux exactly as they do in widget config."""
    from app.page_routes import _coerce_cell_option

    out: dict[str, Any] = {}
    for spec in specs:
        name = str(spec["name"])
        out[name] = _coerce_cell_option(spec, form.get(f"opt_{name}"), form)
    return out
