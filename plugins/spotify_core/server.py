"""spotify_core, shared Spotify OAuth + now-playing helper.

No widget cell of its own; the spotify_* widgets reach in via the
registry and call ``now_playing()`` so they share one OAuth connection.

OAuth (Authorization Code): the user enters their app's Client ID +
Secret in Settings, then hits Connect on this plugin's admin page
(``/plugins/spotify_core/``). The callback exchanges the code for an
access + refresh token, persisted to this plugin's data_dir. ``now_
playing()`` refreshes the access token on demand (it expires hourly) and
queries the currently-playing endpoint.

Redirect-URI note: Spotify only allows ``http`` redirect URIs for the
explicit loopback host ``127.0.0.1`` (not ``localhost``, not a LAN IP).
The admin page surfaces the exact URI to register; run the one-time
Connect from a browser on the host so the URI is the loopback one.
"""

from __future__ import annotations

import base64
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers import Response

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
NOW_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"
QUEUE_URL = "https://api.spotify.com/v1/me/player/queue"
SCOPE = "user-read-currently-playing user-read-playback-state"
USER_AGENT = "tesserae/0.1 (+spotify_core)"
TOKENS_FILE = ".tokens.json"
# Refresh a little before the hard expiry so a render never races the
# boundary with a stale token.
EXPIRY_SKEW_S = 60

# Token refresh + file write must be atomic across concurrent renders
# (the push pipeline renders one composition per distinct panel).
_lock = threading.Lock()


# ----- plugin self-access ---------------------------------------------


def _data_dir() -> Path:
    registry = current_app.config["PLUGIN_REGISTRY"]
    plugin = registry.get("spotify_core")
    if plugin is None:
        raise RuntimeError("spotify_core plugin not registered")
    path: Path = plugin.data_dir
    return path


def _settings() -> dict[str, Any]:
    store = current_app.config["SETTINGS_STORE"]
    section = store.get_section("plugins") or {}
    return section.get("spotify_core") or {}


def get_client_id() -> str:
    return (_settings().get("client_id") or "").strip()


def get_client_secret() -> str:
    # Secret-flagged fields land under <name>_secret on disk.
    s = _settings()
    return (s.get("client_secret_secret") or s.get("client_secret") or "").strip()


def has_credentials() -> bool:
    return bool(get_client_id() and get_client_secret())


# ----- token store -----------------------------------------------------


def _tokens_path() -> Path:
    return _data_dir() / TOKENS_FILE


def _load_tokens() -> dict[str, Any]:
    path = _tokens_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_tokens(tokens: dict[str, Any]) -> None:
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = _tokens_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    tmp.replace(_tokens_path())


def _clear_tokens() -> None:
    with _lock:
        _tokens_path().unlink(missing_ok=True)


def connected() -> bool:
    return bool(_load_tokens().get("refresh_token"))


# ----- OAuth HTTP ------------------------------------------------------


def _basic_auth_header() -> str:
    raw = f"{get_client_id()}:{get_client_secret()}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _post_token(form: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        parsed: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        return parsed


def _store_token_response(payload: dict[str, Any], *, prior: dict[str, Any]) -> dict[str, Any]:
    """Merge a token-endpoint response into the stored token set. Spotify
    omits ``refresh_token`` on a refresh, so keep the prior one."""
    tokens = dict(prior)
    tokens["access_token"] = payload.get("access_token", "")
    if payload.get("refresh_token"):
        tokens["refresh_token"] = payload["refresh_token"]
    tokens["scope"] = payload.get("scope", tokens.get("scope", ""))
    expires_in = int(payload.get("expires_in", 3600) or 3600)
    tokens["expires_at"] = time.time() + expires_in
    _save_tokens(tokens)
    return tokens


def exchange_code(code: str, redirect_uri: str) -> None:
    """Authorization-code → token. Called from the OAuth callback."""
    with _lock:
        payload = _post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            }
        )
        _store_token_response(payload, prior=_load_tokens())


def _refresh_locked(tokens: dict[str, Any]) -> dict[str, Any]:
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise RuntimeError("no refresh token, reconnect Spotify")
    payload = _post_token({"grant_type": "refresh_token", "refresh_token": refresh})
    return _store_token_response(payload, prior=tokens)


