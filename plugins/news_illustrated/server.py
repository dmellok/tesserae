"""news_illustrated (AI Illustrated News): a daily news story per feed,
summarised by Groq and illustrated as a satirical ink cartoon, shown one
story at a time.

The work is split so a render never waits on it:

* ``fetch()`` reads the stories already on disk, picks the one this placement
  shows next, and on a settled render (a push, a warm-ahead, a scheduled
  refresh) starts the day's run in the background when it is due.
* ``pipeline`` makes the stories (feed, article, summary, illustration) on a
  daemon thread, reporting to the job tray.
* The widget's page (``/plugins/news_illustrated/``) shows the stories as the
  frame draws them (client.js itself, see admin.js), the last run with what
  it used, the settings, and has Generate now and Stop.

The API keys are manifest settings, kept encrypted by the app's settings
store and edited under Settings > Widgets; the page says which are set. The
other settings are the page's own, kept in ``config.json`` (config.py).

The helper modules beside this file are imported relatively: the plugin
folder is registered as a package under the name the host gave this module,
since the host loads ``server.py`` by path (docs/widgets.md, "Helper modules
beside server.py").
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import re
import sys
import time
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    has_app_context,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from markupsafe import Markup, escape
from werkzeug.wrappers import Response

_HERE = Path(__file__).resolve().parent


def _register_package() -> None:
    """Make ``from . import x`` work when the host loaded this file by path as
    ``_tesserae_plugins.<id>.server``: the plugin folder becomes that package,
    under an empty ``_tesserae_plugins`` (the import system wants every
    ancestor). Helpers cached from an earlier load are dropped so an in-place
    reload picks up their new code too. Under the test suite the folder is
    already a real package and nothing is done."""
    package = __name__.rpartition(".")[0]
    root = package.partition(".")[0]
    if root != "_tesserae_plugins":
        return
    for name in [m for m in sys.modules if m.startswith(package + ".") and m != __name__]:
        del sys.modules[name]
    for name, search in ((root, []), (package, [str(_HERE)])):
        if name == root and name in sys.modules:
            continue
        spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
        spec.submodule_search_locations = search
        sys.modules[name] = importlib.util.module_from_spec(spec)


_register_package()

from . import config, painters, pipeline, store, text  # noqa: E402

PLUGIN_ID = pipeline.PLUGIN_ID
# How often editors and the push follow-ups look again while a run is going.
LIVE_REFRESH_S = 15

RENDER_SAFE_ENDPOINTS: tuple[str, ...] = ("news_illustrated_admin.image",)


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    """The story this placement shows now. Never makes stories itself."""
    del options
    data_dir = Path(ctx["data_dir"])
    values = config.load(data_dir, _legacy_settings)
    cfg = pipeline.Config.from_settings(merged(values, settings))
    if not cfg.sources:
        return {"error": "Add a feed under Sources in this widget's settings."}
    preview = bool(ctx.get("preview"))
    settled = bool(ctx.get("settled", not preview))
    placement = str(ctx.get("placement_id") or ctx.get("cell_id") or "default")
    if not preview:
        # The frame's size, so the next run draws pictures its shape.
        with contextlib.suppress(OSError, TypeError, ValueError):
            store.note_frame(
                data_dir,
                placement,
                int(ctx.get("cell_w") or 0),
                int(ctx.get("cell_h") or 0),
                now=time.time(),
            )
    tz = _timezone()
    if settled and not pipeline.RUNNER.running(data_dir):
        reason = pipeline.due(data_dir, cfg.run_at, datetime.now(tz))
        if reason:
            pipeline.RUNNER.start(cfg, data_dir, tz=tz, reason=reason)

    out: dict[str, Any] = {
        "run_at": cfg.run_at,
        "running": pipeline.RUNNER.running(data_dir),
        "look": look(values),
    }
    if out["running"]:
        out["live_refresh_s"] = LIVE_REFRESH_S
        out["live_pending"] = pipeline.RUNNER.pending(data_dir)
    if not cfg.painters_ready:
        out["notice"] = "No image provider has a key yet, so stories are text only."

    stories = store.load_stories(data_dir)
    if not stories:
        return out
    index = store.pick(data_dir, placement, len(stories), advance=not preview, now=time.time())
    out.update(story_data(data_dir, stories[index], index, len(stories)))
    return out


def story_data(data_dir: Path, story: dict[str, Any], index: int, count: int) -> dict[str, Any]:
    """One story as client.js draws it."""
    image = str(story.get("image") or "")
    out: dict[str, Any] = {
        "title": str(story.get("title") or ""),
        "summary": str(story.get("summary") or ""),
        "source": str(story.get("source") or ""),
        "date": str(story.get("date") or ""),
        "url": str(story.get("url") or ""),
        "image": (
            f"/plugins/{PLUGIN_ID}/image/{image}"
            if image and store.image_path(data_dir, image)
            else ""
        ),
        "index": index + 1,
        "count": count,
    }
    size = story.get("size")
    if (
        out["image"]
        and isinstance(size, list)
        and len(size) == 2
        and all(isinstance(n, int) and n > 0 for n in size)
    ):
        out["image_w"], out["image_h"] = size
    return out


_HEX = re.compile(r"#[0-9a-fA-F]{6}")
LOOK_DEFAULTS = {"bg": "#000000", "fg": "#ffffff"}
LOOK_PARTS = {
    "tag": "show_tag",
    "date": "show_date",
    "count": "show_count",
    "title": "show_headline",
    "summary": "show_summary",
}


def look(settings: dict[str, Any]) -> dict[str, Any]:
    """How the caption is drawn on every frame: its colours and which parts
    show. Unset or broken values are the defaults."""
    out: dict[str, Any] = {}
    for key, name in (("bg", "caption_bg"), ("fg", "caption_fg")):
        value = str(settings.get(name) or "").strip()
        out[key] = value.lower() if _HEX.fullmatch(value) else LOOK_DEFAULTS[key]
    for key, name in LOOK_PARTS.items():
        out[key] = settings.get(name) is not False
    return out


def merged(values: dict[str, Any], keys: dict[str, Any]) -> dict[str, Any]:
    """The settings the pipeline reads: the page's (config.json) and the API
    keys (the settings store). Nothing but a key is taken from ``keys``."""
    return {**values, **{name: keys.get(name, "") for name in config.KEYS}}


def blueprint() -> Blueprint:
    """``/plugins/news_illustrated/``: the stories, the last run, the settings."""
    bp = Blueprint("news_illustrated_admin", __name__, template_folder="templates")

    @bp.get("/")
    def index() -> str:
        data_dir = _data_dir()
        values = config.load(data_dir, _legacy_settings)
        keys = _keys()
        cfg = pipeline.Config.from_settings(merged(values, keys))
        tz = _timezone()
        state = store.load_state(data_dir)
        current = pipeline.edition(datetime.now(tz), cfg.run_at)
        last = state.get("last_run") if isinstance(state.get("last_run"), dict) else {}
        stories = store.load_stories(data_dir)
        frames = store.frame_sizes(data_dir, time.time())
        return render_template(
            "news_illustrated/index.html",
            stories=stories,
            previews=[story_data(data_dir, s, i, len(stories)) for i, s in enumerate(stories)],
            look=look(values),
            frame=preview_frame(cfg.image_shape, frames),
            aspect=pipeline.pick_aspect(cfg.image_shape, frames),
            cfg=cfg,
            values=values,
            fields=config.FIELD,
            keys=key_states(keys, _unreadable_keys()),
            keys_url=url_for("auth.settings_area", area="plugins", _anchor=f"plugin-{PLUGIN_ID}"),
            providers=provider_rows(cfg),
            link_help=link_help,
            recommended=RECOMMENDED,
            source_problems=pipeline.source_problems(str(values.get("sources") or "")),
            story_usage=story_usage,
            usage_sentence=pipeline.usage_sentence,
            names=pipeline.PROVIDER_NAMES,
            running=pipeline.RUNNER.running(data_dir),
            edition_done=state.get("edition") == current,
            last=last,
            runs=[r for r in state.get("runs") or [] if isinstance(r, dict)][::-1],
            when=lambda ts: datetime.fromtimestamp(int(ts or 0), tz).strftime("%Y-%m-%d %H:%M"),
        )

    @bp.post("/settings")
    def save_settings() -> Response:
        data_dir = _data_dir()
        current = config.load(data_dir, _legacy_settings)
        values, problems = config.from_form(request.form, current)
        if problems:
            flash("Settings not saved: " + "; ".join(problems) + ".", "error")
        else:
            config.save(data_dir, values)
            flash("Settings saved.", "ok")
        return redirect(url_for("news_illustrated_admin.index", _anchor="settings"))

    @bp.get("/admin.js")
    def admin_js() -> Response:
        response = send_file(_HERE / "admin.js", mimetype="text/javascript", max_age=0)
        return response

    @bp.get("/image/<name>")
    def image(name: str) -> Response:
        path = store.image_path(_data_dir(), name)
        if path is None:
            abort(404)
        response = send_file(path, mimetype="image/jpeg", conditional=True)
        # The name carries a hash of the picture, so it never changes under a URL.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @bp.post("/run")
    def run_now() -> Response:
        data_dir = _data_dir()
        values = config.load(data_dir, _legacy_settings)
        cfg = pipeline.Config.from_settings(merged(values, _keys()))
        if not cfg.sources:
            flash("Add a feed under Sources in this widget's settings first.", "warn")
        elif pipeline.RUNNER.start(cfg, data_dir, tz=_timezone(), reason="manual", force=True):
            flash("Making new stories now; the job tray shows how far it got.", "ok")
        else:
            flash("A run is already going.", "warn")
        return redirect(url_for("news_illustrated_admin.index"))

    @bp.post("/stop")
    def stop() -> Response:
        if pipeline.RUNNER.stop(_data_dir()):
            flash("Stopping after the step under way.", "ok")
        else:
            flash("No run is going in this server.", "warn")
        return redirect(url_for("news_illustrated_admin.index"))

    return bp


# Alex's set-up, offered as "Recommended": Groq for the summaries, then
# kie.ai's nano-banana for the pictures with the others behind it.
RECOMMENDED: dict[str, Any] = {
    "summary_service": "groq",
    "groq_model": "openai/gpt-oss-120b",
    "order": list(painters.PROVIDERS),
    "models": {
        "kie": "google/nano-banana",
        "gemini": "gemini-2.5-flash-image",
        "pollinations": "flux",
        "huggingface": "black-forest-labs/FLUX.1-schnell",
    },
}

# Where each service's model names and keys are found.
SERVICE_LINKS: dict[str, dict[str, str]] = {
    "groq": {
        "models": "https://console.groq.com/docs/models",
        "keys": "https://console.groq.com/keys",
        "note": "Any chat model Groq lists, such as openai/gpt-oss-120b.",
    },
    "kie": {
        "models": "https://kie.ai/market",
        "keys": "https://kie.ai/api-key",
        "note": "An image model from kie.ai's market, written as on its page, such as google/nano-banana.",
    },
    "gemini": {
        "models": "https://ai.google.dev/gemini-api/docs/image-generation",
        "keys": "https://aistudio.google.com/apikey",
        "note": "A Gemini model that draws pictures, such as gemini-2.5-flash-image.",
    },
    "pollinations": {
        "models": "https://gen.pollinations.ai/image/models",
        "keys": "https://enter.pollinations.ai",
        "note": "A Pollinations image model, such as flux.",
    },
    "huggingface": {
        "models": "https://huggingface.co/models?pipeline_tag=text-to-image&inference_provider=hf-inference",
        "keys": "https://huggingface.co/settings/tokens",
        "note": "A text-to-image model HuggingFace serves itself, such as black-forest-labs/FLUX.1-schnell.",
    },
}


def link_help(service: str, what: str) -> Markup:
    """An info popover's text with a link: where to find a model name or a key."""
    links = SERVICE_LINKS[service]
    name = pipeline.PROVIDER_NAMES[service]
    if what == "models":
        text = escape(links["note"]) + Markup(" ")
        label = f"{name}'s model list"
    else:
        text = Markup("")
        label = f"Get a {name} key"
    href = escape(links[what])
    return text + Markup(
        f'<a href="{href}" target="_blank" rel="noopener noreferrer">{escape(label)}</a>'
    )


