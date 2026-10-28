"""comics_daily: sources, Bunny Shield, storage, refresh and render, network mocked.

Every request goes through a fake HTTPS handler installed under urllib's
real opener, so cookie handling, redirects and HTTPError bodies behave as
they do live. Fixtures are trimmed copies of the live pages (2026-10-26).
"""

from __future__ import annotations

import email.message
import gzip
import importlib.util
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.response
import zlib
from datetime import date
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path
from typing import Any

import pytest
from flask.testing import FlaskClient
from PIL import Image

_PLUGIN = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "_tesserae_plugins.comics_daily_test.server", _PLUGIN / "server.py"
)
assert _SPEC is not None and _SPEC.loader is not None
srv = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = srv
_SPEC.loader.exec_module(srv)
bunny = srv.bunny_shield
pages = srv.pages
images = srv.images

TODAY = date(2026, 10, 25)
# A crafted challenge, and its answer from the reference implementation
# (argon2-cffi hash_secret_raw, same parameters). Cross-checked when this test was written.
DATA_POW = "userkey-0123456789#chal-abc123#1761500000#sig"
DATA_POW_ANSWER = "170"


def _png(size: tuple[int, int] = (90, 30), mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, "white").save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(size: tuple[int, int] = (95, 110)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 200, 200)).save(buf, format="JPEG")
    return buf.getvalue()


CHALLENGE_PAGE = (
    '<html><body><p>Establishing a secure connection...</p><div id="c" data-pow="'
    + DATA_POW
    + '"></div></body></html>'
)


def _gocomics_page(slug: str, day: str, asset: str) -> str:
    y, m, d = day.split("-")
    return (
        "<html><head>"
        f'<meta property="og:image" content="https://featureassets.gocomics.com/assets/{asset}"/>'
        f'<meta property="og:title" content="Garfield by Jim Davis for October {d}, {y} | GoComics"/>'
        f'<meta property="og:url" content="https://www.gocomics.com/{slug}/{y}/{m}/{d}"/>'
        f'<link rel="canonical" href="https://www.gocomics.com/{slug}/{y}/{m}/{d}"/>'
        '<script src="/.bunny-shield/bd/bunnyprint.js"></script>'
        "</head><body></body></html>"
    )


FARSIDE_PAGE = """
<div class="tfs-content js-daily-dose">
 <div class="tfs-content__1col" data-position="0">
  <div class="card tfs-comic js-comic"><div class="card-body">
   <div class="tfs-comic__image">
    <img data-src="https://featureassets.amuniversal.com/assets/one" data-width="950"
         data-height="1123" class="img-fluid js-lazy-load" alt="ESPRESSO" src="data:x" />
   </div>
   <figure class="figure tfs-comic__caption"><figcaption class="figure-caption">
     While their owners sleep, nervous little dogs prepare for their day.
   </figcaption></figure>
  </div></div>
 </div>
 <div class="tfs-content__1col">
  <div class="card tfs-comic js-comic"><div class="card-body">
   <div class="tfs-comic__image"><img data-src="https://featureassets.amuniversal.com/assets/two" /></div>
   <figure class="figure tfs-comic__caption"><figcaption class="figure-caption"></figcaption></figure>
  </div></div>
 </div>
 <div class="tfs-content__1col">
  <div class="card tfs-comic js-comic"><div class="card-body">
   <div class="tfs-comic__image"><img data-src="https://featureassets.amuniversal.com/assets/three" /></div>
   <figure class="figure"><figcaption>&ldquo;Bummer of a birthmark, Hal.&rdquo;</figcaption></figure>
  </div></div>
 </div>
</div>
<div class="tfs-promo"><img data-src="https://www.thefarside.com/uploads/post_preview_images/x.jpg" /></div>
"""

DILBERT_PAGE = (
    '<main class="w-100 p-1"><h1 class="h4">Wednesday June 14, 1995</h1>'
    '<img class="img-fluid my-3 px-2"alt="Comic for 1995-06-14"src=https://web.archive.org/'
    "web/20230301191927im_/https://assets.amuniversal.com/a37ca4 width=1200 height=364>"
    "<nav></nav></main>"
)


class FakeWeb(urllib.request.HTTPSHandler):
    """Answers requests from a route table; records each one.

    Installed as an urllib handler, so the real opener, cookie processor and
    error handling run around it. A route value is a list of replies served
    in turn (the last repeats): ``(status, headers, body)``.
    """

    def __init__(self, routes: dict[tuple[str, str], list[tuple[int, dict[str, str], bytes]]]):
        super().__init__()
        self.routes = routes
        self.requests: list[urllib.request.Request] = []

    def https_open(self, req: urllib.request.Request) -> Any:
        self.requests.append(req)
        key = (req.get_method(), req.full_url)
        replies = self.routes.get(key)
        if not replies:
            raise urllib.error.URLError(f"no route for {key}")
        status, headers, body = replies.pop(0) if len(replies) > 1 else replies[0]
        msg = email.message.Message()
        for name, value in headers.items():
            msg[name] = value
        resp = urllib.response.addinfourl(io.BytesIO(body), msg, req.full_url, status)
        resp.msg = "OK" if status < 400 else "Error"
        return resp

    def urls(self, method: str = "GET") -> list[str]:
        return [r.full_url for r in self.requests if r.get_method() == method]


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    fake = FakeWeb({})
    real = urllib.request.build_opener
    monkeypatch.setattr(urllib.request, "build_opener", lambda *h: real(*h, fake))
    return fake


def _html(body: str, status: int = 200, **headers: str) -> tuple[int, dict[str, str], bytes]:
    return (status, {"Content-Type": "text/html; charset=utf-8", **headers}, body.encode())


def _img(data: bytes) -> tuple[int, dict[str, str], bytes]:
    return (200, {"Content-Type": "image/png"}, data)


def _gocomics_routes(fake: FakeWeb, slug: str = "garfield") -> None:
    gc = "https://www.gocomics.com"
    fake.routes[("GET", f"{gc}/{slug}/2026/10/25")] = [
        _html(_gocomics_page(slug, "2026-10-25", "abc"), **{"Set-Cookie": "INGRESSCOOKIE=x"})
    ]
    fake.routes[("GET", "https://featureassets.gocomics.com/assets/abc")] = [_img(_png())]


# -- Bunny Shield ----------------------------------------------------------------


def test_challenge_detection() -> None:
    assert bunny.is_challenge({}, CHALLENGE_PAGE)
    assert bunny.is_challenge({"cdn-challenge": "true"}, "")
    # A normal page loads the shield's fingerprint script; that is no challenge.
    assert not bunny.is_challenge({}, _gocomics_page("garfield", "2026-10-25", "abc"))
    assert bunny.find_data_pow(CHALLENGE_PAGE) == DATA_POW
    assert bunny.find_data_pow("<html></html>") is None


def test_argon2id_matches_reference_vector() -> None:
    """cryptography's Argon2id with the challenge's parameters reproduces the
    hash argon2-cffi gave for the same inputs."""
    kdf = bunny._argon2id()(
        salt=b"userkey-0123456789", length=32, iterations=2, lanes=1, memory_cost=512
    )
    assert kdf.derive(b"chal-abc1230").hex() == (
        "a7624216086781e249f5ead85bf630b41e93cc01a8f0521798e8038978b58a91"
    )


def test_solve_finds_the_reference_answer() -> None:
    assert bunny.solve(DATA_POW) == DATA_POW_ANSWER
    assert bunny.challenge_response(DATA_POW, "170") == DATA_POW + "#170"


def test_solve_is_capped_and_rejects_garbage() -> None:
    assert bunny.solve(DATA_POW, max_iterations=5) is None
    with pytest.raises(ValueError):
        bunny.solve("short#chal")
    with pytest.raises(ValueError):
        bunny.solve("nohash")


def _cookie(name: str, value: str, expires: int | None = None) -> Cookie:
    return Cookie(0, name, value, None, False, "www.gocomics.com", False, False, "/", True,
                  True, expires, expires is None, None, None, {})  # fmt: skip


def test_session_cookie_matched_by_prefix_and_expiry() -> None:
    now = 1_800_000_000
    assert bunny.has_session([_cookie("bunny_shield_id_33498", "v")], now)
    assert not bunny.has_session([_cookie("INGRESSCOOKIE", "v")], now)
    assert not bunny.has_session([_cookie("bunny_shield", f"k#s#{now - 5}")], now)
    assert bunny.has_session([_cookie("bunny_shield", f"k#s#{now + 500}")], now)
    assert not bunny.has_session([_cookie("bunny_shield_id_1", "v", expires=now - 1)], now)


# -- page parsers ------------------------------------------------------------------


def test_parse_gocomics_reads_canonical_date_and_label() -> None:
    page = pages.parse_gocomics(_gocomics_page("garfield", "2026-10-24", "abc"), "garfield")
    assert page is not None
    assert page.image_url.endswith("/assets/abc")
    assert page.date == "2026-10-24"
    assert page.label == "Garfield"
    # An unknown name answers a bare page with no Open Graph tags.
    assert pages.parse_gocomics("<html><title>GoComics</title></html>", "nope") is None


def test_parse_farside_cards_only_with_captions() -> None:
    panels = pages.parse_farside(FARSIDE_PAGE)
    assert [p.image_url.rsplit("/", 1)[1] for p in panels] == ["one", "two", "three"]
    assert panels[0].caption.startswith("While their owners sleep")
    assert panels[1].caption == ""
    assert panels[2].caption == "“Bummer of a birthmark, Hal.”"
    assert len(pages.parse_farside(FARSIDE_PAGE, limit=2)) == 2


def test_parse_dilbert_minified_markup() -> None:
    strip = pages.parse_dilbert(DILBERT_PAGE)
    assert strip is not None
    assert strip.date == "1995-06-14"
    assert strip.image_url.startswith("https://web.archive.org/web/")
    assert pages.parse_dilbert("<img alt='logo' src=https://x/y.png>") is None


# -- the Strips option ---------------------------------------------------------------


def test_parse_strips_names_order_comments_and_addresses() -> None:
    text = (
        "garfield, calvinandhobbes\n"
        "# dilbert\n"
        "https://www.gocomics.com/peanuts/2026/10/20\n"
        "pearlsbeforeswine https://featureassets.gocomics.com/assets/xyz\n"
        "garfield\n"
        "bad!name\n"
        "xkcd https://imgs.xkcd.com/comics/a.png\n"
        "dilbert\n"
    )
    sources, problems = srv.parse_strips(text)
    assert [(s.kind, s.name) for s in sources] == [
        ("gocomics", "garfield"),
        ("gocomics", "calvinandhobbes"),
        ("gocomics", "peanuts"),
        ("url", "pearlsbeforeswine"),
        ("dilbert", "dilbert"),
    ]
    assert sources[3].url == "https://featureassets.gocomics.com/assets/xyz"
    assert sources[3].label == "Pearls Before Swine"
    messages = {p.name: p.message for p in problems}
    assert "bad!name" in messages
    assert "imgs.xkcd.com" in messages["xkcd"]


# -- pictures ---------------------------------------------------------------------


def test_normalise_flattens_transparency_and_keeps_jpeg() -> None:
    gif = io.BytesIO()
    frame = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    frame.save(gif, format="GIF")
    picture = images.normalise(gif.getvalue())
    assert picture.ext == "png"
    with Image.open(io.BytesIO(picture.data)) as img:
        assert img.convert("RGB").getpixel((5, 5)) == (255, 255, 255)
    assert images.normalise(_jpeg()).ext == "jpg"
    big = images.normalise(_png((3000, 600)))
    assert (big.width, big.height) == (2400, 480)
    with pytest.raises(ValueError):
        images.normalise(CHALLENGE_PAGE.encode())


def test_store_names_are_validated(tmp_path: Path) -> None:
    store = images.Store(tmp_path)
    name = store.add("gocomics:garfield", images.normalise(_png()))
    assert store.image_path(name) == tmp_path / "images" / name
    assert store.image_path("../state.json") is None
    assert store.image_path("x.png") is None


# -- GoComics through the shield ---------------------------------------------------


def test_gocomics_solves_challenge_on_warm_page_and_keeps_cookies(
    web: FakeWeb, tmp_path: Path
) -> None:
    gc = "https://www.gocomics.com"
    warm = f"{gc}/garfield/2026/10/18"
    web.routes[("GET", warm)] = [
        _html(CHALLENGE_PAGE, status=403),
        _html("<html>real page</html>"),
    ]
    web.routes[("POST", gc + "/.bunny-shield/verify-pow")] = [
        (200, {"Set-Cookie": "bunny_shield_id_33498=s3cret; Path=/"}, b"{}")
    ]
    _gocomics_routes(web)
    cookie_file = tmp_path / srv.COOKIE_FILE

    fetched = srv.GoComics(cookie_file, TODAY).strip("garfield", "Garfield")

    assert fetched.edition == "2026-10-25"
    assert fetched.label == "Garfield"
    asset = "https://featureassets.gocomics.com/assets/abc"
    assert web.urls() == [warm, warm, f"{gc}/garfield/2026/10/25", asset]
    (verify,) = [r for r in web.requests if r.get_method() == "POST"]
    assert verify.get_header("Bunnyshield-challenge-response") == f"{DATA_POW}#{DATA_POW_ANSWER}"
    # Every request carries the Accept headers BunnyCDN insists on.
    for req in web.requests:
        assert req.get_header("Accept", "").startswith("text/html")
        assert req.get_header("Accept-language") == "en-US,en;q=0.5"
    # The strip page was asked for once, with the new session's cookie.
    strip_req = web.requests[3]
    assert "bunny_shield_id_33498=s3cret" in (strip_req.get_header("Cookie") or "")
    assert cookie_file.stat().st_mode & 0o777 == 0o600
    jar = MozillaCookieJar()
    jar.load(str(cookie_file), ignore_discard=True)
    assert bunny.has_session(jar)

    # Next day: the saved session is reused, no warm-up, no proof of work.
    web.requests.clear()
    _gocomics_routes(web, "calvinandhobbes")
    srv.GoComics(cookie_file, TODAY).strip("calvinandhobbes", "Calvin and Hobbes")
    assert web.urls() == [f"{gc}/calvinandhobbes/2026/10/25", asset]
    assert web.urls("POST") == []


