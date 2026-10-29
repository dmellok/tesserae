"""GPT Image straight from OpenAI: the Images API client, the routing inside
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

from app import fal, genai, openai_images
from app.main import REPO_ROOT, create_app

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
REF = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nref").decode()


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "FAL_KEY", "FAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(openai_images, "_BOUND_STORE", None)


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every Images API call and answer with one JPEG."""
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, body: bytes, content_type: str, key: str, timeout: float) -> Any:
        calls.append({"url": url, "body": body, "type": content_type, "key": key})
        return {"data": [{"b64_json": base64.b64encode(JPEG).decode()}]}

    monkeypatch.setattr(openai_images, "_post", fake_post)
    return calls


def test_an_edit_goes_as_multipart_with_every_reference(posted: list[dict[str, Any]]) -> None:
    raw = openai_images.generate("sk-test", "an ink ship", refs=[REF, REF], aspect="3:4")
    assert raw == JPEG
    (call,) = posted
    assert call["url"].endswith("/images/edits") and call["key"] == "sk-test"
    assert call["type"].startswith("multipart/form-data; boundary=")
    body = call["body"]
    assert body.count(b'name="image[]"') == 2 and b"\x89PNG" in body
    assert b'name="model"\r\n\r\ngpt-image-2' in body
    assert b'name="size"\r\n\r\n1024x1536' in body


def test_words_alone_go_to_generations_as_json(posted: list[dict[str, Any]]) -> None:
    openai_images.generate("sk-test", "a heron at dawn", aspect="16:9")
    (call,) = posted
    assert call["url"].endswith("/images/generations") and call["type"] == "application/json"
    body = json.loads(call["body"])
    assert body["model"] == "gpt-image-2" and body["size"] == "1536x1024" and body["n"] == 1


def test_a_url_reference_is_fetched_through_the_outbound_guard(
    posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import net_guard

    seen: list[str] = []

    def fake_fetch(url: str, **kw: Any) -> tuple[bytes, str]:
        seen.append(url)
        return b"photo", "image/jpeg"

    monkeypatch.setattr(net_guard, "fetch_bytes", fake_fetch)
    openai_images.generate("sk-test", "p", refs=["https://upload.wikimedia.org/a.jpg"])
    assert seen == ["https://upload.wikimedia.org/a.jpg"]
    assert b"photo" in posted[0]["body"]


def test_a_refused_key_is_marked_so_no_retry_is_wasted(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> Any:
        raise urllib.error.HTTPError(
            "u", 401, "no", {}, io.BytesIO(b'{"error":{"message":"bad key"}}')
        )

    monkeypatch.setattr(openai_images, "_post", refuse)
    with pytest.raises(openai_images.OpenAIError) as err:
        openai_images.generate("sk-bad", "p")
    assert err.value.auth and "refused the key" in str(err.value)


@pytest.mark.parametrize(
    ("aspect", "size"), [("1:1", "1024x1024"), ("2:3", "1024x1536"), ("4:3", "1536x1024")]
)
def test_sizes_follow_the_aspect(aspect: str, size: str) -> None:
    assert openai_images.size_for(aspect) == size


# -- routing in the shared client --------------------------------------------


def _client(fal_key: str, openai_key: str, charged: list[tuple[int, str]]) -> fal.FalClient:
    return fal.FalClient(fal_key, openai_key=openai_key, charge=lambda c, m: charged.append((c, m)))


def test_gpt_image_goes_to_openai_when_there_is_an_openai_key(
    posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fal, "_http_json", lambda *a, **k: pytest.fail("went to fal.ai"))
    charged: list[tuple[int, str]] = []
    raw = _client("fal-key", "sk-test", charged).generate("gpt_image", "p", refs=[REF])
    assert raw == JPEG and posted
    assert charged == [(fal.cents_of("gpt_image_openai"), "gpt_image_openai")]
    assert fal.label_of("gpt_image_openai") == "GPT Image 2 (OpenAI)"


def test_other_models_stay_on_fal_when_there_is_a_fal_key(
    posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fal, "_http_json", lambda *a, **k: {"images": [{"url": "https://x/y"}]})
    monkeypatch.setattr(fal, "_download", lambda url, timeout=60.0: JPEG)
    charged: list[tuple[int, str]] = []
    _client("fal-key", "sk-test", charged).generate("nano_banana", "p", refs=[REF])
    assert posted == [] and charged == [(fal.cents_of("nano_banana"), "nano_banana")]


def test_with_only_an_openai_key_every_painting_uses_gpt_image(
    posted: list[dict[str, Any]],
) -> None:
    charged: list[tuple[int, str]] = []
    client = _client(fal.OPENAI_ONLY, "sk-test", charged)
    client.generate("nano_banana", "p", refs=[REF])
    client.generate("flux_schnell", "p")
    assert [c[1] for c in charged] == ["gpt_image_openai", "gpt_image_openai_t2i"]
    assert posted[0]["url"].endswith("/edits") and posted[1]["url"].endswith("/generations")
    # A background cut-out has no OpenAI equivalent.
    with pytest.raises(fal.FalError, match=r"only on fal\.ai"):
        client.generate("birefnet", "", refs=[REF])


def test_no_key_at_all_is_still_refused() -> None:
    with pytest.raises(fal.FalError, match=r"no fal\.ai key"):
        fal.FalClient("", openai_key="").generate("nano_banana", "p", refs=[REF])


def test_direct_models_are_not_offered_in_pickers() -> None:
    assert "gpt_image" in fal.EDIT_MODELS
    assert "gpt_image_openai" not in fal.EDIT_MODELS


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


def test_painting_key_accepts_an_openai_only_install(app: Flask) -> None:
    store = app.config["SETTINGS_STORE"]
    with app.app_context():
        assert fal.painting_key({}) == ""
        genai.save(store, {"openai_api_key": "sk-test"})
        assert fal.painting_key({}) == fal.OPENAI_ONLY
        assert fal.painting_key({"fal_api_key": "own"}) == "own"
    # A painting thread without an app context still finds the key.
    assert openai_images.resolve_key() == "sk-test"


def test_the_page_saves_checks_and_masks_the_openai_key(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    page = client.get("/settings/genai").get_data(as_text=True)
    assert 'name="openai_api_key"' in page and "Optional" in page
    monkeypatch.setattr(openai_images, "check_key", lambda key: (key == "sk-good", "checked"))
    resp = client.post("/settings/genai/check-openai", data={"openai_api_key": "sk-good"})
    assert resp.status_code == 302 and "ok_openai=1" in resp.location
    store = app.config["SETTINGS_STORE"]
    assert store.get_for_runtime("app", "openai", openai_images.FIELDS)["api_key"] == "sk-good"
    page = client.get("/settings/genai").get_data(as_text=True)
    assert "sk-good" not in page and "GPT Image goes straight to OpenAI" in page
    # A refused key is not saved.
    client.post("/settings/genai/check-openai", data={"openai_api_key": "sk-bad"})
    assert store.get_for_runtime("app", "openai", openai_images.FIELDS)["api_key"] == "sk-good"
