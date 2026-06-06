// clock_word, Spectra stat archetype. The English-text reading of
// the current time as a big jumbo number-style hero (e.g. "Twenty
// past three"). The hero sits on a phase-of-day tone (a soft tinted
// background that warms / cools by time of day) and is paired with a
// phase badge, Phosphor glyph + label, that reads "morning /
// afternoon / evening / night". Pulse-dot for seconds is just a
// static --accent-4 indicator since the spec forbids animation.

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

// Phase-of-day table. Each phase has a label, an icon, and an accent
// token + tint mix percent for the background tone. Boundaries are
// the canonical solar transitions: dawn 5, noon 12, dusk 17, night 21.
const PHASES = [
  { from: 0,  to: 5,  key: "night",     label: "Night",     icon: "ph-moon-stars", accent: "var(--accent-5)", tint: 10 },
  { from: 5,  to: 12, key: "morning",   label: "Morning",   icon: "ph-sun-horizon", accent: "var(--accent-2)", tint: 8 },
  { from: 12, to: 17, key: "afternoon", label: "Afternoon", icon: "ph-sun",         accent: "var(--accent-3)", tint: 7 },
  { from: 17, to: 21, key: "evening",   label: "Evening",   icon: "ph-sun-horizon", accent: "var(--accent-1)", tint: 9 },
  { from: 21, to: 24, key: "night",     label: "Night",     icon: "ph-moon",        accent: "var(--accent-5)", tint: 10 },
];

function phaseFor(hour) {
  return PHASES.find((p) => hour >= p.from && hour < p.to) || PHASES[0];
}

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
  const showTone = opts.show_tone !== false;
  const showPhaseBadge = opts.show_phase_badge !== false;
  const now = new Date();
  const text = spelledTime(now);
  const phase = phaseFor(now.getHours());

  // Day/night tone, a soft radial gradient anchored at top-left
  // (where the sun would sit for that phase) that tracks the phase
  // accent. Stays subtle so the text remains the focal element; we
  // mix the accent into the surface at the phase's `tint` percent and
  // fall back to the plain surface in the bottom-right corner.
  const widgetBackground = showTone
    ? `radial-gradient(ellipse at 30% 25%,
        color-mix(in oklab, ${phase.accent} ${phase.tint}%, var(--surface)) 0%,
        var(--surface) 75%)`
    : "var(--surface)";

  const phaseBadge = showPhaseBadge
    ? `
      <div class="phase-badge">
        <i class="ph-bold ${phase.icon}" style="color:${phase.accent}"></i>
        <span>${escapeHtml(phase.label)}</span>
      </div>`
    : "";

  const layout = `
    .w[data-widget="clock_word"] {
      background: ${widgetBackground};
    }
    .word-hero {
      font-size: var(--fs-jumbo);
      font-weight: var(--fw-black);
      line-height: var(--lh-tight);
      letter-spacing: var(--ls-tight);
      color: var(--text-primary);
    }
    .phase-badge {
      display: inline-flex;
      align-items: center;
      gap: var(--space-2);
      padding: var(--space-1) var(--space-3);
      border-radius: 999px;
      background: color-mix(in oklab, ${phase.accent} 14%, var(--surface));
      color: ${phase.accent};
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      text-transform: uppercase;
      letter-spacing: var(--ls-label);
      align-self: flex-start;
    }
    .phase-badge i {
      font-size: 1.1em;
    }
    .seconds-dot {
      display: inline-block;
      width: .18em;
      height: .18em;
      border-radius: 50%;
      background: ${phase.accent};
      vertical-align: .15em;
      margin-left: .15em;
    }
  `;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <style>${layout}</style>
    <div class="w" data-widget="clock_word">
      <div class="w-body" style="justify-content:center;align-items:flex-start;gap:var(--space-3)">
        <div class="word-hero">
          ${escapeHtml(text)}
          ${showDot ? `<span class="seconds-dot"></span>` : ""}
        </div>
        ${phaseBadge}
      </div>
    </div>`;
}
