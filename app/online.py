"""Outbound calls to api.tesserae.ink, behind a single master opt-in.

Everything Tesserae sends to api.tesserae.ink (app + device-firmware update
checks, the marketplace install count, the daily heartbeat, and the template
marketplace calls) is gated by one setting, ``settings.app.online_features``.
It is OFF by default (see :func:`online_enabled`): a fresh install never
contacts api.tesserae.ink until the user opts in at the first-run wizard or
in Settings.

What is sent is documented on the privacy page: the install's random id (from
``data/core/install_id.json``), the widget/template id, and the running
version. Sharing a dashboard template additionally sends, ONLY when the user
explicitly submits one: the sanitized template JSON (secrets and
install-specific values stripped at export), the rendered preview image, the
install id, and the app version. A coarse country is derived from the caller
IP on the server side and the IP is then discarded. No account, no personal
data, no IP or User-Agent is stored.

Every call here is best-effort and never raises, EXCEPT
:func:`submit_template` (a user-initiated action whose failure must surface
in the Share dialog). A failure elsewhere (endpoint down, offline, opted out)
degrades to "no data" rather than surfacing an error.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Override for local development / tests via ``TESSERAE_API_BASE``.
API_BASE = os.environ.get("TESSERAE_API_BASE", "https://api.tesserae.ink")
_TIMEOUT_SECONDS = 4.0
_COUNTS_TTL_SECONDS = 300.0


def _coerce_bool(raw: Any, *, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return default


def _ephemeral_environment() -> bool:
    """True in a build/dev/CI environment that boots the app but isn't a real
    install: our CI, GitHub Codespaces, or Gitpod. We never phone home from
    these so they don't pollute the aggregate stats.

    Only *precise*, provider-injected markers are used. The generic ``CI`` var
    is deliberately NOT checked: a real deployment can carry it (leaked from a
    CI/CD pipeline or a base image), and gating on it would silently drop
    legitimate installs. ``GITHUB_ACTIONS`` / ``CODESPACES`` are set only by
    GitHub's runners / Codespaces, and ``GITPOD_WORKSPACE_ID`` only inside
    Gitpod, so none of them appear on a Docker / HA add-on / pip / LXC / bare
    install."""
    if _coerce_bool(os.environ.get("GITHUB_ACTIONS"), default=False):
        return True
    if _coerce_bool(os.environ.get("CODESPACES"), default=False):
        return True
    return bool(os.environ.get("GITPOD_WORKSPACE_ID"))


def online_enabled(settings_store: Any) -> bool:
    """Master opt-in. ``settings.app.online_features`` defaults to **off**: a
    fresh install never contacts api.tesserae.ink until the user says yes at the
    first-run wizard (or flips it on in Settings). Nobody is counted without an
    explicit choice.

    Always False in an ephemeral CI / Codespaces / dev-container environment
    (see :func:`_ephemeral_environment`). A pre-existing ``check_firmware_updates``
    opt-in still counts as on, so an upgrader who had enabled the old firmware
    lookup keeps it.
    """
    if settings_store is None or _ephemeral_environment():
        return False
    try:
        section = settings_store.get_section("app") or {}
    except Exception:
        return False
    if "online_features" in section:
        return _coerce_bool(section.get("online_features"), default=False)
    if "check_firmware_updates" in section:
        return _coerce_bool(section.get("check_firmware_updates"), default=False)
    return False


def report_widget_install(
    widget_id: str,
    install_id: str | None,
    version: str | None,
    *,
    api_base: str = API_BASE,
) -> bool:
    """POST one widget-install event to ``/widgets/install``. Best-effort.

    Returns True on a 2xx response, False on any failure. The caller is
    responsible for the opt-out check (:func:`online_enabled`) before calling.
    """
    if not widget_id:
        return False
    body = json.dumps(
        {"widget": widget_id, "install": install_id or "", "version": version or ""}
    ).encode("utf-8")
    url = f"{api_base.rstrip('/')}/widgets/install"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "tesserae-install"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return 200 <= int(resp.status) < 300
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("online: install report failed for %s: %s", widget_id, exc)
        return False
    except Exception as exc:
        logger.debug("online: install report failed for %s: %s", widget_id, exc)
        return False


def send_heartbeat(fields: dict[str, Any], *, api_base: str = API_BASE) -> bool:
    """POST one daily heartbeat to ``/heartbeat``. Best-effort; returns success.

    The caller checks :func:`online_enabled` first and builds ``fields`` (only
    low-cardinality, aggregate values). This layer just ships them.
    """
    body = json.dumps(fields).encode("utf-8")
    url = f"{api_base.rstrip('/')}/heartbeat"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "tesserae-heartbeat"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return 200 <= int(resp.status) < 300
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("online: heartbeat failed: %s", exc)
        return False
    except Exception as exc:
        logger.debug("online: heartbeat failed: %s", exc)
        return False


_counts_cache: tuple[float, dict[str, int]] | None = None


def widget_install_counts(
    *, api_base: str = API_BASE, ttl: float = _COUNTS_TTL_SECONDS
) -> dict[str, int]:
    """``GET /widgets/installs`` -> ``{widget_id: unique_count}``.

    Cached for ``ttl`` seconds; best-effort, returns ``{}`` on any failure so a
    down endpoint just hides the counts. The caller checks :func:`online_enabled`
    first (a fully opted-out install makes no request at all).
    """
    global _counts_cache
    now = time.time()
    if _counts_cache is not None and (now - _counts_cache[0]) < ttl:
        return _counts_cache[1]
    url = f"{api_base.rstrip('/')}/widgets/installs"
    req = urllib.request.Request(url, headers={"User-Agent": "tesserae-install"})
    counts: dict[str, int] = {}
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            if int(resp.status) == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                raw = payload.get("counts") if isinstance(payload, dict) else None
                if isinstance(raw, dict):
                    counts = {
                        str(k): int(v)
                        for k, v in raw.items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool)
                    }
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("online: install counts fetch failed: %s", exc)
    except Exception as exc:
        logger.debug("online: install counts fetch failed: %s", exc)
    _counts_cache = (now, counts)
    return counts


def clear_counts_cache() -> None:
    """Drop the cached install counts (tests + on-demand refresh)."""
    global _counts_cache
    _counts_cache = None


def latest_version(
    channel: str,
    current: str | None,
    install: str | None = None,
    *,
    api_base: str = API_BASE,
) -> dict[str, Any] | None:
    """``GET /version/latest?channel=&current=&install=`` -> the parsed dict, or
    ``None`` on any failure.

    The response shape (per channel) is
    ``{channel, current, latest: {version, url, released_at, notes_headline},
    is_current, versions_behind}``. Best-effort; the caller checks
    :func:`online_enabled` first (an opted-out install makes no request).
    """
    from urllib.parse import urlencode

    params = {"channel": channel}
    if current:
        params["current"] = current
    if install:
        params["install"] = install
    url = f"{api_base.rstrip('/')}/version/latest?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "tesserae-version"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            if int(resp.status) == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else None
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("online: version check failed: %s", exc)
    except Exception as exc:
        logger.debug("online: version check failed: %s", exc)
    return None


# -- template marketplace ------------------------------------------------
#
# Unlike the fire-and-forget telemetry above, submitting a template is a
# USER-INITIATED action whose failure must surface in the dialog, so
# ``submit_template`` raises. Everything else here stays best-effort. What a
# submission contains is documented on the privacy page: the sanitized
# template JSON (secrets stripped at export), the rendered preview PNG, the
# install id, and the app version.

_SUBMIT_TIMEOUT_SECONDS = 15.0
_template_index_cache: tuple[float, dict[str, Any]] | None = None


class TemplateSubmitError(Exception):
    """Submission failed; ``str(err)`` is safe to show in the dialog."""


class TemplateRevokedError(Exception):
    """The requested template was removed by the moderators (HTTP 410)."""


def submit_template(
    template: dict[str, Any],
    preview_png_b64: str,
    install_id: str | None,
    version: str | None,
    *,
    api_base: str = API_BASE,
) -> dict[str, Any]:
    """POST a template submission; returns the server ack
    (``{status, id, slug, author}``). Raises :class:`TemplateSubmitError`
    with a user-facing message on any failure. The caller checks
    :func:`online_enabled` first."""
    body = json.dumps(
        {
            "template": template,
            "preview_png_b64": preview_png_b64,
            "install": install_id or "",
            "version": version or "",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/templates/submit",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "tesserae-template"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_SUBMIT_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise TemplateSubmitError("unexpected response from the template service")
            return payload
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            raw = json.loads(exc.read().decode("utf-8"))
            d = raw.get("detail")
            detail = str(d.get("message") or "") if isinstance(d, dict) else str(d or "")
        with contextlib.suppress(Exception):
            exc.close()
        if exc.code == 429:
            raise TemplateSubmitError(
                detail or "submission limit reached; try again later"
            ) from exc
        if exc.code == 413:
            raise TemplateSubmitError(detail or "template or preview too large") from exc
        raise TemplateSubmitError(detail or f"submission rejected (HTTP {exc.code})") from exc
    except TemplateSubmitError:
        raise
    except Exception as exc:
        raise TemplateSubmitError("could not reach the template service") from exc


def fetch_template_author(
    install_id: str | None, *, api_base: str = API_BASE
) -> dict[str, Any] | None:
    """The pseudonym this install would publish under (share-dialog preview).
    Best-effort: None on any failure."""
    if not install_id:
        return None
    url = f"{api_base.rstrip('/')}/templates/author?install={install_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "tesserae-template"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            if int(resp.status) == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                author = payload.get("author") if isinstance(payload, dict) else None
                return author if isinstance(author, dict) else None
    except Exception as exc:
        logger.debug("online: template author fetch failed: %s", exc)
    return None


def fetch_template_index(
    *, api_base: str = API_BASE, ttl: float = _COUNTS_TTL_SECONDS
) -> dict[str, Any] | None:
    """``GET /templates/index.json`` (approved templates), TTL-cached.
    Best-effort: None means the catalog is unreachable (the Browse tab shows
    an offline state rather than an empty gallery)."""
    global _template_index_cache
    now = time.time()
    if _template_index_cache is not None and (now - _template_index_cache[0]) < ttl:
        return _template_index_cache[1]
    url = f"{api_base.rstrip('/')}/templates/index.json"
    req = urllib.request.Request(url, headers={"User-Agent": "tesserae-template"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            if int(resp.status) == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                if isinstance(payload, dict):
                    _template_index_cache = (now, payload)
                    return payload
    except Exception as exc:
        logger.debug("online: template index fetch failed: %s", exc)
    return None


def clear_template_index_cache() -> None:
    """Drop the cached template index (tests + explicit refresh)."""
    global _template_index_cache
    _template_index_cache = None


def fetch_template_doc(slug: str, *, api_base: str = API_BASE) -> dict[str, Any] | None:
    """``GET /templates/<slug>.json`` -> the full template payload, fetched
    server-side at install time (same trust posture as the widget marketplace:
    never install from a client-supplied doc). Raises
    :class:`TemplateRevokedError` on 410; returns None on other failures."""
    url = f"{api_base.rstrip('/')}/templates/{slug}.json"
    req = urllib.request.Request(url, headers={"User-Agent": "tesserae-template"})
    try:
        with urllib.request.urlopen(req, timeout=_SUBMIT_TIMEOUT_SECONDS) as resp:
            if int(resp.status) == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else None
    except urllib.error.HTTPError as exc:
        code = exc.code
        with contextlib.suppress(Exception):
            exc.close()
        if code == 410:
            raise TemplateRevokedError(slug) from exc
        logger.debug("online: template doc fetch failed: %s", exc)
    except Exception as exc:
        logger.debug("online: template doc fetch failed: %s", exc)
    return None


def report_template_install(
    slug: str,
    install_id: str | None,
    version: str | None,
    *,
    api_base: str = API_BASE,
) -> bool:
    """POST one template-install event. Best-effort, mirrors
    :func:`report_widget_install`."""
    if not slug:
        return False
    body = json.dumps({"slug": slug, "install": install_id or "", "version": version or ""}).encode(
        "utf-8"
    )
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/templates/install",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "tesserae-template"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return 200 <= int(resp.status) < 300
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("online: template install report failed: %s", exc)
    except Exception as exc:
        logger.debug("online: template install report failed: %s", exc)
    return False


def report_template(
    slug: str,
    reason: str,
    install_id: str | None,
    version: str | None,
    *,
    api_base: str = API_BASE,
) -> bool:
    """Ask for a published template to be taken down (``POST
    /templates/<slug>/report``). Routed to the same human review queue as
    submissions; a submitter reporting their own template is flagged there.
    Returns True when the request was accepted."""
    if not slug:
        return False
    body = json.dumps(
        {"reason": reason or "", "install": install_id or "", "version": version or ""}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/templates/{slug}/report",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "tesserae-template"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return 200 <= int(resp.status) < 300
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("online: template report failed: %s", exc)
    except Exception as exc:
        logger.debug("online: template report failed: %s", exc)
    return False
