"""news_front_pages: the composer renders a front page at every supported
size and serves the image; plus the background downloads, fallbacks, rotation,
locking, pruning and paper-list rules in server.py. Freedom Forum is faked
throughout: the fake ``urlopen`` answers from a dict of URL → page and 404s
everything else."""

from __future__ import annotations

import contextvars
import http.client
import importlib.util
import io
import json
import os
import re
import sys
import threading
import time
import types
import urllib.error
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from PIL import Image

from plugins.news_front_pages import server

TODAY = date(2026, 10, 25)
CDN = "https://cdn.freedomforum.org/dfp"
IDLE = 2 * server.PRUNE_GRACE_S


def _jpeg(width: int = 700, height: int = 1342) -> bytes:
    buf = io.BytesIO()
    Image.new("L", (width, height), 255).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


PAGE = _jpeg()


def _url(code: str, day: int) -> str:
    return f"{CDN}/jpg{day}/lg/{code}.jpg"


class _Resp:
    """A response: its body, headers, a read error to raise, or a callback run
    on every read (to drip bytes slowly)."""

    def __init__(
        self,
        body: bytes,
        headers: dict[str, str] | None = None,
        error: Exception | None = None,
        on_read: Any = None,
    ) -> None:
        self._body = io.BytesIO(body)
        self._error = error
        self._on_read = on_read
        self.headers = headers or {}
        self.reads = 0

    def read1(self, limit: int = -1) -> bytes:
        self.reads += 1
        if self._on_read is not None:
            self._on_read()
            return self._body.read(1)
        if self._error is not None:
            raise self._error
        return self._body.read1(limit)

    def read(self, limit: int = -1) -> bytes:
        if self._error is not None:
            raise self._error
        return self._body.read(limit)

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class Clocks:
    """The wall clock (persisted times; starts at the real time so file mtimes
    line up) and the monotonic one (a download's budget), both moved by hand."""

    def __init__(self) -> None:
        self.wall = time.time()
        self.mono = 1000.0


# The fake records the value the worker thread sees here, to show the worker
# runs in a copy of the render's context (where the egress allowlist lives).
CONTEXT_PROBE: contextvars.ContextVar[str] = contextvars.ContextVar("probe", default="")


class FakeCdn:
    """Stand-in for ``urllib.request.urlopen``. ``pages`` maps a URL to a body
    (or a ``_Resp``); anything else is a 404, or a refused connection when
    ``down`` is set. With ``gate`` set, a request waits until it opens. Every
    URL asked for lands in ``calls``."""

    def __init__(self, pages: dict[str, bytes | _Resp] | None = None) -> None:
        self.pages = pages or {}
        self.calls: list[str] = []
        self.agents: list[str | None] = []
        self.down = False
        self.gate: threading.Event | None = None
        self.contexts: list[str] = []

    def __call__(self, req: Any, timeout: float | None = None) -> _Resp:
        url = req if isinstance(req, str) else req.full_url
        self.calls.append(url)
        self.agents.append(None if isinstance(req, str) else req.get_header("User-agent"))
        self.contexts.append(CONTEXT_PROBE.get())
        if self.gate is not None:
            assert self.gate.wait(10), "test gate never opened"
        if self.down:
            raise urllib.error.URLError("connection refused")
        hit = self.pages.get(url)
        if hit is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))  # type: ignore[arg-type]
        return hit if isinstance(hit, _Resp) else _Resp(hit)


@pytest.fixture
def clocks(monkeypatch: pytest.MonkeyPatch) -> Clocks:
    c = Clocks()
    monkeypatch.setattr(server, "_wall", lambda: c.wall)
    monkeypatch.setattr(server, "_clock", lambda: c.mono)
    return c


@pytest.fixture
def cdn(monkeypatch: pytest.MonkeyPatch, clocks: Clocks, tmp_path: Path) -> Iterator[FakeCdn]:
    """Freedom Forum, faked. The PDF rasteriser is off here, so these tests
    see the JPEG path alone; the ``pdf`` fixture turns it on."""
    fake = FakeCdn()
    monkeypatch.setattr(server, "_edition_today", lambda: TODAY)
    monkeypatch.setattr(server, "rasteriser_available", lambda: False)
    with patch("urllib.request.urlopen", fake):
        try:
            yield fake
        finally:
            if fake.gate is not None:
                fake.gate.set()
            assert server._join_worker(tmp_path, 10)


def _fetch(
    tmp_path: Path,
    *,
    paper: str = "",
    papers: Any = None,
    preview: bool = False,
    fresh: bool = False,
) -> dict[str, Any]:
    """A render. ``papers`` is the paper list in config.json (any form it was
    ever saved in); None leaves no config.json, so the defaults apply."""
    _configure(tmp_path, papers)
    ctx = {"data_dir": str(tmp_path), "preview": preview, "fresh": fresh}
    return server.fetch({"paper": paper}, {}, ctx=ctx)


def _configure(tmp_path: Path, papers: Any = None, **more: Any) -> None:
    path = tmp_path / server.CONFIG_FILE
    if papers is None and not more:
        path.unlink(missing_ok=True)
        return
    data = {**more} if papers is None else {"papers": papers, **more}
    tmp_path.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _settle(tmp_path: Path) -> None:
    assert server._join_worker(tmp_path, 10), "downloads didn't finish"


def _fetch_settled(tmp_path: Path, **kw: Any) -> dict[str, Any]:
    """A render, its downloads, then the render that shows them."""
    _fetch(tmp_path, **kw)
    _settle(tmp_path)
    return _fetch(tmp_path, **kw)


def _seed(tmp_path: Path, name: str, body: bytes = PAGE, *, idle_s: float = 0.0) -> Path:
    """Save a page, last used ``idle_s`` seconds ago."""
    path = tmp_path / "pages" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    if idle_s:
        then = time.time() - idle_s
        os.utime(path, (then, then))
    return path


def _state(tmp_path: Path) -> dict[str, Any]:
    return server._read_json(tmp_path / "state.json")


# ----- rendering through the app ---------------------------------------------


def _app_module(app: Flask) -> ModuleType:
    """The loader imports server.py under its own module name, so the app's copy
    is patched separately from the one this file imports."""
    module = app.config["PLUGIN_REGISTRY"].get("news_front_pages").server_module
    assert module is not None
    return module  # type: ignore[no-any-return]


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_widget_renders(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch, size: str
) -> None:
    module = _app_module(app)
    monkeypatch.setattr(module, "_edition_today", lambda: TODAY)
    data_dir = Path(app.config["PLUGIN_REGISTRY"].get("news_front_pages").data_dir)
    render = f"/_test/render?plugin=news_front_pages&size={size}"
    fake = FakeCdn({_url(c, 25): PAGE for c in ("NY_NYT", "WSJ", "USAT")})
    with patch("urllib.request.urlopen", fake):
        first = client.get(render)
        assert module._join_worker(data_dir, 10)
        resp = client.get(render)
    assert first.status_code == 200
    assert "pending" in first.get_data(as_text=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="news_front_pages"' in body
    assert "/plugins/news_front_pages/page/NY_NYT_20261025.jpg" in body

    image = client.get("/plugins/news_front_pages/page/NY_NYT_20261025.jpg")
    assert image.status_code == 200
    assert image.mimetype == "image/jpeg"
    assert image.data == PAGE
    image.close()


def test_page_route_serves_only_page_files(app: Flask, client: FlaskClient) -> None:
    pages = Path(app.config["PLUGIN_REGISTRY"].get("news_front_pages").data_dir) / "pages"
    pages.mkdir(parents=True)
    (pages / "notes.jpg").write_bytes(PAGE)
    assert client.get("/plugins/news_front_pages/page/notes.jpg").status_code == 404
    assert client.get("/plugins/news_front_pages/page/state.json").status_code == 404
    assert client.get("/plugins/news_front_pages/page/WSJ_20261025.jpg").status_code == 404


def test_page_route_is_render_safe(app: Flask) -> None:
    assert "news_front_pages.serve_page" in app.config["RENDER_SAFE_ENDPOINTS"]


# ----- the paper list --------------------------------------------------------

NYT_CSS = "transform: translateY(-3.5%)"
WSJ_CSS = "transform: translateY(-2%)"


def test_the_manifest_declares_no_settings() -> None:
    """The papers live in the widget's own config.json, edited on its admin
    page; Settings > Widgets has nothing for it."""
    manifest = json.loads((Path(server.__file__).parent / "plugin.json").read_text())
    assert "settings" not in manifest and "admin_title" not in manifest
    assert manifest["cell_options"][0]["choices_from"] == "papers"
    rows, errors = server.validate_rows(server.DEFAULT_PAPERS)
    assert errors == {} and rows == server.DEFAULT_PAPERS
    assert len(server.DEFAULT_PAPERS) <= server.MAX_PAPERS


def test_parse_papers_defaults() -> None:
    assert server.parse_papers(server.DEFAULT_PAPERS) == [
        server.Paper("NY_NYT", True, NYT_CSS),
        server.Paper("WSJ", True, WSJ_CSS),
        server.Paper("USAT", True, ""),
        server.Paper("AZ_ADS", False, ""),
    ]


def test_parse_papers_reads_rows() -> None:
    rows = [
        {"enabled": True, "code": " ny_nyt ", "css": "filter: contrast(1.2)", "extra": 1},
        {"enabled": True, "code": ""},  # an empty row
        {"enabled": True, "code": "   "},
        {"enabled": True, "code": "../etc"},  # not a code
        {"enabled": True, "code": "NY NYT"},
        {"enabled": True, "code": ["NY_NYT"]},
        "not a row",
        None,
        {"code": "USAT"},  # no enabled: in the rotation
        {"enabled": "false", "code": "TX_HC"},  # a hand-edited settings file
        {"enabled": 0, "code": "WSJ"},
        {"enabled": True, "code": "WSJ", "css": WSJ_CSS},  # beats the switched-off WSJ
        {"enabled": True, "code": "USAT", "css": "opacity: 0.5"},  # a later duplicate: ignored
    ]
    assert server.parse_papers(rows) == [
        server.Paper("NY_NYT", True, "filter: contrast(1.2)"),
        server.Paper("USAT", True, ""),
        server.Paper("TX_HC", False, ""),
        server.Paper("WSJ", True, WSJ_CSS),
    ]
    # The same rows as a JSON string read the same.
    assert server.parse_papers(json.dumps(rows[:2])) == [
        server.Paper("NY_NYT", True, "filter: contrast(1.2)")
    ]


@pytest.mark.parametrize("value", [None, 7, {"code": "WSJ"}, "[not json", [], ""])
def test_parse_papers_finds_nothing_in_junk(value: Any) -> None:
    assert server.parse_papers(value) == []


def test_parse_papers_stops_at_max_papers() -> None:
    rows = [{"code": f"P{i}"} for i in range(server.MAX_PAPERS + 10)]
    assert len(server.parse_papers(rows)) == server.MAX_PAPERS


def test_old_text_setting_still_reads() -> None:
    """The setting used to be a textarea: ``CODE [trim%]`` per line, ``#``
    to switch a line off. A saved value of that kind keeps working, the
    trim turned into the CSS that does the same."""
    text = "# My papers\nny_nyt 3.5\n\nbad code!\nWSJ x\nUSAT 80\nNY_NYT 4\n#  tx_hc 1.5\nBRA^PR"
    assert server.parse_papers(text) == [
        server.Paper("NY_NYT", True, NYT_CSS),
        server.Paper("USAT", True, "transform: translateY(-25%)"),
        server.Paper("TX_HC", False, "transform: translateY(-1.5%)"),
    ]
    assert server.parse_papers("NY_NYT 3.5\nWSJ 2\nUSAT\n# AZ_ADS") == server.parse_papers(
        server.DEFAULT_PAPERS
    )


# ----- per-paper CSS ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("transform: translateY(-3.5%);", "transform: translateY(-3.5%)"),
        (
            "object-position: 50% 3%; filter: contrast(1.15) grayscale(1);",
            "object-position: 50% 3%; filter: contrast(1.15) grayscale(1)",
        ),
        (
            "TRANSFORM : Scale( 1.2 ) ;transform-origin:top   center",
            "transform: Scale( 1.2 ); transform-origin: top center",
        ),
        ("height: 100%; object-fit: cover", "height: 100%; object-fit: cover"),
        ("clip-path: inset(3% 0 0 0)", "clip-path: inset(3% 0 0 0)"),
        ("margin-top: -2% !important", "margin-top: -2%"),
        ("/* trim */ transform: translateY(-2%)", "transform: translateY(-2%)"),
        (
            "transform: translate(0, -20px) rotate(-0.5deg)",
            "transform: translate(0, -20px) rotate(-0.5deg)",
        ),
        ("image-rendering: pixelated; opacity: .9", "image-rendering: pixelated; opacity: .9"),
        ("", ""),
        ("   ;;  ", ""),
    ],
)
def test_css_keeps_plain_declarations(raw: str, kept: str) -> None:
    assert server.sanitize_css(raw) == kept


