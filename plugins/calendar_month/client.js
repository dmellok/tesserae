// calendar_month, Spectra month-grid. Each day cell carries:
//
//   - A heat tint on the cell background scaled by event count, so a
//     glance over the grid reveals which days are stacked vs quiet.
//   - Up to 4 feed-colour micro-strips at the bottom of the cell -
//     one per unique feed that has events that day, so it reads
//     "this day has work + personal + bills" without spelling it out.
//   - A +N chip when the day's event count exceeds what the visible
//     strips / text rows could show.
//
// Today's cell keeps the filled accent-1 block behind the day number;
// out-of-month days fade via the existing is-out class.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DOW_SUN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// Dedupe feed colours preserving first-seen order. The cell shows
// one strip per unique colour rather than one per event, so a day
// with three "work" meetings doesn't paint three identical blue
// strips, it paints one wide blue strip alongside whatever other
// feeds the day touches.
function uniqueColours(events) {
  const seen = new Set();
  const out = [];
  for (const ev of events) {
    const c = ev.colour || "var(--accent-4)";
    if (!seen.has(c)) {
      seen.add(c);
      out.push(c);
    }
  }
  return out;
}

// Heat-tint background, scales the cell's background from surface
// (0 events) up to a `color-mix` overlay of accent-4 (teal) at
// increasing alpha. Capped at 5+ events so a single day with 20
// meetings doesn't drown out the rest of the grid's distinctions.
function heatBackground(count) {
  if (count <= 0) return "";
  const intensity = Math.min(1, count / 5);
  const alpha = 6 + 18 * intensity;
  return `background: color-mix(in oklab, var(--accent-4) ${alpha.toFixed(0)}%, var(--surface));`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const display = opts.event_display === "text" ? "text" : "bars";
  const maxPerDay = Math.max(1, Number(opts.max_events_per_day) || 3);
  // Heat-tint on by default; cell option flips it off for users who
  // want the month-grid to read as a uniform field of cells instead
  // of "busy days darker".
  const heatmap = opts.heatmap !== false;
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="calendar_month">
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const days = Array.isArray(data.days) ? data.days : [];
  const dowNames = (data.week_start === "sunday") ? DOW_SUN : DOW;
  const monthName = (data.month_name || "").toUpperCase();
  const year = data.year || "";
  const weekStartLabel = (data.week_start === "sunday") ? "WEEK STARTS SUN" : "WEEK STARTS MON";

  const dowHeader = dowNames.map((name) => `<span>${escapeHtml(name)}</span>`).join("");

  const cells = days.map((d) => {
    const classes = ["mc-cell"];
    if (!d.in_month) classes.push("is-out");
    if (d.is_today) classes.push("is-today");

    const events = Array.isArray(d.events) ? d.events : [];
    const count = events.length;
    const heatStyle = (heatmap && d.in_month) ? heatBackground(count) : "";

    let body = "";
    if (display === "text") {
      // Original "title text with feed colour bar" rendering kept
      // intact so existing dashboards with that cell option stay
      // visually consistent.
      const visible = events.slice(0, maxPerDay);
      const remainder = Math.max(0, count - maxPerDay);
      body = `
        ${visible.map((ev) => {
          const colour = ev.colour || "var(--accent-4)";
          return `<span class="mc-text" style="border-left-color:${colour}" title="${escapeHtml(ev.summary || "")}">${escapeHtml(ev.summary || "")}</span>`;
        }).join("")}
        ${remainder > 0 ? `<span class="mc-more">+${remainder}</span>` : ""}`;
    } else {
      // bars mode (default), strips per unique feed-colour. Cap at
      // 4 strips so a day with 7 distinct feeds doesn't paint a
      // rainbow stack that pushes the day number out of frame; the
      // rest are summarised by the +N chip.
      const colours = uniqueColours(events);
      const visibleColours = colours.slice(0, 4);
      const overflow = colours.length - visibleColours.length;
      const stripsRow = visibleColours.length
        ? `<div class="mc-strips">
            ${visibleColours.map((c) => `<span class="mc-strip" style="background:${c}"></span>`).join("")}
          </div>`
        : "";
      const moreChip = (count > visibleColours.length || overflow > 0)
        ? `<span class="mc-more">${count}</span>`
        : "";
      body = `${stripsRow}${moreChip}`;
    }

    return `
      <div class="${classes.join(" ")}" style="${heatStyle}">
        <span class="mc-num">${escapeHtml(String(d.day))}</span>
        <div class="mc-dots">${body}</div>
      </div>`;
  }).join("");

  // Inline layout for the visual-pass additions (heat tints handled
  // via inline style above; strips + chip styling below). Strip
  // height bumps at LG so the row reads cleanly on a wide cell.
  const layout = `
    /* Feed-colour micro-strips row at the bottom of each cell. One
       strip per unique feed colour on that day; capped at 4 so the
       row doesn't elbow the day number. */
    .mc-strips {
      display: flex;
      gap: var(--stroke-1);
      width: 100%;
      height: var(--stroke-3);
    }
    .mc-strip {
      flex: 1 1 0;
      display: block;
    }
    .mc-cell .mc-dots {
      display: flex;
      flex-direction: column;
      gap: 0.15em;
    }
    /* Refined +N chip, small numeric badge in the bottom-right of
       cells with more events than the visible strips count. Sits
       inside .mc-dots so it stacks naturally above the strips when
       in bars mode and below the text rows when in text mode. */
    .mc-more {
      align-self: flex-end;
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      color: var(--text-muted);
      font-variant-numeric: tabular-nums;
      line-height: 1;
    }
    /* Slightly chunkier strips at lg so they read as deliberate
       feed-colour indicators rather than hairlines. */
    @container (min-width: 700px) {
      .mc-strips { height: calc(var(--stroke-3) * 1.4); }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="calendar_month">
      <div class="w-body" style="gap:var(--space-3)">
        <div class="cal-head">
          <div class="cal-head-row">
            <span class="cal-head-title">${escapeHtml(monthName)} <span class="num">${escapeHtml(String(year))}</span></span>
            <span class="cal-head-meta">${escapeHtml(weekStartLabel)}</span>
          </div>
          <div class="cal-head-rule"></div>
        </div>
        <div class="mc-body" style="flex:1 1 auto;min-height:0;display:flex;flex-direction:column;gap:var(--space-2)">
          <div class="mc-dow">${dowHeader}</div>
          <div class="mc-grid">${cells}</div>
        </div>
      </div>
    </div>`;
}
