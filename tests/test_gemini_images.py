"""Nano Banana straight from Google: the Gemini API client, the routing inside
:class:`app.fal.FalClient`, and the key under Settings › Generative AI.
No network: every call is faked."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import fal, gemini_images, genai, openai_images
from app.main import REPO_ROOT, create_app

PNG = b"\x89PNG\r\n\x1a\nfake-png"
REF = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nref").decode()


def _answer(raw: bytes = PNG) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Here is your picture."},
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64.b64encode(raw).decode(),
                            }
                        },
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "FAL_KEY", "FAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(openai_images, "_BOUND_STORE", None)
    monkeypatch.setattr(gemini_images, "_BOUND_STORE", None)


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every generateContent call and answer with one PNG."""
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, body: bytes, key: str, timeout: float) -> Any:
        calls.append({"url": url, "body": json.loads(body), "key": key})
        return _answer()

    monkeypatch.setattr(gemini_images, "_post", fake_post)
    return calls


def test_an_edit_sends_every_reference_inline(posted: list[dict[str, Any]]) -> None:
    raw = gemini_images.generate("AIza-test", "an ink ship", refs=[REF, REF], aspect="3:4")
    assert raw == PNG
    (call,) = posted
    assert call["url"].endswith("/models/gemini-2.5-flash-image:generateContent")
    assert call["key"] == "AIza-test"
    parts = call["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "an ink ship"}
    assert [p["inline_data"]["mime_type"] for p in parts[1:]] == ["image/png", "image/png"]
    config = call["body"]["generationConfig"]
    assert "IMAGE" in config["responseModalities"]
    assert config["imageConfig"] == {"aspectRatio": "3:4"}


def test_pro_asks_for_a_1k_picture(posted: list[dict[str, Any]]) -> None:
    gemini_images.generate("k", "p", refs=[REF], model=gemini_images.MODEL_PRO, aspect="7:5")
    (call,) = posted
    assert call["url"].endswith("/models/gemini-3-pro-image:generateContent")
    assert call["body"]["generationConfig"]["imageConfig"] == {
        "aspectRatio": "4:3",
        "imageSize": "1K",
    }


def test_a_url_reference_is_fetched_through_the_outbound_guard(
    posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import net_guard

    seen: list[str] = []

    def fake_fetch(url: str, **kw: Any) -> tuple[bytes, str]:
        seen.append(url)
        return b"photo", "image/jpeg"

    monkeypatch.setattr(net_guard, "fetch_bytes", fake_fetch)
    gemini_images.generate("k", "p", refs=["https://upload.wikimedia.org/a.jpg"])
    assert seen == ["https://upload.wikimedia.org/a.jpg"]
    inline = posted[0]["body"]["contents"][0]["parts"][1]["inline_data"]
    assert inline == {"mime_type": "image/jpeg", "data": base64.b64encode(b"photo").decode()}


@pytest.mark.parametrize(
    ("answer", "match"),
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, r"declined the prompt \(SAFETY\)"),
        (
            {
                "candidates": [
                    {"content": {"parts": [{"text": "no"}]}, "finishReason": "IMAGE_SAFETY"}
                ]
            },
            "IMAGE_SAFETY",
        ),
        ({"candidates": []}, "sent no picture"),
    ],
)
def test_an_answer_without_a_picture_says_why(answer: dict[str, Any], match: str) -> None:
    with pytest.raises(gemini_images.GeminiError, match=match):
        gemini_images.pick_image(answer)


