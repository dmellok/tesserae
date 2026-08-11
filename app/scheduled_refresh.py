"""Resolve manifest-supported placement schedules independently of events."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.plugin_loader import PluginRegistry
from app.state.page_store import Page
from app.state.widget_update_schedule import WidgetUpdateSchedule


@dataclass(frozen=True)
class ScheduledPlacement:
    """One enabled, currently supported placement update schedule."""

    key: str
    page_id: str
    schedule: WidgetUpdateSchedule


def _supported(plugin_id: str | None, kind: str, registry: PluginRegistry) -> bool:
    if not plugin_id:
        return False
    plugin = registry.get(plugin_id)
    if plugin is None:
        return False
    return any(spec.get("kind") == kind for spec in plugin.on_schedule_updates)


def scheduled_placements(
    pages: Iterable[Page], registry: PluginRegistry
) -> list[ScheduledPlacement]:
    """Return supported Grid and Canvas schedules with stable placement keys."""
    out: list[ScheduledPlacement] = []
    for page in pages:
        for cell in page.cells:
            schedule = cell.update_schedule
            if schedule is not None and _supported(cell.plugin, schedule.kind, registry):
                out.append(
                    ScheduledPlacement(
                        key=(
                            f"grid:{page.id}:{cell.id}:{schedule.kind}:"
                            f"{schedule.at or 'day-boundary'}"
                        ),
                        page_id=page.id,
                        schedule=schedule,
                    )
                )
        if page.canvas is None:
            continue
        for element in page.canvas.els:
            schedule = element.update_schedule
            if (
                element.kind == "widget"
                and schedule is not None
                and _supported(element.widget, schedule.kind, registry)
            ):
                out.append(
                    ScheduledPlacement(
                        key=(
                            f"canvas:{page.id}:{element.id}:{schedule.kind}:"
                            f"{schedule.at or 'day-boundary'}"
                        ),
                        page_id=page.id,
                        schedule=schedule,
                    )
                )
    return out
