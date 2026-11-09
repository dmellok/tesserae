# Custom CSS on a dashboard

Every dashboard has a **Custom CSS** box under Appearance in the dashboard
editor. What you put there is applied to the page, and also inside every
widget on the dashboard, so you can hide a title row, shrink a label, or
recolour one widget without touching the widget itself.

The box is per dashboard. Two dashboards that want the same tweak each carry
their own copy.

## Hooks you can rely on

Widgets render inside a shadow root, so page-level selectors would normally
stop at the widget's edge. Tesserae copies your CSS into each widget as well,
which is why the selectors below work.

| Selector | What it is |
| --- | --- |
| `[data-widget="weather_now"]` | The root of one widget, by its id. The id is the one shown in the cell's widget picker. |
| `.w-title` | A widget's title row (icon, heading, and the small meta on the right). |
| `.w-title h3` | The heading alone. |
| `.w-title-meta` | The small text at the right of the title row. |
| `.w-body` | Everything under the title row. |
| `.u-muted` | Secondary text. |
| `.cell` | A cell on the page (outside the widgets). |
| `.panel` | The whole page. |

These classes come from the shared widget stylesheet, so they are stable
across widgets that use it. A widget's own inner classes (a specific label, a
row) are that widget's business and may change between versions; use the
browser's inspector on the editor preview to find them, and expect to revisit
after an update.

## Examples

Hide the title row on one widget:

```css
[data-widget="weather_now"] .w-title { display: none; }
```

Hide every title row on the dashboard:

```css
.w-title { display: none; }
```

Keep the heading but drop the small meta text next to it:

```css
.w-title-meta { display: none; }
```

Make the headings smaller everywhere:

```css
.w-title h3 { font-size: 0.8em; }
```

Change one widget's accent without changing the theme:

```css
[data-widget="calendar_day"] { --accent-1: #2a5d8f; }
```

## Notes

- The preview updates when you leave the box, like the other text fields.
- Theme and style pickers still apply; your CSS runs after them and wins ties.
- A closing `</style>` tag in the box is neutralised on save, since the text
  is printed into a style element. Everything else is passed through as
  written, so a typo in a rule is simply ignored by the browser.
- The box holds up to 20,000 characters.
- Canvas dashboards do not need this: a code element on a canvas carries its
  own HTML and CSS.