@pytest.mark.parametrize("code", [400, 403])
def test_a_refused_key_is_marked_so_no_retry_is_wasted(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    def refuse(*_a: Any, **_k: Any) -> Any:
        body = b'{"error":{"message":"API key not valid. Please pass a valid API key."}}'
        raise urllib.error.HTTPError("u", code, "no", {}, io.BytesIO(body))  # type: ignore[arg-type]

    monkeypatch.setattr(gemini_images, "_post", refuse)
    with pytest.raises(gemini_images.GeminiError) as err:
        gemini_images.generate("bad", "p")
    assert err.value.auth and "refused the key" in str(err.value)


# -- routing in the shared client --------------------------------------------


def _client(
    fal_key: str, gemini_key: str, charged: list[tuple[int, str]], openai_key: str = ""
) -> fal.FalClient:
    return fal.FalClient(
        fal_key,
        openai_key=openai_key,
        gemini_key=gemini_key,
        charge=lambda c, m: charged.append((c, m)),
    )


def test_nano_banana_goes_to_google_when_there_is_a_gemini_key(
    posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fal, "_http_json", lambda *a, **k: pytest.fail("went to fal.ai"))
    charged: list[tuple[int, str]] = []
    client = _client("fal-key", "AIza-test", charged)
    assert client.generate("nano_banana", "p", refs=[REF]) == PNG
    client.generate("nano_banana_pro", "p", refs=[REF])
    client.generate("nano_banana_t2i", "p")
    assert [m for _c, m in charged] == [
        "nano_banana_gemini",
        "nano_banana_pro_gemini",
        "nano_banana_gemini_t2i",
    ]
    assert "gemini-3-pro-image" in posted[1]["url"]
    assert fal.label_of("nano_banana_pro_gemini") == "Nano Banana Pro (Google)"


def test_other_models_stay_on_fal_when_there_is_a_fal_key(
    posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fal, "_http_json", lambda *a, **k: {"images": [{"url": "https://x/y"}]})
    monkeypatch.setattr(fal, "_download", lambda url, timeout=60.0: PNG)
    charged: list[tuple[int, str]] = []
    _client("fal-key", "AIza-test", charged).generate("seedream", "p", refs=[REF])
    assert posted == [] and charged == [(fal.cents_of("seedream"), "seedream")]


def test_with_only_a_gemini_key_every_painting_uses_nano_banana(
    posted: list[dict[str, Any]],
) -> None:
    charged: list[tuple[int, str]] = []
    client = _client(fal.DIRECT_ONLY, "AIza-test", charged)
    client.generate("gpt_image", "p", refs=[REF])
    client.generate("flux_schnell", "p")
    assert [m for _c, m in charged] == ["nano_banana_gemini", "nano_banana_gemini_t2i"]
    with pytest.raises(fal.FalError, match=r"only on fal\.ai"):
        client.generate("birefnet", "", refs=[REF])


def test_with_both_direct_keys_gpt_image_still_goes_to_openai(
    posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        openai_images,
        "_post",
        lambda *a, **k: {"data": [{"b64_json": base64.b64encode(PNG).decode()}]},
    )
    charged: list[tuple[int, str]] = []
    client = _client(fal.DIRECT_ONLY, "AIza-test", charged, openai_key="sk-test")
    client.generate("gpt_image", "p", refs=[REF])
    client.generate("seedream", "p", refs=[REF])
    assert [m for _c, m in charged] == ["gpt_image_openai", "nano_banana_gemini"]


def test_direct_models_are_not_offered_in_pickers() -> None:
    assert "nano_banana" in fal.EDIT_MODELS
    assert not any(m.endswith("_gemini") for m in fal.EDIT_MODELS)


# -- the key under Settings ---------------------------------------------------


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def test_painting_key_accepts_a_gemini_only_install(app: Flask) -> None:
    store = app.config["SETTINGS_STORE"]
    with app.app_context():
        assert fal.painting_key({}) == ""
        genai.save(store, {"gemini_api_key": "AIza-test"})
        assert fal.painting_key({}) == fal.DIRECT_ONLY
    # A painting thread without an app context still finds the key.
    assert gemini_images.resolve_key() == "AIza-test"


def test_the_page_saves_checks_and_masks_the_gemini_key(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    page = client.get("/settings/genai").get_data(as_text=True)
    assert 'name="gemini_api_key"' in page
    monkeypatch.setattr(gemini_images, "check_key", lambda key: (key == "AIza-good", "checked"))
    resp = client.post("/settings/genai/check-gemini", data={"gemini_api_key": "AIza-good"})
    assert resp.status_code == 302 and "ok_gemini=1" in resp.location
    store = app.config["SETTINGS_STORE"]
    assert store.get_for_runtime("app", "gemini", gemini_images.FIELDS)["api_key"] == "AIza-good"
    page = client.get("/settings/genai").get_data(as_text=True)
    assert (
        "AIza-good" not in page and "Nano Banana and Nano Banana Pro go straight to Google" in page
    )
    # A refused key is not saved.
    client.post("/settings/genai/check-gemini", data={"gemini_api_key": "AIza-bad"})
    assert store.get_for_runtime("app", "gemini", gemini_images.FIELDS)["api_key"] == "AIza-good"
