# Community widgets gallery

!!! tip "The catalog now lives at tesserae.ink/catalog"
    Browse every community contribution, widgets, themes, and dashboard
    templates, at
    **[tesserae.ink/catalog](https://tesserae.ink/catalog/)**. It is
    filterable by type and category, shows install counts, and is
    always current. This page keeps the install flow and the review
    model; the listing itself is no longer mirrored here.

Community entries don't ship in Tesserae's default install. They're one
click away via **Settings → Widgets → Browse catalog**, and the catalog
is curated: every entry is reviewed before it lands.

If your dashboard is missing a widget that used to be in the bundle
(F1, Spotify, GitHub, etc.), it's there.

## How install works

1. Open **Settings → Widgets → Browse catalog** in Tesserae.
2. Click **Install** on the entry you want. Tesserae downloads the
   release tarball, verifies its sha256 against the catalog, and
   drops the widget folders into `plugins/`.
3. Restart Tesserae (or click **Restart now** in the banner) so the
   plugin loader picks them up.
4. The widget appears in the composition picker on the next page
   edit.

Uninstalling is the same flow in reverse, with the option to keep or
delete the plugin's data dir.

## Trust model

Audit-only review: every catalog entry is a PR to
[dmellok/tesserae-widgets](https://github.com/dmellok/tesserae-widgets)
that the maintainer reads end-to-end before merge. The runtime also
enforces network egress per widget via the [`requires:` capability
declarations](../widgets.md#capabilities-requires); extended-palette
widgets are flagged in the entry. See [Publish via the
catalog](../dev/publishing-a-widget.md) for the reviewer's
checklist.

The catalog is the right home for widgets that:

- Need an account or API key (Spotify, GitHub, Apple Developer).
- Serve a niche audience (F1, OctoPrint, region-specific data).
- Are variants of an already-bundled archetype (a fancier clock,
  another weather variant).

!!! tip "Verified badge"
    Entries marked **verified** are reviewed + maintained by the
    catalog owner; the source repos belong to the catalog
    maintainer. The badge doesn't endorse the third-party service a
    widget connects to (Spotify, GitHub, etc.); it certifies the
    widget code itself.

---

Want to contribute one? See
[Publish via the catalog](../dev/publishing-a-widget.md). The path is
the same whether you're contributing a single widget or a bundle of
related ones.
