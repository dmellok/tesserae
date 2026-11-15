"""What Home Assistant knows about an OpenDisplay tag (app.opendisplay_tags).

The shapes here are the ones a real Home Assistant returned for three tags:
a Solum 3.5" (184x384, taller than wide), a Solum 4.1" (400x300) and an
OpenDisplay 4.26" mono kit (800x480). No HA is contacted; ``_FakeCore``
stands in for the ha_core module.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app import opendisplay_tags as tags


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    tags.reset_cache()


class _FakeCore:
    def __init__(self, rows: list[dict[str, Any]], diagnostics: dict[str, Any]) -> None:
        self.rows = rows
        self.diagnostics = diagnostics
        self.template_calls = 0
        self.diagnostic_calls: list[str] = []

    def base_url(self) -> str:
        return "http://ha"

    def render_template(self, _template: str, timeout: int = 10) -> str:
        self.template_calls += 1
        return json.dumps(self.rows)

    def request_json(self, path: str, timeout: int = 10) -> Any:
        self.diagnostic_calls.append(path)
        entry = path.rsplit("/", 1)[-1]
        if entry not in self.diagnostics:
            raise RuntimeError("HTTP Error 401: Unauthorized")
        return {"data": {"device_config": {"displays": [self.diagnostics[entry]]}}}


def _core() -> _FakeCore:
    return _FakeCore(
        rows=[
            {
                "device_id": "dev-solum",
                "entry_id": "01M7CWG65MJNEDEK6KNCG9SX8E",
                "model": '3.5" BWRY',
            },
            {
                "device_id": "dev-mono",
                "entry_id": "01M7CWQCRNRKAHHCK9GP2N3KRT",
                "model": '4.3" MONO',
            },
        ],
        diagnostics={
            "01M7CWG65MJNEDEK6KNCG9SX8E": {
                "pixel_width": 184,
                "pixel_height": 384,
                "rotation": 0,
                "color_scheme": 3,
            },
            "01M7CWQCRNRKAHHCK9GP2N3KRT": {
                "pixel_width": 800,
                "pixel_height": 480,
                "rotation": 0,
                "color_scheme": 0,
            },
        },
    )


@pytest.mark.parametrize(
    ("model", "gamut"),
    [
        ('3.5" BWRY', "bwry_4"),
        ('4.3" MONO', "mono"),
        ('2.9" BWR', "bwr_3"),
        ('2.9" BWY', "bwy_3"),
        ('7.3" BWGBRY', "waveshare_e6"),
        ("800x480 BWGBRY_SPLIT", "waveshare_e6"),
        ("296x128 GRAYSCALE_4", "gray_4"),
        ('10.3" GRAYSCALE_16', "gray_16"),
        ('5.7" SEVEN_COLOR', "inky_7colour"),
        ('4.2" 9', None),  # a scheme this build of the SDK does not name
        ("", None),
        (None, None),
    ],
)
def test_gamut_from_model(model: object, gamut: str | None) -> None:
    assert tags.gamut_from_model(model) == gamut


def test_surface_is_the_pixel_size_turned_by_the_tags_own_rotation() -> None:
    """The SDK applies the tag's rotation to whatever it is sent, so the
    shape of picture a tag wants is its pixel size turned by that rotation."""
    display = {"pixel_width": 184, "pixel_height": 384, "color_scheme": 3}
    assert tags.facts_from_display({**display, "rotation": 0})["w"] == 184
    turned = tags.facts_from_display({**display, "rotation": 90})
    assert (turned["w"], turned["h"]) == (384, 184)
    # Older data stored a quarter-turn index rather than degrees.
    indexed = tags.facts_from_display({**display, "rotation": 3})
    assert (indexed["w"], indexed["h"], indexed["tag_rotation"]) == (384, 184, 270)
    assert tags.facts_from_display({**display, "rotation": 180})["h"] == 384


def test_facts_from_display_tolerates_junk() -> None:
    assert tags.facts_from_display(None) == {}
    assert tags.facts_from_display({"pixel_width": 0, "pixel_height": 10}) == {}
    assert tags.facts_from_display({"pixel_width": "x", "pixel_height": 10}) == {}
    assert tags.facts_from_diagnostics({"data": {"device_config": {"displays": []}}}) == {}
    assert tags.facts_from_diagnostics("nope") == {}


def test_detect_reads_size_and_palette_per_tag() -> None:
    found = tags.detect(_core())
    assert found["dev-solum"] == {
        "w": 184,
        "h": 384,
        "tag_rotation": 0,
        "gamut": "bwry_4",
        "model": '3.5" BWRY',
    }
    assert (found["dev-mono"]["w"], found["dev-mono"]["h"]) == (800, 480)
    assert found["dev-mono"]["gamut"] == "mono"


def test_detect_keeps_the_palette_when_diagnostics_are_refused() -> None:
    """A token that is not an administrator's cannot read diagnostics. The
    model string still names the colour scheme, so the palette survives and
    only the size is unknown."""
    core = _core()
    core.diagnostics = {}
    found = tags.detect(core)
    assert found["dev-solum"] == {"model": '3.5" BWRY', "gamut": "bwry_4"}


def test_detect_is_soft_when_home_assistant_is_unreachable() -> None:
    class Down:
        def base_url(self) -> str:
            return "http://ha"

        def render_template(self, _t: str, timeout: int = 10) -> str:
            raise OSError("connection refused")

    assert tags.detect(Down()) == {}
    assert tags.detect(None) == {}
    assert tags.lookup(Down(), "dev-solum") == {}


def test_detect_is_cached_until_asked_fresh() -> None:
    core = _core()
    tags.detect(core)
    tags.detect(core)
    assert core.template_calls == 1
    tags.detect(core, fresh=True)
    assert core.template_calls == 2


def test_an_empty_answer_is_not_cached() -> None:
    core = _core()
    core.rows = []
    assert tags.detect(core) == {}
    core.rows = _core().rows
    assert "dev-solum" in tags.detect(core)


def test_entry_id_is_validated_before_it_reaches_a_url() -> None:
    core = _core()
    core.rows = [{"device_id": "dev-x", "entry_id": "../../api/config", "model": '2.9" BWR'}]
    found = tags.detect(core)
    assert core.diagnostic_calls == []
    assert found["dev-x"]["gamut"] == "bwr_3"


def test_enrich_prefers_the_size_the_tag_reports() -> None:
    rows = [
        {"device_id": "dev-solum", "name": "Office", "model": '3.5" BWRY', "w": 1, "h": 1},
        {"device_id": "dev-unknown", "name": "Other", "model": ""},
    ]
    out = tags.enrich(rows, _core())
    assert (out[0]["w"], out[0]["h"], out[0]["gamut"]) == (184, 384, "bwry_4")
    assert "gamut" not in out[1]
