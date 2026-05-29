# Install Tesserae

Tesserae is the **server**: it serves the admin UI, renders dashboards, and
publishes frames to your MQTT broker. It runs on macOS, Linux, Raspberry Pi,
and Windows. You'll also need an **MQTT broker** (e.g. Mosquitto, or the one
built into Home Assistant) and at least one [client](clients.md) to paint a
panel.

## Quick install

=== "macOS / Linux / Raspberry Pi"

    ```sh
    curl -fsSL https://raw.githubusercontent.com/dmellok/tesserae/main/install.sh | bash
    ```

=== "Windows (PowerShell)"

    ```powershell
    iwr https://raw.githubusercontent.com/dmellok/tesserae/main/install.ps1 -UseBasicParsing | iex
    ```

The installer:

- Sanity-checks `git` + Python 3.11+
- Clones the repo (default `~/tesserae`, override with `TESSERAE_DIR`)
- Creates a venv and installs the project
- Asks for a port (default `8000`)
- Installs Chromium via Playwright for webpage rendering (with a system-browser fallback — see below)
- Writes a `run.sh` (or `run.ps1`) shortcut in the install dir

When it finishes, start the server with `./run.sh` (or `.\run.ps1`) from the
install dir and open `http://localhost:8000/`.

## Manual install

If you'd rather do it by hand (or already cloned the repo):

```sh
git clone https://github.com/dmellok/tesserae.git
cd tesserae
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/python -m app.main         # production: waitress, port 8000
.venv/bin/python -m app.main --dev   # Flask dev server: auto-reload + debugger
```

`python -m app.main` runs under
[waitress](https://docs.pylonsproject.org/projects/waitress/), a pure-Python
production WSGI server — the same command works on a Raspberry Pi appliance, no
nginx required for a single-user install. `--dev` opts into Flask's dev server
when you're hacking on the admin.

!!! windows "Windows line endings"
    If `.\install.ps1` fails to parse on PowerShell 5.1, your checkout may have
    LF line endings. `git pull` to get the `.gitattributes` fix, or run the
    manual steps above with `.venv\Scripts\python.exe -m app.main`.

## First run

1. Open `http://127.0.0.1:8000/` — on first boot you're sent to `/setup` to pick an admin password.
2. Sign in at `/login`, then go to **Settings → Server** and point Tesserae at your **MQTT broker** and set the **base URL** the panel uses to fetch frames.
3. Renderers and plugins that declare settings show up as their own sections, generated from their manifests.

To preview a single widget without composing a dashboard, run `--dev`, sign
in, then open
`http://127.0.0.1:8000/_test/render?plugin=clock_analog&size=md` in your
browser. `/_test/render` needs the dev (or test) server **and** a logged-in
session — it isn't loopback-exempt. The loopback bypass is only for
`/compose/`, `/renders/`, and `/plugins/<id>/<asset>`, which the in-process
Playwright renderer fetches without a session.

## Chromium for webpage rendering

The **Send → Webpage** tab and the `webpage` widget screenshot pages with
headless Chromium via Playwright. Playwright ships its own binaries for most
platforms; on 32-bit Raspberry Pi OS it doesn't, so the installer falls back to
a system browser. To point at one yourself:

```sh
export TESSERAE_CHROMIUM_PATH=/usr/bin/chromium-browser
```

…or write the path to `data/core/.chromium` (single line). If no browser is
found, everything except webpage rendering still works.

## Running the tests

```sh
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy app/
```

The renderer, transport, push pipeline, auth, and settings flow are all covered
with no broker or Chromium dependency.

## Next steps

- [Install a client](clients.md) for your panel hardware
- [Set up a device](devices.md) — register it, calibrate orientation, bind a dashboard
- [Browse the widgets](../widgets/gallery.md) you can place on a dashboard
