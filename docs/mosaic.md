# Looks

Looks is an open design system with five page styles for e-ink dashboard code elements.
Choose a look with one attribute. Components stay readable at every screen size, in colour or one-bit (black or white).

In code, the library is called Mosaic. The global is `Mosaic`, and CSS classes start with `m-`.
Get the source at [dmellok/tesserae-mosaic](https://github.com/dmellok/tesserae-mosaic).

A Mosaic layout in the cloud editor means a dashboard of widget tiles. It is unrelated to this library.

| Look | What it is |
|---|---|
| `bauhaus` | Solid colour blocks, thick rules, very heavy type and big icons. |
| `almanac` | A printed page: serif numerals, a masthead between rules, one red accent. |
| `signal` | Swiss minimal on a twelve-column grid, designed for one-bit panels first. |
| `pixel` | Pixel type, pixel-art icons and dithered windows. |
| `os7` | The classic Macintosh System 7: pinstriped windows, a menu bar, dialogs. |

Looks is optional. A code element loads it through a `data-look` attribute, Mosaic's classes or a call to `Mosaic.`.
Tesserae includes the core styles, chosen look and script, plus that look's fonts and icons.

```html
<main class="m" data-look="almanac"><div class="m-page">
  <header class="m-masthead"><span>Tuesday</span><span>29 September</span><span>2026</span></header>
  <div class="m-hero"><div class="m-clock" data-fit></div></div>
</div></main>
```

```js
Mosaic.clock(document.querySelector(".m-clock"));
Mosaic.ready();   // loads the look's fonts, then fits [data-fit] text and trims lists
```

See the [Mosaic README](https://github.com/dmellok/tesserae-mosaic#readme) for the full documentation.
To update the bundled copy, build Mosaic and run `python3 scripts/sync_mosaic.py`.
