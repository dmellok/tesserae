"""Settings → System endpoints: self-update, backup, webhook, telemetry.

Updates and restore-from-backup both restart the process via os.execv
(the in-process Playwright renderer + admin UI come back ~1 s later).
Refused in ``--dev``: the werkzeug reloader owns restarts there.
All gated by the push manager's lock so a frame push can't race with a
tree-mutating operation.
"""

from __future__ import annotations

import io

from flask import current_app, flash, request, send_file, session
from werkzeug.wrappers import Response

from app import backup as _backup_mod
from app import updater as _updater_mod

from ._shared import (
    bp,
    data_root,
    push_manager,
    refuse_in_container,
    refuse_in_dev,
    settings_store,
    system_redirect,
    updater,
)

# -- updates ------------------------------------------------------------


@bp.post("/settings/system/update/check")
def system_update_check() -> Response:
    channel = (request.form.get("channel") or "edge").strip()
    try:
        check = updater().check_remote(channel)
    except _updater_mod.UpdaterError as err:
        flash(f"Check failed: {err}", "error")
        return system_redirect()
    if check.available:
        flash(
            f"Update available: {check.commits_behind} commit"
            f"{'s' if check.commits_behind != 1 else ''} behind "
            f"{check.target_ref}.",
            "ok",
        )
    else:
        flash(f"Up to date with {check.target_ref}.", "ok")
    return system_redirect()


@bp.post("/settings/system/update/apply")
def system_update_apply() -> Response:
    refused = refuse_in_dev() or refuse_in_container()
    if refused is not None:
        return refused
    channel = (request.form.get("channel") or "edge").strip()
    force = request.form.get("force") == "1"
    push_mgr = push_manager()
    try:
        result = updater().apply_update(channel, force=force, push_lock=push_mgr._lock)
    except _updater_mod.UpdaterError as err:
        flash(str(err), "error")
        return system_redirect()
    if not result.ok:
        flash(f"Update failed: {result.error or 'unknown error'}", "error")
        return system_redirect()
    if result.from_sha == result.to_sha:
        flash(f"Already up to date ({result.to_sha[:7]}).", "ok")
        return system_redirect()
    note = f"Updated {result.from_sha[:7]} → {result.to_sha[:7]}"
    if result.pip_changed:
        note += " (deps reinstalled)"
    flash(note + ". Restarting…", "ok")
    telemetry = current_app.config.get("TELEMETRY")
    if telemetry is not None:
        telemetry.send(
            "update.applied",
            {
                "from": result.from_sha[:7],
                "to": result.to_sha[:7],
                "channel": channel,
                "pip_changed": "yes" if result.pip_changed else "no",
            },
        )
    updater().restart(delay_s=1.5)
    return system_redirect()


@bp.post("/settings/system/update/rollback")
def system_update_rollback() -> Response:
    refused = refuse_in_dev() or refuse_in_container()
    if refused is not None:
        return refused
    push_mgr = push_manager()
    try:
        result = updater().rollback_last(push_lock=push_mgr._lock)
    except _updater_mod.UpdaterError as err:
        flash(str(err), "error")
        return system_redirect()
    if not result.ok:
        flash(f"Rollback failed: {result.error or 'unknown error'}", "error")
        return system_redirect()
    flash(f"Rolled back to {result.to_sha[:7]}. Restarting…", "ok")
    updater().restart(delay_s=1.5)
    return system_redirect()


# -- backups ------------------------------------------------------------


@bp.post("/settings/system/backup/create")
def system_backup_create() -> Response:
    note = (request.form.get("note") or "").strip()[:200]
    try:
        backup = _backup_mod.create(data_root(), label=_backup_mod.LABEL_MANUAL, note=note)
    except OSError as err:
        flash(f"Backup failed: {err}", "error")
        return system_redirect()
    flash(f"Backup created: {backup.id} ({backup.bytes // 1024} KB).", "ok")
    return system_redirect()


