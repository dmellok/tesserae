# Community widgets gallery

!!! tip "The catalog now lives at tesserae.ink/catalog"
    Browse widgets, themes and dashboard templates at **[tesserae.ink/catalog](https://tesserae.ink/catalog/)**.
    Filter by type or category; each entry shows its install count. The listing stays current.

Community entries aren't included by default. Widgets that left the built-in set (F1, Spotify,
GitHub and others) are in the catalog. Install them from **Settings → Widgets → Browse catalog**.

## How install works

1. Open **Settings → Widgets → Browse catalog** in Tesserae.
2. Click **Install** on the entry you want. Tesserae downloads the release archive and checks its sha256 against the catalog.
   It installs the widget folders in `plugins/`.
3. Restart Tesserae (or click **Restart now** in the banner) so the
   plugin loader picks them up.
4. The widget appears in the composition picker on the next page
   edit.

Uninstall from the same screen. Choose whether to keep or delete the plugin's data folder.

## Updates

Browse catalog shows **Update to vX** when a newer release is available.
Use **Update all** to install all pending updates. Releases needing a newer Tesserae are skipped with a message.

Restart Tesserae to load the new versions.

The **Widgets** sidebar entry counts widgets with updates waiting.
Tesserae checks the catalog at most once an hour in the background. This needs **Online features** on.

Hide the count with **Settings → Widgets → Catalog updates**. Browse catalog still marks each widget's updates.

## Trust model

Every entry is a pull request to
[dmellok/tesserae-widgets](https://github.com/dmellok/tesserae-widgets)
that the maintainer reads in full before accepting. Review consists of a code audit.

Tesserae limits each widget's outgoing network access through its
[`requires:` capability declarations](../widgets.md#capabilities-requires).
Entries flag widgets that use extended colour palettes. See [Publish via the catalog](../dev/publishing-a-widget.md) for the review checklist.

The catalog is the right home for widgets that:

- Need an account or API key.
- Serve a niche audience or use regional data.
- Are variations of bundled widgets, such as another clock or weather widget.

!!! tip "Verified badge"
    The catalog owner reviews and maintains **verified** entries. Their source repositories belong to the owner.

    The badge certifies the widget's code. It doesn't endorse the service the widget connects to.

---

To contribute one widget or a bundle, see [Publish via the catalog](../dev/publishing-a-widget.md).
