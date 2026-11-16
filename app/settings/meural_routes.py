"""Netgear sign-in and frame selection for Meural displays."""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from flask import (
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response as RedirectResponse

from app import meural_auth, meural_pending
from app.device_loader import Device
from app.meural import MeuralError, release_sleep
from app.meural_cloud import (
    API,
    clear_session,
    delivery_for_device,
    ensure_frame_available,
    get_cloud_client,
    resource_id,
    save_session,
)

from ._shared import bp, devices, rebuild_transport_fn, settings_store


def _device(instance_id: str) -> Device:
    device = devices().devices.get(instance_id)
    if device is None or device.kind_of != "meural":
        abort(404)
    return device


def _return_url(instance_id: str) -> str:
    return url_for("auth.device_page", instance_id=instance_id, _anchor="connection")


def _stop_publisher() -> None:
    publisher = current_app.config.get("MEURAL_PUBLISHER")
    if publisher is not None:
        publisher.stop()


def _config(instance_id: str) -> dict[str, Any]:
    return (settings_store().get_section("devices") or {}).get(instance_id) or {}


@contextmanager
def _changing_frame() -> Iterator[None]:
    _stop_publisher()
    try:
        with current_app.config.setdefault("MEURAL_IO_LOCK", threading.Lock()):
            yield
    finally:
        rebuild_transport_fn()()


RESTORE_WARNING = (
    "Could not restore Meural playback. Meural may stay paused. "
    "Re-enable rotation in the Meural app. Choose Remove anyway to continue."
)


class RestoreFailed(Exception):
    pass


@bp.errorhandler(RestoreFailed)
def _restore_failed(_error: RestoreFailed) -> tuple[Response, int]:
    return jsonify(error=RESTORE_WARNING, restore_failed=True), 409


@bp.after_request
def _json_rejections(response: Response) -> Response:
    if request.endpoint and request.endpoint.startswith("auth.meural_"):
        response.headers["Cache-Control"] = "no-store"
    if (
        request.endpoint
        and request.endpoint.startswith("auth.meural_")
        and response.status_code >= 400
        and not response.is_json
    ):
        result = jsonify(error="Meural request rejected. Reload this page and try again.")
        result.status_code = response.status_code
        return result
    return response


def _request_data() -> Any:
    data = request.get_json(silent=True) if request.is_json else request.form
    return data if isinstance(data, dict) or not request.is_json else {}


def _release_native(device: Device) -> None:
    """Keep restoration retryable unless the operator explicitly chooses to leave."""
    data = _request_data()
    abandon = (
        isinstance(data, dict) and data.get("remove_anyway") is True
        if request.is_json
        else request.form.get("remove_anyway") == "1"
    )
    settings_store().update_item("devices", device.id, {"meural_management_paused": True})
    failed = False
    try:
        release_sleep(device, str(_config(device.id).get("host") or ""), config=_config(device.id))
    except (MeuralError, OSError, ValueError):
        failed = True
    try:
        delivery = delivery_for_device(current_app, device)
        if delivery is not None:
            delivery[0].release(delivery[1], abandon=abandon)
    except (meural_auth.MeuralAuthError, meural_auth.HttpError, OSError, ValueError):
        failed = True
    if failed and not abandon:
        raise RestoreFailed


def _success(instance_id: str) -> Response | RedirectResponse:
    if request.accept_mimetypes.best == "application/json":
        return jsonify(
            status="ok", return_url=_return_url(instance_id), account=_account_state(instance_id)
        )
    return redirect(_return_url(instance_id))


@bp.route("/settings/meural/login", defaults={"instance_id": None}, methods=["GET", "POST"])
@bp.route("/settings/devices/<instance_id>/meural/login", methods=["GET", "POST"])
def meural_login(instance_id: str | None) -> Response | tuple[Response, int]:
    device = _device(instance_id) if instance_id is not None else None
    client = get_cloud_client(current_app, instance_id) if instance_id is not None else None
    lock = client.lock if client is not None else meural_pending.pending_lock()
    if instance_id is None and request.method == "GET":
        meural_pending.discard_pending()
    session_id = (
        instance_id
        if instance_id is not None
        else "add:" + meural_pending.pending_key(create=request.method == "GET")
    )
    return_url = (
        _return_url(instance_id)
        if instance_id is not None
        else url_for("auth.settings_area", area="devices", _anchor="add-device")
    )
    sessions = current_app.config.setdefault("MEURAL_LOGIN_SESSIONS", {})
    if request.method == "GET":
        nonce = secrets.token_urlsafe(24)
        cfg = _config(instance_id) if instance_id is not None else {}
        with lock:
            sessions[session_id] = {
                "state": secrets.token_urlsafe(32),
                "trust_id": cfg.get("meural_trust_id") or str(uuid.uuid4()),
                "expires_at": time.monotonic() + 600,
            }
            pending = dict(sessions[session_id])
        response = current_app.make_response(
            render_template(
                "meural_login.html",
                nonce=nonce,
                device_name=device.name if device is not None else "Add device",
                return_url=return_url,
                auth_config={
                    "state": pending["state"],
                    "trust_id": pending["trust_id"],
                    "email": cfg.get("meural_email", ""),
                    "cognito_client_id": meural_auth.COGNITO_CLIENT_ID,
                    "cognito_url": meural_auth.COGNITO_URL,
                    "response_keys": meural_auth.RESPONSE_KEY_MAP,
                    "password_exclusions": meural_auth.PASSWORD_CHALLENGE_EXCLUSION_KEYWORDS,
                    "migration_keywords": meural_auth.USER_MIGRATION_KEYWORDS,
                    "waf_keywords": meural_auth.WAF_ERROR_KEYWORDS,
                    "waf_page_keywords": meural_auth.WAF_BLOCK_PAGE_KEYWORDS,
                },
            )
        )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                    f"connect-src 'self' {meural_auth.COGNITO_URL}; "
                    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
                ),
            }
        )
        return response
    if not request.is_json or request.content_length is None or request.content_length > 16384:
        return jsonify(error="Invalid sign-in request. Reload this page and try again."), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {"remove_anyway"} != {
        "state",
        "trust_id",
        "email",
        "cognito_access_token",
    }:
        return jsonify(error="Invalid sign-in request. Reload this page and try again."), 400
    email, token = data["email"], data["cognito_access_token"]
    if not isinstance(email, str) or not 3 <= len(email.strip()) <= 320 or "@" not in email:
        return jsonify(error="Invalid sign-in request. Reload this page and try again."), 400
    if not isinstance(token, str) or not 100 <= len(token) <= 8192:
        return jsonify(error="Invalid sign-in request. Reload this page and try again."), 400
    with lock:
        pending = sessions.get(session_id)
        if (
            not pending
            or pending["expires_at"] <= time.monotonic()
            or (
                pending.get("consumed")
                and not (data.get("remove_anyway") is True and pending.get("staged"))
            )
        ):
            return jsonify(error="Invalid sign-in request. Reload this page and try again."), 400
        if not isinstance(data["state"], str) or not secrets.compare_digest(
            data["state"], pending["state"]
        ):
            return jsonify(error="Invalid sign-in request. Reload this page and try again."), 400
        if data["trust_id"] != pending["trust_id"]:
            return jsonify(error="Invalid sign-in request. Reload this page and try again."), 400
        pending["consumed"] = True
    try:
        if pending.get("staged"):
            sealed, account_id, frames = pending["staged"]
            result = meural_pending.pending_tokens(sealed)
        else:
            result = meural_auth.NetgearAuthenticator(pending["trust_id"]).exchange_cognito_token(
                token
            )
            headers = {
                "Authorization": f"Token {result.access_token}",
                "Accept": "application/json",
                "x-meural-api-version": "4",
                "x-meural-source-platform": "web",
            }
            user = meural_auth.request_json("GET", API + "user", headers=headers)
            if not isinstance(user, dict):
                raise ValueError("Meural returned an invalid account")
            user = user.get("data", user)
            if not isinstance(user, dict):
                raise ValueError("Meural returned an invalid account")
            frames = []
            page = 1
            while True:
                inventory = meural_auth.request_json(
                    "GET", API + f"user/devices?count=100&page={page}", headers=headers
                )
                if not isinstance(inventory, dict):
                    raise ValueError("Meural returned an invalid frame list")
                rows = inventory.get("data", [])
                if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                    raise ValueError("Meural returned an invalid frame list")
                frames.extend(rows)
                if inventory.get("isLast", True):
                    break
                page += 1
            account_id = resource_id(user.get("id"))
            pending["staged"] = (meural_pending.seal_tokens(result), account_id, frames)
        if instance_id is None:
            with lock:
                if (
                    sessions.get(session_id) is not pending
                    or pending["expires_at"] <= time.monotonic()
                ):
                    return jsonify(error="Sign-in expired. Start again."), 400
                meural_pending.stage_account(result, account_id, email.strip(), frames)
                sessions.pop(session_id, None)
            return jsonify(
                status="ok", return_url=return_url, account=meural_pending.pending_state()
            )
        assert device is not None and client is not None
        with _changing_frame(), lock:
            if (
                sessions.get(session_id) is not pending
                or devices().devices.get(instance_id) is not device
                or pending["expires_at"] <= time.monotonic()
            ):
                return jsonify(
                    error="Invalid sign-in request. Reload this page and try again."
                ), 400
            cfg = _config(instance_id) if instance_id is not None else {}
            selected = next(
                (f for f in frames if str(f.get("id")) == str(cfg.get("cloud_device_id"))), None
            )
            if selected is None and len(frames) == 1:
                selected = frames[0]
            target = resource_id(selected["id"]) if selected else ""
            ensure_frame_available(settings_store(), instance_id, target)
            same_frame = (
                str(cfg.get("meural_account_id")) == account_id
                and str(cfg.get("cloud_device_id") or "") == target
            )
            if not same_frame:
                _release_native(device)
            save_session(
                settings_store(),
                instance_id,
                result,
                email=email.strip(),
                account_id=account_id,
                devices=frames,
            )
            client.reset_auth()
            sessions.pop(session_id, None)
    except (meural_auth.MeuralAuthError, meural_auth.HttpError, ValueError) as exc:
        with lock:
            if sessions.get(session_id) is pending:
                sessions.pop(session_id)
        return jsonify(error=f"{exc} Reload this page to start again."), 400
    return jsonify(status="ok", return_url=return_url, account=_account_state(instance_id))


