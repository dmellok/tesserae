# Design note: fold Rotations into Decks

## Decision (2026-07-28): reframe only, merge DEFERRED

Scoping the merge showed Rotation is not a simple `(page, dwell)` loop: it carries
per-step `conditions`, `scheduled`/`priority` modes, `min_hold_minutes`, `end_at`
windows, `days_of_week`, `smart_sync`, and `priority`. A lossless merge means ~9
new `Deck` fields, moving the whole rotation scheduler path onto decks, a mapping
migration, and rewriting the rotation tests: a large refactor of working code for
"one fewer noun". Not worth the churn right now.

**Shipped instead:** the reframe only. The three pages now cross-position each
other in their ledes so users know which to pick (Deck = move by tap; Rotation = a
deck that advances on a timer; Schedule = a time trigger). No model, scheduler,
migration, routes, MCP, or splash changes. The full-merge plan below is kept as the
blueprint if the appetite ever returns.

## Problem

Schedules, Decks, and Rotations are presented as three peer concepts, but they
answer overlapping questions and users have to guess which fits. There are really
only two ideas:

1. **A group of pages a display moves through.** That is a Deck. A Rotation is the
   same thing that advances on a timer instead of a tap.
2. **A time trigger** ("at 7am put this page on that display"). That is a Schedule.
   It is genuinely different and stays as-is.

So: merge Rotation into Deck, keep Schedule separate. Three nouns become two.

## End-state model

A **Deck** is a group of pages with an **advance** mode:

- `manual` (today's Deck): move by tap / button / swipe via `DeckLink`s.
- `timer` (today's Rotation): auto-advance through the pages in order, on a
  wall-clock anchor, looping.
- `both`: auto-advances AND accepts taps (new, neither had it).

`Schedule` is unchanged and can point a display at a page or a deck.

## Model changes

`app/state/deck_model.py`:

- `Deck.advance: Literal["manual","timer","both"] = "manual"` (default keeps every
  existing deck behaving exactly as today).
- `Deck.advance_anchor: str = "00:00"` (daily re-anchor for timer mode; from
  `Rotation.anchor`, same `HH:MM` shape and daily-reseed semantics so DST flips
  don't desync).
- `Deck.advance_interval_minutes: int = 30` (default per-step dwell when a page
  does not override it).
- `Deck.priority: int = 0` (timer-mode preemption vs schedules; from
  `Rotation.priority`).
- `DeckPage.dwell_minutes: int | None = None` (per-page dwell override; from
  `RotationStep.dwell_minutes`).
- `DeckPage.conditions: list[Condition] = []` (from `RotationStep.conditions`;
  an unmet condition advances past the step in timer mode, same as rotations).

`refresh_interval_minutes` on `DeckPage` stays and is orthogonal: it is how often
the page's frame is re-rendered in the background, not how long it is shown.

Manual decks are an arbitrary graph; timer decks advance through `pages` in
document order and loop. Branching graphs stay manual-only. `entry_page_id` is the
timer start (falls back to `pages[0]`).

## Scheduler

`app/scheduler.py` currently has a rotation path (compute the current step from
`(now_local - anchor_today) % cycle_minutes`, push if the step's page changed) and
a separate deck path (background refresh + warm cache). Move the rotation
step-computation to operate on timer-decks:

- On each tick, for every enabled deck with `advance in (timer, both)`, compute the
  current step from `advance_anchor` + per-page dwell, honour `conditions`, and push
  if the resolved page differs from what was last pushed for that deck+device.
- Reuse the existing smart-sync JIT path (decks already warm frames per device, so
  a timer advance can promote the pre-warmed frame instead of rendering inline).
- `RotationStore` and the rotation scheduler branch are deleted once decks cover it.

## Migration (lossless, automatic, one-time)

On load, convert every `Rotation` to a `Deck`:

| Rotation                     | Deck                                         |
|------------------------------|----------------------------------------------|
| `id`, `name`, `enabled`      | same (id kept; collision-suffix if a deck id already exists) |
| `device_ids`                 | `device_ids`                                 |
| `steps[]` (order preserved)  | `pages[]` with `links=[]`                     |
| `RotationStep.page_id`       | `DeckPage.page_id`                            |
| `RotationStep.dwell_minutes` | `DeckPage.dwell_minutes`                      |
| `RotationStep.conditions`    | `DeckPage.conditions`                         |
| `anchor`                     | `advance_anchor`                             |
| `priority`                   | `priority`                                   |
| (implicit)                   | `advance = "timer"`, `entry_page_id = steps[0].page_id` |

Displays keep showing exactly what they showed; behaviour is identical, only the
label changes. Runs once; a marker in settings prevents re-running.

## Routes / IA

- Remove the Rotations nav item. `/rotations*` -> 301 to `/decks`.
- Deck editor gains the **Advance** control (manual / timer / both), a default
  interval, and optional per-page timing.
- Settings groups the two by intent: Decks = "pages a display moves through, by
  tap or on a timer"; Schedules = "put a page or deck on a display at a set time".

## MCP

`create_rotation` / `list_rotations` / `delete_rotation` become thin deprecated
aliases that create/list/delete timer-decks, so the bridge and agent scripts do
not break. Dropped a couple of releases later. `create_deck` gains the advance
fields.

## First-run notice ("what happened to rotations")

Shown once, only to installs that had >= 1 rotation at upgrade time (new users
never see it). A dismissible modal on the first admin page load after the update;
dismissal sets `settings.app.rotations_merged_notice_seen = true`.

Copy:

> **Rotations are now part of Decks**
>
> A rotation was just a deck that advances on a timer instead of a tap, so we
> combined them. Your rotations have been moved to Decks automatically, with the
> same pages and timings and set to advance on a timer. Nothing changed on your
> displays, and there is nothing you need to do.
>
> Open a deck any time to switch it between advancing on a tap, on a timer, or both.
>
> [ View Decks ]  [ Got it ]

## Build stages

1. **Model**: extend `Deck` / `DeckPage` with the advance fields (default `manual`,
   fully back-compat). Tests.
2. **Scheduler**: drive timer-decks from the (moved) rotation step logic. Tests.
3. **Migration**: rotations -> decks on load; `/rotations` redirect; MCP aliases.
4. **UI**: Deck editor Advance control; remove Rotations nav; the first-run splash.
5. **Cleanup**: delete `RotationStore` / rotation model / rotation routes once the
   above is green and migration is proven. Docs.
