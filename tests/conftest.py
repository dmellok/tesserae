"""Shared pytest fixtures.

Currently only used to stub out network-facing helpers that would
otherwise reach api.tesserae.ink on every settings page render (four
second timeout each; on machines with no route to the aggregator this
also raises ResourceWarning on GC of the HTTPError body, which pytest
promotes to a test failure). Individual tests can still override the
mock with their own patch when they want to exercise the fetch path.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _stub_firmware_check_fetch(request):
    """Prevent live api.tesserae.ink calls during the test suite.

    ``tests/test_firmware_check.py`` exercises the real fetch path
    against a mocked ``urlopen``, so skip the stub in that module.
    """
    if request.node.fspath.basename == "test_firmware_check.py":
        yield
        return
    with patch("app.firmware_check._fetch", return_value=None):
        yield
