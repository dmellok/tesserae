"""Admin auth endpoints: first-run setup, sign-in, sign-out.

* ``GET/POST /setup``, first-run password set, only reachable while no
  password is configured (otherwise it's a silent admin-takeover hole).
  A client outside the local network must also present the setup token.
* ``GET/POST /login``, sign-in form. Redirects to ``/setup`` if no
  password is set yet; honours a ``?next=`` query param bounded by
  :func:`safe_next` so the post-login redirect can't be used as an open
  redirector.
* ``POST /logout``, drop the session and redirect to /login.
"""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import auth, onboarding

from ._shared import bp, log_auth, safe_next, settings_store


@bp.route("/setup", methods=["GET", "POST"])
def setup() -> Response | str:
    settings = settings_store()
    # Setup only works while no password is set, otherwise it's a way to
    # silently take over the admin.
    if auth.password_is_set(settings):
        return redirect(url_for("auth.settings"))
    # Validation failures render inline in the card, not as a flash: the
    # corner toast auto-dismisses after 5s and is easy to miss when the
    # re-rendered page otherwise looks unchanged (#265).
    error = None
    # A browser on the local network sets the password as before. Anyone
    # else must also present the setup token from the server log, so an
    # internet-reachable install can't be claimed by the first stranger to
    # find it. Resolved here, before the form renders, so the token is
    # already in the log by the time the page asks for it.
    needs_token = auth.setup_requires_token()
    submitted_token = ""
    if needs_token:
        auth.setup_token(settings)
    if request.method == "POST":
        pw = request.form.get("password", "")
        pw2 = request.form.get("password_confirm", "")
        submitted_token = request.form.get("setup_token", "")
        if needs_token and not auth.verify_setup_token(settings, submitted_token):
            error = "That setup token is not correct. Copy it from the server log."
            log_auth("setup", "denied", error="incorrect setup token")
        elif len(pw) < 8:
            error = "Password must be at least 8 characters."
        elif pw != pw2:
            error = "Passwords do not match."
        else:
            auth.set_password(settings, pw)
            auth.discard_setup_token(settings)
            auth.login()
            log_auth("setup", "ok")
            # First run drops into the setup wizard, not straight to
            # Settings, the wizard sequences broker → device → dashboard.
            # An install already set up another way (through Home
            # Assistant's ingress, which needs no password) goes home.
            if onboarding.is_onboarded(settings):
                return redirect(url_for("index"))
            return redirect(url_for("onboarding.index"))
    return render_template(
        "setup.html", error=error, needs_token=needs_token, setup_token_value=submitted_token
    )


@bp.route("/login", methods=["GET", "POST"], endpoint="login_view")
def login_view() -> Response | str:
    settings = settings_store()
    if not auth.password_is_set(settings):
        return redirect(url_for("auth.setup"))
    if auth.is_authed():
        return redirect(safe_next(request.args.get("next")))
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if auth.verify_password(settings, pw):
            auth.login()
            log_auth("login", "ok")
            return redirect(safe_next(request.form.get("next")))
        log_auth("login", "denied", error="incorrect password")
        error = "Incorrect password."
    return render_template("login.html", next=request.args.get("next", ""), error=error)


@bp.post("/logout")
def logout_view() -> Response:
    auth.logout()
    log_auth("logout", "ok")
    return redirect(url_for("auth.login_view"))


# -- Settings → System → Authentication --------------------------------
# Three endpoints for managing the admin password while logged in.
# Disable requires ``confirmed=1`` so a stray POST can't silently drop
# auth, the Settings switch sets it after a JS confirm() dialog. The
# matching CLI escape hatch (``tesserae --reset-password``) lives in
# ``app.main`` for when the user has lost the password entirely.


@bp.post("/settings/system/auth/change-password")
def change_password() -> Response:
    settings = settings_store()
    if not auth.password_is_set(settings):
        flash("No password is set, visit /setup first.", "error")
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


@bp.post("/settings/system/auth/forget")
def forget_password_view() -> Response:
    """Forget the saved password while the gate is off (#373). Only
    offered then: with the gate off anyone on the LAN already has the
    admin UI, so dropping the hash opens nothing new, and it spares a
    Home Assistant App user (who reaches the UI through ingress and may
    never have known the password) a trip to ``tesserae --reset-password``."""
    settings = settings_store()
    if auth.password_required(settings):
        flash("Disable the password first, then forget it.", "error")
        return redirect(url_for("auth.settings_area", area="system"))
    if request.form.get("confirmed") != "1":
        flash("Forgetting the password requires confirmation.", "error")
        return redirect(url_for("auth.settings_area", area="system"))
    auth.forget_password(settings)
    log_auth("forget_password", "ok")
    flash("Saved password forgotten. Enabling the password will ask you to pick a new one.", "ok")
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


@bp.post("/settings/system/auth/trusted-networks")
def trusted_networks_view() -> Response:
    """Save the operator's extra trusted networks (one CIDR per line)."""
    settings = settings_store()
    raw = request.form.get("trusted_networks", "")
    try:
        nets = auth.set_trusted_networks(settings, raw)
    except ValueError as exc:
        flash(f"Trusted networks not saved: {exc}", "error")
        return redirect(url_for("auth.settings_area", area="system"))
    log_auth("trusted_networks", "ok")
    if nets:
        flash(f"Trusted networks saved: {', '.join(nets)}.", "ok")
    else:
        flash("Trusted networks cleared; only the standard private ranges count as local.", "ok")
    return redirect(url_for("auth.settings_area", area="system"))
