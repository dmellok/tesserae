// ha_automation_history — HA automation firing history.
//
// Fragment-first, ha_* family house style: paints on the shared
// spectra-widgets.css structural classes (.w-title / .w-body / list-body /
// list-lead / list-title / list-meta / u-muted) + design tokens, never raw
// pixels. `full` = ranking + totals + recently-active board; `bar` = a
// fired-count chip. No images, no dither, no animation, idempotent innerHTML.

export default function render(shadow, ctx) {
  const data = (ctx && ctx.data) || {};
  const o = (ctx.cell && ctx.cell.options) || {};
  const fragment = (ctx.cell && ctx.cell.fragment) || ctx.fragment || "full";
  const title = (o.title && o.title.trim()) || data.title || "Automations";

  // ---- helpers ------------------------------------------------------------
  const num = (n) => typeof n === "number" && isFinite(n);
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  function ago(sec) {
    if (!num(sec)) return "—";
    if (sec < 45) return "now";
    if (sec < 3600) return Math.round(sec / 60) + "m";
    if (sec < 86400) return Math.round(sec / 3600) + "h";
    return Math.round(sec / 86400) + "d";
  }
  const count = (n) => (num(n) ? String(n) : "0");

  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;
  const layout = `
    .ah-stack { display: flex; flex-direction: column; gap: var(--space-1);
                height: 100%; min-height: 0; }

    .ah-rank { display: flex; gap: var(--space-2); flex: 0 0 auto; }
    .ah-rank .tile { flex: 1 1 0; min-width: 0; background: var(--surface-sunken);
                     border-radius: var(--radius-1); padding: var(--space-2);
                     display: flex; flex-direction: column; gap: 1px; }
    .ah-rank .lab { display: flex; align-items: center; gap: var(--space-1);
                    font-size: var(--fs-caption); font-weight: var(--fw-bold);
                    letter-spacing: var(--ls-label); text-transform: uppercase;
                    color: var(--text-muted); white-space: nowrap; overflow: hidden; }
    .ah-rank .most .lab i { color: var(--accent-2); }
    .ah-rank .least .lab i { color: var(--text-muted); }
    .ah-rank .nm { font-weight: var(--fw-bold); white-space: nowrap;
                   overflow: hidden; text-overflow: ellipsis; }
    .ah-rank .ct { font-size: var(--fs-caption); color: var(--text-secondary);
                   font-variant-numeric: tabular-nums; }

    .ah-totals { display: flex; gap: var(--space-2); flex: 0 0 auto; }
    .ah-totals .cell { flex: 1 1 0; text-align: center; background: var(--surface-sunken);
                       border-radius: var(--radius-1); padding: var(--space-1); }
    .ah-totals .n { font-weight: var(--fw-black); font-size: 1.4em; line-height: 1.05;
                    font-variant-numeric: tabular-nums; }
    .ah-totals .l { font-size: var(--fs-caption); color: var(--text-muted);
                    letter-spacing: var(--ls-label); text-transform: uppercase; }

    .ah-recent { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column;
                 gap: 1px; overflow: hidden; }
    .ah-recent .subhead { font-size: var(--fs-caption); font-weight: var(--fw-bold);
                          letter-spacing: var(--ls-label); text-transform: uppercase;
                          color: var(--text-muted); flex: 0 0 auto; margin-bottom: 1px; }
    .arow { display: flex; align-items: center; justify-content: space-between;
            gap: var(--space-2); padding: 2px var(--space-2);
            border-radius: var(--radius-1); min-width: 0; }
    .arow.is-zebra { background: color-mix(in oklab, var(--text-primary) 3%, transparent); }
    .arow.is-stale { background: color-mix(in oklab, var(--accent-1) 8%, var(--surface));
                     box-shadow: inset 3px 0 0 var(--accent-1); }
    .arow .list-lead { flex: 1 1 auto; min-width: 0; display: flex; align-items: center;
                       gap: var(--space-2); }
    .arow .dot { flex: 0 0 auto; width: .5em; height: .5em; border-radius: 50%;
                 background: var(--accent-3); }
    .arow.is-off .dot { background: var(--text-muted); }
    .arow .list-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .arow.is-off .list-title { color: var(--text-muted); }
    .arow .flag { flex: 0 0 auto; color: var(--accent-1); }
    .arow .list-meta { flex: 0 0 auto; font-variant-numeric: tabular-nums;
                       color: var(--text-secondary); }
    .arow .list-meta .x { color: var(--text-muted); margin-right: .4em; }

    .w-title-meta .stale { color: var(--accent-1); font-weight: var(--fw-bold);
                           margin-right: var(--space-1); }

    /* responsive trimming — the .w is a size container in spectra-widgets.css */
    @container (max-width: 460px) { .ah-totals { display: none; } }
    @container (max-width: 210px) {
      .ah-recent, .w-title-meta .win { display: none; }
      .ah-rank { flex-direction: column; }
    }

    /* bar fragment */
    .ah-bar { display: flex; align-items: center; gap: var(--space-2); height: 100%;
              padding: 0 var(--space-2); box-sizing: border-box; }
    .ah-bar i.logo { color: var(--accent-3); font-size: clamp(1.3em, 44cqmin, 2.8em); flex: 0 0 auto; }
    .ah-bar .stat { display: flex; align-items: baseline; gap: .25em; min-width: 0; }
    .ah-bar .stat b { font-size: clamp(1.3em, 44cqmin, 2.8em); font-weight: var(--fw-black);
                      font-variant-numeric: tabular-nums; }
    .ah-bar .stat small { color: var(--text-secondary); font-size: clamp(.55em, 16cqmin, 1em); }
    .ah-bar .sep { color: var(--text-muted); flex: 0 0 auto; }
    .ah-bar .barstale { margin-left: auto; display: inline-flex; align-items: center;
                        gap: .2em; color: var(--accent-1); font-weight: var(--fw-bold);
                        font-size: clamp(.55em, 16cqmin, .95em); flex: 0 0 auto; }
  `;

  // ---- error state --------------------------------------------------------
  if (data.error) {
    shadow.innerHTML = `${css}
      <div class="w" data-widget="ha_automation_history">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>${esc(title)}</h3></div>
        <div class="w-body"><p class="u-muted">${esc(data.error)}</p></div>
      </div>`;
    return;
  }

  // ---- empty state --------------------------------------------------------
  if (data.empty || !Array.isArray(data.automations) || data.automations.length === 0) {
    shadow.innerHTML = `${css}
      <div class="w" data-widget="ha_automation_history">
        <div class="w-title"><i class="ph-bold ph-lightning-slash"></i><h3>${esc(title)}</h3></div>
        <div class="w-body"><p class="u-muted">No automations found${
          data.tracked_all === false ? " for your selection" : ""
        }.</p></div>
      </div>`;
    return;
  }

  // ---- bar fragment -------------------------------------------------------
  if (fragment === "bar") {
    const stale = num(data.stale_count) && data.stale_count > 0
      ? `<span class="barstale"><i class="ph-bold ph-warning"></i>${data.stale_count}</span>` : "";
    shadow.innerHTML = `${css}<style>${layout}</style>
      <div class="w" data-widget="ha_automation_history">
        <div class="ah-bar">
          <i class="ph-bold ph-lightning logo"></i>
          <span class="stat"><b>${count(data.total_24h)}</b><small>fired · 24h</small></span>
          <span class="sep">·</span>
          <span class="stat"><b>${count(data.total_1h)}</b><small>1h</small></span>
          ${stale}
        </div>
      </div>`;
    return;
  }

  // ---- full board ---------------------------------------------------------
  const most = data.most_fired;
  const least = data.least_fired;
  const rankTiles =
    (most
      ? `<div class="tile most">
           <div class="lab"><i class="ph-bold ph-trophy"></i>Most fired · 7d</div>
           <div class="nm">${esc(most.name)}</div>
           <div class="ct">${count(most.c7)} triggers</div>
         </div>`
      : `<div class="tile most">
           <div class="lab"><i class="ph-bold ph-moon"></i>This week</div>
           <div class="nm">No triggers</div>
           <div class="ct">quiet week</div>
         </div>`) +
    (least
      ? `<div class="tile least">
           <div class="lab"><i class="ph-bold ph-arrow-down"></i>Least fired · 7d</div>
           <div class="nm">${esc(least.name)}</div>
           <div class="ct">${count(least.c7)} triggers</div>
         </div>`
      : "");

  const totals = `
    <div class="ah-totals">
      <div class="cell"><div class="n">${count(data.total_1h)}</div><div class="l">1 hour</div></div>
      <div class="cell"><div class="n">${count(data.total_24h)}</div><div class="l">24 hours</div></div>
      <div class="cell"><div class="n">${count(data.total_7d)}</div><div class="l">7 days</div></div>
    </div>`;

  const feed = Array.isArray(data.recent) ? data.recent : [];
  const byId = {};
  (data.automations || []).forEach((a) => { byId[a.eid] = a; });
  const rows = feed
    .map((r, i) => {
      const a = byId[r.eid] || {};
      const cls =
        (i % 2 ? " is-zebra" : "") + (a.stale ? " is-stale" : "") + (a.on === false ? " is-off" : "");
      const flag = a.stale ? `<i class="ph-bold ph-warning flag"></i>` : "";
      const n7 = num(a.c7) ? `<span class="x">${a.c7}×</span>` : "";
      return `<div class="arow${cls}">
        <div class="list-lead"><span class="dot"></span>
          <span class="list-title">${esc(r.name)}</span>${flag}</div>
        <span class="list-meta">${n7}${ago(r.ago_s)}</span>
      </div>`;
    })
    .join("");
  const recent = `
    <div class="ah-recent">
      <div class="subhead">Recently active</div>
      <div class="list-body">${
        rows || `<p class="u-muted">Nothing in the last ${esc(data.window_days || 7)} days.</p>`
      }</div>
    </div>`;

  const stale = num(data.stale_count) && data.stale_count > 0
    ? `<span class="stale"><i class="ph-bold ph-warning"></i> ${data.stale_count} stale</span>` : "";
  const meta = `<span class="w-title-meta">${stale}<span class="win">${esc(data.window_days || 7)}d</span></span>`;

  shadow.innerHTML = `${css}<style>${layout}</style>
    <div class="w" data-widget="ha_automation_history">
      <div class="w-title">
        <i class="ph-bold ph-lightning" style="color:var(--accent-3)"></i>
        <h3>${esc(title)}</h3>
        ${meta}
      </div>
      <div class="w-body">
        <div class="ah-stack">
          <div class="ah-rank">${rankTiles}</div>
          ${totals}
          ${recent}
        </div>
      </div>
    </div>`;
}
