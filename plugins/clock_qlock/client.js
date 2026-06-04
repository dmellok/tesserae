// clock_qlock — Spectra-styled QLOCKTWO-inspired letter clock. Renders
// an 11-column letter grid; the words for the current time light up at
// --text-primary (or the chosen accent), unlit letters use --text-muted.
// Optional corner dots add per-minute precision (1-4 minutes past the
// current 5-minute step).

const GRID = [
  "ITLISASTIME",
  "ACQUARTERDC",
  "TWENTYFIVEX",
  "HALFSTENFTO",
  "PASTERUNINE",
  "ONESIXTHREE",
  "FOURFIVETWO",
  "EIGHTELEVEN",
  "SEVENTWELVE",
  "TENSEOCLOCK",
];

const WORDS = {
  IT: [[0, 0], [0, 1]],
  IS: [[0, 3], [0, 4]],
  TEN_M: [[3, 5], [3, 6], [3, 7]],
  QUARTER: [[1, 2], [1, 3], [1, 4], [1, 5], [1, 6], [1, 7], [1, 8]],
  TWENTY: [[2, 0], [2, 1], [2, 2], [2, 3], [2, 4], [2, 5]],
  FIVE_M: [[2, 6], [2, 7], [2, 8], [2, 9]],
  HALF: [[3, 0], [3, 1], [3, 2], [3, 3]],
  PAST: [[4, 0], [4, 1], [4, 2], [4, 3]],
  TO: [[3, 9], [3, 10]],
  ONE: [[5, 0], [5, 1], [5, 2]],
  TWO: [[6, 8], [6, 9], [6, 10]],
  THREE: [[5, 6], [5, 7], [5, 8], [5, 9], [5, 10]],
  FOUR: [[6, 0], [6, 1], [6, 2], [6, 3]],
  FIVE_H: [[6, 4], [6, 5], [6, 6], [6, 7]],
  SIX: [[5, 3], [5, 4], [5, 5]],
  SEVEN: [[8, 0], [8, 1], [8, 2], [8, 3], [8, 4]],
  EIGHT: [[7, 0], [7, 1], [7, 2], [7, 3], [7, 4]],
  NINE: [[4, 7], [4, 8], [4, 9], [4, 10]],
  TEN_H: [[9, 0], [9, 1], [9, 2]],
  ELEVEN: [[7, 5], [7, 6], [7, 7], [7, 8], [7, 9], [7, 10]],
  TWELVE: [[8, 5], [8, 6], [8, 7], [8, 8], [8, 9], [8, 10]],
  OCLOCK: [[9, 5], [9, 6], [9, 7], [9, 8], [9, 9], [9, 10]],
};

const ACCENT_TOKEN = {
  fg: "var(--text-primary)",
  accent: "var(--accent-4)",
  accent2: "var(--accent-5)",
  accent3: "var(--accent-3)",
};

function activeWords(date) {
  const h = date.getHours() % 12;
  const m = date.getMinutes();
  const minStep = Math.floor(m / 5) * 5;
  const isTo = minStep > 30;
  const hourIdx = isTo ? (h + 1) % 12 : h;

  const out = ["IT", "IS"];
  const MIN_WORDS = {
    0: [], 5: ["FIVE_M", "PAST"], 10: ["TEN_M", "PAST"],
    15: ["QUARTER", "PAST"], 20: ["TWENTY", "PAST"],
    25: ["TWENTY", "FIVE_M", "PAST"], 30: ["HALF", "PAST"],
    35: ["TWENTY", "FIVE_M", "TO"], 40: ["TWENTY", "TO"],
    45: ["QUARTER", "TO"], 50: ["TEN_M", "TO"], 55: ["FIVE_M", "TO"],
  };
  out.push(...(MIN_WORDS[minStep] || []));

  const HOUR_WORD = ["TWELVE", "ONE", "TWO", "THREE", "FOUR", "FIVE_H", "SIX", "SEVEN", "EIGHT", "NINE", "TEN_H", "ELEVEN"];
  out.push(HOUR_WORD[hourIdx]);
  if (minStep === 0) out.push("OCLOCK");
  return out;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const litColor = ACCENT_TOKEN[opts.lit_color] || ACCENT_TOKEN.fg;
  const showCorners = opts.show_corners !== false;

  const now = new Date();
  const active = new Set(activeWords(now));
  const lit = new Set();
  for (const w of active) {
    for (const [r, c] of WORDS[w]) lit.add(`${r}:${c}`);
  }

  // Build the grid as a single CSS grid: 11 columns × 10 rows, each
  // cell exactly 1fr wide + monospace, so words line up vertically
  // across rows like the real QLOCKTWO. Unlit letters drop to 22%
  // opacity so the lit words really pop.
  const cells = [];
  for (let r = 0; r < GRID.length; r++) {
    for (let c = 0; c < GRID[r].length; c++) {
      const isLit = lit.has(`${r}:${c}`);
      const color = isLit ? litColor : "var(--text-primary)";
      const weight = isLit ? "var(--fw-black)" : "var(--fw-bold)";
      const opacity = isLit ? 1 : 0.22;
      cells.push(`<span style="color:${color};font-weight:${weight};opacity:${opacity};text-align:center;line-height:1">${escapeHtml(GRID[r][c])}</span>`);
    }
  }

  const minMod5 = now.getMinutes() % 5;
  const dot = (active) => `<span style="width:.5em;height:.5em;border-radius:50%;background:${active ? litColor : "var(--text-muted)"};opacity:${active ? 1 : .25}"></span>`;
  const corners = showCorners
    ? `
      <div style="position:absolute;inset:.5em;pointer-events:none">
        <div style="position:absolute;top:0;left:0">${dot(minMod5 >= 1)}</div>
        <div style="position:absolute;top:0;right:0">${dot(minMod5 >= 2)}</div>
        <div style="position:absolute;bottom:0;right:0">${dot(minMod5 >= 3)}</div>
        <div style="position:absolute;bottom:0;left:0">${dot(minMod5 >= 4)}</div>
      </div>`
    : "";

  // Grid square: pick the smaller of cqw/cqh and stay centred so the
  // 11×10 letter matrix keeps a near-square aspect on any cell shape.
  // Letter-spacing 0 and monospace so all letters are uniform width.
  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <div class="w" data-widget="clock_qlock">
      <div class="w-body" style="justify-content:center;align-items:center;position:relative">
        <div style="
          display:grid;
          grid-template-columns:repeat(11, 1fr);
          grid-auto-rows:1fr;
          width:min(100%, calc(100cqh * 11 / 10));
          aspect-ratio:11 / 10;
          font-family:'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
          font-size:clamp(0.75em, 8cqmin, 1.8em);
          letter-spacing:0;
          line-height:1;
          place-items:center
        ">
          ${cells.join("")}
        </div>
        ${corners}
      </div>
    </div>`;
}
