// news_wikipedia_otd — Spectra list archetype. Wikipedia "On This Day"
// events. Each row leads with the YEAR (right-aligned-meta-style),
// followed by the historical text; the title bar shows the date.

const KIND_LABEL = {
  events: "EVENTS",
  births: "BIRTHS",
  deaths: "DEATHS",
  holidays: "HOLIDAYS",
  selected: "SELECTED",
  all: "ALL",
};

const KIND_ACCENT = {
  events: "var(--accent-5)",
  births: "var(--accent-3)",
  deaths: "var(--accent-1)",
  holidays: "var(--accent-2)",
  selected: "var(--accent-4)",
};

// Per-kind leading icon so a glance across the rows reads as the
// shape of the day's history.
const KIND_PH = {
  events: "ph-clock-counter-clockwise",
  births: "ph-baby",
  deaths: "ph-flower",
  holidays: "ph-confetti",
  selected: "ph-star",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_wikipedia_otd">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>On This Day</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const kind = data.kind || "events";
  const dateLabel = data.date || "";
  const kindLabel = KIND_LABEL[kind] || String(kind).toUpperCase();
  const accent = KIND_ACCENT[kind] || "var(--accent-5)";

  if (items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_wikipedia_otd">
        <div class="w-title">
          <i class="ph-bold ph-clock-counter-clockwise" style="color:${accent}"></i>
          <h3>On ${escapeHtml(dateLabel)}</h3>
        </div>
        <div class="w-body"><p class="u-muted">Nothing recorded.</p></div>
      </div>`;
    return;
  }

  const ph = KIND_PH[kind] || "ph-clock-counter-clockwise";

  const rows = items.map((it, i) => {
    // Two-line title: the historical text on top, the Wikipedia page
    // it points at beneath in muted weight with a small ph-arrow-
    // bend-up-right cue so the row reads as "this event → that
    // article".
    const pageLine = it.page
      ? `<small style="display:flex;align-items:center;gap:.3em;color:var(--text-muted);font-weight:var(--fw-semi);font-size:.72em;line-height:1.1;margin-top:.15em">
          <i class="ph-bold ph-arrow-bend-up-right" style="font-size:.95em"></i>
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(it.page)}</span>
        </small>`
      : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}" style="align-items:flex-start;padding-top:var(--space-2);padding-bottom:var(--space-2)">
        <div class="list-lead" style="align-items:flex-start">
          <i class="ph-bold ${ph}" style="color:${accent};margin-top:.15em"></i>
          <div style="display:flex;flex-direction:column;min-width:0">
            <span class="list-title" style="white-space:normal;line-height:1.2">${escapeHtml(it.text)}</span>
            ${pageLine}
          </div>
        </div>
        <span class="list-meta" style="color:${accent};font-weight:var(--fw-black);font-size:var(--fs-lead)">${escapeHtml(String(it.year || "—"))}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="news_wikipedia_otd">
      <div class="w-title">
        <i class="ph-bold ph-clock-counter-clockwise" style="color:${accent}"></i>
        <h3>On ${escapeHtml(dateLabel)}</h3>
        <span class="w-title-meta">${escapeHtml(kindLabel)}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
