// ai_brief, editorial text card.
//
// Header carries the configured label + a small model badge; body is
// the LLM-written brief with a drop cap on the first letter, a left
// accent rail, and automatic accent-coloured numbers (°C, %, time,
// etc). Footer gets a sparkle-flanked horizontal rule + model/age
// meta. Error states swap the body for a muted "configure me" message
// so a brand-new install still renders something coherent before the
// API key is set.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function relativeAge(iso) {
  if (!iso) return "";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "";
  const diff = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (diff < 60) return "just now";
  const mins = Math.round(diff / 60);
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function modelBadge(model) {
  if (!model) return "";
  const m = model.match(/claude-(opus|sonnet|haiku|fable)-(\d+(?:-\d+)?)/i);
  if (m) return `${m[1].toUpperCase()} ${m[2].replace("-", ".")}`;
  return model.toUpperCase();
}

// Wrap numeric tokens in a span so we can accent-colour them in CSS.
// Catches: 14, 14.5, 14°C, 14°F, 14%, 9:30, 21°, 4 events, etc.
// Render order: escape the whole brief FIRST, then apply this regex
// against the escaped string. The lookbehind excludes word chars,
// ``&`` (entity start), ``#`` (numeric-entity prefix like ``&#39;``),
// and ``;`` (entity end) so digits inside HTML entities (e.g. the
// 39 in ``&#39;``) never get wrapped.
function highlightNumbers(briefHtml) {
  return briefHtml.replace(
    /(?<![\w&#;])(\d+(?:\.\d+)?(?:[:.]\d+)?(?:°[CF]?|%|km|mi|kg|lb|hrs?|min|m)?)/g,
    '<span class="num">$1</span>'
  );
}

// Wrap the first letter (post-leading-whitespace) in a span so CSS
// can render the drop cap. Operates on escaped HTML; finds the first
// alphabetic char after any tags or whitespace.
function dropCap(html) {
  return html.replace(/^(\s*)(\p{L})/u, (_, ws, ch) =>
    `${ws}<span class="drop-cap">${ch}</span>`
  );
}

const LAYOUT = `
.w[data-widget="ai_brief"] {
  display: grid;
  grid-template-rows: auto 1fr auto;
  height: 100%;
  padding: 1em 1.2em 0.9em;
  gap: 0.7em;
  background: var(--bg);
  color: var(--text);
  position: relative;
}
.w[data-widget="ai_brief"]::before {
  content: "";
  position: absolute;
  left: 1.2em;
  top: 3.4em;
  bottom: 3em;
  width: 3px;
  background: var(--accent-3);
  border-radius: 2px;
  opacity: 0.85;
}

.ai-brief-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6em;
  font-family: var(--font-mono, monospace);
  font-size: 0.7em;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}
.ai-brief-head .lead {
  display: inline-flex;
  align-items: center;
  gap: 0.5em;
  color: var(--accent-3);
  font-weight: 700;
}
.ai-brief-head .lead i { font-size: 1.45em; }
.ai-brief-head .lead .lead-label {
  border-bottom: 2px solid var(--accent-3);
  padding-bottom: 1px;
}
.ai-brief-head .meta {
  color: var(--text-muted);
  font-weight: 600;
  letter-spacing: 0.1em;
}
.ai-brief-head .meta .badge {
  background: var(--surface);
  padding: 0.22em 0.55em;
  border-radius: 0.35em;
  margin-right: 0.5em;
  color: var(--text);
  letter-spacing: 0.16em;
  border: 1px solid var(--accent-3);
}

.ai-brief-body {
  display: flex;
  align-items: center;
  font-family: var(--font-serif, Georgia, "Iowan Old Style", serif);
  font-size: 1.1em;
  line-height: 1.5;
  font-weight: 500;
  color: var(--text);
  padding-left: 0.9em;
  hyphens: auto;
}
.ai-brief-body p { margin: 0; }
.ai-brief-body .drop-cap {
  float: left;
  font-size: 3em;
  line-height: 0.95;
  font-weight: 700;
  margin: 0.05em 0.12em 0 0;
  color: var(--accent-3);
  font-family: var(--font-serif, Georgia, "Iowan Old Style", serif);
}
.ai-brief-body .num {
  color: var(--accent-3);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.ai-brief-body.is-error,
.ai-brief-body.is-empty {
  color: var(--text-muted);
  font-family: var(--font-mono, monospace);
  font-style: normal;
  font-size: 0.95em;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  text-align: center;
  gap: 0.5em;
  padding-left: 0;
}
.w[data-widget="ai_brief"]:has(.ai-brief-body.is-error)::before,
.w[data-widget="ai_brief"]:has(.ai-brief-body.is-empty)::before {
  display: none;
}
.ai-brief-body.is-error i,
.ai-brief-body.is-empty i {
  font-size: 2.6em;
  color: var(--accent-4);
}

.ai-brief-foot {
  display: flex;
  align-items: center;
  gap: 0.6em;
  color: var(--text-muted);
  font-family: var(--font-mono, monospace);
  font-size: 0.6em;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.ai-brief-foot::before,
.ai-brief-foot::after {
  content: "";
  flex: 1 1 auto;
  height: 1px;
  background: linear-gradient(to right, transparent, var(--text-muted), transparent);
  opacity: 0.4;
}
.ai-brief-foot i {
  color: var(--accent-3);
  font-size: 1.2em;
}
`;

function errorCard(label, message) {
  return `
    <div class="w" data-widget="ai_brief">
      <div class="ai-brief-head">
        <span class="lead"><i class="ph-bold ph-sparkle"></i><span class="lead-label">${escapeHtml(label)}</span></span>
      </div>
      <div class="ai-brief-body is-error">
        <i class="ph-bold ph-warning-circle"></i>
        <p>${escapeHtml(message)}</p>
      </div>
      <div class="ai-brief-foot"><i class="ph-fill ph-sparkle"></i></div>
    </div>`;
}

function emptyCard(label) {
  return `
    <div class="w" data-widget="ai_brief">
      <div class="ai-brief-head">
        <span class="lead"><i class="ph-bold ph-sparkle"></i><span class="lead-label">${escapeHtml(label)}</span></span>
      </div>
      <div class="ai-brief-body is-empty">
        <i class="ph-bold ph-sparkle"></i>
        <p>Waiting for the first generation...</p>
      </div>
      <div class="ai-brief-foot"><i class="ph-fill ph-sparkle"></i></div>
    </div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;
  const style = `<style>${LAYOUT}</style>`;
  const label = data.header_label || "BRIEF";

  if (data.error) {
    shadow.innerHTML = `${css}${style}${errorCard(label, data.error)}`;
    return;
  }
  const brief = (data.brief || "").trim();
  if (!brief) {
    shadow.innerHTML = `${css}${style}${emptyCard(label)}`;
    return;
  }

  const badge = modelBadge(data.model);
  const age = relativeAge(data.generated_at);
  const meta = [badge && `<span class="badge">${escapeHtml(badge)}</span>`, age]
    .filter(Boolean)
    .join("");

  const footMeta = [age, badge].filter(Boolean).map(escapeHtml).join(" · ");

  // Escape, then highlight numbers, then add the drop cap. Order matters:
  // escapeHtml runs first so the regexes never see raw HTML.
  let bodyHtml = escapeHtml(brief);
  bodyHtml = highlightNumbers(bodyHtml);
  bodyHtml = dropCap(bodyHtml);

  shadow.innerHTML = `
    ${css}${style}
    <div class="w" data-widget="ai_brief">
      <div class="ai-brief-head">
        <span class="lead"><i class="ph-bold ph-sparkle"></i><span class="lead-label">${escapeHtml(label)}</span></span>
        <span class="meta">${meta}</span>
      </div>
      <div class="ai-brief-body">
        <p>${bodyHtml}</p>
      </div>
      <div class="ai-brief-foot">
        <i class="ph-fill ph-sparkle"></i>
        <span>${footMeta}</span>
        <i class="ph-fill ph-sparkle"></i>
      </div>
    </div>`;
}
