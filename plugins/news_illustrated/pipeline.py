"""The daily edition: one illustrated story per source, made in the background.

For each source: the feed's newest item, the article text (or the feed's
description), a Groq summary, then an illustration from the provider
cascade. A story is replaced only as a whole. When the feed cannot be read,
or no provider paints a picture, the previous story stays, title, summary
and picture together, so a new headline never sits on an unrelated image.

Making an edition takes minutes and costs money, so it never runs inside a
render. ``fetch()`` asks :func:`due` (cheap: two small JSON reads) and, on a
settled render, :meth:`Runner.start` hands the work to a daemon thread and
returns at once; the render shows the stories already on disk. The widget's
page starts a run by hand, and its progress shows in the job tray.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import re
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from . import feeds, painters, store, summarise, text
from . import media_lifecycle as lifecycle

try:
    from app import jobs
except ImportError:  # a Tesserae without the job tray
    jobs = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

PLUGIN_ID = "news_illustrated"
LABEL = "AI Illustrated News"
DEFAULT_SOURCES = (
    "Tech | https://hnrss.org/frontpage?points=100\n"
    "Business | https://feeds.bbci.co.uk/news/business/rss.xml\n"
    "Global | https://feeds.bbci.co.uk/news/world/rss.xml"
)
DEFAULT_RUN_AT = "06:05"
DEFAULT_WORDS = 70
# A day's edition is attempted at most this often, this far apart, when a
# run makes no story at all (the network was down at run time, say).
# A scheduled edition that made nothing is tried again on media_lifecycle's
# backoff (30 minutes, then 1 and 2 hours), at most MAX_ATTEMPTS times a day:
# fewer than the other widgets, since each try may pay for pictures.
MAX_ATTEMPTS = 4
RETRY_AFTER_S = lifecycle.RETRY_FIRST_S  # the first wait; it doubles after
RUN_HISTORY = 30
LOG_LINES = 200
# While a paid picture waits to be collected, a run comes round this often.
COLLECT_EVERY_S = 10 * 60
# Who writes the summaries: Groq, or nobody (the article's opening words).
SUMMARY_SERVICES = ("groq", "off")
# "auto" draws pictures the shape of the frames that show them.
IMAGE_SHAPES = ("auto", *painters.ASPECTS)


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    label: str
    feed: str

    @property
    def key(self) -> str:
        """Stable id: the label as a slug plus a hash of the feed, so a story
        survives reordering but not pointing the label at another feed."""
        slug = re.sub(r"[^a-z0-9]+", "-", self.label.lower()).strip("-")[:24] or "news"
        digest = hashlib.sha1(self.feed.encode("utf-8"), usedforsecurity=False).hexdigest()
        return f"{slug}-{digest[:8]}"


@dataclass(frozen=True)
class Config:
    sources: tuple[Source, ...]
    groq_key: str = field(default="", repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    summary_words: int = DEFAULT_WORDS
    summary_prompt: str = ""
    run_at: str = DEFAULT_RUN_AT
    summary_service: str = "groq"
    image_shape: str = "auto"
    paint: painters.Settings = field(default_factory=lambda: painters.Settings(keys={}))
    # Every key as configured, for scrubbing anything logged or saved.
    secrets: tuple[str, ...] = field(default=(), repr=False)
    # Settings that could not be used, said in words for the log and the page.
    problems: tuple[str, ...] = ()

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Config:
        def get(name: str, default: str = "") -> str:
            return str(settings.get(name) or "").strip() or default

        secrets: list[str] = []
        problems: list[str] = []

        def key(name: str) -> str:
            raw = get(name)
            if raw:
                secrets.append(raw)
                secrets.extend(part for part in raw.split() if len(part) >= 8)
            if raw and not text.is_clean_key(raw):
                problems.append(f"{name} has spaces or control characters in it, so it is not used")
                return ""
            return raw

        keys = {name: key(f"{name}_api_key") for name in painters.PROVIDERS}
        groq_key = key("groq_api_key")
        # Absent is the default list; saved blank means no sources at all.
        raw_sources = (
            DEFAULT_SOURCES if settings.get("sources") is None else str(settings["sources"])
        )
        problems.extend(source_problems(raw_sources))
        service = get("summary_service", "groq").lower()
        shape = get("image_shape", "auto")
        return cls(
            sources=parse_sources(raw_sources),
            groq_key=groq_key,
            groq_model=get("groq_model", "openai/gpt-oss-120b"),
            summary_words=_whole(settings.get("summary_words"), DEFAULT_WORDS, 10, 300),
            summary_prompt=get("summary_prompt"),
            run_at=parse_run_at(get("run_at", DEFAULT_RUN_AT)),
            summary_service=service if service in SUMMARY_SERVICES else "groq",
            image_shape=shape if shape in IMAGE_SHAPES else "auto",
            paint=painters.Settings(
                keys=keys,
                order=parse_order(get("provider_order")),
                kie_model=get("kie_model", "google/nano-banana"),
                gemini_model=get("gemini_model", "gemini-2.5-flash-image"),
                pollinations_model=get("pollinations_model", "flux"),
                huggingface_model=get("huggingface_model", "black-forest-labs/FLUX.1-schnell"),
                image_prompt=get("image_prompt"),
            ),
            secrets=tuple(secrets),
            problems=tuple(problems),
        )

    @property
    def painters_ready(self) -> list[str]:
        """The providers in the cascade that have a key."""
        return [p for p in self.paint.order if self.paint.keys.get(p)]

    @property
    def summary_key(self) -> str:
        """The key summaries are written with; "" when they are not."""
        return self.groq_key if self.summary_service == "groq" else ""


def parse_sources(raw: str) -> tuple[Source, ...]:
    """One source per line, ``Label | feed URL``; a bare URL is labelled News.
    Blank lines, ``#`` comments and anything that is not an http(s) URL are skipped."""
    out: list[Source] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        label, sep, url = line.rpartition("|")
        if not sep:
            label, url = "News", line
        label, url = " ".join(label.split())[:40] or "News", url.strip()
        if url.startswith(("http://", "https://")):
            out.append(Source(label, url))
    return tuple(out)


