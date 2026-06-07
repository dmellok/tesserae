"""Widget capability manifest + per-plugin runtime enforcement.

A widget's ``plugin.json`` can declare a ``requires:`` block listing
the capabilities it needs (network egress targets, settings keys,
filesystem writes). The plugin loader parses this into a
:class:`Capabilities` snapshot and the host enforces it at runtime
when the plugin's ``server.py`` is called.

The threat model is the marketplace's audit-only trust gate: every
widget runs in-process with full Python privileges, so a determined
attacker can always reach around any wrapper we install (eval,
frame inspection, ctypes). What capability declaration buys is
**reviewability**, the manifest tells a human reviewer exactly what
the widget claims it needs, and the runtime enforces that claim so
a drift between manifest and behaviour fails loud.

What we enforce in v1 (this module):

* **Network egress.** A monkey-patched ``socket.create_connection``
  (the bottom of every urllib / requests / aiohttp call) refuses
  hostnames that aren't in the active widget's allowlist. A widget
  trying to phone home gets a ``CapabilityDenied`` instead of a
  network round-trip.

What we DON'T enforce in v1 (review-only):

* **Settings reads.** A widget reaching ``current_app.config[
  "SETTINGS_STORE"]`` directly can still read sibling plugin
  secrets. Wrapping ``current_app.config`` per-plugin is fiddly
  (Flask globals + thread-locals) and we can punt; the manifest
  forces the reviewer to notice when a widget claims
  ``settings:plugin`` and grep for any unexpected reaches.
* **Filesystem writes outside data_dir.** Python ``open()`` is
  reached via too many paths to interpose cleanly; document the
  reviewer rule and revisit under #3 (process isolation).

The active capability context is held on a :class:`contextvars.
ContextVar` so concurrent renders (one widget's ``fetch`` running
alongside another's) don't cross-contaminate. The context is set
by ``capability_scope()`` around every call into a widget's
``server.py``, see ``app.composer._fetch_plugin_data``.

mypy --strict applies, see pyproject.toml.
"""

from __future__ import annotations

import contextvars
import logging
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Recognised capability category prefixes. The schema regex enforces
# the shape; this constant keeps the parser honest if the schema
# grows a new category.
_CATEGORIES: frozenset[str] = frozenset({"network", "settings", "filesystem"})


class CapabilityDenied(RuntimeError):
    """Raised when a widget attempts an action its declared
    capabilities don't cover. The host catches + surfaces this as a
    cell-level error so a contract drift doesn't break the whole
    render pass."""


@dataclass(frozen=True)
class Capabilities:
    """Parsed view of a widget's ``requires:`` declarations.

    All fields are read-only after construction so a hot-path check
    (the socket hook fires on every connect) doesn't need a lock.

    * ``network_hosts``: hostnames the widget may connect to. The
      special value ``"*"`` means "unrestricted but reviewed and
      acknowledged" — the catalog CI flags it.
    * ``settings_scopes``: scopes the manifest claims; not enforced
      in v1, surfaced to docs / reviewer output.
    * ``filesystem_writes``: paths the widget may write outside its
      ``data_dir``; same, review-only in v1.
    * ``declared``: True when the manifest had a ``requires:`` array
      at all. Widgets without one default to unenforced (the
      pre-#2 behaviour) so the marketplace upgrade doesn't break
      existing installs."""

    plugin_id: str
    network_hosts: frozenset[str] = field(default_factory=frozenset)
    settings_scopes: frozenset[str] = field(default_factory=frozenset)
    filesystem_writes: frozenset[str] = field(default_factory=frozenset)
    declared: bool = False

    @property
    def allows_any_network(self) -> bool:
        return "*" in self.network_hosts

    def allows_host(self, host: str) -> bool:
        """True when the widget may connect to ``host``. Undeclared
        widgets (no ``requires:`` block in the manifest) get an
        implicit pass so we don't break every existing install."""
        if not self.declared:
            return True
        if self.allows_any_network:
            return True
        return host in self.network_hosts


# Active capability scope for the current render call. ``None`` means
# host code is running (no plugin in the call stack); the socket hook
# treats this as "allow", same as undeclared widgets.
_active: contextvars.ContextVar[Capabilities | None] = contextvars.ContextVar(
    "tesserae_active_capabilities",
    default=None,
)


