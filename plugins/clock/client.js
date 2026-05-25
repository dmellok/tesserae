// Clock — digital time, optional date and seconds. The time element auto-fits
// the cell by measuring its rendered width/height and scaling font-size.

function formatTime(now, format, showSeconds) {
  const opts = {
    hour: "2-digit",
    minute: "2-digit",
    hour12: format === "12h",
  };
  if (showSeconds) opts.second = "2-digit";
  return new Intl.DateTimeFormat("en-US", opts).format(now);
}

function formatDate(now, size) {
  const opts =
    size === "lg"
      ? { weekday: "long", month: "long", day: "numeric" }
      : { weekday: "short", month: "short", day: "numeric" };
  return new Intl.DateTimeFormat("en-US", opts).format(now);
}

const REFERENCE_PX = 200;
const SAFETY = 0.94;

function fitText(el, availW, availH) {
  if (!el || availW <= 0 || availH <= 0) return;
  el.style.fontSize = `${REFERENCE_PX}px`;
  const naturalW = el.scrollWidth;
  const naturalH = el.scrollHeight;
  if (naturalW === 0 || naturalH === 0) return;
  const scale = Math.min(availW / naturalW, availH / naturalH) * SAFETY;
  el.style.fontSize = `${REFERENCE_PX * scale}px`;
}

async function ensureCellFontLoaded(cell) {
  if (!document.fonts || !document.fonts.load) return;
  const computed = getComputedStyle(cell).fontFamily || "";
  const family = computed
    .split(",")[0]
    .trim()
    .replace(/^['"]|['"]$/g, "");
  if (!family) return;
  try {
    await Promise.all([
      document.fonts.load(`700 100px "${family}"`),
      document.fonts.load(`600 100px "${family}"`),
    ]);
  } catch {
    /* font not declared or load failed; fall through to fallback */
  }
}

export default async function render(host, ctx) {
  const { size, options } = ctx.cell;
  const format = options.format || "24h";
  const showSeconds = options.show_seconds === true;
  const showDate = options.show_date !== false && size !== "xs";

  const sizeClass = `clock--${size}`;
  host.innerHTML = `
    <link rel="stylesheet" href="/static/style/widget-base.css">
    <link rel="stylesheet" href="/plugins/clock/client.css">
    <div class="clock ${sizeClass}">
      <div class="time" aria-label="time"></div>
      ${showDate ? `<div class="date" aria-label="date"></div>` : ""}
    </div>
  `;

  const timeEl = host.querySelector(".time");
  const dateEl = host.querySelector(".date");
  const cell = host.host;

  function refit() {
    const cellW = cell.clientWidth;
    const cellH = cell.clientHeight;
    if (cellW === 0 || cellH === 0) return;
    const padding = Math.min(cellW, cellH) * 0.04;
    let dateSpace = 0;
    if (dateEl) {
      dateEl.style.fontSize = `${Math.min(cellW * 0.06, cellH * 0.1)}px`;
      dateSpace = dateEl.offsetHeight + padding * 0.5;
    }
    fitText(timeEl, cellW - padding * 2, cellH - padding * 2 - dateSpace);
  }

  function tick() {
    const now = new Date();
    timeEl.textContent = formatTime(now, format, showSeconds);
    if (dateEl) dateEl.textContent = formatDate(now, size);
    refit();
  }

  await ensureCellFontLoaded(cell);

  tick();
  const intervalMs = showSeconds ? 1000 : 30_000;
  const interval = setInterval(tick, intervalMs);

  const observer = new ResizeObserver(refit);
  observer.observe(cell);

  host.__tesseraeCleanup = () => {
    clearInterval(interval);
    observer.disconnect();
  };

  host.host.dataset.rendered = "true";
}

export function cleanup(host) {
  if (host.__tesseraeCleanup) host.__tesseraeCleanup();
}
