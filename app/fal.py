"""One fal.ai client for everything in Tesserae that makes a picture.

The widgets that paint (Specimen Sheet, Overhead, Backyard Birds), the Panels
backgrounds and the fal-image widget each carried a client of their own with
the same model table and the same request shapes. This is that table and
those shapes once, with the key resolved the same way for all of them: the
widget's own ``fal_api_key`` setting when it has one, else the app-level key
under Settings (``app.fal.api_key``), else ``FAL_KEY`` in the environment.

A :class:`FalClient` is bound to a key and to a ``charge`` callback, so a
budget (``app.artwork``) is debited the model's listed price on every call
before the request goes out: a painting that is paid for is counted whether
or not it comes back.

GPT Image can also go straight to OpenAI (:mod:`app.openai_images`): with an
OpenAI key set, a ``gpt_image`` call is sent there and charged at OpenAI's
price. Nano Banana can likewise go straight to Google
(:mod:`app.gemini_images`): with a Gemini key set, ``nano_banana``,
``nano_banana_pro`` and ``nano_banana_t2i`` calls are sent there. With no
fal.ai key at all, every edit or text-to-picture call falls back to Nano
Banana on Google when there is a Gemini key, else GPT Image on OpenAI.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

API_BASE = "https://fal.run"
USER_AGENT = "tesserae/artwork"
TIMEOUT_S = 120.0

# Model id -> endpoint, label, listed price in US cents (October 2026), the
# kind of call ("edit" paints from reference pictures, "t2i" from words
# alone, "mask" returns the picture with its background removed) and how
# many reference pictures the endpoint takes.
MODELS: dict[str, dict[str, Any]] = {
    "nano_banana": {
        "endpoint": "fal-ai/nano-banana/edit",
        "label": "Nano Banana",
        "cents": 4,
        "kind": "edit",
        "refs": 4,
    },
    "nano_banana_pro": {
        "endpoint": "fal-ai/nano-banana-pro/edit",
        "label": "Nano Banana Pro",
        "cents": 15,
        "kind": "edit",
        "refs": 4,
    },
    "flux_kontext": {
        "endpoint": "fal-ai/flux-pro/kontext",
        "label": "FLUX.1 Kontext Pro",
        "cents": 4,
        "kind": "edit",
        "refs": 1,
    },
    "gpt_image": {
        "endpoint": "openai/gpt-image-2/edit",
        "label": "GPT Image 2",
        "cents": 5,
        "kind": "edit",
        "refs": 4,
    },
    "seedream": {
        "endpoint": "fal-ai/bytedance/seedream/v4/edit",
        "label": "Seedream 4",
        "cents": 3,
        "kind": "edit",
        "refs": 4,
    },
    "nano_banana_t2i": {
        "endpoint": "fal-ai/nano-banana",
        "label": "Nano Banana",
        "cents": 4,
        "kind": "t2i",
        "refs": 0,
    },
    "flux_schnell": {
        "endpoint": "fal-ai/flux/schnell",
        "label": "FLUX.1 Schnell",
        "cents": 1,
        "kind": "t2i",
        "refs": 0,
    },
    # GPT Image sent straight to OpenAI rather than relayed by fal.ai. Not
    # offered in pickers (``direct`` names the provider); a client routes to
    # them on its own. Prices: medium quality at 1536x1024 plus the reference
    # pictures' input tokens, rounded up (October 2026).
    "gpt_image_openai": {
        "endpoint": "openai:images/edits",
        "label": "GPT Image 2 (OpenAI)",
        "cents": 8,
        "kind": "edit",
        "refs": 16,
        "direct": "openai",
    },
    "gpt_image_openai_t2i": {
        "endpoint": "openai:images/generations",
        "label": "GPT Image 2 (OpenAI)",
        "cents": 6,
        "kind": "t2i",
        "refs": 0,
        "direct": "openai",
    },
    # Nano Banana sent straight to Google the same way. Prices: one 1K
    # picture plus the reference pictures' input tokens, rounded up
    # (October 2026).
    "nano_banana_gemini": {
        "endpoint": "gemini:gemini-2.5-flash-image",
        "label": "Nano Banana (Google)",
        "cents": 4,
        "kind": "edit",
        "refs": 3,
        "direct": "gemini",
    },
    "nano_banana_pro_gemini": {
        "endpoint": "gemini:gemini-3-pro-image",
        "label": "Nano Banana Pro (Google)",
        "cents": 14,
        "kind": "edit",
        "refs": 6,
        "direct": "gemini",
    },
    "nano_banana_gemini_t2i": {
        "endpoint": "gemini:gemini-2.5-flash-image",
        "label": "Nano Banana (Google)",
        "cents": 4,
        "kind": "t2i",
        "refs": 0,
        "direct": "gemini",
    },
    "birefnet": {
        "endpoint": "fal-ai/birefnet/v2",
        "label": "BiRefNet",
        "cents": 2,
        "kind": "mask",
        "refs": 1,
    },
}
DEFAULT_EDIT_MODEL = "nano_banana"
EDIT_MODELS = tuple(k for k, v in MODELS.items() if v["kind"] == "edit" and not v.get("direct"))
# What :func:`painting_key` answers when there is no fal.ai key but an OpenAI
# or Gemini key: truthy, so every "is there a key" gate passes, and a client
# built with it knows it has no fal.ai key.
DIRECT_ONLY = "direct-only"
OPENAI_ONLY = DIRECT_ONLY
# Which direct model a fal.ai model goes to when its provider has a key.
_SAME_ON = {
    "gemini": {
        "nano_banana": "nano_banana_gemini",
        "nano_banana_pro": "nano_banana_pro_gemini",
        "nano_banana_t2i": "nano_banana_gemini_t2i",
    },
    "openai": {"gpt_image": "gpt_image_openai"},
}
# What any other edit or text-to-picture call goes to with no fal.ai key.
_FALLBACK = {
    "gemini": {"edit": "nano_banana_gemini", "t2i": "nano_banana_gemini_t2i"},
    "openai": {"edit": "gpt_image_openai", "t2i": "gpt_image_openai_t2i"},
}


class FalError(Exception):
    """A call that did not produce a picture, with a sentence for the widget."""


def model_of(model: str | None, default: str = DEFAULT_EDIT_MODEL) -> str:
    return model if model in MODELS else default


def cents_of(model: str | None) -> int:
    return int(MODELS.get(model_of(model), MODELS[DEFAULT_EDIT_MODEL])["cents"])


def label_of(model: str | None) -> str:
    return str(MODELS.get(model_of(model), MODELS[DEFAULT_EDIT_MODEL])["label"])


def resolve_key(settings: dict[str, Any] | None = None) -> str:
    """The key to paint with: the widget's own setting, else the app-level
    key under Settings, else the environment. Empty when there is none."""
    own = str((settings or {}).get("fal_api_key") or "").strip()
    if own:
        return own
    try:
        from flask import current_app

        store = current_app.config.get("SETTINGS_STORE")
        if store is not None:
            vals = store.get_for_runtime("app", "fal", [{"name": "api_key", "secret": True}])
            key = str(vals.get("api_key") or "").strip()
            if key:
                return key
    except Exception:
        pass
    return os.environ.get("FAL_KEY", "").strip() or os.environ.get("FAL_API_KEY", "").strip()


def painting_key(settings: dict[str, Any] | None = None) -> str:
    """What a painting is started with: the fal.ai key from
    :func:`resolve_key`, else :data:`DIRECT_ONLY` when an OpenAI or Gemini
    key is set (every picture then goes straight to one of them), else empty."""
    key = resolve_key(settings)
    if key:
        return key
    from app import gemini_images, openai_images

    return DIRECT_ONLY if (gemini_images.resolve_key() or openai_images.resolve_key()) else ""


def request_body(
    model: str, prompt: str, refs: Sequence[str], aspect: str = "4:3"
) -> dict[str, Any]:
    """The request each endpoint expects. ``refs`` are URLs or data URIs."""
    urls = list(refs)
    portrait = aspect in ("3:4", "9:16", "2:3")
    kind = MODELS[model]["kind"]
    if kind == "mask":
        return {
            "image_url": urls[0],
            "model": "General Use (Light)",
            "operating_resolution": "1024x1024",
            "output_format": "png",
            "refine_foreground": True,
        }
    if kind == "t2i":
        if model == "flux_schnell":
            return {
                "prompt": prompt,
                "image_size": "portrait_4_3" if portrait else "landscape_4_3",
                "num_images": 1,
                "num_inference_steps": 4,
            }
        return {"prompt": prompt, "num_images": 1, "output_format": "jpeg", "aspect_ratio": aspect}
    if model == "flux_kontext":
        return {
            "prompt": prompt,
            "image_url": urls[0],
            "num_images": 1,
            "output_format": "jpeg",
            "aspect_ratio": aspect,
        }
    if model == "gpt_image":
        return {
            "prompt": prompt,
            "image_urls": urls,
            "image_size": "portrait_4_3" if portrait else "landscape_4_3",
            "background": "opaque",
            "quality": "medium",
            "output_format": "jpeg",
            "num_images": 1,
        }
    if model == "seedream":
        return {
            "prompt": prompt,
            "image_urls": urls,
            "image_size": "portrait_4_3" if portrait else "landscape_4_3",
            "num_images": 1,
        }
    body: dict[str, Any] = {
        "prompt": prompt,
        "image_urls": urls,
        "num_images": 1,
        "output_format": "jpeg",
        "aspect_ratio": aspect,
    }
    if model == "nano_banana_pro":
        body["resolution"] = "1K"
    return body


def pick_image_url(payload: Any) -> str:
    """The first picture URL in a fal answer, whatever shape the model used."""
    if not isinstance(payload, dict):
        return ""
    images = payload.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"])
        if isinstance(first, str):
            return first
    image = payload.get("image")
    if isinstance(image, dict) and image.get("url"):
        return str(image["url"])
    if isinstance(image, str):
        return image
    return ""


def data_uri(raw: bytes) -> str:
    kind = "png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "jpeg"
    return f"data:image/{kind};base64," + base64.b64encode(raw).decode()


def _http_json(url: str, body: dict[str, Any], key: str, timeout: float) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Key {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return bytes(resp.read())


def _explain(err: Exception) -> str:
    if isinstance(err, urllib.error.HTTPError):
        detail = ""
        with contextlib.suppress(Exception):
            detail = err.read(600).decode("utf-8", "replace")
        if err.code in (401, 403):
            return f"fal.ai refused the key or the account has no balance (HTTP {err.code})"
        if err.code == 422:
            return f"fal.ai refused the request (HTTP 422: {detail[:200]})"
        return f"fal.ai answered HTTP {err.code}{': ' + detail[:200] if detail else ''}"
    if isinstance(err, urllib.error.URLError):
        return f"fal.ai could not be reached ({err.reason})"
    if isinstance(err, TimeoutError):
        return "fal.ai took too long to answer"
    return f"{type(err).__name__}: {str(err)[:200]}"


class FalClient:
    """A key and a budget to charge, with one ``generate`` for every model.

    ``charge(cents, model)`` is called before each request with the model's
    listed price; raise from it to refuse the call (the day's budget is spent)."""

    def __init__(
        self,
        key: str,
        *,
        charge: Callable[[int, str], None] | None = None,
        stop: threading.Event | None = None,
        timeout: float = TIMEOUT_S,
        openai_key: str | None = None,
        gemini_key: str | None = None,
    ) -> None:
        self.key = "" if key == DIRECT_ONLY else key
        if openai_key is None:
            from app import openai_images

            openai_key = openai_images.resolve_key()
        if gemini_key is None:
            from app import gemini_images

            gemini_key = gemini_images.resolve_key()
        self.openai_key = openai_key
        self.gemini_key = gemini_key
        self._charge = charge
        self._stop = stop
        self._timeout = timeout
        self.spent_cents = 0

    @property
    def stopped(self) -> bool:
        return self._stop is not None and self._stop.is_set()

    def generate(
        self,
        model: str,
        prompt: str = "",
        *,
        refs: Sequence[str] = (),
        aspect: str = "4:3",
        retries: int = 1,
    ) -> bytes:
        """One picture from ``model``: a JPEG or PNG as bytes. Raises
        :class:`FalError` with a sentence when there is none."""
        model = model_of(model)
        spec = MODELS[model]
        if spec["kind"] != "t2i" and not refs:
            raise FalError("no reference picture to paint from")
        direct = self.direct_model(model)
        if direct is not None:
            return self._generate_direct(direct, prompt, refs, aspect, retries)
        if not self.key:
            raise FalError(
                "no fal.ai key"
                + (
                    f" ({label_of(model)} is only on fal.ai)"
                    if self.openai_key or self.gemini_key
                    else ""
                )
            )
        refs = list(refs)[: max(1, int(spec["refs"]))] if spec["refs"] else []
        body = request_body(model, prompt, refs, aspect)
        url = f"{API_BASE}/{spec['endpoint']}"
        last = "no answer"
        for _attempt in range(retries + 1):
            if self.stopped:
                raise FalError("stopped")
            if self._charge is not None:
                self._charge(int(spec["cents"]), model)
            self.spent_cents += int(spec["cents"])
            try:
                answer = _http_json(url, body, self.key, self._timeout)
            except Exception as err:
                last = _explain(err)
                if isinstance(err, urllib.error.HTTPError) and err.code in (401, 403):
                    raise FalError(last) from err
                continue
            if self.stopped:
                raise FalError("stopped")
            out = pick_image_url(answer)
            if not out:
                last = "fal.ai sent no picture"
                continue
            try:
                raw = _download(out)
            except Exception as err:
                last = f"the picture could not be fetched ({_explain(err)})"
                continue
            if raw:
                return raw
            last = "fal.ai sent an empty picture"
        raise FalError(last if retries == 0 else f"{last} (after {retries + 1} tries)")

    def _direct_key(self, provider: str) -> str:
        return {"openai": self.openai_key, "gemini": self.gemini_key}.get(provider) or ""

    def direct_model(self, model: str) -> str | None:
        """The direct model a call for ``model`` goes to, or None for fal.ai:
        Nano Banana on Google whenever there is a Gemini key, GPT Image on
        OpenAI whenever there is an OpenAI key, and any other edit or
        text-to-picture model when there is no fal.ai key to run it (Google
        first, else OpenAI)."""
        model = model_of(model)
        spec = MODELS[model]
        if spec.get("direct"):
            return model
        for provider, same in _SAME_ON.items():
            if model in same and self._direct_key(provider):
                return same[model]
        if not self.key:
            for provider, fallback in _FALLBACK.items():
                if self._direct_key(provider) and spec["kind"] in fallback:
                    return fallback[spec["kind"]]
        return None

    def _generate_direct(
        self, model: str, prompt: str, refs: Sequence[str], aspect: str, retries: int
    ) -> bytes:
        from app import gemini_images, openai_images

        spec = MODELS[model]
        provider = str(spec["direct"])
        key = self._direct_key(provider)
        refs = list(refs)[: int(spec["refs"])] if spec["refs"] else []
        last = "no answer"
        for _attempt in range(retries + 1):
            if self.stopped:
                raise FalError("stopped")
            if self._charge is not None:
                self._charge(int(spec["cents"]), model)
            self.spent_cents += int(spec["cents"])
            try:
                if provider == "gemini":
                    raw = gemini_images.generate(
                        key,
                        prompt,
                        refs=refs,
                        aspect=aspect,
                        model=str(spec["endpoint"]).removeprefix("gemini:"),
                    )
                else:
                    raw = openai_images.generate(key, prompt, refs=refs, aspect=aspect)
            except (openai_images.OpenAIError, gemini_images.GeminiError) as err:
                last = str(err)
                if err.auth:
                    raise FalError(last) from err
                continue
            if self.stopped:
                raise FalError("stopped")
            return raw
        raise FalError(last if retries == 0 else f"{last} (after {retries + 1} tries)")
