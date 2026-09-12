// ha_locks, Spectra list archetype. Lock / door / window / garage
// entries with a stateful kind icon (swaps open ↔ closed glyph), a
// secured/unsecured chip, and, for entries that aren't secured -
// an "unsecured for Xm" timer chip computed from the entity's
// last_changed timestamp. Unsecured rows wear a soft terracotta
// wash so the queue of "stuff needing attention" pops at a glance.

// Stateful icons: separate glyphs for secured vs unsecured per kind.
const KIND_PH = {
  lock:   { secured: "ph-lock",            unsecured: "ph-lock-open" },
  door:   { secured: "ph-door",            unsecured: "ph-door-open" },
  window: { secured: "ph-square",          unsecured: "ph-square-half" },
  garage: { secured: "ph-garage",          unsecured: "ph-garage" },
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function kindIcon(kind, secured) {
  const entry = KIND_PH[kind];
  if (!entry) return "ph-lock";
  return secured ? entry.secured : entry.unsecured;
}

function unsecuredFor(iso, t) {
  if (!iso) return null;
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return null;
  const secs = Math.max(0, (Date.now() - ts) / 1000);
  if (secs < 60) return t("just_now", "just now");
  if (secs < 3600) return `${Math.floor(secs / 60)}${t("minutes_abbr", "m")}`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}${t("hours_abbr", "h")}`;
  return `${Math.floor(secs / 86400)}${t("days_abbr", "d")}`;
}

// server.py's ``state`` is a closed vocabulary (locked / unlocked /
// open / closed), so each id names a translated slot; the English
// fallback is the same lowercase word the row printed before.
function stateLabel(e, t) {
  switch (e.state) {
    case "locked": return t("state_locked", "locked");
    case "unlocked": return t("state_unlocked", "unlocked");
    case "open": return t("state_open", "open");
    case "closed": return t("state_closed", "closed");
    default:
      return e.state || (e.secured ? t("secured", "secured") : t("state_open", "open"));
  }
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const t = ctx?.t || ((key, fallback) => fallback ?? key);
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_locks">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>${escapeHtml(t("locks", "Locks"))}</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const entries = Array.isArray(data.entries) ? data.entries : [];
  // server.py sends "Doors & locks" verbatim when the cell has no Label
  // option, so only that exact default gets translated; a typed label is
  // the user's.
  const rawPlace = data.place || data.label || "";
  const place = rawPlace
    ? (rawPlace === "Doors & locks" ? t("doors_and_locks", "Doors & locks") : rawPlace)
    : t("locks", "Locks");
  const summary = data.summary || {};
  const unsecured = summary.unsecured ?? 0;

  if (entries.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_locks">
        <div class="w-title"><i class="ph-bold ph-lock"></i><h3>${escapeHtml(place)}</h3></div>
        <div class="w-body" style="justify-content:center;align-items:center">
          <i class="ph-bold ph-shield-check" style="color:var(--accent-3);font-size:3em"></i>
          <p class="u-muted">${escapeHtml(t("all_clear", "All clear."))}</p>
        </div>
      </div>`;
    return;
  }

  const rows = entries.map((e, i) => {
    const accent = e.secured ? "var(--accent-3)" : "var(--accent-1)";
    const ph = kindIcon(e.kind, e.secured);
    const since = !e.secured ? unsecuredFor(e.last_changed, t) : null;
    const sinceChip = since
      ? `<span class="lock-since" title="${escapeHtml(t("open_since", "open since"))} ${escapeHtml(e.last_changed || "")}">
          <i class="ph-bold ph-clock" style="font-size:.85em"></i>${escapeHtml(since)}
        </span>`
      : "";
    return `
      <div class="lock-row ${i % 2 ? "is-zebra" : ""}${e.secured ? "" : " is-unsecured"}">
        <div class="list-lead lock-row-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(e.name)}</span>
          ${sinceChip}
        </div>
        <span class="lock-state" style="color:${accent}">${escapeHtml(stateLabel(e, t))}</span>
      </div>`;
  }).join("");

  const titleAccent = unsecured > 0 ? "var(--accent-1)" : "var(--accent-3)";
  const meta = unsecured > 0
    ? `<span class="w-title-meta" style="color:var(--accent-1)">${unsecured} ${escapeHtml(t("open_count_suffix", "OPEN"))}</span>`
    : `<span class="w-title-meta" style="color:var(--accent-3)">${escapeHtml(t("all_secured", "ALL SECURED"))}</span>`;
  const titleIcon = unsecured > 0 ? "ph-lock-open" : "ph-shield-check";

  const layout = `
    .lock-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .lock-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    /* Unsecured rows get the terracotta wash + an accent-1 left
       border so they read as "needs attention" before you parse the
       state text. Wash overrides the zebra so an even-index row
       can't camouflage as secured. */
    .lock-row.is-unsecured {
      background: color-mix(in oklab, var(--accent-1) 8%, var(--surface));
      box-shadow: inset 3px 0 0 var(--accent-1);
    }
    .lock-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .lock-row-lead .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .lock-since {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 1px var(--space-1);
      border-radius: 999px;
      background: color-mix(in oklab, var(--accent-1) 16%, var(--surface));
      color: var(--accent-1);
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      flex: 0 0 auto;
    }
    .lock-state {
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      font-size: var(--fs-caption);
      flex: 0 0 auto;
    }
    @container (max-width: 280px) {
      .lock-since { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_locks">
      <div class="w-title">
        <i class="ph-bold ${titleIcon}" style="color:${titleAccent}"></i>
        <h3>${escapeHtml(place)}</h3>
        ${meta}
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
