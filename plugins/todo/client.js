// todo — Bauhaus checklist card. Display-only; editing happens on the
// admin page at /plugins/todo/.
//
// Layout:
//   1. Inverted header bar (mark + list name + N/M done count)
//   2. Item list (incomplete ph-circle, completed ph-fill-check-circle
//      with strikethrough)

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function timeAgo(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function renderEmpty(reason, listName) {
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
    secondary = listName ? `Nothing on '${listName}'.` : "Nothing to do.";
  }
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/todo/client.css">
    <div class="root size-md is-empty">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">${escapeHtml(listName || "Todo")}</span>
      </header>
      <div class="td-empty">
        <i class="ph-duotone ph-${icon}" aria-hidden="true"></i>
        <div class="td-empty-primary">${escapeHtml(primary)}</div>
        <div class="td-empty-secondary">${escapeHtml(secondary)}</div>
      </div>
    </div>
  `;
}

function itemHtml(it) {
  const done = !!it.completed_at;
  const iconCls = done ? "ph-fill ph-fill-check-circle" : "ph ph-circle";
  return `
    <li class="td-item ${done ? "td-item--done" : ""}">
      <i class="${iconCls} td-tick" aria-hidden="true"></i>
      <span class="td-text">${escapeHtml(it.text || "")}</span>
      ${done ? `<span class="td-when">${escapeHtml(timeAgo(it.completed_at))}</span>` : ""}
    </li>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.empty) {
    shadow.innerHTML = renderEmpty(data.reason || "no_list", data.list_name);
    return;
  }

  const size = ctx.cell.size;
  const items = Array.isArray(data.items) ? data.items : [];
  const done = data.completed || 0;
  const total = data.total || items.length;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
    <link rel="stylesheet" href="/plugins/todo/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">${escapeHtml(data.list_name || "Todo")}</span>
        <span class="wb-bar-count">${done}/${total}</span>
      </header>
      <ul class="td-list">
        ${items.map(itemHtml).join("")}
      </ul>
    </div>
  `;
}
