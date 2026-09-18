"""Note rendering helpers + the paste-detection rule the Send page shares."""

from __future__ import annotations

import base64

import pytest

from app import note_render
from app.note_render import (
    detect_kind,
    normalise_align,
    normalise_size,
    note_html,
    note_label,
    render_note_png,
    split_note,
    theme_variables,
)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("", ""),
        ("   \n ", ""),
        ("https://example.com/poster.png", "image"),
        ("HTTPS://EXAMPLE.COM/A.JPEG", "image"),
        ("https://example.com/photo.webp?w=800", "image"),
        ("https://example.com/shot.HEIC", "image"),
        ("https://ha.local:8123/lovelace/energy", "webpage"),
        ("http://example.com/", "webpage"),
        ("https://example.com/report.pdf", "webpage"),
        ("  https://example.com/x.png  ", "image"),
        ("Back at 3pm", "note"),
        ("https://example.com/a.png\nand a second line", "note"),
        ("see https://example.com", "note"),
        ("example.com/a.png", "note"),
        ("ftp://example.com/a.png", "note"),
    ],
)
def test_detect_kind(text: str, kind: str) -> None:
    assert detect_kind(text) == kind


def test_normalisers_fall_back_to_defaults() -> None:
    assert normalise_size("Medium") == "medium"
    assert normalise_size("huge") == "large"
    assert normalise_size(None) == "large"
    assert normalise_align("centre") == "center"
    assert normalise_align("RIGHT") == "right"
    assert normalise_align("") == "center"


def test_split_note_and_label() -> None:
    assert split_note("\n\n  Back at 3pm \nGone to the vet\n\n") == (
        "Back at 3pm",
        "Gone to the vet",
    )
    assert split_note("Only a headline") == ("Only a headline", "")
    assert split_note("") == ("", "")
    assert note_label("Back at 3pm\nmore") == "Back at 3pm"
    assert note_label("   ") == "Note"
    long = "x" * 80
    assert note_label(long).endswith("…") and len(note_label(long)) == 60


def test_theme_variables_bundled_and_fallback() -> None:
    light = theme_variables("light")
    assert light["--bg"] == "#E7E4DC"
    assert theme_variables(None) == light
    assert theme_variables("no-such-theme") == light
    nord = theme_variables("nord")
    assert nord["--bg"] != light["--bg"]


def test_theme_variables_honours_user_theme_css() -> None:
    extra = '[data-theme="user-mine"]{ --bg: #123456; --text-primary: #ABCDEF; }'
    mine = theme_variables("user-mine", extra)
    assert mine["--bg"] == "#123456"
    assert mine["--text-primary"] == "#ABCDEF"


def test_note_html_escapes_and_lays_out() -> None:
    page = note_html(
        "Back at 3pm\nGone <b>out</b>",
        size="medium",
        align="left",
        theme_vars={"--bg": "#fff", "--text-primary": "#000"},
    )
    assert page.startswith("<!doctype html>")
    assert ":root{--bg:#fff;--text-primary:#000;}" in page
    assert '<div class="note-head">Back at 3pm</div>' in page
    assert '<div class="note-body">Gone &lt;b&gt;out&lt;/b&gt;</div>' in page
    assert '<div class="note-rule"></div>' in page
    assert "font-size:9.5vmin" in page
    assert "justify-content:flex-start" in page
    assert "text-align:left" in page


def test_note_html_headline_only_has_no_rule_or_body() -> None:
    page = note_html("Just this")
    assert '<div class="note-rule">' not in page
    assert '<div class="note-body">' not in page


def test_render_note_png_screenshots_a_data_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_render(request, *, pool=None):
        seen["request"] = request
        seen["pool"] = pool
        return b"PNG"

    monkeypatch.setattr(note_render, "render_to_png", fake_render)
    out = render_note_png("Hello\nworld", w=800, h=480, size="small", align="right", pool="P")
    assert out == b"PNG"
    req = seen["request"]
    assert seen["pool"] == "P"
    assert req.viewport_w == 800 and req.viewport_h == 480
    assert req.is_composer is False
    assert req.url.startswith("data:text/html;charset=utf-8;base64,")
    decoded = base64.b64decode(req.url.split(",", 1)[1]).decode("utf-8")
    assert '<div class="note-head">Hello</div>' in decoded
    assert "font-size:6.5vmin" in decoded
