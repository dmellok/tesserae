"""Signed, time-limited URLs for render artifacts (issue #151).

A publicly-hosted Tesserae instance gates ``/renders/`` to LAN / session
clients so a rendered household dashboard isn't served to anyone who
reaches the port (``app.auth._gate``). That also blocks a legitimate REST
device on a public VPS: it authenticates to ``GET /frame``, is handed a
``/renders/<digest>.<ext>`` URL, then gets ``403`` fetching it.

The fix keeps the secure default and adds a narrow allowance: ``/frame``
mints the render URL with a short-lived signature bound to that exact
render path, and the auth gate accepts a valid, unexpired signature from
any origin. Properties:

* **Secure default preserved** , no signature means LAN / session only.
* **No firmware change** , the device just fetches the URL it's given.
* **Bound + expiring** , the signature covers the render path and a
  timestamp, so it can't be replayed later or edited to point at a
  different render.

Signing is keyed off the Flask session secret (already unique per install)
via ``itsdangerous`` (a Flask dependency). When no secret is available the
helpers degrade to "unsigned", so a misconfigured install fails closed
(public stays gated) rather than open.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Render URLs are minted in the /frame response and normally fetched within
# seconds. An hour is generous headroom for a device that sleeps between the
# poll and the fetch, while keeping a leaked URL short-lived.
RENDER_URL_TTL_S: int = 3600

SIG_PARAM: str = "sig"
_SALT: str = "tesserae-render-url-v1"


def _serializer(secret: str | bytes) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_SALT)


def sign_render_query(secret: str | bytes | None, render_path: str) -> str:
    """Return a ``sig=<token>`` query fragment binding ``render_path`` to a
    timestamp, or ``""`` when no secret is available (signing disabled)."""
    if not secret:
        return ""
    token = _serializer(secret).dumps(render_path)
    return f"{SIG_PARAM}={token}"


def render_signature_valid(
    secret: str | bytes | None,
    render_path: str,
    token: str | None,
    *,
    max_age_s: int = RENDER_URL_TTL_S,
) -> bool:
    """True iff ``token`` is a valid, unexpired signature for exactly
    ``render_path``. Any signature failure, expiry, or path mismatch
    (a tampered URL) returns False."""
    if not secret or not token:
        return False
    try:
        signed_path = _serializer(secret).loads(token, max_age=max_age_s)
    except (BadSignature, SignatureExpired):
        return False
    return bool(signed_path == render_path)
