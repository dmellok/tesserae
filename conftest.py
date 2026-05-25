"""Root conftest: keep the repo importable as ``app`` and share fixtures
across the top-level ``tests/`` directory and each plugin's own
``plugins/<id>/tests/`` directory.

pytest auto-walks parent directories for conftest, so fixtures defined here
are visible to every test no matter where it lives in the tree.
"""

from __future__ import annotations

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