def source_problems(raw: str) -> list[str]:
    """Each line of the Sources setting that is not used, and why, in words
    for the run log and the widget's page."""
    out: list[str] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        _label, sep, url = line.rpartition("|")
        url = url.strip() if sep else line
        if not url.startswith(("http://", "https://")):
            shown = url if len(url) <= 60 else url[:57] + "..."
            out.append(
                f"Sources line {number} is skipped: {shown or 'nothing'!r} is not an "
                "http(s) feed address"
            )
    return out


def parse_order(raw: str) -> tuple[str, ...]:
    """Provider names separated by commas or spaces, unknown ones dropped;
    the default order when nothing usable is left. ``none`` turns every
    provider off (the page saves that when no box is ticked)."""
    names = [n for n in re.split(r"[\s,>]+", raw.lower()) if n in painters.PROVIDERS]
    if not names and re.fullmatch(r"\s*none\s*", raw.lower()):
        return ()
    return tuple(dict.fromkeys(names)) or painters.PROVIDERS


def pick_aspect(image_shape: str, frames: list[tuple[int, int]]) -> str:
    """The picture shape for a run: the setting, or with "auto" the shape of
    the biggest frame showing the widget lately (9:16 before any has)."""
    if image_shape in painters.ASPECTS:
        return image_shape
    if not frames:
        return painters.DEFAULT_ASPECT
    width, height = max(frames, key=lambda wh: wh[0] * wh[1])
    return painters.nearest_aspect(width, height)


