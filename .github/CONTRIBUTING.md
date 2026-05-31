# Contributing

Tesserae is a self-hosted hobby project. Contributions are welcome; the
rules are short.

## Set up a dev environment

Quickest path:

```sh
git clone https://github.com/dmellok/tesserae.git
cd tesserae
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Or run the platform installer (sanity-checks Python, sets up the venv,
installs Chromium via Playwright):

```sh
curl -fsSL https://raw.githubusercontent.com/dmellok/tesserae/main/install.sh | bash
```

Run the server in dev mode (Flask reloader + debugger):

```sh
.venv/bin/python -m app.main --dev
```

## Run the checks

Before opening a PR, run all three locally — CI runs the same set:

```sh
.venv/bin/python -m pytest -q       # ~2 minutes
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m mypy app        # strict mode applies on contract modules
```

A failing pre-commit or CI run is on you to fix; don't `--no-verify`.

## Add a widget / renderer / device

The drop-a-folder plugin model is documented in
[docs/dev/writing-a-plugin.md](docs/dev/writing-a-plugin.md). That's
the canonical guide — manifest schema, lifecycle hooks, and how to
test in the dev gallery. Read it once before writing your first
plugin; it'll save you guessing at field names.

## Commit and PR conventions

- One concern per PR. A bugfix that also reformats unrelated files is
  two PRs.
- Conventional commit prefixes: `feat:`, `fix:`, `docs:`, `chore:`,
  `refactor:`, `test:`. PR titles use the same.
- Commit message bodies explain *why*, not *what* — the diff already
  shows what changed.
- Bump `pyproject.toml` and tag with a matching `vX.Y.Z` for any
  user-facing feature or fix.

## Where to ask questions

- **[GitHub Discussions](https://github.com/dmellok/tesserae/discussions)**
  — the right place for "how do I…", "is X possible", or "should this
  be a feature". Use bug reports for actual bugs.
- **Issues** — bugs and feature requests with a clear shape. Use the
  issue templates.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
TL;DR: be kind, assume good faith, don't be a jerk.
