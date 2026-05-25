// Calendar widget — month grid on one side, agenda list on the other.
// Layout responds to the cell's aspect ratio via a CSS container query:
// wide cells (landscape / half-height) place the agenda on the right;
// tall cells (portrait) stack the agenda below the grid.
//
// The month grid is generated client-side from `new Date()` so it always
// reflects today without a server round-trip. The agenda items come from
// the server via ctx.data.events, sorted ascending by start time.

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

const MONTH_LONG = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAY_SHORT_SUN = ["S", "M", "T", "W", "T", "F", "S"];
const WEEKDAY_SHORT_MON = ["M", "T", "W", "T", "F", "S", "S"];

// 6×7 = max possible weeks in a month grid (5 won't fit Feb-with-leap on a
// Sunday-start year, but 6 always works).
const ROWS = 6;
const COLS = 7;

// Layout the month for `today`'s month, with leading days from the previous
// month and trailing days from the next month padding the 6×7 grid.
// Returns an array of { day:number, in_month:bool, is_today:bool, weekday:0-6 }
function buildMonthCells(today, weekStart) {
  const year = today.getFullYear();
  const month = today.getMonth();
  const todayDay = today.getDate();

  // JS Date: getDay() returns 0 for Sunday. Shift so 0 == weekStart.
  const shift = weekStart === "mon" ? 1 : 0;
  const firstWeekday = (new Date(year, month, 1).getDay() - shift + 7) % 7;
  const daysInThisMonth = new Date(year, month + 1, 0).getDate();
  const daysInPrevMonth = new Date(year, month, 0).getDate();

  const cells = [];
  // Leading days from previous month.
  for (let i = firstWeekday - 1; i >= 0; i--) {
    cells.push({
      day: daysInPrevMonth - i,
      in_month: false,
      is_today: false,
    });
  }
  // Days in current month.
  for (let d = 1; d <= daysInThisMonth; d++) {
    cells.push({
      day: d,
      in_month: true,
      is_today: d === todayDay,
    });
  }
  // Trailing days from next month to pad to ROWS*COLS.
  let nextDay = 1;
  while (cells.length < ROWS * COLS) {
    cells.push({
      day: nextDay++,
      in_month: false,
      is_today: false,
    });
  }
  return cells;
}

// Pretty-print a start time. All-day events get just the weekday; timed
// events get "Tue 14:30" so the agenda reads naturally on the panel.
function formatStart(event) {
  const start = new Date(event.start_iso);
  if (Number.isNaN(start.getTime())) return "—";
  const wd = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][start.getDay()];
  const today = new Date();
  const dayDelta = Math.round(
    (start.setHours(0, 0, 0, 0) - today.setHours(0, 0, 0, 0)) / 86400000,
  );
  let dateLabel;
  if (dayDelta === 0) dateLabel = "Today";
  else if (dayDelta === 1) dateLabel = "Tomorrow";
  else if (dayDelta >= 0 && dayDelta < 7) dateLabel = wd;
  else dateLabel = `${wd} ${start.getDate()}/${start.getMonth() + 1}`;
  if (event.all_day) return dateLabel;
  const original = new Date(event.start_iso);
  const hh = String(original.getHours()).padStart(2, "0");
  const mm = String(original.getMinutes()).padStart(2, "0");
  return `${dateLabel} · ${hh}:${mm}`;
}

export default function render(host, ctx) {
  const data = ctx.data || {};
  const opts = ctx.cell?.options || {};
  const today = new Date();
  const weekStart = opts.week_start === "mon" ? "mon" : "sun";
  const highlightToday = opts.highlight_today !== false;

  // Error / unconfigured state — use the shared widget-base block so the
  // empty look matches every other widget.
  if (data.error) {
    host.innerHTML = `
      <link rel="stylesheet" href="/static/style/widget-base.css">
      <link rel="stylesheet" href="/static/icons/phosphor.css">
      <div class="widget">
        <div class="state-error">
          <i class="ph ph-calendar-x"></i>
          <div class="msg">${escapeHtml(data.error)}</div>
        </div>
      </div>
    `;
    host.host.dataset.rendered = "true";
    return;
  }

  const monthLabel = MONTH_LONG[today.getMonth()].toUpperCase();
  const weekdayHeaders = weekStart === "mon" ? WEEKDAY_SHORT_MON : WEEKDAY_SHORT_SUN;
  const cells = buildMonthCells(today, weekStart);

  // Month grid SVG-less HTML: a fixed 7-col CSS grid for the day numbers.
  const dayCells = cells
    .map((c) => {
      const cls = [
        "day",
        c.in_month ? "" : "is-outside",
        c.is_today && highlightToday ? "is-today" : "",
      ]
        .filter(Boolean)
        .join(" ");
      return `<div class="${cls}">${c.day}</div>`;
    })
    .join("");
  const weekdayCells = weekdayHeaders
    .map((d) => `<div class="dow">${d}</div>`)
    .join("");

  // Agenda — each event is a flat surface card with a coloured strip
  // along the left edge identifying its source calendar. An empty state
  // pops in when there's nothing in the next 60 days.
  const events = data.events || [];
  const calendars = data.calendars || [];
  const showCalLegend = calendars.length > 1;
  const agenda = events.length
    ? events
        .map((e) => {
          const colour = e.cal_colour || "var(--theme-accent)";
          return `
        <article class="event" style="--cal-colour: ${escapeHtml(colour)};">
          <div class="event-body">
            <div class="event-title">${escapeHtml(e.title)}</div>
            <div class="event-meta">
              <span><i class="ph ph-clock"></i> ${escapeHtml(formatStart(e))}</span>
              ${showCalLegend && e.cal_name
                ? `<span class="event-cal">${escapeHtml(e.cal_name)}</span>`
                : ""}
              ${e.location
                ? `<span class="event-loc"><i class="ph ph-map-pin"></i> ${escapeHtml(e.location)}</span>`
                : ""}
            </div>
          </div>
        </article>`;
        })
        .join("")
    : `<div class="event-empty">
         <i class="ph ph-confetti"></i>
         <span>Nothing on the agenda.</span>
       </div>`;

  // Legend: tiny coloured chip + calendar name, one per source. Only
  // shown when at least two calendars are wired up.
  const legend = showCalLegend
    ? `<div class="cal-legend">
         ${calendars
           .map(
             (c) =>
               `<span class="cal-chip">
                  <span class="cal-swatch" style="background: ${escapeHtml(c.colour)}"></span>
                  ${escapeHtml(c.name)}
                </span>`,
           )
           .join("")}
       </div>`
    : "";

  const noteBar = data.note
    ? `<div class="cal-note"><i class="ph ph-warning"></i> ${escapeHtml(data.note)}</div>`
    : "";

  const yearLabel = today.getFullYear();

  host.innerHTML = `
    <link rel="stylesheet" href="/static/style/widget-base.css">
    <link rel="stylesheet" href="/plugins/calendar/client.css">
    <link rel="stylesheet" href="/static/icons/phosphor.css">
    <div class="widget cal">
      <div class="head">
        <i class="ph ph-calendar-blank head-icon"></i>
        <span class="head-title">${escapeHtml(monthLabel)}</span>
        <span class="head-place">${yearLabel}</span>
      </div>
      ${noteBar}
      <div class="cal-grid">
        <section class="month">
          <div class="dow-row">${weekdayCells}</div>
          <div class="days">${dayCells}</div>
        </section>
        <section class="agenda-wrap">
          <div class="agenda">${agenda}</div>
          ${legend}
        </section>
      </div>
    </div>
  `;
  host.host.dataset.rendered = "true";
}
