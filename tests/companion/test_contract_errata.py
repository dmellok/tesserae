"""The vendored contract stays verbatim, and the errata list stays honest.

Two properties, both of which we lost by editing the vendored file directly
and only noticed after six deviations had accumulated:

* the vendored copy carries no local edits, so refreshing it is a copy
  rather than a merge;
* every erratum is still needed, so the list shrinks itself when the
  published contract catches up instead of quietly rotting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from .contract_errata import (
    ERRATA,
    SCHEMA_ERRATA,
    Erratum,
    SchemaErratum,
    apply,
    missing_keys,
    missing_values,
)

_RAW: dict[str, Any] = yaml.safe_load(
    (Path(__file__).parent / "contract" / "app-v1.openapi.yaml").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("erratum", ERRATA, ids=lambda e: f"{e.pointer}:{','.join(e.values)}")
def test_each_erratum_is_still_needed(erratum: Erratum) -> None:
    """The self-cleaning half.

    When a re-vendor brings a value in, this fails and names the entry to
    delete from ``contract_errata.py``. Without it the list would keep
    growing and nobody would know which parts were still real, which is
    exactly how the inline comments ended up misleading.
    """
    still_missing = missing_values(_RAW, erratum)
    assert still_missing == erratum.values, (
        f"The vendored contract now carries "
        f"{sorted(set(erratum.values) - set(still_missing))} at {erratum.pointer}. "
        f"Delete those from ERRATA in contract_errata.py."
    )


def test_the_vendored_contract_has_no_local_edits() -> None:
    """A local edit is a merge waiting to happen: a plain overwrite on the
    next refresh would drop it silently and fail a dozen tests elsewhere."""
    text = (Path(__file__).parent / "contract" / "app-v1.openapi.yaml").read_text(encoding="utf-8")
    for marker in ("errata", "pending re-vendor", "Tesserae serves"):
        assert marker.lower() not in text.lower(), (
            f"{marker!r} appears in the vendored contract. Local additions "
            f"belong in contract_errata.py so the copy stays verbatim."
        )


def test_applying_errata_is_idempotent() -> None:
    """Applying to a refreshed contract that already caught up must not
    duplicate a value, so a partial re-vendor is safe."""
    once = apply(yaml.safe_load(yaml.safe_dump(_RAW)))
    twice = apply(once)
    for erratum in ERRATA:
        node: Any = twice
        for part in erratum.pointer.split("/"):
            node = node[part]
        for value in erratum.values:
            assert node.count(value) == 1, f"{value} duplicated at {erratum.pointer}"


def test_every_erratum_says_why_and_since() -> None:
    """The list doubles as what to send the contract's owner, so an entry
    without a reason or a version isn't much use to them."""
    for erratum in (*ERRATA, *SCHEMA_ERRATA):
        assert erratum.since.startswith("v"), erratum.pointer
        assert len(erratum.why) > 40, f"{erratum.pointer} needs a real reason"


@pytest.mark.parametrize(
    "erratum", SCHEMA_ERRATA, ids=lambda e: f"{e.pointer}:{','.join(sorted(e.patch))}"
)
def test_each_schema_erratum_is_still_needed(erratum: SchemaErratum) -> None:
    """Same self-cleaning rule for the whole-key additions.

    A re-vendor that brings a Gallery schema in should delete the entry
    rather than leave a transcription shadowing the published one."""
    still_missing = missing_keys(_RAW, erratum)
    assert set(still_missing) == set(erratum.patch), (
        f"The vendored contract now carries "
        f"{sorted(set(erratum.patch) - set(still_missing))} at {erratum.pointer}. "
        f"Delete those from SCHEMA_ERRATA in contract_errata.py."
    )


def test_schema_errata_never_overwrite_the_published_shape() -> None:
    """Applying twice, and applying over a contract that caught up, must
    leave the vendored definition in place: an erratum adds what is
    missing, it does not get to redefine what was published."""
    caught_up = yaml.safe_load(yaml.safe_dump(_RAW))
    marker = {"type": "string", "description": "published"}
    for erratum in SCHEMA_ERRATA:
        node: Any = caught_up
        for part in erratum.pointer.split("/"):
            node = node[part]
        for key in erratum.patch:
            node[key] = marker
    applied = apply(caught_up)
    for erratum in SCHEMA_ERRATA:
        node = applied
        for part in erratum.pointer.split("/"):
            node = node[part]
        for key in erratum.patch:
            assert node[key] == marker, f"{key} at {erratum.pointer} was overwritten"
