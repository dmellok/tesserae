// clock_qlock — QLOCKTWO-style letter-grid clock.
//
// An 11-wide × 10-tall matrix of letters. Words for the current
// time light up; everything else stays dim. Four corner dots count
// minutes past the current 5-minute step (1-4) for ±1 minute
// precision. Pure client-side; ticks every 15 seconds.

// The canonical QLOCKTWO English layout. Every row is 11 characters.
const GRID = [
  "ITLISASAMPM", // 0: IT (0-1), IS (3-4)
  "ACQUARTERDC", // 1: A (0), QUARTER (2-8)
  "TWENTYFIVEX", // 2: TWENTY (0-5), FIVE_MIN (6-9)
  "HALFSTENFTO", // 3: HALF (0-3), TEN_MIN (5-7), TO (9-10)
  "PASTERUNINE", // 4: PAST (0-3), NINE (7-10)
  "ONESIXTHREE", // 5: ONE (0-2), SIX (3-5), THREE (6-10)
  "FOURFIVETWO", // 6: FOUR (0-3), FIVE_HR (4-7), TWO (8-10)
  "EIGHTELEVEN", // 7: EIGHT (0-4), ELEVEN (5-10)
  "SEVENTWELVE", // 8: SEVEN (0-4), TWELVE (5-10)
  "TENSEOCLOCK", // 9: TEN_HR (0-2), OCLOCK (5-10)
];

// (row, col, length) — a contiguous run of letters that spells one word.
const WORDS = {
  IT:       [0, 0, 2],
  IS:       [0, 3, 2],
  A:        [1, 0, 1],
  QUARTER:  [1, 2, 7],
  TWENTY:   [2, 0, 6],
  FIVE_MIN: [2, 6, 4],
  HALF:     [3, 0, 4],
  TEN_MIN:  [3, 5, 3],
  TO:       [3, 9, 2],
  PAST:     [4, 0, 4],
  NINE:     [4, 7, 4],
  ONE:      [5, 0, 3],
  SIX:      [5, 3, 3],
  THREE:    [5, 6, 5],
  FOUR:     [6, 0, 4],
  FIVE_HR:  [6, 4, 4],
  TWO:      [6, 8, 3],
  EIGHT:    [7, 0, 5],
  ELEVEN:   [7, 5, 6],
  SEVEN:    [8, 0, 5],
  TWELVE:   [8, 5, 6],
  TEN_HR:   [9, 0, 3],
  OCLOCK:   [9, 5, 6],
};

// Index 0..11 -> word id. 0 = TWELVE for midnight + noon.
const HOUR_WORDS = [
  "TWELVE", "ONE",   "TWO",   "THREE",
  "FOUR",   "FIVE_HR", "SIX",  "SEVEN",
  "EIGHT",  "NINE",  "TEN_HR", "ELEVEN",
];

// For each 5-minute step: which minute words light up, and whether
// the hour rolls to the next one ("twenty-five to" five = 4:35).
const MINUTE_BAND = {
  0:  { words: ["OCLOCK"],                            next: false },
  5:  { words: ["FIVE_MIN", "PAST"],                  next: false },
  10: { words: ["TEN_MIN", "PAST"],                   next: false },
  15: { words: ["A", "QUARTER", "PAST"],              next: false },
  20: { words: ["TWENTY", "PAST"],                    next: false },
  25: { words: ["TWENTY", "FIVE_MIN", "PAST"],        next: false },
  30: { words: ["HALF", "PAST"],                      next: false },
  35: { words: ["TWENTY", "FIVE_MIN", "TO"],          next: true },
  40: { words: ["TWENTY", "TO"],                      next: true },
  45: { words: ["A", "QUARTER", "TO"],                next: true },
  50: { words: ["TEN_MIN", "TO"],                     next: true },
  55: { words: ["FIVE_MIN", "TO"],                    next: true },
};

function litCells(date) {
  const lit = new Set();
  const light = (id) => {
    const [r, c, len] = WORDS[id];
    for (let i = 0; i < len; i++) lit.add(`${r}-${c + i}`);
  };
  light("IT");
  light("IS");

  // Round down to the nearest 5 minutes for the band lookup, then
  // capture the remainder for the corner-dot precision indicator.
  const realMins = date.getMinutes();
  const step = Math.floor(realMins / 5) * 5;
  const remainder = realMins - step;
  const band = MINUTE_BAND[step];
  band.words.forEach(light);

  let hourIdx = date.getHours() % 12;
  if (band.next) hourIdx = (hourIdx + 1) % 12;
  light(HOUR_WORDS[hourIdx]);

  return { lit, remainder };
}

export default async function render(shadow, ctx) {
  const size = ctx.cell.size;
  const opts = ctx.cell.options || {};
  const litColor = opts.lit_color || "fg";
  const showCorners = opts.show_corners !== false;

  function paint() {
    const d = new Date();
    const { lit, remainder } = litCells(d);
    let html = "";
    for (let r = 0; r < GRID.length; r++) {
      const row = GRID[r];
      for (let c = 0; c < row.length; c++) {
        const on = lit.has(`${r}-${c}`);
        html += `<span class="qk-cell${on ? " is-lit" : ""}">${row[c]}</span>`;
      }
    }
    const corners = showCorners
      ? `
        <span class="qk-corner qk-corner--tl ${remainder >= 1 ? 'is-lit' : ''}"></span>
        <span class="qk-corner qk-corner--tr ${remainder >= 2 ? 'is-lit' : ''}"></span>
        <span class="qk-corner qk-corner--bl ${remainder >= 4 ? 'is-lit' : ''}"></span>
        <span class="qk-corner qk-corner--br ${remainder >= 3 ? 'is-lit' : ''}"></span>
      `
      : "";
    return { grid: html, corners };
  }

  const { grid, corners } = paint();
  shadow.innerHTML = `
    <link rel="stylesheet" href="/plugins/clock_qlock/client.css">
    <div class="root size-${size} lit-${litColor}">
      <div class="qk-stage">
        <div class="qk-grid" data-qk-grid>${grid}</div>
        <div class="qk-corners" data-qk-corners>${corners}</div>
      </div>
    </div>
  `;

  function tick() {
    const result = paint();
    const gridEl = shadow.querySelector("[data-qk-grid]");
    const cornersEl = shadow.querySelector("[data-qk-corners]");
    if (gridEl) gridEl.innerHTML = result.grid;
    if (cornersEl) cornersEl.innerHTML = result.corners;
  }
  if (shadow.__qkTimer) clearInterval(shadow.__qkTimer);
  shadow.__qkTimer = setInterval(tick, 15000);
}
