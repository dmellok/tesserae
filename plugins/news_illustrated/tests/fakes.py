"""Fakes for the news_illustrated tests: a scripted network, real picture
bytes and feed documents. No test touches the network."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest

from plugins.news_illustrated import net


@dataclass
class Call:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout: float

    def json(self) -> Any:
        return json.loads((self.body or b"").decode("utf-8"))


Answer = net.Reply | Callable[[Call], net.Reply]


class FakeNet:
    """Answers requests by URL prefix, in order; the last answer for a prefix
    repeats. Every call is recorded. An unrouted URL fails like a dead host."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, list[Answer]]] = []
        self.calls: list[Call] = []

    def on(self, prefix: str, *answers: Answer) -> FakeNet:
        self.routes.append((prefix, list(answers)))
        return self

    def __call__(
        self,
        url: str,
        *,
        timeout: float,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        max_bytes: int = 0,
    ) -> net.Reply:
        del max_bytes
        call = Call(method, url, dict(headers or {}), body, timeout)
        self.calls.append(call)
        for prefix, answers in self.routes:
            if url.startswith(prefix):
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                return answer(call) if callable(answer) else answer
        return net.Reply(0, error="could not connect (no route in the test)")

    def to(self, prefix: str) -> list[Call]:
        return [c for c in self.calls if c.url.startswith(prefix)]


def install(monkeypatch: pytest.MonkeyPatch, net_module: ModuleType) -> FakeNet:
    fake = FakeNet()
    monkeypatch.setattr(net_module, "request", fake)
    return fake


def picture(width: int = 768, height: int = 1376, fmt: str = "JPEG") -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 200, 200)).save(out, fmt)
    return out.getvalue()


def ok(body: bytes | str, content_type: str = "text/html; charset=utf-8") -> net.Reply:
    raw = body.encode("utf-8") if isinstance(body, str) else body
    return net.Reply(200, raw, content_type)


def ok_json(value: Any, status: int = 200) -> net.Reply:
    return net.Reply(status, json.dumps(value).encode("utf-8"), "application/json")


def rss(title: str, link: str, description: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>{title}</title><link>{link}</link>
<description><![CDATA[{description}]]></description></item>
<item><title>Older story</title><link>https://example.com/old</link></item>
</channel></rss>"""


def article_page(body: str, title: str = "A story") -> str:
    return (
        f"<html><head><title>{title}</title><script>var tracking = 1;</script></head>"
        f"<body><nav>Home News Sport</nav><article>{body}</article>"
        "<footer>Copyright footer</footer></body></html>"
    )