@bp.get("/settings/system/backup/<backup_id>/download")
def system_backup_download(backup_id: str) -> Response:
    backup = _backup_mod.get(data_root(), backup_id)
    if backup is None:
        return Response("backup not found", status=404)
    # Serve from a BytesIO so the underlying file is fully read + closed
    # before the response is built, avoids a lingering file handle that
    # finalizers complain about under the test client.
    data = backup.path.read_bytes()
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"tesserae-backup-{backup_id}.zip",
        mimetype="application/zip",
    )


@bp.post("/settings/system/backup/<backup_id>/restore")
def system_backup_restore(backup_id: str) -> Response:
    refused = refuse_in_dev() or refuse_in_container()
    if refused is not None:
        return refused
    push_mgr = push_manager()
    if not push_mgr._lock.acquire(blocking=True, timeout=10):
        flash("Another push is in flight, try again in a moment.", "error")
        return system_redirect()
    try:
        try:
            _backup_mod.restore(data_root(), backup_id)
        except (FileNotFoundError, ValueError, OSError) as err:
            flash(f"Restore failed: {err}", "error")
            return system_redirect()
    finally:
        push_mgr._lock.release()
    flash(f"Restored from {backup_id}. Restarting…", "ok")
    updater().restart(delay_s=1.5)
    return system_redirect()


@bp.post("/settings/system/backup/<backup_id>/delete")
def system_backup_delete(backup_id: str) -> Response:
    if _backup_mod.delete(data_root(), backup_id):
        flash(f"Deleted backup {backup_id}.", "ok")
    else:
        flash(f"No backup named {backup_id}.", "error")
    return system_redirect()


# -- one-shot export / import (migrate to a fresh Tesserae) -------------


@bp.get("/settings/system/data/export")
def system_data_export() -> Response:
    """Generate a fresh data ``.zip`` and stream it to the user. Reuses
    the backup machinery (same exclusions: gallery photos + the render
    cache stay out) but deletes the staged file afterwards so this
    doesn't pollute the Backups list. For moving data between Tesserae
    installs, restore-from-existing-backup is the in-place flow."""
    try:
        backup = _backup_mod.create(data_root(), label=_backup_mod.LABEL_MANUAL, note="export")
    except OSError as err:
        flash(f"Export failed: {err}", "error")
        return system_redirect()
    try:
        data = backup.path.read_bytes()
    finally:
        backup.path.unlink(missing_ok=True)
    import time as _time

    fname = f"tesserae-data-{_time.strftime('%Y%m%d-%H%M%S')}.zip"
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=fname,
        mimetype="application/zip",
    )


