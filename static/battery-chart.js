/* Battery chart component (templates/_battery_chart.html).
 *
 * Draws every `.dx-battery-chartbox`: the battery line, a dot at each wake-up,
 * and a hatched band where the device sent no readings. Inputs come from the
 * canvas's data attributes, built by app/battery_chart.py:
 *
 *   data-series       [{t_ms, pct}]                      the line
 *   data-wakes        [{t_ms, pct, cat, label, detail, reason}]   wake-up dots
 *   data-gaps         [{start_ms, end_ms, kind}]         spans with no readings
 *   data-window-hours the x axis spans this many hours
 *
 * Range. The buttons above the chart (6h | 12h | 24h | 7d | 30d) change this chart
 * only: it fetches that range for its device from the box's data-url
 * (/devices/battery/<id>/card.json), redraws, and swaps the stat tiles, hint
 * and key for the new range. The choice is kept per device in localStorage; a
 * chart with none opens at the server's default (7 days). Dragging across the
 * plot zooms into that span (no refetch); "Reset zoom" or a double click undoes it.
 *
 * A dot's shape and colour say why the device woke (STYLES below; the legend
 * under the chart is drawn from the same table in dx-battery.css). Hovering the
 * line, a dot or a band shows a tooltip: the time and charge, the cause, and the
 * raw device reason in muted text; a band says "Asleep 23:02-09:33 (quiet
 * hours), no readings" or "No readings 14:10-16:40 (offline)". The hovered dot
 * grows and a vertical crosshair marks where it falls on the time axis. The
 * same tooltip opens from the keyboard: Tab to the chart, Left / Right step
 * through the wake-ups (or the samples when there are none), Home / End jump to
 * the ends, Escape closes it.
 *
 * A gap is never drawn as measured data: the line breaks, and a muted dashed
 * connector joins the readings either side across the hatched band.
 *
 * Colours come from the theme tokens on <html> (--t-*), read again whenever the
 * theme or UI design changes, so the chart reads in light and dark, Classic and
 * Paper, without per-theme code.
 *
 * Needs Chart.js (UMD) and static/admin-chart.js loaded first. Safe to load
 * twice, and to call `TesseraeBatteryChart.init(root)` after inserting new
 * cards; a box is only ever drawn once.
 */
