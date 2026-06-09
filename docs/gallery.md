# Gallery

Screenshots of the Tesserae admin UI in action: composing dashboards, integrating with Home Assistant, and how a single widget handles being dropped into different cell sizes.

For panels in domestic context, see the [README](https://github.com/dmellok/tesserae/blob/main/README.md#tesserae) hero image. For the per-widget grid, see the [bundled widget gallery](widgets/gallery.md) or the [community catalog](widgets/community.md).

## Inside Home Assistant

![Home Assistant dashboard with the Tesserae Hub tile plus four panel device cards](screenshots/ui/ha-hub.png)

Tesserae lives inside HA. The Hub tile plus one device card per panel, each pulling its live image entity, battery, signal, and IP.

## Composing a dashboard

![Composing a Home Assistant dashboard in Tesserae, cell grid on the left, rendered preview on the right with ha_climate plus ha_history plus calendar plus Spotify plus todo tiles](screenshots/ui/ha-composition.png)

Composing a dashboard. Cell grid on the left, live preview on the right with HA widgets (climate gauges, history, calendar, Spotify, todo).

![Paper Calendar dashboard composition, Unsplash photo on the left plus calendar_month on the right at 1200x1600](screenshots/ui/paper-calendar.png)

"Paper Calendar" dashboard, full-bleed Unsplash photo + month grid, rendered at 1200×1600 for an Inky 13.3".

![Bedside dashboard composition with vivid gradient weather plus clock tiles in the preview, Sending toast in the corner](screenshots/ui/bedside.png)

Bedside dashboard with two of the gradient themes, mid-send.

## One widget, every cell size

![The same weather_now_scenic widget rendered at four different cell sizes (large landscape, medium portrait, medium square, two small squares) showing different content density per size](screenshots/ui/widget-sizing.jpg){ width="600" }

The same `weather_now_scenic` widget at four cell sizes. Widgets use CSS container queries to redistribute content as the cell shrinks, so one widget composes across every layout instead of shipping size-specific variants.
