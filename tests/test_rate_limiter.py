"""RateLimiter unit tests + register-endpoint integration.

Covers the limiter behaviour in isolation (allow until cap, deny when
exceeded, sliding window) and the register endpoint's response shape
under rate-limit (429 + Retry-After header).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.rate_limiter import RateLimiter

# -- unit tests --------------------------------------------------------


def test_allows_attempts_under_the_cap() -> None:
    limiter = RateLimiter(max_attempts=3, window_s=60)
    for _ in range(3):
        result = limiter.check_and_consume("1.2.3.4")
        assert result.allowed is True


def test_denies_attempts_at_the_cap() -> None:
    limiter = RateLimiter(max_attempts=3, window_s=60)
    for _ in range(3):
        limiter.check_and_consume("1.2.3.4")
    result = limiter.check_and_consume("1.2.3.4")
    assert result.allowed is False
    assert result.retry_after_s > 0


def test_buckets_per_client_id() -> None:
    """Different IPs don't affect each other."""
    limiter = RateLimiter(max_attempts=1, window_s=60)
    a = limiter.check_and_consume("1.1.1.1")
    b = limiter.check_and_consume("2.2.2.2")
    assert a.allowed is True
    assert b.allowed is True


def test_record_success_releases_bucket() -> None:
    """A successful attempt zeroes the failure count for that client,
    so a user pairing several devices in a row isn't penalised."""
    limiter = RateLimiter(max_attempts=2, window_s=60)
    limiter.check_and_consume("1.2.3.4")
    limiter.check_and_consume("1.2.3.4")
    blocked = limiter.check_and_consume("1.2.3.4")
    assert blocked.allowed is False
    limiter.record_success("1.2.3.4")
    again = limiter.check_and_consume("1.2.3.4")
    assert again.allowed is True


def test_window_expires_attempts() -> None:
    """After window_s elapses, the oldest attempt ages out and a new
    one slips in."""
    limiter = RateLimiter(max_attempts=1, window_s=1)
    limiter.check_and_consume("1.2.3.4")
    assert limiter.check_and_consume("1.2.3.4").allowed is False
    time.sleep(1.1)
    assert limiter.check_and_consume("1.2.3.4").allowed is True


# -- integration with /register ----------------------------------------


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    # Tight cap for the test so we don't have to flood it.
    a.config["REGISTER_RATE_LIMITER"] = RateLimiter(max_attempts=3, window_s=60)
    return a


def _post_register(client, *, code: str = "wrong0", device_id: str = "rl_dev"):
    return client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": "pico_bin_client",
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "0.0.1",
            }
        ),
    )


def test_register_returns_429_after_cap_failed_attempts(app: Flask) -> None:
    """After three bad pairing codes from the same IP, the next call
    gets 429 + Retry-After before the code is even inspected."""
    client = app.test_client()
    # Three failed attempts (all 403 because pairing code is bogus).
    for _ in range(3):
        resp = _post_register(client, code="wrong0")
        # The endpoint validates the kind FIRST then consumes the
        # code; a bogus code falls through to a 403 here.
        assert resp.status_code == 403
    # Fourth attempt is blocked by the rate limiter (429 BEFORE
    # the pairing-code check).
    resp = _post_register(client, code="wrong0")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0


def test_successful_register_releases_rate_limit_bucket(app: Flask) -> None:
    """A successful registration zeros the failure count for that IP,
    so a user who legitimately pairs multiple devices in a row doesn't
    get throttled."""
    client = app.test_client()
    # Two failed attempts.
    _post_register(client, code="wrong0")
    _post_register(client, code="wrong0")
    # A successful pair.
    pairing_code = app.config["PAIRING_STORE"].issue(note="t").code
    resp = _post_register(client, code=pairing_code)
    assert resp.status_code == 201
    # Bucket released: three more failed attempts allowed.
    for _ in range(3):
        resp = _post_register(client, code="wrong0")
        assert resp.status_code == 403
