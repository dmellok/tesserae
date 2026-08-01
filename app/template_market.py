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
