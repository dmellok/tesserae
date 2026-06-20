#!/usr/bin/env bash
# Tesserae systemd installer.
#
# Run AFTER install.sh has finished. Writes /etc/systemd/system/<name>.service,
# enables it (so it auto-starts on reboot), and starts it now.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/dmellok/tesserae/main/scripts/install-systemd.sh | bash
#
# Or from a local checkout:
#   ./scripts/install-systemd.sh
#
# Optional env vars (pre-answer the prompts; useful in CI / scripts):
#   TESSERAE_DIR=/path/to/install       (default: ~/tesserae)
#   TESSERAE_PORT=8765                  (default: detected from run.sh, else 8765)
#   TESSERAE_SERVICE_NAME=tesserae      (default: tesserae; rename for parallel installs)
#   TESSERAE_USER=$USER                 (default: invoking user)
#   NONINTERACTIVE=1                    skip all prompts, use defaults / env
#
# What it does:
#   1. Refuses on non-Linux / non-systemd platforms (macOS uses launchd, not systemd).
#   2. Verifies the Tesserae install dir + venv exist.
#   3. Resolves port (from env, then by scraping run.sh, else 8765).
#   4. Generates a unit file from the resolved values.
#   5. ``sudo`` writes it to /etc/systemd/system/<name>.service, daemon-reloads,
#      enables it (auto-start on reboot), and starts it now.
#   6. Prints status + the journalctl/systemctl commands you'll need later.
#
# What it does NOT do:
#   - Create a dedicated ``tesserae`` system user. The service runs as whoever
#     installed it (matches install.sh's "install to your home directory" model).
#     If you want a dedicated user, create one first, install Tesserae into its
#     home, then run this script with TESSERAE_USER=<that-user>.
#   - Sandbox the service (no ProtectHome / ReadWritePaths). The install dir is
#     usually in the user's home which makes the obvious lock-down directives
#     fight each other. Add them by hand if you're hosting on a multi-tenant box.
#   - Touch firewall rules. The server binds 0.0.0.0:<port> by default; open
#     the port in your firewall yourself if needed.

set -euo pipefail

# ---------- pretty output ----------
if [[ -t 1 ]]; then
  C_BOLD="$(printf '\033[1m')"
  C_DIM="$(printf '\033[2m')"
  C_GREEN="$(printf '\033[32m')"
  C_YELLOW="$(printf '\033[33m')"
  C_RED="$(printf '\033[31m')"
  C_OFF="$(printf '\033[0m')"
else
  C_BOLD=""; C_DIM=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_OFF=""
fi
info()  { printf '%s•%s %s\n'  "$C_DIM" "$C_OFF" "$*"; }
ok()    { printf '%s✓%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn()  { printf '%s!%s %s\n' "$C_YELLOW" "$C_OFF" "$*"; }
fail()  { printf '%s✗%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }
step()  { printf '\n%s== %s ==%s\n' "$C_BOLD" "$*" "$C_OFF"; }

# Pick a tty for prompts. When piped from curl (`curl ... | bash`)
# stdin is the script body, not the terminal; /dev/tty still works.
TTY=""
if [[ -t 0 ]]; then
  TTY=/dev/stdin
elif [[ -r /dev/tty ]]; then
  TTY=/dev/tty
fi

prompt() {
  # $1=question  $2=default  -> echoes the chosen value
  local question="$1" default="$2" answer=""
  if [[ "${NONINTERACTIVE:-0}" == "1" || -z "$TTY" ]]; then
    printf '%s\n' "$default"
    return
  fi
  read -r -p "${question} [${default}]: " answer < "$TTY" || answer=""
  printf '%s\n' "${answer:-$default}"
}

confirm() {
  # $1=question  $2=default(y|n)  -> exit code 0 on yes, 1 on no
  local question="$1" default="$2" answer=""
  if [[ "${NONINTERACTIVE:-0}" == "1" || -z "$TTY" ]]; then
    [[ "$default" == "y" ]]
    return
  fi
  read -r -p "${question} [${default}]: " answer < "$TTY" || answer="$default"
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy] ]]
}

# ---------- sanity ----------
step "Sanity checks"

OS="$(uname -s)"
if [[ "$OS" != "Linux" ]]; then
  fail "systemd is Linux-only. On macOS use launchd, on Windows use a Service Wrapper or run from a terminal."
fi

if ! command -v systemctl >/dev/null 2>&1; then
  fail "systemctl not found. This script needs a systemd-based distro (Debian / Ubuntu / Raspberry Pi OS / Fedora / Arch)."
fi

# Is systemd actually init? Some containers (LXC, WSL1) ship systemctl
# but PID 1 is something else; in that case enabling a unit silently
# does nothing useful. Spot-check.
if [[ ! -d /run/systemd/system ]]; then
  warn "systemd doesn't appear to be the init system here (no /run/systemd/system)."
  warn "WSL1 + some container shells fall into this bucket. The unit will be"
  warn "written but ``systemctl enable`` won't survive a reboot the way you'd expect."
  if ! confirm "Continue anyway?" "n"; then
    fail "Aborted."
  fi
fi

ok "Linux + systemd"

# ---------- config ----------
INSTALL_DIR="${TESSERAE_DIR:-$HOME/tesserae}"
SERVICE_NAME="${TESSERAE_SERVICE_NAME:-tesserae}"
RUN_AS_USER="${TESSERAE_USER:-${SUDO_USER:-$USER}}"
PORT="${TESSERAE_PORT:-}"