@pytest.mark.parametrize(
    "raw",
    [
        # Out of the style context, or markup.
        "</style><script>alert(1)</script>",
        "filter: contrast(1.1)</style>",
        "transform: none; } img { display: none",
        "transform: none <",
        # Network fetches and their spellings.
        "@import 'https://evil.example/x.css'",
        '@import url("https://evil.example/x.css")',
        "filter: url(https://evil.example/f.svg#x)",
        "filter: u\\72l(https://evil.example/)",
        'width: 10px; content: "x"',
        "clip-path: url(#clip)",
        # Comments hiding things, an unclosed comment.
        "filter: contrast(1.1) /* unclosed",
        # Not ASCII, control characters.
        "transform: translateY(-2％)",
        "transform: none;\x00",
        # Too long.
        "opacity: 1;" * 100,
        # Not a string.
        None,
        ["opacity: 1"],
    ],
)
def test_css_that_could_escape_or_fetch_is_dropped_whole(raw: Any) -> None:
    assert server.sanitize_css(raw) == ""


@pytest.mark.parametrize(
    "raw",
    [
        "filter: url(x)",  # url() without anything special
        "background: url(//evil.example/x.png)",
        "transform: translateY(expression(alert(1)))",
        "width: expression(1)",
        "filter: image-set(x 1x)",
        "width: calc(100px * 1000)",
        "width: var(--x)",
        "width: attr(data-x)",
        "transform: translate(env(safe-area-inset-top))",
        "position: fixed",  # out of the cell
        "z-index: 99999",
        "background: red",  # not in the vocabulary
        "-moz-binding: x",
        "behavior: x",
        "--custom: 1",
        "width: 100000px",  # absurd sizes
        "width: 1e9px",
        "transform: scale(50)",
        "transform: scale(3) scale(3)",  # 9x together
        "scale: 20",
        "transform: rotate(9999deg)",
        "filter: contrast(1.1",  # unbalanced
        "filter: contrast(1.1))",
        "transform: translate((1px), 2px)",
        "width: 10pt",  # unit not on the list
        "transition: all 1s",
        "animation: spin 1s infinite",
        "opacity: 1 ! important now",
        "width",  # no value
        "width:",
        ": 10px",
        "transform: " + "rotate(1deg) " * 7,  # too many functions
    ],
)
def test_css_declarations_outside_the_vocabulary_are_dropped(raw: str) -> None:
    assert server.sanitize_css(raw) == ""
    # ...on their own: the declarations around them stay.
    assert server.sanitize_css(f"opacity: 0.9; {raw}; filter: invert(1)").startswith("opacity: 0.9")


def test_css_with_long_whitespace_runs_is_quick() -> None:
    start = time.monotonic()
    for raw in (
        "opacity:" + " " * 990 + "1",
        "opacity: 1" + " " * 985 + "x",
        "opacity: 1 " + "!" + " " * 980,
        "/*" * 499,
    ):
        server._sanitize_css.cache_clear()
        server.sanitize_css(raw)
    assert time.monotonic() - start < 0.5


@pytest.mark.parametrize(
    "raw",
    [
        "scale: 4",  # over the per-axis bound on its own
        "transform: scale(4)",
        "scale: 10 0.1",  # one axis far over
        "transform: scaleX(10) scaleY(0.1)",
        "transform: scaleX(2) scaleX(2)",
        "scale: 2 2 2",  # a z value
        "transform: scale()",
        "transform: skewX(89.999deg)",  # skew isn't offered
        "transform: skew(10deg)",
        "transform: translateX(5000px)",
        "transform: translate(80%)",
        "margin-top: -90%",
        "margin: 2000px",
        "width: 1000%",
        "width: 5000px",
        "height: 300vh",
        "width: 10",  # a unitless length that isn't 0
        "object-position: 50% 300%",
        "transform: 5px",  # a number with no context
        "rotate: 1 1 0 45deg",
        "filter: contrast(50)",
        "opacity: 3000%",
    ],
)
def test_css_sizes_and_moves_are_bounded(raw: str) -> None:
    assert server.sanitize_css(raw) == ""


@pytest.mark.parametrize(
    "raw",
    [
        # A rotate turns one axis's scale into the other's: 9x along one axis.
        "transform: scaleX(3) rotate(90deg) scaleY(3)",
        "transform: scale(3, 1) rotate(90deg) scale(3, 1)",
        "transform: scaleX(3) rotate(90deg) scaleX(3) rotate(90deg) scaleX(3) rotate(90deg)",
        # Shrinking one axis doesn't buy growth on another.
        "transform: scaleX(0.1) rotate(90deg) scaleY(3) scaleX(3)",
        "transform: scale(0.5) scale(3) scale(3)",
    ],
)
def test_every_scale_counts_at_its_largest_whatever_turns_it(raw: str) -> None:
    assert server.sanitize_css(raw) == ""


def test_the_scale_property_on_top_of_a_rotated_transform_is_dropped() -> None:
    raw = "scale: 3; transform: scaleX(3) rotate(90deg)"
    assert server.sanitize_css(raw) == "transform: scaleX(3) rotate(90deg)"
    assert server.sanitize_css("scale: 1.5; transform: rotate(90deg) scaleY(2)") == (
        "scale: 1.5; transform: rotate(90deg) scaleY(2)"
    )


def test_scale_counts_the_property_and_the_transform_together() -> None:
    # 2 x 2 = 4, over the bound together: the scale property goes.
    assert server.sanitize_css("scale: 2; transform: scale(2)") == "transform: scale(2)"
    assert server.sanitize_css("transform: scale(2) translateY(-2%); scale: 1 2") == (
        "transform: scale(2) translateY(-2%)"
    )
    # 1.5 x 2 = 3, on the bound: both stay.
    assert server.sanitize_css("scale: 1.5; transform: scale(2)") == (
        "scale: 1.5; transform: scale(2)"
    )
    assert server.sanitize_css("transform: scaleX(1.5) scaleX(2)") == (
        "transform: scaleX(1.5) scaleX(2)"
    )
    # A declaration set again replaces the first, so the bound sees the last:
    # 1.2 x 2 is inside it, 3 x 2 would not have been.
    assert server.sanitize_css("transform: scale(3); transform: scale(1.2); scale: 2") == (
        "transform: scale(1.2); scale: 2"
    )
    assert server.sanitize_css("transform: scale(1.2); transform: scale(3); scale: 2") == (
        "transform: scale(3)"
    )


def test_css_keeps_at_most_a_dozen_declarations() -> None:
    props = ["margin", "margin-top", "margin-right", "margin-bottom", "margin-left"]
    props += ["width", "height", "max-width", "max-height", "min-width", "min-height"]
    props += ["object-position", "transform-origin"]
    kept = server.sanitize_css("; ".join(f"{p}: 0" for p in props))
    assert kept.count(": 0") == server.CSS_MAX_DECLARATIONS
    # A property set twice counts once: the last one, as in CSS.
    assert server.sanitize_css("opacity: 1;" * 30) == "opacity: 1"
    assert server.sanitize_css("opacity: 0.5; filter: invert(1); opacity: 0.8") == (
        "filter: invert(1); opacity: 0.8"
    )


def test_each_paper_gets_its_own_css(tmp_path: Path, cdn: FakeCdn, clocks: Clocks) -> None:
    _seed_today(tmp_path, "NY_NYT", "WSJ")
    rows = [
        {"enabled": True, "code": "NY_NYT", "css": "filter: contrast(1.2)"},
        {"enabled": True, "code": "WSJ", "css": "@import url(x); opacity: 1"},
    ]
    first = _fetch(tmp_path, papers=rows)
    clocks.wall += server.ROTATION_HOLD_S
    second = _fetch(tmp_path, papers=rows)
    assert (first["code"], first["css"]) == ("NY_NYT", "filter: contrast(1.2)")
    assert (second["code"], second["css"]) == ("WSJ", "")


def test_client_applies_the_css_to_the_page_image_only() -> None:
    js = (Path(server.__file__).parent / "client.js").read_text()
    assert "img.style.cssText = paperCss(data)" in js
    assert "${data.css" not in js and "${paperCss" not in js  # never through markup


# ----- rotation --------------------------------------------------------------


def _seed_today(tmp_path: Path, *codes: str) -> None:
    for code in codes:
        _seed(tmp_path, f"{code}_20261025.jpg")


def _sends(tmp_path: Path, clocks: Clocks, count: int, **kw: Any) -> list[str]:
    """``count`` panel renders, each a hold window after the last."""
    shown = []
    for _ in range(count):
        shown.append(_fetch(tmp_path, **kw)["code"])
        clocks.wall += server.ROTATION_HOLD_S
    return shown


def test_rotation_moves_one_paper_per_send(tmp_path: Path, cdn: FakeCdn, clocks: Clocks) -> None:
    _seed_today(tmp_path, "NY_NYT", "WSJ", "USAT")
    assert _sends(tmp_path, clocks, 4) == ["NY_NYT", "WSJ", "USAT", "NY_NYT"]
    assert _state(tmp_path)["current"] == "NY_NYT"
    assert cdn.calls == []


def test_warm_ahead_send_and_follow_up_show_one_paper(
    tmp_path: Path, cdn: FakeCdn, clocks: Clocks
) -> None:
    """Warm-ahead composes for a send without sending, maybe twice; the send
    and its follow-up come after. All are non-preview renders."""
    _seed_today(tmp_path, "NY_NYT", "WSJ", "USAT")
    shown = []
    for step in (0, 30, 120, 30):  # warm, warm, send, follow-up
        clocks.wall += step
        shown.append(_fetch(tmp_path)["code"])
    assert shown == ["NY_NYT"] * 4
    clocks.wall += server.ROTATION_HOLD_S
    assert _fetch(tmp_path)["code"] == "WSJ"


def test_preview_never_moves_the_rotation(tmp_path: Path, cdn: FakeCdn, clocks: Clocks) -> None:
    _seed_today(tmp_path, "NY_NYT", "WSJ", "USAT")
    assert _fetch(tmp_path)["code"] == "NY_NYT"
    assert _fetch(tmp_path, preview=True)["code"] == "NY_NYT"  # still held
    clocks.wall += server.ROTATION_HOLD_S
    assert _fetch(tmp_path, preview=True)["code"] == "WSJ"  # what the next send gets
    assert _fetch(tmp_path, preview=True)["code"] == "WSJ"
    assert _state(tmp_path)["current"] == "NY_NYT"
    assert _fetch(tmp_path)["code"] == "WSJ"


