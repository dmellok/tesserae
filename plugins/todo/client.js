// Todo — render-only widget. Items are added/done/undone at /plugins/todo/.
// Server returns items pre-sorted: open (newest first) then completed
// (most-recent first). Completed items linger 24h then prune themselves.
//
// Layout matches the design screenshot:
//   - Header: "TASKS LEFT" caps + big open-count / total
//   - Filled accent progress bar (only when there are tasks AND some progress)
//   - List of tasks: open with hollow circle, completed with filled circle +
//     strikethrough text + JUST DONE badge (within ~10 min of completion)

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const VISIBLE_BY_SIZE = { xs: 3, sm: 6, md: 10, lg: 16 };
const JUST_DONE_WINDOW_S = 10 * 60;

export default function render(host, ctx) {
  const { size } = ctx.cell;
  const data = ctx.data || {};
  const items = data.items || [];
  // Only surface the list name when there's more than one list to disambiguate
  // — single-list users see the original "TASKS LEFT" header unchanged.
  const showListName = (data.list_count || 0) > 1 && data.list_name;
  const visible = VISIBLE_BY_SIZE[size] ?? 8;

  const open = items.filter((i) => !i.completed_at);
  const done = items.filter((i) => i.completed_at);
  const total = items.length;
  const openCount = open.length;
  const pct = total === 0 ? 0 : (done.length / total) * 100;
  const nowS = Date.now() / 1000;

  const shown = items.slice(0, visible);
  const remaining = Math.max(0, items.length - shown.length);

  const itemRows = shown
    .map((item) => {
      const isDone = !!item.completed_at;
      const icon = isDone ? "ph-check-circle-fill" : "ph-circle";
      const justDone = isDone && (nowS - item.completed_at) < JUST_DONE_WINDOW_S;
      // Mirrors HN/news card shape: bullet on left, title in the middle,
      // status pill on the right (DONE / JUST DONE). Open items show no pill.
      let pill = "";
      if (justDone) {
        pill = `<span class="score-pill is-just"><i class="ph ph-sparkle"></i> JUST DONE</span>`;
      } else if (isDone) {
        pill = `<span class="score-pill"><i class="ph ph-check"></i> DONE</span>`;
      }
      return `
        <article class="card ${isDone ? "done" : "open"}">
          <i class="ph ${icon} bullet"></i>
          <div class="card-body">
            <div class="card-title">${escapeHtml(item.text)}</div>
          </div>
          ${pill}
        </article>
      `;
    })
    .join("");

  const moreRow =
    remaining > 0
      ? `<div class="more">+${remaining} more</div>`
      : "";

  const body =
    items.length === 0
      ? `<div class="state-empty">
           <i class="ph ph-check-circle-fill"></i>
           <div class="msg">All clear.</div>
         </div>`
      : `<div class="cards">${itemRows}${moreRow}</div>`;

  // Show progress bar only when there's work AND some of it is done — an
  // empty/full bar adds visual weight without information.
  const showProgress = total > 0 && done.length > 0 && done.length < total && size !== "xs";
  const progress = showProgress
    ? `<div class="progress" role="progressbar" aria-valuenow="${pct.toFixed(0)}" aria-valuemin="0" aria-valuemax="100">
         <div class="progress-fill" style="width: ${pct.toFixed(2)}%;"></div>
       </div>`
    : "";

  const headerCount =
    total === 0
      ? ""
      : `<span class="count">
           <span class="count-num">${openCount}</span><span class="count-total">/${total}</span>
         </span>`;

  host.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor.css">
    <link rel="stylesheet" href="/static/style/widget-base.css">
      <link rel="stylesheet" href="/plugins/todo/client.css">
    <div class="widget todo todo--${size}">
      <div class="head">
        <i class="ph ph-list-checks head-icon"></i>
        <span class="head-title">${showListName ? escapeHtml(data.list_name).toUpperCase() : "TASKS LEFT"}</span>
        ${headerCount}
      </div>
      ${progress}
      ${body}
    </div>
  `;

  host.host.dataset.rendered = "true";
}
