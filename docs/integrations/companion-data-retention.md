# Companion snapshot retention

In Companion, choose how long the selected server keeps this phone's latest data.
Reminders and Health have separate settings. Choose 48 hours (default), 1, 7, 30 or 90 days, or Never.

Sync to apply a change. It affects all widgets using that phone and source.
Reminder due dates and the seven-day Health window stay the same.

Upgrade the server before choosing a longer period or Never.
Updated servers advertise `personal_data_retention` and a
`personal_data_max_ttl_seconds` limit of 365 days.

Publishers must send `expires_at: null` for Never. Missing or malformed dates
are rejected. Existing clients keep their usual 48-hour deadline.

Each accepted sync replaces the previous snapshot, the server's saved copy.

- At expiry, the server deletes raw values and keeps only non-sensitive expiry details.
- Never keeps values until a new snapshot replaces them, or you stop sync and delete them.
- Switching from Never to a timed period takes effect on the next accepted sync.
- Expired values need a new sync. Changing settings cannot recover them.
- Disabling a source deletes its latest snapshot.

After 24 hours without a new snapshot, widgets can mark data as stale.
They keep using it until it expires. With a null deadline, widgets never expire the data but still track freshness.

Deleting a snapshot does not erase images already on an e-ink screen or in History.
Those images can remain until replaced.
