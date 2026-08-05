"""Debounced widget dependency refreshes for server-side data changes.

Data ingestion remains synchronous and authoritative: callers store an accepted
snapshot first, then enqueue a small event containing only a source id and
opaque selector ids. A daemon timer waits for a quiet window, resolves the
currently opted-in widget placements, deduplicates their containing pages, and
hands those page ids to the existing scheduler refresh machinery.

No reminder titles, list names, or item bodies enter this event path.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from app.plugin_loader import PluginRegistry
from app.state.page_store import Page, PageStore

logger = logging.getLogger(__name__)

DEFAULT_DATA_CHANGE_DEBOUNCE_SECONDS = 10.0


@dataclass(frozen=True)
class DataChangeEvent:
    """A non-sensitive dependency event.

    ``selectors=None`` is a source-wide change. A concrete set narrows the
    event to matching placement option values, such as Reminders ``list_id``.
    """

    source: str
    selectors: frozenset[str] | None = None


def _snapshot_data(snapshot: dict[str, Any] | None) -> Any:
    if not isinstance(snapshot, dict):
        return None
    return snapshot.get("data")


def _canonical_items(value: Any) -> tuple[str, ...]:
    """Order-insensitive reminder item representation for semantic diffs."""
    if not isinstance(value, list):
        return ()
    encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
    return tuple(sorted(encoded))


def _reminder_lists(snapshot: dict[str, Any] | None) -> dict[str, tuple[str, tuple[str, ...]]]:
    data = _snapshot_data(snapshot)
    raw_lists = data.get("lists") if isinstance(data, dict) else None
    if not isinstance(raw_lists, list):
        return {}
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for reminder_list in raw_lists:
        if not isinstance(reminder_list, dict):
            continue
        list_id = reminder_list.get("id")
        title = reminder_list.get("title")
        if isinstance(list_id, str) and isinstance(title, str):
            out[list_id] = (title, _canonical_items(reminder_list.get("items")))
    return out


def personal_data_update_event(
    source_id: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> DataChangeEvent | None:
    """Return the semantic event for an accepted personal-data replacement.

    Envelope timestamps and expiry are deliberately ignored. Reminders events
    identify only lists whose title or item set changed; list and item ordering
    alone is not meaningful. The deprecated fridge source likewise treats item
    ordering as presentation-only. The first usable snapshot is source-wide
    because freshness/missing-source state can affect every configured placement.
    """
    source = f"personal_data.{source_id}"
    previous_data = _snapshot_data(previous)
    current_data = _snapshot_data(current)
    if previous_data is None:
        if source_id == "reminders":
            current_lists = _reminder_lists(current)
            if current_lists:
                return DataChangeEvent(source=source, selectors=frozenset(current_lists))
        return DataChangeEvent(source=source)
    if source_id == "reminders.fridge":
        previous_items = previous_data.get("items") if isinstance(previous_data, dict) else None
        current_items = current_data.get("items") if isinstance(current_data, dict) else None
        return (
            None
            if _canonical_items(previous_items) == _canonical_items(current_items)
            else DataChangeEvent(source=source)
        )
    if source_id != "reminders":
        return None if previous_data == current_data else DataChangeEvent(source=source)

    before = _reminder_lists(previous)
    after = _reminder_lists(current)
    changed = {
        list_id
        for list_id in before.keys() | after.keys()
        if before.get(list_id) != after.get(list_id)
    }
    if not changed:
        return None
    return DataChangeEvent(source=source, selectors=frozenset(changed))


def personal_data_delete_event(
    source_id: str, previous: dict[str, Any] | None = None
) -> DataChangeEvent:
    """DELETE invalidates only known selectors, else the complete source."""
    source = f"personal_data.{source_id}"
    if source_id == "reminders":
        previous_lists = _reminder_lists(previous)
        if previous_lists:
            return DataChangeEvent(source=source, selectors=frozenset(previous_lists))
    return DataChangeEvent(source=source)


def _selector_values(raw: Any) -> set[str]:
    if isinstance(raw, (list, tuple, set, frozenset)):
        return {str(value) for value in raw if value is not None and str(value)}
    if raw is None:
        return set()
    value = str(raw)
    return {value} if value else set()


def _placement_matches(
    *,
    plugin_id: str | None,
    options: dict[str, Any],
    enabled: bool,
    registry: PluginRegistry,
    events: dict[str, frozenset[str] | None],
) -> bool:
    if not enabled or not plugin_id:
        return False
    plugin = registry.get(plugin_id)
    if plugin is None:
        return False
    for spec in plugin.on_change_updates:
        source = spec["source"]
        if source not in events:
            continue
        changed_selectors = events[source]
        selector_option = spec.get("selector_option")
        if selector_option is None or changed_selectors is None:
            return True
        if _selector_values(options.get(selector_option)) & set(changed_selectors):
            return True
    return False


def matching_page_ids(
    pages: Iterable[Page],
    registry: PluginRegistry,
    events: dict[str, frozenset[str] | None],
) -> set[str]:
    """Resolve opted-in Grid and Canvas placements to containing page ids."""
    matched: set[str] = set()
    for page in pages:
        for cell in page.cells:
            if _placement_matches(
                plugin_id=cell.plugin,
                options=cell.options,
                enabled=cell.update_on_change,
                registry=registry,
                events=events,
            ):
                matched.add(page.id)
                break
        if page.id in matched or page.canvas is None:
            continue
        for element in page.canvas.els:
            if element.kind != "widget":
                continue
            if _placement_matches(
                plugin_id=element.widget,
                options=element.options,
                enabled=element.update_on_change,
                registry=registry,
                events=events,
            ):
                matched.add(page.id)
                break
    return matched


class DataChangeRefreshCoordinator:
    """Quiet-window debounce from data events to existing page refreshes."""

    def __init__(
        self,
        *,
        page_store: PageStore,
        plugin_registry: Callable[[], PluginRegistry],
        refresh_pages: Callable[[set[str]], None],
        debounce_seconds: float = DEFAULT_DATA_CHANGE_DEBOUNCE_SECONDS,
    ) -> None:
        self._page_store = page_store
        self._plugin_registry = plugin_registry
        self._refresh_pages = refresh_pages
        self._debounce_seconds = max(0.0, debounce_seconds)
        self._lock = threading.Lock()
        self._pending: dict[str, frozenset[str] | None] = {}
        self._generation = 0
        self._timer: threading.Timer | None = None
        self._stopped = False

    def notify(self, event: DataChangeEvent) -> None:
        """Merge one event and restart the 10-second quiet window."""
        with self._lock:
            if self._stopped:
                return
            if event.source not in self._pending:
                self._pending[event.source] = event.selectors
            else:
                current = self._pending[event.source]
                self._pending[event.source] = (
                    None
                    if current is None or event.selectors is None
                    else current | event.selectors
                )
            self._generation += 1
            generation = self._generation
            if self._timer is not None:
                self._timer.cancel()
            # Deliberately a pure quiet-window debounce in v1: there is no
            # maximum-latency cap, so a sustained stream waits until it pauses.
            timer = threading.Timer(self._debounce_seconds, self._fire, args=(generation,))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _fire(self, generation: int) -> None:
        with self._lock:
            if self._stopped or generation != self._generation:
                return
            events = dict(self._pending)
            self._pending.clear()
            self._timer = None
        try:
            pages = self._page_store.list()
            page_ids = matching_page_ids(pages, self._plugin_registry(), events)
            if page_ids:
                self._refresh_pages(page_ids)
        except Exception:
            # Refresh is explicitly detached from ingestion. Never surface
            # renderer/delivery failures back through a successful snapshot PUT.
            logger.exception("data-change refresh failed after ingestion")

    def stop(self) -> None:
        """Cancel a pending debounce during process shutdown."""
        with self._lock:
            self._stopped = True
            self._pending.clear()
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
