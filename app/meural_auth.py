"""Netgear OAuth exchange/refresh, adapted from GuySie/ha-meural (MIT).

Cognito password and MFA challenges run in the browser. This module handles
the same Meural token exchange and refresh as HA-meural's netgear_auth.py.
Copyright (c) 2020 Guy Sie. Full license in NOTICES.md.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from app.meural_http import open_request, read_response

# Public app ids from Netgear's own app, not client secrets.
COGNITO_CLIENT_ID = "487bd4kvb1fnop6mbgk8gu5ibf"
COGNITO_URL = "https://cognito-idp.eu-west-1.amazonaws.com/"
MEURAL_OAUTH_CLIENT_ID = "3ui6nklcaqoij8inrkm06gfk4s"
NETGEAR_ACCOUNTS_URL = "https://accounts2.netgear.com"
RESPONSE_KEY_MAP = {
    "EMAIL_MFA": "EMAIL_MFA_CODE",
    "EMAIL_OTP": "EMAIL_OTP_CODE",
    "SMS_MFA": "SMS_MFA_CODE",
    "SMS_OTP": "SMS_OTP_CODE",
    "SOFTWARE_TOKEN_MFA": "SOFTWARE_TOKEN_MFA_CODE",
    "NEW_PASSWORD_REQUIRED": "NEW_PASSWORD",
}
PASSWORD_CHALLENGE_EXCLUSION_KEYWORDS = ("otp", "verification", "email", "phone", "code")
WAF_ERROR_KEYWORDS = ("forbiddenexception", "waf")
WAF_BLOCK_PAGE_KEYWORDS = ("request blocked", "cloudfront", "<html")
USER_MIGRATION_KEYWORDS = ("user_not_found", "usernotfoundexception")


class MeuralAuthError(Exception):
    """An authentication failure safe to display to the operator."""


class CannotConnect(MeuralAuthError):
    pass


class InvalidAuth(MeuralAuthError):
    pass


class AuthenticationBlocked(MeuralAuthError):
    pass


@dataclass(frozen=True, repr=False)
class AuthResult:
    access_token: str
    refresh_token: str
    expires_at: float
    trust_id: str


class HttpError(Exception):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"Meural request failed (HTTP {status})")
        self.status = status
        # Providers sometimes echo request credentials. Keep only classification,
        # never their raw reply on an exception that a caller might log.
        text = json.dumps(body).lower()
        blocked = any(word in text for word in WAF_ERROR_KEYWORDS) or (
            status == 403 and any(word in text for word in WAF_BLOCK_PAGE_KEYWORDS)
        )
        self.body = {"message": "waf"} if blocked else {}


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_data: Any = None,
    body: bytes | None = None,
    timeout: float = 15,
) -> Any:
    request_headers = dict(headers or {})
    if json_data is not None:
        body = json.dumps(json_data).encode()
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with open_request(request, timeout=timeout) as response:
            raw = read_response(response)
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        with exc:
            raw = exc.read(16384)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            payload = {"message": raw.decode("utf-8", "replace")}
        raise HttpError(exc.code, payload) from None
    except (OSError, ValueError, urllib.error.URLError):
        raise CannotConnect("Could not connect to Meural or Netgear Accounts") from None


class NetgearAuthenticator:
    def __init__(self, trust_id: str | None = None) -> None:
        self.trust_id = trust_id or str(uuid.uuid4())

    def refresh(self, refresh_token: str) -> AuthResult:
        try:
            response = request_json(
                "GET",
                f"{NETGEAR_ACCOUNTS_URL}/api/getAccessToken",
                headers={
                    "Authorization": f"Bearer {refresh_token}",
                    "appkey": MEURAL_OAUTH_CLIENT_ID,
                    "Accept": "application/json",
                },
            )
        except HttpError as exc:
            self._raise_auth_error(exc)
        return self._parse_tokens(response, refresh_token)

    def exchange_cognito_token(self, access_token: str) -> AuthResult:
        query = urlencode({"client_id": MEURAL_OAUTH_CLIENT_ID})
        try:
            response = request_json(
                "GET",
                f"{NETGEAR_ACCOUNTS_URL}/api/oauth/authorize?{query}",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            data = self._unwrap(response)
            code = data.get("code") or data.get("authorizationCode")
            if not isinstance(code, str) or not code:
                raise InvalidAuth("Netgear did not return an authorization code")
            response = request_json(
                "GET",
                f"{NETGEAR_ACCOUNTS_URL}/api/oauth/token?{urlencode({'code': code})}",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        except HttpError as exc:
            self._raise_auth_error(exc)
        return self._parse_tokens(response)

    @staticmethod
    def _raise_auth_error(error: HttpError) -> None:
        text = json.dumps(error.body).lower()
        if any(word in text for word in WAF_ERROR_KEYWORDS) or (
            error.status == 403 and any(word in text for word in WAF_BLOCK_PAGE_KEYWORDS)
        ):
            raise AuthenticationBlocked(
                "Netgear blocked the request. Try signing in from a phone on mobile data."
            ) from None
        if error.status == 429 or error.status >= 500:
            raise CannotConnect("Netgear Accounts is temporarily unavailable") from None
        raise InvalidAuth("The Meural session has expired. Sign in again.") from None

    @staticmethod
    def _unwrap(response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise InvalidAuth("Netgear returned an invalid sign-in response")
        data = response.get("data", response)
        if not isinstance(data, dict):
            raise InvalidAuth("Netgear returned an invalid sign-in response")
        return data

    def _parse_tokens(self, response: Any, existing_refresh: str | None = None) -> AuthResult:
        data = self._unwrap(response)
        access = data.get("access_token") or data.get("accessToken") or data.get("token")
        refresh = data.get("refresh_token") or data.get("refreshToken") or existing_refresh
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise InvalidAuth("Netgear returned an incomplete Meural session")
        try:
            lifetime = float(data.get("expires_in", data.get("expiresIn", 3600)))
            if not math.isfinite(lifetime) or lifetime <= 0:
                lifetime = 3600
        except (ValueError, TypeError):
            lifetime = 3600
        expiry = time.time() + lifetime
        parts = access.split(".")
        if len(parts) == 3:
            try:
                claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
                claimed = float(claims["exp"])
                if math.isfinite(claimed):
                    expiry = claimed
            except (binascii.Error, KeyError, TypeError, ValueError):
                pass
        return AuthResult(access, refresh, expiry, self.trust_id)
