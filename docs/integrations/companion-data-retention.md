# Companion snapshot retention

Companion's Reminders and Health settings independently choose how long this
phone's latest snapshot is kept on the selected Tesserae server. The default is
48 hours; the app also offers 1, 7, 30, and 90 days, or Never. Apply a changed
period by syncing. It applies to all widgets using that publisher and source,
not to the due dates of individual reminders or the seven-day Health window.

Updated servers advertise `personal_data_retention` and a finite
`personal_data_max_ttl_seconds` limit of 365 days. A publisher opts into Never
with a required, explicit `expires_at: null`; omission or malformed dates are
rejected. Existing clients continue publishing their usual 48-hour deadline.
Upgrade the server before selecting longer retention or Never in Companion.

The server always replaces the previous snapshot. Finite deadlines still remove
raw values and retain only non-sensitive expiry metadata. Never retains values
until a new snapshot replaces them or the user stops sync and deletes them.
Changing Never to a finite period takes effect on the next accepted sync.
Already expired values require a new sync and cannot be recovered by changing
settings. Disabling the source still deletes the latest snapshot.

Freshness is separate: after 24 hours without a new snapshot, widgets can report
stale data but keep using it until its chosen expiry. Widgets should treat a null
deadline as no expiry and keep tracking freshness separately. E-ink frames and
rendered History images can remain until replaced; snapshot deletion does not
remotely erase an already displayed image.