def provider_rows(cfg: pipeline.Config) -> list[dict[str, Any]]:
    """The image providers for the settings: the ones in use first, in their
    order, then the rest. ``has_key`` is whether a usable key is saved."""
    order = list(cfg.paint.order)
    rest = [p for p in painters.PROVIDERS if p not in order]
    rows = []
    for name in order + rest:
        rows.append(
            {
                "id": name,
                "name": pipeline.PROVIDER_NAMES[name],
                "on": name in order,
                "has_key": bool(cfg.paint.keys.get(name)),
                "model_help": link_help(name, "models"),
                "key_help": link_help(name, "keys"),
            }
        )
    return rows


def story_usage(story: dict[str, Any]) -> str:
    """What one story used, for its caption on the page: "Groq 1,312 tokens"."""
    got = story.get("usage")
    usage: dict[str, Any] = got if isinstance(got, dict) else {}
    bits = []
    for part in ("summary", "picture"):
        got = usage.get(part)
        if isinstance(got, dict) and isinstance(got.get("tokens"), int):
            name = pipeline.PROVIDER_NAMES.get(str(got.get("provider")), str(got.get("provider")))
            bits.append(f"{name} {got['tokens']:,} tokens")
    return " · ".join(bits)


# The frame a preview is drawn at when no frame has shown the widget yet.
SHAPE_FRAMES = {
    "9:16": (1440, 2560),
    "2:3": (1200, 1800),
    "3:4": (1200, 1600),
    "4:5": (1200, 1500),
    "1:1": (1200, 1200),
    "5:4": (1500, 1200),
    "4:3": (1600, 1200),
    "3:2": (1800, 1200),
    "16:9": (1920, 1080),
    "21:9": (2520, 1080),
}


