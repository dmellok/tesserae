"""Warm-ahead: a page's widgets run before a scheduled send, and the lead
learns from how long the page took to settle."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app import warm_ahead as wa
from app.warm_ahead import WarmAhead, page_warm_ahead_s


class _Clock:
    def __init__(self, at: float = 1000.0) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at

    def sleep(self, s: float) -> None:
        self.at += s


def _registry(**plugins: int) -> SimpleNamespace:
    table = {
        pid: SimpleNamespace(manifest={"render": {"warm_ahead_s": s}} if s else {})
        for pid, s in plugins.items()
    }
    return SimpleNamespace(get=table.get)


def test_page_warm_ahead_is_the_longest_ask_on_the_page() -> None:
    reg = _registry(overhead_plate=150, clock=0, specimen_sheet=180)
    grid = SimpleNamespace(
        cells=[SimpleNamespace(plugin="clock"), SimpleNamespace(plugin="overhead_plate")],
        canvas=None,
    )
    assert page_warm_ahead_s(grid, reg) == 150
    canvas = SimpleNamespace(
        cells=[],
        canvas=SimpleNamespace(
            els=[
                SimpleNamespace(widget="clock", sources=[SimpleNamespace(key="specimen_sheet")]),
            ]
        ),
    )
    assert page_warm_ahead_s(canvas, reg) == 180, "a code element's data source counts too"
    assert (
        page_warm_ahead_s(
            SimpleNamespace(cells=[SimpleNamespace(plugin="clock")], canvas=None), reg
        )
        == 0
    )
    assert page_warm_ahead_s(grid, None) == 0
    assert page_warm_ahead_s(grid, _registry(overhead_plate=5)) == wa.LEAD_MIN_S, (
        "clamped to the window"
    )


def test_a_warm_polls_until_the_page_settles_and_learns_the_time() -> None:
    clock = _Clock()
    hints = iter([(20, 3), (20, 1), (0, 0), (0, 0)])
    warmed: list[str] = []

    def warm(page_id: str) -> tuple[int, int]:
        warmed.append(page_id)
        clock.at += 5  # a compose takes a moment
        return next(hints)

    w = WarmAhead(warm=warm, clock=clock, sleep=clock.sleep, threaded=False)
    assert w.lead_s("home", 150) == 150, "the manifest's number until something is learned"
    assert not w.consider("home", clock.at + 600, 150, clock.at), "the fire is beyond the lead"
    assert w.consider("home", clock.at + 120, 150, clock.at)
    assert warmed == ["home", "home", "home"], "polled until the hint dropped to 0"
    settled = w.settle_s("home")
    assert settled is not None and 50 <= settled <= 60, "three composes and two 20 s waits"
    seen = w.learned("home")
    assert seen and seen["items"] == 3 and seen["base_s"] == 10, (
        "three paintings pending at the start"
    )
    assert abs(seen["per_item_s"] - (settled - 10) / 3) < 0.01, (
        "the time beyond the base is split among them"
    )
    expected = seen["base_s"] + seen["items"] * seen["per_item_s"]
    assert w.lead_s("home", 150) == int(expected * wa.MARGIN + wa.MARGIN_S), (
        "the next lead follows what was seen"
    )
    fire = clock.at + 60
    assert w.consider("home", fire, 150, clock.at) and not w.consider(
        "home", fire, 150, clock.at + 1
    ), "a fire already warmed is not warmed twice"


def test_a_warm_that_runs_out_of_lead_asks_for_more_next_time() -> None:
    clock = _Clock()
    w = WarmAhead(warm=lambda _p: (30, 0), clock=clock, sleep=clock.sleep, threaded=False)
    fire = clock.at + 40
    assert w.consider("slow", fire, 60, clock.at)
    assert clock.at <= fire, "the warm gave up before the fire, not after"
    assert w.lead_s("slow", 60) == min(wa.LEAD_MAX_S, int(40 * 1.5 * wa.MARGIN + wa.MARGIN_S))
    assert w.status()["slow"]["lead_s"] == w.lead_s("slow", 60)


def test_a_failing_warm_does_not_raise_and_still_records() -> None:
    clock = _Clock()

    def boom(_p: str) -> tuple[int, int]:
        raise RuntimeError("compose down")

    w = WarmAhead(warm=boom, clock=clock, sleep=clock.sleep, threaded=False)
    assert w.consider("x", clock.at + 50, 60, clock.at)
    assert w.settle_s("x") is not None


def test_warm_page_reads_the_hint_back_from_the_composed_page(tmp_path) -> None:
    from app.push import PushManager

    page = SimpleNamespace(id="home", device_ids=[], canvas=None, cells=[])
    manager = PushManager.__new__(PushManager)
    manager._page_store = SimpleNamespace(get=lambda pid: page if pid == "home" else None)
    manager._devices = None
    manager._settings = None
    manager._base_url_fn = lambda: "http://127.0.0.1:5000"
    manager._automation_eligible_fn = None
    panel = SimpleNamespace(w=800, h=480)

    class _Resp:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, _n: int = -1) -> bytes:
            return self._body

    html = b'<html><head><meta name="tesserae-refresh-s" content="12"><meta name="tesserae-pending" content="4"></head></html>'
    with (
        patch("app.push.panel_groups_for_push", return_value=[(panel, ["dev"])]),
        patch("app.push.urllib.request.urlopen", return_value=_Resp(html)) as opened,
    ):
        assert manager.warm_page("home") == (12, 4)
        url = opened.call_args.args[0].full_url
        assert "/compose/home?for_push=1&w=800&h=480" in url
    with (
        patch("app.push.panel_groups_for_push", return_value=[(panel, ["dev"])]),
        patch("app.push.urllib.request.urlopen", return_value=_Resp(b"<html></html>")),
    ):
        assert manager.warm_page("home") == (0, 0), "no hint, nothing waiting"
    assert manager.warm_page("missing") == (0, 0)


def test_items_learn_seconds_per_item_across_warms() -> None:
    clock = _Clock()
    plan = {"n": 0.0, "t0": 0.0}

    def warm(_p: str) -> tuple[int, int]:
        # a page with n items pending settles 10 s + 15 s per item after the warm began
        clock.at += 1
        done = clock.at - plan["t0"] >= 10 + 15 * plan["n"]
        return (0, 0) if done else (10, int(plan["n"]))

    w = WarmAhead(warm=warm, clock=clock, sleep=clock.sleep, threaded=False)
    for n in (2, 2, 6, 6, 6):
        plan["n"], plan["t0"] = float(n), clock.at
        assert w.consider("p", clock.at + w.lead_s("p", 600), 600, clock.at), (
            "the fire sits at the current lead"
        )
        clock.at += 1000
    seen = w.learned("p")
    assert seen and 10 <= seen["per_item_s"] <= 22 and 4 <= seen["items"] <= 6, seen
    assert 100 <= w.lead_s("p", 600) <= 200, "six items at about fifteen seconds, with a margin"


def test_ineligible_warm_is_not_started_and_can_resume():
    clock = _Clock()
    renders = []
    eligible = False
    w = WarmAhead(
        warm=lambda pid: (renders.append(pid) or 0, 0),
        clock=clock,
        sleep=clock.sleep,
        threaded=False,
        eligible=lambda _pid: eligible,
    )
    assert w.consider("home", clock.at + 100, 150, clock.at) is False
    assert len(renders) == 0
    eligible = True
    assert w.consider("home", clock.at + 100, 150, clock.at) is True
    assert len(renders) == 1


def test_warm_stops_polling_when_target_becomes_ineligible():
    clock = _Clock()
    renders = []
    eligible = True

    def sleep(seconds):
        nonlocal eligible
        clock.sleep(seconds)
        eligible = False

    w = WarmAhead(
        warm=lambda pid: (renders.append(pid) or 10, 1), clock=clock, sleep=sleep, threaded=False
    )
    w._eligible = lambda _pid: eligible
    fire_at = clock.at + 100
    assert w.consider("home", fire_at, 150, clock.at) is True
    assert len(renders) == 1
    eligible = True
    assert w.consider("home", fire_at, 150, clock.at) is True
    assert len(renders) == 2