# Resolve install dir
if [[ ! -d "$INSTALL_DIR" ]]; then
  fail "Install dir not found: $INSTALL_DIR. Run install.sh first, or set TESSERAE_DIR."
fi
INSTALL_DIR="$(cd "$INSTALL_DIR" && pwd)"  # canonicalise

# Sanity-check the install
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  fail "Venv not found at $INSTALL_DIR/.venv/bin/python. Run install.sh first."
fi
if [[ ! -f "$INSTALL_DIR/app/main.py" ]]; then
  fail "$INSTALL_DIR/app/main.py missing. Doesn't look like a Tesserae checkout."
fi
ok "Install dir: $INSTALL_DIR"

# Resolve port: env > run.sh scrape > prompt > default
if [[ -z "$PORT" && -f "$INSTALL_DIR/run.sh" ]]; then
  # run.sh writes ``--port 8765``; grep it.
  PORT="$(grep -Eo '\-\-port[ =][0-9]+' "$INSTALL_DIR/run.sh" | head -1 | tr -dc 0-9 || true)"
fi
if [[ -z "$PORT" ]]; then
  PORT="$(prompt "Listen on which port?" "8765")"
fi
ok "Port: $PORT"

# Resolve user
if ! id -u "$RUN_AS_USER" >/dev/null 2>&1; then
  fail "User $RUN_AS_USER doesn't exist."
fi
ok "Run as: $RUN_AS_USER"

# Resolve service name
if [[ ! "$SERVICE_NAME" =~ ^[a-z][a-z0-9-]*$ ]]; then
  fail "Service name must match [a-z][a-z0-9-]*, got: $SERVICE_NAME"
fi
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
ok "Service: $SERVICE_NAME ($UNIT_PATH)"

# ---------- existing unit handling ----------
if [[ -f "$UNIT_PATH" ]]; then
  warn "Service file already exists: $UNIT_PATH"
  if ! confirm "Overwrite and restart the service?" "y"; then
    fail "Aborted."
  fi
fi

# ---------- generate ----------
step "Generating unit file"

TMP_UNIT="$(mktemp)"
trap 'rm -f "$TMP_UNIT"' EXIT

cat > "$TMP_UNIT" <<EOF
[Unit]
Description=Tesserae self-hosted e-ink dashboard server
Documentation=https://github.com/dmellok/tesserae
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_AS_USER
Group=$(id -gn "$RUN_AS_USER")
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python -m app.main --port $PORT
Restart=on-failure
RestartSec=5s
# PYTHONUNBUFFERED keeps logging.warning / print() flowing into journald
# in real time instead of buffering until the process exits.
Environment=PYTHONUNBUFFERED=1
# StandardOutput / StandardError default to journal on modern systemd
# (>= 240); set explicitly for older distros (Raspberry Pi OS Bullseye
# was 247, Buster was 241).
StandardOutput=journal
StandardError=journal
# Generous timeout: a cold render of a heavy compose page on a Pi can
# take a few seconds at startup, and Playwright's bundled Chromium
# wakes up slowly the first time.
TimeoutStartSec=30s
TimeoutStopSec=20s

[Install]
WantedBy=multi-user.target
EOF
ok "Wrote unit to $TMP_UNIT"

info "Preview:"
sed 's/^/    /' "$TMP_UNIT"

# ---------- install ----------
step "Installing"

# Stop the existing service if running, so the new ExecStart takes
# effect cleanly. Ignore failures (it might not be running yet).
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
  info "Stopping existing service first…"
  sudo systemctl stop "$SERVICE_NAME" || true
fi

sudo install -m 0644 "$TMP_UNIT" "$UNIT_PATH"
ok "Installed $UNIT_PATH"

sudo systemctl daemon-reload
ok "systemctl daemon-reload"

sudo systemctl enable "$SERVICE_NAME"
ok "Enabled (auto-start on reboot)"

sudo systemctl start "$SERVICE_NAME"
ok "Started"

# Give it a moment to fail fast if the venv / port / args are wrong.
sleep 2

if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
  ok "Service is running."
else
  warn "Service didn't stay up. Recent logs:"
  sudo journalctl -u "$SERVICE_NAME" --no-pager -n 30 || true
  fail "Investigate above, fix, then ``sudo systemctl restart $SERVICE_NAME``."
fi

# ---------- done ----------
step "Done"

cat <<EOF
${C_BOLD}Tesserae is now a systemd service.${C_OFF}

  Auto-start on reboot:  yes (enabled)
  Listening on:          http://0.0.0.0:$PORT/
  Running as:            $RUN_AS_USER
  Working dir:           $INSTALL_DIR

Common commands:

  sudo systemctl status $SERVICE_NAME       # is it running?
  sudo systemctl restart $SERVICE_NAME      # bounce after an upgrade
  sudo systemctl stop $SERVICE_NAME         # stop until next boot
  sudo systemctl disable $SERVICE_NAME      # stop AND don't auto-start
  sudo journalctl -u $SERVICE_NAME -f       # tail the logs
  sudo journalctl -u $SERVICE_NAME -n 100   # last 100 lines

To uninstall the service (leaves the install dir alone):

  sudo systemctl disable --now $SERVICE_NAME
  sudo rm $UNIT_PATH
  sudo systemctl daemon-reload

Visit ${C_BOLD}http://localhost:$PORT/${C_OFF} to confirm it's up.
EOF
