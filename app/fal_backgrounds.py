"""AI-generated canvas backgrounds via fal.ai (Approach A: composite).

Generates a full-bleed background image from a text prompt and stores it as a
local render asset, so a canvas dashboard can set it as ``bg_image`` and paint
its data widgets crisply ON TOP. The data never passes through the image model,
which is the whole point: the background is decorative, the numbers stay exact.

The heavy lifting downstream already exists, ``CanvasLayout.bg_image`` /
``bg_fit`` render a full-bleed ``<img>`` at ``z-index:0`` behind every element.
This module only covers the missing piece: call fal, fetch the bytes, and
hand back a stored PNG the canvas can point at.

Ported from the community ``fal-image`` widget's fal client so the request
shapes and model handling stay in lockstep, but scoped to a one-shot,
on-demand background (no per-cadence rotation).

Key resolution (see :func:`resolve_fal_key`) reuses an installed fal-image
widget's key first, then an app-level setting, then ``FAL_KEY`` in the env.
"""

from __future__ import annotations

import hashlib
import io
import os
import urllib.error
import urllib.request
from typing import Any

from PIL import Image

API_BASE = "https://fal.run"
USER_AGENT = "tesserae/fal-backgrounds"
HTTP_TIMEOUT_S = 60.0
DEFAULT_MODEL = "fal-ai/flux/schnell"

# The curated model list, mirroring the fal-image widget. Backgrounds default
# to Flux Schnell (~$0.003) for cheap, fast, dithers-clean output.
MODELS: tuple[str, ...] = (
    "fal-ai/flux/schnell",
    "fal-ai/hyper-sdxl",
    "fal-ai/fast-sdxl",
    "fal-ai/flux/dev",
    "fal-ai/recraft-v3",
    "fal-ai/flux-pro/v1.1",
    "fal-ai/nano-banana",
    "fal-ai/nano-banana-2",
    "fal-ai/nano-banana-pro",
)

# Nano Banana (Gemini) accepts only ratio strings, not width/height. Map the
# canvas aspect onto its vocabulary.
_NANO_MODELS = frozenset({"fal-ai/nano-banana", "fal-ai/nano-banana-2", "fal-ai/nano-banana-pro"})
FLUX_DEV = "fal-ai/flux/dev"

# Flux + SDXL want width/height rounded to a multiple of 16 and clamped to
# [256, 1536]; below ~256 the models produce garbage.
_DIM_MIN = 256
_DIM_MAX = 1536
_DIM_STEP = 16

# Style presets prepended to the prompt. Same vocabulary as the fal-image
# widget so a user's mental model carries over.
STYLE_PRESETS: dict[str, str] = {
    "none": "",
    "oil_painting": "oil painting style, painterly brushstrokes, rich textures",
    "watercolor": "watercolor painting, soft washes, paper texture",
    "pencil_sketch": "graphite pencil sketch, hatched lines, paper grain",
    "pixel_art": "pixel art, 16-bit aesthetic, blocky shapes",
    "cyberpunk": "cyberpunk aesthetic, neon lights, rain-slick streets",
    "botanical": "vintage botanical illustration, scientific plate, fine ink lines",
    "bauhaus": "bauhaus geometric design, primary colors, hard edges, modernist",
    "risograph": "risograph print, limited two-color palette, paper grain, offset registration",
    "line_art": "minimal continuous line art, single line drawing, white background",
    "ukiyo_e": "ukiyo-e Japanese woodblock print, flat colors, bold outlines",
    "art_deco": "art deco style, geometric symmetry, gold accents, 1920s",
}

# For a *background*, low-contrast busy output turns to mud behind widgets and
# under 6-colour dithering. This suffix steers toward a clean backdrop and,
# unlike the widget's version, nudges detail away from the centre where the
# data sits.
_EINK_SUFFIX = (
    ", high contrast, limited palette, simple composition, "
    "uncluttered center, suitable as a background"
)


class FalError(RuntimeError):
    """A fal.ai request or image fetch failed. Carries a client-safe message."""


def resolve_fal_key(registry: Any, settings_store: Any) -> str | None:
    """Find a fal.ai API key: an installed fal-image widget's key first, then an
    app-level setting (``app.fal.api_key``), then the ``FAL_KEY`` /
    ``FAL_API_KEY`` env var. Returns ``None`` when none is configured."""
    if registry is not None and settings_store is not None:
        for plugin in getattr(registry, "plugins", {}).values():
            manifest = getattr(plugin, "manifest", {}) or {}
            fields = manifest.get("settings", []) or []
            if not any(f.get("name") == "api_key" for f in fields):
                continue
            pid = str(getattr(plugin, "id", "") or "")
            name = str(manifest.get("name", "") or "")
            if "fal" not in pid.lower() and "fal" not in name.lower():
                continue
            vals = settings_store.get_for_runtime("plugins", pid, fields)
            key = str(vals.get("api_key") or "").strip()
            if key:
                return key
    if settings_store is not None:
        vals = settings_store.get_for_runtime("app", "fal", [{"name": "api_key", "secret": True}])
        key = str(vals.get("api_key") or "").strip()
        if key:
            return key
    for env in ("FAL_KEY", "FAL_API_KEY"):
        val = (os.environ.get(env) or "").strip()
        if val:
            return val
    return None


