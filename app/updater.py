"""Self-update: pull a newer git revision, reinstall deps if changed,
re-exec the process. Includes a rollback path to the previous revision.

Two channels:

* ``edge``   — track ``origin/<default-branch>`` (typically ``main``).
* ``stable`` — pin to the most recent annotated tag on the remote.

The flow on apply:

1. Acquire the push lock (refuse to update mid-push).
2. Take a pre-update :mod:`~app.backup` snapshot.
3. Capture the current ``HEAD`` SHA (for rollback).
4. ``git fetch`` then either ``git pull --ff-only`` (edge) or
   ``git reset --hard <tag-sha>`` (stable / force).
5. If ``pyproject.toml`` changed between the old and new SHA, run
   ``pip install -e .`` so newly-added base deps land.
6. Record the transition under ``data/core/.update_history.json``.
7. Schedule :py:meth:`Updater.restart` — ``os.execv`` replaces the process
   image so the running Python re-imports every ``app.*`` module from the
   freshly-pulled files. Brief blip (~1 s); in-flight requests drop.

The system is **remote-code-execution by design** (pulling and installing
code from a remote). It is gated behind admin auth + the push lock and is
deliberately user-initiated — there is no silent auto-update path here.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path

from app import backup as _backup

logger = logging.getLogger(__name__)

HISTORY_FILENAME = ".update_history.json"
HISTORY_KEEP = 10
GIT_TIMEOUT_S = 60
PIP_TIMEOUT_S = 300


def _is_windows() -> bool:
    """Tested-as-a-function so the Windows / POSIX restart branches are
    monkeypatch-able without mutating the global ``os.name``. Mutating
    ``os.name`` globally breaks pathlib (``WindowsPath`` cannot be
    instantiated on POSIX), which surfaces as a pytest ``INTERNALERROR``
    when the test's failure formatter then tries to construct a Path."""
    return os.name == "nt"


Channel = str  # "edge" | "stable"
CHANNELS: tuple[str, ...] = ("edge", "stable")


@dataclass(frozen=True)
class UpdateState:
    version: str  # from package metadata (pyproject.toml)
    sha: str  # current HEAD SHA
    short_sha: str
    branch: str  # current branch (or "DETACHED" if HEAD-detached)
    default_branch: str  # the remote's default (origin/HEAD → main)
    dirty: bool  # working tree has uncommitted changes


@dataclass(frozen=True)
class ChangelogEntry:
    sha: str
    subject: str


@dataclass(frozen=True)
class RemoteCheck:
    channel: Channel
    available: bool  # True iff target_sha differs from current and is ahead
    current_sha: str
    target_sha: str
    target_ref: str  # human label: "origin/main" or "v0.3.0"
    commits_behind: int
    changelog: list[ChangelogEntry] = field(default_factory=list)


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    from_sha: str
    to_sha: str
    backup_id: str
    pip_changed: bool
    error: str | None = None


@dataclass(frozen=True)
class HistoryEntry:
    at: float
    from_sha: str
    to_sha: str
    channel: str
    backup_id: str
    pip_changed: bool


class UpdaterError(RuntimeError):
    """Distinguishes our deliberate refuse-to-proceed from incidental
    subprocess failures."""


