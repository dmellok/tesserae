// clock_analog — analog clock face, pure SVG, 100% client-side.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function tickMarks(face) {
  const ticks = [];
  for (let i = 0; i < 60; i++) {
    const major = i % 5 === 0;
    if (face === "skeleton" && !major) continue;
    if (face === "swiss" && !major && i % 5 !== 0) continue;
    const len = major ? 8 : 3;
    const w = major ? (face === "brutalist" ? 4 : 2) : 1;
    const r1 = 100 - len;
    const r2 = 100;
    const a = (i / 60) * Math.PI * 2 - Math.PI / 2;
    const x1 = (100 + r1 * Math.cos(a)).toFixed(2);
    const y1 = (100 + r1 * Math.sin(a)).toFixed(2);
    const x2 = (100 + r2 * Math.cos(a)).toFixed(2);
    const y2 = (100 + r2 * Math.sin(a)).toFixed(2);
    ticks.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="currentColor" stroke-width="${w}" stroke-linecap="round"/>`);
  }
  return ticks.join("");
}

function numerals(face) {
  if (face !== "brutalist" && face !== "swiss") return "";
  const out = [];
  for (let n = 1; n <= 12; n++) {
    const a = (n / 12) * Math.PI * 2 - Math.PI / 2;
    const r = 76;
    const x = (100 + r * Math.cos(a)).toFixed(1);
    const y = (100 + r * Math.sin(a)).toFixed(1);
    out.push(`<text x="${x}" y="${y}" text-anchor="middle" dominant-baseline="central" font-weight="800" font-size="14">${n}</text>`);
  }
  return out.join("");
}

function bauhausShapes() {
  // Three coloured shapes — red triangle at 12, blue circle at 4, yellow square at 8
  return `
    <polygon points="100,12 92,30 108,30" fill="var(--theme-accent3)" />
    <circle cx="154" cy="155" r="10" fill="var(--theme-accent)" />
    <rect x="36" y="145" width="20" height="20" fill="var(--theme-accent2)" transform="rotate(15 46 155)" />
  `;
}

export default async function render(shadow, ctx) {
  const face = ctx.cell.options.face || "minimalist";
  const showSeconds = ctx.cell.options.show_seconds !== false;
  const size = ctx.cell.size;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/plugins/clock_analog/client.css">
    <div class="root size-${size} face-${face}">
      <svg viewBox="0 0 200 200" class="ca-svg">
        <circle class="ca-bg" cx="100" cy="100" r="98" />
        ${face === "bauhaus" ? bauhausShapes() : ""}
        <g class="ca-ticks">${tickMarks(face)}</g>
        <g class="ca-numerals">${numerals(face)}</g>
        <line class="ca-hour"   x1="100" y1="100" x2="100" y2="46" />
        <line class="ca-minute" x1="100" y1="100" x2="100" y2="22" />
        ${showSeconds ? `<line class="ca-second" x1="100" y1="100" x2="100" y2="14" />` : ""}
        <circle class="ca-hub" cx="100" cy="100" r="4" />
      </svg>
    </div>
  `;

  // Tick — update transform on hour/minute/second hands.
  function tick() {
    const d = new Date();
    const h = (d.getHours() % 12) + d.getMinutes() / 60 + d.getSeconds() / 3600;
    const m = d.getMinutes() + d.getSeconds() / 60;
    const s = d.getSeconds() + d.getMilliseconds() / 1000;
    const hourEl = shadow.querySelector(".ca-hour");
    const minEl  = shadow.querySelector(".ca-minute");
    const secEl  = shadow.querySelector(".ca-second");
    if (hourEl) hourEl.setAttribute("transform", `rotate(${h * 30} 100 100)`);
    if (minEl)  minEl.setAttribute("transform", `rotate(${m * 6} 100 100)`);
    if (secEl)  secEl.setAttribute("transform", `rotate(${s * 6} 100 100)`);
  }
  if (shadow.__caTimer) clearInterval(shadow.__caTimer);
  tick();
  shadow.__caTimer = setInterval(tick, showSeconds ? 1000 : 30000);
}
