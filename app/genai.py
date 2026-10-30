"""Settings › Generative AI: the fal.ai account every painting widget shares,
one spending ceiling over all of them, and the ledger of what was spent.

An OpenAI key, for GPT Image straight from OpenAI, is stored as
``app.openai.api_key`` (:mod:`app.openai_images`), and a Gemini key, for
Nano Banana straight from Google, as ``app.gemini.api_key``
(:mod:`app.gemini_images`).

The fal.ai key is stored as ``app.fal.api_key`` (where :func:`app.fal.resolve_key`
already looks after a widget's own key). The ceilings are US dollars a day
and a month over every :class:`app.artwork.Collection` together; a widget's
own daily budget still applies inside them. The ledger is one line per
charge in ``<data root>/artwork/ledger.jsonl``, appended by the collection
that spent, and read back here for the page.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

FIELDS: list[dict[str, Any]] = [
    {"name": "api_key", "secret": True},
    {"name": "daily_usd", "type": "number", "default": 0},
    {"name": "monthly_usd", "type": "number", "default": 0},
    {"name": "contact", "type": "string", "default": ""},
]
UNLIMITED = 10**9
KEEP_DAYS = 400  # ledger lines older than this are dropped on the next append
# A request that cannot exist: a good key gets "not found", a bad one "unauthorised",
# and nothing is painted or charged
CHECK_URL = (
    "https://queue.fal.run/fal-ai/nano-banana/requests/00000000-0000-0000-0000-000000000000/status"
)


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
def settings(store: Any) -> dict[str, Any]:
    """The runtime values: the key, and the ceilings in US dollars (0 is none)."""
    if store is None:
        return {"api_key": "", "daily_usd": 0, "monthly_usd": 0, "contact": ""}
    vals = store.get_for_runtime("app", "fal", FIELDS)
    return {
        "api_key": str(vals.get("api_key") or "").strip(),
        "daily_usd": _usd(vals.get("daily_usd")),
        "monthly_usd": _usd(vals.get("monthly_usd")),
        "contact": " ".join(str(vals.get("contact") or "").split())[:120],
    }


def for_admin(store: Any) -> dict[str, Any]:
    """The same with the key masked, for the form."""
    vals = store.get_for_runtime("app", "fal", FIELDS)
    masked = store.get_for_admin("app", "fal", FIELDS)
    from app import gemini_images, openai_images

    o_vals = store.get_for_runtime("app", "openai", openai_images.FIELDS)
    o_masked = store.get_for_admin("app", "openai", openai_images.FIELDS)
    g_vals = store.get_for_runtime("app", "gemini", gemini_images.FIELDS)
    g_masked = store.get_for_admin("app", "gemini", gemini_images.FIELDS)
    return {
        "api_key": str(masked.get("api_key") or ""),
        "has_key": bool(str(vals.get("api_key") or "").strip()),
        "openai_api_key": str(o_masked.get("api_key") or ""),
        "has_openai_key": bool(str(o_vals.get("api_key") or "").strip()),
        "gemini_api_key": str(g_masked.get("api_key") or ""),
        "has_gemini_key": bool(str(g_vals.get("api_key") or "").strip()),
        "daily_usd": _usd(vals.get("daily_usd")),
        "monthly_usd": _usd(vals.get("monthly_usd")),
        "contact": " ".join(str(vals.get("contact") or "").split())[:120],
    }


def save(store: Any, form: dict[str, Any]) -> None:
    """Keep what the form sent: a key left masked stays, an empty one clears."""
    values: dict[str, Any] = {}
    if "api_key" in form:
        values["api_key"] = str(form.get("api_key") or "").strip()
    for name in ("daily_usd", "monthly_usd"):
        if name in form:
            values[name] = _usd(form.get(name))
    if "contact" in form:
        values["contact"] = " ".join(str(form.get("contact") or "").split())[:120]
    store.update_for_namespace("app", "fal", values, FIELDS)
    if "openai_api_key" in form:
        from app import openai_images

        store.update_for_namespace(
            "app",
            "openai",
            {"api_key": str(form.get("openai_api_key") or "").strip()},
            openai_images.FIELDS,
        )
    if "gemini_api_key" in form:
        from app import gemini_images

        store.update_for_namespace(
            "app",
            "gemini",
            {"api_key": str(form.get("gemini_api_key") or "").strip()},
            gemini_images.FIELDS,
        )


def ceilings(store: Any) -> tuple[int, int]:
    """(daily, monthly) in US cents; UNLIMITED for 0."""
    s = settings(store)
    return _cents(s["daily_usd"]), _cents(s["monthly_usd"])


def _usd(raw: Any) -> float:
    try:
        v = float(str(raw).replace("$", "").replace("US", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(v, 100000.0)), 2)


def _cents(usd: float) -> int:
    return round(usd * 100) if usd > 0 else UNLIMITED


def dollars(cents: int) -> str:
    return "no limit" if cents >= UNLIMITED else f"US${cents / 100:.2f}"


# ----------------------------------------------------------------------
# The key check
# ----------------------------------------------------------------------
def check_key(key: str, timeout: float = 8.0) -> tuple[bool, str]:
    """(ok, a sentence). Asks fal.ai about a request that does not exist:
    a good key is told so, a bad one is refused. Nothing is charged."""
    key = (key or "").strip()
    if not key:
        return False, "No key to check."
    req = urllib.request.Request(
        CHECK_URL, headers={"Authorization": f"Key {key}", "User-Agent": "tesserae"}
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            pass
    except urllib.error.HTTPError as err:
        took = time.time() - t0
        if err.code in (401, 403):
            return False, "fal.ai refused the key."
        if err.code in (404, 422, 400):
            return True, f"fal.ai accepted the key, answering in {took:.1f} s."
        return False, f"fal.ai answered HTTP {err.code}."
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return False, f"fal.ai could not be reached ({type(err).__name__})."
    return True, "fal.ai accepted the key."


# ----------------------------------------------------------------------
# The ledger
# ----------------------------------------------------------------------
class Ledger:
    """One line per charge: when, which widget and collection, which model,
    how many US cents. Today's count can be reset without losing the lines."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.path = self.folder / "ledger.jsonl"
        self.reset_path = self.folder / "ledger_reset.json"
        self._lock = threading.Lock()
        self._kept_at = 0.0

    def append(self, plugin_id: str, collection: str, model: str, cents: int) -> None:
        now = time.time()
        row = {
            "t": now,
            "day": time.strftime("%Y-%m-%d", time.localtime(now)),
            "plugin": plugin_id,
            "collection": collection,
            "model": model or "",
            "cents": int(cents),
        }
        with self._lock:
            with contextlib.suppress(OSError):
                self.folder.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
            if now - self._kept_at > 86400:
                self._kept_at = now
                self._trim(now)

    def _trim(self, now: float) -> None:
        keep_after = now - KEEP_DAYS * 86400
        rows = [r for r in self._rows_unlocked() if r["t"] >= keep_after]
        with contextlib.suppress(OSError):
            self.path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def _rows_unlocked(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    with contextlib.suppress(ValueError, TypeError):
                        r = json.loads(line)
                        if isinstance(r, dict) and "t" in r:
                            r["t"] = float(r["t"])
                            r["cents"] = int(r.get("cents") or 0)
                            out.append(r)
        except OSError:
            pass
        return out

    def rows(self, since: float = 0.0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._rows_unlocked()
        return [r for r in rows if r["t"] >= since]

    # -- today, and the reset ------------------------------------------------

    def reset_today(self) -> None:
        """Today's spend counts from now; the lines stay for the record."""
        with contextlib.suppress(OSError):
            self.folder.mkdir(parents=True, exist_ok=True)
            self.reset_path.write_text(
                json.dumps({"day": time.strftime("%Y-%m-%d"), "t": time.time()}),
                encoding="utf-8",
            )

    def _reset_after(self) -> float:
        try:
            got = json.loads(self.reset_path.read_text(encoding="utf-8"))
            if got.get("day") == time.strftime("%Y-%m-%d"):
                return float(got.get("t") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return 0.0

    def today_cents(self) -> int:
        day = time.strftime("%Y-%m-%d")
        after = self._reset_after()
        return sum(r["cents"] for r in self.rows() if r.get("day") == day and r["t"] > after)

    def month_cents(self) -> int:
        month = time.strftime("%Y-%m")
        return sum(r["cents"] for r in self.rows() if str(r.get("day", "")).startswith(month))

    # -- the page ----------------------------------------------------------------

    def summary(self, days: int = 30) -> dict[str, Any]:
        """Today, this month, a strip of the last ``days`` days, and the month's
        lines grouped by widget and by widget and model."""
        now = time.time()
        today = date.today()
        month = today.strftime("%Y-%m")
        rows = self.rows(now - (days + 1) * 86400)
        after = self._reset_after()
        by_day: dict[str, int] = {}
        by_widget: dict[tuple[str, str], dict[str, Any]] = {}
        by_model: dict[tuple[str, str, str], dict[str, Any]] = {}
        for r in rows:
            d = str(r.get("day") or "")
            by_day[d] = by_day.get(d, 0) + r["cents"]
            w = by_widget.setdefault(
                (str(r.get("plugin")), str(r.get("collection"))),
                {"today": 0, "period": 0, "pictures": 0},
            )
            if d == today.isoformat() and r["t"] > after:
                w["today"] += r["cents"]
            w["period"] += r["cents"]
            w["pictures"] += 1
            if d.startswith(month):
                m = by_model.setdefault(
                    (str(r.get("plugin")), str(r.get("collection")), str(r.get("model") or "")),
                    {"pictures": 0, "cents": 0},
                )
                m["pictures"] += 1
                m["cents"] += r["cents"]
        strip: list[dict[str, Any]] = []
        for i in range(days - 1, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            strip.append({"day": d, "cents": by_day.get(d, 0)})
        peak = max((int(s["cents"]) for s in strip), default=0)
        for s in strip:
            c = int(s["cents"])
            s["level"] = 0 if not c else max(1, round(4 * c / peak))
        elapsed = max(1, today.day)
        month_cents = sum(v for d, v in by_day.items() if d.startswith(month))
        days_in_month = (
            (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        ).day
        return {
            "today": self.today_cents(),
            "month": month_cents,
            "month_label": today.strftime("%B"),
            "month_projected": int(month_cents * days_in_month / elapsed),
            "period_days": days,
            "strip": strip,
            "strip_from": strip[0]["day"] if strip else "",
            "strip_to": strip[-1]["day"] if strip else "",
            "by_widget": by_widget,
            "by_model": by_model,
            "reset_at": datetime.fromtimestamp(after).strftime("%H:%M") if after else "",
        }
