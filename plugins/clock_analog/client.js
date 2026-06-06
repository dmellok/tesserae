// clock_analog, Spectra-styled analog face. Pure inline SVG so the
// renderer can screenshot it without waiting on font / image loads.
// Five face styles (minimalist / swiss / bauhaus / brutalist /
// skeleton) tune the tick weight, numeral set, hand widths and
// accent colours; cell options layer on top (AM/PM sun, date plate,
// numeral choice).

const NUMERAL_SETS = {
  arabic: ["12", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"],
  roman:  ["XII", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI"],
};

// Per-face configuration. Each entry tells the SVG renderer which
// elements to paint and at what weight / accent. Keeping the styling
// table-driven makes adding a new face (or tuning an existing one)
// a one-line change rather than a control-flow patch.
const FACE_CONFIGS = {
  minimalist: {
    showTicks: true,
    showMinorTicks: true,
    cardinalsMode: "main",     // "main" = 12/3/6/9, "all" = 1-12, "none"
    handColor: "var(--text-primary)",
    secondColor: "var(--accent-1)",
    handWidths: { hour: 3.5, minute: 2.5, second: 1 },
    centerColor: "var(--text-primary)",
    secondTipDisc: false,
    cardinalAccents: false,
  },
  swiss: {
    showTicks: true,
    showMinorTicks: true,
    cardinalsMode: "main",
    handColor: "var(--text-primary)",
    secondColor: "var(--accent-1)",
    handWidths: { hour: 4.8, minute: 3.6, second: 1.6 },
    centerColor: "var(--text-primary)",
    // Signature Swiss railway-clock disc at the second-hand tip.
    secondTipDisc: true,
    cardinalAccents: false,
    tickWeights: { cardinal: 2.8, major: 1.8, minor: 0.6 },
  },
  bauhaus: {
    showTicks: true,
    showMinorTicks: false,
    cardinalsMode: "main",
    // Primary-triad hands: red hour, slate-blue minute, ochre second.
    // Reads as "this is a Bauhaus clock" before you parse the time.
    handColor: "var(--accent-1)",
    minuteColor: "var(--accent-5)",
    secondColor: "var(--accent-2)",
    handWidths: { hour: 4.2, minute: 3.2, second: 1.6 },
    centerColor: "var(--accent-1)",
    secondTipDisc: false,
    // Cardinal ticks rotate through the primary triad so the rim
    // reads as a colour wheel instead of four identical bars.
    cardinalAccents: true,
  },
  brutalist: {
    showTicks: true,
    showMinorTicks: false,
    cardinalsMode: "all",      // 12 heavy numerals visible
    handColor: "var(--text-primary)",
    secondColor: "var(--text-primary)",
    handWidths: { hour: 5.5, minute: 4.2, second: 2.4 },
    centerColor: "var(--text-primary)",
    secondTipDisc: false,
    cardinalAccents: false,
    // Chunky tick bars across the hour positions.
    tickWeights: { cardinal: 3.6, major: 2.6, minor: 0 },
    // Big square center hub matches the heavy hands.
    centerSquare: true,
  },
  skeleton: {
    // Nothing on the dial, just the hands floating in the cell.
    showTicks: false,
    showMinorTicks: false,
    cardinalsMode: "none",
    handColor: "var(--text-primary)",
    secondColor: "var(--accent-1)",
    handWidths: { hour: 3.2, minute: 2.4, second: 1 },
    centerColor: "var(--text-primary)",
    secondTipDisc: false,
    cardinalAccents: false,
  },
};

// Bauhaus cardinal-tick colour rotation: 12 red, 3 ochre, 6 slate,
// 9 forest. Reads as a primary-triad nod.
const BAUHAUS_ACCENTS = ["var(--accent-1)", "var(--accent-2)", "var(--accent-5)", "var(--accent-3)"];

function clockSvg(opts) {
  const { showSeconds, numerals, showDate, showAmPm, face } = opts;
  const cfg = FACE_CONFIGS[face] || FACE_CONFIGS.minimalist;
  const now = new Date();
  const h = now.getHours() % 12;
  const m = now.getMinutes();
  const s = now.getSeconds();
  const dayNum = now.getDate();
  const realHours = now.getHours();

  const hourAng = (h + m / 60) * 30 - 90;
  const minAng = (m + s / 60) * 6 - 90;
  const secAng = s * 6 - 90;

  function hand(angle, len, width, color) {
    const rad = (angle * Math.PI) / 180;
    const x = 50 + Math.cos(rad) * len;
    const y = 50 + Math.sin(rad) * len;
    return `<line x1="50" y1="50" x2="${x.toFixed(2)}" y2="${y.toFixed(2)}" stroke="${color}" stroke-width="${width}" stroke-linecap="round" />`;
  }

  // Tick ring. Face config controls whether minor ticks paint, what
  // weights to use, and whether the cardinals get primary-accent
  // colours (Bauhaus) or stay monochrome (everyone else).
  let ticks = "";
  if (cfg.showTicks) {
    const weights = cfg.tickWeights || { cardinal: 2.2, major: 1.4, minor: 0.6 };
    for (let i = 0; i < 60; i++) {
      const major = i % 5 === 0;
      const cardinal = i % 15 === 0;
      if (!cfg.showMinorTicks && !major) continue;
      if (cfg.showMinorTicks === false && cardinal && weights.minor === 0) continue;
      const ang = i * 6 - 90;
      const rad = (ang * Math.PI) / 180;
      const r1 = cardinal ? 38 : major ? 40 : 42;
      const r2 = 44;
      const x1 = 50 + Math.cos(rad) * r1;
      const y1 = 50 + Math.sin(rad) * r1;
      const x2 = 50 + Math.cos(rad) * r2;
      const y2 = 50 + Math.sin(rad) * r2;
      const w = cardinal ? weights.cardinal : major ? weights.major : weights.minor;
      let color = cardinal ? "var(--text-primary)" : major ? "var(--text-secondary)" : "var(--text-muted)";
      if (cfg.cardinalAccents && cardinal) {
        color = BAUHAUS_ACCENTS[Math.floor(i / 15) % BAUHAUS_ACCENTS.length];
      }
      ticks += `<line x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}" stroke="${color}" stroke-width="${w}" stroke-linecap="round"/>`;
    }
  }

  // Numerals. Face config decides whether to show 12 / main / none;
  // cell option `numerals` picks Arabic vs Roman labels. Roman font
  // is dropped one step because "XII" is wider than "12".
  let nums = "";
  if (cfg.cardinalsMode !== "none" && numerals !== "dots") {
    const set = NUMERAL_SETS[numerals] || NUMERAL_SETS.arabic;
    const fontSize = numerals === "roman" ? 5.5 : 7;
    const positions = cfg.cardinalsMode === "all"
      ? Array.from({ length: 12 }, (_, i) => i)        // 0..11 → 12,1,2,..,11
      : [0, 3, 6, 9];                                  // main cardinals
    for (const idx of positions) {
      const angDeg = idx * 30 - 90;
      const r = cfg.cardinalsMode === "all" ? 32 : 30;
      const rad = (angDeg * Math.PI) / 180;
      const x = 50 + Math.cos(rad) * r;
      const y = 50 + Math.sin(rad) * r;
      let fill = "var(--text-primary)";
      if (cfg.cardinalAccents && cfg.cardinalsMode === "main") {
        // Bauhaus: pair the numeral colour with its tick on the rim.
        const cardIdx = [0, 3, 6, 9].indexOf(idx);
        fill = BAUHAUS_ACCENTS[cardIdx] || "var(--text-primary)";
      }
      const fontWeight = face === "brutalist" ? 900 : numerals === "roman" ? 800 : 900;
      nums += `<text x="${x.toFixed(2)}" y="${y.toFixed(2)}" text-anchor="middle" dominant-baseline="central" font-size="${fontSize}" font-weight="${fontWeight}" fill="${fill}">${set[idx] ?? ""}</text>`;
    }
  }

  // AM/PM sun/moon indicator at the upper-right of the face. Sun
  // for daytime (06:00-18:00), crescent moon for night.
  let amPmIndicator = "";
  if (showAmPm) {
    const isDaytime = realHours >= 6 && realHours < 18;
    const cx = 70, cy = 26;
    if (isDaytime) {
      amPmIndicator = `
        <g transform="translate(${cx}, ${cy})">
          <circle r="3.2" fill="var(--accent-2)"/>
          <g stroke="var(--accent-2)" stroke-width="1" stroke-linecap="round">
            <line x1="0" y1="-5.2" x2="0" y2="-3.8"/>
            <line x1="0" y1="3.8" x2="0" y2="5.2"/>
            <line x1="-5.2" y1="0" x2="-3.8" y2="0"/>
            <line x1="3.8" y1="0" x2="5.2" y2="0"/>
            <line x1="-3.7" y1="-3.7" x2="-2.7" y2="-2.7"/>
            <line x1="2.7" y1="-2.7" x2="3.7" y2="-3.7"/>
            <line x1="-3.7" y1="3.7" x2="-2.7" y2="2.7"/>
            <line x1="2.7" y1="2.7" x2="3.7" y2="3.7"/>
          </g>
        </g>`;
    } else {
      amPmIndicator = `
        <g transform="translate(${cx}, ${cy})">
          <path d="M 3 -3 A 4 4 0 1 0 3 3 A 3 3 0 0 1 3 -3 Z"
                fill="var(--text-secondary)"/>
        </g>`;
    }
  }

  // Date plate at the 6 o'clock area. Skipped on skeleton + brutalist
  // because those styles deliberately strip ornament.
  const datePlateAllowed = face !== "skeleton" && face !== "brutalist";
  const datePlate = (showDate && datePlateAllowed) ? `
    <g transform="translate(50, 64)">
      <rect x="-5.5" y="-3.5" width="11" height="7"
            fill="var(--surface-sunken)"
            stroke="var(--text-muted)" stroke-width="0.5"/>
      <text x="0" y="0.4" text-anchor="middle" dominant-baseline="central"
            font-size="5" font-weight="900"
            fill="var(--text-primary)"
            font-variant-numeric="tabular-nums">${dayNum}</text>
    </g>` : "";

  const minuteColor = cfg.minuteColor || cfg.handColor;
  const handHour = hand(hourAng, 22, cfg.handWidths.hour, cfg.handColor);
  const handMin = hand(minAng, 32, cfg.handWidths.minute, minuteColor);
  const handSec = showSeconds ? hand(secAng, 36, cfg.handWidths.second, cfg.secondColor) : "";

  // Swiss railway-clock signature: a small disc near the tip of the
  // second hand. Position at ~80% of the second hand length so the
  // disc rides outside the minute hand path.
  let secondTipDisc = "";
  if (showSeconds && cfg.secondTipDisc) {
    const tipR = 34;
    const rad = (secAng * Math.PI) / 180;
    const tx = 50 + Math.cos(rad) * tipR;
    const ty = 50 + Math.sin(rad) * tipR;
    secondTipDisc = `<circle cx="${tx.toFixed(2)}" cy="${ty.toFixed(2)}" r="2.8" fill="${cfg.secondColor}"/>`;
  }

  const centerHub = cfg.centerSquare
    ? `<rect x="46.5" y="46.5" width="7" height="7" fill="${cfg.centerColor}"/>`
    : `<circle cx="50" cy="50" r="2.4" fill="${cfg.centerColor}"/>`;

  return `
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%;display:block">
      <circle cx="50" cy="50" r="45" fill="var(--surface)" stroke="none"/>
      ${ticks}
      ${nums}
      ${amPmIndicator}
      ${datePlate}
      ${handHour}
      ${handMin}
      ${handSec}
      ${secondTipDisc}
      ${centerHub}
      ${showSeconds && !cfg.centerSquare ? `<circle cx="50" cy="50" r="1.2" fill="${cfg.secondColor}"/>` : ""}
    </svg>`;
}

export default function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const showSeconds = opts.show_seconds !== false;
  const face = Object.prototype.hasOwnProperty.call(FACE_CONFIGS, opts.face) ? opts.face : "minimalist";
  const numerals = ["arabic", "roman", "dots"].includes(opts.numerals) ? opts.numerals : "arabic";
  const showDate = opts.show_date !== false;
  const showAmPm = opts.show_am_pm !== false;
  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <div class="w" data-widget="clock_analog">
      <div class="w-body" style="justify-content:center;align-items:center;padding:0">
        ${clockSvg({ showSeconds, numerals, showDate, showAmPm, face })}
      </div>
    </div>`;
}
