/* Shared Chart.js setup for admin pages.
 *
 * Every admin chart reads the same design tokens off the document root so
 * it follows the theme toggle, and every one of them wants the same base
 * options (no animation, recessive grid, tabular ticks). This exists so
 * that stays in one place instead of being copy-pasted per page; the
 * battery page predates it and can adopt it next time it's touched.
 *
 * Loaded as a plain script after Chart.js UMD, so it publishes one global
 * rather than an ES module (admin templates are server-rendered and have
 * no bundler).
 *
 * The categorical colours are three hues plus a neutral for "other",
 * validated for colour-blind separation against both the light and dark
 * chart surfaces. Assignment is fixed per series name, never cycled: a
 * filter that drops a band must not repaint the survivors.
 */
window.TesseraeCharts = (function () {
  function token(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (value || fallback).trim();
  }

  function rgba(input, alpha) {
    var s = (input || "").trim();
    if (s.charAt(0) === "#") {
      var h = s.slice(1);
      if (h.length === 3) {
        h = h
          .split("")
          .map(function (c) {
            return c + c;
          })
          .join("");
      }
      if (h.length < 6) return "rgba(113,112,108," + alpha + ")";
      return (
        "rgba(" +
        parseInt(h.slice(0, 2), 16) +
        "," +
        parseInt(h.slice(2, 4), 16) +
        "," +
        parseInt(h.slice(4, 6), 16) +
        "," +
        alpha +
        ")"
      );
    }
    var m = s.match(/rgba?\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
    if (m) return "rgba(" + m[1] + "," + m[2] + "," + m[3] + "," + alpha + ")";
    return "rgba(113,112,108," + alpha + ")";
  }

  /* Series name -> colour. Unknown names fall to the neutral, which is
     also what "other" gets, so an unexpected band reads as unclassified
     rather than borrowing another series' identity. */
  function seriesColor(name) {
    var slot = {
      scheduled: "--t-chart-1",
      "by hand": "--t-chart-2",
      integrations: "--t-chart-3",
    }[name];
    return token(slot || "--t-chart-other", "#a1a09a");
  }

  function baseOptions() {
    var muted = token("--t-muted", "#71706c");
    var surface = token("--t-surface", "#ffffff");
    return {
      animation: false,
      maintainAspectRatio: false,
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        /* Identity lives in the page's own HTML legend, which can use
           text tokens for the labels rather than the series colour. */
        legend: { display: false },
        tooltip: {
          backgroundColor: token("--t-fg", "#18181b"),
          titleColor: surface,
          bodyColor: surface,
          padding: 10,
          cornerRadius: 6,
          displayColors: true,
          boxWidth: 8,
          boxHeight: 8,
          usePointStyle: true,
        },
      },
      scales: {
        x: {
          stacked: true,
          grid: { display: false },
          border: { color: rgba(muted, 0.35) },
          ticks: { color: muted, font: { size: 11 }, maxRotation: 0, autoSkipPadding: 24 },
        },
        y: {
          stacked: true,
          beginAtZero: true,
          grid: { color: rgba(muted, 0.16), drawTicks: false },
          border: { display: false },
          ticks: { color: muted, font: { size: 11 }, padding: 8, precision: 0 },
        },
      },
    };
  }

  return { token: token, rgba: rgba, seriesColor: seriesColor, baseOptions: baseOptions };
})();
