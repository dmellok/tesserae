"""GPT Image at a chosen quality and size: the request fal.ai is sent, the price
the budget is charged, and the quality carried to OpenAI when it goes direct."""

from __future__ import annotations

from typing import Any

import pytest

from app import fal, openai_images


def test_default_request_is_unchanged() -> None:
    body = fal.request_body("gpt_image", "p", ["data:x"], "3:4")
    assert body["quality"] == "medium"
    assert body["image_size"] == "portrait_4_3"


def test_quality_and_exact_size_reach_the_request() -> None:
    body = fal.request_body("gpt_image", "p", ["data:x"], "3:4", quality="high", size=(1200, 1600))
    assert body["quality"] == "high"
    assert body["image_size"] == {"width": 1200, "height": 1600}


def test_size_rounds_to_multiples_of_sixteen() -> None:
    assert fal.size16((1199, 1601)) == {"width": 1200, "height": 1600}
    assert fal.size16((800, 480)) == {"width": 800, "height": 480}
    assert fal.size16((3, 3)) == {"width": 16, "height": 16}


def test_unknown_quality_falls_back_to_medium() -> None:
    assert fal.request_body("gpt_image", "p", ["data:x"], quality="ultra")["quality"] == "medium"


def test_other_models_ignore_quality_and_size() -> None:
    plain = fal.request_body("nano_banana", "p", ["data:x"], "3:4")
    asked = fal.request_body(
        "nano_banana", "p", ["data:x"], "3:4", quality="high", size=(1200, 1600)
    )
    assert plain == asked


def test_price_follows_quality() -> None:
    assert fal.cents_of("gpt_image") == 5
    assert fal.cents_of("gpt_image", "high") == 20
    assert fal.cents_of("nano_banana", "high") == fal.cents_of("nano_banana")


def test_generate_charges_the_high_price(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def fake_http(url: str, body: dict[str, Any], key: str, timeout: float) -> Any:
        sent.update(body)
        return {"images": [{"url": "https://example.test/p.jpg"}]}

    monkeypatch.setattr(fal, "_http_json", fake_http)
    monkeypatch.setattr(fal, "_download", lambda url: b"jpeg")
    charged: list[int] = []
    client = fal.FalClient(
        "key", charge=lambda c, m: charged.append(c), openai_key="", gemini_key=""
    )
    raw = client.generate(
        "gpt_image", "p", refs=["data:x"], aspect="3:4", quality="high", size=(1200, 1600)
    )
    assert raw == b"jpeg"
    assert charged == [20] and client.spent_cents == 20
    assert sent["quality"] == "high" and sent["image_size"] == {"width": 1200, "height": 1600}


def test_direct_openai_gets_the_quality(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_generate(key: str, prompt: str, **kw: Any) -> bytes:
        seen.update(kw)
        return b"jpeg"

    monkeypatch.setattr(openai_images, "generate", fake_generate)
    charged: list[int] = []
    client = fal.FalClient(
        "", charge=lambda c, m: charged.append(c), openai_key="sk", gemini_key=""
    )
    client.generate("gpt_image", "p", refs=["data:x"], aspect="3:4", quality="high")
    assert seen["quality"] == "high"
    assert charged == [fal.cents_of("gpt_image_openai", "high")]
