"""Collect per-origin request headers for a dashboard page's webpage cells (#234).

The ``webpage`` widget renders its target inside an ``<iframe src="…">``. There
is no HTML or JavaScript mechanism for putting an ``Authorization`` header on an
iframe navigation, so the credential cannot travel with the widget's own markup.
It has to be attached by Chromium, which means the server has to know about it
before the render starts.

That is what this module does: walk a page's cells, pick out the ``webpage``
ones, and turn their configured headers into the origin-keyed map
:attr:`app.renderer.RenderRequest.headers_by_origin` consumes. The renderer's
interceptor then attaches each set only to requests for its own origin, so one
cell's bearer token is never handed to another cell's upstream, to a font CDN,
or to wherever a redirect points next.

Reading the config here rather than shipping it to the browser is also what
keeps it out of the page. ``compose.html`` serialises every cell's options into
a ``data-options`` attribute for client-side widgets to read, so a token routed
through the widget's own options would sit in the composed DOM. The widget's
``client.js`` has no use for it anyway, since the iframe cannot send it.

A malformed header map on one cell is logged and skipped rather than raised: a
dashboard with six widgets should still render the other five, and the operator
sees the same validation error in the editor when they save. The one thing this
never does is log a value.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.http_headers import HeaderError, header_summary, parse_header_map
from app.renderer import origin_of

if TYPE_CHECKING:
    from app.state.page_store import Page

logger = logging.getLogger(__name__)

# Manifest id of the widget whose cells carry this config. Named rather than
# inferred from a manifest capability: the header plumbing is specific to the
# iframe-embedding widget, and a second widget wanting it should opt in here
# deliberately rather than by declaring an option with a matching name.
WEBPAGE_PLUGIN_ID = "webpage"

HEADERS_OPTION = "headers"
URL_OPTION = "url"


def headers_by_origin_for_page(page: Page) -> dict[str, dict[str, str]] | None:
    """Origin-keyed request headers for every configured ``webpage`` cell, or
    ``None`` when the page has none.

    ``None`` rather than an empty dict on purpose: the renderer installs no
    request interception at all when there are no headers, so a page without
    them renders down exactly the path it did before #234.

    Two cells pointing at the same origin have their maps merged, later cells
    winning a key. That is a rare, ambiguous configuration; merging keeps the
    request valid, and preferring the last cell at least makes it deterministic
    in page order.
    """
    collected: dict[str, dict[str, str]] = {}
    for cell in page.cells:
        if cell.plugin != WEBPAGE_PLUGIN_ID:
            continue
        options: dict[str, Any] = cell.options or {}
        raw_headers = options.get(HEADERS_OPTION)
        if not raw_headers:
            continue
        url = str(options.get(URL_OPTION) or "").strip()
        origin = origin_of(url)
        if not origin:
            logger.warning(
                "page %s cell %s: headers configured but the URL has no host; ignoring",
                page.id,
                cell.id,
            )
            continue
        try:
            headers = parse_header_map(str(raw_headers))
        except HeaderError as err:
            logger.warning(
                "page %s cell %s: ignoring headers (%s)",
                page.id,
                cell.id,
                err,
            )
            continue
        if not headers:
            continue
        logger.debug(
            "page %s cell %s: %s for %s",
            page.id,
            cell.id,
            header_summary(headers),
            origin,
        )
        collected.setdefault(origin, {}).update(headers)
    return collected or None