def test_gocomics_stale_session_rewarms_once_then_reports_blocked(
    web: FakeWeb, tmp_path: Path
) -> None:
    gc = "https://www.gocomics.com"
    cookie_file = tmp_path / srv.COOKIE_FILE
    jar = MozillaCookieJar()
    jar.set_cookie(_cookie("bunny_shield_id_1", "old"))
    jar.save(str(cookie_file), ignore_discard=True)
    web.routes[("GET", f"{gc}/garfield/2026/10/25")] = [_html(CHALLENGE_PAGE, status=403)]
    web.routes[("GET", f"{gc}/garfield/2026/10/18")] = [_html(CHALLENGE_PAGE, status=403)]
    web.routes[("POST", gc + "/.bunny-shield/verify-pow")] = [(403, {}, b"no")]

    with pytest.raises(srv.SourceError, match="turned down"):
        srv.GoComics(cookie_file, TODAY).strip("garfield", "Garfield")
    # One proof of work attempt, on the warm-up page, not the strip page.
    assert len(web.urls("POST")) == 1
    assert web.urls()[1] == f"{gc}/garfield/2026/10/18"


# -- the other sources ---------------------------------------------------------------


def test_farside_and_dilbert_fetchers(web: FakeWeb) -> None:
    web.routes[("GET", srv.FARSIDE_URL)] = [_html(FARSIDE_PAGE)]
    for name in ("one", "two", "three"):
        web.routes[("GET", f"https://featureassets.amuniversal.com/assets/{name}")] = [
            _img(_jpeg())
        ]
    fetched = srv.fetch_farside(srv.Web())
    assert [caption[:5] for _, caption in fetched.pictures] == ["While", "", "“Bumm"]

    web.routes[("GET", f"{srv.DILBERT}/")] = [_html("<html>awake</html>")]
    picker = srv.random.Random(TODAY.toordinal())
    day = srv.DILBERT_FIRST + srv.timedelta(
        days=picker.randrange((srv.DILBERT_LAST - srv.DILBERT_FIRST).days + 1)
    )
    web.routes[("GET", f"{srv.DILBERT}/{day.isoformat()}")] = [_html(DILBERT_PAGE)]
    web.routes[("GET", pages.parse_dilbert(DILBERT_PAGE).image_url)] = [_img(_png((120, 36)))]
    dilbert = srv.fetch_dilbert(srv.Web(), TODAY)
    assert dilbert.edition == "1995-06-14"


# -- refresh bookkeeping ---------------------------------------------------------------


def test_is_due() -> None:
    go = srv.Source("gocomics:garfield", "gocomics", "garfield")
    now = 1_000_000.0
    today = "2026-10-25"
    assert srv.is_due(go, None, today, now)
    done = {"fetched_on": today, "edition": today, "last_attempt": now - 10, "pictures": [1]}
    assert not srv.is_due(go, done, today, now)
    lagging = dict(done, edition="2026-10-24", attempted_on=today, attempts=1)
    assert not srv.is_due(go, lagging, today, now)
    assert srv.is_due(go, dict(lagging, last_attempt=now - srv.EDITION_RETRY_S), today, now)
    assert not srv.is_due(
        go, dict(lagging, last_attempt=0, attempts=srv.EDITION_ATTEMPTS), today, now
    )
    yesterday = dict(done, fetched_on="2026-10-24", last_attempt=now - 120)
    assert not srv.is_due(go, yesterday, today, now)  # failed two minutes ago: back off
    assert srv.is_due(go, dict(yesterday, last_attempt=now - srv.RETRY_STALE_S), today, now)


def test_failed_refresh_keeps_last_good_and_reports_stale(web: FakeWeb, tmp_path: Path) -> None:
    store = images.Store(tmp_path)
    source = srv.parse_strips("garfield https://featureassets.gocomics.com/assets/m")[0][0]
    web.routes[("GET", source.url)] = [_img(_png())]
    assert srv.refresh_source(store, source, date(2026, 10, 24), web=srv.Web(), gocomics=None)
    first = store.sources()[source.key]["pictures"]

    web.routes[("GET", source.url)] = [_html(CHALLENGE_PAGE)]
    assert not srv.refresh_source(store, source, TODAY, web=srv.Web(), gocomics=None)
    record = store.sources()[source.key]
    assert record["pictures"] == first
    assert "wasn't a picture" in record["error"]

    payload = srv._payload([source], [], store.sources(), set(), {}, store, TODAY.isoformat())
    assert payload["strips"][0]["src"].endswith(first[0]["file"])
    assert payload["notes"] == [
        {"label": "Garfield", "state": "stale", "since": "2026-10-24", "message": record["error"]}
    ]
    assert "error" not in payload


# -- the widget end to end -----------------------------------------------------------------


