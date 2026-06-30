"""Updater tests, git/pip are mocked so the suite never hits the network
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


# ----- restart: POSIX execv vs Windows Popen ---------------------------


def test_restart_posix_uses_execv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On POSIX the kernel hands the listening socket FD to the new
    Python, ``os.execv`` is the right primitive."""
    monkeypatch.setattr("app.updater._is_windows", lambda: False)
    captured: dict[str, Any] = {}

    def fake_execv(executable: str, argv: list[str]) -> None:
        captured["executable"] = executable
        captured["argv"] = argv

    monkeypatch.setattr("app.updater.os.execv", fake_execv)

    u = _FakeUpdater(repo_root=tmp_path, data_root=_new_data_root(tmp_path), responses={})
    u.restart(delay_s=0.0)
    # Timer is async; wait for it to fire.
    for _ in range(50):
        if "argv" in captured:
            break
        import time as _time

        _time.sleep(0.01)
    assert "argv" in captured
    assert captured["argv"][0] == captured["executable"]  # sys.executable repeated
    assert "app.main" in captured["argv"]


def test_restart_windows_spawns_detached_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows the new process is spawned in its own process group
    and the old process exits via ``os._exit`` so the listening socket
    is released. The parent's PID rides along in ``TESSERAE_PARENT_PID``
    so the child knows whose grave to wait at."""
    monkeypatch.setattr("app.updater._is_windows", lambda: True)
    popen_calls: list[dict[str, Any]] = []

    class _FakePopen:
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            popen_calls.append({"argv": argv, **kwargs})

    exit_calls: list[int] = []

    def fake_exit(code: int) -> None:
        exit_calls.append(code)

    monkeypatch.setattr("app.updater.subprocess.Popen", _FakePopen)
    monkeypatch.setattr("app.updater.os._exit", fake_exit)

    u = _FakeUpdater(repo_root=tmp_path, data_root=_new_data_root(tmp_path), responses={})
    u.restart(delay_s=0.0)
    for _ in range(50):
        if popen_calls:
            break
        import time as _time

        _time.sleep(0.01)
    assert popen_calls, "Popen never called"
    call = popen_calls[0]
    env = call["env"]
    assert env["TESSERAE_PARENT_PID"]  # not empty
    assert int(env["TESSERAE_PARENT_PID"]) > 0
    # CREATE_NEW_PROCESS_GROUP = 0x200; the child must be in its own
    # group so a Ctrl+C aimed at the parent can't kill it.
    assert call["creationflags"] & 0x00000200
    # And we must have asked the parent process to die so the port is
    # released for the child.
    assert exit_calls == [0]


# ----- wait_for_parent_exit -----------------------------------------


def test_wait_for_parent_exit_is_noop_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX never sets TESSERAE_PARENT_PID (execv replaces in place);
    the helper must return immediately even if the env var is somehow
    present, since ctypes.WinDLL would explode on Linux."""
    from app.updater import PARENT_PID_ENV, wait_for_parent_exit

    monkeypatch.setattr("app.updater._is_windows", lambda: False)
    monkeypatch.setenv(PARENT_PID_ENV, "12345")
    wait_for_parent_exit()
    # And the env var should still be consumed so a re-entry doesn't
    # confuse a future call (defence-in-depth).
    import os as _os

    assert PARENT_PID_ENV not in _os.environ


def test_wait_for_parent_exit_consumes_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env var is single-shot: read once at startup so a child of
    *this* process doesn't inherit the relaunch handshake."""
    from app.updater import PARENT_PID_ENV, wait_for_parent_exit

    monkeypatch.setattr("app.updater._is_windows", lambda: False)
    monkeypatch.setenv(PARENT_PID_ENV, "999999")
    wait_for_parent_exit()
    import os as _os

    assert PARENT_PID_ENV not in _os.environ


# ----- latest_release_via_api ----------------------------------------


class _StubReleaseUpdater(Updater):
    """Updater with the single GitHub-API method stubbed so tests stay
    offline. ``api_responses`` maps an api_path (e.g. "/tags") to the
    JSON-decoded payload to return, or to an exception to raise."""

    def __init__(self, *, repo_root: Path, data_root: Path, api_responses: dict) -> None:
        super().__init__(repo_root=repo_root, data_root=data_root)
        self._api_responses = api_responses
        self.api_calls: list[str] = []

    def _github_get_json(self, repo: str, api_path: str, *, user_agent: str) -> object:
        del repo, user_agent
        self.api_calls.append(api_path)
        resp = self._api_responses[api_path]
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_latest_release_via_api_reads_tags_not_releases(tmp_path: Path) -> None:
    """The Updates card source of truth is the newest pushed tag, not
    the latest published Release. /releases/latest can lag the head by
    a whole weekly Release cycle, so reading it would surface a stale
    version on every fresh source checkout."""
    u = _StubReleaseUpdater(
        repo_root=tmp_path,
        data_root=_new_data_root(tmp_path),
        api_responses={"/tags": [{"name": "v0.64.45"}, {"name": "v0.64.44"}]},
    )
    check = u.latest_release_via_api("0.64.45")
    assert check.latest_tag == "v0.64.45"
    assert check.latest_url == "https://github.com/dmellok/tesserae/releases/tag/v0.64.45"
    assert check.behind is False
    assert check.error is None
    # Single API call: /tags only, no /releases/latest follow-up.
    assert u.api_calls == ["/tags"]


def test_latest_release_via_api_marks_behind_when_local_is_older(tmp_path: Path) -> None:
    u = _StubReleaseUpdater(
        repo_root=tmp_path,
        data_root=_new_data_root(tmp_path),
        api_responses={"/tags": [{"name": "v0.64.45"}]},
    )
    check = u.latest_release_via_api("0.64.40")
    assert check.behind is True
    assert check.latest_tag == "v0.64.45"


def test_latest_release_via_api_handles_empty_tags(tmp_path: Path) -> None:
    """Fresh repo, no tags pushed yet: the card surfaces a clean
    'no tags found' rather than crashing."""
    u = _StubReleaseUpdater(
        repo_root=tmp_path,
        data_root=_new_data_root(tmp_path),
        api_responses={"/tags": []},
    )
    check = u.latest_release_via_api("0.1.0")
    assert check.latest_tag == ""
    assert check.behind is False
    assert check.error == "no tags found"


def test_latest_release_via_api_caches_within_ttl(tmp_path: Path) -> None:
    """Two reads within the TTL window hit GitHub once: prevents a
    multi-tab user from blowing the 60/hr anonymous limit."""
    u = _StubReleaseUpdater(
        repo_root=tmp_path,
        data_root=_new_data_root(tmp_path),
        api_responses={"/tags": [{"name": "v0.64.45"}]},
    )
    u.latest_release_via_api("0.64.45")
    u.latest_release_via_api("0.64.45")
    assert u.api_calls == ["/tags"]  # second call served from cache