def _valid_access_token() -> str:
    """Return a non-expired access token, refreshing under the lock if
    needed. Raises if the connection is unusable."""
    with _lock:
        tokens = _load_tokens()
        if not tokens.get("refresh_token"):
            raise RuntimeError("Spotify not connected")
        if not tokens.get("access_token") or time.time() >= (
            tokens.get("expires_at", 0) - EXPIRY_SKEW_S
        ):
            tokens = _refresh_locked(tokens)
        token: str = tokens.get("access_token", "")
        if not token:
            raise RuntimeError("could not obtain an access token")
        return token


# ----- public helper for widgets --------------------------------------


def _api_get(url: str, token: str) -> tuple[int, Any]:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        status = resp.getcode()
        if status == 204:
            return 204, None
        return status, json.loads(resp.read().decode("utf-8"))


def _normalise(item: dict[str, Any], is_playing: bool, progress_ms: int) -> dict[str, Any]:
    album = item.get("album") or {}
    images = album.get("images") or []
    # images come largest-first; keep the biggest for full-bleed art.
    art = images[0]["url"] if images else None
    artists = ", ".join(a.get("name", "") for a in (item.get("artists") or []) if a.get("name"))
    return {
        "connected": True,
        "ok": True,
        "is_playing": is_playing,
        "track": item.get("name") or "",
        "artist": artists,
        "album": album.get("name") or "",
        "album_art": art,
        "progress_ms": progress_ms,
        "duration_ms": int(item.get("duration_ms") or 0),
    }


def now_playing() -> dict[str, Any]:
    """The shared entry point every spotify widget calls. Returns a dict:

    * not connected / error → ``{"connected": False, "error": "..."}``
    * nothing playing (HTTP 204) → ``{"connected": True, "idle": True}``
    * a track → ``{"connected": True, "ok": True, track/artist/album/
      album_art/is_playing/progress_ms/duration_ms}``
    """
    if not has_credentials():
        return {"connected": False, "error": "Add your Spotify Client ID + Secret in Settings."}
    if not connected():
        return {
            "connected": False,
            "error": "Spotify not connected, connect at Plugins → Spotify.",
        }
    try:
        token = _valid_access_token()
    except Exception as err:
        return {"connected": False, "error": _coerce_error(err)}
    try:
        status, body = _api_get(NOW_PLAYING_URL, token)
    except urllib.error.HTTPError as err:
        if err.code == 401:
            # Token rejected mid-flight, force a refresh and retry once.
            try:
                with _lock:
                    _refresh_locked(_load_tokens())
                status, body = _api_get(NOW_PLAYING_URL, _valid_access_token())
            except Exception as err2:
                return {"connected": True, "error": _coerce_error(err2)}
        else:
            return {"connected": True, "error": _coerce_error(err)}
    except Exception as err:
        return {"connected": True, "error": _coerce_error(err)}

    if status == 204 or not body:
        return {"connected": True, "idle": True}
    item = body.get("item")
    if not isinstance(item, dict):
        # Ads, podcasts with no track item, or a private session.
        return {"connected": True, "idle": True}
    return _normalise(
        item,
        is_playing=bool(body.get("is_playing")),
        progress_ms=int(body.get("progress_ms") or 0),
    )


