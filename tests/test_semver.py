"""Plain-SemVer comparison (app/semver.py).

Replaces the ``packaging`` dependency, which was imported at module scope in
``app/ota/release.py`` but never declared, so the OTA release CLI + the
``/status`` release-delivery path crashed with ModuleNotFoundError on the slim
Docker image that ships without it."""

from __future__ import annotations

from app.semver import is_strictly_newer, parse_version


def test_parse_version_plain() -> None:
    assert parse_version("1.6.0") == (1, 6, 0)
    assert parse_version("v1.6.0") == (1, 6, 0)
    assert parse_version("2.0") == (2, 0)


def test_parse_version_unparseable() -> None:
    assert parse_version("1.6.0-rc1") is None
    assert parse_version("beta2") is None
    assert parse_version("") is None


def test_is_strictly_newer_ordering() -> None:
    assert is_strictly_newer("1.6.0", "1.5.0") is True
    assert is_strictly_newer("1.5.0", "1.6.0") is False
    assert is_strictly_newer("1.5.0", "1.5.0") is False


def test_is_strictly_newer_pads_lengths() -> None:
    # 1.6 and 1.6.0 are the same version.
    assert is_strictly_newer("1.6", "1.6.0") is False
    assert is_strictly_newer("1.6.1", "1.6") is True


def test_is_strictly_newer_none_when_unparseable() -> None:
    assert is_strictly_newer("1.6.0-rc1", "1.5.0") is None
    assert is_strictly_newer("1.6.0", "beta") is None


def test_release_import_has_no_packaging_dep() -> None:
    """Importing the release module must not need the undeclared ``packaging``
    (the original crash was at its module-level import)."""
    import importlib

    mod = importlib.import_module("app.ota.release")
    assert mod.is_newer("1.6.0", "1.5.0") is True
    assert mod.is_newer("1.5.0", "1.5.0") is False
    # Unparseable falls back to string-differ (conservative offer).
    assert mod.is_newer("beta2", "beta1") is True
    assert mod.is_newer("beta1", "beta1") is False
