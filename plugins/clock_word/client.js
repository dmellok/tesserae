// clock_word — Spectra stat archetype. The English-text reading of
// the current time as a big jumbo number-style hero (e.g. "Twenty
// past three"). Pulse-dot for seconds is just a static --accent-4
// indicator since the spec forbids animation.

const MIN_WORDS = {
  0: ["", "OCLOCK"],
  5: ["FIVE PAST", ""],
  10: ["TEN PAST", ""],
  15: ["QUARTER PAST", ""],
  20: ["TWENTY PAST", ""],
  25: ["TWENTY-FIVE PAST", ""],
  30: ["HALF PAST", ""],
  35: ["TWENTY-FIVE TO", ""],
  40: ["TWENTY TO", ""],
  45: ["QUARTER TO", ""],
  50: ["TEN TO", ""],
  55: ["FIVE TO", ""],
};

const HOUR_WORD = [
  "Twelve", "One", "Two", "Three", "Four", "Five",
  "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
];

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function spelledTime(date) {
  const h = date.getHours() % 12;
  const m = date.getMinutes();
  const step = Math.floor(m / 5) * 5;
  const isTo = step > 30;
  const hourIdx = isTo ? (h + 1) % 12 : h;
  const [prefix, suffix] = MIN_WORDS[step] || ["", ""];
  const hour = HOUR_WORD[hourIdx];
  const parts = [prefix.toLowerCase(), hour, suffix.toLowerCase()].filter(Boolean);
  return parts.join(" ").trim();
}

export default function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const showDot = opts.show_seconds_dot !== false;
  const now = new Date();
  const text = spelledTime(now);
  const period = now.getHours() < 12 ? "morning" : now.getHours() < 18 ? "afternoon" : "evening";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <div class="w" data-widget="clock_word">
      <div class="w-body" style="justify-content:center;align-items:flex-start;gap:var(--space-3)">
        <div style="font-size:var(--fs-jumbo);font-weight:var(--fw-black);line-height:var(--lh-tight);letter-spacing:var(--ls-tight);color:var(--text-primary)">
          ${escapeHtml(text)}
          ${showDot ? `<span style="display:inline-block;width:.18em;height:.18em;border-radius:50%;background:var(--accent-4);vertical-align:.15em;margin-left:.15em"></span>` : ""}
        </div>
        <div style="font-size:var(--fs-body);font-weight:var(--fw-semi);color:var(--text-secondary);text-transform:uppercase;letter-spacing:var(--ls-label)">
          ${escapeHtml(period)}
        </div>
      </div>
    </div>`;
}
