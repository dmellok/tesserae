"""ha_core, shared Home Assistant connection + REST helpers.

No widget cell of its own; sibling ha_* widgets reach in via the plugin
registry and call ``get_state`` / ``get_states`` / ``history`` /
``entity_choices`` so all of them share one base URL, token, and TLS
policy. Tesserae renders headlessly on push/schedule, so a plain REST
poll at render time fits, no WebSocket needed.

Config lives in Settings → Widgets → Home Assistant Core: a base URL and
a Long-Lived Access Token (HA → profile → Long-Lived Access Tokens).
"""

from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from flask import current_app

USER_AGENT = "tesserae/0.1 (+ha_core)"


def _settings() -> dict[str, Any]:
    store = current_app.config["SETTINGS_STORE"]
    section = store.get_section("plugins") or {}
    return section.get("ha_core") or {}


def base_url() -> str:
    """Configured HA URL with any trailing slash stripped, or ""."""
    return (_settings().get("base_url") or "").strip().rstrip("/")


def token() -> str:
    """The Long-Lived Access Token. Stored as ``token_secret`` on disk per
    the settings_store secret convention."""
    s = _settings()
    return (s.get("token_secret") or s.get("token") or "").strip()


def _verify_tls() -> bool:
    # Default on; only an explicit False opts out (self-signed HTTPS).
    return _settings().get("verify_tls", True) is not False


def _not_configured() -> RuntimeError:
    """The error every call raises when Home Assistant can't be used, said
    precisely enough to act on.

    "Not configured" is true but unhelpful when the operator did configure
    it and the stored token has since become unreadable: that happens when
    the encryption key changes (a recreated container, a restored data
    folder), and the settings read turns the ciphertext into an empty
    string. Telling those apart is the difference between "set this up" and
    "type your token in again"."""
    have_url = bool(base_url())
    try:
        store = current_app.config["SETTINGS_STORE"]
        unreadable = store.unreadable_secrets("plugins", "ha_core")
    except Exception:
        unreadable = set()
    if "token_secret" in unreadable:
        return RuntimeError(
            "Home Assistant token can no longer be decrypted (the encryption key "
            "changed); re-enter it in Settings -> Plugins -> Home Assistant Core"
        )
    if have_url:
        return RuntimeError(
            "Home Assistant access token is missing; add it in Settings -> Plugins "
            "-> Home Assistant Core"
        )
    return RuntimeError("Home Assistant is not configured")


def is_configured() -> bool:
    return bool(base_url() and token())


def _ssl_context() -> ssl.SSLContext | None:
    if _verify_tls():
        return None  # urllib uses the default verifying context
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
    }


