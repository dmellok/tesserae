// todo, Spectra list archetype. Each item is a zebra row: empty
// checkbox + accent-4 for active items, struck-through with a muted
// check for completed ones. A completion progress bar sits beneath
// the title so the overall list progress reads at a glance, plus
// the active / total count in the title meta.

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
        <div class="w-body" style="justify-content:center;align-items:center">
          <i class="ph-bold ph-check-circle" style="color:var(--accent-3);font-size:3em"></i>
          <p class="u-muted">All done.</p>
        </div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const total = data.total ?? items.length;
  const completed = data.completed ?? items.filter((i) => i.completed_at).length;
  const active = total - completed;
  const completionPct = total > 0 ? (completed / total) * 100 : 0;

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

  // Completion bar, overall progress through the list. Reads as a
  // calm horizontal rule with a moss accent fill so a "you're 60%
  // through this list" sense lands before you scan the row text.
  const progressBar = total > 0
    ? `
      <div class="todo-progress" title="${completed} of ${total} done">
        <div class="todo-progress-track">
          <div class="todo-progress-fill" style="width:${completionPct.toFixed(1)}%"></div>
        </div>
        <span class="todo-progress-text">${completed}<span class="todo-progress-of">/</span>${total}</span>
      </div>`
    : "";

  const layout = `
    .todo-progress {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      padding: var(--space-1) var(--space-3) 0;
    }
    .todo-progress-track {
      flex: 1 1 auto;
      height: 5px;
      border-radius: 3px;
      background: color-mix(in oklab, var(--text-primary) 6%, transparent);
      overflow: hidden;
    }
    .todo-progress-fill {
      height: 100%;
      background: var(--accent-3);
      border-radius: 3px;
    }
    .todo-progress-text {
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
      color: var(--text-secondary);
      flex: 0 0 auto;
      min-width: 2.6em;
      text-align: right;
    }
    .todo-progress-of {
      color: var(--text-muted);
      margin: 0 1px;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="todo">
      <div class="w-title">
        <i class="ph-bold ph-list-checks" style="color:${active > 0 ? "var(--accent-4)" : "var(--accent-3)"}"></i>
        <h3>${escapeHtml(listName)}</h3>
        <span class="w-title-meta">${active} TO DO${completed > 0 ? ` · ${completed} DONE` : ""}</span>
      </div>
      ${progressBar}
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