def parse_run_at(raw: str) -> str:
    """``HH:MM`` (24-hour), or the default when it is not one."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", raw or "")
    if match and int(match[1]) < 24 and int(match[2]) < 60:
        return f"{int(match[1]):02d}:{match[2]}"
    return DEFAULT_RUN_AT


# ----------------------------------------------------------------------
# When a run is due
# ----------------------------------------------------------------------


def edition(now: datetime, run_at: str) -> str:
    """The edition a moment belongs to: today's from ``run_at`` on, else
    yesterday's. An ISO date."""
    hour, minute = (int(p) for p in run_at.split(":"))
    day = now.date() if (now.hour, now.minute) >= (hour, minute) else now.date() - timedelta(1)
    return day.isoformat()


def due(data_dir: Path, run_at: str, now: datetime) -> str:
    """Why a settled render should start a run, or "" when it should not.
    "schedule": there are no stories yet, or today's edition has not been made
    (nor tried too often). "collect": a picture already paid for is still to
    be collected, which does not wait for tomorrow's edition."""
    state = store.load_state(data_dir)
    if _scheduled(state, data_dir, edition(now, run_at), now):
        return "schedule"
    if owed(data_dir, now.timestamp()) and (
        now.timestamp() - float(state.get("collect_at") or 0) >= COLLECT_EVERY_S
    ):
        return "collect"
    return ""


def _scheduled(state: dict[str, Any], data_dir: Path, current: str, now: datetime) -> bool:
    if state.get("edition") == current and store.load_stories(data_dir):
        return False
    tries = [float(t) for t in (state.get("attempts") or {}).get(current, [])]
    if len(tries) >= MAX_ATTEMPTS:
        return False
    return not tries or now.timestamp() - max(tries) >= lifecycle.retry_wait(len(tries))


# ----------------------------------------------------------------------
# One story
# ----------------------------------------------------------------------


@dataclass
class Outcome:
    """What a run did, for the log, the tray and the widget's page."""

    made: int = 0
    unchanged: int = 0
    kept: int = 0
    missing: int = 0

    def count(self, what: str) -> None:
        if what not in ("made", "unchanged", "kept", "missing"):
            raise ValueError(f"unknown story outcome {what!r}")
        setattr(self, what, getattr(self, what) + 1)

    def sentence(self) -> str:
        bits = [f"{self.made} new"]
        if self.unchanged:
            bits.append(f"{self.unchanged} unchanged")
        if self.kept:
            bits.append(f"{self.kept} kept from before")
        if self.missing:
            bits.append(f"{self.missing} missing")
        return ", ".join(bits)


def story_id(source: Source, item: feeds.Item) -> str:
    """One article from one source: the source's key and a hash of the
    article's link (its title when it has none). What a paid picture task
    belongs to, and the only thing that settles it."""
    digest = hashlib.sha1((item.link or item.title).encode("utf-8"), usedforsecurity=False)
    return f"{source.key}--{digest.hexdigest()[:12]}"


