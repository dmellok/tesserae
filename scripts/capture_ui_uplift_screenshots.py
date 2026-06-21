"""Capture current-state screenshots for every top-level admin page
that hasn't yet had the v0.54 ``.dx-*`` design uplift applied.

Boots an isolated testing-mode Tesserae on a free port (the auth
gate is bypassed under ``testing=True``), pre-populates a small
fleet so the list pages render real rows instead of just empty
states, then walks playwright across each route and writes a
full-page PNG into
``notes/design-handoffs/ui-uplift/screenshots/``.

Run from the repo root:

    .venv/bin/python scripts/capture_ui_uplift_screenshots.py
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
import time
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from playwright.sync_api import sync_playwright

from app.app_factory import REPO_ROOT, create_app


class _ThreadedWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    """The default wsgiref server is single-threaded, which deadlocks
    when the page being rendered itself makes a sub-request back to
    the server (themes pulls /themes/user.css, the dashboards page
    pulls a preview, etc). Mixing in ThreadingMixIn lets each
    in-flight request run in its own worker thread."""

    daemon_threads = True


# Routes to capture. Each entry is (output_filename, url_path,
# optional_setup_js_snippet). The JS snippet runs inside the page
# after navigation + before the screenshot, so a page can be put
# into a particular state (form revealed, disclosure open, etc.)
# instead of always rendering its default first-paint.
ROUTES: list[tuple[str, str, str | None]] = [
    # Top-level admin
    ("dashboards.png", "/pages", None),
    ("send.png", "/send", None),
    # Schedules: default saved-list view + a populated New schedule
    # form (with conditions disclosure open) so the design agent sees
    # both flows.
    ("schedules.png", "/schedules", None),
    (
        "schedules-new-form.png",
        "/schedules",
        # New schedule form revealed via the toggle, with the
        # Conditions disclosure open (empty: shows the "Add
        # condition / Test conditions" picker chrome).
        """
        const card = document.querySelector('[data-new-schedule-form]');
        if (card) card.hidden = false;
        document.querySelectorAll('details.sf-conditions').forEach(d => d.open = true);
        """,
    ),
    (
        "schedules-conditions-open.png",
        # Edit form for the seeded schedule, which carries two
        # conditions; opens the disclosure so the populated picker
        # rows are visible (HA entity + Sun example).
        "/schedules?edit=weekday_15",
        """
        document.querySelectorAll('details.sf-conditions').forEach(d => d.open = true);
        """,
    ),
    # Rotations: default view + the per-step Conditions disclosure
    # expanded so the popover content is visible.
    ("rotations.png", "/rotations", None),
    (
        "rotations-conditions-open.png",
        # Edit mode for the seeded rotation. The saved-list view only
        # shows step names + dwell; the editable form (with the per-
        # step Conditions picker) lives behind ``?edit=<id>``.
        "/rotations?edit=kitchen_loop",
        # Force-open every condition panel so the design agent sees
        # the populated picker layout next to the step controls (the
        # first step carries two sample conditions from the seed).
        """
        document.querySelectorAll('.rot-step-cond-panel').forEach(p => p.hidden = false);
        document.querySelectorAll('.rot-step-cond-toggle').forEach(b =>
          b.setAttribute('aria-expanded', 'true')
        );
        """,
    ),
    ("events.png", "/events", None),
    ("history.png", "/history", None),
    # Themes
    ("themes-list.png", "/themes", None),
    # Widgets browse (marketplace) + plugin index
    ("widgets-browse.png", "/plugins/browse", None),
    ("widgets-index.png", "/plugins", None),
    # Battery diagnostic dashboard
    ("battery-dashboard.png", "/devices/battery", None),
    # System settings tab
    ("settings-system.png", "/settings/system", None),
    # Onboarding wizard (welcome step)
    ("onboarding-welcome.png", "/onboarding", None),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _populate(app) -> None:
    """Seed enough fixture data that list pages render real rows."""
    client = app.test_client()

    # Two devices so the per-instance device cards + send-page
    # device list have content to show.
    client.post(
        "/settings/devices/add",
        data={
            "id": "lounge",
            "kind": "esp32_client",
            "name": "Lounge",
            "panel_preset": "inky_13_3",
        },
    )
    client.post(
        "/settings/devices/add",
        data={
            "id": "kitchen_pi",
            "kind": "pi_bin_client",
            "name": "Kitchen Pi",
            "panel_preset": "inky_7_3",
        },
    )

    # Discovery cache: one REST + one MQTT entry so the Discovered
    # strip has both transport groups populated.
    discovery = app.config["DISCOVERY_CACHE"]
    discovery.record(
        "hallway_esp",
        json.dumps({"battery_pct": 88, "rssi": -68, "ip": "192.168.50.74"}).encode(),
    )
    discovery.record(
        "garage_pico",
        json.dumps(
            {"transport": "rest", "mac": "AA:BB:CC:11:22:33", "ip": "192.168.50.91"}
        ).encode(),
    )

    # Two saved pages so the dashboards list + schedules + send-page
    # picker have something to bind to, AND the rotation has more than
    # one step.
    from app.state.page_store import Page

    page_store = app.config["PAGE_STORE"]
    page_store.save(Page(id="home", name="Home dashboard"))
    page_store.save(Page(id="ambient", name="Ambient"))

    # One active interval schedule (every 15 minutes, weekdays, smart
    # sync on) so the Schedules page renders a populated row + the
    # Next-24-hours lookahead has marks to plot.
    from app.state.schedule_model import Schedule
    from app.state.schedule_store import ScheduleStore

    schedule_store: ScheduleStore = app.config["SCHEDULE_STORE"]
    # A representative AND'd condition set (HA entity presence + a
    # sun guard) so the conditions disclosure has real content when
    # the design agent opens it; an empty conditions list renders as
    # just an empty textarea which doesn't show the picker layout.
    from app.state.conditions import Condition

    sample_conditions = [
        Condition(
            source_kind="ha_entity",
            source_id="person.kayden",
            operator="==",
            value="home",
        ),
        Condition(
            source_kind="sun",
            source_id="",
            operator="is_day",
            value={},
        ),
    ]
    schedule_store.upsert(
        Schedule(
            id="weekday_15",
            name="Weekday 15-min refresh",
            page_id="home",
            type="interval",
            interval_minutes=15,
            days_of_week=[0, 1, 2, 3, 4],
            time_of_day_start="07:00",
            time_of_day_end="22:00",
            smart_sync=True,
            priority=5,
            conditions=sample_conditions,
        )
    )

    # One active rotation cycling through both pages so the Rotations
    # page renders a populated row with two steps.
    from app.state.rotation_model import Rotation, RotationStep
    from app.state.rotation_store import RotationStore

    rotation_store: RotationStore = app.config["ROTATION_STORE"]
    rotation_store.upsert(
        Rotation(
            id="kitchen_loop",
            name="Kitchen alternation",
            steps=[
                # First step carries a sample condition so when the
                # per-step Conditions panel is opened in the
                # rotations-conditions-open variant the picker has
                # populated rows rather than an empty textarea.
                RotationStep(
                    page_id="home",
                    dwell_minutes=30,
                    conditions=sample_conditions,
                ),
                RotationStep(page_id="ambient", dwell_minutes=30),
            ],
            anchor="07:00",
            device_ids=["lounge"],
        )
    )


def main() -> None:
    out_dir = REPO_ROOT / "notes" / "design-handoffs" / "ui-uplift" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    app = create_app(testing=True)
    _populate(app)

    port = _free_port()
    server = make_server(
        "127.0.0.1",
        port,
        app,
        server_class=_ThreadedWSGIServer,
        handler_class=WSGIRequestHandler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.4)

    base = f"http://127.0.0.1:{port}"

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1400, "height": 1800})
            page = ctx.new_page()
            for name, path, setup_js in ROUTES:
                try:
                    page.goto(base + path, wait_until="domcontentloaded")
                    page.wait_for_timeout(500)
                    if setup_js:
                        page.evaluate(setup_js)
                        page.wait_for_timeout(150)
                    page.screenshot(path=str(out_dir / name), full_page=True)
                    print(f"wrote {out_dir / name}")
                except Exception as err:
                    print(f"skip {name} ({path}): {err}")
            browser.close()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
