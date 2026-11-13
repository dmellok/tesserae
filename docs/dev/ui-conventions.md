# Admin UI conventions

## Links go left of buttons

Put links before buttons. Put secondary buttons before the primary button.
Apply this order to every action row, including those built by scripts.

```
Cancel   Back   Skip   Reset   Learn more      [ Save ]  [ Continue ]
^ link-styled, first                             ^ button-styled, last
```

A link-styled control has no border or fill: an anchor, `.ghost`, or a
text/quiet button (Cancel, Back, Skip, Edit, Reset, Learn more, Clear history).

A button-styled control has a border or fill: `.button`, `.btn`, `.dx-btn`,
`.dx-btn-ghost-sm` (outlined), `.primary`, `.dx-btn-primary`.
Delete text links follow this rule too, except in their own danger-zone row.

On phones, keep this order when rows wrap or stack: links above, primary button last.

## The DOM order is the visual order

Write controls in their visual order. Do not reverse rows with
`flex-direction: row-reverse` or `order`. Tab and screen readers follow the markup.

If moving a submit button changes Enter's action, connect it to its own form
with the `form` attribute. See Clear next to Save custom token in `templates/settings.html`.

## Not covered

- Segmented toggles and tab strips, which select a state.
- Menus of items.
- Corner close/dismiss icons on cards and dialogs.

## Test

`tests/test_action_order_browser.py` checks Classic and Paper in light and dark
at desktop and phone widths, including hidden states. It fails if links follow
buttons, the primary button is not last, or markup order differs from visual order.

Add new pages with action rows to `PAGES`.
