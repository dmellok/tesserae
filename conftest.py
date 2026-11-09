"""Root conftest: keep the repo importable as ``app`` and share fixtures
across the top-level ``tests/`` directory and each plugin's own
``plugins/<id>/tests/`` directory.

pytest auto-walks parent directories for conftest, so fixtures defined here
are visible to every test no matter where it lives in the tree.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Imported after sys.path is set so the in-tree app/ package resolves cleanly
# whether pytest was launched from the repo root or a subdirectory.
from app.main import REPO_ROOT, create_app  # noqa: E402


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
    )


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def _shard_spec() -> tuple[int, int] | None:
    """``TESSERAE_TEST_SHARD=i/n`` (1-based), or None to run everything."""
    raw = os.environ.get("TESSERAE_TEST_SHARD", "").strip()
    if not raw:
        return None
    try:
        index, total = (int(part) for part in raw.split("/", 1))
    except ValueError as err:
        raise pytest.UsageError(f"TESSERAE_TEST_SHARD must look like 1/3, got {raw!r}") from err
    if total < 1 or not 1 <= index <= total:
        raise pytest.UsageError(f"TESSERAE_TEST_SHARD out of range: {raw!r}")
    return index, total


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Run one CI shard of the suite: every n-th collected test, round-robin,
    so slow files spread across shards instead of landing on one. The
    selection depends only on the collection order, so every xdist worker
    picks the same tests."""
    spec = _shard_spec()
    if spec is None:
        return
    index, total = spec
    keep: list[pytest.Item] = []
    drop: list[pytest.Item] = []
    for position, item in enumerate(items):
        (keep if position % total == index - 1 else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
