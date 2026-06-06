// ha_todo — Spectra list archetype. Pending items as zebra rows
// with a leading checkbox icon, a due-proximity chip (overdue →
// terracotta, today → ochre, tomorrow → moss, this week → slate,
// later → muted), and an optional priority dot when the integration
// surfaces an iCal priority. Completed items get the struck-through
// muted check.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Parse a "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS" string into a Date
// representing the local day-start. Times beyond date precision are
// kept as-is for ISO datetime payloads (server returns whatever HA
// gave it). Returns null for malformed inputs.
function parseDue(iso) {
  if (typeof iso !== "string" || !iso) return null;
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10));
}

// Categorise the due-date relative to today. "Today" is a same-day
// match; "tomorrow" is +1; "soon" is 2–7 days; "later" is anything
// beyond. Negative deltas (in the past) bucket as "overdue".
function dueProximity(iso) {
  const d = parseDue(iso);
  if (!d) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((d.getTime() - today.getTime()) / 86_400_000);
  if (days < 0) return { key: "overdue", days, color: "var(--accent-1)", label: days === -1 ? "yesterday" : `${-days}d late` };
  if (days === 0) return { key: "today", days, color: "var(--accent-2)", label: "today" };
  if (days === 1) return { key: "tomorrow", days, color: "var(--accent-3)", label: "tomorrow" };
  if (days <= 7) return { key: "soon", days, color: "var(--accent-5)", label: `${days}d` };
  return { key: "later", days, color: "var(--text-muted)", label: formatLater(d) };
}

function formatLater(d) {
  const today = new Date();
  const sameYear = d.getFullYear() === today.getFullYear();
  return sameYear
    ? `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}`
    : `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}/${String(d.getFullYear()).slice(2)}`;
}

// iCal priority 0-9. 0 = none, 1-4 = high (terracotta), 5 = medium
// (ochre), 6-9 = low (muted). Returns null when there's no priority
// to render.
function priorityDot(priority) {
  if (!Number.isFinite(priority) || priority === 0) return null;
  if (priority >= 1 && priority <= 4) return { color: "var(--accent-1)", label: "high" };
  if (priority === 5) return { color: "var(--accent-2)", label: "medium" };
  if (priority >= 6 && priority <= 9) return { color: "var(--text-muted)", label: "low" };
  return null;
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

  // Count overdue items so the title bar can scream when something's
  // actually past its date.
  const overdueCount = items.filter((it) => {
    if (it.status === "completed") return false;
    const p = dueProximity(it.due);
    return p?.key === "overdue";
  }).length;

  if (items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_todo">
        <div class="w-title"><i class="ph-bold ph-list-checks"></i><h3>${escapeHtml(title)}</h3></div>
        <div class="w-body" style="justify-content:center;align-items:center">
          <i class="ph-bold ph-check-circle" style="color:var(--accent-3);font-size:3em"></i>
          <p class="u-muted">All done.</p>
        </div>
      </div>`;
    return;
  }

  const rows = items.map((it, i) => {
    const done = it.status === "completed";
    const ph = done ? "ph-check-square" : "ph-square";
    const accent = done ? "var(--text-muted)" : "var(--accent-4)";
    const titleStyle = done
      ? "color:var(--text-muted);text-decoration:line-through"
      : "";
    const proximity = !done ? dueProximity(it.due) : null;
    const dueChip = proximity
      ? `<span class="todo-due todo-due--${proximity.key}" style="color:${proximity.color};background:color-mix(in oklab, ${proximity.color} 14%, var(--surface))">
          ${proximity.key === "overdue" ? '<i class="ph-bold ph-warning" style="font-size:.85em"></i>' : ""}
          ${escapeHtml(proximity.label)}
        </span>`
      : "";
    const dot = !done ? priorityDot(it.priority) : null;
    const dotSpan = dot
      ? `<span class="todo-priority" style="background:${dot.color}" title="priority: ${dot.label}"></span>`
      : "";
    return `
      <div class="todo-row ${i % 2 ? "is-zebra" : ""}${proximity?.key === "overdue" ? " is-overdue" : ""}">
        <div class="list-lead todo-row-lead">
          ${dotSpan}
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title" style="${titleStyle}">${escapeHtml(it.summary)}</span>
        </div>
        ${dueChip}
      </div>`;
  }).join("");

  const titleAccent = overdueCount > 0 ? "var(--accent-1)" : pending > 0 ? "var(--accent-4)" : "var(--accent-3)";
  const meta = overdueCount > 0
    ? `<span class="w-title-meta" style="color:var(--accent-1)"><i class="ph-bold ph-warning" style="margin-right:.2em"></i>${overdueCount} OVERDUE</span>`
    : `<span class="w-title-meta">${pending} TO DO${completed > 0 ? ` · ${completed} DONE` : ""}</span>`;

  const layout = `
    .todo-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .todo-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .todo-row.is-overdue {
      background: color-mix(in oklab, var(--accent-1) 8%, var(--surface));
      box-shadow: inset 3px 0 0 var(--accent-1);
    }
    .todo-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .todo-row-lead .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .todo-priority {
      display: inline-block;
      width: 0.55em;
      height: 0.55em;
      border-radius: 50%;
      flex: 0 0 auto;
    }
    .todo-due {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 1px var(--space-1);
      border-radius: 999px;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      font-variant-numeric: tabular-nums;
      flex: 0 0 auto;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_todo">
      <div class="w-title">
        <i class="ph-bold ph-list-checks" style="color:${titleAccent}"></i>
        <h3>${escapeHtml(title)}</h3>
        ${meta}
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
