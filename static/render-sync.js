// Keeps a device's thumbnail, tiles and Queued line current.
//
// Settings > Devices and the device page are server-rendered, so a push (or
// the frame fetching it) after the page loaded would leave the old image on
// screen until a manual reload. This polls the per-device render signature
// and, for a device whose signature moved, swaps in the freshly rendered
// [data-render-sync] regions (thumbnail, tiles) from the same page. The rest
// of the page is left alone, so an open row stays open and nothing typed is
// lost.
(function () {
  const BASE = (window.TESSERAE_URL_PREFIX || "") + "/settings/devices/";
  let busy = false;

  function regions() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-render-sync^='thumb:']"));
  }

  async function swap(id) {
    const resp = await fetch(window.location.href, { credentials: "same-origin" });
    if (!resp.ok) return;
    const fresh = new DOMParser().parseFromString(await resp.text(), "text/html");
    ["thumb:", "tiles:"].forEach((kind) => {
      const key = kind + id;
      const next = fresh.querySelector('[data-render-sync="' + key + '"]');
      const now = document.querySelector('[data-render-sync="' + key + '"]');
      if (next && now) now.replaceWith(document.importNode(next, true));
    });
  }

  async function poll() {
    // Looked up each time: this script can load before the list it serves.
    if (busy || document.hidden || !regions().length) return;
    busy = true;
    try {
      const resp = await fetch(BASE + "render-state.json", {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!resp.ok) return;
      const state = (await resp.json()).devices || {};
      for (const el of regions()) {
        const id = el.getAttribute("data-render-sync").slice("thumb:".length);
        if (id in state && state[id] !== el.getAttribute("data-render-sig")) await swap(id);
      }
    } catch {
      /* offline or restarting, try again next tick */
    } finally {
      busy = false;
    }
  }

  setInterval(poll, 5000);
  document.addEventListener("visibilitychange", poll);
  window.addEventListener("pageshow", poll);
})();
