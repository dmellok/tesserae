"""The widget's own settings, kept in ``config.json`` in its data dir.

The API keys are manifest settings: the app's settings store encrypts them
and Settings > Widgets edits them. Everything else (the feeds, when to run,
the summaries, the pictures, the caption) is edited on the widget's page
and kept here, as plugins/todo keeps its lists in ``lists.json``.

* :func:`load` reads the file over :func:`defaults`: a value that is missing
  or no longer valid reads as its default.
* :func:`save` checks values with :func:`check` and writes the file
  atomically (temp file and rename) under a lock.
* :func:`from_form` turns the page's form into values, saying in words what
  is wrong with any it refuses.

These settings were manifest settings before 0.3.0, so an install that had
them keeps them in settings.json under ``plugins`` > ``news_illustrated``.
The first :func:`load` without a ``config.json`` copies them from there
(``legacy``, see server.py) and writes the file; the old values stay in
settings.json, untouched, so going back to the old version finds them.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import painters, pipeline, store

CONFIG = "config.json"

# Manifest settings (secret: true), read from the settings store.
KEYS = (
    "groq_api_key",
    "kie_api_key",
    "gemini_api_key",
    "pollinations_api_key",
    "huggingface_api_key",
)

SUMMARY_PROMPT = (
    "Summarize this news story in about {words} words. Use simple, plain language that "
    "anyone can understand. No jargon, no bullet points, no headers — just clear flowing "
    "sentences."
)

FIELDS: tuple[dict[str, Any], ...] = (
    {
        "name": "sources",
        "type": "textarea",
        "label": "Sources",
        "rows": 4,
        "default": pipeline.DEFAULT_SOURCES,
        "help": "One feed per line, as Label | feed address (RSS or Atom). Each feed gives "
        "one story a day: its newest. The label is the tag shown on the frame.",
    },
    {
        "name": "run_at",
        "type": "time",
        "label": "Time to make today's stories",
        "default": pipeline.DEFAULT_RUN_AT,
        "help": "In the server's time zone. The stories are made at the first frame update "
        "after this time. For an exact time, turn on the placement's daily refresh at the "
        "same time.",
    },
    {
        "name": "summary_words",
        "type": "number",
        "label": "Summary length (words)",
        "default": pipeline.DEFAULT_WORDS,
        "min": 10,
        "max": 300,
        "help": "About how long each summary is. Short articles get shorter summaries.",
    },
    {
        "name": "summary_service",
        "type": "select",
        "label": "Summaries by",
        "default": "groq",
        "choices": [
            {"value": "groq", "label": "Groq (recommended)"},
            {"value": "off", "label": "Off: the article's opening words"},
        ],
        "help": "Groq writes a short summary of each article. Off shows the article's "
        "opening words instead.",
    },
    {
        "name": "groq_model",
        "type": "string",
        "label": "Groq model",
        "default": "openai/gpt-oss-120b",
    },
    {
        "name": "summary_prompt",
        "type": "textarea",
        "label": "Summary prompt",
        "rows": 3,
        "default": SUMMARY_PROMPT,
        "help": "{words} is the summary length. Rules against inventing facts are always added.",
    },
    {
        "name": "provider_order",
        "type": "string",
        "label": "Image provider order",
        "default": ", ".join(painters.PROVIDERS),
        "help": "The image providers to use, first choice first, comma separated (kie, gemini, "
        "pollinations, huggingface), or none.",
    },
    {
        "name": "kie_model",
        "type": "string",
        "label": "kie.ai model",
        "default": "google/nano-banana",
    },
    {
        "name": "gemini_model",
        "type": "string",
        "label": "Gemini model",
        "default": "gemini-2.5-flash-image",
    },
    {
        "name": "pollinations_model",
        "type": "string",
        "label": "Pollinations model",
        "default": "flux",
    },
    {
        "name": "huggingface_model",
        "type": "string",
        "label": "HuggingFace model",
        "default": "black-forest-labs/FLUX.1-schnell",
    },
    {
        "name": "image_prompt",
        "type": "textarea",
        "label": "Illustration prompt",
        "rows": 10,
        "default": painters.DEFAULT_IMAGE_PROMPT,
        "help": "For kie.ai and Gemini. {title}, {summary} and {shape} (the picture's shape, "
        "such as Tall portrait (9:16)) are filled in. Pollinations and HuggingFace get a short "
        "prompt of their own, which suits FLUX better.",
    },
    {
        "name": "image_shape",
        "type": "select",
        "label": "Picture shape",
        "default": "auto",
        "choices": [
            {"value": "auto", "label": "Automatic: the shape of your frame"},
            {"value": "9:16", "label": "Tall portrait (9:16)"},
            {"value": "2:3", "label": "Portrait (2:3)"},
            {"value": "3:4", "label": "Portrait (3:4)"},
            {"value": "1:1", "label": "Square (1:1)"},
            {"value": "4:3", "label": "Landscape (4:3)"},
            {"value": "3:2", "label": "Landscape (3:2)"},
            {"value": "16:9", "label": "Wide landscape (16:9)"},
        ],
        "help": "Automatic draws pictures the shape of the biggest frame that showed this "
        "widget lately (9:16 until one has).",
    },
    {
        "name": "caption_bg",
        "type": "color",
        "label": "Gradient colour",
        "default": "#000000",
        "help": "The band behind the text at the foot of the picture, fading upwards.",
    },
    {"name": "caption_fg", "type": "color", "label": "Text colour", "default": "#ffffff"},
    {"name": "show_tag", "type": "boolean", "label": "Source tag", "default": True},
    {"name": "show_date", "type": "boolean", "label": "Date", "default": True},
    {"name": "show_count", "type": "boolean", "label": "Story number", "default": True},
    {"name": "show_headline", "type": "boolean", "label": "Headline", "default": True},
    {"name": "show_summary", "type": "boolean", "label": "Summary", "default": True},
)
FIELD: dict[str, dict[str, Any]] = {str(f["name"]): f for f in FIELDS}

# The same settings as they were stored when they were manifest settings,
# for SettingsStore.get_for_runtime: no defaults, so only saved values come
# back, and none is secret.
LEGACY_FIELDS: tuple[dict[str, Any], ...] = tuple({"name": f["name"]} for f in FIELDS)

_TIME = re.compile(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*")
_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
_LOCK = threading.Lock()


def defaults() -> dict[str, Any]:
    return {str(f["name"]): f["default"] for f in FIELDS}


def check(name: str, value: Any) -> Any:
    """``value`` as setting ``name`` keeps it, or ValueError saying why not."""
    spec = FIELD[name]
    kind, label = spec["type"], spec["label"]
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{label} must be on or off")
        return value
    if kind == "number":
        if isinstance(value, str) and not value.strip():
            return spec["default"]
        low, high = spec["min"], spec["max"]
        try:
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            number = value if isinstance(value, int) else int(str(value).strip())
        except (OverflowError, ValueError):
            number = None
        if isinstance(value, bool) or number is None or not low <= number <= high:
            raise ValueError(f"{label} must be a whole number from {low} to {high}")
        return number
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if kind == "time":
        if not value.strip():
            return spec["default"]
        match = _TIME.fullmatch(value)
        if not match or int(match[1]) > 23 or int(match[2]) > 59:
            raise ValueError(f"{label} must be a time such as 06:05")
        return f"{int(match[1]):02d}:{match[2]}"
    if kind == "select":
        choices = [str(c["value"]) for c in spec["choices"]]
        if value not in choices:
            raise ValueError(f"{label} must be one of {', '.join(choices)}")
        return value
    if kind == "color":
        if not _COLOUR.fullmatch(value.strip()):
            raise ValueError(f"{label} must be a colour such as #000000")
        return value.strip().lower()
    if kind == "textarea":
        return value.replace("\r\n", "\n")
    return value.strip()


def clean(values: Mapping[str, Any]) -> dict[str, Any]:
    """The known settings in ``values`` that pass :func:`check`; the rest are left out."""
    out: dict[str, Any] = {}
    for name in FIELD:
        if name in values:
            try:
                out[name] = check(name, values[name])
            except ValueError:
                continue
    return out


def from_form(
    form: Mapping[str, str], current: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """The page's form as settings, and what is wrong with it in words.

    A checkbox is on when the form carries it. Any other setting the form
    doesn't carry (a hand-made POST) keeps its ``current`` value."""
    values: dict[str, Any] = {}
    problems: list[str] = []
    for name, spec in FIELD.items():
        if spec["type"] == "boolean":
            values[name] = name in form
            continue
        if name not in form:
            values[name] = current.get(name, spec["default"])
            continue
        try:
            values[name] = check(name, form[name])
        except ValueError as err:
            problems.append(str(err))
    return values, problems


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / CONFIG


def load(
    data_dir: Path, legacy: Callable[[], Mapping[str, Any] | None] | None = None
) -> dict[str, Any]:
    """The settings, over the defaults. Without a ``config.json`` the values
    ``legacy()`` returns (the settings store's, from before they moved here)
    are copied into a new one; when it has none to give (``None``: no store
    to ask), the defaults are returned and nothing is written."""
    with _LOCK:
        path = _path(data_dir)
        if path.exists():
            got = store.read_json(path, {})
            return {**defaults(), **clean(got if isinstance(got, dict) else {})}
        try:
            old = legacy() if legacy is not None else None
        except Exception:
            old = None
        if old is None:
            return defaults()
        values = {**defaults(), **clean(old)}
        store.write_json(path, values)
        return values


def save(data_dir: Path, values: Mapping[str, Any]) -> dict[str, Any]:
    """Check ``values`` and write them over the saved settings; ValueError
    (nothing written) when one doesn't pass. Returns what is saved."""
    checked = {name: check(name, value) for name, value in values.items() if name in FIELD}
    with _LOCK:
        got = store.read_json(_path(data_dir), {})
        saved = {**defaults(), **clean(got if isinstance(got, dict) else {}), **checked}
        store.write_json(_path(data_dir), saved)
        return saved
