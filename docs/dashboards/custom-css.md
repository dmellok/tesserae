# Custom CSS on a dashboard

Open **Appearance → Custom CSS** in the dashboard editor to change how it looks.
Your CSS applies to the page and every widget.
Use it to hide a title row, shrink a label, or recolour one widget.

Each dashboard keeps its own CSS. To use a change on two dashboards, copy it to both.

## Hooks you can rely on

Widgets use a shadow root, a browser boundary that normally blocks page-level CSS.
Tesserae copies your CSS inside each widget, so these selectors work:

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

These shared classes stay stable across widgets that use them.
Other classes inside a widget may change between versions.
Find them with your browser's inspector on the editor preview, and check them again after updates.

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
- Tesserae neutralises a closing `</style>` tag on save because the CSS goes inside a style element. Everything else passes through as written. The browser ignores rules with typos.
- The box holds up to 20,000 characters.
- Canvas dashboards do not need this: a code element on a canvas carries its
  own HTML and CSS.
