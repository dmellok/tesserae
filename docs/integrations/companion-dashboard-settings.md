# Dashboard settings in Companion

Servers advertising `dashboard_settings` let Companion read and edit a saved
Dashboard's settings without opening the full web editor. Grid dashboards offer
name, icon, display bindings, content update cadence, device wake cadence and
appearance. Servers also advertising `dashboard_component_settings` discover
widget options for each Grid cell, Canvas widget/data element, Code widget source
and data binding. Existing dashboards need no new declarations for those fields.
Declared `canvas.inputs` take priority over matching discovered fields and may
control several targets. The full layout, widget code and CSS remain in the web
editor.

Enable **Edit dashboard settings** for the paired app in **Settings → Companion**.
This optional `dashboards:write` scope is not granted by pairing or by upgrading
an existing server. `dashboards:read` allows reading settings, with `can_edit`
reflecting the paired app's current permission. Permission changes take effect on
the next request without pairing again.

## Contract

The companion contract and examples live in
`tests/companion/contract/app-v1.openapi.yaml` and its `Fixtures` directory.
The vendored OpenAPI file stays unchanged. Settings additions are layered through
`tests/companion/contract_errata.py` from `dashboard_settings.openapi.yaml`;
those definitions and the five settings fixtures were copied from
[`charmmmz/tesserae-companion-ios` commit `30b32d42dec5db2a318b9dccfba2fa214d7325a6`](https://github.com/charmmmz/tesserae-companion-ios/tree/30b32d42dec5db2a318b9dccfba2fa214d7325a6/Contracts).
The existing errata tests flag additions to remove when a future re-vendor
includes them.

- `GET /api/app/v1/dashboards/{id}/settings` returns the saved settings, available
  theme/style/font options, declared inputs, per-instance `components`, permission,
  coverage and opaque page/schema revisions.
- `PATCH /api/app/v1/dashboards/{id}/settings` accepts that `revision` plus only the
  changed `settings`, `inputs` or `component_values`, plus `reset_fields` when
  removing saved overrides. The returned body has the same shape as GET.
- `GET /api/app/v1/dashboards/{id}/settings/fields/{field_id}/choices` resolves
  choices only for a field referenced by that dashboard. It accepts `q`, `limit`
  (1–100), `offset` and `schema_revision`; `status: unavailable` is distinct from
  a successful empty result. It needs `dashboards:read`, not web-editor or MCP
  permissions. Secret fields are never exposed by this route. Omitting
  `schema_revision` resolves choices against the current schema; supplying an
  outdated revision returns `409 schema_conflict`.
- The revision covers the entire dashboard, including advanced edits from the
  web editor or MCP. The check and write share the page store's lock. A changed
  revision returns `409 revision_conflict`; reload and review changes before
  retrying. A deleted dashboard returns 404.
- Send `schema_revision` with component edits/resets. Changed widget definitions,
  defaults or constraints return `409 schema_conflict`; ordinary dynamic choice
  changes do not. Field IDs are opaque and scoped to that dashboard's schema.
- Invalid edits return `400 invalid_request`, with `error.fields` mapping field
  paths such as `settings.name`, `inputs.location`, `component_values.{field_id}`
  or `reset_fields.{field_id}` to messages. Validation
  errors do not echo submitted values, and a failed edit changes nothing.

```json
{
  "revision": "the opaque revision returned by GET",
  "settings": {"name": "Kitchen", "refresh_minutes": 30, "sleep_interval_s": null},
  "inputs": {"location": "Shanghai", "optional_api_key": null}
}
```

`refresh_minutes` is the server content update interval (0 means on demand,
maximum 1440). `sleep_interval_s` is the display wake interval (1–604800 seconds,
subject to the display's existing limits); null inherits the display's setting.
Theme/style/font choices come from the same registries as the web editor. A null
font uses the default. Existing unavailable appearance values remain untouched
when other fields are saved.

Omitted fields, inputs and secrets are preserved. A supplied null clears an
optional field while retaining an explicit override; `reset_fields` removes the
override so the widget default applies. Required fields cannot become empty.
GET reports defaults without saving them. Mixed multi-target inputs omit `value`
and report `value_state: mixed`; they change only after an explicit edit. Secret fields return
`configured` but never their current value or default, including headers,
manifest-declared sensitive options and overlapping aliases of secret targets.
Sending a value replaces it. Sending a whole header-map input replaces that map,
so removed headers do not survive the update.

Supported component types are `string`, `text`, `textarea`, `url`, `password`,
`integer`, `number`, `boolean`, `switch`, `select` and `multiselect`. Numbers and
booleans are JSON values, not strings. Select choices retain their declared JSON
value types. Multiselect array order is preserved, including media-player
priority; duplicates are rejected. Selected values removed from the provider may
be retained or reordered, while new values must still be available. Dynamic
provider failures return a generic retry message without credentials or provider
exception details. Duplicate input names appear once as a read-only field; fix the declarations in
the web editor before editing them in Companion. Saving other settings preserves
the original declarations. Location search, sliders, native colors and fields
with conditional `show_if` rules remain read-only. Unknown types,
non-text secret fields and inputs with unresolved targets return
`editable: false` and can be opened in the web editor. Secret fields never expose
their choice values or labels.

Saving updates the dashboard and changes its content-keyed preview cache token;
it does not push a frame. Companion can request the refreshed preview and invoke
the existing explicit Push action afterward. No firmware changes are needed for
this settings API.
