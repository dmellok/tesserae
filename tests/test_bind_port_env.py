"""``TESSERAE_BIND_PORT`` drives the ``--port`` default (app/main.py).

Docker operators can't reach the CLI flag without overriding the image
CMD, so the bind port is settable from a compose ``environment:`` block.
The env var was already trusted by the renderer's loopback rewrite as
"the port the server actually listens on"; these tests pin the CLI side
of that contract.
"""

from app.main import _default_bind_port


def test_default_is_8765_without_env(monkeypatch):
    monkeypatch.delenv("TESSERAE_BIND_PORT", raising=False)
    assert _default_bind_port() == 8765


def test_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("TESSERAE_BIND_PORT", "8766")
    assert _default_bind_port() == 8766


def test_non_numeric_env_falls_back(monkeypatch):
    monkeypatch.setenv("TESSERAE_BIND_PORT", "eighty")
    assert _default_bind_port() == 8765


def test_blank_env_falls_back(monkeypatch):
    monkeypatch.setenv("TESSERAE_BIND_PORT", "  ")
    assert _default_bind_port() == 8765
