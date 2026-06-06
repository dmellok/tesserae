// ha_entities — Spectra list archetype. Generic entity list with a
// Phosphor icon per row (server picks domain / device_class →
// glyph), the current value/state on the right, and a "just changed"
// chip on rows whose last_changed timestamp sits within the past
// 10 minutes. Recently-changed rows also pick up a soft accent-4
// background tint via contrast alone (no animation, e-ink safe).

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// State-vocab → accent. Same palette as before:
//   on / open / playing / home → live teal (accent-4)
//   off / closed / idle        → muted
//   missing / unavailable      → terracotta (accent-1)
//   anything else (numeric / named) → text-secondary
function statusAccent(status) {
  if (!status) return "var(--text-secondary)";
  const s = String(status).toLowerCase();
  if (["on", "open", "playing", "home"].includes(s)) return "var(--accent-4)";
  if (["off", "closed", "idle"].includes(s)) return "var(--text-muted)";
  if (["missing", "unavailable", "unknown"].includes(s)) return "var(--accent-1)";
  return "var(--text-secondary)";
}

// Compact "Nm ago" for the change badge — only used when the change
// is within the 10-minute window, so it's always sub-hour.
function changeAgo(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs > 600) return null; // outside 10-minute window → no badge
  if (secs < 60) return "just now";
  return `${Math.floor(secs / 60)}m ago`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_entities">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Entities</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (data.empty || !Array.isArray(data.items) || data.items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_entities">
        <div class="w-title"><i class="ph-bold ph-list"></i><h3>${escapeHtml(data.title || "Entities")}</h3></div>
        <div class="w-body"><p class="u-muted">No entities selected.</p></div>
      </div>`;
    return;
  }

  const items = data.items;
  const title = data.title || "Entities";

  // Count recently-changed items so the title bar can carry a hint.
  let recentCount = 0;
  for (const it of items) if (changeAgo(it.last_changed)) recentCount++;

  const rows = items.map((it, i) => {
    const accent = statusAccent(it.status);
    const ago = changeAgo(it.last_changed);
    const changedClass = ago ? " is-changed" : "";
    const changeBadge = ago
      ? `<span class="entity-change-badge" title="changed ${escapeHtml(it.last_changed)}">
          <i class="ph-bold ph-circle-notch"></i>${escapeHtml(ago)}
        </span>`
      : "";
    return `
      <div class="entity-row ${i % 2 ? "is-zebra" : ""}${changedClass}">
        <div class="list-lead entity-row-lead">
          <i class="ph-bold ph-${escapeHtml(it.icon || "circle")}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(it.name)}</span>
          ${changeBadge}
        </div>
        <span class="list-meta" style="color:${accent}">${escapeHtml(it.label || "—")}</span>
      </div>`;
  }).join("");

  const layout = `
    .entity-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .entity-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    /* Recently-changed rows pick up a stronger accent-4 wash + an
       accent-4 left border so the change reads via contrast alone
       (no animation needed for e-ink). The wash overrides the zebra
       so a changed row never looks identical to its quiescent sibling
       just because it landed on an even index. */
    .entity-row.is-changed {
      background: color-mix(in oklab, var(--accent-4) 8%, var(--surface));
      box-shadow: inset 3px 0 0 var(--accent-4);
    }
    .entity-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .entity-row-lead .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .entity-change-badge {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 1px var(--space-1);
      border-radius: 999px;
      background: color-mix(in oklab, var(--accent-4) 14%, var(--surface));
      color: var(--accent-4);
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      flex: 0 0 auto;
    }
    @container (max-width: 320px) {
      .entity-change-badge { display: none; }
    }
  `;

  const meta = recentCount > 0
    ? `<span class="w-title-meta" style="color:var(--accent-4)"><i class="ph-bold ph-circle-notch" style="margin-right:.2em"></i>${recentCount}</span>`
    : `<span class="w-title-meta">${items.length}</span>`;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_entities">
      <div class="w-title">
        <i class="ph-bold ph-list" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(title)}</h3>
        ${meta}
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
