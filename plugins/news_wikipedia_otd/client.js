// news_wikipedia_otd, Spectra list archetype. Wikipedia "On This
// Day" events. Each row leads with an era glyph (antiquity →
// medieval → renaissance → industrial → modern), the historical
// text, the page name as a sub-line, and an inline thumbnail when
// Wikipedia ships one. A year-timeline strip above the rows pins
// the events on a relative year axis so the day's history reads
// as a temporal shape.

// Kind id (server enum) → title-bar meta chip. Unknown ids fall back
// to the raw id uppercased, as before.
function kindLabelFor(kind, t) {
  switch (kind) {
    case "events": return t("kind_events", "EVENTS");
    case "births": return t("kind_births", "BIRTHS");
    case "deaths": return t("kind_deaths", "DEATHS");
    case "holidays": return t("kind_holidays", "HOLIDAYS");
    case "selected": return t("kind_selected", "SELECTED");
    case "all": return t("kind_all", "ALL");
    default: return String(kind).toUpperCase();
  }
}

// server.py sends today's date as "<day> <English month>" (its own UTC
// day, which is the day it fetched events for). Parse that stable shape
// back into a date and re-format it with the active locale; anything
// else is passed through untouched.
const EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
function localizeDate(label, locale) {
  const m = typeof label === "string" ? label.match(/^(\d{1,2}) ([A-Za-z]+)$/) : null;
  if (!m) return label || "";
  // The server's own "12 September" is already the English form; only
  // other locales need Intl to reorder and translate the month.
  if (!locale || String(locale).toLowerCase().startsWith("en")) return label;
  const month = EN_MONTHS.indexOf(m[2]);
  if (month < 0) return label;
  // 2024 is a leap year so 29 February round-trips.
  const d = new Date(2024, month, parseInt(m[1], 10));
  try {
    return new Intl.DateTimeFormat(locale || "en", { day: "numeric", month: "long" }).format(d);
  } catch {
    return label;
  }
}

const KIND_ACCENT = {
  events: "var(--accent-5)",
  births: "var(--accent-3)",
  deaths: "var(--accent-1)",
  holidays: "var(--accent-2)",
  selected: "var(--accent-4)",
};

