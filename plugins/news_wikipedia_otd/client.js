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

  const rows = items.map((it, i) => `
    <div class="list-row ${i % 2 ? "is-zebra" : ""}">
      <div class="list-lead">
        <span class="list-title">${escapeHtml(it.text)}</span>
      </div>
      <span class="list-meta" style="color:${accent}">${escapeHtml(String(it.year || "—"))}</span>
    </div>`).join("");

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
