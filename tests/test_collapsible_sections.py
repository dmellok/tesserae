"""Collapsible sections: the shared markup (``section_card`` and ``card_head``
with ``collapse``) on the pages that stack sections, the opt-out, and the
shared sub-section title class. The behaviour (collapse, remember, hash,
invalid field) is in tests/test_collapsible_browser.py."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.main import REPO_ROOT, create_app


class _Collect(HTMLParser):
    """Ids, and the toggle buttons with their aria attributes."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.sections: list[dict[str, str | None]] = []
        self.toggles: list[dict[str, str | None]] = []
        self.heads = 0
        self.bodies: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"] or "")
        if "data-collapsible" in a:
            self.sections.append(a)
        if "data-collapsible-head" in a:
            self.heads += 1
        if "data-collapsible-body" in a:
            self.bodies.append(a)
        if "data-collapsible-toggle" in a:
            self.toggles.append(a)


def _parse(html: str) -> _Collect:
    c = _Collect()
    c.feed(html)
    return c


@pytest.fixture
def admin(tmp_path: Path) -> FlaskClient:
    app: Flask = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    return app.test_client()


def _assert_wired(html: str, at_least: int) -> _Collect:
    page = _parse(html)
    assert len(page.toggles) >= at_least, (len(page.toggles), at_least)
    # one section, header, body and toggle per collapsible section
    assert len(page.sections) == len(page.toggles) == len(page.bodies) == page.heads
    ids = [a["id"] for a in page.sections]
    assert all(ids) and len(set(ids)) == len(ids), ids
    for t in page.toggles:
        assert t["type"] == "button"
        assert t["aria-expanded"] == "true", "every section starts expanded"
        controls = t["aria-controls"]
        assert controls and controls in page.ids, controls
        assert controls.endswith("-body")
    for b in page.bodies:
        assert b["id"] and "hidden" not in b
    assert "ph-caret-down" in html
    assert "static/collapsible.js" in html
    assert "style/collapsible.css" in html
    return page


@pytest.mark.parametrize(
    ("url", "at_least", "section_ids"),
    [
        ("/stats/", 5, {"sec-pushes-per-day", "sec-displays", "sec-outcomes"}),
        ("/settings/server", 5, {"server-group-network", "broker"}),
        ("/settings/system", 5, {"system-updates", "backups", "mcp", "features"}),
        ("/settings/about", 2, {"about-project", "about-community"}),
        ("/settings/companion", 2, {"companion-install", "companion-app"}),
        ("/settings/genai", 3, {"genai-account", "genai-spending"}),
        ("/settings/relay", 1, {"relay-link"}),
        ("/plugins/calendar_core/", 4, {"cal-add", "cal-feeds"}),
        ("/plugins/", 1, set()),
    ],
)
def test_pages_that_stack_sections_are_collapsible(
    admin: FlaskClient, url: str, at_least: int, section_ids: set[str]
) -> None:
    resp = admin.get(url, follow_redirects=True)
    assert resp.status_code == 200
    page = _assert_wired(resp.get_data(as_text=True), at_least)
    assert section_ids <= {a["id"] for a in page.sections}


@pytest.mark.parametrize(
    "url",
    ["/devices/battery", "/events", "/history", "/send", "/settings/firmware", "/settings/devices"],
)
def test_single_section_and_tool_pages_are_not(admin: FlaskClient, url: str) -> None:
    resp = admin.get(url, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "data-collapsible-toggle" not in html
    assert "data-collapsible-head" not in html


def test_section_card_opt_out_and_default(app: Flask) -> None:
    env = app.jinja_env
    on = env.from_string(
        '{% from "_components.html" import section_card %}'
        "{% call section_card(icon='gear', title='Network & Time') %}body{% endcall %}"
    ).render()
    page = _parse(on)
    assert [a["id"] for a in page.sections] == ["sec-network-time"]
    assert page.toggles[0]["aria-controls"] == "sec-network-time-body"
    assert page.toggles[0]["aria-expanded"] == "true"
    assert "Collapse Network &amp; Time" in on

    off = env.from_string(
        '{% from "_components.html" import section_card %}'
        "{% call section_card(icon='gear', title='Network', collapsible=False) %}body{% endcall %}"
    ).render()
    assert "data-collapsible" not in off
    assert "ph-caret-down" not in off
    assert 'class="dx-section-body"' in off

    explicit = env.from_string(
        '{% from "_components.html" import section_card %}'
        "{% call section_card(icon='gear', title='Network', id='net') %}body{% endcall %}"
    ).render()
    assert 'id="net"' in explicit
    assert 'id="net-body"' in explicit


def test_header_keeps_actions_before_the_chevron(app: Flask) -> None:
    html = app.jinja_env.from_string(
        '{% from "_components.html" import section_card %}'
        "{% call section_card(icon='gear', title='Feeds', id='f',"
        " meta={'label': 'Count', 'value': 3},"
        " cta={'label': 'New', 'href': '/x', 'icon': 'plus'}) %}body{% endcall %}"
    ).render()
    assert (
        html.index("dx-section-meta")
        < html.index('href="/x"')
        < html.index("data-collapsible-toggle")
    )


def test_card_head_collapse_puts_the_chevron_last_in_the_action_slot(app: Flask) -> None:
    html = app.jinja_env.from_string(
        '{% from "_components.html" import card_head %}'
        "{{ card_head('Feeds', action='<a href=\"/x\">Add</a>'|safe, collapse='feeds') }}"
    ).render()
    assert "data-collapsible-head" in html
    assert html.index('href="/x"') < html.index("data-collapsible-toggle")
    assert 'aria-controls="feeds-body"' in html
    plain = app.jinja_env.from_string(
        '{% from "_components.html" import card_head %}{{ card_head("Feeds") }}'
    ).render()
    assert "data-collapsible" not in plain
    assert "None" not in plain


def test_sub_section_titles_share_one_class(admin: FlaskClient) -> None:
    html = admin.get("/stats/").get_data(as_text=True)
    assert 'class="dx-subhead"' in html
    css = (REPO_ROOT / "static/style/settings.css").read_text()
    block = css[css.index(".dx-subhead,") : css.index("}", css.index(".dx-subhead,"))]
    for cls in (".device-subhead", ".themes-strip-head", ".wizard-subhead"):
        assert cls in block
    assert "font-size: var(--t-subhead-size, 18px)" in block
    assert "text-transform: none" in block
