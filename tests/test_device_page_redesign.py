"""The device page after the 2026-11 redesign (summary rail + compact field
rows), rendered for a display of every kind it treats differently
(tests/device_page_kinds.py).

The inventory test is the guard that nothing was lost: every form field name
(with its form association) and every form endpoint the page rendered before
the redesign, per device kind, is still rendered. The baseline in
tests/fixtures/device_page_inventory.json was captured from the old page; a
field may move, it may not disappear.
"""

from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from tests.device_page_kinds import KINDS, build_app, fake_visionect_service

BASELINE = Path(__file__).parent / "fixtures" / "device_page_inventory.json"
OLD_ANCHORS = (
    "overview",
    "identity",
    "rendering",
    "timing",
    "lineups",
    "connection",
    "logs",
    "power",
    "display",
    "schedule",
    "troubleshooting",
)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Any:
    from app import visionect

    mp = pytest.MonkeyPatch()
    mp.setattr(visionect, "service", fake_visionect_service)
    app = build_app(tmp_path_factory.mktemp("device-kinds"))
    client = app.test_client()
    client.post("/login", data={"password": "abcdefgh"})
    yield app, client
    mp.undo()


def _page(client: Any, dev: str, qs: str = "") -> str:
    resp = client.get(f"/settings/devices/{dev}{qs}")
    assert resp.status_code == 200
    return str(resp.get_data(as_text=True))


class _Inventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.names: set[str] = set()
        self.actions: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if "name" in a and tag in ("input", "select", "textarea", "button"):
            form = a.get("form", "")
            self.names.add(
                f"{tag}[name={a['name']}]" + (f"@{form.split('-')[-1]}" if form else "")
            )
        if tag == "form" and a.get("action"):
            self.actions.add(a["action"].split("?")[0])


def _rail(body: str) -> str:
    start = body.index('<aside class="dx-rail" id="overview"')
    return body[start : body.index("</aside>", start)]


def _main(body: str) -> str:
    return body[body.index('<div class="dx-dp-main">') :]


# ---- nothing lost ----------------------------------------------------------


def test_every_field_and_endpoint_of_the_old_page_is_still_rendered(seeded: Any) -> None:
    app, client = seeded
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    kinds = {label: dev for label, dev, _ in KINDS}
    up = app.config["DEVICE_LOGS"].list_uploads("kitchen")
    missing: list[str] = []
    for key, want in baseline.items():
        label, _, variant = key.partition(":")
        dev = kinds[label]
        qs = {"": "", "calibrating": f"?calibrating={dev}", "log": f"?log={up[0].name}"}[variant]
        inv = _Inventory()
        inv.feed(_page(client, dev, qs))
        for field in ("names", "actions"):
            lost = set(want[field]) - getattr(inv, field)
            missing += [f"{key} {field}: {item}" for item in sorted(lost)]
    assert not missing, "\n".join(missing)


@pytest.mark.parametrize(("label", "dev"), [(k[0], k[1]) for k in KINDS])
def test_old_anchors_land_for_every_kind(seeded: Any, label: str, dev: str) -> None:
    _app, client = seeded
    body = _page(client, dev)
    for anchor in OLD_ANCHORS:
        # Logs only for firmware that advertised log upload, as before.
        want = 0 if anchor == "logs" and dev != "kitchen" else 1
        assert body.count(f'id="{anchor}"') == want, (label, anchor)


# ---- the rail ----------------------------------------------------------------


