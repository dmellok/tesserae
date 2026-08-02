// Fridge grocery-list widget (direction D4: framed, accent count block, checkbox
// list). Renders the reminders.fridge snapshot fetched server-side. Read-only.

function escapeHtml(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function stateBadge(state, updated) {
  if (state === "expired") {
    return `<span class="fr-state is-exp"><i class="ph-bold ph-clock-countdown"></i>Expired</span>`;
  }
  const stale = state === "stale";
  const label = updated ? `${stale ? "Stale" : "Fresh"} · ${escapeHtml(updated)}` : stale ? "Stale" : "Fresh";
  return `<span class="fr-state ${stale ? "is-stale" : "is-fresh"}"><span class="fr-dot"></span>${label}</span>`;
}

const STYLE = `
  /* Full-bleed: the frame is the design, so drop the card padding. */
  .w[data-widget="reminders_fridge"] { padding: 0; }
  .fridge {
    --fr-accent: var(--accent-1);
    height: 100%;
    display: flex;
    flex-direction: column;
    border: max(3px, 0.5cqmin) solid var(--text-primary);
    border-radius: var(--cell-corner-radius, var(--radius-0));
    overflow: hidden;
    container-type: size;
  }
  .fridge.is-dim { opacity: 0.72; }
  .fridge.is-muted { --fr-accent: var(--text-muted); }

  .fr-head { display: flex; align-items: stretch; border-bottom: max(3px, 0.5cqmin) solid var(--text-primary); }
  .fr-count {
    background: var(--fr-accent);
    color: var(--surface);
    display: flex; align-items: baseline; gap: 0.4em;
    padding: 0.5em 0.7em;
    border-right: max(3px, 0.5cqmin) solid var(--text-primary);
  }
  .fr-num { font-size: clamp(1.6em, 15cqmin, 4em); font-weight: var(--fw-black); line-height: 0.85; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
  .fr-lab { font-size: 0.62em; font-weight: var(--fw-black); letter-spacing: 0.06em; text-transform: uppercase; line-height: 1.05; }

  .fr-meta { flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center; gap: 0.15em; padding: 0.4em 0.7em; }
  .fr-title { font-size: 0.82em; font-weight: var(--fw-black); letter-spacing: 0.02em; text-transform: uppercase; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .fr-state { display: inline-flex; align-items: center; gap: 0.4em; font-size: 0.6em; font-weight: var(--fw-bold); letter-spacing: 0.04em; color: var(--text-muted); }
  .fr-state.is-exp { color: var(--fr-accent); }
  .fr-dot { width: 0.62em; height: 0.62em; border-radius: 50%; background: var(--accent-3); flex: 0 0 auto; }
  .fr-state.is-stale .fr-dot { background: var(--accent-2); }

  .fr-list { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 0.7em; }
  .fr-row { display: flex; align-items: center; gap: 0.6em; padding: 0.35em 0; border-bottom: max(1.5px, 0.28cqmin) solid color-mix(in oklab, var(--text-primary) 16%, transparent); flex: 1; min-height: 0; }
  .fr-row:last-child { border-bottom: 0; }
  .fr-box { flex: 0 0 auto; width: 0.9em; height: 0.9em; border: max(2px, 0.32cqmin) solid var(--text-primary); border-radius: 2px; }
  .fr-box.is-high { background: var(--fr-accent); border-color: var(--fr-accent); }
  .fr-name { flex: 1; min-width: 0; font-size: 0.86em; font-weight: var(--fw-bold); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .fr-due { flex: 0 0 auto; font-size: 0.62em; font-weight: var(--fw-black); letter-spacing: 0.03em; text-transform: uppercase; color: var(--text-muted); }
  .fr-due.is-urgent { color: var(--fr-accent); }
  .fr-more { font-size: 0.62em; font-weight: var(--fw-bold); color: var(--text-muted); padding: 0.3em 0; }

  .fr-blank { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.4em; color: var(--text-muted); text-align: center; padding: 0.8em; }
  .fr-blank i { font-size: 1.8em; }
  .fr-blank .msg { font-size: 0.82em; font-weight: var(--fw-bold); }
  .fr-blank .sub { font-size: 0.68em; }

  /* Tight cells: drop the due tags and the "To buy" label so the count + names hold. */
  @container (max-width: 210px) {
    .fr-due { display: none; }
    .fr-lab { display: none; }
  }
`;

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;
  const accent = `var(--${/^accent-[1-6]$/.test(data.accent) ? data.accent : "accent-1"})`;
  const title = data.title || "Fridge";
  const style = `<style>${STYLE}</style>`;

  // Panels canvas fragment: just the count block.
  if (ctx?.fragment === "value") {
    shadow.innerHTML = `${css}<style>.fv{height:100%;display:flex;align-items:center;justify-content:center;background:${accent};color:var(--surface)}.fv b{font-size:clamp(2em,32cqmin,8em);font-weight:var(--fw-black);line-height:1;font-variant-numeric:tabular-nums}</style>
      <div class="w" data-widget="reminders_fridge"><div class="fv"><b>${data.count ?? 0}</b></div></div>`;
    return;
  }

  const framed = (inner, extra = "") =>
    `${css}${style}<div class="w" data-widget="reminders_fridge"><div class="fridge${extra}" style="--fr-accent:${accent}">${inner}</div></div>`;

  if (data.state === "expired") {
    shadow.innerHTML = framed(
      `<div class="fr-blank"><i class="ph-bold ph-clock-countdown"></i><span class="msg">List expired</span><span class="sub">Open the Companion app to refresh it.</span></div>`,
      " is-muted",
    );
    return;
  }

  if (data.empty) {
    shadow.innerHTML = framed(
      `<div class="fr-head"><div class="fr-count"><span class="fr-num">0</span><span class="fr-lab">To<br>buy</span></div><div class="fr-meta"><span class="fr-title">${escapeHtml(title)}</span></div></div>
       <div class="fr-blank"><i class="ph-bold ph-basket"></i><span class="msg">Nothing on the list</span></div>`,
    );
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const rows = items
    .map(
      (it) =>
        `<div class="fr-row"><span class="fr-box${it.high ? " is-high" : ""}"></span><span class="fr-name">${escapeHtml(it.title)}</span>${
          it.due ? `<span class="fr-due${it.urgent ? " is-urgent" : ""}">${escapeHtml(it.due)}</span>` : ""
        }</div>`,
    )
    .join("");
  const more =
    (data.count ?? 0) > (data.shown ?? 0)
      ? `<div class="fr-more">+${data.count - data.shown} more</div>`
      : "";

  shadow.innerHTML = framed(
    `<div class="fr-head">
       <div class="fr-count"><span class="fr-num">${data.count ?? 0}</span><span class="fr-lab">To<br>buy</span></div>
       <div class="fr-meta"><span class="fr-title">${escapeHtml(title)}</span>${stateBadge(data.state, data.updated_label)}</div>
     </div>
     <div class="fr-list">${rows}${more}</div>`,
    data.state === "stale" ? " is-dim" : "",
  );
}