@bp.post("/settings/devices/<instance_id>/meural/logout")
def meural_logout(instance_id: str) -> Response | RedirectResponse:
    device = _device(instance_id)
    client = get_cloud_client(current_app, instance_id)
    with _changing_frame(), client.lock:
        current_app.config.get("MEURAL_LOGIN_SESSIONS", {}).pop(instance_id, None)
        settings_store().update_item("devices", instance_id, {"meural_management_paused": True})
        _release_native(device)
        clear_session(settings_store(), instance_id)
        client.reset_auth()
    return _success(instance_id)


@bp.post("/settings/devices/<instance_id>/meural/frame")
def meural_select_frame(instance_id: str) -> Response | RedirectResponse | tuple[Response, int]:
    device = _device(instance_id)
    client = get_cloud_client(current_app, instance_id)
    with _changing_frame(), client.lock:
        cfg = _config(instance_id)
        data = _request_data()
        selected = (
            data.get("cloud_device_id") if isinstance(data, dict) or not request.is_json else None
        )
        frame = next((f for f in cfg.get("meural_devices", []) if f["id"] == selected), None)
        if frame is None:
            return jsonify(error="Choose a frame from the linked Meural account."), 400
        try:
            ensure_frame_available(settings_store(), instance_id, str(selected))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        if str(cfg.get("cloud_device_id")) != selected:
            _release_native(device)
        settings_store().update_item(
            "devices",
            instance_id,
            {
                "cloud_device_id": selected,
                "host": frame.get("host", ""),
                "meural_management_paused": False,
            },
        )
    return _success(instance_id)


