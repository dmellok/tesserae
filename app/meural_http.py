"""Bounded Meural replies without forwarding credentials through redirects."""

from __future__ import annotations

import http.client
import urllib.request
from typing import Any

MAX_RESPONSE_BYTES = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


class _LowercaseHeaderConnection(http.client.HTTPSConnection):
    # urllib title-cases header names ("appkey" goes out as "Appkey"), and Netgear
    # Accounts reads its app key header case-sensitively. Send names as browsers do.
    def putheader(self, header: str | bytes, *values: Any) -> None:
        super().putheader(header.lower(), *values)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: Any) -> Any:
        context = getattr(self, "_context", None)
        return self.do_open(_LowercaseHeaderConnection, req, context=context)


open_request = urllib.request.build_opener(NoRedirect(), _HTTPSHandler()).open


def read_response(response: Any) -> bytes:
    raw: bytes = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Meural response is too large")
    return raw