(function () {
  "use strict";

  var DAY = 86400 * 1000;
  var HOUR = 3600 * 1000;
  var LINE = 0,
    WAKES = 1,
    BRIDGES = 2,
    GAPS = 3;
  var MIN_ZOOM_MS = 5 * 60 * 1000;
  var STORE_KEY = "tesserae.batteryRange.";

  /* Dot per cause. `tok` is a theme token; shape is the Chart.js pointStyle.
     Shape carries the meaning as much as colour does: Paper's accent, chart-2
     and danger are all red. Keep in step with .dx-wake-key in dx-battery.css
     and _wake_shape in _battery_chart.html. Every dot is ringed in the card's
     colour (wakeHalo below) so overlapping dots stay apart, and grows by 3px
     while it is the hovered / focused one. */
  var STYLES = {
    scheduled: { style: "circle", r: 2.6, hover: 5.6, tok: "--t-accent", filled: false, bw: 1.6 },
    lineup: { style: "circle", r: 3.8, hover: 6.8, tok: "--t-accent", filled: true, bw: 1.6 },
    ha: { style: "rectRot", r: 4.8, hover: 7.8, tok: "--t-chart-1", filled: true, bw: 1.6 },
    device: { style: "triangle", r: 4.8, hover: 7.8, tok: "--t-chart-2", filled: true, bw: 1.6 },
    manual: { style: "rect", r: 4, hover: 7, tok: "--t-chart-3", filled: true, bw: 1.6 },
    webhook: { style: "star", r: 5.2, hover: 8.2, tok: "--t-chart-other", filled: true, bw: 1.6 },
    // Reconnected: softer than a restart (hollow rounded square, warning).
    reconnect: { style: "rectRounded", r: 4, hover: 7, tok: "--t-warn", filled: false, bw: 2 },
    // Restarted or crashed: a cross, danger.
    problem: { style: "crossRot", r: 4.8, hover: 7.8, tok: "--t-danger", filled: false, bw: 2.8 },
    other: { style: "circle", r: 3.2, hover: 6.2, tok: "--t-chart-other", filled: true, bw: 1.6 },
  };

  function token(name, fallback) {
    if (window.TesseraeCharts) return window.TesseraeCharts.token(name, fallback);
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v || fallback).trim();
  }

  function rgba(color, alpha) {
    if (window.TesseraeCharts) return window.TesseraeCharts.rgba(color, alpha);
    return "rgba(80,100,140," + alpha + ")";
  }

  function readPalette() {
    var p = {
      muted: token("--t-muted", "#71706c"),
      fg: token("--t-fg", "#18181b"),
      accent: token("--t-accent", "#0d8c7e"),
      // Tooltip: a raised surface with a border, so it contrasts with the
      // card behind it in either theme.
      tipBg: token("--t-surface-raised", token("--t-surface", "#ffffff")),
      tipBorder: token("--t-border-strong", token("--t-border", "#d4d4d8")),
      // The card behind the chart: hollow dots and every dot's ring use it.
      dotFill: token("--t-inset", token("--t-surface", "#ffffff")),
      cat: {},
    };
    Object.keys(STYLES).forEach(function (cat) {
      p.cat[cat] = token(STYLES[cat].tok, p.accent);
    });
    return p;
  }

  var pal = readPalette();
  var hatchPattern = null;

  function fmtWhen(ms) {
    return window.tesseraeComponents.clockTime(ms, { date: true });
  }

  /* "23:02-09:33", with dates when the span is longer than a day. */
  function fmtSpan(gap) {
    var withDate = gap.end_ms - gap.start_ms > DAY;
    var c = window.tesseraeComponents.clockTime;
    return c(gap.start_ms, { date: withDate }) + "–" + c(gap.end_ms, { date: withDate });
  }

  function gapLabel(gap) {
    return gap.kind === "asleep"
      ? "Asleep " + fmtSpan(gap) + " (quiet hours), no readings"
      : "No readings " + fmtSpan(gap) + " (offline)";
  }

  /* A small diagonal-hatch tile for the no-readings band. */
  function hatch(color) {
    var tile = document.createElement("canvas");
    tile.width = tile.height = 8;
    var t = tile.getContext("2d");
    t.strokeStyle = rgba(color, 0.4);
    t.lineWidth = 1;
    t.beginPath();
    t.moveTo(-1, 9);
    t.lineTo(9, -1);
    t.stroke();
    return tile.getContext("2d").createPattern(tile, "repeat");
  }

  /* Tick spacing for the x axis, by how much time is on screen. */
  function pickStep(span) {
    return span <= 40 * 60e3
      ? 5 * 60e3
      : span <= 2 * HOUR
        ? 15 * 60e3
        : span <= 8 * HOUR
          ? HOUR
          : span <= 14 * HOUR
            ? 2 * HOUR
            : span <= 1.5 * DAY
              ? 4 * HOUR
              : span <= 3 * DAY
                ? DAY
                : span <= 14 * DAY
                  ? 2 * DAY
                  : span <= 30 * DAY
                    ? 5 * DAY
                    : 14 * DAY;
  }

  function storedRange(id) {
    try {
      var v = parseFloat(window.localStorage.getItem(STORE_KEY + id));
      return v > 0 ? v : null;
    } catch {
      return null;
    }
  }

  function storeRange(id, hours) {
    try {
      window.localStorage.setItem(STORE_KEY + id, String(hours));
    } catch {
      /* private mode */
    }
  }

  /* Hover target for every chart (the mode is global, the state is the
     chart's own, on chart.$battery): inside a gap, the gap; else the closest
     wake dot or line point by x (a dot wins a tie, so hovering one never reads
     as the line). Own mode because the stock "nearest" would also pick the
     bridge points. */
  if (typeof Chart !== "undefined") {
    Chart.Interaction.modes.batteryChart = function (chart, e) {
      var st = chart.$battery;
      if (!st) return [];
      var value = chart.scales.x.getValueForPixel(e.x);
      for (var gi = 0; gi < st.gaps().length; gi++) {
        var g = st.gaps()[gi];
        if (value > g.start_ms && value < g.end_ms) {
          var anchor = chart.getDatasetMeta(GAPS).data[gi];
          if (anchor) return [{ element: anchor, datasetIndex: GAPS, index: gi }];
        }
      }
      var best = null,
        bestD = Infinity;
      [WAKES, LINE].forEach(function (di) {
        var data = chart.getDatasetMeta(di).data;
        var raw = chart.data.datasets[di].data;
        for (var i = 0; i < data.length; i++) {
          if (!raw[i] || raw[i].y === null) continue;
          var d = Math.abs(data[i].x - e.x);
          if (d < bestD) {
            bestD = d;
            best = { element: data[i], datasetIndex: di, index: i };
          }
        }
      });
      return best ? [best] : [];
    };
  }

  var boxes = [];

  function initBox(box) {
    if (box.dataset.batteryBound === "1") return;
    var canvas = box.querySelector("canvas.battery-chart");
    if (!canvas || typeof Chart === "undefined") return;
    box.dataset.batteryBound = "1";

    var deviceId = box.dataset.batteryChart;
    var url = box.dataset.url;
    var live = box.querySelector("[data-battery-chart-live]");
    var resetBtn = box.querySelector("[data-battery-zoom-reset]");
    var rangeBtns = box.querySelectorAll(".dx-battery-range button");

    var data = { series: [], wakes: [], gaps: [], windowHours: 168 };
    var chart = null;
    var full = { min: 0, max: 1 }; // the whole window
    var view = { min: 0, max: 1 }; // what is on screen (zoomed or not)
    var shown = []; // wakes inside the view
    var linePoints = [],
      bridges = [],
      anchors = [],
      nav = [];
    var drag = null,
      cursor = -1;
    var loading = 0;

    function readAttrs() {
      try {
        data.series = JSON.parse(canvas.dataset.series || "[]");
        data.wakes = JSON.parse(canvas.dataset.wakes || "[]");
        data.gaps = JSON.parse(canvas.dataset.gaps || "[]");
      } catch {
        data.series = [];
        data.wakes = [];
        data.gaps = [];
      }
      data.windowHours = parseFloat(box.dataset.windowHours) || 168;
    }

    function layoutFull() {
      var now = Date.now();
      var windowMs = data.windowHours * HOUR;
      var pad = Math.min(DAY * 0.25, windowMs * 0.1);
      var first = data.series.length ? data.series[0].t_ms : now - windowMs;
      full = { min: Math.max(now - windowMs, first - pad), max: now };
      view = { min: full.min, max: full.max };
    }

    function style(i) {
      return STYLES[(shown[i] || {}).cat] || STYLES.other;
    }
    function wakeAt(ms) {
      for (var i = 0; i < shown.length; i++) {
        if (Math.abs(shown[i].t_ms - ms) < 1000) return shown[i];
      }
      return null;
    }

    // The line breaks (a null point) wherever a gap separates two samples,
    // and a dashed bridge joins the real readings either side of it. Gap
    // anchors are invisible points mid-gap that hover resolves to.
    function prepare() {
      var series = data.series,
        gaps = data.gaps;
      var gapStarts = {};
      gaps.forEach(function (g, i) {
        gapStarts[g.start_ms] = i;
      });
      linePoints = [];
      bridges = [];
      var endPcts = [];
      var visibleIdx = [];
      series.forEach(function (p, i) {
        if (p.t_ms >= view.min && p.t_ms <= view.max) visibleIdx.push([i, linePoints.length]);
        linePoints.push({ x: p.t_ms, y: p.pct });
        var gi = gapStarts[p.t_ms];
        if (gi === undefined) return;
        var g = gaps[gi],
          next = series[i + 1];
        endPcts[gi] = next ? (p.pct + next.pct) / 2 : p.pct;
        if (next) {
          linePoints.push({ x: (g.start_ms + g.end_ms) / 2, y: null });
          bridges.push(
            { x: g.start_ms, y: p.pct },
            { x: g.end_ms, y: next.pct },
            { x: g.end_ms, y: null }
          );
        }
      });
      anchors = gaps.map(function (g, gi) {
        var lo = Math.max(g.start_ms, view.min),
          hi = Math.min(g.end_ms, view.max);
        var mid = hi > lo ? (lo + hi) / 2 : (g.start_ms + g.end_ms) / 2;
        var y =
          endPcts[gi] !== undefined
            ? endPcts[gi]
            : series.length
              ? series[series.length - 1].pct
              : 0;
        return { x: mid, y: y };
      });
      shown = data.wakes.filter(function (w) {
        return w.t_ms >= view.min && w.t_ms <= view.max;
      });
      nav = shown.length
        ? shown.map(function (w, i) {
            return {
              ds: WAKES,
              index: i,
              when: w.t_ms,
              pct: w.pct,
              label: w.label,
              detail: w.detail,
            };
          })
        : visibleIdx.map(function (pair) {
            var p = series[pair[0]];
            return { ds: LINE, index: pair[1], when: p.t_ms, pct: p.pct };
          });
      cursor = -1;
    }

    function pushData() {
      var ds = chart.data.datasets;
      ds[LINE].data = linePoints;
      ds[WAKES].data = shown.map(function (w) {
        return { x: w.t_ms, y: w.pct };
      });
      ds[BRIDGES].data = bridges;
      ds[GAPS].data = anchors;
      var xs = chart.options.scales.x;
      xs.min = view.min;
      xs.max = view.max;
      xs.ticks.stepSize = pickStep(view.max - view.min);
      chart.setActiveElements([]);
      chart.tooltip.setActiveElements([], { x: 0, y: 0 });
      chart.update("none");
      if (resetBtn) resetBtn.hidden = view.min === full.min && view.max === full.max;
      if (live) live.textContent = "";
    }

    function setView(min, max) {
      view = { min: min, max: max };
      prepare();
      pushData();
    }

    // -- chart.js plugins ---------------------------------------------------

    var hatchBands = {
      id: "batteryGapBands",
      beforeDatasetsDraw: function (c) {
        var area = c.chartArea;
        if (!area || !data.gaps.length) return;
        var ctx = c.ctx,
          xs = c.scales.x;
        ctx.save();
        data.gaps.forEach(function (g) {
          var x0 = Math.max(area.left, xs.getPixelForValue(g.start_ms));
          var x1 = Math.min(area.right, xs.getPixelForValue(g.end_ms));
          if (x1 <= x0) return;
          ctx.fillStyle = rgba(pal.muted, 0.07);
          ctx.fillRect(x0, area.top, x1 - x0, area.bottom - area.top);
          ctx.fillStyle = hatchPattern;
          ctx.fillRect(x0, area.top, x1 - x0, area.bottom - area.top);
        });
        ctx.restore();
      },
    };

    // A vertical line at the hovered / focused point, bottom to top of the
    // plot, to read where it falls on the time axis.
    var crosshair = {
      id: "batteryCrosshair",
      beforeDatasetsDraw: function (c) {
        var area = c.chartArea;
        var active = c.tooltip && c.tooltip.getActiveElements();
        delete canvas.dataset.crosshairX;
        if (!area || !active || !active.length) return;
        var x = active[0].element.x;
        if (x < area.left || x > area.right) return;
        canvas.dataset.crosshairX = String(Math.round(x)); // read by the browser test
        var ctx = c.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(x, area.bottom);
        ctx.lineTo(x, area.top);
        ctx.lineWidth = 1;
        ctx.strokeStyle = rgba(pal.muted, 0.7);
        ctx.stroke();
        ctx.restore();
      },
    };

    // A ring in the card's colour under each dot: where dots overlap, or sit
    // on the line, the ring keeps each one's outline readable.
    var halo = {
      id: "batteryWakeHalo",
      beforeDatasetDraw: function (c, args) {
        if (args.index !== WAKES) return;
        var ctx = c.ctx;
        var activeIdx = {};
        c.getActiveElements().forEach(function (a) {
          if (a.datasetIndex === WAKES) activeIdx[a.index] = true;
        });
        args.meta.data.forEach(function (el, i) {
          var st = style(i);
          var r = (activeIdx[i] ? st.hover : st.r) + st.bw / 2 + 1.8;
          ctx.save();
          ctx.fillStyle = pal.dotFill;
          Chart.helpers.drawPoint(
            ctx,
            { pointStyle: st.style, rotation: 0, radius: r, borderWidth: 0 },
            el.x,
            el.y
          );
          ctx.restore();
        });
      },
    };

    // The rubber band while dragging to zoom.
    var selection = {
      id: "batteryZoomSelection",
      afterDatasetsDraw: function (c) {
        if (!drag || Math.abs(drag.x1 - drag.x0) < 2) return;
        var area = c.chartArea,
          ctx = c.ctx;
        var x0 = Math.min(drag.x0, drag.x1),
          x1 = Math.max(drag.x0, drag.x1);
        ctx.save();
        ctx.fillStyle = rgba(pal.accent, 0.14);
        ctx.fillRect(x0, area.top, x1 - x0, area.bottom - area.top);
        ctx.strokeStyle = rgba(pal.accent, 0.7);
        ctx.lineWidth = 1;
        ctx.strokeRect(x0 + 0.5, area.top + 0.5, x1 - x0 - 1, area.bottom - area.top - 1);
        ctx.restore();
      },
    };

    function createChart() {
      hatchPattern = hatch(pal.muted);
      chart = new Chart(canvas.getContext("2d"), {
        type: "line",
        plugins: [hatchBands, crosshair, halo, selection],
        data: {
          datasets: [
            {
              label: "Battery",
              data: linePoints,
              spanGaps: false,
              clip: 8,
              borderColor: pal.accent,
              backgroundColor: function (ctx) {
                var area = ctx.chart.chartArea;
                if (!area) return "transparent";
                var g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
                g.addColorStop(0, rgba(pal.accent, 0.22));
                g.addColorStop(0.7, rgba(pal.accent, 0.04));
                g.addColorStop(1, rgba(pal.accent, 0));
                return g;
              },
              tension: 0.25,
              pointRadius: 0,
              pointHoverRadius: 5,
              pointHoverBackgroundColor: pal.accent,
              pointHoverBorderColor: pal.accent,
              borderWidth: 2,
              fill: "origin",
            },
            {
              label: "Wake-ups",
              data: [],
              showLine: false,
              clip: false, // a dot at 100% or at the window edge is not cut in half
              order: -1, // drawn above the line
              pointStyle: function (ctx) {
                return style(ctx.dataIndex).style;
              },
              pointRadius: function (ctx) {
                return style(ctx.dataIndex).r;
              },
              pointHoverRadius: function (ctx) {
                return style(ctx.dataIndex).hover;
              },
              pointBorderWidth: function (ctx) {
                return style(ctx.dataIndex).bw;
              },
              pointHoverBorderWidth: function (ctx) {
                return style(ctx.dataIndex).bw;
              },
              pointBackgroundColor: function (ctx) {
                var st = style(ctx.dataIndex);
                return st.filled
                  ? pal.cat[(shown[ctx.dataIndex] || {}).cat] || pal.accent
                  : pal.dotFill;
              },
              pointBorderColor: function (ctx) {
                return pal.cat[(shown[ctx.dataIndex] || {}).cat] || pal.accent;
              },
              pointHoverBackgroundColor: function (ctx) {
                return pal.cat[(shown[ctx.dataIndex] || {}).cat] || pal.accent;
              },
              pointHoverBorderColor: function (ctx) {
                return pal.cat[(shown[ctx.dataIndex] || {}).cat] || pal.accent;
              },
            },
            {
              label: "No readings",
              data: bridges,
              spanGaps: false,
              borderColor: rgba(pal.muted, 0.85),
              borderDash: [3, 4],
              borderWidth: 1.5,
              fill: false,
              pointRadius: 0,
              pointHoverRadius: 0,
              pointHitRadius: 0,
              tension: 0,
            },
            {
              label: "Gap anchors",
              data: anchors,
              showLine: false,
              pointRadius: 0,
              pointHoverRadius: 0,
              pointHitRadius: 0,
            },
          ],
        },
        options: {
          animation: false,
          maintainAspectRatio: false,
          responsive: true,
          layout: { padding: { top: 6, right: 12, bottom: 0, left: 10 } },
          interaction: { mode: "batteryChart", intersect: false },
          scales: {
            x: {
              type: "linear",
              min: view.min,
              max: view.max,
              ticks: {
                stepSize: pickStep(view.max - view.min),
                color: pal.muted,
                font: { size: 11 },
                padding: 6,
                callback: function (v) {
                  var d = new Date(v);
                  if (view.max - view.min <= 1.5 * DAY)
                    return window.tesseraeComponents.clockTime(d);
                  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
                },
              },
              grid: { display: false },
              border: { color: rgba(pal.muted, 0.35) },
            },
            y: {
              // The axis reads 0-100%, with room above and below so a dot at
              // either end is whole.
              min: -5,
              max: 105,
              afterBuildTicks: function (axis) {
                axis.ticks = [0, 25, 50, 75, 100].map(function (v) {
                  return { value: v };
                });
              },
              ticks: {
                color: pal.muted,
                font: { size: 11 },
                padding: 6,
                callback: function (v) {
                  return v + "%";
                },
              },
              grid: { color: rgba(pal.muted, 0.1), drawTicks: false },
              border: { display: false },
            },
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: pal.tipBg,
              borderColor: pal.tipBorder,
              borderWidth: 1,
              titleColor: pal.muted,
              bodyColor: pal.fg,
              footerColor: pal.muted,
              displayColors: false,
              cornerRadius: 6,
              padding: 8,
              titleFont: { size: 11, weight: "normal" },
              bodyFont: { size: 13, weight: "bold" },
              footerFont: { size: 11, weight: "normal" },
              callbacks: {
                title: function (items) {
                  if (!items.length || items[0].datasetIndex === GAPS) return "";
                  return fmtWhen(items[0].parsed.x);
                },
                label: function (item) {
                  if (item.datasetIndex === GAPS) return gapLabel(data.gaps[item.dataIndex]);
                  var lines = [item.parsed.y + "%"];
                  var wake =
                    item.datasetIndex === WAKES ? shown[item.dataIndex] : wakeAt(item.parsed.x);
                  if (wake) {
                    lines.push(wake.label);
                    if (wake.detail) lines.push(wake.detail);
                  }
                  return lines;
                },
                footer: function (items) {
                  if (!items.length || items[0].datasetIndex === GAPS) return "";
                  var item = items[0];
                  var wake =
                    item.datasetIndex === WAKES ? shown[item.dataIndex] : wakeAt(item.parsed.x);
                  return wake && wake.reason ? "Device reported: " + wake.reason : "";
                },
              },
            },
          },
        },
      });
      chart.$battery = {
        gaps: function () {
          return data.gaps;
        },
      };
      pushData();
    }

    function restyle() {
      if (!chart) return;
      hatchPattern = hatch(pal.muted);
      var ds = chart.data.datasets;
      ds[LINE].borderColor = pal.accent;
      ds[LINE].pointHoverBackgroundColor = pal.accent;
      ds[LINE].pointHoverBorderColor = pal.accent;
      ds[BRIDGES].borderColor = rgba(pal.muted, 0.85);
      var o = chart.options;
      o.scales.x.ticks.color = pal.muted;
      o.scales.x.border.color = rgba(pal.muted, 0.35);
      o.scales.y.ticks.color = pal.muted;
      o.scales.y.grid.color = rgba(pal.muted, 0.1);
      var tip = o.plugins.tooltip;
      tip.backgroundColor = pal.tipBg;
      tip.borderColor = pal.tipBorder;
      tip.titleColor = pal.muted;
      tip.bodyColor = pal.fg;
      tip.footerColor = pal.muted;
      chart.update("none");
    }

    // -- load data into the chart -------------------------------------------

    function apply() {
      var any = data.series.length > 0;
      var wrap = canvas.parentElement;
      var empty = box.querySelector("[data-battery-empty]");
      wrap.hidden = !any;
      if (empty) empty.hidden = any;
      layoutFull();
      prepare();
      if (any && !chart) createChart();
      else if (chart) pushData();
    }

    function syncAttrs(card) {
      canvas.dataset.series = JSON.stringify(data.series);
      canvas.dataset.wakes = JSON.stringify(data.wakes);
      canvas.dataset.gaps = JSON.stringify(data.gaps);
      canvas.dataset.windowHours = String(data.windowHours);
      box.dataset.windowHours = String(data.windowHours);
      canvas.setAttribute(
        "aria-label",
        "Battery percent over the last " +
          card.window_label +
          (data.wakes.length
            ? ", with a dot at each wake-up. Use the left and right arrow keys to step through them. Drag across the chart to zoom."
            : "")
      );
    }

    function swapParts(card) {
      var legend = box.querySelector("[data-battery-legend]");
      if (legend) legend.innerHTML = card.legend_html || "";
      var grid = document.querySelector('[data-battery-stats="' + deviceId + '"]');
      if (!grid) return;
      var old = grid.querySelectorAll("[data-battery-tile]");
      var t = document.createElement("template");
      t.innerHTML = card.tiles_html || "";
      grid.insertBefore(t.content, old.length ? old[0] : grid.firstChild);
      old.forEach(function (n) {
        n.remove();
      });
      var next = grid.nextElementSibling;
      if (next && next.hasAttribute("data-battery-hint")) next.remove();
      if (card.hint_html) grid.insertAdjacentHTML("afterend", card.hint_html);
    }

    function markPressed(hours) {
      rangeBtns.forEach(function (b) {
        b.setAttribute(
          "aria-pressed",
          Math.abs(parseFloat(b.dataset.hours) - hours) < 0.01 ? "true" : "false"
        );
      });
    }

    function load(hours) {
      var token = ++loading;
      box.setAttribute("aria-busy", "true");
      return fetch(url + "?hours=" + encodeURIComponent(hours), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(function (r) {
          if (!r.ok) throw new Error(String(r.status));
          return r.json();
        })
        .then(function (card) {
          if (token !== loading) return; // a newer choice is on its way
          data = {
            series: card.series || [],
            wakes: card.wakes || [],
            gaps: card.gaps || [],
            windowHours: card.window_hours,
          };
          syncAttrs(card);
          apply();
          swapParts(card);
          markPressed(card.window_hours);
        })
        .catch(function () {
          markPressed(data.windowHours); // keep showing what is drawn
        })
        .then(function () {
          if (token === loading) box.removeAttribute("aria-busy");
        });
    }

    rangeBtns.forEach(function (b) {
      b.addEventListener("click", function () {
        var hours = parseFloat(b.dataset.hours);
        if (!(hours > 0)) return;
        storeRange(deviceId, hours);
        markPressed(hours);
        load(hours);
      });
    });

    // -- drag to zoom -------------------------------------------------------

    function inPlot(x, y) {
      var a = chart && chart.chartArea;
      return !!a && x >= a.left && x <= a.right && y >= a.top && y <= a.bottom;
    }
    canvas.addEventListener("pointerdown", function (e) {
      if (!chart || e.button > 0 || !inPlot(e.offsetX, e.offsetY)) return;
      drag = { x0: e.offsetX, x1: e.offsetX };
      try {
        canvas.setPointerCapture(e.pointerId);
      } catch {
        /* not capturable */
      }
    });
    canvas.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var a = chart.chartArea;
      drag.x1 = Math.max(a.left, Math.min(a.right, e.offsetX));
      chart.setActiveElements([]);
      chart.tooltip.setActiveElements([], { x: 0, y: 0 });
      chart.draw();
    });
    function endDrag(commit) {
      if (!drag) return;
      var d = drag;
      drag = null;
      if (commit && Math.abs(d.x1 - d.x0) >= 8) {
        var xs = chart.scales.x;
        var a = xs.getValueForPixel(Math.min(d.x0, d.x1));
        var b = xs.getValueForPixel(Math.max(d.x0, d.x1));
        if (b - a >= MIN_ZOOM_MS) {
          setView(a, b);
          return;
        }
      }
      chart.draw();
    }
    canvas.addEventListener("pointerup", function () {
      endDrag(true);
    });
    canvas.addEventListener("pointercancel", function () {
      endDrag(false);
    });
    function resetZoom() {
      if (view.min === full.min && view.max === full.max) return;
      setView(full.min, full.max);
    }
    canvas.addEventListener("dblclick", resetZoom);
    if (resetBtn) resetBtn.addEventListener("click", resetZoom);

    // -- keyboard: arrows walk the wake-ups (or the samples) ----------------

    function show(i) {
      if (!chart || !nav.length) return;
      cursor = Math.max(0, Math.min(nav.length - 1, i));
      var n = nav[cursor];
      var el = chart.getDatasetMeta(n.ds).data[n.index];
      if (!el) return;
      var active = [{ datasetIndex: n.ds, index: n.index }];
      chart.setActiveElements(active);
      chart.tooltip.setActiveElements(active, { x: el.x, y: el.y });
      chart.update("none");
      if (live) {
        live.textContent =
          fmtWhen(n.when) +
          ", " +
          n.pct +
          "%" +
          (n.label ? ", " + n.label + (n.detail ? ", " + n.detail : "") : "");
      }
    }
    function hide() {
      cursor = -1;
      if (!chart) return;
      chart.setActiveElements([]);
      chart.tooltip.setActiveElements([], { x: 0, y: 0 });
      chart.update("none");
      if (live) live.textContent = "";
    }
    canvas.addEventListener("focus", function () {
      // A pointer press focuses too; only open the tooltip for keyboard focus.
      if (canvas.matches(":focus-visible")) show(nav.length - 1);
    });
    canvas.addEventListener("blur", hide);
    canvas.addEventListener("keydown", function (e) {
      var next = null;
      if (e.key === "ArrowLeft") next = cursor < 0 ? nav.length - 1 : cursor - 1;
      else if (e.key === "ArrowRight") next = cursor < 0 ? nav.length - 1 : cursor + 1;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = nav.length - 1;
      else if (e.key === "Escape") {
        hide();
        return;
      }
      if (next === null) return;
      e.preventDefault();
      show(next);
    });

    // -- go -----------------------------------------------------------------

    readAttrs();
    apply();
    boxes.push({ restyle: restyle });
    // A remembered range for this device replaces the server's default one.
    var stored = storedRange(deviceId);
    if (stored && Math.abs(stored - data.windowHours) > 0.01) {
      markPressed(stored);
      load(stored);
    }
  }

  // One observer for every chart on the page.
  var observing = false;
  var pending = false;
  function watchTheme() {
    if (observing) return;
    observing = true;
    new MutationObserver(function () {
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () {
        pending = false;
        pal = readPalette();
        boxes.forEach(function (b) {
          b.restyle();
        });
      });
    }).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "data-ui", "data-server-colour", "class", "style"],
    });
  }

  function init(root) {
    pal = readPalette();
    (root || document).querySelectorAll(".dx-battery-chartbox").forEach(initBox);
    watchTheme();
  }

  window.TesseraeBatteryChart = { init: init };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      init();
    });
  } else {
    init();
  }
})();