def preview_frame(image_shape: str, frames: list[tuple[int, int]]) -> dict[str, Any]:
    """The frame the page previews stories on: the biggest that showed the
    widget lately, else one the shape of the pictures."""
    if frames:
        width, height = max(frames, key=lambda wh: wh[0] * wh[1])
        return {"w": width, "h": height, "seen": True}
    width, height = SHAPE_FRAMES.get(pipeline.pick_aspect(image_shape, []), (1440, 2560))
    return {"w": width, "h": height, "seen": False}


def _data_dir() -> Path:
    plugin = current_app.config["PLUGIN_REGISTRY"].get(PLUGIN_ID)
    if plugin is None:
        abort(404)
    path: Path = plugin.data_dir
    return path


def _settings_store() -> Any:
    return current_app.config.get("SETTINGS_STORE") if has_app_context() else None


def _keys() -> dict[str, Any]:
    """The API keys, readable, as ``fetch()`` gets them."""
    settings_store = _settings_store()
    plugin = current_app.config["PLUGIN_REGISTRY"].get(PLUGIN_ID)
    if settings_store is None or plugin is None:
        return {}
    fields = plugin.manifest.get("settings", [])
    got: dict[str, Any] = settings_store.get_for_runtime("plugins", PLUGIN_ID, fields)
    return got


