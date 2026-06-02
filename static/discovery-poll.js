// Live device discovery.
//
// The Discovered strip (Settings -> Devices, and the setup wizard) is
// server-rendered, so a client that announces itself *after* the page
// loads wouldn't appear until a manual refresh. This polls the
// discovered-devices JSON and reloads the page when the set changes, so
// new devices (and their Register buttons, once a kind-bearing heartbeat
// lands) show up on their own.
//
// Guarded by a [data-discovered-poll] marker so it only runs where the
// strip is present. Skips reloading while the user is typing in a form
// (e.g. the manual Add-device fields) so we never clobber input.
(function () {
  const marker = document.querySelector("[data-discovered-poll]");
  if (!marker) return;
  const ENDPOINT = (window.TESSERAE_URL_PREFIX || "") + "/settings/devices/discovered.json";
  // Baseline = what the server actually rendered (carried in the marker),
  // NOT the first poll. Otherwise a device that announces itself between
  // page render and the first poll gets adopted silently and never shows.
  let seen = marker.getAttribute("data-discovered-poll") || "";

  function signature(list) {
    return (list || [])
      .map((d) => d.id + ":" + (d.kind || ""))
      .sort()
      .join(",");
  }

  function userIsTyping() {
    const el = document.activeElement;
    if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return true;
    return Array.prototype.some.call(
      document.querySelectorAll(
        "input[type=text], input[type=number], input:not([type])"
      ),
      (i) => i.value
    );
  }

  async function poll() {
    try {
      const resp = await fetch(ENDPOINT, { headers: { Accept: "application/json" } });
      if (!resp.ok) return;
      const sig = signature((await resp.json()).devices);
      if (sig !== seen && !userIsTyping()) {
        location.reload();
      }
    } catch (e) {
      /* broker/transport hiccup — try again next tick */
    }
  }

  setInterval(poll, 5000);
})();
