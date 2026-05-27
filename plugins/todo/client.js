// todo — Bauhaus checklist card. Display-only; editing happens on the
// admin page at /plugins/todo/.
//
// Layout (md/lg):
//   1. Inverted header bar (mark + list name + list-checks icon)
//   2. Progress hero — big "X / Y" with a colour-blocked progress
//      bar showing the percent done
//   3. Item list — open items first (big mono numbers, bold text),
//      completed items below (ph-check-circle + strikethrough)
//   4. 3-up stat strip (Open / Done / Oldest open age)

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function timeAgo(iso, opts = {}) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (s < 60) return opts.short ? "now" : "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m${opts.short ? "" : " ago"}`;
  if (s < 86400) return `${Math.floor(s / 3600)}h${opts.short ? "" : " ago"}`;
  return `${Math.floor(s / 86400)}d${opts.short ? "" : " ago"}`;
}

function shell(size, body, extra = "") {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/todo/client.css">
    <div class="root size-${size} ${extra}">${body}</div>
  `;
}

function renderEmpty(reason, listName, size) {
  let icon = "list-checks";
  let primary = "Pick a list";
  let secondary = 'Open Plugins → Todo and choose this cell’s list.';
  if (reason === "list_missing") {
    icon = "warning-circle";
    primary = "List not found";
    secondary = "The selected list was deleted. Re-pick it in the cell editor.";
  } else if (reason === "empty_list") {
    icon = "coffee";
    primary = "All clear";
    secondary = listName ? `Nothing on ‘${listName}’.` : "Nothing to do.";
  }
  return shell(size, `
    <header class="wb-bar">
      <span class="wb-mark" aria-hidden="true"></span>
      <span class="wb-title">${escapeHtml(listName || "Todo")}</span>
      <i class="ph-bold ph-list-checks wb-bar-icon"></i>
    </header>
    <div class="td-empty">
      <i class="ph-duotone ph-${icon}" aria-hidden="true"></i>
      <div class="td-empty-primary">${escapeHtml(primary)}</div>
      <div class="td-empty-secondary">${escapeHtml(secondary)}</div>
    </div>
  `, "is-empty");
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  const size = ctx.cell.size;

  if (data.empty) {
    shadow.innerHTML = renderEmpty(data.reason || "no_list", data.list_name, size);
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const open = items.filter((it) => !it.completed_at);
  const done = items.filter((it) => !!it.completed_at);
  const totalCount = data.total ?? items.length;
  const doneCount = data.completed ?? done.length;
  const openCount = Math.max(0, totalCount - doneCount);
  const pct = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0;

  // Oldest open item (most likely to be stale).
  let oldestAge = "—";
  if (open.length) {
    const oldest = open
      .filter((it) => it.created_at)
      .sort((a, b) => new Date(a.created_at) - new Date(b.created_at))[0];
    if (oldest) oldestAge = timeAgo(oldest.created_at, { short: true });
  }

  // Open items first (numbered), then done (with strikethrough).
  // Each open item gets a bold mono number; each done item gets a
  // ph-fill check-circle. Limit how many of each we render based on
  // size + max_items.
  const openItems = open.map((it, i) => `
    <li class="td-item td-item--open">
      <span class="td-num">${String(i + 1).padStart(2, "0")}</span>
      <span class="td-text">${escapeHtml(it.text || "")}</span>
      ${it.created_at ? `<span class="td-when"><i class="ph-bold ph-clock"></i>${escapeHtml(timeAgo(it.created_at, { short: true }))}</span>` : ""}
    </li>
  `).join("");

  const doneItems = done.map((it) => `
    <li class="td-item td-item--done">
      <i class="ph-fill ph-fill-check-circle td-tick" aria-hidden="true"></i>
      <span class="td-text">${escapeHtml(it.text || "")}</span>
      ${it.completed_at ? `<span class="td-when"><i class="ph-bold ph-check"></i>${escapeHtml(timeAgo(it.completed_at, { short: true }))}</span>` : ""}
    </li>
  `).join("");

  const ledeHtml = `
    <section class="td-progress">
      <div class="td-progress-text">
        <div class="td-progress-count">
          <span class="td-progress-done">${doneCount}</span>
          <span class="td-progress-sep">/</span>
          <span class="td-progress-total">${totalCount}</span>
        </div>
        <div class="td-progress-label">${pct}% complete</div>
      </div>
      <div class="td-progress-icon" aria-hidden="true">
        <i class="ph-bold ph-list-checks"></i>
      </div>
      <div class="td-progress-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
        <div class="td-progress-fill" style="width: ${pct}%"></div>
      </div>
    </section>
  `;

  const statsHtml = `
    <section class="td-stats">
      <div class="td-stat td-stat--accent">
        <i class="ph-bold ph-circle td-stat-icon"></i>
        <span class="td-stat-label">Open</span>
        <span class="td-stat-value">${openCount}</span>
      </div>
      <div class="td-stat td-stat--surface">
        <i class="ph-bold ph-check-circle td-stat-icon"></i>
        <span class="td-stat-label">Done</span>
        <span class="td-stat-value">${doneCount}</span>
      </div>
      <div class="td-stat td-stat--accent2">
        <i class="ph-bold ph-clock-countdown td-stat-icon"></i>
        <span class="td-stat-label">Oldest open</span>
        <span class="td-stat-value">${escapeHtml(oldestAge)}</span>
      </div>
    </section>
  `;

  shadow.innerHTML = shell(size, `
    <header class="wb-bar">
      <span class="wb-mark" aria-hidden="true"></span>
      <span class="wb-title">${escapeHtml(data.list_name || "Todo")}</span>
      <i class="ph-bold ph-list-checks wb-bar-icon"></i>
    </header>
    ${ledeHtml}
    <ul class="td-list">
      ${openItems}
      ${doneItems}
    </ul>
    ${statsHtml}
  `);
}
