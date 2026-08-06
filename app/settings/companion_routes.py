"""Settings -> Companion app: pair the iOS client and manage its tokens.

The companion API (``/api/app/v1``, discussion #147) mints single-use codes the
app exchanges for a scoped, revocable per-client token. This page owns that
operator surface: issue a code, watch what is pending, and disconnect a paired
client.

The same controls used to be a card inside Settings -> Devices. They moved to
their own page (#186) so the app surface has room to grow without crowding the
device list, and so the beta install link has somewhere to live.
"""

from __future__ import annotations

import time
from typing import Any

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers import Response

from ._shared import bp

# Public TestFlight beta for the community-built iOS app. The QR on the page
# encodes this same URL so a phone can install it without typing anything.
TESTFLIGHT_URL = "https://testflight.apple.com/join/gjQar3TK"


def _pending_companion_pairings() -> list[dict[str, Any]]:
    """Snapshot of live companion pairing codes. Separate store from the
    firmware pairing codes (``COMPANION_PAIRING_STORE``) so the two purposes
    never mix. Defensive against the store not being wired (test paths)."""
    store = current_app.config.get("COMPANION_PAIRING_STORE")
    if store is None:
        return []
    return [
        {
            "code": p.code,
            "note": p.note,
            "seconds_left": max(0, int(p.expires_at - time.time())),
        }
        for p in store.list_pending()
    ]


def _companion_sessions() -> list[dict[str, Any]]:
    """Paired companion clients (name + last use), each revocable on its own.
    Defensive against the store not being wired."""
    store = current_app.config.get("COMPANION_TOKENS")
    if store is None:
        return []
    return [record.public_dict() for record in store.list_active()]


@bp.get("/settings/companion")
def companion_index() -> str:
    return render_template(
        "settings_companion.html",
        active="companion",
        companion_pairing_codes=_pending_companion_pairings(),
        companion_sessions=_companion_sessions(),
        companion_pairing_reveal=session.pop("_companion_pairing_reveal", None),
        testflight_url=TESTFLIGHT_URL,
        companion_configured=current_app.config.get("COMPANION_PAIRING_STORE") is not None,
    )


@bp.post("/settings/companion/pair")
def companion_pair_issue() -> Response:
    """Mint a companion pairing code. The operator reads it into the iOS app,
    which exchanges it at ``POST /api/app/v1/pair`` for a per-client token.
    Distinct store from firmware pairing so a firmware code can't pair the
    app."""
    store = current_app.config.get("COMPANION_PAIRING_STORE")
    if store is None:
        flash("The companion API is not configured on this install.", "error")
        return redirect(url_for("auth.companion_index"))
    note = (request.form.get("note") or "").strip()[:64]
    record = store.issue(note=note)
    session["_companion_pairing_reveal"] = {
        "code": record.code,
        "expires_at": record.expires_at,
        "note": record.note,
    }
    flash("Companion pairing code issued. Enter it in the companion app.", "ok")
    return redirect(url_for("auth.companion_index"))


@bp.post("/settings/companion/pair/<code>/revoke")
def companion_pair_revoke(code: str) -> Response:
    """Drop a pending companion pairing code."""
    store = current_app.config.get("COMPANION_PAIRING_STORE")
    if store is not None and store.revoke(code):
        flash(f"Companion pairing code {code} revoked.", "ok")
    return redirect(url_for("auth.companion_index"))


@bp.post("/settings/companion/session/<token_id>/revoke")
def companion_session_revoke(token_id: str) -> Response:
    """Revoke a paired companion client by its token id. The client's bearer
    stops working immediately; it can re-pair with a fresh code."""
    store = current_app.config.get("COMPANION_TOKENS")
    if store is not None and store.revoke(token_id):
        flash("Companion app disconnected.", "ok")
    return redirect(url_for("auth.companion_index"))
