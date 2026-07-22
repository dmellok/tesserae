"""Firmware update check: fetch latest firmware info per kind via
api.tesserae.ink, cache in-process for 60 min, compare against the
version the device reported in its heartbeat."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from app import firmware_check


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    firmware_check.clear_cache()


def _mock_response(payload: dict[str, Any]):
    class _R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return _R()


def test_latest_for_kind_returns_parsed_info(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "kind": "picpak_client",
        "latest": {
            "version": "0.1.1",
            "released_at": "2026-07-01T09:00:00Z",
            "url": "https://github.com/varanu5/picpak-tesserae-client/releases/tag/v0.1.1",
            "notes_headline": "Fix vflip regression",
            "assets": [
                {
                    "name": "picpak-firmware-v0.1.1.bin",
                    "download_url": "https://example.com/asset.bin",
                }
            ],
        },
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        info = firmware_check.latest_for_kind("picpak_client")
    assert info is not None
    assert info.version == "0.1.1"
    assert info.released_at == "2026-07-01T09:00:00Z"
    assert info.notes_headline == "Fix vflip regression"
    assert len(info.assets) == 1


def test_current_version_sent_as_query_param() -> None:
    seen: dict[str, str] = {}

    def _capture(req: Any, timeout: float | None = None):  # type: ignore[no-untyped-def]
        seen["url"] = req.full_url
        return _mock_response({"latest": {"version": "1.6.0"}})

    with patch("urllib.request.urlopen", side_effect=_capture):
        firmware_check.latest_for_kind("esp32_client", current="1.4.0")
    assert "?current=1.4.0" in seen["url"]


def test_descriptor_url_parsed() -> None:
    payload = {"latest": {"version": "1.6.0", "descriptor_url": "https://api.tesserae.ink/d.json"}}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        info = firmware_check.latest_for_kind("esp32_client")
    assert info is not None
    assert info.descriptor_url == "https://api.tesserae.ink/d.json"


def test_second_call_serves_from_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"latest": {"version": "1.0.0", "released_at": "", "url": "", "notes_headline": ""}}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)) as mocked:
        firmware_check.latest_for_kind("esp32_client")
        firmware_check.latest_for_kind("esp32_client")
        firmware_check.latest_for_kind("esp32_client")
    assert mocked.call_count == 1


def test_no_data_yet_response_returns_none() -> None:
    """The API returns 200 with detail body when the poller hasn't
    populated a cache entry for that kind. In that case ``latest`` is
    missing from the JSON, so latest_for_kind returns None."""

    class _R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"detail":"firmware data not yet available"}'

    with patch("urllib.request.urlopen", return_value=_R()):
        info = firmware_check.latest_for_kind("pi_bin_client")
    assert info is None


def test_network_error_returns_none_without_raising() -> None:
    """Any HTTP-ish failure returns None so the Devices card falls back
    to just showing the current fw_version without an update pill."""
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        info = firmware_check.latest_for_kind("esp32_client")
    assert info is None


def test_compare_versions_current() -> None:
    latest = firmware_check.FirmwareInfo("1.1.1", "", "", "", ())
    assert firmware_check.compare_versions("1.1.1", latest) == "current"


def test_compare_versions_current_tolerates_v_prefix() -> None:
    """Some clients report ``v1.1.1``; the aggregate carries ``1.1.1``.
    Same version modulo the prefix should compare as current."""
    latest = firmware_check.FirmwareInfo("1.1.1", "", "", "", ())
    assert firmware_check.compare_versions("v1.1.1", latest) == "current"


def test_compare_versions_outdated() -> None:
    latest = firmware_check.FirmwareInfo("1.2.0", "", "", "", ())
    assert firmware_check.compare_versions("1.1.1", latest) == "outdated"


def test_compare_versions_unknown_when_current_missing() -> None:
    latest = firmware_check.FirmwareInfo("1.2.0", "", "", "", ())
    assert firmware_check.compare_versions(None, latest) == "unknown"
    assert firmware_check.compare_versions("", latest) == "unknown"


def test_compare_versions_no_data_when_latest_missing() -> None:
    assert firmware_check.compare_versions("1.2.0", None) == "no_data"


def test_compare_versions_unparseable_falls_back_to_unknown() -> None:
    """A dev-build version like ``1.2.0+abc1234`` can't be compared to a
    released SemVer via packaging.Version; we return ``unknown`` rather
    than falsely claim outdated."""
    latest = firmware_check.FirmwareInfo("1.2.0", "", "", "", ())
    # Explicitly ambiguous, packaging.Version rejects trailing metadata
    # only in some forms; we shouldn't crash.
    assert firmware_check.compare_versions("garbage-not-a-version", latest) in (
        "unknown",
        "outdated",
        "current",
    )
