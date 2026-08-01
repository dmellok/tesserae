"""Outbound HTTP client for the cloud relay (home side).

Speaks the ``docs/relay/contract.md`` ``/v1`` API from the home instance:
register the install, broker rendezvous pairing, and upload sealed frames. Uses
the stdlib ``urllib`` (the convention in ``app/opendisplay_ha.py`` /
``app/ota/``), so no new dependency. The panel side (``GET .../frame``) lives in
firmware and the test harness, not here.

Every call is outbound HTTPS to a public relay origin; ``assert_public_url``
guards against a misconfigured URL pointing at a private/loopback address.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.net_guard import assert_operator_url, assert_public_url

_TIMEOUT_S: float = 10.0


class RelayError(Exception):
    """A relay call failed. ``status`` is the HTTP code (``None`` for transport
    errors); ``code`` is the relay's error envelope ``code`` when present."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _call(
    method: str,
    url: str,
    *,
    token: str | None = None,
    json_body: Any | None = None,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
    allow_local: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    """One request. Returns ``(status, headers, body)``; raises ``RelayError``
    on a 4xx/5xx or a transport failure.

    A production relay must be internet-reachable, so the URL is guarded with
    the strict :func:`assert_public_url`. ``allow_local=True`` relaxes to
    :func:`assert_operator_url` (loopback/LAN allowed, metadata still refused)
    for the wrangler-dev E2E harness."""
    if allow_local:
        assert_operator_url(url)
    else:
        assert_public_url(url)
    hdrs: dict[str, str] = dict(headers or {})
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        hdrs.setdefault("Content-Type", "application/octet-stream")
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return int(resp.status), {k: v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        code: str | None = None
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                err = parsed.get("error")
                if isinstance(err, dict) and isinstance(err.get("code"), str):
                    code = err["code"]
        except (ValueError, TypeError):
            pass
        raise RelayError(f"{method} {url} -> {exc.code}", status=exc.code, code=code) from exc
    except urllib.error.URLError as exc:
        raise RelayError(f"{method} {url} failed: {exc.reason}") from exc


def _json(status: int, body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body) if body else {}
    except ValueError as exc:
        raise RelayError(f"relay returned non-JSON ({status})") from exc
    if not isinstance(parsed, dict):
        raise RelayError(f"relay returned unexpected JSON shape ({status})")
    return parsed


def register_install(
    base_url: str, install_pubkey_b64u: str, *, label: str = "", allow_local: bool = False
) -> tuple[str, str]:
    """``POST /v1/install/register``. Returns ``(install_id, publisher_token)``.
    The token is shown once; the caller persists both."""
    body: dict[str, Any] = {"install_pubkey": install_pubkey_b64u}
    if label:
        body["label"] = label
    status, _h, raw = _call(
        "POST",
        f"{base_url.rstrip('/')}/v1/install/register",
        json_body=body,
        allow_local=allow_local,
    )
    data = _json(status, raw)
    install_id = data.get("install_id")
    token = data.get("publisher_token")
    if not isinstance(install_id, str) or not isinstance(token, str) or not install_id or not token:
        raise RelayError("register response missing install_id/publisher_token")
    return install_id, token


@dataclass(frozen=True)
class RelayClient:
    """Authenticated home-side client bound to one install."""

    base_url: str
    install_id: str
    publisher_token: str
    allow_local: bool = False

    def _install_url(self, suffix: str) -> str:
        return f"{self.base_url.rstrip('/')}/v1/i/{quote(self.install_id, safe='')}{suffix}"

    def mint_pair_code(self) -> tuple[str, str]:
        """``POST pair/codes``. Returns ``(code, expires_at)`` for the user to
        enter on the remote panel."""
        status, _h, raw = _call(
            "POST",
            self._install_url("/pair/codes"),
            token=self.publisher_token,
            allow_local=self.allow_local,
        )
        data = _json(status, raw)
        code = data.get("code")
        if not isinstance(code, str) or not code:
            raise RelayError("pair/codes response missing code")
        expires = data.get("expires_at")
        return code, expires if isinstance(expires, str) else ""

    def pending_pairings(self) -> list[dict[str, str]]:
        """``GET pair/pending``. Each entry is ``{code, panel_pubkey}`` awaiting
        completion by this install."""
        status, _h, raw = _call(
            "GET",
            self._install_url("/pair/pending"),
            token=self.publisher_token,
            allow_local=self.allow_local,
        )
        data = _json(status, raw)
        pending = data.get("pending")
        out: list[dict[str, str]] = []
        if isinstance(pending, list):
            for item in pending:
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("code"), str)
                    and isinstance(item.get("panel_pubkey"), str)
                ):
                    out.append({"code": item["code"], "panel_pubkey": item["panel_pubkey"]})
        return out

    def complete_pairing(
        self,
        *,
        code: str,
        device_id: str,
        device_token: str,
        device_token_sha256: str,
        home_pubkey_b64u: str,
        config: dict[str, Any],
    ) -> None:
        """``POST pair/<code>/complete``. Hands the relay the token (forwarded
        once to the panel) plus its hash (retained for poll validation) and the
        home public key (for the panel's key derivation)."""
        body = {
            "device_id": device_id,
            "device_token": device_token,
            "device_token_sha256": device_token_sha256,
            "home_pubkey": home_pubkey_b64u,
            "config": config,
        }
        _call(
            "POST",
            self._install_url(f"/pair/{quote(code, safe='')}/complete"),
            token=self.publisher_token,
            json_body=body,
            allow_local=self.allow_local,
        )

    def put_frame(
        self,
        *,
        device_id: str,
        etag: str,
        sealed: bytes,
        panel_w: int,
        panel_h: int,
        fmt: str,
        renderer_id: str,
        meta_b64u: str,
    ) -> None:
        """``PUT d/<device>/frame`` with the sealed body and plaintext metadata
        headers. Idempotent on a repeated ETag."""
        headers = {
            "ETag": f'"{etag}"',
            "X-Tesserae-Panel-W": str(panel_w),
            "X-Tesserae-Panel-H": str(panel_h),
            "X-Tesserae-Format": fmt,
            "X-Tesserae-Renderer": renderer_id,
            "X-Tesserae-Meta": meta_b64u,
        }
        _call(
            "PUT",
            self._install_url(f"/d/{quote(device_id, safe='')}/frame"),
            token=self.publisher_token,
            raw_body=sealed,
            headers=headers,
            allow_local=self.allow_local,
        )

    def get_device_status(self, device_id: str) -> dict[str, Any] | None:
        """``GET d/<device>/status``. The panel's last-relayed telemetry as
        ``{body, received_at}``, or ``None`` when it hasn't posted any."""
        status, _h, raw = _call(
            "GET",
            self._install_url(f"/d/{quote(device_id, safe='')}/status"),
            token=self.publisher_token,
            allow_local=self.allow_local,
        )
        if status == 204 or not raw:
            return None
        data = _json(status, raw)
        body = data.get("body")
        if not isinstance(body, str):
            return None
        received = data.get("received_at")
        return {"body": body, "received_at": received if isinstance(received, str) else ""}

    def revoke_device(self, device_id: str) -> None:
        """``DELETE d/<device>``. Drops the mailbox + token so the panel 401s."""
        _call(
            "DELETE",
            self._install_url(f"/d/{quote(device_id, safe='')}"),
            token=self.publisher_token,
            allow_local=self.allow_local,
        )
