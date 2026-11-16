"""Choice providers: which ones get the cell's options, and what they get."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.widget_choices import accepts_options, choice_rows, wants_options


def _plugin(fn: Any) -> Any:
    return SimpleNamespace(id="w", manifest={}, server_module=SimpleNamespace(choices=fn))


def test_accepts_options_reads_the_signature() -> None:
    def plain(name: str) -> list[Any]:
        return []

    def keyword(name: str, options: dict[str, Any] | None = None) -> list[Any]:
        return []

    def kw_only(name: str, *, options: dict[str, Any]) -> list[Any]:
        return []

    def anything(name: str, **kwargs: Any) -> list[Any]:
        return []

    def positional_only(name: str, options: Any = None, /) -> list[Any]:
        return []

    assert not accepts_options(plain)
    assert accepts_options(keyword) and accepts_options(kw_only) and accepts_options(anything)
    assert not accepts_options(positional_only)
    assert not accepts_options(len)  # a builtin with no inspectable signature is plain
    assert wants_options(_plugin(keyword)) and not wants_options(_plugin(plain))
    assert not wants_options(SimpleNamespace(server_module=None))


def test_a_provider_that_takes_options_gets_the_cells_values() -> None:
    seen: list[Any] = []

    def choices(name: str, options: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        seen.append((name, options))
        return [{"value": "a", "label": "A", "disabled": True, "reason": "Not here."}]

    spec = {"name": "method", "type": "select", "choices_from": "method"}
    rows = choice_rows(_plugin(choices), spec, {"printer": "beta"})
    assert seen == [("method", {"printer": "beta"})]
    assert rows == [{"value": "a", "label": "A", "disabled": True, "reason": "Not here."}], (
        "disabled and reason reach the editor"
    )
    # no cell to ask about: an empty dict, never None
    choice_rows(_plugin(choices), spec)
    assert seen[-1] == ("method", {})


def test_a_plain_provider_is_called_as_before() -> None:
    def choices(name: str) -> list[dict[str, Any]]:
        return [{"value": name, "label": name, "image": "/plugins/w/static/a.jpg"}]

    rows = choice_rows(_plugin(choices), {"choices_from": "x"}, {"printer": "p"})
    assert rows == [{"value": "x", "label": "x", "image": "/plugins/w/static/a.jpg"}], (
        "a dynamic row keeps its picture, so the picker can be dynamic"
    )


def test_static_choices_ignore_options() -> None:
    spec = {"choices": [{"value": "a", "label": "A", "disabled": True}]}
    assert choice_rows(None, spec, {"x": 1}) == spec["choices"]
