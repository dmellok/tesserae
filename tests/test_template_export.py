"""Template export sanitizer: the "secrets never leave the machine" contract."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app import template_export
from app.state.page_store import Page
from app.state.panel_store import Binding, CanvasLayout, CodeSource, Element


def _page(els: list[Element], **canvas_kwargs: Any) -> Page:
    return Page(
        id="pg1",
        name="My Board",
        layout_kind="canvas",
        device_ids=["dev_a"],
        canvas=CanvasLayout(w=800, h=480, els=els, **canvas_kwargs),
    )


def _export(app: Flask, page: Page, records: dict | None = None) -> dict[str, Any]:
    with app.app_context():
        return template_export.build_template(
            page,
            registry=app.config["PLUGIN_REGISTRY"],
            installed_records=records or {},
            data_root=app.config["DATA_ROOT"],
        )


def test_rest_service_secret_options_are_redacted(app: Flask) -> None:
    """The acceptance case: an Authorization header configured on a
    rest_service source never appears in the exported template; a secret
    input pointing at the slot replaces it."""
    el = Element(
        id="c1",
        kind="code",
        x=0,
        y=0,
        w=200,
        h=100,
        sources=[
            CodeSource(
                key="rest_service",
                name="api",
                options={
                    "url": "https://internal/x",
                    "headers": '{"Authorization": "Bearer s3cr3t-token-abc"}',
                },
            )
        ],
        js="document.body.textContent = ctx.data.api.value",
    )
    result = _export(app, _page([el]))
    dumped = str(result["template"])
    assert "s3cr3t-token-abc" not in dumped
    assert "https://internal/x" not in dumped
    assert result["blocking"] == []
    secret_inputs = [s for s in result["inputs_suggested"] if s["secret"]]
    assert len(secret_inputs) >= 2  # url + headers
    slots = {t["slot"] for s in secret_inputs for t in s["targets"]}
    assert slots == {"source_options"}


def test_code_source_transport_headers_always_stripped(app: Flask) -> None:
    el = Element(
        id="c1",
        kind="code",
        x=0,
        y=0,
        w=200,
        h=100,
        sources=[CodeSource(url="https://api.example/data", headers={"X-Api-Key": "k-123456"})],
        js="1",
    )
    result = _export(app, _page([el]))
    assert "k-123456" not in str(result["template"])
    assert any(
        t["slot"] == "source_header" for s in result["inputs_suggested"] for t in s["targets"]
    )
    assert any("request headers" in r for r in result["redactions"])


def test_install_specific_values_become_inputs(app: Flask) -> None:
    el = Element(
        id="d1",
        kind="data",
        source="ha_sensor",
        options={"entity": "sensor.kitchen_temperature"},
        field="state",
        x=0,
        y=0,
        w=100,
        h=50,
    )
    result = _export(app, _page([el]))
    assert "sensor.kitchen_temperature" not in str(result["template"])
    assert any(not s["secret"] for s in result["inputs_suggested"])


def test_device_ids_and_page_identity_never_exported(app: Flask) -> None:
    el = Element(id="t1", kind="text", text="hi", x=0, y=0, w=100, h=40)
    result = _export(app, _page([el]))
    dumped = str(result["template"])
    assert "dev_a" not in dumped and "pg1" not in dumped
    assert result["template"]["title"] == "My Board"


def test_page_asset_bg_inlines_small_and_blocks_large(app: Flask) -> None:
    from app.page_assets import assets_dir

    el = Element(id="t1", kind="text", text="hi", x=0, y=0, w=100, h=40)
    adir = assets_dir(app.config["DATA_ROOT"], "pg1")
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    page = _page([el], bg_image="/page-assets/pg1/photo.png")
    result = _export(app, page)
    assert result["template"]["canvas"]["bg_image"].startswith("data:image/png;base64,")
    assert result["blocking"] == []

    (adir / "big.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"0" * (template_export.MAX_INLINE_ASSET_BYTES + 1)
    )
    result = _export(app, _page([el], bg_image="/page-assets/pg1/big.png"))
    assert any("too large" in b for b in result["blocking"])


def test_requires_from_marketplace_records(app: Flask) -> None:
    el = Element(id="w1", kind="widget", widget="clock_analog", x=0, y=0, w=200, h=200)
    # Pretend "clock" came from the marketplace under catalog id "fancy-clock".
    records = {"fancy-clock": SimpleNamespace(kind="widget", folders=["clock_analog"])}
    result = _export(app, _page([el]), records)
    assert result["template"]["requires"] == ["fancy-clock"]
    # Bundled with no record: no requires entry.
    result = _export(app, _page([el]), {})
    assert result["template"]["requires"] == []


def test_unknown_widget_blocks(app: Flask) -> None:
    el = Element(id="w1", kind="widget", widget="not_a_widget", x=0, y=0, w=100, h=100)
    result = _export(app, _page([el]))
    assert any("not installed" in b for b in result["blocking"])


def test_user_theme_blocks(app: Flask) -> None:
    el = Element(id="t1", kind="text", text="hi", x=0, y=0, w=100, h=40)
    result = _export(app, _page([el], theme="user-abc123"))
    assert any("user theme" in b for b in result["blocking"])


def test_hardcoded_key_in_js_blocks(app: Flask) -> None:
    el = Element(
        id="c1",
        kind="code",
        x=0,
        y=0,
        w=100,
        h=100,
        js="const KEY = 'sk-abcdefghijklmnop1234'; render(KEY)",
    )
    result = _export(app, _page([el]))
    assert any("credential material" in b for b in result["blocking"])


def test_benign_long_text_does_not_block(app: Flask) -> None:
    el = Element(
        id="t1",
        kind="text",
        text="the quick brown fox jumps over the lazy dog again and again",
        x=0,
        y=0,
        w=100,
        h=40,
    )
    result = _export(app, _page([el]))
    assert result["blocking"] == []


def test_inlined_bg_does_not_trip_entropy_lint(app: Flask) -> None:
    from app.page_assets import assets_dir

    el = Element(id="t1", kind="text", text="hi", x=0, y=0, w=100, h=40)
    adir = assets_dir(app.config["DATA_ROOT"], "pg1")
    adir.mkdir(parents=True, exist_ok=True)
    import os

    (adir / "noise.png").write_bytes(b"\x89PNG\r\n\x1a\n" + os.urandom(4096))
    result = _export(app, _page([el], bg_image="/page-assets/pg1/noise.png"))
    assert result["blocking"] == []
    assert not any(w["rule"] == "high-entropy-string" for w in result["lint"]["warnings"])


@pytest.mark.parametrize(
    ("slot", "builder", "reader"),
    [
        (
            "options",
            lambda: Element(
                id="e1", kind="widget", widget="clock_analog", options={}, x=0, y=0, w=9, h=9
            ),
            lambda el: el["options"]["city"],
        ),
        (
            "source_options",
            lambda: Element(
                id="e1", kind="code", sources=[CodeSource(key="rest_service")], x=0, y=0, w=9, h=9
            ),
            lambda el: el["sources"][0]["options"]["city"],
        ),
        (
            "source_header",
            lambda: Element(
                id="e1", kind="code", sources=[CodeSource(url="https://x")], x=0, y=0, w=9, h=9
            ),
            lambda el: el["sources"][0]["headers"]["Authorization"],
        ),
        (
            "source_url",
            lambda: Element(
                id="e1", kind="code", sources=[CodeSource(url="https://old")], x=0, y=0, w=9, h=9
            ),
            lambda el: el["sources"][0]["url"],
        ),
        (
            "bind_options",
            lambda: Element(
                id="e1",
                kind="rect",
                bind=[Binding(source="weather_now", field="temp", transform="length")],
                x=0,
                y=0,
                w=9,
                h=9,
            ),
            lambda el: el["bind"][0]["options"]["city"],
        ),
    ],
)
def test_apply_inputs_round_trip_every_slot(slot: str, builder: Any, reader: Any) -> None:
    el = builder()
    layout = CanvasLayout(w=100, h=100, els=[el])
    target: dict[str, Any] = {"el": "e1", "slot": slot}
    if slot in ("source_options", "source_header", "source_url", "bind_options"):
        target["index"] = 0
    if slot in ("options", "source_options", "bind_options"):
        target["key"] = "city"
    if slot == "source_header":
        target["key"] = "Authorization"
    template = {
        "schema_version": 1,
        "title": "T",
        "inputs": [{"name": "v", "label": "V", "type": "string", "targets": [target]}],
        "canvas": layout.model_dump(mode="json", exclude_none=True),
    }
    patched = template_export.apply_inputs(template, {"v": "supplied-value"})
    els = {e["id"]: e for e in patched["els"]}
    assert reader(els["e1"]) == "supplied-value"
    # The patched canvas still validates through the real model.
    CanvasLayout.model_validate(patched)


def test_apply_inputs_ignores_unknown_names_and_missing_values() -> None:
    el = Element(
        id="e1", kind="widget", widget="clock_analog", options={"city": ""}, x=0, y=0, w=9, h=9
    )
    layout = CanvasLayout(w=100, h=100, els=[el])
    template = {
        "schema_version": 1,
        "title": "T",
        "inputs": [
            {
                "name": "city",
                "label": "City",
                "type": "string",
                "targets": [{"el": "e1", "slot": "options", "key": "city"}],
            }
        ],
        "canvas": layout.model_dump(mode="json", exclude_none=True),
    }
    patched = template_export.apply_inputs(template, {"unrelated": "x"})
    assert patched["els"][0]["options"]["city"] == ""


def test_export_data_uri_bg_round_trips_valid_base64(app: Flask) -> None:
    from app.page_assets import assets_dir

    el = Element(id="t1", kind="text", text="hi", x=0, y=0, w=100, h=40)
    adir = assets_dir(app.config["DATA_ROOT"], "pg1")
    adir.mkdir(parents=True, exist_ok=True)
    raw = b"\x89PNG\r\n\x1a\n" + b"payload"
    (adir / "bg.png").write_bytes(raw)
    result = _export(app, _page([el], bg_image="/page-assets/pg1/bg.png"))
    encoded = result["template"]["canvas"]["bg_image"].split(",", 1)[1]
    assert base64.b64decode(encoded) == raw


# -- preview downscaling -------------------------------------------------


def _png_bytes(w: int, h: int, *, noisy: bool) -> bytes:
    """A PNG of the given size: noisy (incompressible, worst case) or flat."""
    import io
    import os

    from PIL import Image

    if noisy:
        image = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    else:
        image = Image.new("RGB", (w, h), (240, 238, 232))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_shrink_preview_fits_target_and_stays_readable() -> None:
    """A full-size 13.3" render (1600x1200) is downsampled under the cap while
    staying large enough to review in the Discord embed."""
    from PIL import Image

    original = _png_bytes(1600, 1200, noisy=True)
    assert len(original) > template_export.PREVIEW_TARGET_BYTES  # worst case

    shrunk = template_export.shrink_preview(original)
    assert len(shrunk) <= template_export.PREVIEW_TARGET_BYTES

    import io

    image = Image.open(io.BytesIO(shrunk))
    assert max(image.size) <= template_export.PREVIEW_MAX_EDGE
    # Even the fallback steps stay big enough to judge a dashboard by.
    assert max(image.size) >= 640
    assert abs((image.size[0] / image.size[1]) - (1600 / 1200)) < 0.01  # aspect kept


def test_shrink_preview_leaves_already_small_renders_untouched() -> None:
    """Flat dashboard art compresses better at native size than after
    resampling, so a render already inside the budget is passed through
    byte-identical: full resolution for the reviewer, no size regression."""
    small = _png_bytes(1600, 1200, noisy=False)
    assert len(small) < template_export.PREVIEW_TARGET_BYTES
    assert template_export.shrink_preview(small) == small


def test_shrink_preview_passes_through_unreadable_bytes() -> None:
    junk = b"\x89PNG\r\n\x1a\nnot-actually-an-image"
    assert template_export.shrink_preview(junk) == junk