@bp.post("/settings/devices/<instance_id>/meural/manage")
def meural_manage(instance_id: str) -> Response | RedirectResponse | tuple[Response, int]:
    device = _device(instance_id)
    data = _request_data()
    action = data.get("action") if isinstance(data, dict) or not request.is_json else None
    if action not in ("manage", "release"):
        return jsonify(error="Choose manage or release."), 400
    with _changing_frame(), get_cloud_client(current_app, instance_id).lock:
        settings_store().update_item(
            "devices",
            instance_id,
            {"meural_management_paused": action == "release"},
        )
        if action == "release":
            _release_native(device)
    return _success(instance_id)


def _account_state(instance_id: str) -> dict[str, Any]:
    login = current_app.config.get("MEURAL_LOGIN_SESSIONS", {}).get(instance_id)
    return meural_pending.account_state(
        _config(instance_id),
        saved=True,
        signing_in=bool(login and login["expires_at"] > time.monotonic()),
    )


@bp.get("/settings/meural/account", defaults={"instance_id": None})
@bp.get("/settings/devices/<instance_id>/meural/account")
def meural_account(instance_id: str | None) -> Response:
    if instance_id is not None:
        _device(instance_id)
    return jsonify(
        account=_account_state(instance_id)
        if instance_id is not None
        else meural_pending.pending_state()
    )


