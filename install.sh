#!/usr/bin/env bash
# Tesserae installer, macOS / Linux / Raspberry Pi.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/dmellok/tesserae/main/install.sh | bash
#
# Or pin to a tag once we cut releases:
#   curl -fsSL https://raw.githubusercontent.com/dmellok/tesserae/v0.1.0/install.sh | bash
#
# Optional env vars (pre-answer the prompts; useful in CI / scripts):
#   TESSERAE_DIR=/path/to/install   (default: ~/tesserae)
#   TESSERAE_PORT=8765               (default: 8765, prompted interactively)
#   PYTHON=python3.12                (default: python3)
#   SKIP_PLAYWRIGHT=1                skip the Chromium download step
#   NONINTERACTIVE=1                 skip all prompts, use defaults / env
#
# What it does:
#   1. Sanity-checks git + Python 3.11+
#   2. Clones (or `git pull`s) the repo to TESSERAE_DIR
#   3. Creates a venv and pip-installs the project
#   4. Tries Playwright's bundled Chromium; on platforms where there's
#      no prebuilt (e.g. 32-bit Pi OS), falls back to the system
#      `chromium-browser` and writes its path to data/core/.chromium
#      (the renderer reads that sidecar at launch).
#   5. Prints the run command.
#
# What it does NOT do:
#   - Set up systemd / launchd auto-start (too many opinions). The
#     README has copy-paste recipes for this.
#   - Touch any system packages. If python3 / git aren't installed
#     it'll tell you and bail.

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

# ---------- config ----------
INSTALL_DIR="${TESSERAE_DIR:-$HOME/tesserae}"
REPO_URL="${TESSERAE_REPO:-https://github.com/dmellok/tesserae.git}"
BRANCH="${TESSERAE_BRANCH:-main}"
PYTHON="${PYTHON:-python3}"
PORT="${TESSERAE_PORT:-}"

# Pick a tty for prompts. When piped from curl (`curl ... | bash`)
# stdin is the script body, not the terminal, /dev/tty still works.
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

# ---------- sanity ----------
step "Sanity checks"

command -v git >/dev/null 2>&1 || fail "git not found. Install it first."
command -v "$PYTHON" >/dev/null 2>&1 || fail "$PYTHON not found. Install Python 3.11+ first."

PY_VERSION="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"
if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ) ]]; then
  fail "Python 3.11+ required (found $PY_VERSION). Set PYTHON=python3.12 if you have a newer build."
fi
ok "git, $PYTHON ($PY_VERSION)"

OS="$(uname -s)"
ARCH="$(uname -m)"
info "Platform: $OS / $ARCH"

# ---------- interactive prompts ----------
if [[ -z "$PORT" ]]; then
  PORT="$(prompt "Listen on which port?" "8765")"
fi
# Defensive: strip non-digits, fall back to 8765 if user typed garbage.
PORT="${PORT//[^0-9]/}"
if [[ -z "$PORT" || "$PORT" -lt 1 || "$PORT" -gt 65535 ]]; then
  warn "Invalid port '${PORT}', using 8765"
  PORT=8765
fi
info "Port: $PORT"

# ---------- clone / update ----------
step "Source"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Existing checkout at $INSTALL_DIR, pulling $BRANCH"
  git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout --quiet "$BRANCH"
  git -C "$INSTALL_DIR" pull --quiet --ff-only origin "$BRANCH"
  ok "Updated to $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
elif [[ -e "$INSTALL_DIR" ]]; then
  fail "$INSTALL_DIR exists but isn't a git checkout. Move or delete it, then re-run."
else
  info "Cloning $REPO_URL -> $INSTALL_DIR"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  ok "Cloned to $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
fi
cd "$INSTALL_DIR"

# ---------- venv + deps ----------
step "Python environment"
if [[ ! -d .venv ]]; then
  info "Creating .venv"
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
info "Upgrading pip"
pip install --quiet --upgrade pip
info "Installing project (editable)"
pip install --quiet -e ".[dev]"
ok "Dependencies installed"

# ---------- Chromium for the webpage renderer ----------
step "Chromium (webpage rendering)"
if [[ "${SKIP_PLAYWRIGHT:-0}" == "1" ]]; then
  warn "SKIP_PLAYWRIGHT=1 set, skipping Chromium setup."
  warn "The Send → Webpage tab and any webpage widgets won't render."
else
  if playwright install chromium 2>/dev/null; then
    ok "Playwright bundled Chromium installed"
  else
    warn "Playwright doesn't ship Chromium for $OS / $ARCH (common on 32-bit Pi OS)."
    info "Looking for a system Chromium..."
    SYS_CHROMIUM=""
    for cand in chromium-browser chromium google-chrome google-chrome-stable brave-browser; do
      if command -v "$cand" >/dev/null 2>&1; then
        SYS_CHROMIUM="$(command -v "$cand")"
        break
      fi
    done
    if [[ -n "$SYS_CHROMIUM" ]]; then
      mkdir -p data/core
      printf '%s\n' "$SYS_CHROMIUM" > data/core/.chromium
      ok "Pointed Playwright at $SYS_CHROMIUM (saved to data/core/.chromium)"
    else
      warn "No system Chromium found either. Install one:"
      warn "    Debian / Pi OS:  sudo apt install chromium-browser"
      warn "    macOS:           brew install --cask chromium"
      warn "  Then re-run this installer, OR set TESSERAE_CHROMIUM_PATH yourself."
      warn "  Until then, webpage rendering won't work, the rest does."
    fi
  fi
fi

# ---------- launcher shortcut ----------
# Write a tiny run.sh that activates the venv and starts the server on
# the chosen port. Users can edit this later (e.g. add --dev).
cat > run.sh <<EOF_RUN
#!/usr/bin/env bash
# Auto-generated by install.sh. Edit freely.
set -euo pipefail
cd "\$(dirname "\$0")"
exec .venv/bin/python -m app.main --port ${PORT} "\$@"
EOF_RUN
chmod +x run.sh

# ---------- done ----------
step "Done"
printf '%sTesserae is installed at %s%s\n' "$C_BOLD" "$INSTALL_DIR" "$C_OFF"
printf '\nStart it:\n'
printf '  cd %s\n' "$INSTALL_DIR"
printf '  ./run.sh                 # production (waitress, port %s)\n' "$PORT"
printf '  ./run.sh --dev           # dev mode (Flask reloader)\n'
printf '\nThen visit %shttp://localhost:%s/%s\n' "$C_BOLD" "$PORT" "$C_OFF"
printf '\nFirst-run: the app will prompt for an admin password at /setup.\n'
printf '\nOther devices on your LAN: replace localhost with this machine'"'"'s\n'
printf 'IP. The server binds 0.0.0.0 by default.\n'

if [[ "$OS" == "Linux" ]] && command -v systemctl >/dev/null 2>&1; then
  printf '\n%sAuto-start on reboot (optional)%s\n' "$C_BOLD" "$C_OFF"
  printf 'Run as a systemd service so it survives reboots + restarts on crash:\n'
  printf '  cd %s && ./scripts/install-systemd.sh\n' "$INSTALL_DIR"
fi

# ---------- comment about NOT setting up systemd ----------
# The earlier comment block at the top of this script said "What it
# does NOT do: Set up systemd / launchd auto-start". The systemd half
# of that is now covered by scripts/install-systemd.sh, which the user
# runs as a deliberate follow-up step. Keep this installer focused on
# the cross-platform install path and let install-systemd.sh own the
# Linux-specific service wiring.