def _track_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Compact normalised representation of a Spotify track object -
    enough for a list row (title, artist, album, art) without the
    audio-features / external-url ballast."""
    album = item.get("album") or {}
    images = album.get("images") or []
    # Spotify orders images largest-first; the smallest is plenty for a
    # list row, the largest is needed for a hero / cover slot.
    art_large = images[0]["url"] if images else None
    art_small = images[-1]["url"] if images else None
    artists = ", ".join(a.get("name", "") for a in (item.get("artists") or []) if a.get("name"))
    return {
        "track": item.get("name") or "",
        "artist": artists,
        "album": album.get("name") or "",
        "album_art": art_large,
        "album_art_thumb": art_small,
        "duration_ms": int(item.get("duration_ms") or 0),
    }


def queue() -> dict[str, Any]:
    """Return the currently-playing track + the next N items in the
    user's Spotify queue.

    Shapes mirror ``now_playing()`` so widgets handle them the same way:

    * not connected / config missing → ``{"connected": False, "error": ...}``
    * Premium-required (HTTP 403)    → ``{"connected": True, "error":
      "Spotify Premium is required to read the queue."}``
    * nothing playing                → ``{"connected": True, "idle": True}``
    * a queue                        → ``{"connected": True, "ok": True,
      "currently_playing": <track>, "queue": [<track>, ...]}``

    Each track in ``currently_playing`` / ``queue`` is a ``_track_summary``
    dict. The queue Spotify returns blends user-queued tracks with the
    auto-mix "up next" set; there's no API-level distinction, so widgets
    treat them flat.
    """
    if not has_credentials():
        return {"connected": False, "error": "Add your Spotify Client ID + Secret in Settings."}
    if not connected():
        return {
            "connected": False,
            "error": "Spotify not connected, connect at Plugins → Spotify.",
        }
    try:
        token = _valid_access_token()
    except Exception as err:
        return {"connected": False, "error": _coerce_error(err)}
    try:
        status, body = _api_get(QUEUE_URL, token)
    except urllib.error.HTTPError as err:
        if err.code == 401:
            try:
                with _lock:
                    _refresh_locked(_load_tokens())
                status, body = _api_get(QUEUE_URL, _valid_access_token())
            except Exception as err2:
                return {"connected": True, "error": _coerce_error(err2)}
        elif err.code == 403:
            # Free Spotify accounts can't read the queue; surface that
            # specifically so the widget shows a useful message rather
            # than a bare "403".
            return {
                "connected": True,
                "error": "Spotify Premium is required to read the queue.",
            }
        else:
            return {"connected": True, "error": _coerce_error(err)}
    except Exception as err:
        return {"connected": True, "error": _coerce_error(err)}

    if status == 204 or not body:
        return {"connected": True, "idle": True}
    current = body.get("currently_playing")
    queue_items = body.get("queue") or []
    if not isinstance(current, dict):
        # Ads, podcasts with no track item, or a private session, same
        # treatment as ``now_playing``.
        return {"connected": True, "idle": True}
    return {
        "connected": True,
        "ok": True,
        "currently_playing": _track_summary(current),
        "queue": [_track_summary(item) for item in queue_items if isinstance(item, dict)],
    }


def _coerce_error(err: Exception) -> str:
    if isinstance(err, urllib.error.HTTPError):
        try:
            payload = json.loads(err.read().decode("utf-8", errors="replace"))
            msg = (payload.get("error") or {}).get("message") or err.reason
        except Exception:
            msg = err.reason
        return f"Spotify HTTP {err.code}: {msg}"
    return f"{type(err).__name__}: {err}"


# ----- admin blueprint (OAuth connect/callback) ------------------------


def _redirect_uri() -> str:
    return url_for("spotify_core_admin.callback", _external=True)


def blueprint() -> Blueprint:
    bp = Blueprint("spotify_core_admin", __name__, template_folder="templates")

    @bp.get("/")
    def index() -> str:
        redirect_uri = _redirect_uri()
        np: dict[str, Any] = {}
        if connected():
            np = now_playing()
        return render_template(
            "spotify_core/index.html",
            has_credentials=has_credentials(),
            connected=connected(),
            redirect_uri=redirect_uri,
            now=np,
        )

    @bp.get("/connect")
    def connect() -> Response:
        if not has_credentials():
            flash("Add your Client ID + Secret in Settings → Plugins → Spotify Core first.", "warn")
            return redirect(url_for("spotify_core_admin.index"))
        state = secrets.token_urlsafe(24)
        session["spotify_oauth_state"] = state
        params = {
            "client_id": get_client_id(),
            "response_type": "code",
            "redirect_uri": _redirect_uri(),
            "scope": SCOPE,
            "state": state,
        }
        return redirect(f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}")

    @bp.get("/callback")
    def callback() -> Response:
        err = request.args.get("error")
        if err:
            flash(f"Spotify authorisation was declined: {err}", "error")
            return redirect(url_for("spotify_core_admin.index"))
        expected = session.pop("spotify_oauth_state", None)
        if not expected or request.args.get("state") != expected:
            flash("OAuth state mismatch, please try connecting again.", "error")
            return redirect(url_for("spotify_core_admin.index"))
        code = request.args.get("code")
        if not code:
            flash("No authorisation code returned by Spotify.", "error")
            return redirect(url_for("spotify_core_admin.index"))
        try:
            exchange_code(code, _redirect_uri())
        except Exception as exc:
            flash(f"Token exchange failed: {_coerce_error(exc)}", "error")
            return redirect(url_for("spotify_core_admin.index"))
        flash("Spotify connected.", "ok")
        return redirect(url_for("spotify_core_admin.index"))

    @bp.post("/disconnect")
    def disconnect() -> Response:
        _clear_tokens()
        flash("Spotify disconnected.", "ok")
        return redirect(url_for("spotify_core_admin.index"))

    return bp
