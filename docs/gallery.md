# Gallery

See home installations in the [README](https://github.com/dmellok/tesserae/blob/main/README.md#tesserae). Browse individual widgets in the [bundled widget gallery](widgets/gallery.md) or [community catalog](https://tesserae.ink/catalog/).

## Inside Home Assistant

![Home Assistant dashboard with the Tesserae Hub tile plus four panel device cards](screenshots/ui/ha-hub.png)

Home Assistant shows a Hub tile and one card per screen.
Each card shows a live image, battery level, signal and IP address.

## Composing a dashboard

![Composing a Home Assistant dashboard in Tesserae, cell grid on the left, rendered preview on the right with ha_climate plus ha_history plus calendar plus Spotify plus todo tiles](screenshots/ui/ha-composition.png)

The tile grid is on the left. The live preview is on the right.

![Paper Calendar dashboard composition, Unsplash photo on the left plus calendar_month on the right at 1200x1600](screenshots/ui/paper-calendar.png)

"Paper Calendar" pairs an edge-to-edge Unsplash photo with a month grid. It is drawn at 1200×1600 for an Inky 13.3".

![Bedside dashboard composition with vivid gradient weather plus clock tiles in the preview, Sending toast in the corner](screenshots/ui/bedside.png)

Bedside dashboard with two of the gradient themes, mid-send.

## One widget, every cell size

![The same weather_now_scenic widget rendered at four different cell sizes (large landscape, medium portrait, medium square, two small squares) showing different content density per size](screenshots/ui/widget-sizing.jpg){ width="600" }

The `weather_now_scenic` widget uses CSS container queries to rearrange content as its cell shrinks. One version fits all sizes.