@bp.post("/settings/system/data/import")
def system_data_import() -> Response:
    """Accept a zip uploaded from another Tesserae install and apply it
    in place of the current ``data/``. Same exclusions as restore
    (the current gallery photos + cached renders stay put). Restarts
    the server afterwards on production; in --dev mode the os.execv
    restart fights the werkzeug reloader, so we extract the zip and
    leave it to the user to stop + start the dev server manually."""
    refused = refuse_in_container()
    if refused is not None:
        return refused

    upload = request.files.get("archive")
    if upload is None or not upload.filename:
        flash("Pick a Tesserae export zip to import.", "error")
        return system_redirect()

    raw = upload.read()
    if not raw:
        flash("Uploaded file was empty.", "error")
        return system_redirect()

    # Validate it's a real Tesserae export before we let it touch
    # data/. Also guard against zip-slip, backup.restore writes each
    # member to ``td_path / member`` without sanitisation, so a member
    # like ``../../etc/x`` would land outside the temp stage.
    import zipfile as _zipfile
    from pathlib import PurePosixPath

    try:
        with _zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
    except _zipfile.BadZipFile:
        flash("That file isn't a valid zip.", "error")
        return system_redirect()
    if _backup_mod.META_NAME not in names:
        flash(
            f"That zip doesn't look like a Tesserae export (missing {_backup_mod.META_NAME}).",
            "error",
        )
        return system_redirect()
    for n in names:
        parts = PurePosixPath(n).parts
        if not parts or any(p in ("..", "") for p in parts) or parts[0].startswith("/"):
            flash(f"Refusing import: zip member path looks unsafe ({n!r}).", "error")
            return system_redirect()

    # Stage the upload into the backups dir under a synthetic id so the
    # existing restore() pipeline can pick it up, keeps the restore
    # path consistent with the in-place restore flow.
    push_mgr = push_manager()
    if not push_mgr._lock.acquire(blocking=True, timeout=10):
        flash("Another push is in flight, try again in a moment.", "error")
        return system_redirect()
    import time as _time

    ts = _time.strftime("%Y%m%d-%H%M%S")
    backups_dir = data_root() / _backup_mod.BACKUPS_SUBDIR
    backups_dir.mkdir(parents=True, exist_ok=True)
    staged = backups_dir / f"{ts}-import.zip"
    staged.write_bytes(raw)
    try:
        try:
            _backup_mod.restore(data_root(), staged.stem)
        except (FileNotFoundError, ValueError, OSError) as err:
            flash(f"Import failed: {err}", "error")
            staged.unlink(missing_ok=True)
            return system_redirect()
    finally:
        push_mgr._lock.release()
    # restore() leaves the staged file in place (it's preserved as part
    # of the backups dir during the rebuild). Drop it now so it doesn't
    # show up in the Backups list as a confusing one-time entry.
    staged.unlink(missing_ok=True)
    if current_app.debug:
        flash(
            "Data imported. Stop and restart the --dev server (Ctrl-C, then "
            "``.venv/bin/python -m app.main --dev``) to load the new state.",
            "ok",
        )
    else:
        flash("Data imported. Restarting…", "ok")
        updater().restart(delay_s=1.5)
    return system_redirect()


# -- webhook ------------------------------------------------------------


@bp.post("/settings/system/webhook/regenerate")
def system_webhook_regenerate() -> Response:
    """Mint a fresh random webhook token and persist it. Stashed in the
    session as ``_webhook_token_reveal`` so the Settings GET that follows
    the redirect can pop it into a one-shot modal with a copy button.
    After that render it's gone, the disk value is masked like any
    other ``_secret`` field."""
    from app.webhook_routes import generate_token

    token = generate_token()
    settings_store().update_section("app", {"webhook_token_secret": token})
    session["_webhook_token_reveal"] = token
    return system_redirect()


@bp.post("/settings/system/webhook/set")
def system_webhook_set() -> Response:
    """Set or clear the webhook bearer token by hand. ``clear=1`` wipes
    the on-disk value (webhooks return 503 until re-set). Otherwise the
    pasted ``webhook_token`` value replaces whatever was there. Used
    when an automation tool already has a specific secret and the user
    wants Tesserae to match it instead of issuing a fresh one."""
    if request.form.get("clear"):
        settings_store().update_section("app", {"webhook_token_secret": ""})
        flash("Webhook token cleared. POST /api/v1/push will return 503.", "ok")
        return system_redirect()
    token = (request.form.get("webhook_token") or "").strip()
    if not token:
        flash("Paste a token first, or use Clear to disable webhooks.", "error")
        return system_redirect()
    settings_store().update_section("app", {"webhook_token_secret": token})
    flash("Webhook token saved.", "ok")
    return system_redirect()


# -- telemetry ----------------------------------------------------------


@bp.post("/settings/system/telemetry/test")
def system_telemetry_test() -> Response:
    """Fire a synchronous app.started and surface the outcome in a flash
    + the Events tab. Dev-only, the card is hidden in production
    builds, so this route is gated to ``current_app.debug`` to avoid
    leaving an undocumented endpoint exposed."""
    if not current_app.debug:
        return system_redirect()
    telemetry = current_app.config.get("TELEMETRY")
    if telemetry is None or not telemetry.enabled:
        flash(
            "Telemetry is off. Tick the toggle in Settings → Server → App first.",
            "error",
        )
        return system_redirect()
    err = telemetry.test_send()
    if err:
        flash(f"Test event failed: {err}. Check the endpoint config.", "error")
    else:
        flash("Test event delivered. Check the Events tab for the row.", "ok")
    return system_redirect()
