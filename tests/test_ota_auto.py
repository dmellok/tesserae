"""Auto-update: a failed release refresh is visible, not silent."""

from __future__ import annotations

import logging

import pytest

from app.ota import auto


@pytest.fixture(autouse=True)
def _reset():
    auto._freshen_warned.clear()
    yield
    auto._freshen_warned.clear()


def test_a_failed_refresh_warns_once_per_kind_and_error_per_hour(caplog):
    caplog.set_level(logging.DEBUG, logger="app.ota.auto")
    err = "fetch_failed: HTTP Error 504: Gateway Time-out"
    auto._warn_freshen_failed("pimoroni_inky_frame_73", err, now=1000.0)
    auto._warn_freshen_failed("pimoroni_inky_frame_73", err, now=1060.0)  # next heartbeat
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "pimoroni_inky_frame_73" in warnings[0].getMessage()
    assert "504" in warnings[0].getMessage()
    # The repeat is still there for anyone at debug.
    assert any(r.levelno == logging.DEBUG for r in caplog.records)


def test_a_different_error_or_kind_warns_again(caplog):
    caplog.set_level(logging.WARNING, logger="app.ota.auto")
    auto._warn_freshen_failed("k1", "fetch_failed: 504", now=0.0)
    auto._warn_freshen_failed("k1", "bad_signature", now=1.0)
    auto._warn_freshen_failed("k2", "fetch_failed: 504", now=2.0)
    assert len(caplog.records) == 3


def test_the_same_failure_warns_again_after_an_hour(caplog):
    caplog.set_level(logging.WARNING, logger="app.ota.auto")
    auto._warn_freshen_failed("k1", "fetch_failed: 504", now=0.0)
    auto._warn_freshen_failed("k1", "fetch_failed: 504", now=3601.0)
    assert len(caplog.records) == 2
