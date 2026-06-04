// clock_analog — Spectra-styled analog face. Pure inline SVG so the
// renderer can screenshot it without waiting on font / image loads.
// Three concentric layers: tick ring (heavy 12/3/6/9, thin else),
// hour numerals at the cardinals, hour + minute + optional second
// hand. Hands resolve to Spectra tokens so a theme swap re-skins
// the clock with no markup change.

function clockSvg(opts) {
  const { showSeconds } = opts;
  const now = new Date();
  const h = now.getHours() % 12;
  const m = now.getMinutes();
  const s = now.getSeconds();

  // Convert to angles. SVG 0° is at 3 o'clock; rotate -90 so 12 is up.
  const hourAng = (h + m / 60) * 30 - 90;
  const minAng = (m + s / 60) * 6 - 90;
  const secAng = s * 6 - 90;

  function hand(angle, len, width, color) {
    const rad = (angle * Math.PI) / 180;
    const x = 50 + Math.cos(rad) * len;
    const y = 50 + Math.sin(rad) * len;
    return `<line x1="50" y1="50" x2="${x.toFixed(2)}" y2="${y.toFixed(2)}" stroke="${color}" stroke-width="${width}" stroke-linecap="round" />`;
  }

  // Ticks
  let ticks = "";
  for (let i = 0; i < 60; i++) {
    const major = i % 5 === 0;
    const cardinal = i % 15 === 0;
    const ang = i * 6 - 90;
    const rad = (ang * Math.PI) / 180;
    const r1 = cardinal ? 38 : major ? 40 : 42;
    const r2 = 44;
    const x1 = 50 + Math.cos(rad) * r1;
    const y1 = 50 + Math.sin(rad) * r1;
    const x2 = 50 + Math.cos(rad) * r2;
    const y2 = 50 + Math.sin(rad) * r2;
    const w = cardinal ? 2.2 : major ? 1.4 : 0.6;
    const color = cardinal ? "var(--text-primary)" : major ? "var(--text-secondary)" : "var(--text-muted)";
    ticks += `<line x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}" stroke="${color}" stroke-width="${w}" stroke-linecap="round"/>`;
  }

  // Cardinal numerals
  let nums = "";
  for (const [num, angDeg] of [[12, -90], [3, 0], [6, 90], [9, 180]]) {
    const r = 30;
    const rad = (angDeg * Math.PI) / 180;
    const x = 50 + Math.cos(rad) * r;
    const y = 50 + Math.sin(rad) * r;
    nums += `<text x="${x.toFixed(2)}" y="${y.toFixed(2)}" text-anchor="middle" dominant-baseline="central" font-size="7" font-weight="900" fill="var(--text-primary)">${num}</text>`;
  }

  const handHour = hand(hourAng, 22, 3.5, "var(--text-primary)");
  const handMin = hand(minAng, 32, 2.5, "var(--text-primary)");
  const handSec = showSeconds ? hand(secAng, 36, 1, "var(--accent-1)") : "";

  return `
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%;display:block">
      <circle cx="50" cy="50" r="45" fill="var(--surface)" stroke="none"/>
      ${ticks}
      ${nums}
      ${handHour}
      ${handMin}
      ${handSec}
      <circle cx="50" cy="50" r="2.4" fill="var(--text-primary)"/>
      ${showSeconds ? `<circle cx="50" cy="50" r="1.2" fill="var(--accent-1)"/>` : ""}
    </svg>`;
}

export default function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const showSeconds = opts.show_seconds !== false;
  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <div class="w" data-widget="clock_analog">
      <div class="w-body" style="justify-content:center;align-items:center;padding:0">
        ${clockSvg({ showSeconds })}
      </div>
    </div>`;
}
