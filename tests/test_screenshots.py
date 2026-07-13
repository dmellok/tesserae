"""The `python -m app.screenshots` catalog-set emitter.

The emitter drives a running server's render.png endpoint, so these tests stub
``_render`` (the one HTTP call) and check the CLI's file layout, preset
handling, and argument guards, not the render itself (covered in test_mcp_api)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app import screenshots


def _stub_render(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(base, token, widget_id, *, size=None, w=None, h=None, options=None, timeout=60.0):  # type: ignore[no-untyped-def]
        calls.append({"size": size, "w": w, "h": h, "options": options})
        tag = size or f"{w}x{h}"
        return f"PNG:{tag}".encode()

    monkeypatch.setattr(screenshots, "_render", fake)
    return calls


def test_writes_lg_and_extras(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_render(monkeypatch)
    presets = tmp_path / "presets.json"
    presets.write_text(
        json.dumps(
            [
                {"name": "configured", "options": {"seed": 1}},
                {"name": "medium", "options": {}, "size": "md"},
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "shots"
    rc = screenshots.main(
        ["clock_analog", "--out", str(out), "--lg", "--extra", str(presets), "--url", "http://x"]
    )
    assert rc == 0
    assert (out / "lg.png").read_bytes() == b"PNG:lg"
    assert (out / "extra-1.png").exists()
    assert (out / "extra-2.png").exists()
    # lg first, then each preset with its options / size honoured.
    assert calls[0]["size"] == "lg"
    assert calls[1]["options"] == {"seed": 1}
    assert calls[2]["size"] == "md"


def test_requires_lg_or_extra(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        screenshots.main(["clock_analog", "--out", str(tmp_path / "o")])


def test_presets_must_be_a_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_render(monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        screenshots.main(["clock_analog", "--out", str(tmp_path / "o"), "--extra", str(bad)])