def test_rotation_skips_a_paper_without_a_page(
    tmp_path: Path, cdn: FakeCdn, clocks: Clocks
) -> None:
    _seed_today(tmp_path, "NY_NYT", "USAT")  # WSJ has nothing, and Freedom Forum 404s it
    assert _sends(tmp_path, clocks, 3) == ["NY_NYT", "USAT", "NY_NYT"]


def test_a_held_paper_that_lost_its_page_moves_on(
    tmp_path: Path, cdn: FakeCdn, clocks: Clocks
) -> None:
    _seed_today(tmp_path, "NY_NYT", "WSJ")
    assert _fetch(tmp_path)["code"] == "NY_NYT"
    (tmp_path / "pages" / "NY_NYT_20261025.jpg").unlink()
    clocks.wall += 10
    assert _fetch(tmp_path)["code"] == "WSJ"
    assert _state(tmp_path)["current"] == "WSJ"


def test_disabled_papers_are_left_out(tmp_path: Path, cdn: FakeCdn, clocks: Clocks) -> None:
    _seed_today(tmp_path, "NY_NYT", "WSJ")
    assert _sends(tmp_path, clocks, 2, papers="# NY_NYT\nWSJ") == ["WSJ", "WSJ"]
    assert _sends(tmp_path, clocks, 2, papers=_rows("-NY_NYT", "WSJ")) == ["WSJ", "WSJ"]


def test_rotation_and_plan_helpers() -> None:
    assert server.rotation(["A", "B", "C"], "B") == ["C", "A", "B"]
    assert server.rotation(["A", "B", "C"], "C") == ["A", "B", "C"]
    assert server.rotation(["A", "B", "C"], "GONE") == ["A", "B", "C"]
    # A paper that left the list resumes after where it stood.
    assert server.rotation(["A", "C", "D"], "B", ["A", "B", "C", "D"]) == ["C", "D", "A"]
    assert server.rotation(["A", "B", "C"], "D", ["A", "B", "C", "D"]) == ["A", "B", "C"]
    assert server.rotation(["A"], "B", ["B", "C"]) == ["A"]
    assert server.rotation([], "B", ["B"]) == []
    held = {"current": "B", "since": 1000.0}
    assert server.plan(["A", "B", "C"], held, 1010.0) == (["B", "C", "A"], "B")
    later = 1000.0 + server.ROTATION_HOLD_S
    assert server.plan(["A", "B", "C"], held, later) == (["C", "A", "B"], None)
    assert server.plan(["A", "C"], held, 1010.0) == (["A", "C"], None)


def test_pinned_paper_ignores_the_rotation(tmp_path: Path, cdn: FakeCdn) -> None:
    _seed_today(tmp_path, "NY_NYT", "TX_HC")
    data = _fetch(tmp_path, paper="tx_hc")
    assert (data["code"], data["css"]) == ("TX_HC", "")
    assert "current" not in _state(tmp_path)
    assert _fetch(tmp_path)["code"] == "NY_NYT"


def test_a_pinned_paper_switched_off_in_the_list_keeps_its_css(
    tmp_path: Path, cdn: FakeCdn
) -> None:
    _seed_today(tmp_path, "AZ_ADS", "WSJ")
    rows = [{"enabled": False, "code": "AZ_ADS", "css": "opacity: 0.8"}, {"code": "WSJ"}]
    data = _fetch(tmp_path, paper="AZ_ADS", papers=rows)
    assert (data["code"], data["css"]) == ("AZ_ADS", "opacity: 0.8")
    assert _fetch(tmp_path, papers=rows)["code"] == "WSJ"


def test_bad_pinned_code_and_empty_list_are_errors(tmp_path: Path, cdn: FakeCdn) -> None:
    assert "isn't a Freedom Forum paper code" in _fetch(tmp_path, paper="../x")["error"]
    assert "No papers to show" in _fetch(tmp_path, papers=[])["error"]
    assert "No papers to show" in _fetch(tmp_path, papers=[{"code": ""}, {"code": "a b"}])["error"]
    off = [{"enabled": False, "code": "NY_NYT"}, {"enabled": False, "code": "WSJ"}]
    assert "Every paper in the list is switched off" in _fetch(tmp_path, papers=off)["error"]
    assert "switched off" in _fetch(tmp_path, papers="# NY_NYT")["error"]
    assert cdn.calls == []


def test_payload_carries_size_and_css(tmp_path: Path, cdn: FakeCdn) -> None:
    _seed_today(tmp_path, "NY_NYT")
    assert _fetch(tmp_path, papers="NY_NYT 3.5") == {
        "url": "/plugins/news_front_pages/page/NY_NYT_20261025.jpg?w=700",
        "code": "NY_NYT",
        "date": "2026-10-25",
        "width": 700,
        "height": 1342,
        "css": NYT_CSS,
        "fit": "top",  # the "On smaller screens" setting
    }
    assert cdn.calls == []


# ----- the rotation over edited rows -----------------------------------------


def _rows(*codes: str) -> list[dict[str, Any]]:
    return [{"enabled": not c.startswith("-"), "code": c.lstrip("-"), "css": ""} for c in codes]


def test_rotation_follows_reordered_rows(tmp_path: Path, cdn: FakeCdn, clocks: Clocks) -> None:
    _seed_today(tmp_path, "A1", "B1", "C1", "D1")
    assert _sends(tmp_path, clocks, 2, papers=_rows("A1", "B1", "C1", "D1")) == ["A1", "B1"]
    # Reordered: the paper after B1 in the new order comes next.
    assert _sends(tmp_path, clocks, 3, papers=_rows("D1", "B1", "A1", "C1")) == ["A1", "C1", "D1"]


def test_rotation_resumes_after_a_removed_or_switched_off_paper(
    tmp_path: Path, cdn: FakeCdn, clocks: Clocks
) -> None:
    _seed_today(tmp_path, "A1", "B1", "C1", "D1")
    assert _sends(tmp_path, clocks, 2, papers=_rows("A1", "B1", "C1", "D1")) == ["A1", "B1"]
    # B1, the paper on show, is removed: C1 (after it) is next, not A1.
    assert _sends(tmp_path, clocks, 1, papers=_rows("A1", "C1", "D1")) == ["C1"]
    # C1 is switched off while held: it moves on at once, to D1.
    clocks.wall -= server.ROTATION_HOLD_S - 10
    assert _fetch(tmp_path, papers=_rows("A1", "-C1", "D1"))["code"] == "D1"


def test_rotation_wraps_when_the_last_paper_is_removed(
    tmp_path: Path, cdn: FakeCdn, clocks: Clocks
) -> None:
    """The state names papers, so a list that shrank under the rotation
    can't leave it pointing past the end."""
    _seed_today(tmp_path, "A1", "B1", "C1", "D1")
    assert _sends(tmp_path, clocks, 4, papers=_rows("A1", "B1", "C1", "D1"))[-1] == "D1"
    assert _sends(tmp_path, clocks, 2, papers=_rows("A1", "B1")) == ["A1", "B1"]
    # Every paper it knew is gone: it starts over.
    _seed_today(tmp_path, "X1", "Y1")
    assert _sends(tmp_path, clocks, 2, papers=_rows("X1", "Y1")) == ["X1", "Y1"]


def test_rotation_survives_an_edited_code_and_old_state(
    tmp_path: Path, cdn: FakeCdn, clocks: Clocks
) -> None:
    _seed_today(tmp_path, "A1", "B1", "C1", "E1")
    assert _sends(tmp_path, clocks, 2, papers=_rows("A1", "B1", "C1")) == ["A1", "B1"]
    # B1's code is edited to E1 in place: the rotation carries on after it.
    assert _sends(tmp_path, clocks, 2, papers=_rows("A1", "E1", "C1")) == ["C1", "A1"]
    # A state file from before "order" was kept, or with junk in it.
    for junk in (None, "A1", [1, None, "C1"]):
        state = _state(tmp_path)
        state["order"] = junk
        server._write_json(tmp_path / "state.json", state)
        clocks.wall += server.ROTATION_HOLD_S
        assert _fetch(tmp_path, papers=_rows("A1", "E1", "C1"))["code"] in {"A1", "E1", "C1"}


# ----- background downloads --------------------------------------------------


def test_first_render_says_fetching_then_shows_the_page(tmp_path: Path, cdn: FakeCdn) -> None:
    cdn.pages = {_url("NY_NYT", 25): PAGE}
    first = _fetch(tmp_path, paper="NY_NYT")
    assert (first["pending"], first["live_pending"]) == (True, 1)
    assert server.HOST_FOLLOW_UPS * first["live_refresh_s"] >= server.JOB_WORST_S
    _settle(tmp_path)
    assert _fetch(tmp_path, paper="NY_NYT")["date"] == "2026-10-25"


def test_a_slow_download_never_holds_up_a_render(tmp_path: Path, cdn: FakeCdn) -> None:
    _seed(tmp_path, "NY_NYT_20261024.jpg")
    cdn.gate = threading.Event()  # the CDN answers only when the test says so
    cdn.pages = {_url("NY_NYT", 25): PAGE}
    start = time.monotonic()
    first = _fetch(tmp_path, paper="NY_NYT")
    second = _fetch(tmp_path, paper="NY_NYT")
    assert time.monotonic() - start < 1.0
    assert first["date"] == second["date"] == "2026-10-24"
    assert first["live_pending"] == 1
    cdn.gate.set()
    _settle(tmp_path)
    assert _fetch(tmp_path, paper="NY_NYT")["date"] == "2026-10-25"


def test_a_dripping_download_gives_up_at_its_budget(cdn: FakeCdn, clocks: Clocks) -> None:
    def tick() -> None:
        clocks.mono += 1.0  # a byte a second

    drip = _Resp(PAGE, on_read=tick)
    cdn.pages = {_url("NY_NYT", 25): drip}
    with pytest.raises(server.CdnUnreachable):
        server.download_edition("NY_NYT", TODAY)
    assert drip.reads <= server.DOWNLOAD_BUDGET_S + 1


def test_concurrent_renders_trigger_one_download(tmp_path: Path, cdn: FakeCdn) -> None:
    cdn.gate = threading.Event()
    cdn.pages = {_url("NY_NYT", 25): PAGE}
    renders = [
        threading.Thread(target=_fetch, args=(tmp_path,), kwargs={"paper": "NY_NYT"})
        for _ in range(6)
    ]
    for thread in renders:
        thread.start()
    for thread in renders:
        thread.join(5)
    _fetch(tmp_path, paper="NY_NYT", fresh=True)  # even a forced one joins the queued job
    cdn.gate.set()
    _settle(tmp_path)
    assert cdn.calls == [_url("NY_NYT", 25)]


class _HookLock:
    """A lock that runs ``hook`` once, in the worker thread, right after the
    worker releases it with an empty queue: the moment the exit race needs."""

    def __init__(self, run_of: Any) -> None:
        self._lock = threading.Lock()
        self._run_of = run_of
        self.hook: Any = None

    def __enter__(self) -> _HookLock:
        self._lock.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        run = self._run_of()
        idle = run is not None and not run.queue and run.active is None
        self._lock.release()
        if self.hook is not None and idle and threading.current_thread().name.endswith("downloads"):
            hook, self.hook = self.hook, None
            hook()


