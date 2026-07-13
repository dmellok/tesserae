"""The fal.ai background service (app.fal_backgrounds).

The network round-trip is stubbed; these cover prompt assembly, per-model
request bodies, key resolution precedence, the PNG re-encode, and storage."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app import fal_backgrounds as fb


def test_build_prompt_prepends_style_and_appends_suffix() -> None:
    p = fb.build_prompt("a lake", "watercolor", eink_friendly=True)
    assert p.startswith("watercolor painting")
    assert "a lake" in p
    assert "high contrast" in p  # e-ink suffix
    # No style, no suffix: the prompt is untouched.
    assert fb.build_prompt("a lake", "none", eink_friendly=False) == "a lake"


def test_build_body_nano_uses_aspect_ratio() -> None:
    body = fb._build_body("fal-ai/nano-banana", "p", 800, 480, 7)
    assert "aspect_ratio" in body and "image_size" not in body and "seed" not in body


def test_build_body_flux_uses_image_size_clamped() -> None:
    body = fb._build_body("fal-ai/flux/schnell", "p", 800, 480, 7)
    assert body["image_size"] == {"width": 800, "height": 480} and body["seed"] == 7
    # Clamp to [256, 1536] and round to /16.
    big = fb._build_body("fal-ai/flux/schnell", "p", 99999, 100, 1)
    assert big["image_size"] == {"width": 1536, "height": 256}


def test_resolve_key_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)
    assert fb.resolve_fal_key(None, None) is None
    monkeypatch.setenv("FAL_KEY", "env-key")
    assert fb.resolve_fal_key(None, None) == "env-key"


def test_generate_reencodes_to_png(monkeypatch: pytest.MonkeyPatch) -> None:
    # fal often returns jpeg/webp; the service re-encodes to PNG.
    raw = io.BytesIO()
    Image.new("RGB", (64, 48), "blue").save(raw, format="JPEG")
    monkeypatch.setattr(fb, "_fal_request", lambda m, b, k: {"images": [{"url": "http://x/y.jpg"}]})
    monkeypatch.setattr(fb, "_download", lambda url: raw.getvalue())
    out = fb.generate("a sea", api_key="k", model="fal-ai/flux/schnell", width=800, height=480)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_generate_no_image_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fb, "_fal_request", lambda m, b, k: {"nope": 1})
    with pytest.raises(fb.FalError):
        fb.generate("x", api_key="k", width=800, height=480)


def test_generate_rejects_unknown_model() -> None:
    with pytest.raises(fb.FalError):
        fb.generate("x", api_key="k", model="fal-ai/not-a-model", width=800, height=480)


def test_store_background_content_addressed(tmp_path: Path) -> None:
    url = fb.store_background(tmp_path, b"\x89PNG-data")
    assert url.startswith("/renders/") and url.endswith(".png")
    fname = url.split("/renders/")[1]
    assert (tmp_path / fname).read_bytes() == b"\x89PNG-data"
    # Same bytes → same digest → same URL (idempotent).
    assert fb.store_background(tmp_path, b"\x89PNG-data") == url