def build_prompt(prompt: str, style: str, *, eink_friendly: bool = True) -> str:
    """Prepend the style preset and append the e-ink suffix, same order as the
    fal-image widget."""
    parts: list[str] = []
    preset = STYLE_PRESETS.get((style or "none").strip().lower(), "")
    if preset:
        parts.append(preset)
    parts.append(prompt.strip())
    final = ", ".join(p for p in parts if p)
    if eink_friendly:
        final += _EINK_SUFFIX
    return final


def _round_dim(px: int) -> int:
    px = max(_DIM_MIN, min(_DIM_MAX, px))
    return round(px / _DIM_STEP) * _DIM_STEP


def _nano_aspect(width: int, height: int) -> str:
    """Snap a canvas aspect to Nano Banana's nearest ratio string."""
    ratio = width / height if height else 1.0
    table = {"1:1": 1.0, "4:3": 4 / 3, "16:9": 16 / 9, "3:4": 3 / 4, "9:16": 9 / 16}
    return min(table, key=lambda r: abs(table[r] - ratio))


def _build_body(model: str, prompt: str, width: int, height: int, seed: int) -> dict[str, Any]:
    if model in _NANO_MODELS:
        return {"prompt": prompt, "aspect_ratio": _nano_aspect(width, height), "num_images": 1}
    body: dict[str, Any] = {
        "prompt": prompt,
        "seed": seed,
        "image_size": {"width": _round_dim(width), "height": _round_dim(height)},
    }
    if model == FLUX_DEV:
        body["num_inference_steps"] = 28
    return body


def _fal_request(model: str, body: dict[str, Any], api_key: str) -> Any:
    import json

    url = f"{API_BASE}/{model.lstrip('/')}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Key {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        import json as _json

        return _json.loads(resp.read().decode("utf-8"))


def _pick_image_url(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    images = payload.get("images")
    if isinstance(images, list) and images and isinstance(images[0], dict):
        url = images[0].get("url")
        if isinstance(url, str) and url:
            return url
    image = payload.get("image")
    if isinstance(image, dict):
        url = image.get("url")
        if isinstance(url, str) and url:
            return url
    return None


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return bytes(resp.read())


def _to_png(raw: bytes) -> bytes:
    """Re-encode whatever fal returned (jpeg/webp/png) to PNG so the stored
    asset is a valid ``.png`` and serves with the right mime."""
    buf = io.BytesIO()
    Image.open(io.BytesIO(raw)).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def generate(
    prompt: str,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    style: str = "none",
    width: int,
    height: int,
    eink_friendly: bool = True,
    seed: int | None = None,
) -> bytes:
    """Generate one background image and return PNG bytes. Raises
    :class:`FalError` (client-safe message) on any request / fetch failure."""
    if not prompt.strip():
        raise FalError("prompt is required")
    model = (model or DEFAULT_MODEL).strip()
    if model not in MODELS:
        raise FalError(f"unknown model {model!r}")
    final_prompt = build_prompt(prompt, style, eink_friendly=eink_friendly)
    if seed is None:
        seed = int(hashlib.sha256(final_prompt.encode("utf-8")).hexdigest()[:8], 16)
    body = _build_body(model, final_prompt, width, height, seed)
    try:
        payload = _fal_request(model, body, api_key)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:200].strip()
        raise FalError(f"fal.ai returned {err.code}: {detail}") from err
    except urllib.error.URLError as err:
        raise FalError(f"cannot reach fal.ai: {err.reason}") from err
    url = _pick_image_url(payload)
    if not url:
        raise FalError("fal.ai returned no image")
    try:
        raw = _download(url)
        return _to_png(raw)
    except (urllib.error.URLError, OSError) as err:
        raise FalError(f"could not fetch the generated image: {err}") from err


def store_background(renders_dir: Any, png_bytes: bytes) -> str:
    """Write ``png_bytes`` content-addressed under ``renders_dir`` and return the
    ``/renders/<digest>.png`` URL path the canvas ``bg_image`` points at."""
    from pathlib import Path

    digest = hashlib.sha256(png_bytes).hexdigest()[:16]
    renders = Path(renders_dir)
    renders.mkdir(parents=True, exist_ok=True)
    (renders / f"{digest}.png").write_bytes(png_bytes)
    return f"/renders/{digest}.png"
