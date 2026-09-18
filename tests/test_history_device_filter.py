"""History per-display filter (discussion #280).

A ``device`` query arg narrows the feed to pushes that landed on one
display; a chip row under the source chips offers every display the
loaded rows mention, and every other filter link carries the choice along.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _seed(app: Flask, client) -> None:
    for did, name in (("lounge", "Lounge"), ("hall", "Hall")):
        resp = client.post(
            "/settings/devices/add", data={"id": did, "kind": "esp32_client", "name": name}
        )
        assert resp.status_code == 302
    log = app.config["EVENT_LOG"]
    log.record(
        type="push",
        source="page",
        target="kitchen_page",
        status="sent",
        digest="d1",
        extra={"device_ids": ["lounge"]},
    )
    log.record(
        type="push",
        source="page",
        target="hall_page",
        status="sent",
        digest="d2",
        extra={"device_ids": ["hall"]},
    )
    log.record(
        type="push",
        source="scheduler",
        target="both_page",
        status="sent",
        digest="d3",
        extra={"device_ids": ["lounge", "hall"]},
    )


def _rows(html: str) -> str:
    return html[html.index("dx-hist-card") :]


def test_device_filter_keeps_only_that_displays_rows(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _seed(app, client)
    html = client.get("/history?device=hall").get_data(as_text=True)
    rows = _rows(html)
    assert "hall_page" in rows and "both_page" in rows
    assert "kitchen_page" not in rows
    assert rows.count('data-history-row="') == 2


def test_device_chip_row_lists_displays_with_counts(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _seed(app, client)
    html = client.get("/history").get_data(as_text=True)
    strip = html[html.index("dx-hist-displays") : html.index("dx-hist-clear")]
    assert "All displays" in strip
    assert strip.index("All displays") < strip.index("Lounge") < strip.index("Hall")
    assert strip.count("seg-item dx-hist-display") == 3
    # Counts: three loaded rows in all, two per display.
    assert '<span class="seg-count">3</span>' in strip
    assert strip.count('<span class="seg-count">2</span>') == 2
    # Nothing filtered yet: "All displays" is the active chip.
    all_chip = strip[max(0, strip.index("All displays") - 400) : strip.index("All displays")]
    assert "is-on" in all_chip
    assert 'aria-selected="true"' in all_chip


def test_switching_filters_preserves_each_other(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _seed(app, client)
    html = client.get("/history?device=hall&include_skipped=1&sort=dashboard").get_data(
        as_text=True
    )
    strip = html[: html.index("dx-hist-clear")]
    # Source chips keep the display filter; display chips keep the rest.
    assert (
        "source=scheduler&amp;device=hall" in strip or "device=hall&amp;source=scheduler" in strip
    )
    hall_chip = strip[
        strip.rindex("Only pushes to Hall") - 400 : strip.rindex("Only pushes to Hall")
    ]
    assert "is-on" in hall_chip
    assert "include_skipped=1" in hall_chip and "sort=dashboard" in hall_chip
    # The "All displays" link drops only the device arg.
    all_chip = strip[
        strip.index("Pushes to every display") - 400 : strip.index("Pushes to every display")
    ]
    assert "device=" not in all_chip
    assert "include_skipped=1" in all_chip and "sort=dashboard" in all_chip


def test_row_chip_names_the_hardware_on_hover(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _seed(app, client)
    html = client.get("/history").get_data(as_text=True)
    chip = html[
        html.index('class="tg dx-hist-device"') : html.index('class="tg dx-hist-device"') + 120
    ]
    assert 'title="' in chip
    # The kind's display name, not its id.
    assert "esp32_client" not in chip


def test_device_filter_with_no_matching_rows_explains_and_offers_clear(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _seed(app, client)
    resp = client.post(
        "/settings/devices/add", data={"id": "attic", "kind": "esp32_client", "name": "Attic"}
    )
    assert resp.status_code == 302
    html = client.get("/history?device=attic").get_data(as_text=True)
    assert "No pushes to <strong>Attic</strong>" in html
    assert "Clear filters" in html
    # The filtered display still has a chip so the filter can be seen.
    assert "Only pushes to Attic" in html
