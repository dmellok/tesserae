// ha_history — one or more numeric HA sensors as Bauhaus sparklines.
// One entity → a big hero (current + trend + large chart). Several →
// stacked rows, each with a name, current value, trend arrow and a
// mini sparkline. Non-scaling stroke keeps the line crisp on e-ink.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
  <link rel="stylesheet" href="/plugins/ha_history/client.css">`;

const TREND_ICON = { up: "trend-up", down: "trend-down", flat: "arrow-right" };

function sparkline(values) {
  const n = values.length;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const range = hi - lo || 1;
  const W = 100, H = 100;
  const pts = values.map((v, i) => {
    const x = n > 1 ? (i / (n - 1)) * W : 0;
    const y = H - ((v - lo) / range) * H;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const line = pts.join(" ");
  const area = `0,${H} ${line} ${W},${H}`;
  return `
    <svg class="hh-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      <polygon class="hh-area" points="${area}"></polygon>
      <polyline class="hh-line" points="${line}" vector-effect="non-scaling-stroke"></polyline>
    </svg>`;
}

function bar(title, hours) {
  return `
    <header class="wb-bar">
      <span class="wb-mark" aria-hidden="true"></span>
      <span class="wb-title">${escapeHtml(title)}</span>
      <span class="wb-bar-meta">${escapeHtml(hours)}h</span>
    </header>`;
}

function valueHtml(it) {
  const u = it.unit ? `<span class="hh-unit">${escapeHtml(it.unit)}</span>` : "";
  return `${escapeHtml(it.current) || "—"}${u}`;
}

function trendIcon(it) {
  return `<i class="hh-trend is-${escapeHtml(it.trend)} ph-bold ph-${TREND_ICON[it.trend] || "arrow-right"}" aria-hidden="true"></i>`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};

  if (data.error) {
    shadow.innerHTML = `${HEAD}
      <div class="root error">
        <i class="ph ph-warning-circle" aria-hidden="true"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }
  if (data.empty || !(data.items && data.items.length)) {
    shadow.innerHTML = `${HEAD}
      <div class="root">
        ${bar(data.title || "History", data.hours || 24)}
        <div class="hh-stub">
          <i class="ph-duotone ph-chart-line" aria-hidden="true"></i>
          <div class="hh-stub-primary">Pick entities</div>
          <div class="hh-stub-secondary">List numeric Home Assistant entity ids to chart.</div>
        </div>
      </div>`;
    return;
  }

  const size = ctx.cell.size;
  const items = data.items;

  if (items.length === 1) {
    const it = items[0];
    const chart = it.sparse
      ? `<div class="hh-note">Not enough history yet</div>`
      : `${sparkline(it.values)}`;
    shadow.innerHTML = `${HEAD}
      <div class="root size-${size} is-hero">
        ${bar(data.title, data.hours)}
        <section class="hh-hero">
          <div class="hh-current">${valueHtml(it)} ${trendIcon(it)}</div>
          <div class="hh-chart">${chart}</div>
          ${it.sparse ? "" : `<div class="hh-axis"><span>${escapeHtml(it.min)}</span><span>${escapeHtml(it.max)}</span></div>`}
        </section>
      </div>`;
    return;
  }

  const rows = items.map((it) => `
    <article class="hh-row${it.sparse ? " is-sparse" : ""}">
      <div class="hh-row-meta">
        <div class="hh-row-name">${escapeHtml(it.name)}</div>
        <div class="hh-row-value">${valueHtml(it)} ${trendIcon(it)}</div>
      </div>
      <div class="hh-row-chart">${it.sparse ? "" : sparkline(it.values)}</div>
    </article>`).join("");

  shadow.innerHTML = `${HEAD}
    <div class="root size-${size}">
      ${bar(data.title, data.hours)}
      <section class="hh-list">${rows}</section>
    </div>`;
}
