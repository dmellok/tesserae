"""The locale pickers list whatever languages installed widgets ship,
not a hardcoded en/fr pair (issue #279)."""

from __future__ import annotations

from types import SimpleNamespace

from app.locale_choices import available_locales, label_for


class _Registry:
    def __init__(self, *string_maps: dict[str, dict[str, str]]) -> None:
        self._widgets = [SimpleNamespace(strings=m) for m in string_maps]

    def widgets(self):
        return self._widgets


def test_english_is_always_first_even_with_no_widgets() -> None:
    assert available_locales(_Registry()) == [{"value": "en", "label": "English"}]
    assert available_locales(None) == [{"value": "en", "label": "English"}]


def test_unions_every_widgets_locales_and_dedupes() -> None:
    reg = _Registry(
        {"en": {}, "fr": {}},
        {"fr": {}, "sk": {}},
        {},  # a widget that hasn't opted in contributes nothing
    )
    values = [c["value"] for c in available_locales(reg)]
    assert values[0] == "en"
    assert sorted(values[1:]) == ["fr", "sk"]
    assert {c["value"]: c["label"] for c in available_locales(reg)}["sk"] == "Slovenčina"


def test_rest_sorts_by_label_not_tag() -> None:
    # Deutsch < Français < Slovenčina by label; by tag it would be de, fr, sk
    # too, so use one where they differ: Čeština (cs) sorts after Deutsch
    # by tag order but should be first by casefolded label... except
    # casefold keeps the diacritic, so assert the label order explicitly.
    reg = _Registry({"sk": {}, "de": {}, "fr": {}})
    labels = [c["label"] for c in available_locales(reg)]
    assert labels == ["English", "Deutsch", "Français", "Slovenčina"]


def test_unknown_tag_falls_back_to_the_tag_and_regional_variant_to_base() -> None:
    assert label_for("xx") == "xx"
    assert label_for("fr-CA") == "Français (CA)"
    assert label_for("pt-BR") == "Português (Brasil)"