def make_story(
    source: Source,
    previous: dict[str, Any] | None,
    cfg: Config,
    data_dir: Path,
    *,
    today: date,
    force: bool,
    log: Callable[[str], None],
    stopped: Callable[[], bool],
    budget: Callable[[], painters.Budget] = painters.Budget,
    usage: Usage | None = None,
) -> tuple[dict[str, Any] | None, str, str]:
    """(the story to show for ``source``, what happened: one of the
    :class:`Outcome` counters, the id of the story attempted or "")."""
    usage = usage if usage is not None else Usage()
    item = feeds.newest_item(source.feed, log)
    if item is None:
        log("Feed failed, keeping the previous story")
        return previous, "kept" if previous else "missing", ""
    log(f"Title: {item.title}")
    sid = story_id(source, item)
    if not force and previous and previous.get("image") and previous.get("story_id") == sid:
        log("Same story as before, keeping its picture")
        return previous, "unchanged", sid
    if stopped():
        return previous, "kept" if previous else "missing", ""
    # An earlier attempt at this very story (a crash, or a picture still being
    # painted) is taken up where it stopped: same summary, same paid task.
    job = store.load_job(data_dir, sid)
    story = job.get("story") if isinstance(job.get("story"), dict) else None
    if story:
        log("Taking up the unfinished attempt at this story")
    else:
        found = feeds.article(item, log)
        log(f"Summarising {len(found.text)} characters from the {found.origin}")
        summary_usage: dict[str, Any] = {}
        summary = summarise.summarise(
            item.title,
            found.text,
            api_key=cfg.summary_key,
            model=cfg.groq_model,
            words=cfg.summary_words,
            template=cfg.summary_prompt,
            log=log,
            usage=summary_usage.update,
            off=cfg.summary_service == "off",
        )
        if summary_usage:
            usage.add("groq", summary_usage.get("tokens"))
        story = {
            "story_id": sid,
            "source_key": source.key,
            "source": source.label,
            "title": item.title,
            "summary": summary,
            "url": item.link if item.link.startswith(("http://", "https://")) else "",
            "date": today.isoformat(),
            "image": "",
            "provider": "",
        }
        if summary_usage:
            story["usage"] = {"summary": {"provider": "groq", **summary_usage}}
        job = {"story": story}
        store.save_job(data_dir, sid, job)
    if stopped():
        return previous, "kept" if previous else "missing", sid

    def remember(task: dict[str, Any] | None) -> None:
        job["kie"] = task
        store.save_job(data_dir, sid, job)

    memo = painters.TaskMemo(job.get("kie"), save=remember)
    painting = painters.paint(
        str(story["title"]),
        str(story["summary"]),
        cfg.paint,
        budget=budget(),
        log=log,
        stopped=stopped,
        memo=memo,
    )
    if painting is None:
        if previous and previous.get("image"):
            # Never pair the new headline with an unrelated old picture.
            log("No picture, keeping the whole previous story")
            return previous, "kept", sid
        log("No picture and nothing earlier with one, saving the story as text only")
        return {**story, "made": int(time.time())}, "made", sid
    image = store.save_image(data_dir, source.key, painting.jpeg)
    usage.picture(painting)
    return (
        {
            **story,
            "image": image,
            "provider": painting.provider,
            "size": list(painting.size),
            "usage": {**(story.get("usage") or {}), "picture": _picture_usage(painting)},
            "made": int(time.time()),
        },
        "made",
        sid,
    )


def _picture_usage(painting: painters.Painting) -> dict[str, Any]:
    return {"provider": painting.provider, **painting.usage}


@dataclass
class Usage:
    """What a run used, as far as the services say: tokens per service,
    pictures per provider and kie.ai's credits before and after."""

    tokens: dict[str, int] = field(default_factory=dict)
    pictures: dict[str, int] = field(default_factory=dict)
    kie_before: float | None = None
    kie_after: float | None = None

    def add(self, service: str, tokens: Any) -> None:
        if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
            self.tokens[service] = self.tokens.get(service, 0) + tokens

    def picture(self, painting: painters.Painting) -> None:
        self.pictures[painting.provider] = self.pictures.get(painting.provider, 0) + 1
        self.add(painting.provider, painting.usage.get("tokens"))

    def record(self) -> dict[str, Any]:
        """For state.json's last run: only what is known."""
        out: dict[str, Any] = {}
        if self.tokens:
            out["tokens"] = dict(self.tokens)
        if self.pictures:
            out["pictures"] = dict(self.pictures)
        if self.kie_after is not None:
            out["kie_balance"] = self.kie_after
            if self.kie_before is not None and self.kie_before >= self.kie_after:
                out["kie_used"] = round(self.kie_before - self.kie_after, 2)
        return out

    def sentence(self) -> str:
        return usage_sentence(self.record())


PROVIDER_NAMES = {
    "groq": "Groq",
    "kie": "kie.ai",
    "gemini": "Gemini",
    "pollinations": "Pollinations",
    "huggingface": "HuggingFace",
}


