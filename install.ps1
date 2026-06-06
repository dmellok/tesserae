# Tesserae installer, Windows / PowerShell.
#
# Usage (in PowerShell):
#   iwr https://raw.githubusercontent.com/dmellok/tesserae/main/install.ps1 -UseBasicParsing | iex
#
# Or with a custom port:
#   $env:TESSERAE_PORT = '8765'; iwr ... | iex
#
# Optional env vars (pre-answer the prompts):
#   $env:TESSERAE_DIR   default: $HOME\tesserae
#   $env:TESSERAE_PORT  default: 8765 (otherwise prompted)
#   $env:PYTHON         default: python
#   $env:SKIP_PLAYWRIGHT = '1'  to skip Chromium
#   $env:NONINTERACTIVE  = '1'  skip prompts, use defaults

$ErrorActionPreference = 'Stop'

function Step($msg)  { Write-Host ""; Write-Host "== $msg ==" -ForegroundColor White }
function Info($msg)  { Write-Host "  $msg" -ForegroundColor DarkGray }
function Ok($msg)    { Write-Host "[OK] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "[!] $msg"  -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "[X] $msg"  -ForegroundColor Red; exit 1 }

$InstallDir = if ($env:TESSERAE_DIR) { $env:TESSERAE_DIR } else { Join-Path $HOME 'tesserae' }
$RepoUrl    = if ($env:TESSERAE_REPO) { $env:TESSERAE_REPO } else { 'https://github.com/dmellok/tesserae.git' }
$Branch     = if ($env:TESSERAE_BRANCH) { $env:TESSERAE_BRANCH } else { 'main' }
$Python     = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
$Port       = $env:TESSERAE_PORT
$NonInter   = $env:NONINTERACTIVE -eq '1'

function Prompt-Default([string]$question, [string]$default) {
  if ($NonInter) { return $default }
  $answer = Read-Host "$question [$default]"
  if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
  return $answer
}

# ---------- sanity ----------
Step "Sanity checks"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Fail "git not found. Install Git for Windows first: https://git-scm.com/"
}
if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
  Fail "$Python not found. Install Python 3.11+ from https://www.python.org/ (tick 'Add to PATH')."
}
$pyVer = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$major, $minor = $pyVer.Split('.') | ForEach-Object { [int]$_ }
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
  Fail "Python 3.11+ required (found $pyVer)."
}
Ok "git + $Python ($pyVer)"

# ---------- port ----------
if (-not $Port) {
  $Port = Prompt-Default "Listen on which port?" "8765"
}
$Port = ($Port -replace '[^0-9]', '')
if (-not $Port -or [int]$Port -lt 1 -or [int]$Port -gt 65535) {
  Warn "Invalid port, using 8765"
  $Port = '8765'
}
Info "Port: $Port"

# ---------- clone / update ----------
Step "Source"
if (Test-Path (Join-Path $InstallDir '.git')) {
  Info "Existing checkout at $InstallDir, pulling $Branch"
  git -C $InstallDir fetch --quiet origin $Branch
  git -C $InstallDir checkout --quiet $Branch
  git -C $InstallDir pull --quiet --ff-only origin $Branch
  $rev = (git -C $InstallDir rev-parse --short HEAD).Trim()
  Ok "Updated to $rev"
} elseif (Test-Path $InstallDir) {
  Fail "$InstallDir exists but isn't a git checkout. Move or delete it, then re-run."
} else {
  Info "Cloning $RepoUrl -> $InstallDir"
  git clone --quiet --branch $Branch $RepoUrl $InstallDir
  $rev = (git -C $InstallDir rev-parse --short HEAD).Trim()
  Ok "Cloned to $rev"
}
Set-Location $InstallDir

# ---------- venv + deps ----------
Step "Python environment"
$VenvDir = Join-Path $InstallDir '.venv'
if (-not (Test-Path $VenvDir)) {
  Info "Creating .venv"
  & $Python -m venv .venv
}
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
Info "Upgrading pip"
& $VenvPy -m pip install --quiet --upgrade pip
Info "Installing project (editable)"
& $VenvPy -m pip install --quiet -e ".[dev]"
Ok "Dependencies installed"

# ---------- Chromium ----------
Step "Chromium (webpage rendering)"
if ($env:SKIP_PLAYWRIGHT -eq '1') {
  Warn "SKIP_PLAYWRIGHT=1 set, skipping Chromium setup."
  Warn "The Send -> Webpage tab and any webpage widgets won't render."
} else {
  $VenvPlaywright = Join-Path $VenvDir 'Scripts\playwright.exe'
  try {
    & $VenvPlaywright install chromium | Out-Null
    Ok "Playwright bundled Chromium installed"
  } catch {
    Warn "Playwright install chromium failed. Looking for a system browser..."
    $candidates = @(
      "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
      "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe",
      "$env:LocalAppData\Google\Chrome\Application\chrome.exe",
      "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) {
      $dataCore = Join-Path $InstallDir 'data\core'
      New-Item -ItemType Directory -Path $dataCore -Force | Out-Null
      Set-Content -Path (Join-Path $dataCore '.chromium') -Value $found -NoNewline
      Ok "Pointed Playwright at $found (saved to data\core\.chromium)"
    } else {
      Warn "No Chromium / Chrome / Edge found. Install Google Chrome and re-run,"
      Warn "or set TESSERAE_CHROMIUM_PATH yourself. Until then, webpage"
      Warn "rendering won't work, the rest does."
    }
  }
}

# ---------- launcher shortcut ----------
$runPs1 = Join-Path $InstallDir 'run.ps1'
@"
# Auto-generated by install.ps1. Edit freely.
`$ErrorActionPreference = 'Stop'
Set-Location `$PSScriptRoot
& "`$PSScriptRoot\.venv\Scripts\python.exe" -m app.main --port $Port @args
"@ | Set-Content -Path $runPs1 -Encoding UTF8

# ---------- done ----------
Step "Done"
Write-Host "Tesserae is installed at $InstallDir" -ForegroundColor White
Write-Host ""
Write-Host "Start it:"
Write-Host "  cd $InstallDir"
Write-Host "  .\run.ps1                 # production (waitress, port $Port)"
Write-Host "  .\run.ps1 --dev           # dev mode (Flask reloader)"
Write-Host ""
Write-Host "Then visit http://localhost:$Port/" -ForegroundColor White
Write-Host ""
Write-Host "First-run: the app will prompt for an admin password at /setup."
