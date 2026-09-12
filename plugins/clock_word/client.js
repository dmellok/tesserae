// clock_word, Spectra stat archetype. The English-text reading of
// the current time as a big jumbo number-style hero (e.g. "Twenty
// past three"). The hero sits on a phase-of-day tone (a soft tinted
// background that warms / cools by time of day) and is paired with a
// phase badge, Phosphor glyph + label, that reads "morning /
// afternoon / evening / night". Pulse-dot for seconds is just a
// static --accent-4 indicator since the spec forbids animation.

// Phrasing lives behind ctx.t() so a translator can localise the
// whole sentence. Each five-minute step is a template with a {hour}
// slot; the spelled hour word (already shifted to the next hour for
// the "to" half) is substituted in, so a locale that says the hour
// first ("trois heures cinq") just moves the slot. Tokens stay
// lowercase, spelledTime() capitalises the first letter of the
// assembled sentence so it reads "Twenty past three".
function stepTemplates(t) {
  return {
    0: t("w_oclock", "{hour} o'clock"),
    5: t("w_five_past", "five past {hour}"),
    10: t("w_ten_past", "ten past {hour}"),
    15: t("w_quarter_past", "quarter past {hour}"),
    20: t("w_twenty_past", "twenty past {hour}"),
    25: t("w_twenty_five_past", "twenty-five past {hour}"),
    30: t("w_half_past", "half past {hour}"),
    35: t("w_twenty_five_to", "twenty-five to {hour}"),
    40: t("w_twenty_to", "twenty to {hour}"),
    45: t("w_quarter_to", "quarter to {hour}"),
    50: t("w_ten_to", "ten to {hour}"),
    55: t("w_five_to", "five to {hour}"),
  };
}

function hourWords(t) {
  return [
    t("w_twelve", "twelve"), t("w_one", "one"), t("w_two", "two"),
    t("w_three", "three"), t("w_four", "four"), t("w_five", "five"),
    t("w_six", "six"), t("w_seven", "seven"), t("w_eight", "eight"),
    t("w_nine", "nine"), t("w_ten", "ten"), t("w_eleven", "eleven"),
  ];
}

// Phase-of-day table. Each phase has a label, an icon, and an accent
// token + tint mix percent for the background tone. Boundaries are
// the canonical solar transitions: dawn 5, noon 12, dusk 17, night 21.
const PHASES = [
  { from: 0,  to: 5,  key: "night",     icon: "ph-moon-stars", accent: "var(--accent-5)", tint: 10 },
  { from: 5,  to: 12, key: "morning",   icon: "ph-sun-horizon", accent: "var(--accent-2)", tint: 8 },
  { from: 12, to: 17, key: "afternoon", icon: "ph-sun",         accent: "var(--accent-3)", tint: 7 },
  { from: 17, to: 21, key: "evening",   icon: "ph-sun-horizon", accent: "var(--accent-1)", tint: 9 },
  { from: 21, to: 24, key: "night",     icon: "ph-moon",        accent: "var(--accent-5)", tint: 10 },
];

function phaseFor(hour) {
  return PHASES.find((p) => hour >= p.from && hour < p.to) || PHASES[0];
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function spelledTime(date, t) {
  const h = date.getHours() % 12;
  const m = date.getMinutes();
  const step = Math.floor(m / 5) * 5;
  const isTo = step > 30;
  const hourIdx = isTo ? (h + 1) % 12 : h;
  const template = stepTemplates(t)[step] || "{hour}";
  const words = hourWords(t);
  const hour = words[hourIdx];
  // Templates may also use {this} (the hour just gone) and {next}
  // (the hour to come) regardless of the step: languages that tell
  // 3:30 as "half four" (German, Dutch, Norwegian, Swedish, Czech)
  // write "halb {next}" for the 30 step, where {hour} would still be
  // "three".
  const thisHour = words[h];
  const nextHour = words[(h + 1) % 12];
  // Capitalise the very first letter of the assembled sentence so
  // the output reads "Twenty past three" rather than "twenty past
  // three".
  const sentence = template
    .replace("{hour}", hour)
    .replace("{this}", thisHour)
    .replace("{next}", nextHour)
    .trim();
  return sentence ? sentence.charAt(0).toUpperCase() + sentence.slice(1) : "";
}

export default function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const t = ctx?.t || ((key, fallback) => fallback ?? key);
  const showDot = opts.show_seconds_dot !== false;
  const showTone = opts.show_tone !== false;
  const showPhaseBadge = opts.show_phase_badge !== false;
  const now = new Date();
  const text = spelledTime(now, t);
  const phase = phaseFor(now.getHours());
  const PHASE_LABELS = {
    night: t("night", "Night"),
    morning: t("morning", "Morning"),
    afternoon: t("afternoon", "Afternoon"),
    evening: t("evening", "Evening"),
  };

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
        <span>${escapeHtml(PHASE_LABELS[phase.key] || phase.key)}</span>
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
