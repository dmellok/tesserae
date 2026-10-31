"""Keep every lineup step on a display its dashboard is bound to.

A dashboard is delivered only to the displays it is bound to, so a lineup,
schedule or rotation that points a display at a dashboard bound elsewhere
fails on every fire ("dashboard targets none of the requested device(s)").
Every write path that can put a dashboard on a display goes through here:
it names the dashboards that would be stranded, and refuses unless the
caller consented to bind them (``bind_pages``, optionally ``bind_also`` to
keep the dashboard's other display). Nothing is ever bound silently.

A dashboard bound to no display at all is not stranded: the existing
"adding one binds it" rule for unassigned dashboards is unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from flask import Response, current_app, jsonify

CODE = "pages_not_bound"


@dataclass(frozen=True)
class Consent:
    """Dashboards the caller agreed to bind. ``also`` keeps their other
    display (bound to both); the rest move to the lineup's display."""

    pages: frozenset[str] = frozenset()
    also: frozenset[str] = frozenset()

    def covers(self, page_id: str) -> bool:
        return page_id in self.pages or page_id in self.also

    @classmethod
    def from_form(cls, form: Any) -> Consent:
        also = frozenset(_ids(form.getlist("bind_also")))
        return cls(frozenset(_ids(form.getlist("bind_pages"))) | also, also)

    @classmethod
    def from_json(cls, body: Mapping[str, Any]) -> Consent:
        also = frozenset(_ids(body.get("bind_also")))
        return cls(frozenset(_ids(body.get("bind_pages"))) | also, also)


@dataclass
class Stranded:
    page_id: str
    name: str
    bound_to: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.page_id, "name": self.name, "bound_to": self.bound_to}


def _ids(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, Iterable):
        return []
    return [s for s in (str(x).strip() for x in raw) if s]


def _unique(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in ids if i))


def display_info(device_id: str) -> dict[str, Any]:
    from app.deck_routes import _device_size

    reg = current_app.config.get("DEVICE_REGISTRY")
    device = reg.get(device_id) if reg is not None else None
    if device is None:
        return {"id": device_id, "name": device_id, "width": 0, "height": 0}
    w, h, _spec = _device_size(device)
    return {"id": device_id, "name": device.display_name, "width": w, "height": h}


def display_label(device_id: str) -> str:
    return str(display_info(device_id)["name"])


def stranded_pages(display_ids: Iterable[str], page_ids: Iterable[str]) -> list[Stranded]:
    """Dashboards among ``page_ids`` bound only to displays other than
    ``display_ids``. Missing dashboards and unassigned ones are not stranded."""
    displays = set(display_ids)
    if not displays:
        return []
    store = current_app.config["PAGE_STORE"]
    out: list[Stranded] = []
    for pid in _unique(page_ids):
        page = store.get(pid)
        if page is None or not page.device_ids or displays & set(page.device_ids):
            continue
        out.append(Stranded(pid, page.name, [display_info(d) for d in _unique(page.device_ids)]))
    return out


def unconsented(
    display_ids: list[str],
    page_ids: Iterable[str],
    consent: Consent,
    *,
    grandfathered: Iterable[str] = (),
) -> list[Stranded]:
    """Stranded dashboards the caller did not agree to bind. ``grandfathered``
    ids are exempt: a lineup that was already broken can still be edited
    without being forced to fix every step in the same request."""
    skip = set(grandfathered)
    return [
        s
        for s in stranded_pages(display_ids, page_ids)
        if not consent.covers(s.page_id) and s.page_id not in skip
    ]


def guard_change(
    display_ids: list[str],
    page_ids: Iterable[str],
    consent: Consent,
    *,
    old_displays: list[str] | None = None,
    old_pages: Iterable[str] = (),
) -> list[Stranded]:
    """``unconsented`` for an edit of an existing record: steps that were
    already stranded on the same displays are left alone, so only a newly
    introduced mismatch blocks the save."""
    grandfathered: list[str] = []
    if old_displays is not None and list(old_displays) == list(display_ids):
        grandfathered = [s.page_id for s in stranded_pages(old_displays, old_pages)]
    return unconsented(display_ids, page_ids, consent, grandfathered=grandfathered)


def prompt_text(display_id: str, pages: list[Stranded]) -> str:
    target = display_info(display_id)
    names = ", ".join(f'"{s.name}"' for s in pages)
    return (
        f"{names} {'is' if len(pages) == 1 else 'are'} not bound to {target['name']}, "
        "so it cannot show there."
    )


def refusal(display_ids: list[str], pages: list[Stranded], *, status: int = 409) -> Response:
    """The 4xx body every non-interactive caller (and the UI's retry) gets:
    which dashboards, which displays, and the exact fix."""
    target = display_ids[0] if display_ids else ""
    ids = [s.page_id for s in pages]
    info = display_info(target) if target else {}
    message = (
        f"{prompt_text(target, pages)} Bind them to {info.get('name', target)} by sending "
        f"bind_pages: {ids!r} (add bind_also with the same ids to keep their current "
        "display too), or bind the dashboards to this display first."
    )
    resp = jsonify(
        {
            "ok": False,
            "code": CODE,
            "error": message,
            "message": message,
            "display": info,
            "unbound_pages": [s.to_dict() for s in pages],
            "fix": {"bind_pages": ids},
        }
    )
    resp.status_code = status
    return resp


def flash_text(display_ids: list[str], pages: list[Stranded], *, retry: bool = True) -> str:
    target = display_ids[0] if display_ids else ""
    tail = ", or remove them, then save again." if retry else " first."
    return f"{prompt_text(target, pages)} Bind them to {display_label(target)}{tail}"


def apply_consent(display_ids: list[str], page_ids: Iterable[str], consent: Consent) -> None:
    """Bind the consented, stranded dashboards. ``bind_also`` ones keep their
    other displays; the rest move to the lineup's displays."""
    store = current_app.config["PAGE_STORE"]
    for s in stranded_pages(display_ids, page_ids):
        if not consent.covers(s.page_id):
            continue
        page = store.get(s.page_id)
        if page is None:
            continue
        keep = list(page.device_ids) if s.page_id in consent.also else []
        store.save(page.model_copy(update={"device_ids": _unique([*keep, *display_ids])}))
