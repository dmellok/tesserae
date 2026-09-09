"""The derived encryption key is warned about, not merely noted (#259).

The fallback key survives an ordinary restart and does not survive a
recreated container or a data folder restored alongside a regenerated
session secret. When it does not, every stored secret decrypts to an empty
string while the non-secret settings beside it are intact -- so the install
looks configured and is not, and nothing says so until the next time
something tries to use a credential.

An operator who never pinned a key is one container recreation away from
re-entering everything. That is worth a warning with the line to fix it, not
an info line most deployments never render.
"""

from __future__ import annotations

import logging
import re

import pytest

from app.secret_box import ENV_KEY, KEY_BYTES, SecretBox, suggested_env_key


def test_the_derived_key_warns(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At warning, so it reaches an operator running at default log level."""
    monkeypatch.delenv(ENV_KEY, raising=False)
    with caplog.at_level(logging.INFO):
        SecretBox.resolve(b"0" * 32)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "running on the derived key produced no warning"


def test_the_warning_carries_a_pasteable_line(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acting on it should be a copy, not a trip to the docs.

    The issue asks for "the exact line to add to the compose file".
    """
    monkeypatch.delenv(ENV_KEY, raising=False)
    with caplog.at_level(logging.WARNING):
        SecretBox.resolve(b"0" * 32)
    text = "\n".join(r.getMessage() for r in caplog.records)
    match = re.search(rf"{ENV_KEY}=([0-9a-f]+)", text)
    assert match, f"no `{ENV_KEY}=<key>` line in the warning:\n{text}"
    assert len(match.group(1)) == KEY_BYTES * 2


def test_the_warning_names_the_failure_not_just_the_setting(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Set this variable" is advice; "you will lose your credentials" is a
    reason. The operator is deciding whether to act now or later."""
    monkeypatch.delenv(ENV_KEY, raising=False)
    with caplog.at_level(logging.WARNING):
        SecretBox.resolve(b"0" * 32)
    text = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "container" in text
    assert "empty" in text


def test_a_pinned_key_does_not_warn(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator who already acted must not be nagged on every boot."""
    monkeypatch.setenv(ENV_KEY, "ab" * KEY_BYTES)
    with caplog.at_level(logging.INFO):
        SecretBox.resolve(b"0" * 32)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_suggested_key_is_fresh_each_time() -> None:
    """A key derived from the session secret would reproduce the coupling
    the warning exists to break."""
    first, second = suggested_env_key(), suggested_env_key()
    assert first != second
    assert len(first) == KEY_BYTES * 2
    assert re.fullmatch(r"[0-9a-f]+", first)


def test_the_suggested_key_is_accepted_by_the_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """The line the warning prints has to actually work when pasted."""
    monkeypatch.setenv(ENV_KEY, suggested_env_key())
    box = SecretBox.from_env()
    assert box is not None
    assert box.unwrap(box.wrap("hunter2")) == "hunter2"