def request_json(path: str, *, timeout: int = 10) -> Any:
    """GET ``<base_url><path>`` with auth, return the parsed JSON body.

    Raises on missing config or HTTP errors so each widget can decide how
    to surface them (see ``coerce_error``). ``path`` must start with '/'.
    """
    if not is_configured():
        raise _not_configured()
    req = urllib.request.Request(base_url() + path, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_service_with_response(
    domain: str,
    service: str,
    *,
    data: dict[str, Any] | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """POST ``/api/services/<domain>/<service>?return_response`` with the
    given payload, return the parsed JSON body.

    HA 2024.5+ exposes ``return_response`` so a service call can give the
    caller a payload back (rather than just emitting state changes). Used
    here by ``ha_todo`` against ``todo.get_items``, that's the only
    sensible way to read todo items via REST without WebSocket
    subscriptions.

    Response shape on success:
    ``{"changed_states": [...], "service_response": {<entity_id>: {...}}}``

    Raises on missing config or HTTP errors so the caller can decide how
    to surface them (see ``coerce_error``)."""
    if not is_configured():
        raise _not_configured()
    path = f"/api/services/{quote(domain)}/{quote(service)}?return_response"
    body = json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(base_url() + path, data=body, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    _invalidate_states_cache()
    return payload if isinstance(payload, dict) else {}


def call_service(
    domain: str,
    service: str,
    *,
    data: dict[str, Any] | None = None,
    timeout: int = 10,
) -> list[dict[str, Any]]:
    """POST ``/api/services/<domain>/<service>`` (a plain fire-and-forget
    service call), return the list of changed-state dicts HA echoes back.

    Use this for actuators like ``light.turn_on`` / ``switch.toggle`` /
    ``cover.open``. It does NOT append ``return_response``: HA rejects that
    with 400 for any service that doesn't support returning a payload (most
    don't), which is the wrong tool for a touch action that just wants the
    service to run. ``call_service_with_response`` is only for the handful
    of services that DO return data (``todo.get_items`` et al).

    Raises on missing config or HTTP errors so the caller can surface them."""
    if not is_configured():
        raise _not_configured()
    path = f"/api/services/{quote(domain)}/{quote(service)}"
    body = json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(base_url() + path, data=body, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        raw = resp.read().decode("utf-8")
    _invalidate_states_cache()
    try:
        parsed = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def render_template(template: str, *, timeout: int = 10) -> str:
    """POST ``/api/template`` and return the rendered text.

    HA renders the Jinja template with its own device / entity helpers
    (``integration_entities``, ``device_id``, ``device_attr`` …), which
    aren't reachable through the plain state REST API. Used to pull the
    device registry (e.g. list an integration's devices) without a
    WebSocket connection. Raises on missing config or HTTP errors.
    """
    if not is_configured():
        raise _not_configured()
    body = json.dumps({"template": template}).encode("utf-8")
    req = urllib.request.Request(
        base_url() + "/api/template", data=body, headers=_headers(), method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return resp.read().decode("utf-8")


# Short-TTL caches. The page editor hydrates every cell on each preview
# cycle and resolves an entity dropdown per ha_* widget on load; without
# caching each of those costs a full ``/api/states`` dump (hundreds of KB
# on a real install). A single dump per few seconds is fresh enough for a
# preview or a panel push, so all callers inside the window share one
# request. Errors are cached for the same window so an unreachable HA
# fails fast once instead of stacking sequential 10 s timeouts. History
# is cached longer: a 24 h sparkline's shape doesn't change within a
# couple of minutes. Both caches key on (base_url, token) so a settings
# change invalidates them implicitly.
_STATES_TTL_S = 5.0
_HISTORY_TTL_S = 120.0
_HISTORY_CACHE_MAX = 256

_states_lock = threading.Lock()
_states_cache: tuple[tuple[str, str], float, list[dict[str, Any]] | Exception] | None = None
_history_lock = threading.Lock()
_history_cache: dict[tuple[str, str, str, int], tuple[float, list[dict[str, Any]]]] = {}


def _invalidate_states_cache() -> None:
    """Drop the cached states dump. Called after every service call so a
    touch action's re-render (tap echo, overlay refresh) reads the
    post-toggle state instead of a dump from a few seconds ago."""
    global _states_cache
    with _states_lock:
        _states_cache = None


def get_states() -> list[dict[str, Any]]:
    """All entity states: ``[{entity_id, state, attributes, ...}, ...]``.

    Served from a shared short-TTL cache (see above); callers must treat
    the returned list as read-only. Holding the lock across the fetch is
    deliberate: concurrent cell hydrations queue behind one request and
    then all hit the cache, instead of racing N identical dumps."""
    global _states_cache
    key = (base_url(), token())
    with _states_lock:
        if _states_cache is not None:
            cached_key, ts, value = _states_cache
            if cached_key == key and time.monotonic() - ts < _STATES_TTL_S:
                if isinstance(value, Exception):
                    raise value
                return value
        try:
            data = request_json("/api/states")
        except Exception as err:
            _states_cache = (key, time.monotonic(), err)
            raise
        states = data if isinstance(data, list) else []
        _states_cache = (key, time.monotonic(), states)
        return states


def get_state(entity_id: str) -> dict[str, Any]:
    """A single entity's state dict."""
    data = request_json(f"/api/states/{quote(entity_id)}")
    return data if isinstance(data, dict) else {}


def history(entity_id: str, *, hours: int = 24, timeout: int = 12) -> list[dict[str, Any]]:
    """Recent state changes for one entity over the last ``hours``.

    Uses ``/api/history/period`` with ``minimal_response`` so each sample
    is just ``{state, last_changed}``, enough for a sparkline without the
    attribute payload. Returns the (possibly empty) sample list, served
    from a per-entity TTL cache (errors are not cached)."""
    key = (base_url(), token(), entity_id, hours)
    with _history_lock:
        hit = _history_cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < _HISTORY_TTL_S:
            return hit[1]
    start = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    path = (
        f"/api/history/period/{quote(start)}"
        f"?filter_entity_id={quote(entity_id)}"
        "&minimal_response&significant_changes_only"
    )
    data = request_json(path, timeout=timeout)
    samples: list[dict[str, Any]] = []
    if isinstance(data, list) and data and isinstance(data[0], list):
        samples = [s for s in data[0] if isinstance(s, dict)]
    with _history_lock:
        if len(_history_cache) >= _HISTORY_CACHE_MAX:
            _history_cache.clear()
        _history_cache[key] = (time.monotonic(), samples)
    return samples


def friendly_name(state: dict[str, Any]) -> str:
    """Best label for an entity: its friendly_name, else the entity_id."""
    attrs = state.get("attributes") or {}
    return str(attrs.get("friendly_name") or state.get("entity_id") or "")


def entity_choices(domains: tuple[str, ...] | None = None) -> list[dict[str, str]]:
    """Powers the editor's entity dropdown (``choices_from: "entity"``).

    Lists every entity (optionally filtered to the given domains, e.g.
    ``("climate",)``) as ``{value: entity_id, label: "Friendly (id)"}``.
    When HA isn't configured or is unreachable, returns a single guidance
    option instead of an empty dropdown so the editor explains itself."""
    if not is_configured():
        return [
            {"value": "", "label": "Set HA URL + token in Settings → Widgets → Home Assistant Core"}
        ]
    try:
        states = get_states()
    except Exception:
        return [{"value": "", "label": "Home Assistant unreachable, check URL + token"}]
    out: list[dict[str, str]] = []
    for st in states:
        eid = str(st.get("entity_id") or "")
        if not eid:
            continue
        if domains and eid.split(".", 1)[0] not in domains:
            continue
        name = friendly_name(st)
        label = f"{name} ({eid})" if name and name != eid else eid
        out.append({"value": eid, "label": label})
    out.sort(key=lambda c: c["label"].lower())
    return out


def coerce_error(err: Exception) -> str:
    """Friendly one-line error for widgets to surface."""
    if isinstance(err, urllib.error.HTTPError):
        reason = err.reason
        if err.code == 401:
            reason = "unauthorized, check the access token"
        elif err.code == 404:
            reason = "not found, check the entity id"
        return f"Home Assistant HTTP {err.code}: {reason}"
    if isinstance(err, urllib.error.URLError):
        return f"Home Assistant unreachable: {err.reason}"
    return f"{type(err).__name__}: {err}"
