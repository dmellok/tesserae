# Rooms

Point a panel at a room's calendar and it shows whether the room is free,
when that changes, and what's booked next.

A room names a calendar feed, the panels showing it, and optionally an
endpoint that books it. From that Tesserae generates an ordinary
dashboard, bound to the panel, which you can open and edit like any
other.

## What Tesserae does and doesn't own

Tesserae **displays** room status. It never writes to your calendar and
never stores a booking.

That's deliberate. If Tesserae owned the bookings, then Tesserae being
down would mean nobody could book a room, and two people booking the same
slot from two panels would be a problem it had to solve. Instead your
calendar stays the source of truth, and Tesserae is one more thing
reading it.

## Setup

1. **Add the room's calendar.** Widgets → Calendar Feeds. Any ICS URL
   works, which covers Google Calendar, iCloud and Outlook; CalDAV works
   through the feed discovery. The usual arrangement is one resource
   calendar per room, which is how Exchange, Google Workspace and
   Microsoft 365 model rooms.
2. **Install the Room Status widget** from Widgets, if it isn't already.
3. **Add the room.** Settings → Rooms. Name it, pick its feed, pick the
   panels showing it.

That's it. The dashboard is generated and bound; the panel picks it up on
its next poll.

### One calendar, several rooms

If your rooms share a single calendar rather than having one each, set
**Room name in the calendar**. Events are matched on their `LOCATION`
field, which is where Exchange and Google put the room. Matching is
case-insensitive and partial, so `Kestrel` matches
`Level 3 / Kestrel (8 seats)`.

An event with no location is **excluded** from a filtered room. A room
using the filter has opted into "only events tagged for me", and an
untagged event could belong to any room; assuming it belongs to this one
would show a free room as busy.

## The board

Settings → Rooms can build a **board**: one dashboard with a row per
room, for a lobby or corridor display. It's a normal dashboard, so bind
and schedule it like any other.

Rebuild it after adding or removing a room. The rows are laid out for the
number of rooms enabled when it was built.

Booking is off on a board, because a tap would book whichever room the
finger happened to land on.

## Booking from the panel

Optional, and it needs a server you control.

Set **Booking endpoint** on the room. The panel then shows a book button,
and a tap POSTs to your endpoint and repaints a few seconds later so the
panel shows the room it just booked.

### What your endpoint receives

A POST with a JSON body:

```json
{
  "device_id": "kestrel_door",
  "button": "touch",
  "action_spec": "webhook_refresh:https://your-server/book?room=kestrel",
  "timestamp": "2026-08-21T14:12:00+00:00"
}
```

and the room id in the query string:

```
POST https://your-server/book?room=kestrel
```

The room id is in the URL rather than the body because the payload
carries the device, not the room. One endpoint can serve every room by
reading that parameter.

Your endpoint creates the booking however it likes and returns; Tesserae
doesn't read the response body. Anything you'd want to enforce (who may
book, how long, double-booking) belongs there, where you have the
identity and the calendar.

### Timing

The POST is fire-and-forget with no acknowledgement, so Tesserae waits
before repainting rather than re-rendering immediately and reading your
system's *pre-booking* state. The default wait is 5 seconds; set
`button_webhook_refresh_delay_s` in app settings if your system is
slower.

**If you control the receiving server, there's a better option.** Have it
call [`POST /api/v1/push`](server.md#webhook-push) once the booking
commits. That fires when the work is actually done instead of after a
fixed delay, so there's no race at all and no delay to tune.

## Wake timing

A room panel is only as current as its last wake, and a panel that sleeps
through 15:00 shows a meeting that has ended.

The widget tells the server when the room next changes state, and the
server both wakes the panel then and re-renders the frame that change
invalidated. So a door panel can sit on a long backstop interval, 4 to 6
hours, and still be right within seconds of the transition.

Set the interval to how long you'd tolerate waiting for something the
calendar *can't* predict, like a booking made seconds ago through a
system that doesn't call back.

## Removing a room

Removing a room offers to remove its dashboard too. Leave that unchecked
to keep the dashboard, which is what you want if you've built on it.

Tesserae only ever deletes a dashboard it generated. A room pointed at a
hand-made page leaves that page alone.
