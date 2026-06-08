# Firmware fix: ``sleep_until`` is wrong on the heartbeat

## Symptom

A Tesserae server reports a per-device "Last offset" of several hundred
seconds (e.g. -307s) and the device stays stuck on "Smart sync:
warming" forever. The admin diagnostic on the device card shows:

- `SLEEP_UNTIL`: some absolute unix timestamp
- `NEXT_SLEEP_S`: e.g. 60
- `SLEEP_INTERVAL_S`: 60
- **Server-side "Sleep cycle"** (the value derived from `sleep_until -
  received_at`): wildly larger than `NEXT_SLEEP_S`.

Both values come from the same firmware, on the same heartbeat. They
should always agree. If they don't, the absolute timestamp is wrong.

## Why this happens

`sleep_until` is computed by the firmware as
`time(nullptr) + sleep_interval_s`. That's only correct if **NTP has
synced before the firmware reads `time(nullptr)`**. Common failure
modes that produce the wrong value:

1. **NTP hasn't synced yet** when `sleep_until` is computed.
   `time(nullptr)` returns boot-relative seconds (a very small number
   like `42`) or a hardcoded epoch (like 2016-01-01), so
   `sleep_until` becomes a value years in the wrong direction. The
   wire encoding still looks "valid" (it's a float / int), so the
   server happily accepts it.
2. **Stale value carried across cycles.** Firmware computed
   `sleep_until` once at boot and reused it on subsequent
   heartbeats, so the value is the moment-in-time of the **first**
   wake, not the upcoming one. The server's `sleep_until -
   received_at` math then produces a constant gap that drifts every
   cycle.
3. **Timezone-as-offset confusion.** Firmware adds a TZ offset to
   `time(nullptr)` thinking the server expects local-time. The
   server expects UTC unix seconds.
4. **Different field semantics.** Firmware publishes `sleep_until` as
   "wake time of the cycle AFTER this one" rather than "wake time of
   the upcoming sleep". Server expects the upcoming sleep.

In your case (-307s offset with `sleep_until - received_at = 371s`
when `next_sleep_s = 60`), the most likely cause is (1) or (2),
NTP-not-synced at compute time, or stale tracking. (3) and (4) are
worth ruling out by sanity-checking the value just before publishing.

## What to fix

`sleep_until` must satisfy this invariant on every heartbeat:

```
abs((sleep_until - heartbeat_publish_time) - next_sleep_s) < 5 seconds
```

Concrete steps:

1. **Compute `sleep_until` immediately before publishing the
   heartbeat**, not at boot or somewhere earlier in the wake cycle.
2. **Verify NTP synced before reading `time(nullptr)`** for the
   computation. Pseudocode:

   ```c
   // Wait for NTP, with a generous timeout.
   time_t now;
   for (int i = 0; i < 50; i++) {
       now = time(nullptr);
       if (now > 1700000000) break;  // Anything after 2023; gives a
                                     //  margin against stale/zero clocks.
       delay(100);
   }
   if (now < 1700000000) {
       // NTP never synced; DON'T publish sleep_until at all.
       // Publish next_sleep_s only; server will use that.
       publish_heartbeat_without_sleep_until();
   } else {
       time_t sleep_until = now + sleep_interval_s;
       publish_heartbeat(sleep_until, sleep_interval_s);
   }
   ```

3. **Cross-check before publish.** Sanity-test the computed value:

   ```c
   // sleep_until should land in [now+5, now+sleep_interval_s+5].
   // Anything outside that range means we got our math wrong; don't
   // ship a bad value to the server.
   if (sleep_until < (now + sleep_interval_s - 5) ||
       sleep_until > (now + sleep_interval_s + 5)) {
       // Bug; publish next_sleep_s only and skip sleep_until.
       publish_heartbeat_without_sleep_until();
   }
   ```

4. **Don't publish `sleep_until` at all if NTP isn't reliable on
   this device.** The Tesserae server falls back to `next_sleep_s`
   cleanly when `sleep_until` is missing. `next_sleep_s` is a
   duration so it doesn't depend on clock state, it just works.

## Acceptance criteria

A firmware build that fixes this should:

- [ ] On every heartbeat, `abs((sleep_until - heartbeat_publish_time) - next_sleep_s) < 5` seconds.
- [ ] If NTP hasn't synced, **omit** `sleep_until` from the payload
  (don't publish `0`, don't publish a stale value). `next_sleep_s`
  stays.
- [ ] No regression in WiFi / MQTT / paint timing.
- [ ] Smart sync confidence on the Tesserae server reaches "trusted"
  (3 consecutive on-time wakes within ±60s of prediction) within
  3-4 heartbeats of a fresh device powering on.

## Wire contract reminder

From the original handover prompt:

- `sleep_until`: absolute unix timestamp (seconds since epoch, UTC)
  the device intends to wake. Optional, omit if NTP isn't reliable.
- `next_sleep_s`: the duration of deep sleep about to begin, in
  whole seconds. Optional but **strongly recommended**, this is what
  the server actually uses when `sleep_until` is unreliable.
- `sleep_interval_s`: firmware's configured cycle. Currently
  informational on the server side (the admin UI surfaces it for
  debugging); not used by the smart-sync prediction.

If you can publish only one, publish `next_sleep_s`. It's robust
against clock skew and good enough for ±60s tolerance.

## Server-side fallback (already deployed)

Tesserae v0.43.1 ships a defensive check: if a heartbeat carries
both `sleep_until` and `next_sleep_s` and they disagree by more than
30 seconds, the server rejects `sleep_until` as untrustworthy and
falls back to `next_sleep_s` for the prediction. A `WARNING` log
line records the disagreement so the firmware bug stays
discoverable. This unblocks the device-side issue, but the firmware
fix is still wanted: it's the wrong value to be publishing.

## Repo

Server-side smart-sync: https://github.com/dmellok/tesserae
Issue tracker: https://github.com/dmellok/tesserae/issues