def test_work_queued_as_the_worker_leaves_is_not_stranded(
    tmp_path: Path, cdn: FakeCdn, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _HookLock(lambda: server._RUNS.get(str(tmp_path)))
    monkeypatch.setattr(server, "_RUNS_LOCK", lock)
    cdn.pages = {_url("NY_NYT", 25): PAGE, _url("WSJ", 25): PAGE}
    lock.hook = lambda: server._enqueue(tmp_path, ["WSJ"], TODAY, force=False)
    _fetch(tmp_path, paper="NY_NYT")
    _settle(tmp_path)
    assert _fetch(tmp_path, paper="WSJ")["date"] == "2026-10-25"
    assert _url("WSJ", 25) in cdn.calls


def test_a_download_landing_before_the_pending_check_is_shown(
    tmp_path: Path, cdn: FakeCdn, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = server._enqueue

    def enqueue_and_finish(data_dir: Path, *args: Any, **kw: Any) -> None:
        real(data_dir, *args, **kw)
        _settle(data_dir)  # the download lands before fetch() looks at the queue

    monkeypatch.setattr(server, "_enqueue", enqueue_and_finish)
    cdn.pages = {_url("WSJ", 25): PAGE}
    data = _fetch(tmp_path, paper="WSJ")
    assert data["date"] == "2026-10-25"


def test_the_queue_holds_each_paper_once() -> None:
    run = server._Run(Path("."))
    for _ in range(3):
        run.add(server._Job("NY_NYT", TODAY), first=False)
    run.add(server._Job("WSJ", TODAY), first=False)
    run.add(server._Job("NY_NYT", TODAY, force=True), first=False)
    assert [(j.code, j.force) for j in run.queue] == [("NY_NYT", True), ("WSJ", False)]
    run.add(server._Job("WSJ", TODAY), first=True)
    assert run.line() == ["WSJ", "NY_NYT"]
    run.active = run.queue.pop(0)
    run.add(server._Job("WSJ", TODAY), first=True)  # already downloading
    assert run.line() == ["WSJ", "NY_NYT"]
    for n in range(2 * server.MAX_QUEUED):
        run.add(server._Job(f"P{n}", TODAY), first=False)
    assert len(run.queue) == server.MAX_QUEUED


def _wait_for_active(tmp_path: Path) -> None:
    deadline = time.monotonic() + 5
    while server._line(tmp_path)[1] is None:
        assert time.monotonic() < deadline, "the worker never started a job"
        time.sleep(0.01)


def test_the_paper_a_render_waits_for_jumps_the_queue(tmp_path: Path, cdn: FakeCdn) -> None:
    cdn.gate = threading.Event()
    _fetch(tmp_path)  # queues NY_NYT (downloading), WSJ, USAT
    _wait_for_active(tmp_path)
    data = _fetch(tmp_path, paper="USAT")
    assert server._line(tmp_path)[0][:2] == ["NY_NYT", "USAT"]
    assert data["pending"] is True
    assert data["live_pending"] == 2


def test_the_requested_paper_gets_into_a_full_queue(tmp_path: Path, cdn: FakeCdn) -> None:
    cdn.gate = threading.Event()
    flood = [f"P{n}" for n in range(server.MAX_QUEUED + 5)]
    server._enqueue(tmp_path, flood, TODAY, force=False)
    _wait_for_active(tmp_path)
    data = _fetch(tmp_path, paper="WSJ")
    line = server._line(tmp_path)[0]
    assert line[:2] == ["P0", "WSJ"]
    assert len(line) == server.MAX_QUEUED + 1  # the running job, then a full queue
    assert (data["pending"], data["live_pending"]) == (True, 2)


def test_the_refresh_hint_covers_the_job_budget() -> None:
    covers = server.HOST_FOLLOW_UPS * server._wait_hint(0, server.JOB_WORST_S)["live_refresh_s"]
    assert covers >= server.JOB_WORST_S
    assert server._wait_hint(0, 1.0)["live_refresh_s"] == server.LIVE_REFRESH_S
    later = server._wait_hint(2, server.JOB_WORST_S)
    assert server.HOST_FOLLOW_UPS * later["live_refresh_s"] >= 3 * server.JOB_WORST_S
    assert later["live_pending"] == 3
    assert server._wait_hint(100, None)["live_refresh_s"] == server.LIVE_REFRESH_MAX_S


def test_a_download_finishing_between_queue_looks_is_rechecked(
    tmp_path: Path, cdn: FakeCdn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever the queue looked like when fetch() checked it is what the
    answer rests on: here the download lands right after that look."""
    cdn.gate = threading.Event()
    cdn.pages = {_url("WSJ", 25): PAGE}
    real = server._line

    def look_then_finish(data_dir: Path) -> tuple[list[str], float | None]:
        seen = real(data_dir)
        assert cdn.gate is not None
        cdn.gate.set()
        _settle(data_dir)
        return seen

    monkeypatch.setattr(server, "_line", look_then_finish)
    data = _fetch(tmp_path, paper="WSJ")
    assert "error" not in data
    assert data.get("date") == "2026-10-25" or data.get("pending") is True


def test_a_reload_ignores_runs_kept_from_an_older_revision(
    tmp_path: Path, cdn: FakeCdn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retained registry from before holds a live run of an older class,
    without ``add()``/``line()``; the reloaded module must not drive it."""
    holder = sys.modules[f"_tesserae_runtime.{server.PLUGIN_ID}"]
    stale = types.SimpleNamespace(alive=lambda: True, queue=[], active=None)
    monkeypatch.setitem(holder.__dict__, "runs", {str(tmp_path): stale})
    spec = importlib.util.spec_from_file_location("news_front_pages_reloaded", server.__file__)
    assert spec is not None and spec.loader is not None
    reloaded = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, reloaded)
    spec.loader.exec_module(reloaded)
    reloaded._enqueue(tmp_path, ["NY_NYT"], TODAY, force=False)
    assert reloaded._join_worker(tmp_path, 10)
    assert _url("NY_NYT", 25) in cdn.calls


def test_downloads_run_in_the_render_context(tmp_path: Path, cdn: FakeCdn) -> None:
    """The host's egress allowlist lives in the render's context; the worker
    must carry it."""
    token = CONTEXT_PROBE.set("render scope")
    try:
        _fetch(tmp_path, paper="NY_NYT")
    finally:
        CONTEXT_PROBE.reset(token)
    _settle(tmp_path)
    assert cdn.contexts and set(cdn.contexts) == {"render scope"}


def test_falls_back_across_a_month_boundary(
    tmp_path: Path, cdn: FakeCdn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_edition_today", lambda: date(2026, 11, 1))
    cdn.pages = {_url("WSJ", 31): PAGE}
    assert _fetch_settled(tmp_path, paper="WSJ")["date"] == "2026-10-31"
    assert cdn.calls == [_url("WSJ", 1), _url("WSJ", 31)]


def test_gives_up_after_three_days(tmp_path: Path, cdn: FakeCdn) -> None:
    data = _fetch_settled(tmp_path, paper="WSJ")
    assert data["error"] == "No WSJ front page in the last 3 days."
    assert cdn.calls == [_url("WSJ", 25), _url("WSJ", 24), _url("WSJ", 23)]


def test_todays_page_on_disk_needs_no_network(tmp_path: Path, cdn: FakeCdn) -> None:
    _seed(tmp_path, "NY_NYT_20261025.jpg")
    data = _fetch(tmp_path, paper="NY_NYT")
    assert data["date"] == "2026-10-25"
    assert "live_refresh_s" not in data
    assert cdn.calls == []


def test_an_older_kept_edition_ends_the_lookback(tmp_path: Path, cdn: FakeCdn) -> None:
    _seed(tmp_path, "NY_NYT_20261024.jpg")
    assert _fetch_settled(tmp_path, paper="NY_NYT")["date"] == "2026-10-24"
    assert cdn.calls == [_url("NY_NYT", 25)]


def test_keeps_the_last_good_page_when_freedom_forum_is_down(tmp_path: Path, cdn: FakeCdn) -> None:
    _seed(tmp_path, "WSJ_20261020.jpg")
    cdn.down = True
    assert _fetch_settled(tmp_path, paper="WSJ")["date"] == "2026-10-20"
    assert len(cdn.calls) == 1  # an outage ends the job


def test_outage_with_nothing_saved_says_so(tmp_path: Path, cdn: FakeCdn) -> None:
    cdn.down = True
    data = _fetch_settled(tmp_path)
    assert data["error"].startswith("Couldn't reach Freedom Forum")
    assert len(cdn.calls) == 3  # one try per paper, not three days each


def test_a_refused_egress_is_recorded_like_an_outage(
    tmp_path: Path, cdn: FakeCdn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host's allowlist raises its own error type; the job still ends
    cleanly, recorded, and the next paper's job still runs."""

    def denied(code: str, day: date) -> bytes | None:
        raise RuntimeError(f"Capability denied: {code}")

    monkeypatch.setattr(server, "download_edition", denied)
    data = _fetch_settled(tmp_path)
    assert data["error"].startswith("Couldn't reach Freedom Forum")
    assert set(_state(tmp_path)["failed"]) == {"NY_NYT", "WSJ", "USAT"}


def test_a_response_cut_off_mid_body_keeps_the_last_good_page(tmp_path: Path, cdn: FakeCdn) -> None:
    _seed(tmp_path, "NY_NYT_20261024.jpg")
    cut = _Resp(b"", error=http.client.IncompleteRead(b"partial", 600_000))
    cdn.pages = {_url("NY_NYT", 25): cut}
    assert _fetch_settled(tmp_path, paper="NY_NYT")["date"] == "2026-10-24"
    assert len(cdn.calls) == 1
    assert "NY_NYT" in _state(tmp_path)["checked"]


def test_a_missing_edition_is_rechecked_after_a_while(
    tmp_path: Path, cdn: FakeCdn, clocks: Clocks
) -> None:
    _seed(tmp_path, "NY_NYT_20261024.jpg")
    _fetch_settled(tmp_path, paper="NY_NYT")
    _fetch_settled(tmp_path, paper="NY_NYT")
    assert len(cdn.calls) == 1
    _fetch_settled(tmp_path, paper="NY_NYT", fresh=True)
    assert len(cdn.calls) == 2
    clocks.wall += server.RECHECK_S
    cdn.pages = {_url("NY_NYT", 25): PAGE}
    assert _fetch_settled(tmp_path, paper="NY_NYT")["date"] == "2026-10-25"


@pytest.mark.parametrize(
    "body",
    [
        b"<html>Access denied</html>",
        PAGE[: len(PAGE) // 2],  # cut off mid-download
        _jpeg(120, 200),  # a thumbnail, not a page
    ],
    ids=["html", "truncated", "tiny"],
)
def test_refuses_what_is_not_a_front_page(tmp_path: Path, cdn: FakeCdn, body: bytes) -> None:
    cdn.pages = {_url("NY_NYT", 25): body}
    assert "error" in _fetch_settled(tmp_path, paper="NY_NYT")
    assert not list((tmp_path / "pages").glob("*.jpg"))


def test_refuses_last_months_file_in_a_reused_slot(tmp_path: Path, cdn: FakeCdn) -> None:
    stale = _Resp(PAGE, {"Last-Modified": "Fri, 25 Sep 2026 05:06:06 GMT"})
    fresh = _Resp(PAGE, {"Last-Modified": "Sat, 24 Oct 2026 08:54:04 GMT"})
    cdn.pages = {_url("WSJ", 25): stale, _url("WSJ", 24): fresh}
    assert _fetch_settled(tmp_path, paper="WSJ")["date"] == "2026-10-24"


# ----- the PDF: sharp pages ----------------------------------------------------

needs_pdfium = pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None, reason="pypdfium2 not installed"
)


def _pdf(width_pt: int = 612, height_pt: int = 1188) -> bytes:
    """A one-page PDF ``width_pt`` x ``height_pt`` points: white, with a black
    band across the top tenth, so a drawn page can be told from a blank."""
    img = Image.new("L", (width_pt, height_pt), 255)
    img.paste(0, (0, 0, width_pt, height_pt // 10))
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=72)
    return buf.getvalue()


def _pdf_url(code: str, day: int) -> str:
    return f"{CDN}/pdf{day}/{code}.pdf"


@pytest.fixture
def pdf(monkeypatch: pytest.MonkeyPatch, cdn: FakeCdn) -> FakeCdn:
    """The fake CDN with the PDF rasteriser on."""
    monkeypatch.setattr(server, "rasteriser_available", lambda: True)
    return cdn


def _size(body: bytes | None) -> tuple[int, int] | None:
    return server.page_size(body) if body else None


@needs_pdfium
def test_rasterise_draws_page_one_at_panel_width() -> None:
    body = server.rasterise_pdf(_pdf())
    assert body is not None
    with Image.open(io.BytesIO(body)) as img:
        assert img.format == "JPEG"
        assert img.width == server.RASTER_WIDTH_PX
        assert abs(img.height - 1188 * server.RASTER_WIDTH_PX / 612) <= 1
        grey = img.convert("L")
        assert grey.getpixel((700, 20)) < 40  # the band, drawn
        assert grey.getpixel((700, 2000)) > 215  # the page, white


@needs_pdfium
@pytest.mark.parametrize(
    "body",
    [b"%PDF-1.4 not really", b"", _jpeg(), _pdf(100, 5000), _pdf(5000, 100)],
    ids=["broken", "empty", "jpeg", "too-tall", "too-wide"],
)
def test_rasterise_refuses_what_it_cannot_draw(body: bytes) -> None:
    assert server.rasterise_pdf(body) is None


def test_rasterise_gives_up_on_a_stuck_or_failing_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "rasteriser_available", lambda: True)
    monkeypatch.setattr(server, "_RASTER_CHILD", "import time; time.sleep(30)")
    monkeypatch.setattr(server, "RASTER_TIMEOUT_S", 0.5)
    start = time.monotonic()
    assert server.rasterise_pdf(_pdf()) is None
    assert time.monotonic() - start < 10
    monkeypatch.setattr(server, "_RASTER_CHILD", "import sys; sys.stdout.write('junk')")
    assert server.rasterise_pdf(_pdf()) is None
    monkeypatch.setattr(server, "_RASTER_CHILD", "raise SystemExit(9)")
    assert server.rasterise_pdf(_pdf()) is None


def test_rasterise_child_caps_its_own_memory_and_cpu() -> None:
    assert '"RLIMIT_AS", memory' in server._RASTER_CHILD
    assert '"RLIMIT_CPU", cpu' in server._RASTER_CHILD
    assert "document[0]" in server._RASTER_CHILD  # page one only


@needs_pdfium
def test_the_pdf_comes_first(pdf: FakeCdn) -> None:
    pdf.pages = {_pdf_url("WSJ", 25): _pdf(), _url("WSJ", 25): PAGE}
    size = _size(server.download_edition("WSJ", TODAY))
    assert size is not None and size[0] == server.RASTER_WIDTH_PX
    assert pdf.calls == [_pdf_url("WSJ", 25)]


@needs_pdfium
@pytest.mark.parametrize(
    "served",
    [
        None,  # no PDF for this paper (a 404)
        b"<html>captive portal</html>",
        _jpeg(),  # not a PDF
        b"%PDF-1.4 truncated",  # a PDF that won't draw
    ],
    ids=["404", "html", "jpeg", "broken-pdf"],
)
def test_the_jpeg_stands_in_when_the_pdf_fails(pdf: FakeCdn, served: bytes | None) -> None:
    pdf.pages = {_url("WSJ", 25): PAGE}
    if served is not None:
        pdf.pages[_pdf_url("WSJ", 25)] = served
    assert server.download_edition("WSJ", TODAY) == PAGE
    assert pdf.calls == [_pdf_url("WSJ", 25), _url("WSJ", 25)]


@needs_pdfium
def test_an_oversized_or_stale_pdf_is_refused(
    pdf: FakeCdn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "MAX_PDF_BYTES", 1000)
    pdf.pages = {_pdf_url("WSJ", 25): _pdf(), _url("WSJ", 25): PAGE}
    assert server.download_edition("WSJ", TODAY) == PAGE
    monkeypatch.setattr(server, "MAX_PDF_BYTES", 30 * 1024 * 1024)
    stale = {"Last-Modified": "Mon, 21 Sep 2026 06:00:00 GMT"}
    pdf.pages = {_pdf_url("WSJ", 25): _Resp(_pdf(), stale), _url("WSJ", 25): PAGE}
    assert server.download_edition("WSJ", TODAY) == PAGE


def test_without_the_rasteriser_only_the_jpeg_is_asked_for(cdn: FakeCdn) -> None:
    cdn.pages = {_pdf_url("WSJ", 25): b"%PDF", _url("WSJ", 25): PAGE}
    assert server.download_edition("WSJ", TODAY) == PAGE
    assert cdn.calls == [_url("WSJ", 25)]


@needs_pdfium
def test_a_pdf_outage_falls_to_the_jpeg_which_reports_it(pdf: FakeCdn) -> None:
    pdf.down = True
    with pytest.raises(server.CdnUnreachable):
        server.download_edition("WSJ", TODAY)
    assert pdf.calls == [_pdf_url("WSJ", 25), _url("WSJ", 25)]


@needs_pdfium
def test_a_render_gets_the_sharp_page_and_its_size(tmp_path: Path, pdf: FakeCdn) -> None:
    pdf.pages = {_pdf_url("WSJ", 25): _pdf()}
    data = _fetch_settled(tmp_path, paper="WSJ")
    assert data["url"].endswith(f"/WSJ_20261025.jpg?w={server.RASTER_WIDTH_PX}")
    assert data["width"] == server.RASTER_WIDTH_PX


@needs_pdfium
def test_todays_blurry_page_is_redrawn_from_the_pdf_once(
    tmp_path: Path, pdf: FakeCdn, clocks: Clocks
) -> None:
    """A page saved from the JPEG (before the PDF path, or while the PDF
    wasn't out) is redrawn when the PDF is there: the render shows it at
    once and the next one gets the sharp page, under a new URL."""
    _seed(tmp_path, "WSJ_20261025.jpg")  # 700 px wide
    pdf.pages = {_pdf_url("WSJ", 25): _pdf()}
    first = _fetch(tmp_path, paper="WSJ")
    assert first["url"].endswith("?w=700")
    assert first["live_pending"] == 1  # the redraw is on its way
    _settle(tmp_path)
    second = _fetch(tmp_path, paper="WSJ")
    assert second["url"].endswith(f"?w={server.RASTER_WIDTH_PX}")
    assert "live_pending" not in second
    assert pdf.calls == [_pdf_url("WSJ", 25)]  # the PDF only: the JPEG is what it had
    clocks.wall += 2 * server.RECHECK_S
    _fetch_settled(tmp_path, paper="WSJ")
    assert len(pdf.calls) == 1  # sharp now: nothing more to do


@needs_pdfium
def test_a_redraw_that_finds_no_pdf_backs_off_then_stops(
    tmp_path: Path, pdf: FakeCdn, clocks: Clocks
) -> None:
    _seed(tmp_path, "WSJ_20261025.jpg")  # 700 px wide
    assert _fetch_settled(tmp_path, paper="WSJ")["url"].endswith("?w=700")
    assert pdf.calls == [_pdf_url("WSJ", 25)]
    retry = _state(tmp_path)["pdf_retry"]["WSJ"]
    assert (retry["day"], retry["tries"]) == ("2026-10-25", 1)
    assert retry["after"] == pytest.approx(clocks.wall + server.PDF_RETRY_S, abs=5)
    # Inside the backoff (and the recheck wait) nothing is asked for.
    clocks.wall += server.PDF_RETRY_S - 60
    _fetch_settled(tmp_path, paper="WSJ")
    assert len(pdf.calls) == 1
    # Each later try waits twice as long, up to the cap, and the tries stop.
    asked = []
    for _ in range(3 * server.PDF_MAX_TRIES):
        clocks.wall += server.PDF_RETRY_MAX_S
        _fetch_settled(tmp_path, paper="WSJ")
        asked.append(len(pdf.calls))
    assert asked[-1] == server.PDF_MAX_TRIES
    assert _state(tmp_path)["pdf_retry"]["WSJ"]["tries"] == server.PDF_MAX_TRIES
    # A forced refresh asks again whatever the count.
    pdf.pages = {_pdf_url("WSJ", 25): _pdf()}
    forced = _fetch_settled(tmp_path, paper="WSJ", fresh=True)
    assert forced["url"].endswith(f"?w={server.RASTER_WIDTH_PX}")
    assert "WSJ" not in _state(tmp_path)["pdf_retry"]


@needs_pdfium
def test_a_pdf_published_after_the_jpeg_is_still_fetched(
    tmp_path: Path, pdf: FakeCdn, clocks: Clocks
) -> None:
    """The PDF 404s (not out yet) while the JPEG is there: the JPEG is
    shown, and the PDF is asked for again later, not given up on."""
    pdf.pages = {_url("WSJ", 25): PAGE}
    assert _fetch_settled(tmp_path, paper="WSJ")["url"].endswith("?w=700")
    assert _state(tmp_path)["pdf_retry"]["WSJ"]["tries"] == 1
    pdf.pages[_pdf_url("WSJ", 25)] = _pdf()
    clocks.wall += server.PDF_RETRY_S
    data = _fetch_settled(tmp_path, paper="WSJ")
    assert data["url"].endswith(f"?w={server.RASTER_WIDTH_PX}")
    assert "WSJ" not in _state(tmp_path)["pdf_retry"]


@needs_pdfium
def test_a_forced_refresh_skips_the_backoff(tmp_path: Path, pdf: FakeCdn, clocks: Clocks) -> None:
    pdf.pages = {_pdf_url("WSJ", 25): b"<html>", _url("WSJ", 25): PAGE}
    assert _fetch_settled(tmp_path, paper="WSJ")["url"].endswith("?w=700")
    calls = len(pdf.calls)
    clocks.wall += 60
    pdf.pages[_pdf_url("WSJ", 25)] = _pdf()
    assert _fetch_settled(tmp_path, paper="WSJ")["url"].endswith("?w=700")  # backing off
    assert len(pdf.calls) == calls
    data = _fetch_settled(tmp_path, paper="WSJ", fresh=True)
    assert data["url"].endswith(f"?w={server.RASTER_WIDTH_PX}")


@needs_pdfium
def test_an_outage_during_a_redraw_is_tried_again_later(
    tmp_path: Path, pdf: FakeCdn, clocks: Clocks
) -> None:
    _seed(tmp_path, "WSJ_20261025.jpg")
    pdf.down = True
    assert _fetch_settled(tmp_path, paper="WSJ")["url"].endswith("?w=700")
    assert _state(tmp_path)["pdf_retry"]["WSJ"]["tries"] == 1
    pdf.down = False
    pdf.pages = {_pdf_url("WSJ", 25): _pdf()}
    clocks.wall += server.PDF_RETRY_S
    data = _fetch_settled(tmp_path, paper="WSJ")
    assert data["url"].endswith(f"?w={server.RASTER_WIDTH_PX}")


def test_old_retry_records_and_the_old_give_up_record_are_dropped(
    tmp_path: Path, cdn: FakeCdn
) -> None:
    server._write_json(
        tmp_path / "state.json",
        {
            "jpeg_only": {"WSJ": "2026-10-25"},
            "pdf_retry": {
                "OLD": {"day": "2026-10-01", "tries": 2, "after": 0},
                "BAD": 3,
                "NEW": {"day": "2026-10-25", "tries": 1, "after": 0},
            },
        },
    )
    cdn.pages = {_url("WSJ", 25): PAGE}
    _fetch_settled(tmp_path, paper="WSJ")
    state = _state(tmp_path)
    assert "jpeg_only" not in state
    assert set(state["pdf_retry"]) == {"NEW"}


@needs_pdfium
def test_a_slow_pdf_leaves_the_jpeg_its_time(pdf: FakeCdn, clocks: Clocks) -> None:
    """A PDF that trickles in uses up its own share of the edition's time,
    not the JPEG's: the fallback is still fetched."""

    def tick() -> None:
        clocks.mono += 1.0

    pdf.pages = {_pdf_url("WSJ", 25): _Resp(_pdf(), on_read=tick), _url("WSJ", 25): PAGE}
    start = clocks.mono
    assert server.download_edition("WSJ", TODAY) == PAGE
    assert clocks.mono - start <= server.DOWNLOAD_BUDGET_S - server.JPEG_RESERVE_S + 1
    assert pdf.calls == [_pdf_url("WSJ", 25), _url("WSJ", 25)]


def test_drawing_gets_only_the_pdfs_time_left(
    monkeypatch: pytest.MonkeyPatch, pdf: FakeCdn, clocks: Clocks
) -> None:
    timeouts: list[float] = []

    def fake_rasterise(body: bytes, width: int = 0, timeout: float = 0) -> bytes | None:
        timeouts.append(timeout)
        return None

    monkeypatch.setattr(server, "rasterise_pdf", fake_rasterise)
    pdf.pages = {_pdf_url("WSJ", 25): b"%PDF-1.4", _url("WSJ", 25): PAGE}
    assert server.download_edition("WSJ", TODAY) == PAGE
    assert timeouts == [pytest.approx(server.DOWNLOAD_BUDGET_S - server.JPEG_RESERVE_S)]
    # Too little left in the job: not drawn at all, and the JPEG still comes.
    timeouts.clear()
    deadline = clocks.mono + server.JPEG_RESERVE_S + server.RASTER_MIN_S - 1
    assert server.download_edition("WSJ", TODAY, deadline) == PAGE
    assert timeouts == []


@needs_pdfium
def test_every_request_says_who_is_asking(pdf: FakeCdn) -> None:
    """cdn.freedomforum.org answers Python's default urllib User-Agent with a
    403, so the PDF and the JPEG requests both carry the widget's own."""
    pdf.pages = {_url("WSJ", 25): PAGE}
    server.download_edition("WSJ", TODAY)
    assert pdf.agents == [server.USER_AGENT, server.USER_AGENT]
    assert server.USER_AGENT.startswith("tesserae/") and "Python-urllib" not in server.USER_AGENT


def test_client_snaps_the_page_to_whole_pixels() -> None:
    js = (Path(server.__file__).parent / "client.js").read_text()
    assert "snapToPixels(img)" in js
    assert 'addEventListener("load", () => snapToPixels(img)' in js
    # The paper's CSS can't take the property the snap uses.
    assert server.sanitize_css("translate: 0 -2px") == ""


# ----- the lock and handed-out pages -----------------------------------------


def test_a_handed_out_page_is_claimed(tmp_path: Path, cdn: FakeCdn) -> None:
    page = _seed(tmp_path, "WSJ_20261024.jpg", idle_s=IDLE)
    _fetch(tmp_path, paper="WSJ")
    assert time.time() - page.stat().st_mtime < 60


def test_a_busy_lock_answers_with_the_last_claimed_page(
    tmp_path: Path, cdn: FakeCdn, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, "NY_NYT_20261025.jpg")
    claimed = _fetch(tmp_path, paper="NY_NYT")
    monkeypatch.setattr(server, "LOCK_WAIT_S", 0.05)
    with server._LOCK:
        assert _fetch(tmp_path, paper="NY_NYT") == claimed
        # A placement that has had nothing claimed gets no URL at all.
        unclaimed = _fetch(tmp_path)
    assert unclaimed == {"pending": True, "live_refresh_s": server.LIVE_REFRESH_S}


def test_a_page_just_handed_out_survives_a_newer_download(tmp_path: Path, cdn: FakeCdn) -> None:
    """One render serves a 10-day-old last good page; a moment later a newer
    edition lands and pruning runs. The renderer still loading the old
    page's URL must not get a 404."""
    old = _seed(tmp_path, "WSJ_20261015.jpg", idle_s=IDLE)
    cdn.down = True
    assert "/WSJ_20261015.jpg?" in _fetch_settled(tmp_path, paper="WSJ")["url"]
    cdn.down = False
    cdn.pages = {_url("WSJ", 25): PAGE, _url("USAT", 25): PAGE}
    assert _fetch_settled(tmp_path, paper="WSJ", fresh=True)["date"] == "2026-10-25"
    assert old.exists()
    then = time.time() - IDLE
    os.utime(old, (then, then))
    _fetch_settled(tmp_path, paper="USAT")  # the next download prunes it
    assert not old.exists()


def test_prunes_old_editions_but_keeps_each_papers_last_good(tmp_path: Path, cdn: FakeCdn) -> None:
    keep = [
        _seed(tmp_path, "NY_NYT_20261021.jpg", idle_s=IDLE),  # within KEEP_DAYS
        _seed(tmp_path, "WSJ_20261010.jpg", idle_s=IDLE),  # old, but WSJ's newest
        _seed(tmp_path, "NY_NYT_20261013.jpg"),  # old and superseded, but just used
    ]
    gone = [
        _seed(tmp_path, "NY_NYT_20261012.jpg", idle_s=IDLE),  # old and superseded
        _seed(tmp_path, "AZ_ADS_20260901.jpg", idle_s=IDLE),  # past LAST_GOOD_MAX_DAYS
        _seed(tmp_path, ".NY_NYT_20261025.jpg.abc.tmp", idle_s=IDLE),  # interrupted write
    ]
    cdn.pages = {_url("NY_NYT", 25): PAGE}
    _fetch_settled(tmp_path, paper="NY_NYT")
    assert all(p.exists() for p in keep)
    assert not any(p.exists() for p in gone)


def test_too_old_a_page_is_not_shown(tmp_path: Path, cdn: FakeCdn) -> None:
    _seed(tmp_path, "WSJ_20260901.jpg")
    cdn.down = True
    assert "error" in _fetch_settled(tmp_path, paper="WSJ")


# ----- damaged saved pages ---------------------------------------------------


@pytest.mark.parametrize("body", [b"", PAGE[:2000]], ids=["empty", "damaged"])
def test_a_damaged_saved_page_falls_back_to_a_good_one(
    tmp_path: Path, cdn: FakeCdn, body: bytes
) -> None:
    broken = _seed(tmp_path, "NY_NYT_20261025.jpg", body)
    _seed(tmp_path, "NY_NYT_20261024.jpg")
    cdn.down = True
    data = _fetch(tmp_path, paper="NY_NYT")
    assert (data["date"], data["width"], data["height"]) == ("2026-10-24", 700, 1342)
    assert not broken.exists()


def test_a_damaged_saved_page_is_downloaded_again(
    tmp_path: Path, cdn: FakeCdn, clocks: Clocks
) -> None:
    broken = _seed(tmp_path, "NY_NYT_20261025.jpg", b"")
    server._write_json(tmp_path / "state.json", {"checked": {"NY_NYT": clocks.wall}})
    cdn.pages = {_url("NY_NYT", 25): PAGE}
    data = _fetch_settled(tmp_path, paper="NY_NYT", fresh=True)
    assert (data["date"], data["width"]) == ("2026-10-25", 700)
    assert broken.read_bytes() == PAGE


def test_deleting_refuses_a_replaced_file(tmp_path: Path) -> None:
    path = _seed(tmp_path, "NY_NYT_20261025.jpg", b"")
    judged = server._identity(path)
    server._write_atomic(path, PAGE)  # a download renames over it
    assert not server._unlink_if_same(path, judged)
    assert path.read_bytes() == PAGE


def test_validation_keeps_a_replacement_written_while_it_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _seed(tmp_path, "NY_NYT_20261025.jpg", b"")
    real = server.page_size

    def replaced_meanwhile(body: bytes) -> tuple[int, int] | None:
        if not body:
            server._write_atomic(path, PAGE)  # another writer, mid-judgement
        return real(body)

    monkeypatch.setattr(server, "page_size", replaced_meanwhile)
    server.newest_page(tmp_path / "pages", "NY_NYT", TODAY)
    assert path.read_bytes() == PAGE


# ----- the editor's paper picker ---------------------------------------------

_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://frontpages.freedomforum.org/</loc></url>
<url><loc>https://frontpages.freedomforum.org/newspapers/ia_dr-The_Des_Moines_Register</loc></url>
<url><loc>https://frontpages.freedomforum.org/newspapers/ny_wjny-World_Journal_-_New_York</loc></url>
<url><loc>https://frontpages.freedomforum.org/newspapers/bra^pr_ddc-Di%C3%A1rio_dos_Campos</loc></url>
<url><loc>https://frontpages.freedomforum.org/newspapers/al_td-Times&amp;Daily</loc></url>
</urlset>"""


def _choices(app: Flask) -> list[dict[str, str]]:
    with app.app_context():
        rows: list[dict[str, str]] = _app_module(app).choices("papers")
    return rows


def test_choices_list_rotate_then_the_paper_list_then_freedom_forum(app: Flask) -> None:
    fake = FakeCdn({server.SITEMAP_URL: _SITEMAP.encode()})
    with patch("urllib.request.urlopen", fake):
        rows = _choices(app)
        again = _choices(app)
    assert fake.calls == [server.SITEMAP_URL]  # cached after the first read
    assert rows == again
    values = [r["value"] for r in rows]
    assert values[:5] == ["", "NY_NYT", "WSJ", "USAT", "AZ_ADS"]
    labels = {r["value"]: r["label"] for r in rows}
    assert labels["NY_NYT"] == "The New York Times (NY_NYT)"
    assert labels["IA_DR"] == "The Des Moines Register (IA_DR)"
    assert labels["NY_WJNY"] == "World Journal - New York (NY_WJNY)"
    assert labels["AL_TD"] == "Times&Daily (AL_TD)"
    assert not any("DDC" in v for v in values)


def test_choices_fall_back_when_freedom_forum_is_down(app: Flask) -> None:
    fake = FakeCdn()
    fake.down = True
    with patch("urllib.request.urlopen", fake):
        rows = _choices(app)
        _choices(app)
    assert len(fake.calls) == 1  # a failed read isn't retried on the next editor load
    values = {r["value"] for r in rows}
    assert set(server.FALLBACK_DIRECTORY) <= values


def test_choices_ignores_other_names() -> None:
    assert server.choices("other") == []


# ----- the worker's log, on the admin page -------------------------------------


def _log(tmp_path: Path) -> list[str]:
    return [f"{e['code']} {e['text']}" for e in _state(tmp_path).get("log", [])]


def test_the_log_says_what_each_download_got(tmp_path: Path, cdn: FakeCdn) -> None:
    cdn.pages = {_url("WSJ", 24): PAGE}
    _fetch_settled(tmp_path, paper="WSJ")
    assert _log(tmp_path) == [
        "WSJ 2026-10-25: not out yet",
        f"WSJ 2026-10-24: JPG, 700×1342 px, {server._kb(len(PAGE))} in 0.0 s"
        " (no PDF drawing here: pypdfium2 isn't installed)",
        "WSJ today's edition: next try in 30 min (try 2 of 6)",
    ]


def test_the_log_records_an_outage_and_the_page_kept(tmp_path: Path, cdn: FakeCdn) -> None:
    _seed(tmp_path, "NY_NYT_20261024.jpg")
    cdn.down = True
    _fetch_settled(tmp_path, paper="NY_NYT")
    assert _log(tmp_path) == [
        "NY_NYT 2026-10-25: Freedom Forum unreachable (connection refused)",
        "NY_NYT keeping the 2026-10-24 page",
        "NY_NYT today's edition: next try in 30 min (try 2 of 6)",
    ]
    cdn.down = False
    _fetch_settled(tmp_path, paper="USAT", fresh=True)
    assert _log(tmp_path)[3:] == [
        "USAT 2026-10-25: not out yet",
        "USAT 2026-10-24: not out yet",
        "USAT 2026-10-23: not out yet",
        "USAT no page in the last 3 days",
        "USAT today's edition: next try in 30 min (try 2 of 6)",
    ]


@needs_pdfium
def test_the_log_tells_the_jpeg_from_the_pdf_and_follows_the_retries(
    tmp_path: Path, pdf: FakeCdn, clocks: Clocks
) -> None:
    pdf.pages = {_url("WSJ", 25): PAGE}  # the PDF isn't out yet
    _fetch_settled(tmp_path, paper="WSJ")
    first = _log(tmp_path)
    assert first[0].startswith("WSJ 2026-10-25: JPG, 700×1342 px, ")
    assert first[0].endswith(" (PDF not out yet)")
    assert first[1:] == ["WSJ 2026-10-25: PDF asked for again in 30 min"]
    pdf.pages[_pdf_url("WSJ", 25)] = _pdf()
    clocks.wall += server.PDF_RETRY_S + 1
    _fetch_settled(tmp_path, paper="WSJ")
    later = _log(tmp_path)[2:]
    assert later[0] == "WSJ 2026-10-25: saved from the JPG, asking for the PDF again (try 2 of 6)"
    assert re.fullmatch(
        r"WSJ 2026-10-25: redrawn from the PDF, 1440×\d+ px, \d+ KB in \d+\.\d s", later[1]
    )
    assert len(later) == 2


def test_the_log_keeps_only_the_newest_lines() -> None:
    state: dict[str, Any] = {"log": [{"junk": True}, "x"]}
    for i in range(server.LOG_LINES + 5):
        server._append_log(state, "WSJ", [(float(i), f"line {i}", "ok" if i % 2 else "odd")])
    assert len(state["log"]) == server.LOG_LINES
    assert state["log"][0] == {"at": 5.0, "code": "WSJ", "text": "line 5", "outcome": "ok"}
    assert "outcome" not in state["log"][1]  # not an outcome the page knows
    assert state["log"][-1]["text"] == f"line {server.LOG_LINES + 4}"


def test_the_fetches_card_sums_up_the_state_and_lists_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "rasteriser_available", lambda: True)
    at = datetime(2026, 10, 25, 14, 3, 12, tzinfo=UTC).timestamp()
    for name in ("USAT_20261025.jpg", "NY_NYT_20261025.jpg", "WSJ_20261024.jpg"):
        _seed(tmp_path, name)  # 700 px: the JPEG fallback
    server._write_json(
        tmp_path / "state.json",
        {
            "log": [
                {
                    "at": at,
                    "code": "WSJ",
                    "text": "2026-10-25: PDF, 1440\u00d72794 px, 812 KB in 4.2 s",
                    "outcome": "ok",
                },
                {"at": at + 1, "code": "WSJ", "text": "a note"},
                {
                    "at": at + 2,
                    "code": "AZ_ADS",
                    "text": "no page in the last 3 days",
                    "outcome": "missing",
                },
                {"at": at + 3, "code": "", "text": "worker stopped", "outcome": "error"},
                {"at": "soon", "code": "WSJ", "text": "a hand-edited line"},
            ],
            "pdf_retry": {
                "USAT": {"day": "2026-10-25", "tries": 2, "after": at + 3600},
                "NY_NYT": {"day": "2026-10-25", "tries": server.PDF_MAX_TRIES, "after": at},
                # An earlier edition's retry: jobs only redraw today's.
                "WSJ": {"day": "2026-10-24", "tries": 1, "after": at},
                # A paper taken off the list.
                "LAT": {"day": "2026-10-25", "tries": 1, "after": at},
                "BAD": "junk",
            },
            "failed": {"AZ_ADS": "missing", "OLD": "unreachable"},
        },
    )
    listed = ["NY_NYT", "WSJ", "USAT", "AZ_ADS", "CA_LAT"]
    view = server.fetches_view(tmp_path, UTC, listed, today=TODAY)
    assert view["status"] == [
        "Last fetch Oct 25, 14:03.",
        "Asking again for the sharper PDF: USAT at 15:03 (try 3 of 6).",
        "Out of PDF tries for today, showing the smaller JPG: NY_NYT.",
        "No page for AZ_ADS (none in the last 3 days).",
    ]
    assert [(e["when"], e["code"], e["outcome"], e["tone"], e["text"]) for e in view["log"]] == [
        (
            "Oct 25 14:03:12",
            "WSJ   ",
            "OK     ",
            "ok",
            "2026-10-25: PDF, 1440\u00d72794 px, 812 KB in 4.2 s",
        ),
        ("Oct 25 14:03:13", "WSJ   ", "       ", "", "a note"),
        ("Oct 25 14:03:14", "AZ_ADS", "MISSING", "warn", "no page in the last 3 days"),
        ("Oct 25 14:03:15", "-     ", "ERROR  ", "bad", "worker stopped"),
    ]
    assert not view["running"]
    papers = view["papers"]
    assert list(papers) == listed
    assert papers["NY_NYT"]["chip"] == "Today" and papers["NY_NYT"]["tone"] == "ok"
    assert papers["NY_NYT"]["line"] == "Shows the Oct 25 page, from the smaller JPG, blurrier."
    assert papers["WSJ"]["chip"] == "Oct 24" and papers["WSJ"]["tone"] == ""
    assert papers["WSJ"]["line"].endswith("Last fetched Oct 25 at 14:03.")
    assert papers["AZ_ADS"] == {
        "chip": "No page",
        "tone": "bad",
        "line": "No page: none in the last 3 days. "
        "Last checked Oct 25 at 14:03: no page in the last 3 days.",
    }
    assert papers["CA_LAT"]["chip"] == "Not fetched yet"
    # Today's page redrawn sharp since: its retry is done, and with no
    # rasteriser nothing would ever redraw it.
    _seed(tmp_path, "USAT_20261025.jpg", _jpeg(1440, 2700))
    sharp = server.fetches_view(tmp_path, UTC, listed, today=TODAY)
    assert "USAT at" not in " ".join(sharp["status"])
    assert "from the PDF drawn at full width" in sharp["papers"]["USAT"]["line"]
    monkeypatch.setattr(server, "rasteriser_available", lambda: False)
    status = server.fetches_view(tmp_path, UTC, listed, today=TODAY)["status"]
    assert not any(line.startswith(("Asking", "Out of")) for line in status)
    empty = server.fetches_view(tmp_path / "none", UTC, today=TODAY)
    assert empty == {"status": [], "log": [], "running": False, "papers": {}}


@needs_pdfium
def test_an_earlier_editions_jpeg_gets_no_pdf_retry(tmp_path: Path, pdf: FakeCdn) -> None:
    """Today's paper isn't out, yesterday's JPEG is kept: only today's edition
    is ever redrawn, so no retry is recorded or promised for yesterday's."""
    pdf.pages = {_url("WSJ", 24): PAGE}
    assert _fetch_settled(tmp_path, paper="WSJ")["date"] == "2026-10-24"
    assert "WSJ" not in _state(tmp_path).get("pdf_retry", {})
    assert _log(tmp_path)[-1] == (
        "WSJ 2026-10-24: keeping the JPG, an earlier edition's PDF isn't asked for again"
    )
    status = server.fetches_view(tmp_path, None, ["WSJ"], today=TODAY)["status"]
    assert not any("PDF again" in line for line in status)


def test_the_admin_page_is_newspapers_and_shows_the_log(app: Flask, client: FlaskClient) -> None:
    plugin = app.config["PLUGIN_REGISTRY"].get("news_front_pages")
    assert plugin.name == "News, Newspapers"  # the widget keeps its name, in the menu too
    server._write_json(
        Path(plugin.data_dir) / "state.json",
        {"log": [{"at": time.time(), "code": "WSJ", "text": "2026-10-25: JPG, 700 px <b>"}]},
    )
    body = client.get("/plugins/news_front_pages/").get_data(as_text=True)
    assert "Newspapers, Tesserae</title>" in body
    assert re.search(r"<h1>.*Newspapers</h1>", body)
    assert re.search(
        r'href="/plugins/news_front_pages/"[^>]*>\s*<i[^>]*></i>News, Newspapers\s*</a>', body
    )
    fetches = body[body.index('id="fetches"') :]
    assert "data-nx-log" in fetches
    assert "WSJ  <span" in fetches
    assert "2026-10-25: JPG, 700 px &lt;b&gt;" in fetches
    assert "<details" not in fetches  # the log is open
    assert "Pages aren&#39;t fetched on a timer" in body


def test_each_log_line_says_how_it_went(tmp_path: Path, cdn: FakeCdn) -> None:
    cdn.pages = {_url("WSJ", 24): PAGE}
    _fetch_settled(tmp_path, paper="WSJ")
    cdn.down = True
    _fetch_settled(tmp_path, paper="USAT")
    outcomes = [(e["code"], e.get("outcome", "")) for e in _state(tmp_path)["log"]]
    assert outcomes == [
        ("WSJ", "missing"),  # today's not out yet
        ("WSJ", "ok"),  # yesterday's saved
        ("WSJ", ""),  # the retry note
        ("USAT", "error"),
        ("USAT", ""),
    ]


def test_the_admin_page_shows_each_papers_state_and_verify(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _app_module(app)
    monkeypatch.setattr(module, "_edition_today", lambda: TODAY)
    _seed(Path(app.config["PLUGIN_REGISTRY"].get("news_front_pages").data_dir), "WSJ_20261025.jpg")
    body = client.get("/plugins/news_front_pages/").get_data(as_text=True)
    settings = body[: body.index('id="fetches"')]
    # One per row, and one for the paper being added.
    assert body.count("data-nx-verify-btn>") == len(server.DEFAULT_PAPERS) + 1
    assert re.search(r"tg--ok nx-chip\">Today<", settings)
    assert settings.count(">Not fetched yet<") == len(server.DEFAULT_PAPERS) - 1
    assert "Shows the Oct 25 page, from the smaller JPG, blurrier." in settings
    assert "No fetches yet." in body


# ----- Verify and Fetch now, on the admin page ----------------------------------


def _verify(
    app: Flask,
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    pages: dict[str, bytes | _Resp] | None = None,
    *,
    down: bool = False,
) -> tuple[int, dict[str, Any], FakeCdn]:
    module = _app_module(app)
    monkeypatch.setattr(module, "_edition_today", lambda: TODAY)
    monkeypatch.setattr(module, "rasteriser_available", lambda: False)
    fake = FakeCdn(pages)
    fake.down = down
    with patch("urllib.request.urlopen", fake):
        resp = client.post("/plugins/news_front_pages/verify", data={"code": code})
    assert resp.mimetype == "application/json"
    assert resp.headers["Cache-Control"] == "no-store"
    return resp.status_code, resp.get_json(), fake


def _app_dir(app: Flask) -> Path:
    return Path(app.config["PLUGIN_REGISTRY"].get("news_front_pages").data_dir)


def test_verify_downloads_todays_page_without_saving_it(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, data, fake = _verify(app, client, monkeypatch, " ny_nyt ", {_url("NY_NYT", 25): PAGE})
    assert status == 200
    assert data["ok"] is True
    assert (data["chip"], data["tone"], data["code"]) == ("Works", "ok", "NY_NYT")
    assert data["name"] == "The New York Times"
    assert data["message"].startswith(
        "The New York Times (NY_NYT): downloaded today's page (Oct 25). JPG, 700\u00d71342 px"
    )
    thumb = data["thumb"]
    assert thumb.startswith("data:image/jpeg;base64,")
    import base64

    with Image.open(io.BytesIO(base64.b64decode(thumb.split(",", 1)[1]))) as img:
        assert img.size[0] == server.THUMB_WIDTH_PX
    assert _url("NY_NYT", 25) in fake.calls
    assert not list((_app_dir(app) / "pages").glob("*"))  # nothing saved
    last = _state(_app_dir(app))["log"][-1]
    assert last["code"] == "NY_NYT" and last["outcome"] == "ok"
    assert last["text"].startswith("Verify: 2026-10-25: JPG, 700\u00d71342 px")


def test_verify_falls_back_to_the_newest_edition(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, data, _ = _verify(app, client, monkeypatch, "WSJ", {_url("WSJ", 24): PAGE})
    assert status == 200 and data["ok"] is True
    assert data["tone"] == "warn"
    assert "downloaded the Oct 24 page; today's isn't out yet." in data["message"]


def test_verify_says_an_unknown_code_is_no_paper(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, data, fake = _verify(app, client, monkeypatch, "ZZ_NOPE")
    assert status == 200
    assert (data["ok"], data["chip"], data["tone"]) == (False, "Not found", "bad")
    assert data["message"].startswith("No paper with the code ZZ_NOPE: ")
    assert "thumb" not in data
    asked = [u for u in fake.calls if "ZZ_NOPE" in u]
    assert asked == [_url("ZZ_NOPE", d) for d in (25, 24, 23)]
    last = _state(_app_dir(app))["log"][-1]
    assert (last["code"], last["outcome"]) == ("ZZ_NOPE", "missing")


def test_verify_says_a_listed_paper_has_no_recent_page(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, data, _ = _verify(app, client, monkeypatch, "AZ_ADS")
    assert status == 200 and data["ok"] is False
    assert data["message"].startswith(
        "Freedom Forum lists Arizona Daily Star (AZ_ADS), but has no front page for it"
    )


def test_verify_reports_what_came_back_when_it_wasnt_a_page(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, data, _ = _verify(app, client, monkeypatch, "WSJ", {_url("WSJ", 25): b"<html>hi</html>"})
    assert data["ok"] is False
    assert "What came back: 2026-10-25: JPG not an image." in data["message"]


def test_verify_reports_an_outage(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, data, _ = _verify(app, client, monkeypatch, "WSJ", down=True)
    assert status == 200
    assert (data["ok"], data["chip"], data["tone"]) == (False, "Error", "bad")
    assert data["message"].startswith(
        "Couldn't reach Freedom Forum (connection refused), so WSJ wasn't checked."
    )
    last = _state(_app_dir(app))["log"][-1]
    assert (last["outcome"], last["text"]) == (
        "error",
        "Verify: Freedom Forum unreachable (connection refused)",
    )


@pytest.mark.parametrize(
    ("code", "said"),
    [
        ("", "Type a paper code first"),
        ("   ", "Type a paper code first"),
        ("NY NYT", "'NY NYT' isn't a paper code"),
        ("<script>", "'<script>' isn't a paper code"),
        ("../state.json", "isn't a paper code"),
        ("X" * 41, "isn't a paper code"),
    ],
)
def test_verify_refuses_what_isnt_a_code(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch, code: str, said: str
) -> None:
    status, data, fake = _verify(app, client, monkeypatch, code)
    assert status == 400
    assert data["ok"] is False and data["chip"] == "Bad code"
    assert said in data["message"]
    assert fake.calls == []
    assert not (_app_dir(app) / "state.json").exists()


def test_verify_runs_one_check_at_a_time(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _app_module(app)._VERIFY_LOCK
    assert lock.acquire(blocking=False)
    try:
        status, data, fake = _verify(app, client, monkeypatch, "WSJ")
    finally:
        lock.release()
    assert status == 409 and data["chip"] == "Busy"
    assert fake.calls == []


def test_verify_runs_in_the_widgets_network_scope(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import capabilities

    seen: list[Any] = []
    fake = FakeCdn({_url("WSJ", 25): PAGE})

    def urlopen(req: Any, timeout: float | None = None) -> _Resp:
        seen.append(capabilities._active.get())
        return fake(req, timeout)

    module = _app_module(app)
    monkeypatch.setattr(module, "_edition_today", lambda: TODAY)
    monkeypatch.setattr(module, "rasteriser_available", lambda: False)
    with patch("urllib.request.urlopen", urlopen):
        assert (
            client.post("/plugins/news_front_pages/verify", data={"code": "WSJ"}).status_code == 200
        )
    plugin = app.config["PLUGIN_REGISTRY"].get("news_front_pages")
    assert seen and all(scope is plugin.capabilities for scope in seen)


def test_fetch_now_asks_for_the_papers_without_todays_page(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _app_module(app)
    monkeypatch.setattr(module, "_edition_today", lambda: TODAY)
    monkeypatch.setattr(module, "rasteriser_available", lambda: False)
    data_dir = _app_dir(app)
    _seed(data_dir, "NY_NYT_20261025.jpg")
    fake = FakeCdn({_url("WSJ", 25): PAGE})
    with patch("urllib.request.urlopen", fake):
        resp = client.post("/plugins/news_front_pages/fetch")
        assert module._join_worker(data_dir, 10)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/plugins/news_front_pages/#fetches")
    assert not any("NY_NYT" in u for u in fake.calls)
    assert _url("WSJ", 25) in fake.calls
    assert _url("AZ_ADS", 25) in fake.calls  # switched off, but still in the list
    assert (data_dir / "pages" / "WSJ_20261025.jpg").exists()
    log = _state(data_dir)["log"]
    assert log[0] == {
        "at": log[0]["at"],
        "code": "",
        "text": "Fetch now: asked for WSJ, USAT, AZ_ADS",
    }
    page = client.get("/plugins/news_front_pages/").get_data(as_text=True)
    assert "Fetching WSJ, USAT, AZ_ADS now." in page


def test_fetch_now_with_every_page_in_asks_for_nothing(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _app_module(app)
    monkeypatch.setattr(module, "_edition_today", lambda: TODAY)
    monkeypatch.setattr(module, "rasteriser_available", lambda: False)
    for row in server.DEFAULT_PAPERS:
        _seed(_app_dir(app), f"{row['code']}_20261025.jpg")
    fake = FakeCdn()
    with patch("urllib.request.urlopen", fake):
        resp = client.post("/plugins/news_front_pages/fetch", follow_redirects=True)
    assert fake.calls == []
    assert "Every paper already has today&#39;s page." in resp.get_data(as_text=True)


# ----- retries and cleanup (media_lifecycle.py) ------------------------------


def test_an_unavailable_edition_is_retried_on_a_backoff(
    tmp_path: Path, cdn: FakeCdn, clocks: Clocks
) -> None:
    _seed(tmp_path, "NY_NYT_20261024.jpg")
    cdn.down = True
    calls = []

    def render(**kw: Any) -> dict[str, Any]:
        data = _fetch_settled(tmp_path, paper="NY_NYT", **kw)
        calls.append(len(cdn.calls))
        return data

    assert render()["date"] == "2026-10-24"  # the last good page stays up
    clocks.wall += 31 * 60
    render()  # try 2, 30 minutes on
    clocks.wall += 31 * 60
    render()  # too soon: try 3 waits an hour
    clocks.wall += 30 * 60
    render()
    assert calls == [1, 2, 2, 3]
    retry = _state(tmp_path)["retry"]["NY_NYT"]
    assert (retry["day"], retry["tries"]) == ("2026-10-25", 3)
    # A forced refresh ignores the wait.
    render(fresh=True)
    assert calls[-1] == 4
    # Once it works, the new page shows and the backoff is gone.
    cdn.down = False
    cdn.pages = {_url("NY_NYT", 25): PAGE}
    assert render(fresh=True)["date"] == "2026-10-25"
    assert "NY_NYT" not in _state(tmp_path).get("retry", {})


def test_a_paper_nobody_asks_for_loses_its_pages(
    tmp_path: Path, cdn: FakeCdn, clocks: Clocks
) -> None:
    old_wsj = _seed(tmp_path, "WSJ_20261020.jpg", idle_s=IDLE)
    old_nyt = _seed(tmp_path, "NY_NYT_20261024.jpg", idle_s=IDLE)
    # Renders have been tracked for longer than ORPHAN_AFTER_S, and only
    # NY_NYT has been asked for in that time.
    long_ago = clocks.wall - server.ORPHAN_AFTER_S - 60
    server._write_json(
        tmp_path / "state.json",
        {"wanted_since": long_ago, "wanted": {"WSJ": long_ago, "NY_NYT": clocks.wall}},
    )
    cdn.pages = {_url("NY_NYT", 25): PAGE}
    _fetch_settled(tmp_path, paper="NY_NYT")
    assert not old_wsj.exists()  # WSJ's newest, but WSJ left the list
    assert old_nyt.exists()  # NY_NYT's previous edition, within KEEP_DAYS
    assert (tmp_path / "pages" / "NY_NYT_20261025.jpg").exists()


def test_the_pages_folder_stays_under_budget(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    newest = _seed(tmp_path, "NY_NYT_20261025.jpg", idle_s=IDLE)
    older = [_seed(tmp_path, f"NY_NYT_202610{d}.jpg", idle_s=IDLE + d) for d in (21, 22, 23)]
    size = newest.stat().st_size
    gone = server.prune(pages, TODAY, time.time(), {"NY_NYT"}, max_bytes=size * 2 + 10)
    assert len(gone) == 2 and newest.exists()
    assert sum(p.exists() for p in older) == 1
    server.prune(pages, TODAY, time.time(), {"NY_NYT"}, max_bytes=0)
    assert newest.exists()  # over budget, the page on screen stays


def test_the_smaller_screens_choice_reaches_the_page(tmp_path: Path, cdn: FakeCdn) -> None:
    _seed(tmp_path, "NY_NYT_20261025.jpg")
    ctx = {"data_dir": str(tmp_path)}
    _configure(tmp_path, small_screens="whole")
    whole = server.fetch({"paper": "NY_NYT"}, {}, ctx=ctx)
    assert whole["fit"] == "whole"
    _configure(tmp_path, small_screens="odd")
    assert server.fetch({"paper": "NY_NYT"}, {}, ctx=ctx)["fit"] == "top"
    # What the host passes as settings is ignored: the manifest declares none.
    _configure(tmp_path)
    assert server.fetch({"paper": "NY_NYT"}, {"small_screens": "whole"}, ctx=ctx)["fit"] == "top"