def usage_sentence(record: dict[str, Any]) -> str:
    """A run's usage in words: "Groq 3,940 tokens; 3 pictures from kie.ai
    (12 credits, 1,180.5 left)". "" when nothing is known."""
    if not isinstance(record, dict):
        return ""
    bits: list[str] = []
    for service, count in (record.get("tokens") or {}).items():
        if isinstance(count, int):
            bits.append(f"{PROVIDER_NAMES.get(service, service)} {count:,} tokens")
    for provider, count in (record.get("pictures") or {}).items():
        if not isinstance(count, int):
            continue
        what = f"{count} picture{'s' if count != 1 else ''} from {PROVIDER_NAMES.get(provider, provider)}"
        if provider == "kie" and isinstance(record.get("kie_balance"), int | float):
            used = record.get("kie_used")
            spent = f"{used:,g} credits, " if isinstance(used, int | float) else ""
            what += f" ({spent}{record['kie_balance']:,g} left)"
        bits.append(what)
    return "; ".join(bits)


def holds(job: dict[str, Any], now: float) -> bool:
    """True while a job protects money: a kie.ai task paid for and not yet
    collected (for KIE_RESUME_S), or a create that may have been accepted (for
    KIE_UNANSWERED_S). Both are timed from the one moment in the record."""
    task = job.get("kie")
    if not isinstance(task, dict):
        return False
    age = now - float(task.get("at") or 0)
    if task.get("task_id"):
        return age < painters.KIE_RESUME_S
    return bool(task.get("unanswered")) and age < painters.KIE_UNANSWERED_S


def owed(data_dir: Path, now: float) -> list[str]:
    """Ids of stories whose kie.ai task was paid for and not yet collected."""
    return [
        sid
        for sid, job in store.load_jobs(data_dir).items()
        if holds(job, now) and (job.get("kie") or {}).get("task_id")
    ]


def _settle(data_dir: Path, sid: str, committed: dict[str, Any] | None) -> None:
    """After the story list is saved: forget the attempt at story ``sid`` once
    the saved story is that very story with its picture, or once the attempt
    protects no money."""
    if not sid:
        return
    job = store.load_job(data_dir, sid)
    if not job:
        return
    painted = bool(committed and committed.get("story_id") == sid and committed.get("image"))
    if painted or not holds(job, time.time()):
        store.save_job(data_dir, sid, None)


def _prune_jobs(data_dir: Path, cfg: Config) -> None:
    """Keep only attempts that protect money, for sources still configured."""
    keys = {s.key for s in cfg.sources}
    now = time.time()
    store.keep_jobs(
        data_dir,
        lambda job: (job.get("story") or {}).get("source_key") in keys and holds(job, now),
    )


def collect_owed(
    cfg: Config,
    data_dir: Path,
    *,
    stopped: Callable[[], bool] = lambda: False,
    log: Callable[[str], None] = logger.info,
    budget: Callable[[], painters.Budget] = painters.Budget,
    usage: Usage | None = None,
) -> Outcome:
    """Collect the kie.ai pictures already paid for, and do nothing else: no
    feed, no Groq, never a new task. A picture goes up with the story it was
    painted for, unless the source has moved on to a newer story since."""
    data_dir = Path(data_dir)
    outcome = Outcome()
    for sid in owed(data_dir, time.time()):
        if stopped():
            break
        job = store.load_job(data_dir, sid)
        story = job.get("story") if isinstance(job.get("story"), dict) else None
        if not story:
            store.save_job(data_dir, sid, None)
            continue
        log(f"Collecting the paid picture for: {story.get('title')}")

        def remember(
            task: dict[str, Any] | None, sid: str = sid, job: dict[str, Any] = job
        ) -> None:
            job["kie"] = task
            store.save_job(data_dir, sid, job)

        memo = painters.TaskMemo(job.get("kie"), save=remember)
        ordered_at = float((job.get("kie") or {}).get("at") or 0)
        painting = painters.collect(cfg.paint, memo, budget=budget(), log=log, stopped=stopped)
        if painting is None:
            if not memo.record:
                store.save_job(data_dir, sid, None)  # failed for good
            continue
        stories = {s.get("source_key"): s for s in store.load_stories(data_dir)}
        current = stories.get(story.get("source_key"))
        newer = (
            current is not None
            and current.get("story_id") != sid
            and float(current.get("made") or 0) > ordered_at
        )
        if newer or story.get("source_key") not in {s.key for s in cfg.sources}:
            log("That story has been replaced since, so its picture is not shown")
            store.save_job(data_dir, sid, None)
            outcome.count("kept")
            continue
        image = store.save_image(data_dir, str(story["source_key"]), painting.jpeg)
        if usage is not None:
            usage.picture(painting)
        stories[story["source_key"]] = {
            **story,
            "image": image,
            "provider": painting.provider,
            "size": list(painting.size),
            "usage": {**(story.get("usage") or {}), "picture": _picture_usage(painting)},
            "made": int(time.time()),
        }
        store.save_stories(data_dir, [stories[s.key] for s in cfg.sources if s.key in stories])
        store.save_job(data_dir, sid, None)  # only once the story holding it is saved
        outcome.count("made")
    _prune_jobs(data_dir, cfg)
    store.prune_images(
        data_dir, {str(s.get("image")) for s in store.load_stories(data_dir) if s.get("image")}
    )
    return outcome


