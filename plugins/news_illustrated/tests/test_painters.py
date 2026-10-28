"""The image providers and their cascade: request shapes, kie.ai polling,
validation of what comes back, order, keys and the time budget."""

from __future__ import annotations

import base64
import json
import urllib.parse

import pytest

from plugins.news_illustrated import net, painters
from plugins.news_illustrated.tests.fakes import Call, FakeNet, ok, ok_json, picture

KEYS = {"kie": "kie-key", "gemini": "gem-key", "pollinations": "pol-key", "huggingface": "hf-key"}
RESULT_URL = "https://tempfile.example.com/result.png"


class Clock:
    """A clock that only moves when the code under test sleeps (or a test says so)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _paint(
    settings: painters.Settings, clock: Clock | None = None
) -> tuple[painters.Painting | None, list[str]]:
    clock = clock or Clock()
    log: list[str] = []
    budget = painters.Budget(clock=clock, sleep=clock.sleep)
    return painters.paint(
        "Tram line approved", "The council said yes.", settings, budget=budget, log=log.append
    ), log


def _settings(*order: str, keys: dict[str, str] | None = None) -> painters.Settings:
    return painters.Settings(
        keys=KEYS if keys is None else keys,
        order=order or painters.PROVIDERS,
        image_prompt="Comic about {title}: {summary}",
    )


def _kie_record(state: str, **extra: object) -> net.Reply:
    return ok_json({"code": 200, "data": {"taskId": "t1", "state": state, **extra}})


def _kie_success() -> net.Reply:
    return _kie_record("success", resultJson=json.dumps({"resultUrls": [RESULT_URL]}))


def test_kie_creates_polls_with_auth_and_downloads(fake_net: FakeNet) -> None:
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"code": 200, "data": {"taskId": "t1"}}))
    fake_net.on(
        painters.KIE_RECORD_URL, _kie_record("waiting"), _kie_record("generating"), _kie_success()
    )
    fake_net.on(RESULT_URL, ok(picture(), "image/png"))
    clock = Clock()
    got, log = _paint(_settings("kie"), clock)
    assert got is not None and got.provider == "kie" and got.size == (768, 1376)
    assert got.jpeg[:3] == b"\xff\xd8\xff"
    (create,) = fake_net.to(painters.KIE_CREATE_URL)
    assert create.method == "POST"
    assert create.headers["Authorization"] == "Bearer kie-key"
    assert create.json() == {
        "model": "google/nano-banana",
        "input": {
            "prompt": "Comic about Tram line approved: The council said yes.",
            "image_size": "9:16",
        },
    }
    polls = fake_net.to(painters.KIE_RECORD_URL)
    assert len(polls) == 3
    assert all(p.headers["Authorization"] == "Bearer kie-key" for p in polls)
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(polls[0].url).query) == {"taskId": ["t1"]}
    assert clock.now == 1000.0 + 3 * painters.KIE_POLL_EVERY_S
    assert "kie: 768x1376 picture" in log


def test_kie_result_json_may_already_be_an_object(fake_net: FakeNet) -> None:
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    fake_net.on(
        painters.KIE_RECORD_URL, _kie_record("success", resultJson={"resultUrls": [RESULT_URL]})
    )
    fake_net.on(RESULT_URL, ok(picture(), "image/jpeg"))
    assert _paint(_settings("kie"))[0] is not None


def test_kie_failed_task_falls_through_to_the_next_provider(fake_net: FakeNet) -> None:
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    fake_net.on(painters.KIE_RECORD_URL, _kie_record("fail", failMsg="content policy"))
    fake_net.on(painters.HUGGINGFACE_URL, ok(picture(), "image/jpeg"))
    got, log = _paint(_settings("kie", "huggingface"))
    assert got is not None and got.provider == "huggingface"
    assert "kie: generation failed (content policy)" in log


def test_kie_without_task_id_reports_the_api_message(fake_net: FakeNet) -> None:
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"code": 401, "msg": "You are not authorized"}))
    got, log = _paint(_settings("kie"))
    assert got is None
    assert "kie: task not created (You are not authorized)" in log
    assert fake_net.to(painters.KIE_RECORD_URL) == []


def test_kie_polling_stops_inside_the_budget(fake_net: FakeNet) -> None:
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    fake_net.on(painters.KIE_RECORD_URL, _kie_record("queuing"))
    clock = Clock()
    got, log = _paint(_settings("kie"), clock)
    assert got is None
    assert "kie: still painting when the time ran out; the next run collects it" in log
    # Polls stop at 120 s, which also keeps 15 s of the 150 s budget for a download.
    assert clock.now - 1000.0 <= painters.KIE_POLL_MAX_S + painters.KIE_POLL_EVERY_S
    assert len(fake_net.to(painters.KIE_RECORD_URL)) == int(
        painters.KIE_POLL_MAX_S / painters.KIE_POLL_EVERY_S
    )


def test_gemini_request_and_inline_picture(fake_net: FakeNet) -> None:
    raw = picture(1536, 2752, "PNG")
    answer = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Here"},
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64.b64encode(raw).decode(),
                            }
                        },
                    ]
                }
            }
        ]
    }
    fake_net.on("https://generativelanguage.googleapis.com/", ok_json(answer))
    got, _log = _paint(_settings("gemini"))
    assert got is not None and got.provider == "gemini" and got.size[1] == painters.MAX_EDGE_PX
    (call,) = fake_net.calls
    assert call.url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    )
    assert "gem-key" not in call.url
    assert call.headers["x-goog-api-key"] == "gem-key"
    body = call.json()
    assert body["generationConfig"] == {"imageConfig": {"aspectRatio": "9:16", "imageSize": "2K"}}
    assert body["contents"][0]["parts"][0]["text"].startswith("Comic about Tram line approved")


def test_gemini_error_and_missing_picture(fake_net: FakeNet) -> None:
    fake_net.on(
        "https://generativelanguage.googleapis.com/",
        ok_json({"error": {"code": 429, "message": "Quota exceeded"}}, 429),
        ok_json({"candidates": [{"content": {"parts": [{"text": "no image"}]}}]}),
    )
    assert _paint(_settings("gemini"))[1][-1] == "gemini: Quota exceeded"
    assert _paint(_settings("gemini"))[1][-1] == "gemini: no picture in the answer"


def test_pollinations_url_uses_the_short_prompt(fake_net: FakeNet) -> None:
    fake_net.on(painters.POLLINATIONS_URL, ok(picture(), "image/jpeg"))
    log: list[str] = []
    clock = Clock()
    got = painters.paint(
        "A headline",
        "word " * 200,
        _settings("pollinations"),
        budget=painters.Budget(clock=clock, sleep=clock.sleep),
        log=log.append,
    )
    assert got is not None and got.provider == "pollinations"
    (call,) = fake_net.calls
    parts = urllib.parse.urlsplit(call.url)
    prompt = urllib.parse.unquote(parts.path.removeprefix("/image/"))
    assert len(prompt) <= painters.SHORT_PROMPT_CHARS and prompt.endswith("...")
    assert prompt.startswith("Single-panel Far Side-style black and white ink gag cartoon")
    assert urllib.parse.parse_qs(parts.query) == {
        "width": ["784"],
        "height": ["1408"],
        "model": ["flux"],
        "nologo": ["true"],
        "enhance": ["false"],
        "key": ["pol-key"],
    }


def test_huggingface_router_request(fake_net: FakeNet) -> None:
    fake_net.on(painters.HUGGINGFACE_URL, ok(picture(), "image/jpeg"))
    got, _log = _paint(_settings("huggingface"))
    assert got is not None
    (call,) = fake_net.calls
    assert (
        call.url
        == "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
    )
    assert call.headers["Authorization"] == "Bearer hf-key"
    body = call.json()
    assert body["parameters"] == {"width": 784, "height": 1408}
    assert body["inputs"].startswith("Single-panel Far Side-style")


def test_responses_that_are_not_pictures_are_rejected(fake_net: FakeNet) -> None:
    fake_net.on(painters.POLLINATIONS_URL, ok("<html>rate limited</html>"))
    fake_net.on(painters.HUGGINGFACE_URL, ok(picture(100, 100), "image/jpeg"))
    got, log = _paint(_settings("pollinations", "huggingface"))
    assert got is None
    assert any(line.startswith("pollinations: response is not a picture") for line in log)
    assert "huggingface: picture too small (100x100)" in log
    with pytest.raises(painters.PaintError):
        painters.to_jpeg(picture()[:200])  # truncated download


def test_cascade_follows_the_order_and_stops_at_the_first_picture(fake_net: FakeNet) -> None:
    fake_net.on(painters.HUGGINGFACE_URL, net.Reply(503, b'{"error": "loading"}'))
    fake_net.on(painters.POLLINATIONS_URL, ok(picture(), "image/jpeg"))
    got, log = _paint(_settings("huggingface", "pollinations", "kie"))
    assert got is not None and got.provider == "pollinations"
    assert len(fake_net.calls) == 2
    assert fake_net.calls[0].url.startswith(painters.HUGGINGFACE_URL)
    assert fake_net.calls[1].url.startswith(painters.POLLINATIONS_URL)
    assert fake_net.to(painters.KIE_CREATE_URL) == []
    assert log[:2] == ["Painting with huggingface", "huggingface: loading"]


def test_providers_without_a_key_are_skipped(fake_net: FakeNet) -> None:
    fake_net.on(painters.HUGGINGFACE_URL, ok(picture(), "image/jpeg"))
    got, log = _paint(_settings(keys={"huggingface": "hf-key"}))
    assert got is not None and got.provider == "huggingface"
    assert len(fake_net.calls) == 1
    assert log[0] == "Painting with huggingface"


def test_no_provider_is_started_with_too_little_budget_left(fake_net: FakeNet) -> None:
    clock = Clock()

    def slow_failure(call: Call) -> net.Reply:
        del call
        clock.sleep(painters.BUDGET_S - painters.MIN_PROVIDER_S + 1)
        return net.Reply(0, error="timed out")

    fake_net.on(painters.HUGGINGFACE_URL, slow_failure)
    fake_net.on(painters.POLLINATIONS_URL, ok(picture(), "image/jpeg"))
    got, log = _paint(_settings("huggingface", "pollinations"), clock)
    assert got is None
    assert fake_net.to(painters.POLLINATIONS_URL) == []
    assert log[-1] == "Image time budget used up (19s left), skipping the other providers"


def test_request_timeouts_shrink_with_the_budget(fake_net: FakeNet) -> None:
    clock = Clock()
    budget = painters.Budget(clock=clock, sleep=clock.sleep)
    assert budget.timeout(90) == 90
    clock.sleep(140)
    assert budget.timeout(90) == 10
    clock.sleep(10)
    with pytest.raises(painters.PaintError, match="budget used up"):
        budget.timeout(90)  # no request is started once the time is gone


def test_prompts() -> None:
    detailed, short = painters.prompts("Title", "Summary", "")
    assert detailed.startswith("A single-panel gag cartoon in the style of Gary Larson's")
    assert "Headline: Title\nStory: Summary\n" in detailed
    assert "Tall portrait (9:16) picture that fills the whole canvas" in detailed
    assert "{" not in detailed
    assert short == (
        "Single-panel Far Side-style black and white ink gag cartoon, one visual joke, no text, "
        "crosshatching, bold outlines, plain ground at the bottom. Title. Summary"
    )


def test_a_custom_prompt_gets_the_shape() -> None:
    detailed, _ = painters.prompts("T", "S", "{title} / {summary} / {shape}", "16:9")
    assert detailed == "T / S / Wide landscape (16:9)"


@pytest.mark.parametrize(
    ("w", "h", "aspect"),
    [
        (1440, 2560, "9:16"),
        (1600, 1200, "4:3"),
        (800, 480, "16:9"),
        (1200, 1200, "1:1"),
        (1872, 1404, "4:3"),
        (2560, 1080, "21:9"),
        (0, 100, "9:16"),
    ],
)
def test_nearest_aspect(w: int, h: int, aspect: str) -> None:
    assert painters.nearest_aspect(w, h) == aspect


def test_flux_sizes_follow_the_shape() -> None:
    for aspect in painters.ASPECTS:
        width, height = painters.flux_size(aspect)
        assert width % 16 == 0 and height % 16 == 0
        assert abs(width * height - painters.FLUX_PIXELS) / painters.FLUX_PIXELS < 0.05
        want = painters._ratio(aspect)
        assert abs(width / height - want) / want < 0.03


def test_the_shape_reaches_every_provider(fake_net: FakeNet) -> None:
    import dataclasses

    fake_net.on(painters.KIE_CREATE_URL, ok_json({"code": 401, "msg": "no"}, 401))
    fake_net.on(painters.GEMINI_URL.split("{")[0], ok_json({"error": {"message": "no"}}, 400))
    fake_net.on(painters.POLLINATIONS_URL, ok("<html>no</html>"))
    fake_net.on(painters.HUGGINGFACE_URL, ok(picture(1024, 768), "image/jpeg"))
    settings = dataclasses.replace(_settings(*painters.PROVIDERS), aspect="4:3", image_prompt="")
    got, _log = _paint(settings)
    assert got is not None and got.provider == "huggingface" and got.size == (1024, 768)
    kie = fake_net.to(painters.KIE_CREATE_URL)[0].json()
    assert kie["input"]["image_size"] == "4:3"
    gemini = fake_net.to(painters.GEMINI_URL.split("{")[0])[0].json()
    assert gemini["generationConfig"]["imageConfig"]["aspectRatio"] == "4:3"
    assert "Landscape (4:3)" in gemini["contents"][0]["parts"][0]["text"]
    pol = urllib.parse.parse_qs(
        urllib.parse.urlsplit(fake_net.to(painters.POLLINATIONS_URL)[0].url).query
    )
    assert (pol["width"], pol["height"]) == (
        [str(painters.flux_size("4:3")[0])],
        [str(painters.flux_size("4:3")[1])],
    )
    hf = fake_net.to(painters.HUGGINGFACE_URL)[0].json()
    assert (hf["parameters"]["width"], hf["parameters"]["height"]) == painters.flux_size("4:3")


def test_gemini_reports_its_tokens(fake_net: FakeNet) -> None:
    data = base64.b64encode(picture()).decode()
    fake_net.on(
        painters.GEMINI_URL.split("{")[0],
        ok_json(
            {
                "candidates": [{"content": {"parts": [{"inlineData": {"data": data}}]}}],
                "usageMetadata": {
                    "promptTokenCount": 300,
                    "candidatesTokenCount": 1290,
                    "totalTokenCount": 1590,
                },
            }
        ),
    )
    got, log = _paint(_settings("gemini"))
    assert got is not None and got.usage == {"tokens": 1590}
    assert any(line.endswith("picture, 1,590 tokens") for line in log)


def test_kie_balance(fake_net: FakeNet) -> None:
    fake_net.on(painters.KIE_CREDIT_URL, ok_json({"code": 200, "msg": "success", "data": 1180.5}))
    assert painters.kie_balance("kie-key") == 1180.5
    assert fake_net.calls[-1].headers["Authorization"] == "Bearer kie-key"
    assert painters.kie_balance("") is None
    fake_net.routes.clear()
    fake_net.on(painters.KIE_CREDIT_URL, ok_json({"code": 401, "msg": "bad key"}, 401))
    assert painters.kie_balance("kie-key") is None
    fake_net.routes.clear()
    fake_net.on(painters.KIE_CREDIT_URL, ok_json({"data": "lots"}))
    assert painters.kie_balance("kie-key") is None


def _kie_paint(
    memo: painters.TaskMemo, clock: Clock | None = None
) -> tuple[painters.Painting | None, list[str]]:
    clock = clock or Clock()
    log: list[str] = []
    got = painters.paint(
        "T",
        "S",
        _settings("kie"),
        budget=painters.Budget(clock=clock, sleep=clock.sleep),
        log=log.append,
        memo=memo,
    )
    return got, log


def test_kie_task_id_is_saved_before_polling_and_collected_later(fake_net: FakeNet) -> None:
    saved: list[object] = []
    memo = painters.TaskMemo(save=saved.append, clock=lambda: 5000.0)
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t9"}}))
    fake_net.on(painters.KIE_RECORD_URL, _kie_record("generating"))
    got, _log = _kie_paint(memo)
    assert got is None
    assert saved == [
        {"unanswered": True, "at": 5000.0},  # written before the request goes out
        {"task_id": "t9", "at": 5000.0},  # kept for the next run
    ]
    # The next run (a restart, say) collects the same task: no second create, no second charge.
    fake_net.calls.clear()
    fake_net.routes.clear()
    fake_net.on(painters.KIE_RECORD_URL, _kie_success())
    fake_net.on(RESULT_URL, ok(picture(), "image/png"))
    later = painters.TaskMemo(memo.record, save=saved.append, clock=lambda: 5000.0 + 3600)
    got, log = _kie_paint(later)
    assert got is not None and got.provider == "kie"
    assert fake_net.to(painters.KIE_CREATE_URL) == []
    assert "taskId=t9" in fake_net.to(painters.KIE_RECORD_URL)[0].url
    assert "kie: collecting the task an earlier run paid for" in log
    # Still remembered: only saving the story that holds the picture settles it.
    assert later.record == {"task_id": "t9", "at": 5000.0}


def test_an_old_kie_task_is_not_collected(fake_net: FakeNet) -> None:
    memo = painters.TaskMemo(
        {"task_id": "old", "at": 0.0}, clock=lambda: painters.KIE_RESUME_S + 1.0
    )
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "new"}}))
    fake_net.on(painters.KIE_RECORD_URL, _kie_success())
    fake_net.on(RESULT_URL, ok(picture(), "image/png"))
    assert _kie_paint(memo)[0] is not None
    assert len(fake_net.to(painters.KIE_CREATE_URL)) == 1


def test_an_unanswered_kie_create_is_not_resent_at_once(fake_net: FakeNet) -> None:
    memo = painters.TaskMemo(clock=lambda: 100.0)
    fake_net.on(painters.KIE_CREATE_URL, net.Reply(0, error="timed out"))
    got, log = _kie_paint(memo)
    assert got is None and memo.record == {"unanswered": True, "at": 100.0}
    assert "kie: task request unanswered (timed out); not resending yet" in log
    got, log = _kie_paint(painters.TaskMemo(memo.record, clock=lambda: 100.0 + 600))
    assert got is None and len(fake_net.to(painters.KIE_CREATE_URL)) == 1
    assert "may have been accepted" in log[-1]
    # A refusal is definite: nothing is remembered.
    refused = painters.TaskMemo()
    fake_net.routes.clear()
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"code": 402, "msg": "Insufficient credits"}))
    _kie_paint(refused)
    assert refused.record is None


def test_an_answer_after_the_budget_does_not_count(fake_net: FakeNet) -> None:
    clock = Clock()

    def late_success(call: Call) -> net.Reply:
        del call
        clock.now = 1000.0 + painters.BUDGET_S + 15  # the answer lands at 165 s
        return _kie_success()

    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    fake_net.on(painters.KIE_RECORD_URL, late_success)
    fake_net.on(RESULT_URL, ok(picture(), "image/png"))
    got, log = _kie_paint(painters.TaskMemo(), clock)
    assert got is None
    assert fake_net.to(RESULT_URL) == []
    assert log[-1] == "kie: image time budget used up"


def test_polling_never_sleeps_past_the_budget(fake_net: FakeNet) -> None:
    clock = Clock()
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    fake_net.on(painters.KIE_RECORD_URL, _kie_record("generating"))
    budget = painters.Budget(seconds=60, clock=clock, sleep=clock.sleep)
    painters.paint("T", "S", _settings("kie"), budget=budget, log=[].append)
    assert clock.now <= budget.deadline - painters.KIE_DOWNLOAD_RESERVE_S


def test_dying_during_the_create_still_blocks_a_second_create(fake_net: FakeNet) -> None:
    saved: list[object] = []

    def crash(call: Call) -> net.Reply:
        del call
        assert saved == [{"unanswered": True, "at": 100.0}], "marked before sending"
        raise SystemExit("the server died mid-request")

    fake_net.on(painters.KIE_CREATE_URL, crash)
    memo = painters.TaskMemo(save=saved.append, clock=lambda: 100.0)
    with pytest.raises(SystemExit):
        _kie_paint(memo)
    # An immediate retry (a manual Generate now) does not ask kie.ai again.
    got, log = _kie_paint(painters.TaskMemo(saved[-1], clock=lambda: 160.0))  # type: ignore[arg-type]
    assert got is None and len(fake_net.to(painters.KIE_CREATE_URL)) == 1
    assert "may have been accepted" in log[-1]


def test_a_kie_result_that_is_not_a_picture_is_not_collected_again(fake_net: FakeNet) -> None:
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    fake_net.on(painters.KIE_RECORD_URL, _kie_success())
    fake_net.on(RESULT_URL, ok("<html>expired</html>"))
    memo = painters.TaskMemo()
    assert _kie_paint(memo)[0] is None
    assert memo.record is None
