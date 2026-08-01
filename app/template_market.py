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
