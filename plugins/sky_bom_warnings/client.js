// sky_bom_warnings, Spectra list archetype, severity-led.
//
// Each Bureau of Meteorology warning paints as a row with a vertical
// colour-band on the left (severity → accent), a category icon, the
// area title, a severity-tinted tag chip, and (at lg) a state code +
// time-since-issued chip. Rows are sorted worst-severity-first so the
// eye lands on red rows before scanning down to advisories. The
// empty state gets its own moss-tinted "all clear" card so "no
// warnings" reads as deliberate reassurance, not blank space.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const SEV_ACCENT = {
  red: "var(--accent-1)",
  orange: "var(--accent-2)",
  yellow: "var(--accent-3)",
  blue: "var(--accent-5)",
};

// color-mix soft tints, produced inline rather than relying on the
// design system's --accent-N-soft tokens because not every theme
// defines a -soft variant for every accent, and we want the chip to
// degrade gracefully on themes that don't.
function sevSoft(sev) {
  const base = SEV_ACCENT[sev] || "var(--text-secondary)";
  return `color-mix(in oklab, ${base} 18%, var(--surface))`;
}

const ICON_PH = {
  fire: "ph-fire",
  flood: "ph-drop",
  thunderstorm: "ph-cloud-lightning",
  storm: "ph-cloud-lightning",
  cyclone: "ph-tornado",
  wind: "ph-wind",
  heat: "ph-thermometer-hot",
  snow: "ph-snowflake",
  frost: "ph-snowflake",
  thermometer: "ph-thermometer-hot",
  rain: "ph-cloud-rain",
  marine: "ph-wave-triangle",
  warning: "ph-warning",
};

function sevAccent(sev) {
  return SEV_ACCENT[sev] || "var(--text-secondary)";
}

function iconFor(name) {
  return ICON_PH[name] || "ph-warning";
}

const SEV_RANK = { red: 4, orange: 3, yellow: 2, blue: 1 };

