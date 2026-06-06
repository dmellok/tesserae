# Catalog repo seed

This directory is a copy-this-into-a-new-repo starter for the community
widget catalog that `app/marketplace.py` consumes. It is **not** a
runtime part of Tesserae — it's the scaffold for a separate GitHub
repo (default name: `dmellok/tesserae-widgets`).

## Layout

```
.
├── README.md                       # catalog homepage on GitHub
├── widgets.json                    # the index Tesserae fetches
├── schema/marketplace.schema.json  # copy of the host's index schema (CI uses this)
├── CONTRIBUTING.md                 # how to submit a widget
├── screenshots/                    # per-widget thumbnails (lg required, others optional)
│   └── <id>/
│       ├── lg.png
│       ├── md.png   (optional)
│       ├── sm.png   (optional)
│       └── xs.png   (optional)
└── .github/
    ├── PULL_REQUEST_TEMPLATE.md
    └── workflows/
        └── validate.yml            # CI: schema + sha256 + manifest checks
```

## To use this seed

```sh
gh repo create dmellok/tesserae-widgets --public --description "Community widget catalog for Tesserae"
cd ../tesserae-widgets   # wherever you cloned the new empty repo
cp -r /path/to/tesserae/docs/marketplace-catalog-seed/. .
git add .
git commit -m "Initial catalog scaffold"
git push -u origin main
```

Then point a fresh Tesserae install at the new catalog by leaving
`Settings → Server → App → Marketplace catalog URL` at the default
(or editing the default in `app/settings/field_defs.py` if you forked
the repo name).

## Keeping the schema in sync

When the host's index schema bumps (`schema/marketplace.schema.json`
inside Tesserae), copy the new schema into this catalog repo too. CI
on the catalog validates against the local copy, so a drift between
host and catalog will surface there before users hit it on Browse.
