"""Updater tests — git/pip are mocked so the suite never hits the network
or mutates the working tree. One integration test runs ``current_state``
against the real repo for a sanity check."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from app import backup as _backup
from app.updater import Updater, UpdaterError


def _new_data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    (root / "core").mkdir(parents=True)
    (root / "core" / "settings.json").write_text('{"app":{}}')
    return root


class _FakeUpdater(Updater):
    """Updater with ``_git`` and ``_pip_install`` faked out. ``responses``
    maps the first few args of a git call to its stdout (or to an
    ``UpdaterError`` to raise)."""

    def __init__(self, *, repo_root: Path, data_root: Path, responses: dict) -> None:
        super().__init__(repo_root=repo_root, data_root=data_root)
        self._responses = responses
        self.git_calls: list[tuple[str, ...]] = []
        self.pip_called = False

    def _git(self, *args: str, check: bool = True, timeout: float = 60) -> str:
        del check, timeout
        self.git_calls.append(args)
        # Match longest-prefix key.
        for key in sorted(self._responses, key=len, reverse=True):
            if args[: len(key)] == key:
                resp = self._responses[key]
                if isinstance(resp, UpdaterError):
                    raise resp
                return str(resp)
        return ""

    def _pip_install(self) -> None:
        self.pip_called = True


# ----- current_state hits the real repo (sanity) ---------------------


def test_current_state_against_the_real_repo(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parent.parent
    u = Updater(repo_root=repo, data_root=_new_data_root(tmp_path))
    state = u.current_state()
    assert len(state.sha) == 40
    assert state.short_sha
    assert state.version  # comes from pyproject (e.g. "0.2.0")
    assert state.branch  # could be DETACHED on CI but is set


# ----- check_remote --------------------------------------------------


def test_check_remote_edge_at_head_reports_no_update(tmp_path: Path) -> None:
    sha = "a" * 40
    u = _FakeUpdater(
        repo_root=tmp_path,
        data_root=_new_data_root(tmp_path),
        responses={
            ("fetch",): "",
            ("rev-parse", "HEAD"): sha,
            ("rev-parse", "--abbrev-ref", "origin/HEAD"): "origin/main",
            ("rev-parse", "origin/main"): sha,
            ("rev-list", "--count", f"{sha}..{sha}"): "0",
            ("log", f"{sha}..{sha}", "--format=%h %s", "--max-count=20"): "",
        },
    )
    check = u.check_remote("edge")
    assert check.available is False
    assert check.commits_behind == 0
    assert check.target_ref == "origin/main"


def test_check_remote_edge_behind_reports_commits_and_changelog(tmp_path: Path) -> None:
    cur, tgt = "a" * 40, "b" * 40
    u = _FakeUpdater(
        repo_root=tmp_path,
        data_root=_new_data_root(tmp_path),
        responses={
            ("fetch",): "",
            ("rev-parse", "HEAD"): cur,
            ("rev-parse", "--abbrev-ref", "origin/HEAD"): "origin/main",
            ("rev-parse", "origin/main"): tgt,
            ("rev-list", "--count", f"{cur}..{tgt}"): "3",
            (
                "log",
                f"{cur}..{tgt}",
                "--format=%h %s",
                "--max-count=20",
            ): "abc1234 fix the thing\ndef5678 docs: small note\n9876543 release prep",
        },
    )
    check = u.check_remote("edge")
    assert check.available is True
    assert check.commits_behind == 3
    assert [e.subject for e in check.changelog] == [
        "fix the thing",
        "docs: small note",
        "release prep",
    ]


def test_check_remote_stable_with_no_tags_is_safe(tmp_path: Path) -> None:
    sha = "a" * 40
    u = _FakeUpdater(
        repo_root=tmp_path,
        data_root=_new_data_root(tmp_path),
        responses={
            ("fetch",): "",
            ("rev-parse", "HEAD"): sha,
            ("rev-parse", "--abbrev-ref", "origin/HEAD"): "origin/main",
            (
                "for-each-ref",
                "--sort=-creatordate",
                "--format=%(refname:short)",
                "refs/tags",
            ): "",  # no tags
        },
    )
    check = u.check_remote("stable")
    assert check.available is False
    assert check.target_ref == "(no tags published)"


# ----- apply_update --------------------------------------------------


def _apply_responses(
    cur: str, tgt: str, *, pyproject_pre: str, pyproject_post_unused: Any = None
) -> dict:
    # Note: the post-update pyproject is read from disk (real fs), not git show.
    return {
        ("fetch",): "",
        ("rev-parse", "HEAD"): cur,  # called multiple times; the first defines current
        ("rev-parse", "--abbrev-ref", "origin/HEAD"): "origin/main",
        ("rev-parse", "origin/main"): tgt,
        ("rev-list", "--count", f"{cur}..{tgt}"): "2",
        ("log", f"{cur}..{tgt}", "--format=%h %s", "--max-count=20"): "abc one\ndef two",
        ("show", f"{cur}:pyproject.toml"): pyproject_pre,
        ("pull", "--ff-only", "--quiet"): "",
        ("status", "--porcelain"): "",
    }


def test_apply_update_takes_backup_pulls_and_records_history(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    # The real pyproject the new SHA points to (read from disk after pull).
    (repo / "pyproject.toml").write_text("[project]\nversion='0.3.0'\n")
    data = _new_data_root(tmp_path)
    responses = _apply_responses("a" * 40, "b" * 40, pyproject_pre="[project]\nversion='0.2.0'\n")
    # After the pull, HEAD reads as the new SHA; chain rev-parse calls
    # by overriding _git just for the post-pull lookup.
    u = _FakeUpdater(repo_root=repo, data_root=data, responses=responses)

    # Make the second rev-parse HEAD return the new SHA.
    new_sha = "b" * 40
    orig_git = u._git
    rev_parse_count = {"n": 0}

    def _git(*args: str, check: bool = True, timeout: float = 60) -> str:  # type: ignore[override]
        if args == ("rev-parse", "HEAD"):
            rev_parse_count["n"] += 1
            return new_sha if rev_parse_count["n"] > 1 else "a" * 40
        return orig_git(*args, check=check, timeout=timeout)

    u._git = _git  # type: ignore[method-assign]

    result = u.apply_update("edge")
    assert result.ok is True
    assert result.from_sha == "a" * 40
    assert result.to_sha == new_sha
    assert result.backup_id  # pre-update snapshot exists
    assert result.pip_changed is True  # pyproject differed → pip ran
    assert u.pip_called is True

    # History recorded.
    hist = u.history()
    assert len(hist) == 1
    assert hist[0].from_sha == "a" * 40
    assert hist[0].to_sha == new_sha
    assert hist[0].backup_id == result.backup_id

    # Backup is real and on disk.
    backups = _backup.list_all(data)
    assert any(b.id == result.backup_id for b in backups)


def test_apply_update_no_op_when_already_at_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("x")
    sha = "a" * 40
    responses = {
        ("fetch",): "",
        ("rev-parse", "HEAD"): sha,
        ("rev-parse", "--abbrev-ref", "origin/HEAD"): "origin/main",
        ("rev-parse", "origin/main"): sha,
        ("rev-list", "--count", f"{sha}..{sha}"): "0",
        ("log", f"{sha}..{sha}", "--format=%h %s", "--max-count=20"): "",
    }
    u = _FakeUpdater(repo_root=repo, data_root=_new_data_root(tmp_path), responses=responses)
    result = u.apply_update("edge")
    assert result.ok is True
    assert result.from_sha == result.to_sha
    assert result.backup_id == ""  # no backup taken when nothing to do
    assert u.pip_called is False


def test_apply_update_refuses_unknown_channel(tmp_path: Path) -> None:
    u = _FakeUpdater(repo_root=tmp_path, data_root=_new_data_root(tmp_path), responses={})
    with pytest.raises(UpdaterError, match="unknown channel"):
        u.apply_update("bogus")


def test_apply_update_releases_push_lock_after(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("x")
    sha = "a" * 40
    responses = {
        ("fetch",): "",
        ("rev-parse", "HEAD"): sha,
        ("rev-parse", "--abbrev-ref", "origin/HEAD"): "origin/main",
        ("rev-parse", "origin/main"): sha,
        ("rev-list", "--count", f"{sha}..{sha}"): "0",
        ("log", f"{sha}..{sha}", "--format=%h %s", "--max-count=20"): "",
    }
    u = _FakeUpdater(repo_root=repo, data_root=_new_data_root(tmp_path), responses=responses)
    lock = threading.Lock()
    u.apply_update("edge", push_lock=lock)
    # The updater released what it acquired.
    assert lock.acquire(blocking=False) is True
    lock.release()


# ----- rollback_last -------------------------------------------------


def test_rollback_last_resets_to_previous_sha_and_restores_backup(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("x")
    data = _new_data_root(tmp_path)

    # Set up: seed a previous update entry in history pointing at a real backup.
    snap = _backup.create(data, label=_backup.LABEL_PRE_UPDATE, note="seed")
    sha_old, sha_new = "a" * 40, "b" * 40
    u = _FakeUpdater(
        repo_root=repo,
        data_root=data,
        responses={
            ("rev-parse", "HEAD"): sha_new,
            ("reset", "--hard", sha_old): "",
            ("status", "--porcelain"): "",
        },
    )
    # Inject history directly.
    from app.updater import HistoryEntry

    u._record_history(
        HistoryEntry(
            at=1.0,
            from_sha=sha_old,
            to_sha=sha_new,
            channel="edge",
            backup_id=snap.id,
            pip_changed=True,
        )
    )
    # Wipe the snapshot's restored content so we can detect it came back.
    (data / "core" / "settings.json").write_text("MUTATED")

    result = u.rollback_last()
    assert result.ok is True
    assert result.to_sha == sha_old
    # The pre-update settings.json content was restored.
    assert (data / "core" / "settings.json").read_text() == '{"app":{}}'
    assert u.pip_called is True  # because the seeded entry said pip_changed


def test_rollback_with_no_history_raises(tmp_path: Path) -> None:
    u = _FakeUpdater(repo_root=tmp_path, data_root=_new_data_root(tmp_path), responses={})
    with pytest.raises(UpdaterError, match="no previous update"):
        u.rollback_last()
