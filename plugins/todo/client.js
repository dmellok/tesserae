// todo — Spectra list archetype. Each item is a zebra row: empty
// checkbox + accent-4 for active items, struck-through with a
// muted check for completed ones. Title meta surfaces the
// active / total counts.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  const listName = data.list_name || "To-do";

  if (data.empty) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="todo">
        <div class="w-title"><i class="ph-bold ph-list-checks" style="color:var(--accent-3)"></i><h3>${escapeHtml(listName)}</h3></div>
        <div class="w-body"><p class="u-muted">All done.</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const total = data.total ?? items.length;
  const completed = data.completed ?? items.filter((i) => i.completed_at).length;
  const active = total - completed;

  const rows = items.map((it, i) => {
    const done = !!it.completed_at;
    const ph = done ? "ph-check-square" : "ph-square";
    const accent = done ? "var(--text-muted)" : "var(--accent-4)";
    const titleStyle = done
      ? "color:var(--text-muted);text-decoration:line-through"
      : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title" style="${titleStyle}">${escapeHtml(it.text || "")}</span>
        </div>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="todo">
      <div class="w-title">
        <i class="ph-bold ph-list-checks" style="color:${active > 0 ? "var(--accent-4)" : "var(--accent-3)"}"></i>
        <h3>${escapeHtml(listName)}</h3>
        <span class="w-title-meta">${active} TO DO${completed > 0 ? ` · ${completed} DONE` : ""}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
