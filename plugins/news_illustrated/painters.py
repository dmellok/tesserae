"""The illustration: a cascade of image providers, first success wins.

kie.ai, Gemini, Pollinations and HuggingFace in the configured order, each
skipped when it has no key. One story's illustration has a time budget; a
provider is not started with too little of it left, since a half-finished
call is paid for and thrown away. Whatever comes back must decode as a real
picture of a sensible size before it counts: providers answer errors with
HTML or JSON and a 200 often enough.

The provider quirks below were learned on the PHP original and are kept on
purpose: kie.ai wants the Bearer header on the poll as well as the create
and sizes with ``input.image_size`` (``aspect_ratio`` is silently ignored);
Gemini needs ``imageSize`` next to ``aspectRatio`` or it sometimes paints a
square; Pollinations needs a short prompt to keep its GET URL under
Cloudflare's limit; HuggingFace serves from ``router.huggingface.co`` only.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import net, text

PROVIDERS = ("kie", "gemini", "pollinations", "huggingface")
BUDGET_S = 150.0
MIN_PROVIDER_S = 20.0
SHORT_PROMPT_CHARS = 400
MIN_EDGE_PX = 256
# The panel this was made for is 1440x2560; nothing gains from a bigger file.
MAX_EDGE_PX = 2560
JPEG_QUALITY = 85

KIE_CREATE_URL = "https://api.kie.ai/api/v1/jobs/createTask"
KIE_RECORD_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"
KIE_POLL_EVERY_S = 5.0
KIE_POLL_MAX_S = 120.0
# Kept back from the budget for downloading the finished picture.
KIE_DOWNLOAD_RESERVE_S = 15.0
# A task an earlier run paid for is collected if it is younger than this...
KIE_RESUME_S = 6 * 3600
# ...and after a create request that got no answer, kie.ai is not asked again
# for that story for this long (the day's retry comes two hours later).
KIE_UNANSWERED_S = 2 * 3600
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
POLLINATIONS_URL = "https://gen.pollinations.ai/image/"
HUGGINGFACE_URL = "https://router.huggingface.co/hf-inference/models/"
KIE_CREDIT_URL = "https://api.kie.ai/api/v1/chat/credit"
# The picture shapes every provider here can draw (kie.ai's image_size and
# Gemini's aspectRatio both take these), as width:height.
ASPECTS = ("9:16", "2:3", "3:4", "4:5", "1:1", "5:4", "4:3", "3:2", "16:9", "21:9")
DEFAULT_ASPECT = "9:16"
# The FLUX providers take explicit sizes: about this many pixels, both edges
# a multiple of 16 (768x1440 before shapes followed the frame).
FLUX_PIXELS = 768 * 1440
# What a shape is called in a prompt ({shape}).
SHAPE_WORDS = {
    "9:16": "Tall portrait (9:16)",
    "2:3": "Portrait (2:3)",
    "3:4": "Portrait (3:4)",
    "4:5": "Portrait (4:5)",
    "1:1": "Square (1:1)",
    "5:4": "Landscape (5:4)",
    "4:3": "Landscape (4:3)",
    "3:2": "Landscape (3:2)",
    "16:9": "Wide landscape (16:9)",
    "21:9": "Very wide landscape (21:9)",
}
# One joke a reader gets from the headline alone, drawn so it fills the
# picture and leaves the foot plain for the caption the frame lays over it.
# (The old prompt asked for an empty bottom 10%, and the models left a third
# of the picture blank; it fed only the summary, so they drew every fact in
# it, labels and all, instead of a joke.)
DEFAULT_IMAGE_PROMPT = (
    "A single-panel gag cartoon in the style of Gary Larson's The Far Side, about this news story.\n"
    "Headline: {title}\n"
    "Story: {summary}\n\n"
    "First decide the joke: the one absurd or ironic truth behind the headline that everyone "
    "thinks but nobody says. Show it as ONE visual gag that someone who has only read the "
    "headline gets in two seconds: take a phrase from the story literally, swap who is in "
    "charge, or show where this ends up if nobody stops it. Ordinary people, animals or "
    "objects in an everyday place work best, rather than likenesses of the people named. "
    "Leave out the rest of the story's details.\n\n"
    "Rules: one scene, no panels, borders or split screens. Two or three characters at most, "
    "each with the right number of arms, hands and legs. At most one short line of text in "
    "the whole picture (one speech bubble or one sign, under eight words, spelled correctly); "
    "no labels, logos or captions.\n"
    "Style: black and white ink, bold clean outlines, crosshatched shading, plain white "
    "background, high contrast. {shape} picture that fills the whole canvas edge to edge. "
    "Keep the bottom quarter simple, just floor, ground or sky with no faces or text, "
    "because the headline is printed over it."
)


def nearest_aspect(width: float, height: float) -> str:
    """The drawable shape closest to ``width`` x ``height`` (by ratio)."""
    import math

    if width <= 0 or height <= 0:
        return DEFAULT_ASPECT
    want = math.log(width / height)
    return min(ASPECTS, key=lambda a: abs(math.log(_ratio(a)) - want))


def _ratio(aspect: str) -> float:
    w, _, h = aspect.partition(":")
    try:
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return 9 / 16


def flux_size(aspect: str) -> tuple[int, int]:
    """A FLUX picture size for ``aspect``: about FLUX_PIXELS, edges a multiple of 16."""
    ratio = _ratio(aspect)
    width = round((FLUX_PIXELS * ratio) ** 0.5 / 16) * 16
    height = round((FLUX_PIXELS / ratio) ** 0.5 / 16) * 16
    return max(256, width), max(256, height)


class PaintError(Exception):
    """A provider produced no usable picture; the message goes in the run log."""


@dataclass
class Budget:
    """The seconds one illustration may take, on a clock tests can drive."""

    seconds: float = BUDGET_S
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    deadline: float = field(init=False)

    def __post_init__(self) -> None:
        self.deadline = self.clock() + self.seconds

    def left(self) -> float:
        return self.deadline - self.clock()

    def timeout(self, cap: float) -> float:
        """A request's total time: what is left of the budget, at most ``cap``.
        Raises PaintError once nothing is left."""
        self.check()
        return min(cap, self.left())

    def check(self) -> None:
        """Raise PaintError when the budget is spent: an answer that arrives
        after it does not count."""
        if self.left() <= 0:
            raise PaintError("image time budget used up")


@dataclass
class TaskMemo:
    """A paid kie.ai task that has to outlive this process: its id once kie
    accepted it, or the time of a request whose answer never came (kie may
    have accepted it). Saved through ``save`` as soon as it changes, so a
    restart mid-poll collects the same task instead of paying for another."""

    record: dict[str, Any] | None = None
    save: Callable[[dict[str, Any] | None], None] = lambda record: None
    clock: Callable[[], float] = time.time

    def remember(self, record: dict[str, Any] | None) -> None:
        self.record = record
        self.save(record)


@dataclass(frozen=True)
class Attempt:
    """One provider call's inputs."""

    settings: Settings
    key: str
    detailed: str
    short: str
    budget: Budget
    stopped: Callable[[], bool]
    memo: TaskMemo
    log: Callable[[str], None]
    # Filled in by a provider that reports what the call used.
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    """What the cascade needs from the widget's settings."""

    keys: dict[str, str] = field(repr=False)
    order: tuple[str, ...] = PROVIDERS
    kie_model: str = "google/nano-banana"
    gemini_model: str = "gemini-2.5-flash-image"
    pollinations_model: str = "flux"
    huggingface_model: str = "black-forest-labs/FLUX.1-schnell"
    image_prompt: str = ""
    # The picture's shape, one of ASPECTS (the run picks it from the frames).
    aspect: str = DEFAULT_ASPECT