# ----------------------------------------------------------------------
# A whole edition
# ----------------------------------------------------------------------


def run_edition(
    cfg: Config,
    data_dir: Path,
    *,
    tz: tzinfo | None = None,
    force: bool = False,
    stopped: Callable[[], bool] = lambda: False,
    log: Callable[[str], None] = logger.info,
    progress: Callable[[int, int, str], None] = lambda done, total, label: None,
    budget: Callable[[], painters.Budget] = painters.Budget,
    usage: Usage | None = None,
) -> Outcome:
    """Make one story per source, saving after each so panels see new
    stories as they land and a crash loses at most the one under way."""
    data_dir = Path(data_dir)
    today = datetime.now(tz).date()
    before = {s.get("source_key"): s for s in store.load_stories(data_dir)}
    shown: dict[str, dict[str, Any]] = {}
    outcome = Outcome()
    total = len(cfg.sources)
    for n, source in enumerate(cfg.sources, start=1):
        if stopped():
            log("Stopped")
            break
        progress(n - 1, total, source.label)
        log(f"Story {n} of {total}: {source.label}")
        previous = before.get(source.key)
        try:
            story, what, sid = make_story(
                source,
                previous,
                cfg,
                data_dir,
                today=today,
                force=force,
                log=log,
                stopped=stopped,
                budget=budget,
                usage=usage,
            )
        except Exception as err:  # one bad source must not cost the others their stories
            log(
                f"This story failed ({type(err).__name__}: {str(err)[:160]}), keeping the previous one"
            )
            story, what, sid = previous, "kept" if previous else "missing", ""
        outcome.count(what)
        if story is not None:
            shown[source.key] = story
        # Sources not reached yet keep their stories until their turn.
        pending = {s.key: before[s.key] for s in cfg.sources[n:] if s.key in before}
        ordered = [
            (shown | pending)[s.key] for s in cfg.sources if s.key in shown or s.key in pending
        ]
        store.save_stories(data_dir, ordered)
        _settle(data_dir, sid, story)
    _prune_jobs(data_dir, cfg)
    store.prune_images(
        data_dir, {str(s.get("image")) for s in store.load_stories(data_dir) if s.get("image")}
    )
    progress(total, total, "")
    return outcome


# ----------------------------------------------------------------------
# The background runner
# ----------------------------------------------------------------------


@dataclass
class _Run:
    data_dir: Path
    stop: threading.Event
    total: int
    lock: store.FileLock
    # The edition this run makes, fixed when it starts: a run that starts at
    # 06:04 and ends at 06:06 made yesterday's, not today's.
    edition: str
    done: int = 0
    label: str = ""