class Updater:
    """Repo-level update orchestration. Stateless beyond
    ``data/core/.update_history.json``; one instance per app is fine."""

    def __init__(self, repo_root: Path, data_root: Path, package_name: str = "tesserae") -> None:
        self._repo = Path(repo_root)
        self._data = Path(data_root)
        self._package = package_name
        # In-memory cache of the most recent check_remote() result so the
        # Settings → System page can show "X commits behind" without
        # re-hitting the network on every render. Reset on restart.
        self._last_check: RemoteCheck | None = None

    @property
    def last_check(self) -> RemoteCheck | None:
        return self._last_check

    # -- queries --------------------------------------------------------

    def current_state(self) -> UpdateState:
        sha = self._git("rev-parse", "HEAD")
        short = self._git("rev-parse", "--short", "HEAD")
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        if branch == "HEAD":
            branch = "DETACHED"
        default = self._default_branch()
        dirty = bool(self._git("status", "--porcelain"))
        try:
            version = metadata.version(self._package)
        except metadata.PackageNotFoundError:
            version = "unknown"
        return UpdateState(
            version=version,
            sha=sha,
            short_sha=short,
            branch=branch,
            default_branch=default,
            dirty=dirty,
        )

    def check_remote(self, channel: Channel) -> RemoteCheck:
        if channel not in CHANNELS:
            raise UpdaterError(f"unknown channel {channel!r}")
        # Fetch refs + tags so target resolution is up to date.
        self._git("fetch", "--tags", "--quiet", timeout=GIT_TIMEOUT_S)
        current = self._git("rev-parse", "HEAD")
        if channel == "edge":
            target_ref = f"origin/{self._default_branch()}"
            target = self._git("rev-parse", target_ref)
        else:
            tag = self._latest_tag()
            if not tag:
                return RemoteCheck(
                    channel=channel,
                    available=False,
                    current_sha=current,
                    target_sha=current,
                    target_ref="(no tags published)",
                    commits_behind=0,
                )
            target_ref = tag
            target = self._git("rev-list", "-n", "1", tag)
        commits_behind = int(self._git("rev-list", "--count", f"{current}..{target}") or "0")
        log_text = self._git("log", f"{current}..{target}", "--format=%h %s", "--max-count=20")
        changelog = [
            ChangelogEntry(sha=parts[0], subject=parts[1])
            for line in log_text.splitlines()
            if (parts := line.split(" ", 1)) and len(parts) == 2
        ]
        result = RemoteCheck(
            channel=channel,
            available=current != target and commits_behind > 0,
            current_sha=current,
            target_sha=target,
            target_ref=target_ref,
            commits_behind=commits_behind,
            changelog=changelog,
        )
        self._last_check = result
        return result

    def history(self) -> list[HistoryEntry]:
        path = self._history_path()
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        out: list[HistoryEntry] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                out.append(
                    HistoryEntry(
                        at=float(entry["at"]),
                        from_sha=str(entry["from_sha"]),
                        to_sha=str(entry["to_sha"]),
                        channel=str(entry.get("channel", "")),
                        backup_id=str(entry.get("backup_id", "")),
                        pip_changed=bool(entry.get("pip_changed", False)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    # -- mutation -------------------------------------------------------

    def apply_update(
        self, channel: Channel, *, force: bool = False, push_lock: object | None = None
    ) -> UpdateResult:
        """Pull + (maybe) reinstall + record history. Does NOT restart —
        the caller schedules :py:meth:`restart` once it's done flushing
        a UI response. ``push_lock`` (when provided) must be a
        :class:`threading.Lock` we hold for the duration so a frame push
        can't race with a tree change."""
        if channel not in CHANNELS:
            raise UpdaterError(f"unknown channel {channel!r}")
        held = False
        if push_lock is not None:
            held = bool(push_lock.acquire(blocking=True, timeout=10))  # type: ignore[attr-defined]
            if not held:
                raise UpdaterError("another push is in flight — try again in a moment")
        try:
            check = self.check_remote(channel)
            if not check.available and not force:
                from_sha = check.current_sha
                return UpdateResult(
                    ok=True,
                    from_sha=from_sha,
                    to_sha=from_sha,
                    backup_id="",
                    pip_changed=False,
                )

            # 1. Snapshot data/ before touching anything.
            backup = _backup.create(
                self._data,
                label=_backup.LABEL_PRE_UPDATE,
                note=f"{check.current_sha[:7]} → {check.target_sha[:7]} ({channel})",
            )

            from_sha = check.current_sha
            pre_pyproject = self._show_blob(from_sha, "pyproject.toml")

            try:
                if channel == "edge" and not force:
                    # ff-only is safe; fails if local branch diverged.
                    self._git("pull", "--ff-only", "--quiet", timeout=GIT_TIMEOUT_S)
                else:
                    # Hard-pin to the target ref. Used for stable channel
                    # and for the edge --force path.
                    self._git("reset", "--hard", check.target_sha, timeout=GIT_TIMEOUT_S)
            except UpdaterError as err:
                return UpdateResult(
                    ok=False,
                    from_sha=from_sha,
                    to_sha=from_sha,
                    backup_id=backup.id,
                    pip_changed=False,
                    error=str(err),
                )

            to_sha = self._git("rev-parse", "HEAD")
            post_pyproject = (self._repo / "pyproject.toml").read_text(encoding="utf-8")
            pip_changed = pre_pyproject != post_pyproject
            if pip_changed:
                self._pip_install()

            self._record_history(
                HistoryEntry(
                    at=time.time(),
                    from_sha=from_sha,
                    to_sha=to_sha,
                    channel=channel,
                    backup_id=backup.id,
                    pip_changed=pip_changed,
                )
            )
            return UpdateResult(
                ok=True,
                from_sha=from_sha,
                to_sha=to_sha,
                backup_id=backup.id,
                pip_changed=pip_changed,
            )
        finally:
            if held and push_lock is not None:
                with contextlib.suppress(RuntimeError):
                    push_lock.release()  # type: ignore[attr-defined]

    def rollback_last(self, *, push_lock: object | None = None) -> UpdateResult:
        """Reset the working tree to the previous update's ``from_sha``
        AND restore that update's data snapshot. Used when an update
        broke something."""
        entries = self.history()
        if not entries:
            raise UpdaterError("no previous update to roll back")
        last = entries[-1]
        held = False
        if push_lock is not None:
            held = bool(push_lock.acquire(blocking=True, timeout=10))  # type: ignore[attr-defined]
            if not held:
                raise UpdaterError("another push is in flight — try again in a moment")
        try:
            current_sha = self._git("rev-parse", "HEAD")
            try:
                self._git("reset", "--hard", last.from_sha, timeout=GIT_TIMEOUT_S)
            except UpdaterError as err:
                return UpdateResult(
                    ok=False,
                    from_sha=current_sha,
                    to_sha=current_sha,
                    backup_id=last.backup_id,
                    pip_changed=False,
                    error=str(err),
                )
            if last.backup_id:
                with contextlib.suppress(FileNotFoundError):
                    _backup.restore(self._data, last.backup_id)
            if last.pip_changed:
                # The previous revision may have older deps installed
                # against newer pyproject — reinstall to align.
                self._pip_install()
            return UpdateResult(
                ok=True,
                from_sha=current_sha,
                to_sha=last.from_sha,
                backup_id=last.backup_id,
                pip_changed=last.pip_changed,
            )
        finally:
            if held and push_lock is not None:
                with contextlib.suppress(RuntimeError):
                    push_lock.release()  # type: ignore[attr-defined]

    def restart(self, *, delay_s: float = 1.0) -> None:
        """Schedule a relaunch after ``delay_s`` so an in-flight HTTP
        response has time to flush. The new process picks up the
        freshly-pulled code; in-flight requests drop. No-op under
        ``--dev`` (the werkzeug reloader handles restarts there).

        Implementation differs by OS:

        * **POSIX**: ``os.execv`` replaces the current process image —
          the kernel hands the listening socket FD straight to the new
          Python, which re-imports everything from the new files. Clean
          and atomic.
        * **Windows**: ``os.execv`` is *not* a real exec — CPython
          implements it as spawn + exit, which (a) tangles console
          handles so the new process can die when the parent's console
          closes, and (b) races the listening socket's TIME_WAIT release
          so ``waitress`` ``EADDRINUSE``s at startup. Instead we Popen
          a child in its own process group, stash our PID in
          ``TESSERAE_PARENT_PID``, and ``os._exit(0)``. The child
          (see :func:`wait_for_parent_exit`) blocks until that PID has
          gone before binding."""
        argv = [sys.executable, "-m", "app.main", *sys.argv[1:]]

        def _go() -> None:
            logger.info("update: relaunching %s", argv)
            if _is_windows():
                env = os.environ.copy()
                env["TESSERAE_PARENT_PID"] = str(os.getpid())
                # CREATE_NEW_PROCESS_GROUP: the child ignores any
                # CTRL_C aimed at us, so it survives the parent's exit
                # cleanly. We deliberately don't pass DETACHED_PROCESS
                # — keeping the inherited console means startup logs
                # continue to flow into the user's terminal.
                CREATE_NEW_PROCESS_GROUP = 0x00000200
                subprocess.Popen(
                    argv,
                    env=env,
                    creationflags=CREATE_NEW_PROCESS_GROUP,
                    close_fds=False,
                )
                os._exit(0)
            else:
                os.execv(sys.executable, argv)

        threading.Timer(delay_s, _go).start()

    # -- internals ------------------------------------------------------

    def _git(self, *args: str, check: bool = True, timeout: float = GIT_TIMEOUT_S) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self._repo,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=check,
            )
        except subprocess.CalledProcessError as err:
            stderr = (err.stderr or "").strip().splitlines()
            msg = stderr[-1] if stderr else f"git {' '.join(args)} exited {err.returncode}"
            raise UpdaterError(f"git: {msg}") from err
        except FileNotFoundError as err:
            raise UpdaterError("git: command not found on PATH") from err
        except subprocess.TimeoutExpired as err:
            raise UpdaterError(f"git: timed out after {timeout}s") from err
        return (proc.stdout or "").strip()

    def _default_branch(self) -> str:
        """Resolve the remote's default branch (typically ``main``).
        Falls back to the current branch if origin/HEAD isn't set."""
        try:
            ref = self._git("rev-parse", "--abbrev-ref", "origin/HEAD")
            if ref.startswith("origin/"):
                return ref[len("origin/") :]
            return ref
        except UpdaterError:
            return self._git("rev-parse", "--abbrev-ref", "HEAD")

    def _latest_tag(self) -> str:
        """Most recent semver-ish tag on any remote ref. Returns empty
        string if none exist."""
        out = self._git(
            "for-each-ref", "--sort=-creatordate", "--format=%(refname:short)", "refs/tags"
        )
        for line in out.splitlines():
            line = line.strip()
            if line:
                return line
        return ""

    def _show_blob(self, sha: str, path: str) -> str:
        try:
            return self._git("show", f"{sha}:{path}")
        except UpdaterError:
            return ""

    def _pip_install(self) -> None:
        # Base deps only — extras ([dev]/[docs]) are install-time choices
        # and we don't know which the user picked. If a new extra's dep
        # is needed, the UI nudge says to re-pip with the same extras.
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", "."],
                cwd=self._repo,
                check=True,
                timeout=PIP_TIMEOUT_S,
            )
        except subprocess.CalledProcessError as err:
            raise UpdaterError(f"pip install failed (exit {err.returncode})") from err
        except subprocess.TimeoutExpired as err:
            raise UpdaterError(f"pip install timed out after {PIP_TIMEOUT_S}s") from err

    def _history_path(self) -> Path:
        return self._data / "core" / HISTORY_FILENAME

    def _record_history(self, entry: HistoryEntry) -> None:
        path = self._history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        items = self.history()
        items.append(entry)
        items = items[-HISTORY_KEEP:]
        path.write_text(json.dumps([asdict(e) for e in items], indent=2), encoding="utf-8")


# ----- restart helpers (consumed by main._serve) ---------------------


PARENT_PID_ENV = "TESSERAE_PARENT_PID"
_PARENT_WAIT_TIMEOUT_S = 10.0
_PARENT_GRACE_S = 0.5


def wait_for_parent_exit() -> None:
    """Windows-only: block until the relaunching parent process (see
    :meth:`Updater.restart`) has actually exited, then a brief grace
    pause so the OS reclaims the listening socket before waitress tries
    to bind it. No-op on POSIX (``os.execv`` already gave us the FD).

    Bounded by :data:`_PARENT_WAIT_TIMEOUT_S` so a stuck parent can't
    deadlock the relaunch — after that we proceed and accept the small
    chance of an ``EADDRINUSE``."""
    parent_pid = os.environ.pop(PARENT_PID_ENV, None)
    if not parent_pid or not _is_windows():
        return
    try:
        pid = int(parent_pid)
    except ValueError:
        return

    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return  # parent already gone — go.
    try:
        deadline = time.monotonic() + _PARENT_WAIT_TIMEOUT_S
        code = ctypes.c_ulong(STILL_ACTIVE)
        while time.monotonic() < deadline:
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                break
            if code.value != STILL_ACTIVE:
                break
            time.sleep(0.1)
    finally:
        kernel32.CloseHandle(handle)
    # Brief grace so the kernel reclaims the listening socket before
    # waitress tries to bind. Without this we still occasionally lose
    # the bind race on Windows even after the parent process is dead.
    time.sleep(_PARENT_GRACE_S)