def test_rail_summarises_identity_what_is_on_and_vitals(seeded: Any) -> None:
    _app, client = seeded
    rail = _rail(_page(client, "kitchen"))
    assert '<h1 class="dx-rail-name" data-device-head-name>Pico BIN client (kitchen)</h1>' in rail
    assert "Pico BIN client" in rail and ">REST</span>" in rail
    assert 'data-render-sync="thumb:kitchen"' in rail and "Evening agenda" in rail
    labels = re.findall(r'<span class="dx-vital-k">([^<]+)</span>', rail)
    assert labels == ["Battery", "Next update", "Signal", "Firmware", "Checked in"]
    assert "76%" in rail and 'title="3980 mV"' in rail  # mV in the tooltip only
    assert "20.4 °C · 48% RH at the display" in rail  # environment: one muted line
    # The vital signs and the call-outs follow a push without a reload.
    assert 'data-render-sync="tiles:kitchen"' in rail
    assert 'data-render-sync="callouts:kitchen"' in rail


def test_rail_actions_more_menu_then_preview_then_push_now(seeded: Any) -> None:
    _app, client = seeded
    rail = _rail(_page(client, "kitchen"))
    actions = rail[rail.index('class="dx-rail-actions"') :]
    more = actions.index("data-more-menu")
    assert actions.index("Open mirror") > more and actions.index("Raw PNG preview") > more
    preview = actions.index("<span>Preview</span>")
    push = actions.index("<span>Push now</span>")
    assert more < preview < push  # DOM order = visual order, primary rightmost
    assert 'class="dx-btn is-primary"' in actions[preview:]
    assert 'aria-label="More actions"' in actions


@pytest.mark.parametrize(
    ("dev", "expected"),
    [
        ("study", []),  # mains, fresh, nothing queued: nothing to say
        ("door", ["data-device-queued"]),
        ("reader", ["Battery low, 14%"]),
        ("garden", ["data-device-mode-observation", "Phone last confirmed Bluetooth mode"]),
        ("photos", ["Offline album Family photos", 'form="album-resync-photos"']),
    ],
)
def test_call_outs_only_when_something_needs_attention(
    seeded: Any, dev: str, expected: list[str]
) -> None:
    _app, client = seeded
    rail = _rail(_page(client, dev))
    if not expected:
        assert 'class="dx-callouts"' not in rail
        return
    callouts = rail[rail.index('class="dx-callouts"') :]
    for text in expected:
        assert text in callouts, (dev, text)


def test_overdue_call_out_follows_a_stale_heartbeat(seeded: Any) -> None:
    app, client = seeded
    cache = app.config["DEVICE_STATUS"]
    saved = dict(cache["study"])
    try:
        cache["study"] = {**saved, "received_at": time.time() - 6 * 3600}
        rail = _rail(_page(client, "study"))
        assert "Overdue: last check-in 6 h ago" in rail
    finally:
        cache["study"] = saved
    assert "Overdue" not in _rail(_page(client, "study"))


# ---- field rows ----------------------------------------------------------------


def test_every_setting_is_a_standard_field(seeded: Any) -> None:
    """Fields are label line + control line (toggles: one control line with
    the switch and its label); every combined-form control sits in one."""
    _app, client = seeded
    for _label, dev, _ in KINDS:
        main = _main(_page(client, dev))
        fields = re.findall(r'<div class="dx-f ([^"]*)"', main)
        assert fields, dev
        for cls in fields:
            assert re.match(r"(w-(num|time|sm|md|lg|xl|auto)|is-toggle|dx-f-span)\b", cls), cls
        # A toggle carries no label line: switch and label share the control line.
        for toggle in re.findall(r'<div class="dx-f is-toggle[^"]*"[^>]*>(.*?)</label>', main, re.S):
            assert "dx-f-head" not in toggle
            assert 'class="dx-sw"' in toggle


