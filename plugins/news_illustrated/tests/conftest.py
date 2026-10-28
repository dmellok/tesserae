"""Fixtures for the news_illustrated tests."""

from __future__ import annotations

import pytest

from plugins.news_illustrated import net
from plugins.news_illustrated.tests.fakes import FakeNet, install


@pytest.fixture
def fake_net(monkeypatch: pytest.MonkeyPatch) -> FakeNet:
    """Every request the plugin's helper modules make goes to a FakeNet."""
    return install(monkeypatch, net)