@dataclass(frozen=True)
class Painting:
    jpeg: bytes
    provider: str
    size: tuple[int, int]
    # What the provider said the picture cost, when it says: {"tokens": n, ...}.
    usage: dict[str, Any] = field(default_factory=dict)


def prompts(
    title: str, summary: str, template: str, aspect: str = DEFAULT_ASPECT
) -> tuple[str, str]:
    """(detailed prompt for instruction-following models, short keyword-style
    prompt for the FLUX diffusion models)."""
    shape = SHAPE_WORDS.get(aspect, SHAPE_WORDS[DEFAULT_ASPECT])
    detailed = (template or DEFAULT_IMAGE_PROMPT).replace("{title}", title)
    detailed = detailed.replace("{shape}", shape)
    detailed = detailed.replace("{summary}", summary).strip()
    short = (
        "Single-panel Far Side-style black and white ink gag cartoon, one visual joke, "
        f"no text, crosshatching, bold outlines, plain ground at the bottom. {title}. {summary}"
    )
    if len(short) > SHORT_PROMPT_CHARS:
        short = short[: SHORT_PROMPT_CHARS - 3].rstrip() + "..."
    return detailed, short


def paint(
    title: str,
    summary: str,
    settings: Settings,
    *,
    budget: Budget,
    log: Callable[[str], None],
    stopped: Callable[[], bool] = lambda: False,
    memo: TaskMemo | None = None,
) -> Painting | None:
    """The first provider's picture, as a JPEG; None when none produced one."""
    memo = memo or TaskMemo()
    detailed, short = prompts(title, summary, settings.image_prompt, settings.aspect)
    for name in settings.order:
        key = settings.keys.get(name, "")
        if name not in _CALLS or not key:
            continue
        if stopped():
            return None
        left = budget.left()
        if left < MIN_PROVIDER_S:
            log(f"Image time budget used up ({left:.0f}s left), skipping the other providers")
            return None
        log(f"Painting with {name}")
        attempt = Attempt(settings, key, detailed, short, budget, stopped, memo, log)
        try:
            raw = _CALLS[name](attempt)
        except PaintError as err:
            log(f"{name}: {err}")
            continue
        try:
            jpeg, size = to_jpeg(raw)
        except PaintError as err:
            log(f"{name}: {err}")
            if name == "kie":
                memo.remember(None)  # collecting that task again would give the same bytes
            continue
        log(f"{name}: {size[0]}x{size[1]} picture{usage_note(attempt.usage)}")
        return Painting(jpeg, name, size, dict(attempt.usage))
    return None


