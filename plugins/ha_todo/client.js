// ha_todo — Spectra list archetype. Pending items as zebra rows with
// a leading checkbox icon; completed items get a struck-through check
// in muted. Title meta carries the pending count.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDue(iso) {
  if (!iso || typeof iso !== "string") return "";
  // Date-only payloads ("YYYY-MM-DD") and datetimes both fit Date's
  // parser; we just trim seconds.
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return "";
  const today = new Date();
  const year = today.getFullYear();
  const ds = `${m[2]}-${m[3]}${parseInt(m[1], 10) !== year ? "/" + m[1].slice(2) : ""}`;
  return ds;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_todo">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>To-do</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const title = data.title || "To-do";
  const pending = data.needs_action_count ?? items.filter((i) => i.status !== "completed").length;
  const completed = data.completed_count ?? items.filter((i) => i.status === "completed").length;

  if (items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_todo">
        <div class="w-title"><i class="ph-bold ph-list-checks"></i><h3>${escapeHtml(title)}</h3></div>
        <div class="w-body"><p class="u-muted">All done.</p></div>
      </div>`;
    return;
  }

  const rows = items.map((it, i) => {
    const done = it.status === "completed";
    const ph = done ? "ph-check-square" : "ph-square";
    const accent = done ? "var(--text-muted)" : "var(--accent-4)";
    const titleStyle = done
      ? 'color:var(--text-muted);text-decoration:line-through'
      : "";
    const due = !done ? fmtDue(it.due) : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title" style="${titleStyle}">${escapeHtml(it.summary)}</span>
        </div>
        <span class="list-meta u-muted" style="font-weight:var(--fw-semi)">${escapeHtml(due)}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_todo">
      <div class="w-title">
        <i class="ph-bold ph-list-checks" style="color:${pending > 0 ? "var(--accent-4)" : "var(--accent-3)"}"></i>
        <h3>${escapeHtml(title)}</h3>
        <span class="w-title-meta">${pending} TO DO${completed > 0 ? ` · ${completed} DONE` : ""}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
