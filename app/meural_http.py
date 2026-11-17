"""Bounded Meural replies without forwarding credentials through redirects."""

from __future__ import annotations

import urllib.request
from typing import Any

MAX_RESPONSE_BYTES = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


open_request = urllib.request.build_opener(NoRedirect()).open


def read_response(response: Any) -> bytes:
    raw: bytes = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Meural response is too large")
    return raw