@bp.post("/settings/meural/frame")
def meural_pending_frame() -> Response | tuple[Response, int]:
    with meural_pending.pending_lock():
        entry = meural_pending.pending_account()
        data = _request_data()
        selected = data.get("cloud_device_id") if data else None
        if entry is None or not any(f["id"] == selected for f in entry["frames"]):
            return jsonify(error="Sign in and choose a frame from your Meural account."), 400
        try:
            ensure_frame_available(settings_store(), "", str(selected))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        entry["selected"] = selected
        return jsonify(status="ok", account=meural_pending.pending_state())


@bp.post("/settings/meural/logout")
@bp.post("/settings/meural/cancel")
def meural_pending_logout() -> Response:
    meural_pending.discard_pending()
    return jsonify(status="ok", account=meural_pending.pending_state())


def add_pending_device() -> Response | RedirectResponse | tuple[Response, int]:
    from app import device_service, renderer_loader
    from app.panel import panel_overrides_from_form

    from ._shared import device_data_root, renderers

    with (
        meural_pending.pending_lock(),
        current_app.config.setdefault("MEURAL_IO_LOCK", threading.Lock()),
    ):
        entry = meural_pending.pending_account()
        data = _request_data()
        selected = (
            data.get("cloud_device_id")
            if isinstance(data, dict) or not request.is_json
            else None or (entry["selected"] if entry else "")
        )
        if entry is None or not selected or not any(f["id"] == selected for f in entry["frames"]):
            return jsonify(error="Sign in and choose a Meural frame before adding the device."), 400
        try:
            ensure_frame_available(settings_store(), "", str(selected))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        try:
            tokens = meural_pending.pending_tokens(entry)
        except (ValueError, OSError):
            return jsonify(error="Could not use the pending Meural account. Sign in again."), 400
        result = device_service.create_instance(
            devices=devices(),
            renderers=renderers(),
            data_root=device_data_root(),
            instance_id=request.form.get("id") or "",
            kind_id="meural",
            name=request.form.get("name") or "",
            panel_overrides=panel_overrides_from_form(request.form),
            orientation=request.form.get("panel_orientation"),
        )
        if not result.ok or result.device is None:
            return jsonify(error=result.error or "Could not add the Meural device."), 400
        previous = settings_store().get_section("devices").get(result.device.id)
        try:
            save_session(
                settings_store(),
                result.device.id,
                tokens,
                email=entry["email"],
                account_id=entry["account_id"],
                devices=entry["frames"],
                selected_id=selected,
            )
            device_service.device_added(settings_store(), result.device)
            renderer_loader.seed_device_settings_from_base(renderers(), settings_store())
        except (OSError, ValueError):
            device_service.delete_instance(
                devices=devices(), renderers=renderers(), instance_id=result.device.id
            )
            settings_store().remove_item("devices", result.device.id)
            if previous is not None:
                settings_store().update_item("devices", result.device.id, previous)
            return jsonify(error="Could not save the Meural device. Try again."), 400
        meural_pending.discard_pending()
    rebuild_transport_fn()()
    return redirect(url_for("auth.device_page", instance_id=result.device.id))


@bp.route("/settings/devices/<instance_id>/meural/sleep", methods=["GET", "POST"])
@bp.post("/settings/devices/<instance_id>/meural/sleep/refresh", endpoint="meural_sleep_refresh")
def meural_sleep(instance_id: str) -> Response | RedirectResponse | tuple[Response, int]:
    from app.meural_schedule import edit_schedule, sync_schedule

    device = _device(instance_id)
    try:
        with current_app.config.setdefault("MEURAL_IO_LOCK", threading.Lock()):
            if request.method == "POST" and not request.path.endswith("/refresh"):
                data = _request_data()
                if request.form.get("return_to_device"):
                    data = {
                        "start": request.form.get("start"),
                        "end": request.form.get("end"),
                        "enabled": request.form.get("enabled") == "1",
                        "days": request.form.getlist("days"),
                    }
                if not isinstance(data, dict):
                    return jsonify(error="Send the frame sleep settings as JSON."), 400
                state = edit_schedule(current_app, device, data)
            else:
                state = sync_schedule(current_app, device, force=request.method == "POST")
        if request.form.get("return_to_device"):
            return redirect(
                url_for("auth.device_page", instance_id=instance_id, _anchor="frame-sleep")
            )
        return jsonify(status="ok", sleep=state)
    except (meural_auth.MeuralAuthError, meural_auth.HttpError, OSError, ValueError):
        if request.form.get("return_to_device"):
            flash("Could not update the frame sleep schedule. Check it in the Meural app.", "error")
            return redirect(
                url_for("auth.device_page", instance_id=instance_id, _anchor="frame-sleep")
            )
        return jsonify(
            error="Could not update the frame sleep schedule. Check it in the Meural app."
        ), 400