def parse(plugin_id: str, raw: object) -> Capabilities:
    """Build :class:`Capabilities` from a manifest's ``requires:``
    array (or ``None`` if absent).

    Tolerates a missing field — that's the legacy / unenforced path.
    Malformed entries log a warning and are skipped rather than
    failing the load, the schema validation that ran before
    ``parse`` already caught the structural shape errors; this
    is the data-cleaning step.
    """
    if raw is None:
        return Capabilities(plugin_id=plugin_id, declared=False)
    if not isinstance(raw, list):
        logger.warning(
            "plugin %s: requires must be a list (got %s); ignoring",
            plugin_id,
            type(raw).__name__,
        )
        return Capabilities(plugin_id=plugin_id, declared=False)

    network: set[str] = set()
    settings: set[str] = set()
    fs: set[str] = set()

    for entry in raw:
        if not isinstance(entry, str) or ":" not in entry:
            logger.warning("plugin %s: malformed requires entry %r; skipping", plugin_id, entry)
            continue
        category, _, value = entry.partition(":")
        value = value.strip()
        if not value:
            logger.warning("plugin %s: empty value in %r; skipping", plugin_id, entry)
            continue
        if category not in _CATEGORIES:
            logger.warning(
                "plugin %s: unknown capability category %r; skipping",
                plugin_id,
                category,
            )
            continue
        if category == "network":
            network.add(value)
        elif category == "settings":
            settings.add(value)
        elif category == "filesystem":
            # Strip the optional ``write:`` prefix so the stored form
            # matches what the reviewer reads. Reads are implicit;
            # the manifest only needs to declare writes.
            if value.startswith("write:"):
                value = value[len("write:") :]
            fs.add(value)

    return Capabilities(
        plugin_id=plugin_id,
        network_hosts=frozenset(network),
        settings_scopes=frozenset(settings),
        filesystem_writes=frozenset(fs),
        declared=True,
    )


@contextmanager
def capability_scope(caps: Capabilities | None) -> Iterator[None]:
    """Enter a capability scope for the duration of a widget call.

    Used by the host (``composer._fetch_plugin_data``, the plugin
    blueprint route handlers, ``choices()`` resolvers) so the
    socket hook can tell which widget's allowlist is in force when
    a connect attempt happens deep in a urllib stack.

    ``caps=None`` is the host-code case, allow everything. The
    contextvar machinery handles nested scopes correctly (a widget
    fetching its own data + then calling back into another
    plugin's ``choices()`` swaps scopes cleanly)."""
    token = _active.set(caps)
    try:
        yield
    finally:
        _active.reset(token)


# -- socket egress hook -----------------------------------------------


_original_create_connection = socket.create_connection
_installed: bool = False


def _hooked_create_connection(
    address: Any,
    *args: Any,
    **kwargs: Any,
) -> socket.socket:
    """Drop-in replacement for ``socket.create_connection`` that
    consults the active capability scope and refuses unallowed hosts.

    Sits at the bottom of basically every Python HTTP / TCP stack
    (``urllib.request``, ``http.client``, ``requests``, ``httpx``,
    ``aiohttp``'s sync path, etc.), so one hook covers them all.
    Lower-level direct ``socket.socket().connect`` usage isn't
    intercepted here — see :func:`install` for the second hook."""
    caps = _active.get()
    if caps is not None:
        host = address[0]
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="replace")
        if not caps.allows_host(host):
            raise CapabilityDenied(
                f"widget {caps.plugin_id!r} tried to connect to {host!r} "
                "but didn't declare it under requires: in plugin.json"
            )
    return _original_create_connection(address, *args, **kwargs)


_original_socket_connect = socket.socket.connect


def _hooked_socket_connect(
    self: socket.socket,
    address: Any,
) -> None:
    """Catch the lower-level ``socket.socket.connect`` path too. Some
    HTTPS stacks open a TCP socket and call ``connect`` directly
    rather than going through ``create_connection``; mirror the
    same allow/deny logic so neither layer can be bypassed."""
    caps = _active.get()
    if caps is not None and isinstance(address, tuple) and len(address) >= 1:
        host = address[0]
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="replace")
        if isinstance(host, str) and not caps.allows_host(host):
            raise CapabilityDenied(
                f"widget {caps.plugin_id!r} tried to connect to {host!r} "
                "but didn't declare it under requires: in plugin.json"
            )
    return _original_socket_connect(self, address)


def install() -> None:
    """Install the socket hooks. Idempotent; called once from the
    app factory after the registry is built. Tests that need the
    hook removed call :func:`uninstall`."""
    global _installed
    if _installed:
        return
    socket.create_connection = _hooked_create_connection
    socket.socket.connect = _hooked_socket_connect  # type: ignore[assignment,method-assign]
    _installed = True
    logger.debug("capability hooks installed")


def uninstall() -> None:
    """Restore the original socket APIs. Used by the test suite when
    a fixture needs unrestricted egress (e.g. integration tests
    that hit real upstreams without a contextvar scope set)."""
    global _installed
    if not _installed:
        return
    socket.create_connection = _original_create_connection
    socket.socket.connect = _original_socket_connect  # type: ignore[method-assign]
    _installed = False
    logger.debug("capability hooks uninstalled")
