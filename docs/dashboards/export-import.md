# Export and import

Move dashboards, lineups and displays between two Tesserae servers with a file.
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

## Displays

A display can travel in the same kind of file, so you can set one up on a test server and bring it to the one you use.

### Export displays

1. Open **Settings › Devices** and expand **Move displays to another server**.
2. Tick the displays you want.
3. Choose **Export displays**.

For each display the file holds its type, name, panel size and orientation, quiet hours, language, and its settings such as sleep interval and button actions.

**Export all** on the Dashboards page never includes displays.

### Access tokens

By default the file has no access tokens and no MAC addresses.
A display imported from it gets a new token on the other server, and you connect it there as you would a new display.

Tick **Include access tokens** to move a display between two of your own servers.
The display then exists on both servers with the same token, and it switches between them when you change only the address it connects to.

!!! warning
    A file with access tokens works like a password for those displays.
    Anyone who has it can read their dashboards from a server that holds them.
    Keep it private and delete it when you are done.

### What a display export leaves out

- A relay panel's pairing. Pair it through the relay on the other server first; the file then updates its settings.
- Settings that hold a password, key, token or sign-in, such as a Wi-Fi password or a cloud account.
- Which dashboards and lineups the display shows. Add those after importing.

### Import displays

Import the file from **Dashboards › Import**, as for dashboards. The list shows each display and what will happen to it:

- **New:** the display is added under the same id.
- **Update:** a display with that id is already here. Choose **Update** to apply the file's settings, or **Leave it as it is**. Updating keeps the access token the display already has on this server.
- **Skipped:** the reason is shown. A display is skipped when this server does not have its type, when its id is used by a different type of display, or when its access token or MAC address already belongs to another display here.

Each display is handled on its own, so one that is skipped does not stop the others, or the dashboards in the same file.
