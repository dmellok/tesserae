// Live countdown for each active rotation. The template renders a
// [data-rot-countdown] block per rotation with three data attrs:
//
//   data-step-started      epoch seconds when the current step began
//   data-next-transition   epoch seconds when the next step starts
//   data-dwell-seconds     length of the current step's dwell window
//
// We tick every second, paint the fill, write "Xm Ys" remaining, and
// when the countdown crosses zero we soft-reload the section so the
// server can recompute the current step (the scheduler tick is on a
// 30s cadence so we can't just rely on the client knowing the order).
(function () {
  "use strict";

  function format(seconds) {
    if (seconds < 0) seconds = 0;
    if (seconds < 60) return Math.round(seconds) + "s";
    const minutes = Math.floor(seconds / 60);
    const rem = Math.round(seconds % 60);
    if (minutes < 60) return minutes + "m " + (rem < 10 ? "0" : "") + rem + "s";
    const hours = Math.floor(minutes / 60);
    const mrem = minutes % 60;
    return hours + "h " + (mrem < 10 ? "0" : "") + mrem + "m";
  }

  function update(node) {
    const start = parseFloat(node.getAttribute("data-step-started")) * 1000;
    const end = parseFloat(node.getAttribute("data-next-transition")) * 1000;
    if (!start || !end) return;
    const now = Date.now();
    const total = end - start;
    const elapsed = Math.max(0, now - start);
    const remaining = Math.max(0, end - now);
    const pct = total > 0 ? Math.min(100, (elapsed / total) * 100) : 100;
    const fill = node.querySelector("[data-rot-countdown-fill]");
    if (fill) fill.style.width = pct.toFixed(2) + "%";
    const remainingNode = node.querySelector("[data-rot-countdown-remaining]");
    if (remainingNode) remainingNode.textContent = format(remaining / 1000);
    if (remaining <= 0) {
      // Soft reload the listing so the server's recompute drives the
      // next step's bar. Guarded by a flag so the same expiry doesn't
      // trigger multiple reloads if the timer fires twice before nav.
      if (!node.dataset.reloading) {
        node.dataset.reloading = "1";
        // Brief 700ms hold so the bar visibly snaps to full before
        // the page swaps; avoids a jumpy "100% -> 0%" repaint on the
        // new page load.
        setTimeout(() => window.location.reload(), 700);
      }
    }
  }

  function tick() {
    document.querySelectorAll("[data-rot-countdown]").forEach(update);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      tick();
      setInterval(tick, 1000);
    });
  } else {
    tick();
    setInterval(tick, 1000);
  }
})();
