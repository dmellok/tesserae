"""Signed, time-limited render URLs (issue #151).

Unit coverage for the signing helpers plus integration coverage of the
auth gate: a public-origin request to ``/renders/`` is refused without a
signature and served with a valid one, while the secure defaults (LAN
clients, other LAN-reachable paths) are unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.render_signing import render_signature_valid, sign_render_query

_SECRET = "unit-test-secret"
_PATH = "/renders/deadbeefcafe.bin"


def _token(query: str) -> str:
    assert query.startswith("sig=")
    return query.split("=", 1)[1]


# -- unit ----------------------------------------------------------------


def test_sign_and_verify_round_trip() -> None:
    token = _token(sign_render_query(_SECRET, _PATH))
    assert render_signature_valid(_SECRET, _PATH, token) is True


def test_no_secret_disables_signing() -> None:
    assert sign_render_query(None, _PATH) == ""
    assert sign_render_query("", _PATH) == ""
    assert render_signature_valid(None, _PATH, "anything") is False


def test_absent_token_rejected() -> None:
    assert render_signature_valid(_SECRET, _PATH, None) is False
    assert render_signature_valid(_SECRET, _PATH, "") is False


def test_tampered_path_rejected() -> None:
    token = _token(sign_render_query(_SECRET, _PATH))
    assert render_signature_valid(_SECRET, "/renders/other.bin", token) is False


def test_wrong_secret_rejected() -> None:
    token = _token(sign_render_query(_SECRET, _PATH))
    assert render_signature_valid("a-different-secret", _PATH, token) is False


def test_expired_signature_rejected() -> None:
    token = _token(sign_render_query(_SECRET, _PATH))
    # A non-positive max age forces every signature to read as expired.
    assert render_signature_valid(_SECRET, _PATH, token, max_age_s=-1) is False


# -- integration: the auth gate ------------------------------------------

_PUBLIC = {"REMOTE_ADDR": "203.0.113.10"}  # TEST-NET-3, a non-private address


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
    return a


def _seed_frame(app: Flask, name: str = "frame.bin") -> str:
    renders = Path(app.config["RENDERS_DIR"])
    renders.mkdir(parents=True, exist_ok=True)
    (renders / name).write_bytes(b"FRAMEDATA")
    return f"/renders/{name}"


def _enable_public_rest(app: Flask) -> None:
    """Operator opt-in that accepts the public-render-access risk."""
    app.config["SETTINGS_STORE"].patch_section("app", {"public_rest_clients_enabled": True})


def test_public_signature_forbidden_when_opt_in_disabled(app: Flask) -> None:
    # Default: even a valid signature from a public origin is refused until
    # the operator turns on public REST access.
    path = _seed_frame(app)
    query = sign_render_query(app.secret_key, path)
    resp = app.test_client().get(f"{path}?{query}", environ_base=_PUBLIC)
    assert resp.status_code == 403


def test_public_client_without_signature_is_forbidden(app: Flask) -> None:
    _enable_public_rest(app)
    path = _seed_frame(app)
    resp = app.test_client().get(path, environ_base=_PUBLIC)
    assert resp.status_code == 403


def test_public_client_with_valid_signature_is_served(app: Flask) -> None:
    _enable_public_rest(app)
    path = _seed_frame(app)
    query = sign_render_query(app.secret_key, path)
    resp = app.test_client().get(f"{path}?{query}", environ_base=_PUBLIC)
    assert resp.status_code == 200
    assert resp.get_data() == b"FRAMEDATA"
    resp.close()  # release the send_from_directory file handle (Py3.14 warns)


def test_public_client_with_signature_for_another_path_is_forbidden(app: Flask) -> None:
    _enable_public_rest(app)
    _seed_frame(app, "frame.bin")
    _seed_frame(app, "other.bin")
    # Signature minted for other.bin must not unlock frame.bin.
    query = sign_render_query(app.secret_key, "/renders/other.bin")
    resp = app.test_client().get(f"/renders/frame.bin?{query}", environ_base=_PUBLIC)
    assert resp.status_code == 403


def test_private_client_is_served_without_a_signature(app: Flask) -> None:
    path = _seed_frame(app)
    # Default test-client REMOTE_ADDR is 127.0.0.1 (private/loopback); the
    # opt-in doesn't affect LAN clients.
    resp = app.test_client().get(path)
    assert resp.status_code == 200
    resp.close()


def test_signature_does_not_unlock_other_lan_paths(app: Flask) -> None:
    # Only /renders/ is ever handed out signed; /preview/ stays LAN/session
    # only even with public access enabled and a signature appended.
    _enable_public_rest(app)
    query = sign_render_query(app.secret_key, "/preview/kitchen")
    resp = app.test_client().get(f"/preview/kitchen?{query}", environ_base=_PUBLIC)
    assert resp.status_code == 403
