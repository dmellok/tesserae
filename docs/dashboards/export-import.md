# Export and import

Move dashboards and lineups between two Tesserae servers with a file.
Build on a test server, export, and import into the one your displays use.

This is not a backup. To copy a whole server, use **Backups** in Settings.

## Export

- **One dashboard:** on **Dashboards**, open its row and choose the download icon.
- **One lineup:** on **Lineups**, open the lineup's **More** menu and choose **Export**. The file includes every dashboard the lineup shows.
- **Everything:** on **Dashboards**, choose **Export all**. You get every active dashboard and every lineup in one file.

The file is plain JSON, so you can keep it in git.

## What the file leaves out

An export holds the design, not the server it came from:

- Which displays a dashboard or lineup is on.
- Widget options that hold a password or key, such as the REST widget's address and headers.
- Request headers on a code element's data sources.
- API keys and other widget settings.

Home Assistant entity names, calendar addresses and similar values stay in the file.
Check them after importing if the two servers differ.

## Import

1. On **Dashboards**, choose **Import** and pick the file.
2. Check the list. Nothing changes until you choose **Import**.
3. For anything that already exists on this server, choose one:
    - **Replace** swaps in the design from the file and keeps the displays it is already on.
    - **Add as a copy** leaves the existing one alone.

A new dashboard or lineup is not on any display. Add it to one from its editor.

If the file uses a widget this server does not have, the dashboard still imports.
That cell stays empty until you install the widget.

## Moving the same work again

A dashboard keeps its identity across servers.
Export it again after more changes, import it, and **Replace** updates the one from last time.