const KIND_PH = {
  events: "ph-clock-counter-clockwise",
  births: "ph-baby",
  deaths: "ph-flower",
  holidays: "ph-confetti",
  selected: "ph-star",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Era → glyph by year. Five broad bands keyed to recognisable
// historical periods.
function eraGlyph(year, t) {
  if (!Number.isFinite(year)) return { icon: "ph-clock-counter-clockwise", label: "" };
  if (year < 500) return { icon: "ph-buildings", label: t("era_antiquity", "Antiquity") };
  if (year < 1500) return { icon: "ph-castle-turret", label: t("era_medieval", "Medieval") };
  if (year < 1800) return { icon: "ph-scroll", label: t("era_renaissance", "Renaissance") };
  if (year < 1900) return { icon: "ph-factory", label: t("era_industrial", "Industrial") };
  return { icon: "ph-broadcast", label: t("era_modern", "Modern") };
}

// Year-timeline strip, HTML/CSS rather than SVG so the labels stay
// proportional regardless of container width. Container has a 1px
// axis line via background, pips are absolutely positioned at
// percentage offsets, and min/mid/max year labels sit beneath.
function timelineHtml(items, accent) {
  const years = items.map((it) => Number(it.year)).filter(Number.isFinite);
  if (years.length < 2) return "";
  let min = Math.min(...years);
  let max = Math.max(...years);
  const span = Math.max(1, max - min);
  const pad = Math.max(10, span * 0.05);
  min = Math.floor((min - pad) / 10) * 10;
  max = Math.ceil((max + pad) / 10) * 10;
  const mid = Math.round((min + max) / 2);
  const range = max - min;

  const pips = items
    .filter((it) => Number.isFinite(Number(it.year)))
    .map((it) => {
      const pct = ((Number(it.year) - min) / range) * 100;
      return `<span class="otd-pip" style="left:${pct.toFixed(2)}%;background:${accent}" title="${Number(it.year)}"></span>`;
    }).join("");

  return `
    <div class="otd-tl">
      <div class="otd-tl-axis"></div>
      ${pips}
      <span class="otd-tl-label otd-tl-min">${min}</span>
      <span class="otd-tl-label otd-tl-mid">${mid}</span>
      <span class="otd-tl-label otd-tl-max">${max}</span>
    </div>`;
}

export default function render(shadow, ctx) {
  const t = ctx?.t || ((key, fallback) => fallback ?? key);
  const locale = ctx?.locale || "en";
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_wikipedia_otd">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>${escapeHtml(t("on_this_day", "On This Day"))}</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const kind = data.kind || "events";
  const dateLabel = localizeDate(data.date, locale);
  const kindLabel = kindLabelFor(kind, t);
  const onDate = `${escapeHtml(t("on", "On"))} ${escapeHtml(dateLabel)}`;
  const accent = KIND_ACCENT[kind] || "var(--accent-5)";

  if (items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="news_wikipedia_otd">
        <div class="w-title">
          <i class="ph-bold ph-clock-counter-clockwise" style="color:${accent}"></i>
          <h3>${onDate}</h3>
        </div>
        <div class="w-body"><p class="u-muted">${escapeHtml(t("nothing_recorded", "Nothing recorded."))}</p></div>
      </div>`;
    return;
  }

  const titlePh = KIND_PH[kind] || "ph-clock-counter-clockwise";

  const rows = items.map((it, i) => {
    const era = eraGlyph(Number(it.year), t);
    const thumb = it.thumb
      ? `<img class="otd-thumb" src="${escapeHtml(it.thumb)}" alt="" loading="lazy"/>`
      : "";
    return `
      <div class="otd-row ${i % 2 ? "is-zebra" : ""}">
        <div class="otd-row-lead">
          <i class="ph-bold ${era.icon}" title="${escapeHtml(era.label)}" style="color:${accent}"></i>
          <div class="otd-text">
            <span class="otd-title">${escapeHtml(it.text)}</span>
            ${it.page ? `<small class="otd-page"><i class="ph-bold ph-arrow-bend-up-right"></i>${escapeHtml(it.page)}</small>` : ""}
          </div>
          ${thumb}
        </div>
        <span class="otd-year" style="color:${accent}">${escapeHtml(String(it.year || "-"))}</span>
      </div>`;
  }).join("");

  const timeline = timelineHtml(items, accent);

  const layout = `
    /* Year-timeline strip, HTML/CSS so the labels never get
       smooshed by SVG's preserveAspectRatio="none" trick. Axis is a
       1.5px background bar at y=50%; pips sit at percentage offsets
       above the axis; year labels (min / mid / max) sit beneath the
       axis at the corresponding x-positions. */
    .otd-tl {
      position: relative;
      width: 100%;
      height: 32px;
      margin: 0 var(--space-3) var(--space-2);
    }
    .otd-tl-axis {
      position: absolute;
      left: 4px;
      right: 4px;
      top: 11px;
      height: 2px;
      border-radius: 1px;
      background: var(--surface-sunken);
    }
    .otd-pip {
      position: absolute;
      top: 6px;
      width: 8px;
      height: 8px;
      margin-left: -4px;
      border-radius: 50%;
      box-shadow: 0 0 0 1.5px var(--surface);
    }
    .otd-tl-label {
      position: absolute;
      bottom: 0;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      color: var(--text-muted);
      letter-spacing: 0;
      font-variant-numeric: tabular-nums;
    }
    .otd-tl-min { left: 4px; }
    .otd-tl-mid { left: 50%; transform: translateX(-50%); }
    .otd-tl-max { right: 4px; }
    .otd-row {
      display: flex;
      align-items: flex-start;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .otd-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .otd-row-lead {
      flex: 1 1 auto;
      display: flex;
      align-items: flex-start;
      gap: var(--space-2);
      min-width: 0;
    }
    .otd-text {
      flex: 1 1 auto;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .otd-title {
      line-height: 1.25;
    }
    .otd-page {
      display: flex;
      align-items: center;
      gap: 3px;
      color: var(--text-muted);
      font-weight: var(--fw-semi);
      font-size: .72em;
      line-height: 1.1;
    }
    .otd-page i {
      font-size: .95em;
    }
    .otd-thumb {
      width: 3em;
      height: 3em;
      object-fit: cover;
      border-radius: var(--radius-1);
      flex: 0 0 auto;
      background: var(--surface-sunken);
    }
    .otd-year {
      font-weight: var(--fw-black);
      font-size: var(--fs-lead);
      font-variant-numeric: tabular-nums;
      flex: 0 0 auto;
      align-self: flex-start;
    }
    @container (max-width: 320px) {
      .otd-thumb { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="news_wikipedia_otd">
      <div class="w-title">
        <i class="ph-bold ${titlePh}" style="color:${accent}"></i>
        <h3>${onDate}</h3>
        <span class="w-title-meta">${escapeHtml(kindLabel)}</span>
      </div>
      <div class="w-body" style="gap:0">
        ${timeline || ""}
        <div class="list-body" style="display:flex;flex-direction:column;flex:1 1 auto">${rows}</div>
      </div>
    </div>`;
}
