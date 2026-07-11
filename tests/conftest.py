"""Shared pytest fixtures.

Two jobs:

1. Keep the suite offline. Nothing here should ever reach api.tesserae.ink:
   the firmware fetch and the ``online.*`` egress helpers (widget-install
   report, install-count fetch, heartbeat) are stubbed so no test hits the
   network (slow; also writes synthetic rows to the live aggregator). Modules
   that exercise these paths directly against a mocked transport opt out by
   filename.

2. Provide the reserved-prefix synthetic install UUID (see ``make_test_install_uuid``)
   so any test traffic that *does* reach the API can be culled by the API-side
   job (``DELETE ... WHERE install_uuid LIKE '7e57c0de-%'``). Real v4 UUIDs are
   random, so a real install can't collide with this fixed first group.
"""

from __future__ import annotations

import contextlib
import uuid
from unittest.mock import patch

import pytest

# Reserved first group ("testcode") on every synthetic install UUID, kept in one
# place so it stays in lockstep with the API cull job's LIKE pattern.
TEST_INSTALL_PREFIX = "7e57c0de"


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


@pytest.fixture(autouse=True)
def _block_live_api(request: pytest.FixtureRequest) -> object:
    """Prevent live api.tesserae.ink calls during the test suite.

    ``test_firmware_check.py`` and ``test_online.py`` exercise the real fetch /
    egress paths against a mocked transport, so they opt out of the relevant
    stubs and drive the network layer themselves.
    """
    base = request.node.fspath.basename
    with contextlib.ExitStack() as stack:
        if base != "test_firmware_check.py":
            stack.enter_context(patch("app.firmware_check._fetch", return_value=None))
        if base != "test_online.py":
            stack.enter_context(patch("app.online.report_widget_install", return_value=False))
            stack.enter_context(patch("app.online.widget_install_counts", return_value={}))
            stack.enter_context(patch("app.online.send_heartbeat", return_value=False))
        yield