def _unreadable_keys() -> set[str]:
    """The keys saved but no longer decryptable (the app's secret key changed)."""
    settings_store = _settings_store()
    try:
        got = settings_store.unreadable_secrets("plugins", PLUGIN_ID) if settings_store else set()
    except Exception:
        return set()
    return {str(disk).removesuffix("_secret") for disk in got}


def _legacy_settings() -> dict[str, Any] | None:
    """The page's settings as saved when they were manifest settings (in
    settings.json under plugins > news_illustrated), for config.load to copy
    once. None when there is no settings store to ask."""
    settings_store = _settings_store()
    if settings_store is None:
        return None
    got: dict[str, Any] = settings_store.get_for_runtime(
        "plugins", PLUGIN_ID, list(config.LEGACY_FIELDS)
    )
    return got


KEY_STATES = {
    "set": ("Set", "tg--ok"),
    "unset": ("Not set", ""),
    "unusable": ("Not used: it has spaces in it", "tg--warn"),
    "unreadable": ("Saved, but it can no longer be decrypted", "tg--bad"),
}


def key_states(keys: dict[str, Any], unreadable: set[str]) -> dict[str, dict[str, str]]:
    """Whether each API key is set, for the page (never the key itself)."""
    out: dict[str, dict[str, str]] = {}
    for name in config.KEYS:
        raw = str(keys.get(name) or "").strip()
        if name in unreadable:
            state = "unreadable"
        elif not raw:
            state = "unset"
        else:
            state = "set" if text.is_clean_key(raw) else "unusable"
        words, tone = KEY_STATES[state]
        out[name] = {"state": state, "words": words, "tone": tone}
    return out


def _timezone() -> tzinfo | None:
    """The app's time zone (Settings, else the host's); None is the host's local time."""
    try:
        from app.tz_resolve import app_timezone

        return app_timezone()
    except Exception:
        return None