// Relative "time since" label for the issued timestamp. Falls back to
// an empty string when the server hands back an unparseable value so
// the chip just doesn't render.
function timeAgo(iso) {
  if (!iso) return "";
  let t;
  try {
    t = new Date(iso).getTime();
  } catch {
    return "";
  }
  if (!Number.isFinite(t)) return "";
  const secs = (Date.now() - t) / 1000;
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

// Pick a short state code to show as the region chip. Server already
// passes ``state`` (the state where the warning was issued) and
// ``states`` (every state the warning covers). Prefer the short
// per-row state; fall back to the first entry of states[] when the
// row is multi-state.
function regionLabel(w) {
  const s = String(w.state || "").trim();
  if (s) return s.toUpperCase();
  const list = Array.isArray(w.states) ? w.states : [];
  return String(list[0] || "").toUpperCase();
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="sky_bom_warnings">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>BoM</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const region = data.region || data.state || "";

  const layoutCommon = `
    .bom-body {
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      flex-direction: column;
      gap: var(--space-2);
      overflow: hidden;
    }
    /* All-clear card, moss-tinted block with a big shield-check icon.
       Replaces the previous quiet "No active warnings." text so the
       reassuring case reads as a confident state, not blank space. */
    .bom-clear {
      flex: 1 1 auto;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: var(--space-2);
      padding: var(--space-5) var(--space-4);
      background: color-mix(in oklab, var(--accent-3) 14%, var(--surface));
      border-radius: var(--radius-0);
      text-align: center;
    }
    .bom-clear .ph-bold {
      font-size: clamp(2.4em, 22cqmin, 6em);
      color: var(--accent-3);
      line-height: 1;
    }
    .bom-clear-title {
      font-size: var(--fs-lead);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-tight);
      color: var(--accent-3);
    }
    .bom-clear-sub {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-secondary);
    }
  `;

  if (warnings.length === 0) {
    shadow.innerHTML = `
      ${css}
      <style>${layoutCommon}</style>
      <div class="w" data-widget="sky_bom_warnings">
        <div class="w-title">
          <i class="ph-bold ph-shield-check" style="color:var(--accent-3)"></i>
          <h3>BoM</h3>
          <span class="w-title-meta" style="color:var(--accent-3)">ALL CLEAR</span>
        </div>
        <div class="w-body bom-body">
          <div class="bom-clear">
            <i class="ph-bold ph-shield-check"></i>
            <span class="bom-clear-title">All clear</span>
            <span class="bom-clear-sub">${escapeHtml(region || "-")} · No active warnings</span>
          </div>
        </div>
      </div>`;
    return;
  }

  // Sort worst-severity-first so the red rows land at the top. Stable
  // secondary sort by phase (new > update > cancelled) then by the
  // original order so multiple equal-severity rows keep their server
  // ordering. Don't mutate the original array.
  const phaseOrder = { new: 0, update: 1, cancelled: 2 };
  const ordered = warnings
    .map((w, i) => ({ w, i }))
    .sort((a, b) => {
      const sa = SEV_RANK[a.w.severity] || 0;
      const sb = SEV_RANK[b.w.severity] || 0;
      if (sa !== sb) return sb - sa;
      const pa = phaseOrder[a.w.phase] ?? 9;
      const pb = phaseOrder[b.w.phase] ?? 9;
      if (pa !== pb) return pa - pb;
      return a.i - b.i;
    })
    .map((entry) => entry.w);

  const worst = ordered[0];
  const worstAccent = sevAccent(worst.severity);

  const rows = ordered.map((w) => {
    const accent = sevAccent(w.severity);
    const soft = sevSoft(w.severity);
    const ph = iconFor(w.icon);
    const tag = String(w.tag || "").toUpperCase();
    const area = w.area || w.short_title || w.title || "Warning";
    const reg = regionLabel(w);
    const ago = timeAgo(w.issued);
    return `
      <div class="bom-row" style="--sev:${accent};--sev-soft:${soft}">
        <span class="bom-row-bar"></span>
        <i class="ph-bold ${ph} bom-row-icon"></i>
        <div class="bom-row-body">
          <div class="bom-row-head">
            <span class="bom-row-title">${escapeHtml(area)}</span>
            <span class="bom-row-tag">${escapeHtml(tag)}</span>
          </div>
          <div class="bom-row-meta">
            ${reg ? `<span class="bom-row-region">${escapeHtml(reg)}</span>` : ""}
            ${ago ? `<span class="bom-row-time">${escapeHtml(ago)}</span>` : ""}
          </div>
        </div>
      </div>`;
  }).join("");

  const layoutList = `
    .bom-list {
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
      overflow: hidden;
    }
    /* Each warning row: vertical severity bar | icon | title + meta
       stack. The bar is solid, the icon picks up the same accent, and
       the row body lives in a 1fr column so titles ellipsis-truncate
       instead of wrapping into the next row's vertical space. */
    .bom-row {
      display: grid;
      grid-template-columns: var(--stroke-3) auto minmax(0, 1fr);
      align-items: stretch;
      gap: 0;
      padding: var(--space-2) 0;
      min-width: 0;
    }
    .bom-row-bar {
      background: var(--sev);
      align-self: stretch;
      min-height: 1.4em;
    }
    .bom-row-icon {
      align-self: center;
      padding: 0 var(--space-3);
      font-size: var(--icon-lg);
      color: var(--sev);
      line-height: 1;
    }
    .bom-row-body {
      padding: 0 var(--space-2);
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 0.15em;
      min-width: 0;
    }
    .bom-row-head {
      display: flex;
      align-items: baseline;
      gap: var(--space-2);
      min-width: 0;
    }
    .bom-row-title {
      font-weight: var(--fw-semi);
      font-size: var(--fs-body);
      flex: 1 1 auto;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      min-width: 0;
    }
    /* Tag chip in the severity's soft tint with the matching accent
       foreground, same chip idiom the weather_wind Beaufort badge
       uses, scaled down to fit inline beside the title. */
    .bom-row-tag {
      display: inline-flex;
      padding: 0.2em 0.55em;
      background: var(--sev-soft);
      color: var(--sev);
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      border-radius: var(--pill-radius, var(--radius-0));
      white-space: nowrap;
      flex: 0 0 auto;
      line-height: 1.1;
    }
    .bom-row-meta {
      display: flex;
      gap: var(--space-2);
      align-items: center;
      font-size: var(--fs-caption);
      color: var(--text-muted);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      font-weight: var(--fw-bold);
    }
    .bom-row-region {
      color: var(--text-secondary);
    }
    .bom-row-time { color: var(--text-muted); }

    /* xs / sm tight: drop the meta row entirely so the title +
       tag chip have room to breathe. */
    @container (max-width: 360px) {
      .bom-row-meta { display: none; }
      .bom-row-icon { font-size: var(--icon-md); padding: 0 var(--space-2); }
    }

    /* lg: bigger icons + chunkier bars + more padding per row so the
       list reads as a confident dashboard panel rather than a dense
       feed. */
    @container (min-width: 700px) {
      .bom-row {
        grid-template-columns: var(--stroke-4, 6px) auto minmax(0, 1fr);
        padding: var(--space-3) 0;
      }
      .bom-row-icon { font-size: calc(var(--icon-lg) * 1.2); }
      .bom-row-title { font-size: var(--fs-lead); }
      .bom-row-tag { font-size: var(--fs-label); padding: 0.25em 0.7em; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layoutCommon}${layoutList}</style>
    <div class="w" data-widget="sky_bom_warnings">
      <div class="w-title">
        <i class="ph-bold ph-warning" style="color:${worstAccent}"></i>
        <h3>BoM ${escapeHtml(region || "")}</h3>
        <span class="w-title-meta" style="color:${worstAccent}">${warnings.length} ACTIVE</span>
      </div>
      <div class="w-body bom-body">
        <div class="bom-list">${rows}</div>
      </div>
    </div>`;
}
