"""Shared pytest fixtures.

Two jobs:

1. Keep the suite offline. An autouse guard wraps ``urllib.request.urlopen`` and
   refuses any request whose URL targets ``api.tesserae.ink`` (raising, which the
   app's best-effort egress helpers swallow into a no-op). This blocks the
   firmware check, the widget-install report, the install-count fetch, and the
   heartbeat in one place, and automatically covers any future egress that goes
   through urllib, so no per-function stub list has to be maintained. Modules
   that exercise the real fetch path against a mocked transport
   (``test_online.py``, ``test_firmware_check.py``) monkeypatch ``urlopen``
   themselves, which transparently overrides this guard for their scope. Real
   requests to any other host pass straight through.

2. Provide the reserved-prefix synthetic install UUID (see
   ``make_test_install_uuid``) so any test traffic that *does* reach the API can
   be culled by the API-side job (``DELETE ... WHERE install_uuid LIKE
   '7e57c0de-%'``). Real v4 UUIDs are random, so a real install can't collide
   with this fixed first group.
"""

from __future__ import annotations

import urllib.request
import uuid
from typing import Any

import pytest

# Reserved first group ("testcode") on every synthetic install UUID, kept in one
# place so it stays in lockstep with the API cull job's LIKE pattern.
TEST_INSTALL_PREFIX = "7e57c0de"

# Captured once, before any patching, so the guard can delegate non-API traffic
# to the genuine implementation without recursing through its own patch.
_REAL_URLOPEN = urllib.request.urlopen


def make_test_install_uuid() -> str:
    """A v4-shaped install UUID whose first group is the reserved test marker,
    e.g. ``7e57c0de-1a2b-4c3d-8e4f-0123456789ab``. The rest stays random so
    dedup behaves like a real install."""
    return TEST_INSTALL_PREFIX + str(uuid.uuid4())[8:]


@pytest.fixture
def test_install_uuid() -> object:
    """Factory returning fresh reserved-prefix synthetic install UUIDs. Reuse
    one per simulated install; make a new one per install."""
    return make_test_install_uuid


def _guarded_urlopen(req: Any, *args: Any, **kwargs: Any) -> Any:
    url = getattr(req, "full_url", None) or (req if isinstance(req, str) else "")
    if "api.tesserae.ink" in str(url):
        raise RuntimeError("blocked a live api.tesserae.ink call during tests; mock the transport")
    return _REAL_URLOPEN(req, *args, **kwargs)


@pytest.fixture(autouse=True)
def _block_live_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse live api.tesserae.ink traffic for every test (see module docstring)."""
    monkeypatch.setattr(urllib.request, "urlopen", _guarded_urlopen)
