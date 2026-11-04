# Admin UI conventions

## Links go left of buttons

Wherever a link-styled control and a button-styled control sit together, the
button is on the right of the link. Among buttons, the secondary action comes
first and the primary action is rightmost. This holds for form footers, save
bars, dialogs, wizards, toolbars, banners, list rows, empty states and
anything a script builds.

```
Cancel   Back   Skip   Reset   Learn more      [ Save ]  [ Continue ]
^ link-styled, first                             ^ button-styled, last
```

**Link-styled** is a control with no border and no fill: an anchor, `.ghost`,
a text or quiet button (Cancel, Back, Skip, Edit, Reset, Learn more, Clear
history). **Button-styled** has a border or a fill: `.button`, `.btn`,
`.dx-btn`, `.dx-btn-ghost-sm` (outlined), `.primary`, `.dx-btn-primary`.
A destructive text link such as Delete follows the rule too, unless it sits in
its own danger-zone row.

On a phone, when a row wraps or stacks, keep the same order: links first
(above), the primary last (below).

## The DOM order is the visual order

Write the controls in the order they should appear. Do not turn a row around
with `flex-direction: row-reverse` or `order`: Tab and screen readers follow
the markup, so a reversed row sends the keyboard the wrong way.

If moving a submit button changes which one Enter triggers in a form, point
the moved button at its own form with the `form` attribute rather than leaving
it first (see Clear next to Save custom token in `templates/settings.html`).

## Not covered

Segmented toggles and tab strips (one of several states, not an action),
menus of items, and a corner close or dismiss icon on a card or dialog.

## Test

`tests/test_action_order_browser.py` opens the admin pages in Classic and
Paper, light and dark, at desktop and phone width, with hidden states shown,
and fails when a link is right of a button, the primary is not last, or the
DOM order differs from the visual order. When a new page has an action row,
add the page to `PAGES` there.
