// clock_word — "It is half past three." Rounds to nearest 5 minutes.

const HOURS = [
  "Twelve", "One", "Two", "Three", "Four", "Five",
  "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
];
const MINUTES = {
  0:  "o'clock",
  5:  "Five past",
  10: "Ten past",
  15: "Quarter past",
  20: "Twenty past",
  25: "Twenty-five past",
  30: "Half past",
  35: "Twenty-five to",
  40: "Twenty to",
  45: "Quarter to",
  50: "Ten to",
  55: "Five to",
};

function phrase(d) {
  let h = d.getHours();
  let m = Math.round(d.getMinutes() / 5) * 5;
  if (m === 60) { m = 0; h += 1; }
  const mPhrase = MINUTES[m] || "";
  // For "past" minutes the hour stays the same; for "to" minutes the
  // hour advances by 1.
  const hourIdx = (m >= 35 ? h + 1 : h) % 12;
  const hourWord = HOURS[hourIdx];
  if (m === 0) {
    return { lead: hourWord, tail: mPhrase };
  }
  return { lead: mPhrase, tail: hourWord };
}

export default async function render(shadow, ctx) {
  const showDot = ctx.cell.options.show_seconds_dot !== false;
  const size = ctx.cell.size;

  function paint() {
    const d = new Date();
    const p = phrase(d);
    return `
      <div class="cw-it">It is</div>
      <div class="cw-lead">${p.lead}${showDot ? '<span class="cw-dot"></span>' : ""}</div>
      <div class="cw-tail">${p.tail}</div>
    `;
  }

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/plugins/clock_word/client.css">
    <div class="root size-${size}" data-cw>${paint()}</div>
  `;

  function tick() {
    const root = shadow.querySelector("[data-cw]");
    if (root) root.innerHTML = paint();
  }
  if (shadow.__cwTimer) clearInterval(shadow.__cwTimer);
  shadow.__cwTimer = setInterval(tick, 30000);
}
