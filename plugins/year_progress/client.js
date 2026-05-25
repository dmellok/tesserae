// Year progress — big day-of-year, % bar, daily-tick row, and stat cards
// (REMAINING / WEEK / QUARTER). Re-renders once per minute so the
// percentage edges forward in real time.

function isLeap(y) {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

function dayOfYear(d) {
  const start = new Date(d.getFullYear(), 0, 0);
  return Math.floor((d - start) / 86_400_000);
}

function weekOfYear(d) {
  // ISO-ish: Monday-anchored, returns 1..53.
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return Math.ceil(((t - yearStart) / 86_400_000 + 1) / 7);
}

function quarter(d) {
  return Math.floor(d.getMonth() / 3) + 1;
}

function fmtFullDate(d) {
  return d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
}

export default function render(host, ctx) {
  function paint() {
    const now = new Date();
    const year = now.getFullYear();
    const total = isLeap(year) ? 366 : 365;
    const day = dayOfYear(now);
    const remaining = total - day;
    const pct = (day / total) * 100;
    const wk = weekOfYear(now);
    const q = quarter(now);

    const ticks = [];
    for (let i = 1; i <= total; i++) {
      ticks.push(`<span class="tick ${i <= day ? "on" : ""}"></span>`);
    }

    host.innerHTML = `
      <link rel="stylesheet" href="/static/style/widget-base.css">
      <link rel="stylesheet" href="/plugins/year_progress/client.css">
      <div class="widget yp">
        <div class="head">
          <span class="head-title">YEAR</span>
          <span class="head-place">${year}</span>
          <span class="head-time">${fmtFullDate(now)}</span>
        </div>
        <div class="hero">
          <div class="hero-num">${day}</div>
          <div class="hero-of">/ ${total}</div>
        </div>
        <div class="hero-sub">DAYS INTO ${year}</div>

        <div class="pct-row">
          <div class="pct-track">
            <div class="pct-fill" style="width: ${pct.toFixed(2)}%;"></div>
          </div>
          <div class="pct-value">${pct.toFixed(1)}%</div>
        </div>

        <div class="ticks">${ticks.join("")}</div>

        <div class="stats">
          <div class="stat">
            <div class="stat-label">REMAINING</div>
            <div class="stat-value">${remaining}<span class="stat-unit"> DAYS</span></div>
          </div>
          <div class="stat">
            <div class="stat-label">WEEK</div>
            <div class="stat-value">${wk}<span class="stat-unit"> /52</span></div>
          </div>
          <div class="stat">
            <div class="stat-label">QUARTER</div>
            <div class="stat-value">Q${q}</div>
          </div>
        </div>
      </div>
    `;
  }

  paint();
  host.host.dataset.rendered = "true";

  // Re-tick at the top of each minute so the % crawls forward live.
  const tick = () => {
    paint();
    const now = new Date();
    const ms = 60_000 - (now.getSeconds() * 1000 + now.getMilliseconds());
    timer = setTimeout(tick, ms);
  };
  let timer = setTimeout(tick, 60_000 - new Date().getSeconds() * 1000);
  host.__tesseraeCleanup = () => clearTimeout(timer);
}

export function cleanup(host) {
  if (host.__tesseraeCleanup) host.__tesseraeCleanup();
}