def _all_routes(fake: FakeWeb) -> None:
    gc = "https://www.gocomics.com"
    fake.routes[("GET", srv.FARSIDE_URL)] = [_html(FARSIDE_PAGE)]
    for name in ("one", "two", "three"):
        fake.routes[("GET", f"https://featureassets.amuniversal.com/assets/{name}")] = [
            _img(_jpeg())
        ]
    fake.routes[("GET", f"{gc}/garfield/2026/10/18")] = [
        _html("<html>ok</html>", **{"Set-Cookie": "bunny_shield_id_9=v; Path=/"})
    ]
    _gocomics_routes(fake)


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_widget_renders(
    client: FlaskClient, web: FakeWeb, monkeypatch: pytest.MonkeyPatch, size: str
) -> None:
    _all_routes(web)
    monkeypatch.setattr(srv, "_today", lambda: TODAY)
    plugin = client.application.config["PLUGIN_REGISTRY"].get("comics_daily")
    monkeypatch.setattr(plugin.server_module, "_today", lambda: TODAY)
    resp = client.get(f"/_test/render?plugin=comics_daily&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="comics_daily"' in body
    # A test cell is small: the comics take turns, so this render shows the
    # Far Side or a strip, not necessarily both (see the screen tests).
    assert "/plugins/comics_daily/img/" in body

    # Pictures are served read-only from data_dir.
    data_dir = Path(plugin.data_dir)
    state = json.loads((data_dir / "state.json").read_text())
    name = state["sources"]["gocomics:garfield"]["pictures"][0]["file"]
    with client.get(f"/plugins/comics_daily/img/{name}") as img:
        assert img.status_code == 200 and img.data[:4] == b"\x89PNG"
    with client.get("/plugins/comics_daily/img/..%2Fstate.json") as missing:
        assert missing.status_code == 404


def test_fetch_answers_from_disk_while_refresh_runs(
    web: FakeWeb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "_today", lambda: TODAY)
    monkeypatch.setattr(srv, "FIRST_RENDER_WAIT_S", 0.0)
    release = srv.threading.Event()
    real_work = srv._work
    monkeypatch.setattr(srv, "_work", lambda run: (release.wait(5), real_work(run)))
    _all_routes(web)
    options = {"strips": "garfield", "far_side": False}

    first = srv.fetch(options, {}, ctx={"data_dir": str(tmp_path)})
    assert first["strips"] == []
    assert first["updating"] and first["live_refresh_s"] == srv.LIVE_REFRESH_S
    assert first["live_pending"] == 1

    release.set()
    run = srv._RUNS[str(tmp_path)]
    assert run.thread is not None
    run.thread.join(5)
    second = srv.fetch(options, {}, ctx={"data_dir": str(tmp_path)})
    assert len(second["strips"]) == 1 and "live_refresh_s" not in second
    started = time.time()
    srv.fetch(options, {}, ctx={"data_dir": str(tmp_path)})
    assert time.time() - started < 1  # nothing due: no new work, no waiting


def test_no_sources_is_an_error(tmp_path: Path) -> None:
    out = srv.fetch({"strips": "", "far_side": False}, {}, ctx={"data_dir": str(tmp_path)})
    assert "Turn on a strip or the Far Side" in out["error"]


# -- review regressions ------------------------------------------------------------


def _load(name: str, file: str = "server.py") -> Any:
    spec = importlib.util.spec_from_file_location(name, _PLUGIN / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _wait_done(data_dir: Path) -> Any:
    run = srv._RUNS[str(data_dir)]
    assert run.thread is not None
    run.thread.join(5)
    assert run.done and run.active is None
    return run


def test_storage_failure_is_contained(
    web: FakeWeb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk while recording fails the source, never the worker."""
    store = images.Store(tmp_path)
    source = srv.parse_strips("garfield https://featureassets.gocomics.com/assets/m")[0][0]

    def broken(*_a: Any, **_k: Any) -> Any:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "update", broken)
    assert not srv.refresh_source(store, source, TODAY, web=srv.Web(), gocomics=None)


def test_crashed_worker_ends_its_run_and_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(srv, "refresh_source", explode)
    source = srv.Source("url:x", "url", "x", "https://featureassets.gocomics.com/assets/x")
    first = srv._enqueue(tmp_path, [source], TODAY)
    _wait_done(tmp_path)
    assert srv._running_keys(tmp_path) == set()

    # A run left "not done" by a thread that died anyway is not reused.
    first.done = False
    first.active = source
    monkeypatch.setattr(srv, "refresh_source", lambda *a, **k: True)
    second = srv._enqueue(tmp_path, [source], TODAY)
    assert second is not first
    _wait_done(tmp_path)


def test_without_argon2id_only_gocomics_challenges_fail(
    web: FakeWeb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An older cryptography (no Argon2id) still loads the widget."""
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.kdf.argon2", None)
    old = _load("_comics_daily_test_old_crypto.bunny_shield", "bunny_shield.py")
    assert old.is_challenge({}, CHALLENGE_PAGE)
    with pytest.raises(old.SolverUnavailable):
        old.solve(DATA_POW)

    monkeypatch.setattr(srv, "bunny_shield", old)
    gc = "https://www.gocomics.com"
    web.routes[("GET", f"{gc}/garfield/2026/10/18")] = [_html(CHALLENGE_PAGE, status=403)]
    with pytest.raises(srv.SourceError, match="cryptography 44"):
        srv.GoComics(tmp_path / srv.COOKIE_FILE, TODAY).strip("garfield", "Garfield")


def test_reloaded_module_shares_the_running_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    again = _load("_comics_daily_test_reload.server")
    assert again._RUNS is srv._RUNS
    assert again._RUNS_LOCK is srv._RUNS_LOCK
    assert again.images._LOCK is images._LOCK

    gate = threading.Event()
    calls: list[str] = []

    def slow(_store: Any, source: Any, *_a: Any, **_k: Any) -> bool:
        gate.wait(5)
        calls.append(source.key)
        return True

    monkeypatch.setattr(srv, "refresh_source", slow)
    a = srv.Source("url:a", "url", "a", "https://featureassets.gocomics.com/assets/a")
    b = again.Source("url:b", "url", "b", "https://featureassets.gocomics.com/assets/b")
    run = srv._enqueue(tmp_path, [a], TODAY)
    # The reloaded code queues onto the old worker instead of starting another.
    assert again._enqueue(tmp_path, [b], TODAY) is run
    assert run.keys() == {"url:a", "url:b"}
    gate.set()
    _wait_done(tmp_path)
    assert calls == ["url:a", "url:b"]
    assert again._running_keys(tmp_path) == set()


def test_finished_source_is_not_fetched_again_from_a_stale_snapshot(
    web: FakeWeb, tmp_path: Path
) -> None:
    store = images.Store(tmp_path)
    source = srv.parse_strips("garfield https://featureassets.gocomics.com/assets/m")[0][0]
    web.routes[("GET", source.url)] = [_img(_png())]
    assert srv.refresh_source(store, source, TODAY, web=srv.Web(), gocomics=None)
    web.requests.clear()
    before = store.sources()[source.key]

    # A render that read the state before that landed queues it again.
    srv._enqueue(tmp_path, [source], TODAY)
    _wait_done(tmp_path)
    assert web.requests == []
    assert store.sources()[source.key]["attempts"] == before["attempts"]

    # And a live run that already did it won't take it back.
    run = srv._Run(data_dir=tmp_path, today=TODAY, completed={source.key})
    gate = threading.Event()
    run.thread = threading.Thread(target=gate.wait, args=(5,), daemon=True)
    run.thread.start()
    srv._RUNS[str(tmp_path)] = run
    try:
        assert srv._enqueue(tmp_path, [source], TODAY) is run
        assert run.queue == []
        srv._enqueue(tmp_path, [source], TODAY, force=True)
        assert [s.key for s, _ in run.queue] == [source.key]
    finally:
        run.queue.clear()
        gate.set()
        run.thread.join(5)
        run.done = True


def test_partial_far_side_keeps_the_complete_set(web: FakeWeb, tmp_path: Path) -> None:
    store = images.Store(tmp_path)
    web.routes[("GET", srv.FARSIDE_URL)] = [_html(FARSIDE_PAGE)]
    for name in ("one", "two", "three"):
        web.routes[("GET", f"https://featureassets.amuniversal.com/assets/{name}")] = [
            _img(_jpeg((95 + len(name), 110)))
        ]
    yesterday = date(2026, 10, 24)
    assert srv.refresh_source(store, srv.FARSIDE, yesterday, web=srv.Web(), gocomics=None)
    complete = store.sources()["farside"]["pictures"]
    assert len(complete) == 3

    web.routes[("GET", "https://featureassets.amuniversal.com/assets/two")] = [(503, {}, b"")]
    web.routes[("GET", "https://featureassets.amuniversal.com/assets/three")] = [
        _html("<html>not a picture</html>")
    ]
    assert not srv.refresh_source(store, srv.FARSIDE, TODAY, web=srv.Web(), gocomics=None)
    record = store.sources()["farside"]
    assert record["pictures"] == complete
    assert record["fetched_on"] == "2026-10-24"
    assert record["error"] == "Only 1 of 3 pictures came through."
    # Not marked done for today, so it is tried again after the back-off.
    assert srv.is_due(srv.FARSIDE, record, "2026-10-25", time.time() + srv.RETRY_STALE_S)

    # With nothing better on disk, the partial set stands in.
    fresh = images.Store(tmp_path / "fresh")
    assert not srv.refresh_source(fresh, srv.FARSIDE, TODAY, web=srv.Web(), gocomics=None)
    assert len(fresh.sources()["farside"]["pictures"]) == 1


def test_gocomics_freshness_follows_the_edition() -> None:
    go = srv.Source("gocomics:garfield", "gocomics", "garfield")
    today = "2026-10-25"
    pics = True
    ok_today = {"fetched_on": today, "edition": today}
    assert srv.freshness(go, ok_today, pics, False, today, today) == ("ok", None)
    # Yesterday's strip before today's is due (published is still yesterday).
    lagging = {"fetched_on": today, "edition": "2026-10-24", "attempted_on": today, "attempts": 1}
    assert srv.freshness(go, lagging, pics, False, today, "2026-10-24") == ("ok", None)
    # Once today's is due it is late, however few attempts were made.
    assert srv.freshness(go, lagging, pics, False, today, today) == ("stale", "2026-10-24")
    # The request "worked" today but the site keeps serving an old strip.
    old = dict(lagging, edition="2026-10-20")
    assert srv.freshness(go, old, pics, False, today, "2026-10-24") == ("stale", "2026-10-20")
    # Archived Dilbert and image addresses carry old dates by design.
    dilbert = srv.Source("dilbert", "dilbert", "dilbert")
    assert srv.freshness(dilbert, dict(old), pics, False, today, today) == ("ok", None)
    url = srv.Source("url:x", "url", "garfield", "https://featureassets.gocomics.com/assets/x")
    assert srv.freshness(url, dict(old), pics, False, today, today) == ("ok", None)
    assert srv.freshness(go, {"error": "x"}, False, False, today) == ("missing", None)
    assert srv.freshness(go, {}, False, True, today) == ("pending", None)


def test_published_edition_uses_eastern_midnight_plus_grace() -> None:
    from datetime import datetime, timedelta, timezone

    def at(hour: int, offset: int, day: int) -> datetime:
        return datetime(2026, 10, day, hour, 15, tzinfo=timezone(timedelta(hours=offset)))

    # Phoenix 06:15: New York is past 03:00 on the 26th, so the 26th is due.
    assert srv.published_edition(at(6, -7, 26), "2026-10-26") == "2026-10-26"
    # Phoenix 21:15 on the 25th: New York is just past midnight, within the
    # grace, and the local day is still the 25th anyway.
    assert srv.published_edition(at(21, -7, 25), "2026-10-25") == "2026-10-25"
    # Berlin 07:15 on the 26th: New York's 26th is not three hours old yet.
    assert srv.published_edition(at(7, 1, 26), "2026-10-26") == "2026-10-25"
    # Melbourne 09:15 on the 26th is still the 25th in New York.
    assert srv.published_edition(at(9, 11, 26), "2026-10-26") == "2026-10-25"
    # Honolulu late on the 25th: never ahead of the local day.
    assert srv.published_edition(at(22, -10, 25), "2026-10-25") == "2026-10-25"


def test_partial_set_gives_way_to_a_fuller_one(web: FakeWeb, tmp_path: Path) -> None:
    store = images.Store(tmp_path)
    base = "https://featureassets.amuniversal.com/assets/"
    web.routes[("GET", srv.FARSIDE_URL)] = [_html(FARSIDE_PAGE)]

    def serve(*good: str) -> None:
        for name in ("one", "two", "three"):
            reply = _img(_jpeg((95 + len(name), 110))) if name in good else (503, {}, b"")
            web.routes[("GET", base + name)] = [reply]

    def refresh() -> bool:
        return srv.refresh_source(store, srv.FARSIDE, TODAY, web=srv.Web(), gocomics=None)

    serve("one")
    assert not refresh()
    assert len(store.sources()["farside"]["pictures"]) == 1
    serve("one", "two")
    assert not refresh()
    record = store.sources()["farside"]
    assert len(record["pictures"]) == 2
    assert record["error"] == "Only 2 of 3 pictures came through."
    serve("one", "two", "three")
    assert refresh()
    assert len(store.sources()["farside"]["pictures"]) == 3
    assert store.sources()["farside"]["partial"] is False
    serve("one", "two")
    assert not refresh()
    assert len(store.sources()["farside"]["pictures"]) == 3


def test_decoded_size_is_capped() -> None:
    big = b"a" * 10_000
    with pytest.raises(srv.SourceError, match="more than"):
        srv.decode_body(gzip.compress(big), "gzip", 100, "example.org")
    assert srv.decode_body(gzip.compress(b"hello"), "gzip", 100, "example.org") == b"hello"
    raw = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    deflated = raw.compress(b"hello") + raw.flush()
    assert srv.decode_body(deflated, "deflate", 100, "example.org") == b"hello"
    assert srv.decode_body(b"plain", "", 100, "example.org") == b"plain"
    with pytest.raises(srv.SourceError, match="damaged"):
        srv.decode_body(b"not gzip", "gzip", 100, "example.org")


def test_gzip_members_trailers_and_cumulative_cap() -> None:
    two = gzip.compress(b"hello") + gzip.compress(b"world")
    assert srv.decode_body(two, "gzip", 100, "example.org") == b"helloworld"
    with pytest.raises(srv.SourceError, match="more than"):
        srv.decode_body(gzip.compress(b"a" * 60) * 2, "gzip", 100, "example.org")
    # A stream cut before its CRC/length trailer is damaged, not short.
    with pytest.raises(srv.SourceError, match="damaged"):
        srv.decode_body(gzip.compress(b"hello")[:-4], "gzip", 100, "example.org")
    with pytest.raises(srv.SourceError, match="damaged"):
        srv.decode_body(zlib.compress(b"hello") + b"junk", "deflate", 100, "example.org")


# -- the settings, the admin page and its actions --------------------------------

GC = "https://www.gocomics.com"
LEGACY = {
    "far_side": False,
    "strips": "peanuts\n# garfield\ndilbert https://featureassets.gocomics.com/assets/x",
    "gap": 20,
}


def _app_module(client: FlaskClient) -> Any:
    return client.application.config["PLUGIN_REGISTRY"].get("comics_daily").server_module


def _plugin(client: FlaskClient) -> Any:
    return client.application.config["PLUGIN_REGISTRY"].get("comics_daily")


def _settings(client: FlaskClient) -> dict[str, Any]:
    """What config.json holds now (normalised), {} when there is none."""
    values = _app_module(client).config_store(_plugin(client).data_dir).read()
    return values or {}


def _config_path(client: FlaskClient) -> Path:
    return Path(_plugin(client).data_dir) / "config.json"


def _store(client: FlaskClient) -> Any:
    return _app_module(client).images.Store(Path(_plugin(client).data_dir))


def _add_comics_page(client: FlaskClient, options: dict[str, Any]) -> None:
    from app.state.page_store import Cell, Page

    page = Page(
        id="comics-page",
        name="Comics",
        cells=[Cell(id="c1", x=0, y=0, w=1, h=1, plugin="comics_daily", options=options)],
    )
    client.application.config["PAGE_STORE"].save(page)


def _messages(client: FlaskClient) -> list[str]:
    with client.session_transaction() as session:
        return [text for _, text in session.get("_flashes", [])]


def test_legacy_options_become_rows() -> None:
    rows = srv.legacy_strip_rows(LEGACY["strips"])
    assert rows == [
        {"enabled": True, "label": "Peanuts", "slug": "peanuts", "mode": "auto", "image_url": ""},
        {
            "enabled": False,
            "label": "Garfield",
            "slug": "garfield",
            "mode": "auto",
            "image_url": "",
        },
        {
            "enabled": True,
            "label": "Dilbert",
            "slug": "dilbert",
            "mode": "url",
            "image_url": "https://featureassets.gocomics.com/assets/x",
        },
    ]
    assert srv.customised(LEGACY)
    assert not srv.customised(dict(srv.LEGACY_DEFAULTS))
    assert not srv.customised({"strips": "garfield calvinandhobbes\npearlsbeforeswine dilbert"})
    assert not srv.customised({})


def test_settings_decide_what_shows_and_the_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = {
        "far_side": False,
        "far_side_count": 9,
        "gap": 12,
        "strips": [
            {"enabled": True, "label": "Odie's", "slug": "garfield", "mode": "auto"},
            {"enabled": False, "slug": "peanuts"},
            {"enabled": True, "slug": "dilbert", "mode": "upload"},
            {"enabled": True, "slug": "nonsequitur", "mode": "url", "image_url": ""},
            {"enabled": True, "slug": "Bad Name!"},
        ],
    }
    config = srv.config_from_settings(settings)
    assert config.far_side_count == 4 and config.gap == 12
    wanted, problems = srv.sources_for(config)
    assert [(s.key, s.label) for s in wanted] == [
        ("gocomics:garfield", "Odie's"),
        ("manual:dilbert", "Dilbert"),
    ]
    assert [p.message for p in problems] == ["Give it an image URL first."]
    queued: list[tuple[list[str], str | None]] = []
    # No network: the render's work is captured, not started.
    monkeypatch.setattr(
        srv,
        "_enqueue",
        lambda root, sources, today, force=False, pasted=None, **_: queued.append(
            ([s.key for s in sources], pasted)
        ),
    )
    out = srv.fetch({"strips": "peanuts"}, settings, ctx={"data_dir": str(tmp_path)})
    assert out["layout"]["far_side"] is False and out["layout"]["gap"] == 12
    # The cell's own options are ignored when settings are given.
    assert queued == [(["gocomics:garfield"], "")]  # no pasted cookies without a host
    # The manual strip has no picture yet: noted, never fetched.
    notes = {note["label"]: note for note in out["notes"]}
    assert notes["Dilbert"]["state"] == "missing"
    assert notes["Dilbert"]["message"] == "No picture uploaded yet."


def test_settings_are_seeded_from_the_first_customised_cell(client: FlaskClient) -> None:
    _add_comics_page(client, LEGACY)
    assert not _config_path(client).exists()
    page = client.get("/plugins/comics_daily/")
    assert page.status_code == 200
    settings = _settings(client)
    assert [r["slug"] for r in settings["strips"]] == ["peanuts", "garfield", "dilbert"]
    assert settings["strips"][1]["enabled"] is False
    assert settings["far_side"] is False and settings["gap"] == 20
    # From now on config.json rules: a cell's own options are ignored.
    module = _app_module(client)
    with client.application.test_request_context():
        config = module.resolve_config({"strips": "bc", "far_side": True}, {})
    assert [s.slug for s in config.strips] == ["peanuts", "garfield", "dilbert"]
    assert config.far_side is False
    # Nothing is written to settings.json: the widget declares no settings.
    assert (client.application.config["SETTINGS_STORE"].get_section("plugins") or {}).get(
        "comics_daily"
    ) is None


def test_untouched_cells_seed_the_defaults(client: FlaskClient) -> None:
    _add_comics_page(client, dict(srv.LEGACY_DEFAULTS))
    client.get("/plugins/comics_daily/")
    settings = _settings(client)
    assert [r["slug"] for r in settings["strips"]] == [
        "dilbert",
        "garfield",
        "calvinandhobbes",
        "pearlsbeforeswine",
    ]
    assert settings["far_side"] is True
    assert (settings["gap"], settings["gap_min"], settings["gap_max"]) == (32, 6, 48)


def test_a_render_seeds_the_settings_too(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _app_module(client)
    monkeypatch.setattr(module, "_enqueue", lambda *a, **k: None)
    with client.application.test_request_context():
        out = module.fetch(LEGACY, {}, ctx={"data_dir": _plugin(client).data_dir})
    assert out["layout"]["far_side"] is False  # seeded from the cell rendered
    assert [r["slug"] for r in _settings(client)["strips"]] == ["peanuts", "garfield", "dilbert"]


def test_the_far_side_and_layout_cards_save_through_their_own_forms(
    client: FlaskClient,
) -> None:
    client.get("/plugins/comics_daily/")
    resp = client.post(
        "/plugins/comics_daily/far-side", data={"far_side": "on", "far_side_count": "2"}
    )
    assert resp.status_code == 302 and resp.location == "/plugins/comics_daily/#farside"
    assert "Saved the Far Side settings." in _messages(client)
    settings = _settings(client)
    # An unticked switch posts nothing: off.
    assert settings["far_side"] is True and settings["captions"] is False
    assert settings["far_side_count"] == 2
    resp = client.post(
        "/plugins/comics_daily/layout",
        data={"small_screens": "one", "gap_min": "4", "gap": "40", "gap_max": "90"},
    )
    assert resp.location == "/plugins/comics_daily/#layout"
    settings = _settings(client)
    assert (settings["gap_min"], settings["gap"], settings["gap_max"]) == (4, 40, 90)
    assert settings["small_screens"] == "one" and settings["status_note"] is False
    assert settings["far_side_count"] == 2  # the other card's values stay
    # The strips are untouched by either card.
    assert [r["slug"] for r in settings["strips"]][:2] == ["dilbert", "garfield"]
    on_disk = json.loads(_config_path(client).read_text())
    assert on_disk["version"] == 1 and on_disk["gap"] == 40


@pytest.mark.parametrize(
    ("path", "data", "message"),
    [
        ("far-side", {"far_side_count": "5"}, "Panels must be between 1 and 4."),
        ("far-side", {"far_side_count": "two"}, "Panels must be a whole number."),
        (
            "layout",
            {"small_screens": "fit", "gap_min": "6", "gap": "201", "gap_max": "48"},
            "Usual space must be between 0 and 200.",
        ),
        (
            "layout",
            {"small_screens": "fit", "gap_min": "-1", "gap": "32", "gap_max": "48"},
            "Smallest space must be between 0 and 200.",
        ),
        (
            "layout",
            {"small_screens": "fit", "gap_min": "6", "gap": "32", "gap_max": "401"},
            "Largest space must be between 0 and 400.",
        ),
        (
            "layout",
            {"small_screens": "all", "gap_min": "6", "gap": "32", "gap_max": "48"},
            "isn't one of the choices for On small screens",
        ),
    ],
)
def test_a_card_save_that_does_not_fit_is_refused_and_changes_nothing(
    client: FlaskClient, path: str, data: dict[str, str], message: str
) -> None:
    client.get("/plugins/comics_daily/")
    before = _config_path(client).read_text()
    resp = client.post(f"/plugins/comics_daily/{path}", data=data)
    assert resp.status_code == 302
    assert any(m.startswith("Not saved: ") and message in m for m in _messages(client))
    assert _config_path(client).read_text() == before


def test_admin_page_lists_strips_with_status_and_tools(client: FlaskClient) -> None:
    client.get("/plugins/comics_daily/")
    store = _store(client)
    module = _app_module(client)
    picture = module.images.normalise(_png((300, 100)))
    name = store.add("gocomics:garfield", picture)
    store.update(
        "gocomics:garfield",
        lambda e: e.update(
            pictures=[{"file": name, "w": 300, "h": 100, "caption": ""}],
            edition="2026-10-20",
            fetched_on="2026-10-20",
            last_success=1.0,
            error="GoComics is blocking automatic downloads right now.",
        ),
    )
    body = client.get("/plugins/comics_daily/").get_data(as_text=True)
    assert "Comics, Tesserae</title>" in body
    card = body[body.index('id="gocomics"') : body.index('id="layout"')]
    rows = re.findall(r'<li class="cx-row[^"]*" id="strip-([a-z0-9-]+)"', card)
    assert rows == ["dilbert", "garfield", "calvinandhobbes", "pearlsbeforeswine"]
    # Dilbert can't be removed (no button) and its strip name can't change.
    assert card.count("/strips/dilbert/remove") == 0 and card.count("/remove") == 3
    assert re.search(r'id="cx-strip-dilbert-slug"[^>]*readonly', card, re.DOTALL)
    garfield = card[card.index('id="strip-garfield"') : card.index('id="strip-calvinandhobbes"')]
    assert "Blocked" in garfield and "Couldn&#39;t refresh automatically" in garfield
    assert 'action="/plugins/comics_daily/strips/garfield/upload"' in garfield
    assert 'enctype="multipart/form-data"' in garfield
    assert 'formaction="/plugins/comics_daily/strips/garfield/import"' in garfield
    assert '<details class="dx-disclosure cx-edit" open>' in garfield  # it needs a look
    assert 'action="/plugins/comics_daily/strips/add"' in card
    today = body[body.index('id="today"') :]
    assert f"/plugins/comics_daily/img/{name}" in today
    assert "needs a look: Garfield (blocked)" in today
    nav = body[: body.index("<main")]
    assert re.search(r'href="/plugins/comics_daily/"[^>]*>\s*<i[^>]*></i>Comics\s*</a>', nav)


@pytest.mark.parametrize(
    ("typed", "slug"),
    [
        ("https://www.gocomics.com/peanuts", "peanuts"),
        ("gocomics.com/Peanuts/2026/10/25", "peanuts"),
        ("  nonsequitur ", "nonsequitur"),
        ("https://example.com/peanuts", None),
        ("../etc", None),
        ("", None),
    ],
)
def test_parse_strip_input(typed: str, slug: str | None) -> None:
    assert srv.parse_strip_input(typed) == slug


def _peanuts_routes(fake: FakeWeb, body: str | None = None, status: int = 200) -> None:
    _all_routes(fake)
    page = body if body is not None else _gocomics_page("peanuts", "2026-10-25", "pnts")
    fake.routes[("GET", f"{GC}/peanuts/2026/10/25")] = [_html(page, status)]
    fake.routes[("GET", "https://featureassets.gocomics.com/assets/pnts")] = [_img(_png())]


def test_add_a_gocomics_strip_checks_it_and_fetches_it(
    client: FlaskClient, web: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_app_module(client), "_today", lambda: TODAY)
    client.get("/plugins/comics_daily/")
    _peanuts_routes(web)
    resp = client.post("/plugins/comics_daily/strips/add", data={"strip": f"{GC}/peanuts"})
    assert resp.location == "/plugins/comics_daily/#strip-peanuts"
    rows = _settings(client)["strips"]
    assert rows[-1] == {
        "enabled": True,
        "label": "Garfield",  # the page's own name for it (the fixture's title)
        "slug": "peanuts",
        "mode": "auto",
        "image_url": "",
    }
    record = _store(client).sources()["gocomics:peanuts"]
    assert record["fetched_on"] == "2026-10-25" and record["pictures"]
    log = [(e["outcome"], e["source"]) for e in _store(client).log()]
    assert ("ok", "Garfield") in log and ("added", "Garfield") in log
    # Again: already there.
    client.post("/plugins/comics_daily/strips/add", data={"strip": "peanuts"})
    assert len(_settings(client)["strips"]) == len(rows)
    assert any("already in the list" in m for m in _messages(client))


def test_add_refuses_a_name_gocomics_does_not_have(
    client: FlaskClient, web: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_app_module(client), "_today", lambda: TODAY)
    client.get("/plugins/comics_daily/")
    before = _settings(client)["strips"]
    # A page that isn't a strip page.
    _peanuts_routes(web, body="<html><head></head></html>")
    client.post("/plugins/comics_daily/strips/add", data={"strip": "peanuts"})
    assert _settings(client)["strips"] == before
    assert any("no strip called" in m for m in _messages(client))
    # Every date 404s and so does the strip's own page.
    _all_routes(web)
    for day in ("25", "24", "23"):
        web.routes[("GET", f"{GC}/nosuchstrip/2026/10/{day}")] = [_html("gone", 404)]
    web.routes[("GET", f"{GC}/nosuchstrip")] = [_html("gone", 404)]
    client.post("/plugins/comics_daily/strips/add", data={"strip": "nosuchstrip"})
    assert _settings(client)["strips"] == before
    # Not a name at all.
    client.post("/plugins/comics_daily/strips/add", data={"strip": "<script>"})
    assert any("isn't a GoComics address" in m for m in _messages(client))


def test_add_keeps_a_strip_it_could_not_fetch_yet(
    client: FlaskClient, web: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_app_module(client), "_today", lambda: TODAY)
    client.get("/plugins/comics_daily/")
    _all_routes(web)  # no route for peanuts: GoComics unreachable for it
    client.post("/plugins/comics_daily/strips/add", data={"strip": "peanuts"})
    assert _settings(client)["strips"][-1]["slug"] == "peanuts"
    assert any("couldn't be fetched yet" in m for m in _messages(client))
    assert _store(client).sources()["gocomics:peanuts"]["error"]


def test_add_stops_at_the_most_strips(client: FlaskClient) -> None:
    client.get("/plugins/comics_daily/")
    _save_strips(client, ["dilbert", *[f"strip{i}" for i in range(srv.MAX_STRIPS - 1)]])
    body = client.get("/plugins/comics_daily/").get_data(as_text=True)
    assert "/strips/add" not in body and "remove one to add another" in body
    client.post("/plugins/comics_daily/strips/add", data={"strip": "peanuts"})
    assert len(_settings(client)["strips"]) == srv.MAX_STRIPS
    assert any("at most" in m for m in _messages(client))


def test_upload_makes_a_manual_strip_until_switched_back(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _app_module(client)
    monkeypatch.setattr(module, "_today", lambda: TODAY)
    client.get("/plugins/comics_daily/")
    data = {"file": (io.BytesIO(_png((640, 200), "RGBA")), "garfield.png")}
    resp = client.post(
        "/plugins/comics_daily/strips/garfield/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    row = next(r for r in _settings(client)["strips"] if r["slug"] == "garfield")
    assert row["mode"] == "upload"
    record = _store(client).sources()["manual:garfield"]
    assert record["origin"] == "uploaded" and record["pictures"][0]["w"] == 640
    # Every cell now shows the manual picture, and never fetches it.
    queued: list[str] = []
    monkeypatch.setattr(
        module, "_enqueue", lambda root, sources, *a, **k: queued.extend(s.key for s in sources)
    )
    with client.application.test_request_context():
        out = module.fetch({}, {}, ctx={"data_dir": _plugin(client).data_dir})
    assert "gocomics:garfield" not in queued and "manual:garfield" not in queued
    strip = next(s for s in out["strips"] if s["id"] == "garfield")
    assert strip["src"].startswith("/plugins/comics_daily/img/manual-garfield-")
    assert ("uploaded", "Garfield") in [(e["outcome"], e["source"]) for e in _store(client).log()]
    # A later day it still shows, with a warning on the admin page.
    monkeypatch.setattr(module, "_today", lambda: date(2026, 10, 27))
    body = client.get("/plugins/comics_daily/").get_data(as_text=True)
    assert "This picture is from Oct 25" in body
    # Old manual pictures are never pruned for age.
    _store(client).prune(now=time.time() + 400 * 86400)
    assert "manual:garfield" in _store(client).sources()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "Choose a picture"),
        (b"<html>not a picture</html>", "isn't a picture"),
        # Over the cap (lowered here: werkzeug's test client leaves a request
        # body over 500 KB in an unclosed temp file).
        (b"x" * 2000, "That picture is over"),
    ],
)
def test_upload_refuses_what_is_not_a_usable_picture(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch, payload: bytes, message: str
) -> None:
    monkeypatch.setattr(_app_module(client).images, "MAX_UPLOAD_BYTES", 1000)
    client.get("/plugins/comics_daily/")
    data = {"file": (io.BytesIO(payload), "x.png" if payload else "")}
    client.post(
        "/plugins/comics_daily/strips/garfield/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert any(message in m for m in _messages(client))
    assert "manual:garfield" not in _store(client).sources()
    assert next(r for r in _settings(client)["strips"] if r["slug"] == "garfield")["mode"] == "auto"


def test_upload_refuses_too_many_pixels(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_app_module(client).images, "MAX_PIXELS", 1000)
    client.get("/plugins/comics_daily/")
    data = {"file": (io.BytesIO(_png((100, 100))), "big.png")}
    client.post(
        "/plugins/comics_daily/strips/garfield/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert any("isn't a picture this widget can use" in m for m in _messages(client))
    assert client.post("/plugins/comics_daily/strips/nosuch/upload").status_code == 404


def test_import_fetches_through_the_net_guard(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import net_guard

    asked: list[tuple[str, dict[str, Any]]] = []

    def fake_fetch(url: str, **kw: Any) -> tuple[bytes, str]:
        asked.append((url, kw))
        return _jpeg((800, 250)), "image/jpeg"

    client.get("/plugins/comics_daily/")
    monkeypatch.setattr(net_guard, "fetch_bytes", fake_fetch)
    url = "https://pictures.example.org/today.jpg"
    client.post("/plugins/comics_daily/strips/calvinandhobbes/import", data={"url": url})
    assert asked[0][0] == url
    assert asked[0][1]["allow_local"] is False
    assert asked[0][1]["max_bytes"] == _app_module(client).images.MAX_UPLOAD_BYTES
    row = next(r for r in _settings(client)["strips"] if r["slug"] == "calvinandhobbes")
    assert (row["mode"], row["image_url"]) == ("upload", url)
    record = _store(client).sources()["manual:calvinandhobbes"]
    assert record["origin"] == "imported from pictures.example.org"
    assert record["pictures"][0]["file"].endswith(".jpg")


def test_import_refuses_private_addresses_and_reports_failures(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.get("/plugins/comics_daily/")
    # The real guard: a loopback address is refused before any request.
    client.post(
        "/plugins/comics_daily/strips/garfield/import", data={"url": "http://127.0.0.1/x.png"}
    )
    assert any("isn't on the public web" in m for m in _messages(client))
    client.post("/plugins/comics_daily/strips/garfield/import", data={"url": "ftp://x/y.png"})
    assert any("has to start with https://" in m for m in _messages(client))
    client.post("/plugins/comics_daily/strips/garfield/import", data={"url": ""})
    assert any("Image URL first" in m for m in _messages(client))
    from app import net_guard

    def down(url: str, **kw: Any) -> tuple[bytes, str]:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(net_guard, "fetch_bytes", down)
    client.post(
        "/plugins/comics_daily/strips/garfield/import", data={"url": "https://example.org/x.png"}
    )
    assert any("connection refused" in m for m in _messages(client))
    assert "manual:garfield" not in _store(client).sources()


def test_fetch_now_queues_every_fetched_source_forced(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _app_module(client)
    queued: list[tuple[list[str], bool]] = []
    monkeypatch.setattr(
        module,
        "_enqueue",
        lambda root, sources, today, force=False, **_: queued.append(
            ([s.key for s in sources], force)
        ),
    )
    client.get("/plugins/comics_daily/")
    # Garfield: a manual strip is never fetched.
    client.post(
        "/plugins/comics_daily/strips/garfield/edit",
        data={"label": "Garfield", "slug": "garfield", "mode": "upload", "image_url": ""},
    )
    resp = client.post("/plugins/comics_daily/fetch")
    assert resp.location == "/plugins/comics_daily/#today"
    assert queued == [
        (["farside", "dilbert", "gocomics:calvinandhobbes", "gocomics:pearlsbeforeswine"], True)
    ]
    assert _store(client).log()[-1]["source"] == "Fetch now"


def test_the_log_records_each_fetch(
    web: FakeWeb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "_today", lambda: TODAY)
    store = images.Store(tmp_path)
    _all_routes(web)
    gocomics = srv.GoComics(tmp_path / srv.COOKIE_FILE, TODAY)
    garfield = srv.Source(key="gocomics:garfield", kind="gocomics", name="garfield")
    assert srv.refresh_source(store, garfield, TODAY, web=srv.Web(), gocomics=gocomics)
    assert srv.refresh_source(store, srv.FARSIDE, TODAY, web=srv.Web(), gocomics=None)
    url = srv.Source(
        key="url:x", kind="url", name="x", url="https://featureassets.gocomics.com/nope"
    )
    assert not srv.refresh_source(store, url, TODAY, web=srv.Web(), gocomics=None)
    log = store.log()
    assert [(e["source"], e["outcome"]) for e in log] == [
        ("Garfield", "ok"),
        ("The Far Side", "ok"),
        ("X", "error"),
    ]
    assert re.fullmatch(r"2026-10-25, 1 picture, \d+ KB in \d+\.\d s", log[0]["text"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\d, 3 pictures, \d+ KB in \d+\.\d s", log[1]["text"])
    assert log[2]["text"].startswith("Couldn't reach featureassets.gocomics.com.")
    for i in range(images.LOG_LINES + 3):
        store.append_log({"at": float(i), "source": "x", "outcome": "ok", "text": str(i)})
    assert len(store.log()) == images.LOG_LINES
    assert store.log()[-1]["text"] == str(images.LOG_LINES + 2)


def test_the_log_tells_blocked_from_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = images.Store(tmp_path)
    source = srv.Source(key="gocomics:garfield", kind="gocomics", name="garfield")
    blocked = srv.SourceError("GoComics is blocking automatic downloads right now.")
    srv.record_fetch(store, source, blocked, TODAY, time.time())
    old = srv.Fetched([(_png(), "")], edition="2026-10-20")
    monkeypatch.setattr(srv, "published_edition", lambda now, today: "2026-10-25")
    srv.record_fetch(store, source, old, TODAY, time.time())
    assert [e["outcome"] for e in store.log()] == ["blocked", "stale"]
    assert store.log()[1]["text"].startswith("still the 2026-10-20 strip, 1 picture")


def test_the_pictures_on_the_admin_page_keep_a_mount_prefix(client: FlaskClient) -> None:
    """Behind Home Assistant ingress or a reverse proxy the app lives under a
    prefix; the pictures at the top of the page must load from under it."""
    client.get("/plugins/comics_daily/")
    store = _store(client)
    module = _app_module(client)
    picture = module.images.normalise(_png((300, 100)))
    name = store.add("gocomics:garfield", picture)
    store.update(
        "gocomics:garfield",
        lambda e: e.update(
            pictures=[{"file": name, "w": 300, "h": 100, "caption": ""}],
            edition="2026-10-20",
            fetched_on="2026-10-20",
            last_success=1.0,
        ),
    )
    env = {"SCRIPT_NAME": "/tesserae"}
    body = client.get("/plugins/comics_daily/", environ_overrides=env).get_data(as_text=True)
    sources = re.findall(r'<img[^>]*\bsrc="([^"]*comics_daily/img/[^"]*)"', body)
    assert sources
    assert all(src.startswith("/tesserae/plugins/comics_daily/img/") for src in sources)
    image = client.get(sources[0].removeprefix("/tesserae"))
    try:
        assert image.status_code == 200
    finally:
        image.close()


def test_the_admin_page_shows_the_log(client: FlaskClient) -> None:
    client.get("/plugins/comics_daily/")
    _store(client).append_log(
        {"at": time.time(), "source": "Garfield", "outcome": "blocked", "text": "nope <b>"}
    )
    body = client.get("/plugins/comics_daily/").get_data(as_text=True)
    log = body[body.index('id="log"') :]
    assert "data-log-tail" in log
    # Shown open, a row per entry, a problem marked as one.
    assert "<details" not in log[: log.index("</section>")]
    assert re.search(
        r'cx-log-line--bad">.*?>Garfield<.*?>BLOCKED<.*?>nope &lt;b&gt;<', log, re.DOTALL
    )
    # The cards sit between today's comics and the log, in this order.
    order = [body.index(f'id="{card}"') for card in ("today", "farside", "gocomics", "layout")]
    assert order == sorted(order) and order[-1] < body.index('id="log"')


def test_every_change_is_a_plain_form_that_works_without_javascript(
    client: FlaskClient,
) -> None:
    """No settings card, no repeater: each card saves through a form of its
    own, no form is nested in another, every control sits in the form it
    posts with (no ``form=`` pointing elsewhere), and the page's script is
    the plugin's own."""
    client.get("/plugins/comics_daily/")
    body = client.get("/plugins/comics_daily/").get_data(as_text=True)
    main = body[body.index("<main") : body.index("</main>")]
    forms = re.findall(r"<form\b(.*?)>(.*?)</form>", main, re.DOTALL)
    assert all("<form" not in inner for _, inner in forms)  # nothing nested
    assert ' form="' not in main
    posts = sorted(re.search(r'action="([^"]+)"', attrs).group(1) for attrs, _ in forms)
    expected = {
        "/plugins/comics_daily/far-side",
        "/plugins/comics_daily/layout",
        "/plugins/comics_daily/fetch",
        "/plugins/comics_daily/strips/add",
        "/plugins/comics_daily/cookies",
        "/plugins/comics_daily/",  # the preview's screen picker (GET)
    }
    for slug in ("dilbert", "garfield", "calvinandhobbes", "pearlsbeforeswine"):
        for op in ("move", "edit", "upload"):
            expected.add(f"/plugins/comics_daily/strips/{slug}/{op}")
    assert set(posts) == expected
    for attrs, _ in forms:
        method = re.search(r'method="(\w+)"', attrs).group(1)
        assert method == ("get" if 'action="/plugins/comics_daily/"' in attrs else "post")
    assert "data-repeater" not in body and "/settings/plugin-comics_daily" not in body
    assert "/plugins/comics_daily/static/admin.js" in body
    # The cookies sit inside GoComics.
    gocomics = main[main.index('id="gocomics"') : main.index('id="layout"')]
    assert 'id="cookies"' in gocomics and "Get cookies.txt Locally" in gocomics
    assert "free GoComics account" in gocomics and 'name="cookies"' in gocomics
    # Panels is a choice of 1 to 4, the saved one picked.
    farside = main[main.index('id="farside"') : main.index('id="gocomics"')]
    radios = re.findall(r'name="far_side_count" value="(\d)"( checked)?', farside)
    assert [n for n, _ in radios] == ["1", "2", "3", "4"]
    assert [n for n, on in radios if on] == ["4"]
    # The screen picker works without JavaScript too: ?screen= picks one.
    assert "<noscript><button" in main
    picked = client.get("/plugins/comics_daily/?screen=2").get_data(as_text=True)
    assert '<figure class="cx-screen" data-screen="2">' in picked
    assert re.search(r'<option value="2" selected>', picked)
    fallback = client.get("/plugins/comics_daily/?screen=99").get_data(as_text=True)
    assert '<figure class="cx-screen" data-screen="0">' in fallback


def _row_slugs(client: FlaskClient) -> list[str]:
    return [r["slug"] for r in _settings(client)["strips"]]


def test_rows_move_switch_edit_and_go_with_plain_posts(client: FlaskClient) -> None:
    client.get("/plugins/comics_daily/")
    base = "/plugins/comics_daily/strips"
    resp = client.post(f"{base}/garfield/move", data={"direction": "up"})
    assert resp.location == "/plugins/comics_daily/#strip-garfield"
    assert _row_slugs(client) == ["garfield", "dilbert", "calvinandhobbes", "pearlsbeforeswine"]
    assert "Moved Garfield up." in _messages(client)
    # Already at the top (or the bottom): nothing changes.
    client.post(f"{base}/garfield/move", data={"direction": "up"})
    client.post(f"{base}/pearlsbeforeswine/move", data={"direction": "down"})
    assert _row_slugs(client)[0] == "garfield" and _row_slugs(client)[-1] == "pearlsbeforeswine"
    client.post(f"{base}/dilbert/move", data={"direction": "down"})
    assert _row_slugs(client) == ["garfield", "calvinandhobbes", "dilbert", "pearlsbeforeswine"]
    # On and off; Dilbert can be switched off.
    client.post(f"{base}/dilbert/switch", data={"enabled": "off"})
    assert "Dilbert is switched off." in _messages(client)
    rows = {r["slug"]: r for r in _settings(client)["strips"]}
    assert rows["dilbert"]["enabled"] is False
    client.post(f"{base}/dilbert/switch", data={"enabled": "on"})
    assert {r["slug"]: r for r in _settings(client)["strips"]}["dilbert"]["enabled"] is True
    # Edit: label, source, Image URL, and a new strip name (an address works).
    resp = client.post(
        f"{base}/calvinandhobbes/edit",
        data={
            "label": "  Calvin  ",
            "slug": "https://www.gocomics.com/Peanuts/2026/10/25",
            "mode": "url",
            "image_url": "https://featureassets.gocomics.com/assets/x",
        },
    )
    assert resp.location == "/plugins/comics_daily/#strip-peanuts"
    row = _settings(client)["strips"][1]
    assert row == {
        "enabled": True,
        "label": "Calvin",
        "slug": "peanuts",
        "mode": "url",
        "image_url": "https://featureassets.gocomics.com/assets/x",
    }
    # Remove.
    resp = client.post(f"{base}/peanuts/remove")
    assert resp.location == "/plugins/comics_daily/#gocomics"
    assert "Removed Calvin." in _messages(client)
    assert _row_slugs(client) == ["garfield", "dilbert", "pearlsbeforeswine"]
    # A strip that isn't in the list.
    assert client.post(f"{base}/nosuch/move", data={"direction": "up"}).status_code == 404
    assert client.post(f"{base}/nosuch/edit", data={"slug": "x"}).status_code == 404


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"slug": ""}, "Strip 2: Strip is required."),
        ({"slug": "Bad Name!"}, "Strip 2: Strip: a GoComics name like garfield"),
        ({"slug": "x" * 65}, "Strip is longer than 64 characters."),
        ({"slug": "calvinandhobbes"}, "Not saved: calvinandhobbes is already in the list."),
        ({"label": "L" * 61}, "Label is longer than 60 characters."),
        ({"mode": "fax"}, "'fax' isn't one of the choices for Source."),
        ({"image_url": "https://[", "mode": "url"}, "Image URL must be a web address"),
        ({"image_url": "u" * 2001}, "Image URL is longer than 2000 characters."),
    ],
)
def test_a_strip_edit_that_does_not_fit_is_refused(
    client: FlaskClient, data: dict[str, str], message: str
) -> None:
    client.get("/plugins/comics_daily/")
    before = _config_path(client).read_text()
    form = {"label": "Garfield", "slug": "garfield", "mode": "auto", "image_url": "", **data}
    resp = client.post("/plugins/comics_daily/strips/garfield/edit", data=form)
    assert resp.location == "/plugins/comics_daily/#strip-garfield"
    assert any(m.startswith("Not saved: ") and message in m for m in _messages(client))
    assert _config_path(client).read_text() == before


# -- GoComics cookies pasted on the Comics page --------------------------------

cookies_mod = srv.cookies
SHIELD = "bunny_shield_id_9=abc#sig#1893456000"


@pytest.mark.parametrize(
    "paste",
    [
        # Netscape cookies.txt (Get cookies.txt Locally, Copy All)
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.gocomics.com\tTRUE\t/\tTRUE\t1893456000\tbunny_shield_id_9\tabc#sig#1893456000\n"
        ".gocomics.com\tTRUE\t/\tFALSE\t0\tsession\txyz\n"
        ".example.com\tTRUE\t/\tFALSE\t0\tother\tnope\n",
        # A JSON export
        json.dumps(
            [
                {
                    "name": "bunny_shield_id_9",
                    "value": "abc#sig#1893456000",
                    "domain": ".gocomics.com",
                },
                {"name": "session", "value": "xyz", "domain": "www.gocomics.com"},
                {"name": "other", "value": "nope", "domain": "example.com"},
            ]
        ),
        # A Cookie header
        "Cookie: bunny_shield_id_9=abc#sig#1893456000; session=xyz",
    ],
    ids=["netscape", "json", "header"],
)
def test_cookie_paste_formats(paste: str) -> None:
    pasted = cookies_mod.parse_paste(paste)
    assert pasted.header() == "bunny_shield_id_9=abc#sig#1893456000; session=xyz"
    assert pasted.expires_at == 1893456000


@pytest.mark.parametrize(
    ("paste", "message"),
    [
        ("", "Paste the cookies first"),
        ("session=xyz", "No bunny_shield cookie"),
        ("bunny_shield_x=a\r\nInjected: yes", "isn't a name=value cookie"),
        ('bunny_shield_x="a b"', "characters a cookie can't hold"),
        ("[1, 2", "doesn't read as JSON"),
        ('{"cookies": 3}', "isn't a list of cookies"),
        ("x" * 30000, "far more than"),
    ],
)
def test_cookie_paste_refusals(paste: str, message: str) -> None:
    with pytest.raises(cookies_mod.PasteError, match=message):
        cookies_mod.parse_paste(paste)


def test_pasted_cookies_skip_the_check_and_stay_out_of_the_session_file(
    web: FakeWeb, tmp_path: Path
) -> None:
    _gocomics_routes(web)
    gc = srv.GoComics(tmp_path / srv.COOKIE_FILE, TODAY, pasted=SHIELD + "; session=xyz")
    fetched = gc.strip("garfield", "Garfield")
    assert fetched.edition == "2026-10-25"
    sent = [r for r in web.requests if "gocomics.com/garfield/" in r.full_url]
    assert len(sent) == 1  # no warm-up page: the pasted session was used
    assert "bunny_shield_id_9=abc#sig#1893456000" in sent[0].get_header("Cookie")
    assert gc.pasted_result == "worked"
    saved = (tmp_path / srv.COOKIE_FILE).read_text()
    assert "abc#sig" not in saved and "xyz" not in saved


def test_turned_away_cookies_fall_back_to_the_solver(
    web: FakeWeb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _all_routes(web)
    page = web.routes[("GET", f"{GC}/garfield/2026/10/25")][0]
    web.routes[("GET", f"{GC}/garfield/2026/10/25")] = [_html(CHALLENGE_PAGE, 403), page]
    gc = srv.GoComics(tmp_path / srv.COOKIE_FILE, TODAY, pasted=SHIELD)
    assert gc.strip("garfield", "Garfield").edition == "2026-10-25"
    assert gc.pasted_result == "turned away"
    assert f"{GC}/garfield/2026/10/18" in web.urls()  # set up its own session


def test_cookie_card_saves_encrypted_never_echoes_and_clears(client: FlaskClient) -> None:
    client.get("/plugins/comics_daily/")
    secret = "bunny_shield_id_9=topsecret#sig#1893456000; session=hush"
    client.post("/plugins/comics_daily/cookies", data={"cookies": secret})
    assert any("Saved 2 GoComics cookies" in m for m in _messages(client))
    module = _app_module(client)
    with client.application.test_request_context():
        assert module.saved_cookie_header() == secret
    disk = Path(client.application.config["SETTINGS_STORE"]._path).read_text()
    assert "gocomics_cookies_secret" in disk and "topsecret" not in disk
    body = client.get("/plugins/comics_daily/").get_data(as_text=True)
    assert "topsecret" not in body and "hush" not in body
    card = body[body.index('id="cookies"') :]
    assert "Set</span>" in card and "2 cookies" in card and "Clear" in card
    assert "topsecret" not in json.dumps(_store(client).log())
    # A failed paste changes nothing.
    client.post("/plugins/comics_daily/cookies", data={"cookies": "nope=1"})
    with client.application.test_request_context():
        assert module.saved_cookie_header() == secret
    # How the last session did shows on the card.
    _store(client).update_cookie_status(
        lambda st: st.update(result="turned away", used_at=time.time())
    )
    card = client.get("/plugins/comics_daily/").get_data(as_text=True)
    assert "Turned away" in card and "Paste fresh ones" in card
    client.post("/plugins/comics_daily/cookies/clear")
    with client.application.test_request_context():
        assert module.saved_cookie_header() == ""
    body = client.get("/plugins/comics_daily/").get_data(as_text=True)
    assert "Not set" in body[body.index('id="cookies"') :]


def test_cookie_card_shows_the_browser_check_session(client: FlaskClient) -> None:
    """The session the built-in browser check saved shows as Saved, with
    when it was last updated; never its value."""
    client.get("/plugins/comics_daily/")
    card = client.get("/plugins/comics_daily/").get_data(as_text=True)
    card = card[card.index('id="cookies"') :]
    assert "None yet" in card and '<details class="dx-disclosure cx-paste" open>' in card
    path = _store(client).root / "gocomics-cookies.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        "www.gocomics.com\tFALSE\t/\tTRUE\t0\tbunny_shield_id_9\tsecretvalue\n"
    )
    body = client.get("/plugins/comics_daily/").get_data(as_text=True)
    card = body[body.index('id="cookies"') :]
    assert "Saved</span>" in card and "last updated today at" in card
    assert "secretvalue" not in body
    assert '<details class="dx-disclosure cx-paste">' in card  # nothing to paste
    # A shield cookie past its expiry reads as expired.
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        "www.gocomics.com\tFALSE\t/\tTRUE\t1000\tbunny_shield_id_9\tsecretvalue\n"
    )
    card = client.get("/plugins/comics_daily/").get_data(as_text=True)
    assert "Expired</span>" in card[card.index('id="cookies"') :]


def test_renders_and_fetch_now_hand_the_cookies_to_the_worker(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _app_module(client)
    handed: list[Any] = []
    monkeypatch.setattr(
        module,
        "_enqueue",
        lambda root, sources, today, force=False, pasted=None, **_: handed.append(pasted),
    )
    client.get("/plugins/comics_daily/")
    client.post("/plugins/comics_daily/cookies", data={"cookies": SHIELD})
    client.post("/plugins/comics_daily/fetch")
    with client.application.test_request_context():
        module.fetch({}, {}, ctx={"data_dir": _plugin(client).data_dir})
    assert handed == [SHIELD, SHIELD]


def test_an_oversized_upload_is_refused_before_the_form_is_read(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _app_module(client)
    monkeypatch.setattr(module.images, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(module, "UPLOAD_OVERHEAD_BYTES", 1000)
    client.get("/plugins/comics_daily/")
    parsed: list[bool] = []
    request_class = client.application.request_class
    real = request_class.files

    monkeypatch.setattr(
        request_class,
        "files",
        property(lambda self: parsed.append(True) or real.__get__(self, request_class)),
    )
    data = {"file": (io.BytesIO(b"x" * 20_000), "big.png")}
    client.post(
        "/plugins/comics_daily/strips/garfield/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert parsed == []  # never parsed
    assert any("That picture is over" in m for m in _messages(client))
    assert "manual:garfield" not in _store(client).sources()


@pytest.mark.parametrize("fmt", ["PNG", "GIF", "WEBP", "JPEG"])
def test_picture_sizes_are_read_from_the_header(fmt: str) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (321, 123), "white").save(buf, format=fmt)
    assert images.header_size(buf.getvalue()) == (321, 123)


def test_a_huge_picture_is_refused_before_pillow_opens_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WebP claiming a 16384 x 16384 canvas: refused on its header, so
    Pillow never sets up a decoder (or a canvas) for it."""
    canvas = (16383).to_bytes(3, "little") * 2
    body = b"VP8X" + (10).to_bytes(4, "little") + b"\x10\x00\x00\x00" + canvas
    webp = b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body
    assert images.header_size(webp) == (16384, 16384)
    opened: list[bytes] = []
    monkeypatch.setattr(images.Image, "open", lambda *a, **k: opened.append(b"") or None)
    with pytest.raises(ValueError, match="too many pixels"):
        images.normalise(webp)
    with pytest.raises(ValueError):
        images.normalise(b"BM" + b"\x00" * 64)  # a BMP: not one of the four
    assert opened == []


def _save_strips(client: FlaskClient, slugs: list[str]) -> None:
    """Another request (another tab) saving the strips."""
    rows = [
        {"enabled": True, "label": "", "slug": slug, "mode": "auto", "image_url": ""}
        for slug in slugs
    ]
    store = _app_module(client).config_store(_plugin(client).data_dir)
    assert store.update(lambda values: {**values, "strips": rows}) is not None


def test_seeding_never_overwrites_a_save_that_got_in_first(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _app_module(client)

    def save_meanwhile() -> None:
        # Another request saves the settings while this one is seeding.
        _save_strips(client, ["dilbert", "peanuts"])
        return None

    monkeypatch.setattr(module, "_first_cell_options", save_meanwhile)
    with client.application.test_request_context():
        settings = module.ensure_settings()
    assert [r["slug"] for r in settings["strips"]] == ["dilbert", "peanuts"]
    assert [r["slug"] for r in _settings(client)["strips"]] == ["dilbert", "peanuts"]


def test_an_import_does_not_bring_back_a_strip_removed_meanwhile(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import net_guard

    client.get("/plugins/comics_daily/")

    def slow_download(url: str, **kw: Any) -> tuple[bytes, str]:
        # Garfield is removed in another tab while the picture downloads.
        _save_strips(client, ["dilbert", "calvinandhobbes"])
        return _png((300, 100)), "image/png"

    monkeypatch.setattr(net_guard, "fetch_bytes", slow_download)
    client.post(
        "/plugins/comics_daily/strips/garfield/import", data={"url": "https://example.org/g.png"}
    )
    assert [r["slug"] for r in _settings(client)["strips"]] == ["dilbert", "calvinandhobbes"]


def test_config_updates_are_one_step_and_atomic(tmp_path: Path) -> None:
    store = srv.config_store(tmp_path)

    def bump(values: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.001)  # widen the window a lost update would need
        return {**values, "gap": values["gap"] + 1}

    store.update(lambda values: {**values, "gap": 0})
    threads = [
        threading.Thread(target=lambda: [store.update(bump) for _ in range(10)]) for _ in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert store.read()["gap"] == 60
    # Written by replace: no temp file left behind.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]
    # None from the change leaves the file alone; a value that doesn't fit
    # raises and leaves it too.
    before = (tmp_path / "config.json").read_text()
    assert store.update(lambda values: None) is None
    with pytest.raises(srv.config_file.ConfigError):
        store.update(lambda values: {**values, "gap": "wide"})
    assert (tmp_path / "config.json").read_text() == before


def test_a_damaged_config_file_reads_as_the_defaults_until_the_next_save(
    client: FlaskClient,
) -> None:
    client.get("/plugins/comics_daily/")
    _config_path(client).write_text("{not json")
    assert client.get("/plugins/comics_daily/").status_code == 200
    assert _config_path(client).read_text() == "{not json"  # never re-migrated over it
    assert _row_slugs(client) == ["dilbert", "garfield", "calvinandhobbes", "pearlsbeforeswine"]
    client.post("/plugins/comics_daily/far-side", data={"far_side_count": "3"})
    assert _settings(client)["far_side_count"] == 3


def test_dilbert_is_kept_by_every_save_path(client: FlaskClient) -> None:
    client.get("/plugins/comics_daily/")
    before = _settings(client)["strips"]
    # Removed, or renamed on its edit form.
    client.post("/plugins/comics_daily/strips/dilbert/remove")
    client.post(
        "/plugins/comics_daily/strips/dilbert/edit",
        data={"label": "", "slug": "dilbert2", "mode": "auto", "image_url": ""},
    )
    assert _settings(client)["strips"] == before
    assert sum("built in" in m for m in _messages(client)) == 2
    # The plugin's own writes go through the same check.
    module = _app_module(client)
    rows = [{"slug": "garfield"}]
    data_dir = Path(_plugin(client).data_dir)
    with client.application.test_request_context(), pytest.raises(module.config_file.ConfigError):
        module._patch_rows(data_dir, lambda current: rows)
    assert _settings(client)["strips"] == before


def test_legacy_rows_always_carry_dilbert() -> None:
    rows = srv.legacy_strip_rows("garfield\npeanuts")
    assert rows[-1] == {
        "enabled": False,
        "label": "Dilbert",
        "slug": "dilbert",
        "mode": "auto",
        "image_url": "",
    }


@pytest.mark.parametrize(
    "url", ["https://[", "https://exa mple.org/x.png", "ftp://x/y.png", "nope"]
)
def test_a_malformed_image_url_is_refused_on_save_and_harmless_if_stored(
    client: FlaskClient, url: str
) -> None:
    client.get("/plugins/comics_daily/")
    client.post(
        "/plugins/comics_daily/strips/garfield/edit",
        data={"label": "", "slug": "garfield", "mode": "url", "image_url": url},
    )
    assert next(r for r in _settings(client)["strips"] if r["slug"] == "garfield")["mode"] == "auto"
    assert any("must be a web address" in m for m in _messages(client))
    # One stored before this check (a hand-edited file) is a row problem,
    # not a crash, on the page and in a render.
    on_disk = json.loads(_config_path(client).read_text())
    on_disk["strips"][1].update(mode="url", image_url=url)
    _config_path(client).write_text(json.dumps(on_disk))
    assert client.get("/plugins/comics_daily/").status_code == 200
    module = _app_module(client)
    with client.application.test_request_context():
        config = module.resolve_config({}, {})
    wanted, problems = module.sources_for(config)
    assert "gocomics:garfield" not in [s.key for s in wanted]
    assert [p.message for p in problems] == ["That image address isn't a web address."]


# -- moving from settings.json to config.json ------------------------------------

# What the widget's settings looked like in a live settings.json (section
# ``plugins``) before config.json.
LIVE_SETTINGS = {
    "captions": False,
    "far_side": True,
    "far_side_count": 3,
    "gap": 28,
    "gap_max": 60,
    "gap_min": 8,
    "small_screens": "one",
    "status_note": False,
    "strips": [
        {"enabled": True, "label": "Dilbert", "slug": "dilbert", "mode": "auto", "image_url": ""},
        {"enabled": False, "label": "Odie", "slug": "garfield", "mode": "upload", "image_url": ""},
        {
            "enabled": True,
            "label": "",
            "slug": "nonsequitur",
            "mode": "url",
            "image_url": "https://featureassets.gocomics.com/assets/n",
        },
    ],
}


def _keep_in_settings_json(client: FlaskClient, values: dict[str, Any]) -> None:
    client.application.config["SETTINGS_STORE"].patch_section("plugins", {"comics_daily": values})


def test_the_values_kept_in_settings_json_move_to_config_json_once(
    client: FlaskClient,
) -> None:
    secret = "bunny_shield_id_9=v#s#1893456000"
    _keep_in_settings_json(client, dict(LIVE_SETTINGS))
    client.post("/plugins/comics_daily/cookies", data={"cookies": secret})
    _add_comics_page(client, LEGACY)  # a customised cell: settings.json wins
    assert not _config_path(client).exists()
    assert client.get("/plugins/comics_daily/").status_code == 200
    on_disk = json.loads(_config_path(client).read_text())
    assert {k: v for k, v in on_disk.items() if k != "version"} == LIVE_SETTINGS
    # settings.json keeps them, untouched, for going back a version.
    kept = client.application.config["SETTINGS_STORE"].get_section("plugins")["comics_daily"]
    assert {k: kept[k] for k in LIVE_SETTINGS} == LIVE_SETTINGS
    module = _app_module(client)
    with client.application.test_request_context():
        assert module.saved_cookie_header() == secret  # the cookies stay there
    # Copied once: from now on config.json rules.
    _keep_in_settings_json(client, {**LIVE_SETTINGS, "gap": 99})
    client.post("/plugins/comics_daily/layout", data={"gap_min": "8", "gap": "30", "gap_max": "60"})
    assert _settings(client)["gap"] == 30
    assert _settings(client)["captions"] is False  # what wasn't posted stays as moved


def test_a_render_moves_them_too(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _app_module(client)
    monkeypatch.setattr(module, "_enqueue", lambda *a, **k: None)
    _keep_in_settings_json(client, dict(LIVE_SETTINGS))
    with client.application.test_request_context():
        out = module.fetch({}, {}, ctx={"data_dir": _plugin(client).data_dir})
    assert out["layout"]["gap"] == 28 and out["layout"]["captions"] is False
    assert [r["slug"] for r in _settings(client)["strips"]] == [
        "dilbert",
        "garfield",
        "nonsequitur",
    ]


@pytest.mark.parametrize(
    ("strips", "slugs", "enabled"),
    [
        # The repeater's rows, as a JSON string.
        (json.dumps(LIVE_SETTINGS["strips"]), ["dilbert", "garfield", "nonsequitur"], None),
        # The one-per-line Strips text from before the repeater (legacy_rows):
        # a ``#`` line is a strip switched off, Dilbert is always in the list.
        (
            "garfield\n# peanuts\nbc",
            ["garfield", "peanuts", "bc", "dilbert"],
            [True, False, True, False],
        ),
        # Rows that don't fit are dropped; Dilbert is put back, switched off.
        (
            [{"slug": "Bad Name!"}, {"slug": "bc"}, {"slug": "bc"}, "x"],
            ["bc", "dilbert"],
            [True, False],
        ),
    ],
)
def test_older_value_forms_move_over_as_rows(
    client: FlaskClient, strips: Any, slugs: list[str], enabled: list[bool] | None
) -> None:
    _keep_in_settings_json(client, {"strips": strips, "gap": "36"})
    client.get("/plugins/comics_daily/")
    rows = _settings(client)["strips"]
    assert [r["slug"] for r in rows] == slugs
    if enabled is not None:
        assert [r["enabled"] for r in rows] == enabled
    assert _settings(client)["gap"] == 36


def test_settings_json_without_strips_still_takes_them_from_a_customised_cell(
    client: FlaskClient,
) -> None:
    _keep_in_settings_json(client, {"captions": False, "gocomics_cookies_secret": "x"})
    _add_comics_page(client, LEGACY)
    client.get("/plugins/comics_daily/")
    settings = _settings(client)
    assert [r["slug"] for r in settings["strips"]] == ["peanuts", "garfield", "dilbert"]
    assert settings["far_side"] is False  # from the cell
    assert settings["captions"] is False  # from settings.json


def test_only_the_cookies_in_settings_json_is_a_fresh_start(client: FlaskClient) -> None:
    _keep_in_settings_json(client, {"gocomics_cookies_secret": "x"})
    client.get("/plugins/comics_daily/")
    assert _settings(client) == srv.config_file.normalise(srv.config_file.defaults())


# -- retries, reuse and cleanup (media_lifecycle.py) -----------------------------


def test_a_failing_source_backs_off_and_keeps_its_last_good_picture(
    web: FakeWeb, tmp_path: Path
) -> None:
    store = images.Store(tmp_path)
    go = srv.Source(key="gocomics:garfield", kind="gocomics", name="garfield")
    yesterday = date(2026, 10, 24)
    srv.record_fetch(store, go, srv.Fetched([(_png(), "")], edition="2026-10-24"), yesterday, 0)
    good = store.sources()[go.key]["pictures"]
    now = time.time()
    tries = []
    for n in range(1, srv.lifecycle.TRIES_PER_DAY + 1):
        srv.record_fetch(store, go, srv.SourceError("GoComics is blocking."), TODAY, now)
        record = store.sources()[go.key]
        tries.append(record["retry"]["tries"])
        assert record["pictures"] == good  # the last good picture stays up
        wait = srv.lifecycle.retry_wait(n)
        assert not srv.is_due(go, record, TODAY.isoformat(), now + wait - 60)
        if n < srv.lifecycle.TRIES_PER_DAY:
            assert srv.is_due(go, record, TODAY.isoformat(), now + wait + 1)
    assert tries == list(range(1, srv.lifecycle.TRIES_PER_DAY + 1))
    assert [srv.lifecycle.retry_wait(n) / 60 for n in (1, 2, 3, 4, 5, 6)] == [
        30,
        60,
        120,
        240,
        360,
        360,
    ]
    # Out of tries today; tomorrow starts over.
    assert not srv.is_due(go, record, TODAY.isoformat(), now + 10 * 3600)
    assert srv.is_due(go, record, "2026-10-26", now + 10 * 3600)
    log = store.log()
    assert log[1]["text"].endswith(
        "; the last good picture stays up; next try in 30 min (try 2 of 6)"
    )
    assert log[-1]["text"].endswith("no more tries today")
    # A try that works replaces the picture at once and clears the backoff.
    srv.record_fetch(store, go, srv.Fetched([(_png((99, 33)), "")], edition="2026-10-25"), TODAY, 0)
    record = store.sources()[go.key]
    assert "retry" not in record and record["pictures"] != good and record["error"] is None


def test_fetch_now_ignores_the_backoff(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _app_module(client)
    queued: list[tuple[list[str], bool]] = []
    monkeypatch.setattr(
        module,
        "_enqueue",
        lambda root, sources, today, force=False, **_: queued.append(
            ([s.key for s in sources], force)
        ),
    )
    client.get("/plugins/comics_daily/")
    store = _store(client)
    store.update(
        "gocomics:garfield",
        lambda e: e.update(
            error="x", retry={"day": TODAY.isoformat(), "tries": 6, "after": time.time() + 9e9}
        ),
    )
    client.post("/plugins/comics_daily/fetch")
    assert "gocomics:garfield" in queued[-1][0] and queued[-1][1] is True


def _keep_picture(store: Any, key: str, size: tuple[int, int] = (60, 20), age: float = 0) -> str:
    picture = images.normalise(_png(size))
    name = store.add(key, picture)
    when = time.time() - age
    store.update(
        key,
        lambda e: e.update(
            pictures=[{"file": name, "w": size[0], "h": size[1]}],
            last_attempt=when,
            last_success=when,
        ),
    )
    os.utime(store.image_dir / name, (when, when))
    return name


def test_cleanup_forgets_what_the_settings_no_longer_name(tmp_path: Path) -> None:
    store = images.Store(tmp_path)
    hour = images.PRUNE_GRACE_S + 60
    shown = _keep_picture(store, "gocomics:garfield", age=hour)
    removed = _keep_picture(store, "gocomics:peanuts", (61, 20), age=hour)
    fresh = _keep_picture(store, "gocomics:bc", (62, 20))  # removed a minute ago
    orphan_manual = _keep_picture(store, "manual:peanuts", (63, 20), age=hour)
    kept_manual = _keep_picture(store, "manual:garfield", (64, 20), age=40 * 86400)
    wanted = {"farside", "gocomics:garfield", "manual:garfield"}
    now = time.time()
    # The records the settings no longer name go (bc's only once untouched
    # for the grace period); their pictures are now unused, and the grace
    # counts from now, not from when they were written an hour ago.
    assert store.prune(wanted=wanted, now=now) == []
    records = store.sources()
    assert set(records) == {"gocomics:garfield", "manual:garfield", "gocomics:bc"}
    gone = store.prune(wanted=wanted, now=now + images.PRUNE_GRACE_S + 60)
    assert sorted(gone) == sorted([removed, orphan_manual])
    for name in (shown, kept_manual, fresh):
        assert (store.image_dir / name).exists()


def test_cleanup_keeps_the_folder_under_budget_but_never_what_is_shown(tmp_path: Path) -> None:
    store = images.Store(tmp_path)
    shown = _keep_picture(store, "gocomics:garfield", (400, 300), age=10 * 60)
    store.image_dir.mkdir(exist_ok=True)
    now = time.time()
    spare, ledger = [], {}
    for n in range(3):
        path = store.image_dir / f"gocomics-old-{n:012x}.png"
        path.write_bytes(b"x" * 1000)
        spare.append(path.name)
        ledger[path.name] = now - (30 - n) * 60  # unused 30, 29, 28 min: in the grace
    (tmp_path / images.RETIRED_FILE).write_text(json.dumps(ledger))
    on_screen = (store.image_dir / shown).stat().st_size
    gone = store.prune(wanted={"gocomics:garfield"}, max_bytes=on_screen + 1500, now=now)
    assert gone == spare[:2]  # the longest unused first, until under budget
    gone = store.prune(wanted={"gocomics:garfield"}, max_bytes=0, now=now)
    assert gone == spare[2:]
    assert (store.image_dir / shown).exists()  # over budget, still never deleted


def test_a_picture_that_comes_back_is_never_deleted_under_its_new_record(tmp_path: Path) -> None:
    """Sol's race: a content-addressed picture left unused, then written
    again (same bytes, same name) by an upload just as the cleanup runs."""
    store = images.Store(tmp_path)
    name = _keep_picture(store, "manual:garfield", age=2 * 3600)
    store.update("manual:garfield", lambda e: e.update(pictures=[]))  # replaced
    now = time.time()
    store.prune(now=now - 2 * 3600)  # seen unused two hours ago
    # The upload writes the same bytes again (the record will point at it).
    assert store.add("manual:garfield", images.normalise(_png((60, 20)))) == name
    assert store.prune(now=now) == []  # its grace started over at the write
    assert (store.image_dir / name).exists()
    # And a delete checks the records once more, right before it happens.
    gone = images.lifecycle.sweep(
        store.image_dir,
        keep=set(),
        max_bytes=0,
        grace_s=0,
        now=now + 3600,
        still_unused=lambda n: n != name,
    )
    assert name not in gone and (store.image_dir / name).exists()


# -- the screen: what fits, and taking turns (screen.py) ---------------------------

screen = srv.screen
FARSIDE_ITEM = screen.Item(
    "farside",
    ((950, 1123),) * 4,
    ("While their owners sleep, nervous little dogs prepare for their day.", "", "Hal.", ""),
)
STRIP_ITEMS = [screen.Item("strip", ((1200, 380),)) for _ in range(4)]
ALL_ITEMS = [FARSIDE_ITEM, *STRIP_ITEMS]


def _plan(w: int, h: int, *, one: bool = False, start: int = 0) -> Any:
    return screen.plan(
        w,
        h,
        ALL_ITEMS,
        far_side_count=4,
        captions=True,
        note=True,
        gap_min=6,
        one_at_a_time=one,
        start=start,
    )


def test_the_big_portrait_panel_shows_everything_as_before() -> None:
    chosen = _plan(1440, 2560)
    assert chosen.show == (0, 1, 2, 3, 4) and not chosen.rotating
    assert _plan(1440, 2560, one=True).show == (0, 1, 2, 3, 4)  # it all fits


def test_a_small_landscape_screen_takes_turns_and_never_cuts_a_strip() -> None:
    turns = screen.turns(
        800,
        480,
        ALL_ITEMS,
        far_side_count=4,
        captions=True,
        note=True,
        gap_min=6,
        one_at_a_time=False,
    )
    assert [t.show for t in turns] == [(0,), (1,), (2,), (3,), (4,)]
    assert all(t.rotating for t in turns)
    # Each strip shown fits whole: its height at the cell's width is under
    # the room left.
    room = screen.available_height(800, 480, True)
    heights = screen.heights(800, ALL_ITEMS, 4, True)
    assert all(heights[i] <= room for t in turns[1:] for i in t.show)


def test_a_13_inch_screen_shows_as_many_as_fit_or_one_at_a_time() -> None:
    turns = screen.turns(
        1600,
        1200,
        ALL_ITEMS,
        far_side_count=4,
        captions=True,
        note=True,
        gap_min=6,
        one_at_a_time=False,
    )
    # The Far Side's row of four and a strip fit together; the last turn
    # fills its room by coming round to the top again.
    assert [t.show for t in turns] == [(0, 1), (2, 3), (4, 0)]
    one = screen.turns(
        1600,
        1200,
        ALL_ITEMS,
        far_side_count=4,
        captions=True,
        note=True,
        gap_min=6,
        one_at_a_time=True,
    )
    assert [t.show for t in one] == [(0,), (1,), (2,), (3,), (4,)]


def test_narrow_cells_show_fewer_far_side_panels() -> None:
    wide = screen.heights(1440, [FARSIDE_ITEM], 4, False)[0]
    narrow = screen.heights(600, [FARSIDE_ITEM], 4, False)[0]
    # 600 px holds 2 panels of at least 260 px: each is drawn bigger
    # relative to the cell than 4 across would be.
    assert narrow / 600 > wide / 1440


def test_a_turn_moves_on_once_per_hold_and_previews_never_move_it(tmp_path: Path) -> None:
    store = images.Store(tmp_path)

    def decide(start: int) -> Any:
        return _plan(800, 480, start=start)

    now = 1_000_000.0
    assert store.take_turn("cell", 5, decide, advance=True, now=now).show == (0,)
    # A follow-up inside the hold shows the same; so does a preview later.
    assert store.take_turn("cell", 5, decide, advance=True, now=now + 60).show == (0,)
    assert store.take_turn("cell", 5, decide, advance=False, now=now + 999).show == (0,)
    assert store.take_turn("cell", 5, decide, advance=True, now=now + 300).show == (1,)
    # Another placement has its own turn.
    assert store.take_turn("other", 5, decide, advance=True, now=now + 300).show == (0,)
    for step in range(2, 6):
        shown = store.take_turn("cell", 5, decide, advance=True, now=now + 300 * step).show
    assert shown == (0,)  # round again


def test_fetch_sends_only_this_turns_comics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "_today", lambda: TODAY)
    monkeypatch.setattr(srv, "_enqueue", lambda *a, **k: None)
    store = images.Store(tmp_path)
    ok = {"fetched_on": TODAY.isoformat(), "last_success": time.time(), "error": None}
    for key in ("gocomics:garfield", "gocomics:peanuts"):
        name = store.add(key, images.normalise(_png((1200, 380))))
        store.update(
            key,
            lambda e, name=name: e.update(
                pictures=[{"file": name, "w": 1200, "h": 380}], edition=TODAY.isoformat(), **ok
            ),
        )
    settings = {
        "far_side": False,
        "strips": [{"slug": "garfield"}, {"slug": "peanuts"}],
    }
    small = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 300, "cell_id": "a"}
    out = srv.fetch({}, settings, ctx=small)
    assert [s["id"] for s in out["strips"]] == ["garfield"]
    assert out["turns"] == {"showing": 1, "of": 2}
    big = {"data_dir": str(tmp_path), "cell_w": 1440, "cell_h": 2560, "cell_id": "b"}
    out = srv.fetch({}, settings, ctx=big)
    assert [s["id"] for s in out["strips"]] == ["garfield", "peanuts"] and "turns" not in out


def test_an_incomplete_far_side_set_counts_as_a_failed_try(tmp_path: Path) -> None:
    store = images.Store(tmp_path)
    now = time.time()
    partial = srv.Fetched([(_jpeg(), "a"), (_jpeg(), "b")], expected=4)
    for _ in range(srv.lifecycle.TRIES_PER_DAY):
        srv.record_fetch(store, srv.FARSIDE, partial, TODAY, now)
    record = store.sources()["farside"]
    assert record["retry"]["tries"] == srv.lifecycle.TRIES_PER_DAY
    assert record["partial"] is True and len(record["pictures"]) == 2  # still shown
    # Out of tries for today, however long it waits.
    assert not srv.is_due(srv.FARSIDE, record, TODAY.isoformat(), now + 20 * 3600)
    assert srv.is_due(srv.FARSIDE, record, "2026-10-26", now + 20 * 3600)


def test_identical_cells_on_different_screens_keep_their_own_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sol's collision: two cells with the same options (one ``cell_id``),
    a big panel's render must not move the small screen's turn."""
    monkeypatch.setattr(srv, "_today", lambda: TODAY)
    monkeypatch.setattr(srv, "_enqueue", lambda *a, **k: None)
    store = images.Store(tmp_path)
    ok = {"fetched_on": TODAY.isoformat(), "last_success": time.time(), "error": None}
    for key in ("gocomics:garfield", "gocomics:peanuts", "gocomics:bc"):
        name = store.add(key, images.normalise(_png((1200, 380))))
        store.update(
            key,
            lambda e, name=name: e.update(
                pictures=[{"file": name, "w": 1200, "h": 380}], edition=TODAY.isoformat(), **ok
            ),
        )
    settings = {"far_side": False, "strips": [{"slug": s} for s in ("garfield", "peanuts", "bc")]}

    def render(placement: str, w: int, h: int) -> list[str]:
        ctx = {
            "data_dir": str(tmp_path),
            "cell_w": w,
            "cell_h": h,
            "cell_id": "same",
            "settled": True,
        }
        if placement:
            ctx["placement_id"] = placement
        return [s["id"] for s in srv.fetch({}, settings, ctx=ctx)["strips"]]

    assert render("page1/c1@small", 800, 300) == ["garfield"]
    assert render("page2/c1@big", 1440, 2560) == ["garfield", "peanuts", "bc"]
    # Without a placement id, the cell's size keeps them apart too.
    render("", 800, 300)
    render("", 1440, 2560)
    turns = json.loads((tmp_path / images.ROTATION_FILE).read_text())
    assert set(turns) == {"page1/c1@small", "page2/c1@big", "same:800x300", "same:1440x2560"}
    assert turns["page1/c1@small"]["shown"] == 1 and turns["page2/c1@big"]["shown"] == 3


def test_the_composer_gives_each_grid_cell_its_placement(app: Any) -> None:
    from app import composer

    seen: list[str] = []

    def fake_fetch(plugin_id: str, *a: Any, placement: str = "", **k: Any) -> Any:
        seen.append(placement)
        return {}

    with app.app_context():
        original = composer._fetch_plugin_data
        composer._fetch_plugin_data = fake_fetch  # type: ignore[assignment]
        try:
            composer._parallel_fetch_plugin_data(
                [
                    {"plugin_id": "comics_daily", "resolved_options": {}, "cell": {"id": "a"}},
                    {"plugin_id": "comics_daily", "resolved_options": {}, "cell": {"id": "b"}},
                ],
                1440,
                2560,
                False,
                target_device_id="dev1",
                page_id="p9",
            )
        finally:
            composer._fetch_plugin_data = original  # type: ignore[assignment]
    assert sorted(seen) == ["p9/a@dev1", "p9/b@dev1"]


def test_canvas_comics_fit_and_rotate_per_placement(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import composer
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout, Element

    plugin = app.config["PLUGIN_REGISTRY"].get("comics_daily")
    monkeypatch.setattr(plugin.server_module, "_today", lambda: TODAY)
    monkeypatch.setattr(plugin.server_module, "_enqueue", lambda *a, **k: None)
    store = images.Store(plugin.data_dir)
    for slug in ("garfield", "peanuts", "bc"):
        _keep_picture(store, f"gocomics:{slug}", (1200, 380))
    app.config["SETTINGS_STORE"].update_section(
        "plugins",
        {
            "comics_daily": {
                "far_side": False,
                "strips": [{"slug": s} for s in ("garfield", "peanuts", "bc")],
            }
        },
    )
    page = Page(
        id="p9",
        name="Comics canvas",
        layout_kind="canvas",
        canvas=CanvasLayout(
            w=1440,
            h=2560,
            els=[
                Element(id="a", widget="comics_daily", w=800, h=300),
                Element(id="b", widget="comics_daily", w=800, h=300),
                Element(id="c", widget="comics_daily", w=1440, h=2560),
            ],
        ),
    )
    app.config["PAGE_STORE"].save(page)
    monkeypatch.setattr(composer, "render_template", lambda template, **ctx: json.dumps(ctx["els"]))

    def render(device: str) -> list[list[str]]:
        response = app.test_client().get(f"/compose/p9?for_push=1&device_id={device}")
        assert response.status_code == 200
        return [[s["id"] for s in el["data"]["strips"]] for el in json.loads(response.data)]

    assert render("dev1") == [["garfield"], ["garfield"], ["garfield", "peanuts", "bc"]]
    assert render("dev2") == [["garfield"], ["garfield"], ["garfield", "peanuts", "bc"]]
    turns = json.loads((store.root / images.ROTATION_FILE).read_text())
    assert set(turns) == {
        "p9/a@dev1",
        "p9/b@dev1",
        "p9/c@dev1",
        "p9/a@dev2",
        "p9/b@dev2",
        "p9/c@dev2",
    }
    assert turns["p9/a@dev1"]["shown"] == 1
    assert turns["p9/c@dev1"]["shown"] == 3


def test_an_all_manual_setup_still_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No fetch worker runs when every strip is a manual picture, so renders
    clean up too (at most hourly): a removed strip's picture still goes."""
    monkeypatch.setattr(srv, "_today", lambda: TODAY)
    monkeypatch.setattr(srv, "_enqueue", lambda *a, **k: pytest.fail("nothing to fetch"))
    store = images.Store(tmp_path)
    kept = _keep_picture(store, "manual:garfield", age=3 * 3600)
    orphan = _keep_picture(store, "manual:peanuts", (61, 20), age=3 * 3600)
    settings = {
        "far_side": False,
        "strips": [{"slug": "garfield", "mode": "upload"}],
    }
    ctx = {"data_dir": str(tmp_path)}
    srv.fetch({}, settings, ctx=ctx)  # forgets peanuts' record; its picture is now unused
    assert "manual:peanuts" not in store.sources()
    later = time.time() + images.CLEANUP_EVERY_S + images.PRUNE_GRACE_S + 60
    monkeypatch.setattr(srv.time, "time", lambda: later)
    srv.fetch({}, settings, ctx=ctx)
    assert (store.image_dir / kept).exists()
    assert not (store.image_dir / orphan).exists()
    # Not again within the hour.
    assert store.prune_if_due({"manual:garfield"}, later + 60) is None


def test_disabled_sources_still_clean_up_removed_pictures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "_enqueue", lambda *a, **k: pytest.fail("nothing to fetch"))
    store = images.Store(tmp_path)
    kept = _keep_picture(store, "manual:garfield", age=3 * 3600)
    orphan = _keep_picture(store, "manual:peanuts", (61, 20), age=3 * 3600)
    settings = {
        "far_side": False,
        "strips": [{"slug": s, "enabled": False} for s in ("garfield", "dilbert")],
    }
    ctx = {"data_dir": str(tmp_path)}
    assert "error" in srv.fetch({}, settings, ctx=ctx)
    assert set(store.sources()) == {"manual:garfield"}
    later = time.time() + images.CLEANUP_EVERY_S + images.PRUNE_GRACE_S + 60
    monkeypatch.setattr(srv.time, "time", lambda: later)
    assert "error" in srv.fetch({}, settings, ctx=ctx)
    assert (store.image_dir / kept).exists()
    assert not (store.image_dir / orphan).exists()
    assert store.prune_if_due({"manual:garfield"}, later + 60) is None


# ----------------------------------------------------------------------
# The admin page's preview: which screens, and which it opens on
# ----------------------------------------------------------------------


def _device(device_id: str, name: str, w: int, h: int, *, kind: bool = False) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        id=device_id,
        display_name=name,
        kind_of=None if kind else "esp32_client",
        panel={"w": w, "h": h},
    )


def _grid_page(page_id: str, device_ids: list[str], *cells: tuple[str, int, int]) -> Any:
    from app.state.page_store import Cell, Page

    return Page(
        id=page_id,
        name=page_id.title(),
        device_ids=device_ids,
        cells=[
            Cell(id=f"c{n}", x=0, y=0, w=w, h=h, plugin=plugin)
            for n, (plugin, w, h) in enumerate(cells)
        ],
    )


REFERENCE_NAMES = [name for name, _, _ in screen.SCREENS]


def test_the_preview_opens_on_the_screen_that_shows_comics() -> None:
    kitchen = _device("kitchen", "Kitchen", 800, 480)
    frame = _device("frame", "Hall frame", 1440, 2560)
    builtin = _device("builtin", "Built-in kind", 1600, 1200, kind=True)
    pages_list = [
        _grid_page("clock", ["kitchen"], ("clock", 800, 480)),
        _grid_page("comics", ["frame"], ("comics_daily", 1440, 2560)),
    ]
    screens, default = srv.preview_screens(pages_list, [kitchen, frame, builtin])
    # Its own screens first (the one showing comics leads), then the
    # reference screens; a built-in kind is not a screen anyone has.
    assert [(s["name"], s["w"], s["h"], s["group"]) for s in screens] == [
        ("Hall frame", 1440, 2560, srv.MINE),
        ("Kitchen", 800, 480, srv.MINE),
        *[(n, w, h, srv.REFERENCE) for n, w, h in screen.SCREENS],
    ]
    assert default == 0


def test_a_comics_cell_smaller_than_its_screen_previews_at_its_own_size() -> None:
    frame = _device("frame", "Hall frame", 1440, 2560)
    half = _grid_page("half", ["frame"], ("clock", 1440, 1280), ("comics_daily", 1440, 1280))
    screens, default = srv.preview_screens([half], [frame])
    assert (screens[default]["name"], screens[default]["w"], screens[default]["h"]) == (
        "Hall frame, comics cell",
        1440,
        1280,
    )
    # Laid out at another size: scaled to the device's panel.
    from app.state.page_store import Panel

    small = _grid_page("small", ["frame"], ("comics_daily", 720, 640))
    small = small.model_copy(update={"panel": Panel(w=720, h=1280)})
    screens, default = srv.preview_screens([small], [frame])
    assert (screens[default]["w"], screens[default]["h"]) == (1440, 1280)


def test_a_canvas_comics_element_counts_too() -> None:
    from types import SimpleNamespace

    frame = _device("frame", "Hall frame", 1440, 2560)
    element = SimpleNamespace(kind="widget", widget="comics_daily", w=1440, h=2540)
    page = SimpleNamespace(
        name="Comics",
        layout_kind="canvas",
        canvas=SimpleNamespace(w=1440, h=2560, els=[element]),
        device_ids=["frame"],
    )
    screens, default = srv.preview_screens([page], [frame])
    assert (screens[default]["name"], screens[default]["w"]) == ("Hall frame", 1440)
    assert screens[default]["h"] == 2560  # within a few pixels: the whole screen


def test_without_a_screen_showing_comics_the_preview_opens_on_the_first_reference() -> None:
    kitchen = _device("kitchen", "Kitchen", 800, 480)
    screens, default = srv.preview_screens([], [kitchen])
    assert [s["name"] for s in screens] == ["Kitchen", *REFERENCE_NAMES]
    assert screens[default]["name"] == REFERENCE_NAMES[0]
    screens, default = srv.preview_screens([], [])
    assert [s["name"] for s in screens] == REFERENCE_NAMES and default == 0


def _app_with_frame(tmp_path: Path) -> Any:
    from app.main import REPO_ROOT, create_app

    (tmp_path / "devices").mkdir(parents=True, exist_ok=True)
    (tmp_path / "devices" / "frame.json").write_text(
        json.dumps(
            {
                "id": "frame",
                "kind": "esp32_client",
                "name": "Hall frame",
                "panel": {"w": 1440, "h": 2560, "orientation": "portrait"},
            }
        )
    )
    return create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")


def test_the_admin_page_picks_the_users_frame_in_the_screen_dropdown(tmp_path: Path) -> None:
    app = _app_with_frame(tmp_path)
    client = app.test_client()
    app.config["PAGE_STORE"].save(_grid_page("comics", ["frame"], ("comics_daily", 1440, 2560)))
    body = client.get("/plugins/comics_daily/").get_data(as_text=True)
    picker = body[body.index('id="cx-screen-pick"') : body.index("</select>")]
    options = re.findall(r'<option value="(\d+)"( selected)?>([^<]*)</option>', picker)
    assert [(label, bool(on)) for _, on, label in options] == [
        ("Hall frame (1440×2560)", True),
        *[(f"{n} ({w}×{h})", False) for n, w, h in screen.SCREENS],
    ]
    assert '<optgroup label="Your screens">' in picker
    assert '<optgroup label="Reference screens">' in picker
    # Every screen is drawn; only the picked one shows.
    figures = re.findall(r'<figure class="cx-screen" data-screen="(\d+)"( hidden)?>', body)
    assert figures == [("0", ""), ("1", " hidden"), ("2", " hidden"), ("3", " hidden")]
    # The preview no longer sits in the list of comics.
    assert "On other screens" not in body


def test_the_layout_card_names_the_space_between_comics(client: FlaskClient) -> None:
    body = client.get("/plugins/comics_daily/").get_data(as_text=True)
    layout = body[body.index('id="layout"') : body.index('id="log"')]
    assert "On small screens" in layout
    assert re.search(r"<legend[^>]*>Space between comics", layout)
    labels = re.findall(r'<label for="cx-(gap(?:_min|_max)?)">(\w+)', layout)
    assert labels == [("gap_min", "Smallest"), ("gap", "Usual"), ("gap_max", "Largest")]
    assert "Out-of-date note" in layout
    for old in ("When they don", "Smallest gap", "Usual gap", "Largest gap", "Status note"):
        assert old not in layout
