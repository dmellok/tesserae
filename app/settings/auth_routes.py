"""Admin auth endpoints: first-run setup, sign-in, sign-out.

* ``GET/POST /setup`` — first-run password set, only reachable while no
  password is configured (otherwise it's a silent admin-takeover hole).
* ``GET/POST /login`` — sign-in form. Redirects to ``/setup`` if no
  password is set yet; honours a ``?next=`` query param bounded by
  :func:`safe_next` so the post-login redirect can't be used as an open
  redirector.
* ``POST /logout`` — drop the session and redirect to /login.
"""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import auth

from ._shared import bp, log_auth, safe_next, settings_store


@bp.route("/setup", methods=["GET", "POST"])
def setup() -> Response | str:
    settings = settings_store()
    # Setup only works while no password is set — otherwise it's a way to
    # silently take over the admin.
    if auth.password_is_set(settings):
        return redirect(url_for("auth.settings"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        pw2 = request.form.get("password_confirm", "")
        if len(pw) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif pw != pw2:
            flash("Passwords do not match.", "error")
        else:
            auth.set_password(settings, pw)
            auth.login()
            log_auth("setup", "ok")
            # First run drops into the setup wizard, not straight to
            # Settings — the wizard sequences broker → device → dashboard.
            return redirect(url_for("onboarding.index"))
    return render_template("setup.html")


@bp.route("/login", methods=["GET", "POST"], endpoint="login_view")
def login_view() -> Response | str:
    settings = settings_store()
    if not auth.password_is_set(settings):
        return redirect(url_for("auth.setup"))
    if auth.is_authed():
        return redirect(safe_next(request.args.get("next")))
    if request.method == "POST":
        pw = request.form.get("password", "")
        if auth.verify_password(settings, pw):
            auth.login()
            log_auth("login", "ok")
            return redirect(safe_next(request.form.get("next")))
        log_auth("login", "denied", error="incorrect password")
        flash("Incorrect password.", "error")
    return render_template("login.html", next=request.args.get("next", ""))


@bp.post("/logout")
def logout_view() -> Response:
    auth.logout()
    log_auth("logout", "ok")
    return redirect(url_for("auth.login_view"))


# -- Settings → System → Authentication --------------------------------
# Three endpoints for managing the admin password while logged in.
# Disable requires ``confirmed=1`` so a stray POST can't silently drop
# auth — the Settings switch sets it after a JS confirm() dialog. The
# matching CLI escape hatch (``tesserae --reset-password``) lives in
# ``app.main`` for when the user has lost the password entirely.


@bp.post("/settings/system/auth/change-password")
def change_password() -> Response:
    settings = settings_store()
    if not auth.password_is_set(settings):
        flash("No password is set — visit /setup first.", "error")
        return redirect(url_for("auth.settings_area", area="system"))
    current = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm = request.form.get("new_password_confirm", "")
    if not auth.verify_password(settings, current):
        log_auth("change_password", "denied", error="incorrect current password")
        flash("Current password didn't match.", "error")
        return redirect(url_for("auth.settings_area", area="system"))
    if len(new_pw) < 8:
        flash("New password must be at least 8 characters.", "error")
        return redirect(url_for("auth.settings_area", area="system"))
    if new_pw != confirm:
        flash("New passwords don't match.", "error")
        return redirect(url_for("auth.settings_area", area="system"))
    auth.set_password(settings, new_pw)
    log_auth("change_password", "ok")
    flash("Password updated.", "ok")
    return redirect(url_for("auth.settings_area", area="system"))


@bp.post("/settings/system/auth/disable")
def disable_password_view() -> Response:
    settings = settings_store()
    if request.form.get("confirmed") != "1":
        flash("Disable requires confirmation.", "error")
        return redirect(url_for("auth.settings_area", area="system"))
    auth.set_password_disabled(settings, True)
    log_auth("disable_password", "ok")
    flash(
        "Password disabled. Anyone on the LAN can reach the admin UI; "
        "public IPs are still blocked. Re-enable here when you're done.",
        "ok",
    )
    return redirect(url_for("auth.settings_area", area="system"))


@bp.post("/settings/system/auth/enable")
def enable_password_view() -> Response:
    settings = settings_store()
    auth.set_password_disabled(settings, False)
    log_auth("enable_password", "ok")
    if not auth.password_is_set(settings):
        flash("Password protection re-enabled. Pick a password.", "ok")
        return redirect(url_for("auth.setup"))
    flash("Password protection re-enabled.", "ok")
    return redirect(url_for("auth.settings_area", area="system"))