def to_jpeg(raw: bytes) -> tuple[bytes, tuple[int, int]]:
    """Check ``raw`` is a real picture and re-encode it as a JPEG no larger
    than the panel needs. Raises PaintError for anything else (an HTML error
    page, a JSON error, a truncated download, a thumbnail)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not raw:
        raise PaintError("empty response")
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened) or opened
            image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as err:
        raise PaintError(f"response is not a picture ({len(raw)} bytes)") from err
    if min(image.size) < MIN_EDGE_PX:
        raise PaintError(f"picture too small ({image.size[0]}x{image.size[1]})")
    image.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return out.getvalue(), image.size


# ----------------------------------------------------------------------
# Providers. Each returns the raw picture bytes or raises PaintError.
# ----------------------------------------------------------------------


def kie(a: Attempt) -> bytes:
    """kie.ai: create a task (or take up the one an earlier run left), poll it
    until it has a picture, download that."""
    auth = {"Authorization": f"Bearer {a.key}"}
    earlier = a.memo.record or {}
    age = a.memo.clock() - float(earlier.get("at") or 0)
    if earlier.get("task_id") and age < KIE_RESUME_S:
        task_id = str(earlier["task_id"])
        a.log("kie: collecting the task an earlier run paid for")
    elif earlier.get("unanswered") and age < KIE_UNANSWERED_S:
        raise PaintError(
            "an earlier task request went unanswered and may have been accepted; "
            "not sending another yet"
        )
    else:
        if earlier.get("task_id"):
            a.log("kie: the task an earlier run paid for is over 6 hours old; giving up on it")
        task_id = _kie_create(a, auth)
    return _kie_collect(a, task_id, auth)


def collect(
    settings: Settings,
    memo: TaskMemo,
    *,
    budget: Budget,
    log: Callable[[str], None],
    stopped: Callable[[], bool] = lambda: False,
) -> Painting | None:
    """Collect the kie.ai task in ``memo`` and nothing else: no new task is
    ever ordered here. None while it is still painting (the memo keeps it) or
    when it failed (the memo is cleared)."""
    task_id = str((memo.record or {}).get("task_id") or "")
    key = settings.keys.get("kie", "")
    if not task_id or not key:
        return None
    attempt = Attempt(settings, key, "", "", budget, stopped, memo, log)
    try:
        # A definite failure clears the memo inside; running out of time does not.
        raw = _kie_collect(attempt, task_id, {"Authorization": f"Bearer {key}"})
    except PaintError as err:
        log(f"kie: {err}")
        return None
    try:
        jpeg, size = to_jpeg(raw)
    except PaintError as err:
        log(f"kie: {err}")
        memo.remember(None)  # collecting it again would give the same bytes
        return None
    return Painting(jpeg, "kie", size)


def _kie_collect(a: Attempt, task_id: str, auth: dict[str, str]) -> bytes:
    """Poll a kie.ai task until it has a picture, then download that."""
    url = f"{KIE_RECORD_URL}?{urllib.parse.urlencode({'taskId': task_id})}"
    poll_until = min(a.budget.clock() + KIE_POLL_MAX_S, a.budget.deadline - KIE_DOWNLOAD_RESERVE_S)
    while a.budget.clock() + KIE_POLL_EVERY_S <= poll_until:
        a.budget.sleep(KIE_POLL_EVERY_S)
        if a.stopped():
            raise PaintError("stopped (the task is collected next run)")
        polled = net.request(url, timeout=a.budget.timeout(15), headers=auth)
        a.budget.check()
        record = polled.json()
        if not polled.ok:
            continue
        state = str(_dig(record, "data", "state") or "unknown")
        if state == "success":
            image_url = _kie_result_url(_dig(record, "data", "resultJson"))
            if not image_url:
                a.memo.remember(None)
                raise PaintError("finished without a picture URL")
            got = net.request(image_url, timeout=a.budget.timeout(60))
            a.budget.check()
            if not got.ok:
                raise PaintError(f"download failed ({got.reason()}); tried again next run")
            # The task stays remembered until the story holding its picture is
            # saved: a crash before that collects the same task again.
            return got.body
        if state == "fail":
            a.memo.remember(None)
            raise PaintError(
                f"generation failed ({_dig(record, 'data', 'failMsg') or 'no reason'})"
            )
        # waiting / queuing / generating: keep polling
    raise PaintError("still painting when the time ran out; the next run collects it")


def _kie_create(a: Attempt, auth: dict[str, str]) -> str:
    """Ask kie.ai for a picture; the task id, remembered before anything else.
    The request is noted as in flight before it is sent, so dying mid-request
    leaves the same "may have been accepted" mark as an unanswered one."""
    a.memo.remember({"unanswered": True, "at": a.memo.clock()})
    created = net.post_json(
        KIE_CREATE_URL,
        {
            "model": a.settings.kie_model,
            "input": {"prompt": a.detailed, "image_size": a.settings.aspect},
        },
        timeout=a.budget.timeout(30),
        headers=auth,
    )
    answer = created.json()
    task_id = _dig(answer, "data", "taskId")
    if task_id:
        a.memo.remember({"task_id": str(task_id), "at": a.memo.clock()})
        a.budget.check()
        return str(task_id)
    if created.status == 0 or created.status >= 500:
        # No answer (or a server error) after the request went out: kie may
        # have accepted and be charging for it. The in-flight mark stays, so
        # it is not asked again for a while.
        raise PaintError(f"task request unanswered ({created.reason()}); not resending yet")
    # A definite refusal: nothing was accepted, nothing to remember.
    a.memo.remember(None)
    raise PaintError(f"task not created ({_message(answer) or created.reason()})")


def gemini(a: Attempt) -> bytes:
    """Gemini image generation: one call, the picture inline as base64."""
    reply = net.post_json(
        GEMINI_URL.format(model=urllib.parse.quote(a.settings.gemini_model, safe="-._")),
        {
            "contents": [{"parts": [{"text": a.detailed}]}],
            "generationConfig": {
                "imageConfig": {"aspectRatio": a.settings.aspect, "imageSize": "2K"}
            },
        },
        timeout=a.budget.timeout(90),
        # The header rather than ?key= keeps the key out of any URL that gets logged.
        headers={"x-goog-api-key": a.key},
    )
    a.budget.check()
    answer = reply.json()
    if isinstance(answer, dict) and isinstance(answer.get("error"), dict):
        raise PaintError(_message(answer) or reply.reason())
    if not reply.ok:
        raise PaintError(reply.reason())
    tokens = _dig(answer, "usageMetadata", "totalTokenCount")
    if isinstance(tokens, int) and not isinstance(tokens, bool):
        a.usage["tokens"] = tokens
    parts = _dig(answer, "candidates", 0, "content", "parts")
    for part in parts if isinstance(parts, list) else []:
        data = _dig(part, "inlineData", "data")
        if data:
            try:
                return base64.b64decode(str(data), validate=True)
            except (binascii.Error, ValueError) as err:
                raise PaintError("picture data is not valid base64") from err
    raise PaintError("no picture in the answer")


def pollinations(a: Attempt) -> bytes:
    """Pollinations (FLUX): a GET whose path is the prompt."""
    width, height = flux_size(a.settings.aspect)
    query = urllib.parse.urlencode(
        {
            "width": width,
            "height": height,
            "model": a.settings.pollinations_model,
            "nologo": "true",
            # Keep the prompt as written rather than letting the service rewrite it.
            "enhance": "false",
            "key": a.key,
        }
    )
    reply = net.request(
        f"{POLLINATIONS_URL}{urllib.parse.quote(a.short, safe='')}?{query}",
        timeout=a.budget.timeout(90),
    )
    a.budget.check()
    if not reply.ok:
        raise PaintError(reply.reason())
    return reply.body


def huggingface(a: Attempt) -> bytes:
    """HuggingFace Inference (FLUX.1-schnell): the picture is the response body."""
    width, height = flux_size(a.settings.aspect)
    reply = net.post_json(
        HUGGINGFACE_URL + urllib.parse.quote(a.settings.huggingface_model, safe="/-._"),
        {"inputs": a.short, "parameters": {"width": width, "height": height}},
        timeout=a.budget.timeout(90),
        headers={"Authorization": f"Bearer {a.key}", "Accept": "image/jpeg"},
    )
    a.budget.check()
    if not reply.ok:
        raise PaintError(_message(reply.json()) or reply.reason())
    return reply.body


_CALLS: dict[str, Callable[[Attempt], bytes]] = {
    "kie": kie,
    "gemini": gemini,
    "pollinations": pollinations,
    "huggingface": huggingface,
}


def kie_balance(key: str, timeout: float = 10.0) -> float | None:
    """The kie.ai account's credits left, or None when kie.ai does not say.
    Free to ask; a run asks before and after to say what it used."""
    if not key:
        return None
    reply = net.request(KIE_CREDIT_URL, timeout=timeout, headers={"Authorization": f"Bearer {key}"})
    value = _dig(reply.json(), "data") if reply.ok else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def usage_note(usage: dict[str, Any]) -> str:
    """ ", 1,290 tokens" for a log line, or "" when nothing was reported."""
    tokens = usage.get("tokens")
    return f", {tokens:,} tokens" if isinstance(tokens, int) else ""


def _kie_result_url(result: Any) -> str:
    """``resultJson`` is a JSON string (sometimes already an object) holding
    ``resultUrls``."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return ""
    urls = result.get("resultUrls") if isinstance(result, dict) else None
    if isinstance(urls, list) and urls and isinstance(urls[0], str):
        return urls[0]
    return ""


def _dig(value: Any, *path: str | int) -> Any:
    for step in path:
        if isinstance(step, int):
            if not isinstance(value, list) or len(value) <= step:
                return None
            value = value[step]
        else:
            if not isinstance(value, dict):
                return None
            value = value.get(step)
    return value


def _message(answer: Any) -> str:
    """The error sentence an API put in its JSON, if any."""
    if not isinstance(answer, dict):
        return ""
    error = answer.get("error")
    found = error.get("message") if isinstance(error, dict) else error
    found = found or answer.get("msg") or answer.get("message")
    return text.squash(str(found or ""))[:200]
