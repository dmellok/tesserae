# Rooms

A screen outside the door shows whether a room is free, when that changes, and what's booked next.

Choose a calendar feed and the screens that show it. You can also add a booking endpoint, a URL that accepts bookings.
Tesserae creates a dashboard for the room, which you can edit like any other.

## What Tesserae does and doesn't own

Your calendar stores the bookings. Tesserae reads them and only writes to the calendar if you enable
[booking directly in the calendar](#booking-directly-in-the-calendar).

Bookings stay in your calendar even if Tesserae is down.
The calendar must handle conflicting requests.

## Setup

1. Add the room's calendar under **Settings → Widgets → Calendar Core**.
   Use any ICS URL, or find a CalDAV calendar through feed discovery.
   Supported ICS sources include Google Calendar, iCloud, and Outlook.

   Usually, each room has its own calendar.

2. Install the Room Status widget from **Widgets**, if needed.
3. Open **Settings → Rooms** and add the room. Enter its name, calendar feed, and the screens that show it.

The dashboard is created and assigned to the screens. They pick it up at their next check-in.

### One calendar, several rooms

If rooms share one calendar, set **Room name in the calendar**.
Tesserae matches the event's `LOCATION` field, where Exchange and Google put the room.
Matching ignores case and accepts part of a name, so `Kestrel` matches `Level 3 / Kestrel (8 seats)`.

Events without a location are excluded when this filter is on.

## The board

In **Settings → Rooms**, build a board to show one row per room on a lobby or corridor screen.
Assign it to devices and schedule it like any dashboard.

Rebuild it after adding or removing rooms. Its layout uses the room count at the time it was built.
Booking is disabled on boards to prevent accidental bookings.

## Booking from the panel

To book through an endpoint, you need a server you control.
Open **Booking** on the room's row and choose **Post to a booking endpoint**.
Enter the endpoint URL.

The screen shows a booking button. Tapping it sends a POST request and updates the screen a few seconds later.

Choose one booking method per room: not bookable, post to an endpoint, or write into the calendar.

An endpoint URL and a calendar write cannot both be configured.

### What your endpoint receives

A POST request with a JSON body:

```json
{
  "device_id": "kestrel_door",
  "button": "touch",
  "action_spec": "webhook_refresh:https://your-server/book?room=kestrel",
  "timestamp": "2026-08-21T14:12:00+00:00"
}
```

The room id is in the URL:

```
POST https://your-server/book?room=kestrel
```

Read the room id from the URL to use one endpoint for several rooms.
The body identifies the device. Tesserae doesn't read your endpoint's response body.

Your endpoint must handle who can book, how long bookings last, and conflicting bookings.

### Timing

Tesserae doesn't wait for confirmation that the booking finished.
It waits 5 seconds before repainting so it doesn't show the old state.
Increase `button_webhook_refresh_delay_s` in app settings if your server needs longer.

If you control the receiving server, have it call
[`POST /api/v1/push`](server.md#webhook-push) once the booking is saved.
Then the screen updates when the booking finishes, without a fixed delay.

## Booking directly in the calendar

For CalDAV calendars with a username and password, Tesserae can write bookings directly.
You don't need a booking endpoint.

Open **Booking** on the room's row and choose **Write straight into this calendar**.
A tap creates an event in that calendar and repaints the screen.

If the feed doesn't allow writes, the option is disabled and shows the feed's name and the reason.
An `.ics` URL is always read-only, even with credentials.

Requirements:

- Add the feed through **CalDAV discovery**. Pasted ICS export URLs cannot accept writes; they fail with "the calendar refused a write".
- Set a username and password on the feed. Basic and digest authentication work with Radicale, Baikal, Nextcloud, and iCloud app-specific passwords.
- Google Calendar and Microsoft 365 are not supported for direct booking. They need OAuth authorization through a registered app. Use a booking endpoint with your server's credentials for those.

Tesserae sends `If-None-Match: *` to prevent overwriting an existing event.
A collision fails instead of replacing the meeting.

Set the booking length and title on the room.
Tesserae doesn't check whether the slot is free first; the calendar server must handle conflicting requests.
Rejected writes are reported.

## Wake timing

Tesserae wakes the screen and redraws it when a scheduled meeting starts or ends.
A screen with a 4 to 6 hour backup interval can still update within seconds of those changes.

That interval also controls how long you might wait for an unexpected change.
Choose it based on how quickly you need to see new bookings from systems that don't call Tesserae back.

## Removing a room

When removing a room, leave the dashboard option unchecked if you want to keep your edits.
Tesserae only deletes dashboards it generated. A dashboard you made yourself is always kept.