def test_one_control_per_setting(seeded: Any) -> None:
    _app, client = seeded
    for _label, dev, _ in KINDS:
        main = _main(_page(client, dev))
        names = re.findall(r'<(?:input|select|textarea)[^>]*\bname="([^"]+)"', main)
        repeated = {
            n
            for n in names
            if names.count(n) > 1
            and n not in ("quiet_hours_days", "quiet_hours_all_day", "wakes", "top_left", "choice")
        }
        # (Day chips share a name, as do the 1/5/20-wake, 1-4 and Keep mono /
        # Switch to grey buttons; the Colour depth and Renderer controls both
        # post renderer_id, but a device gets one of them, never both.)
        assert not repeated, (dev, repeated)
    # A sign's sleep interval is set on the frame: read-only, no editor.
    frame = _main(_page(client, "frame"))
    assert "Every 30 min" in frame and "set on the frame" in frame
    assert 'name="sleep_interval_s"' not in frame
    # Everyone else picks it, with no read-only copy beside it.
    pico = _main(_page(client, "kitchen"))
    assert 'name="sleep_interval_s"' in pico and "set on the frame" not in pico


def test_dependent_controls_appear_only_when_relevant(seeded: Any) -> None:
    _app, client = seeded
    tablet = _main(_page(client, "tablet"))
    # Poll every sits beside the Stay awake switch, hidden while it is off.
    stay = tablet[tablet.index('name="always_on"') :]
    stay = stay[: stay.index("</div>")]
    assert re.search(r'data-awake-poll hidden', stay)
    assert 'name="awake_poll_s"' in stay
    # Quiet hours: the fields hide (the script hides and disables them) while
    # Own hours is off, and a note says what applies.
    assert 'data-dep-group data-dep-hide' in tablet
    assert "data-dep-off-note" in tablet


def test_advanced_disclosures_hold_the_rarely_used_controls(seeded: Any) -> None:
    _app, client = seeded

    def details(body: str, summary: str) -> str:
        i = body.index(summary)
        start = body.rindex("<details", 0, i)
        return body[start : body.index("</details>", i)]

    pico = _main(_page(client, "kitchen"))
    wake = details(pico, "Advanced: synchronized wake")
    for name in ("wake_align_mode", "wake_align_anchor", "wake_align_times", "wake_align_apply_all"):
        assert f'name="{name}"' in wake, name
    token = details(pico, "Advanced: access token and transport")
    for action in ("reveal-token", "regenerate-token", "set-transport"):
        assert f'action="/settings/devices/kitchen/{action}"' in token, action
    controls = details(pico, "Advanced: effective map and dashboard ids")
    assert "Effective map" in controls and "Dashboard ids for page:" in controls
    debug = details(pico, "Advanced: debug details and raw manifest")
    assert "Raw instance manifest (secrets masked)" in debug and "Instance file" in debug
    # The main Connection list keeps the token out of plain view.
    conn = pico[pico.index('id="connection"') : pico.index("Advanced: access token")]
    assert "Access token" not in conn


def test_rotation_drives_the_size_swap_and_the_swap_button_is_there(seeded: Any) -> None:
    _app, client = seeded
    display = _main(_page(client, "kitchen"))
    display = display[: display.index('id="schedule"')]
    assert "data-orient-swap" in display
    assert 'name="panel_orientation"' in display and "data-size-swap" in display


# ---- copy --------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "dev"), [(k[0], k[1]) for k in KINDS])
def test_copy_rules(seeded: Any, label: str, dev: str) -> None:
    _app, client = seeded
    page = _page(client, dev)
    body = page[page.index('<p class="dx-crumb">') : page.index("</main>")]
    text = re.sub(r"<script.*?</script>|<[^>]+>", " ", body, flags=re.S)
    assert "—" not in text, label  # no em dashes in UI text
    for mark in ("✓", "✔", "✅", "☑"):
        assert mark not in body, label
    icons = set(re.findall(r'<i class="([^"]+)"', body))
    assert all(c.split()[0] == "ph" for c in icons), icons


def test_field_ids_are_unique(seeded: Any) -> None:
    _app, client = seeded
    for _label, dev, _ in KINDS:
        ids = re.findall(r'\sid="([^"]+)"', _page(client, dev))
        dup = {i for i in ids if ids.count(i) > 1}
        assert not dup, (dev, dup)