class Runner:
    """At most one run per data dir, on a daemon thread. The run holds an OS
    lock on a file in the data dir for its whole life, which keeps a second
    process (or a reloaded copy of this module) from starting another."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[Path, _Run] = {}

    def running(self, data_dir: Path) -> bool:
        """True while a run is going, here or in another process."""
        with self._lock:
            if Path(data_dir) in self._runs:
                return True
        return store.lock_held(data_dir)

    def pending(self, data_dir: Path) -> int:
        """Stories this process's run has still to make; 1 for a run elsewhere."""
        with self._lock:
            run = self._runs.get(Path(data_dir))
        if run is not None:
            return max(1, run.total - run.done)
        return 1 if store.lock_held(data_dir) else 0

    def stop(self, data_dir: Path) -> bool:
        with self._lock:
            run = self._runs.get(Path(data_dir))
        if run is None:
            return False
        run.stop.set()
        return True

    def start(
        self,
        cfg: Config,
        data_dir: Path,
        *,
        tz: tzinfo | None,
        reason: str,
        force: bool = False,
    ) -> bool:
        """Start a run unless one is going. Returns at once; False when no run
        was started (one is going, or it could not be set up)."""
        data_dir = Path(data_dir)
        if not cfg.sources:
            return False
        with self._lock:
            if data_dir in self._runs:
                return False
            lock = store.run_lock(data_dir)
            try:
                if not lock.acquire(wait=False):
                    return False
            except OSError:
                logger.warning("%s: could not open the run lock", PLUGIN_ID)
                return False
            now = datetime.now(tz)
            run = _Run(
                data_dir, threading.Event(), len(cfg.sources), lock, edition(now, cfg.run_at)
            )
            self._runs[data_dir] = run
        try:
            _note_attempt(data_dir, run.edition, now, reason)
            # The copied context carries the widget's capability scope into the
            # thread, so the run is held to the hosts the manifest declares.
            context = contextvars.copy_context()
            threading.Thread(
                target=context.run,
                args=(self._work, cfg, run, tz, reason, force),
                name=f"{PLUGIN_ID}-run",
                daemon=True,
            ).start()
        except Exception as err:
            # Nothing is running: undo everything taken above.
            logger.warning("%s: could not start a run (%s)", PLUGIN_ID, type(err).__name__)
            self._finish(run)
            return False
        return True

    def _finish(self, run: _Run) -> None:
        with self._lock:
            self._runs.pop(run.data_dir, None)
        run.lock.release()

    def _work(self, cfg: Config, run: _Run, tz: tzinfo | None, reason: str, force: bool) -> None:
        lines: list[str] = []
        started = time.time()

        def log(line: str) -> None:
            line = text.redact(line, cfg.secrets)
            lines.append(f"{time.strftime('%H:%M:%S')} {line}")
            del lines[:-LOG_LINES]
            logger.info("%s: %s", PLUGIN_ID, line)

        def progress(done: int, total: int, label: str) -> None:
            run.done, run.label = done, label
            _tray_report(run, total)

        outcome: Outcome | None = None
        failure = ""
        usage = Usage()
        kie_key = cfg.paint.keys.get("kie", "") if "kie" in cfg.painters_ready else ""
        try:
            log(f"Run started ({reason})")
            for problem in cfg.problems:
                log(problem)
            aspect = pick_aspect(cfg.image_shape, store.frame_sizes(run.data_dir, time.time()))
            cfg = replace(cfg, paint=replace(cfg.paint, aspect=aspect))
            log(f"Pictures are drawn {aspect}")
            usage.kie_before = painters.kie_balance(kie_key)
            if reason == "collect":
                outcome = collect_owed(
                    cfg, run.data_dir, stopped=run.stop.is_set, log=log, usage=usage
                )
            else:
                outcome = run_edition(
                    cfg,
                    run.data_dir,
                    tz=tz,
                    force=force,
                    stopped=run.stop.is_set,
                    log=log,
                    progress=progress,
                    usage=usage,
                )
            log(f"Run finished: {outcome.sentence()}")
            if usage.kie_before is not None:
                usage.kie_after = painters.kie_balance(kie_key)
            used = usage.sentence()
            log(f"Used: {used}" if used else "Used: nothing the services reported")
        except Exception as err:  # a bug must not leave the lock held
            failure = text.redact(f"{type(err).__name__}: {str(err)[:200]}", cfg.secrets)
            # No traceback: exception text can quote a key. Where it broke is enough.
            frame = traceback.extract_tb(err.__traceback__)[-1:]
            where = f" at {Path(frame[0].filename).name}:{frame[0].lineno}" if frame else ""
            logger.error("%s: run failed%s: %s", PLUGIN_ID, where, failure)
            log(f"Run failed: {failure}")
        finally:
            try:
                _record_run(run, reason, started, outcome, failure, lines, usage.record())
            finally:
                self._finish(run)
                _tray_close(run, outcome, failure)


