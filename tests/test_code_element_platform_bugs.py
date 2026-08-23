"""Regressions for the code-element renderer's silent-failure class.

Each of these rendered "successfully" and wrong, with nothing in the debug
channel, which is what made them expensive to find.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DECORATE_JS = REPO_ROOT / "static" / "panels" / "decorate.js"


def test_code_element_payload_carries_its_options() -> None:
    """``ctx.options`` was always ``{}``. decorate.js builds it from
    ``el.options``, and the composer's code branch never put options in the
    payload, so a config input declared with ``slot: "options"`` rendered in
    the form, saved into the document, and did nothing at render. Only a
    source's options travelled, and those go to the widget fetch."""
    src = (REPO_ROOT / "app" / "composer.py").read_text(encoding="utf-8")
    code_branch = src.split('if e.kind == "code":', 1)[1].split("_stamp_touch", 1)[0]
    assert '"options": e.options or {}' in code_branch


def test_author_js_runs_in_its_own_scope() -> None:
    """``var top = ...`` at global scope cannot overwrite the read-only
    ``window.top``: the assignment silently fails, ``top`` stays a
    cross-origin Window, and the next property read throws "Blocked a frame
    with origin null", blanking the element. Same for name/status/length/
    self/parent/closed. Inside a function they are ordinary locals."""
    js = DECORATE_JS.read_text(encoding="utf-8")
    assert '"<script>try{(function(){" + (el.js || "")' in js, (
        "author JS must be wrapped in a function, not bare inside try{}"
    )


def test_phosphor_probe_ignores_a_css_variable() -> None:
    """``--ph`` (e.g. "plate height") matched the old ``/\\bph\\b(?!-)/``
    because ``-`` is a non-word character, pulling in the whole icon
    stylesheet, whose ``fill`` rules then blanked every inline SVG on the
    page: correct geometry, nothing painted, no error."""
    js = DECORATE_JS.read_text(encoding="utf-8")
    assert "/\\bph\\b(?!-)/" not in js, "the over-broad bare-word probe is back"

    # Mirror the shipped predicate and check the cases that mattered.
    def probe(s: str) -> bool:
        return bool(
            re.search(r"(^|[^\w-])ph(?![\w-])", s) and re.search(r"(^|[^\w-])ph-[a-z0-9]", s)
        )

    assert not probe(":root{--ph:12px}.plate{height:var(--ph)}")
    assert not probe("the ph value is high")
    assert probe('<i class="ph ph-heart"></i>')


def test_console_log_reaches_the_debug_channel(monkeypatch: Any) -> None:
    """``console.log`` from a code element was dropped outright, so
    print-debugging a sandboxed element was impossible. Errors and warnings
    must keep priority: a chatty element cannot cost a stack trace."""
    from app import renderer

    src = (REPO_ROOT / "app" / "renderer.py").read_text(encoding="utf-8")
    body = src.split("def _on_console", 1)[1].split("def _on_pageerror", 1)[0]
    assert 'if msg.type not in ("error", "warning"):\n            return' not in body, (
        "non-error console levels are being dropped again"
    )
    assert "_DIAG_MAX_CHATTY" in body, "logs must have their own budget, not the shared one"
    assert renderer._DIAG_MAX_EVENTS > 0


def test_css_diagnostic_does_not_report_a_rewrite_as_a_drop() -> None:
    """The render-report CSS analysis diffs authored selectors against what the
    parser kept, but the parser REWRITES what it keeps: legacy ``a:before``
    comes back as ``a::before``. Without collapsing ``::`` the diff called that
    a drop, so an author was told a rule was "dropped by the CSS parser" while
    it was applying fine. decorate.js's in-sandbox check already collapsed it;
    the two analysers disagreed with each other."""
    src = (REPO_ROOT / "app" / "mcp_api.py").read_text(encoding="utf-8")
    norm_line = next(line for line in src.splitlines() if "const normSel" in line)
    body = src.split("const normSel", 1)[1].split(";", 1)[0]
    assert "replace(/::/g, ':')" in body, f"normSel must collapse ::, got {norm_line!r}"

    # Mirror both normalisers and prove they now agree on the forms that matter.
    def norm(s: str) -> str:
        return re.sub(r"\s+", "", s).replace("'", "").replace('"', "").replace("::", ":").lower()

    for authored, parsed in (
        ("a:before", "a::before"),
        ("p:first-letter", "p::first-letter"),
        ("li:nth-child(2n + 1)", "li:nth-child(2n+1)"),
    ):
        assert norm(authored) == norm(parsed), authored


def test_icons_field_round_trips_and_defaults_to_inference() -> None:
    """Explicit icon opt-in, alongside the legacy scan rather than replacing it.

    The scan is a heuristic on author text: it can miss icons a script builds
    after render, and it can over-reach, because the injected stylesheet owns
    ``.ph`` / ``.ph-*`` and an element using ``.ph`` as its OWN class name has
    that span restyled to the icon font. Declaring the field removes the guess;
    omitting it keeps every previously-built element behaving identically."""
    from app.state.panel_store import Element

    assert Element(id="e1", kind="code").icons is None, "default must stay inference"
    for value in (True, False, ["bold", "fill"]):
        assert Element(id="e1", kind="code", icons=value).icons == value

    src = (REPO_ROOT / "app" / "composer.py").read_text(encoding="utf-8")
    code_branch = src.split('if e.kind == "code":', 1)[1].split("_stamp_touch", 1)[0]
    assert '"icons": e.icons' in code_branch, "the field has to reach the sandbox"

    js = DECORATE_JS.read_text(encoding="utf-8")
    assert "function iconChoice(" in js
    # Non-icon libs must keep inferring: the field scopes to icons only, unlike
    # autolibs:false which refuses Chart.js, fonts and everything else too.
    assert "if (weight === undefined) return null; // not an icon lib" in js


def test_create_schedule_doc_matches_the_model() -> None:
    """The tool doc said fires_at was "HH:MM" and never mentioned name, so a
    daily schedule written from the doc 422s twice over."""
    from app.state.schedule_model import Schedule

    assert Schedule.model_fields["name"].is_required(), "name must stay required"
    doc = (REPO_ROOT / "packages" / "tesserae-mcp" / "tesserae_mcp" / "__init__.py").read_text(
        encoding="utf-8"
    )
    body = doc.split("def create_schedule", 1)[1].split("def delete_schedule", 1)[0]
    # The doc still mentions "HH:MM", now to say it is NOT that. What must be
    # gone is the old claim that fires_at takes one.
    assert 'fires_at?\n        ("HH:MM")' not in body
    assert 'fires_at? ("HH:MM")' not in body
    assert "FULL datetime" in body
    assert "REQUIRED" in body and "name" in body
    assert "2000-01-01T06:00:00" in body