RUNNER = Runner()


def _note_attempt(data_dir: Path, current: str, now: datetime, reason: str) -> None:
    state = store.load_state(data_dir)
    attempts = state.get("attempts") if isinstance(state.get("attempts"), dict) else {}
    # Only the current edition's attempts matter; older ones are dropped.
    tries = [float(t) for t in (attempts or {}).get(current, [])]
    if reason == "schedule":
        tries.append(now.timestamp())
    elif reason == "collect":
        state["collect_at"] = now.timestamp()
    state["attempts"] = {current: tries}
    store.save_state(data_dir, state)


def _record_run(
    run: _Run,
    reason: str,
    started: float,
    outcome: Outcome | None,
    failure: str,
    lines: list[str],
    usage: dict[str, Any] | None = None,
) -> None:
    state = store.load_state(run.data_dir)
    edition_run = reason != "collect"
    if edition_run and outcome and not run.stop.is_set() and (outcome.made or outcome.unchanged):
        state["edition"] = run.edition
    state["last_run"] = {
        "reason": reason,
        "started": int(started),
        "finished": int(time.time()),
        "outcome": failure or (outcome.sentence() if outcome else ""),
        "stopped": run.stop.is_set(),
        "failed": bool(failure),
        "usage": usage or {},
        "log": lines,
    }
    # Every run (each try of an edition included) in a line, for the page.
    got = state.get("runs")
    earlier: list[Any] = got if isinstance(got, list) else []
    summary = {k: state["last_run"][k] for k in ("reason", "started", "outcome", "failed")}
    if usage:
        summary["usage"] = usage
    state["runs"] = [*[r for r in earlier if isinstance(r, dict)], summary][-RUN_HISTORY:]
    try:
        store.save_state(run.data_dir, state)
    except OSError:
        logger.error("%s: could not save the run state", PLUGIN_ID)


def _tray_report(run: _Run, total: int) -> None:
    if jobs is None:
        return
    try:
        detail = f"Story {min(run.done + 1, total)} of {total}"
        jobs.report(
            PLUGIN_ID,
            "run",
            "Making the AI news",
            progress=run.done / total if total else None,
            detail=f"{detail}: {run.label}" if run.label else detail,
            label=LABEL,
            href=f"/plugins/{PLUGIN_ID}/",
            stop=run.stop.set,
        )
    except Exception:
        logger.debug("%s: job tray report failed", PLUGIN_ID, exc_info=True)


def _tray_close(run: _Run, outcome: Outcome | None, failure: str) -> None:
    if jobs is None:
        return
    try:
        if failure:
            jobs.fail(PLUGIN_ID, "run", failure)
        elif run.stop.is_set():
            jobs.cancel(PLUGIN_ID, "run", "Stopped")
        else:
            jobs.finish(PLUGIN_ID, "run", outcome.sentence() if outcome else "")
    except Exception:
        logger.debug("%s: job tray close failed", PLUGIN_ID, exc_info=True)


def _whole(value: Any, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(float(str(value)))))
    except (TypeError, ValueError):
        return default
