// github_activity, Spectra status archetype. Hero = total events
// over the window; pill names the user + the current activity streak
// in days; status-grid breaks out the activity type counts (commits
// / PRs / issues / releases); a hand-rolled SVG stacked histogram
// of the last 7 days sits at the bottom, each bar is segmented by
// event type (commits = moss, PRs = teal, issues = terracotta,
// releases = ochre, other = muted) with a dominant-type Phosphor
// glyph centred inside the bar so a glance reads "Wednesday was
// mostly PRs" without parsing colours.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const DOW_SHORT = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

// Type → colour token + Phosphor glyph for the segment/icon.
const TYPE_COLOR = {
  commits: "var(--accent-3)",
  prs: "var(--accent-4)",
  issues: "var(--accent-1)",
  releases: "var(--accent-2)",
  other: "var(--text-muted)",
};
const TYPE_GLYPH = {
  commits: "ph-git-commit",
  prs: "ph-git-pull-request",
  issues: "ph-warning-circle",
  releases: "ph-tag",
  other: "ph-pulse",
};

function dominantType(day) {
  let bestKey = "";
  let bestVal = 0;
  for (const k of ["commits", "prs", "issues", "releases", "other"]) {
    const v = day?.[k] ?? 0;
    if (v > bestVal) {
      bestVal = v;
      bestKey = k;
    }
  }
  return bestVal > 0 ? bestKey : "";
}

// Hand-rolled SVG stacked-bar histogram. 7 columns; each column is a
// stack of segments by event type, total height ∝ daily total
// relative to the week's max. Empty days drop a 2px baseline dot so
// you can still tell the day exists. Dominant-type glyph centres on
// the tallest bars; suppressed on the empty/low ones where it would
// be too small to read.
function stackedHistogram({ days, labels, todayIdx, w, h }) {
  if (!Array.isArray(days) || days.length === 0) return "";
  const max = Math.max(
    1,
    ...days.map((d) =>
      (d?.commits ?? 0) + (d?.prs ?? 0) + (d?.issues ?? 0) +
      (d?.releases ?? 0) + (d?.other ?? 0)
    )
  );
  const gap = 4;
  const labelH = 14;
  const innerH = h - labelH;
  const barW = (w - gap * (days.length - 1)) / days.length;

  const cells = days.map((d, i) => {
    const x = i * (barW + gap);
    const segs = ["commits", "prs", "issues", "releases", "other"];
    const total = segs.reduce((acc, k) => acc + (d?.[k] ?? 0), 0);
    const top = innerH - (total / max) * (innerH - 2);
    const isToday = i === todayIdx;
    let cursorY = innerH;
    const rects = [];
    for (const k of segs) {
      const v = d?.[k] ?? 0;
      if (v <= 0) continue;
      const segH = (v / max) * (innerH - 2);
      cursorY -= segH;
      rects.push(`<rect x="${x.toFixed(2)}" y="${cursorY.toFixed(2)}" width="${barW.toFixed(2)}" height="${segH.toFixed(2)}" fill="${TYPE_COLOR[k]}" opacity="${isToday ? 1 : 0.85}" rx="1"/>`);
    }
    if (total === 0) {
      // baseline pip so empty days don't disappear
      rects.push(`<rect x="${x.toFixed(2)}" y="${(innerH - 2).toFixed(2)}" width="${barW.toFixed(2)}" height="2" fill="var(--text-muted)" opacity="0.4" rx="1"/>`);
    }
    // Dominant-type glyph centred in the bar, only when the bar is
    // tall enough to fit (≥18px) so we don't squash a tiny glyph.
    let glyph = "";
    const domKey = dominantType(d);
    const barPxH = innerH - top;
    if (domKey && barPxH >= 18) {
      const cx = x + barW / 2;
      const cy = top + barPxH / 2;
      const fontPx = Math.min(barW * 0.7, 16);
      glyph = `
        <foreignObject x="${(cx - fontPx).toFixed(2)}" y="${(cy - fontPx / 2).toFixed(2)}" width="${(fontPx * 2).toFixed(2)}" height="${fontPx.toFixed(2)}" style="overflow:visible;pointer-events:none">
          <div xmlns="http://www.w3.org/1999/xhtml" style="display:flex;align-items:center;justify-content:center;width:100%;height:100%">
            <i class="ph-bold ${TYPE_GLYPH[domKey]}" style="color:var(--surface);font-size:${fontPx.toFixed(1)}px;line-height:1"></i>
          </div>
        </foreignObject>`;
    }
    const label = `
      <text x="${(x + barW / 2).toFixed(2)}" y="${(innerH + labelH - 2).toFixed(2)}"
            text-anchor="middle" fill="var(--text-muted)"
            font-size="10" font-weight="700"
            font-family="${"var(--font-family)"}"
            ${isToday ? 'style="fill:var(--text-primary)"' : ""}>${escapeHtml(labels[i] || "")}</text>`;
    return rects.join("") + glyph + label;
  }).join("");

  return `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet"
         width="100%" height="100%" aria-hidden="true">
      ${cells}
    </svg>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_activity">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>GitHub</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const user = data.user || "GitHub";
  const total = data.count || 0;
  const daily = Array.isArray(data.daily) ? data.daily : [];
  const dailyTyped = Array.isArray(data.daily_typed)
    ? data.daily_typed
    : daily.map((v) => ({ commits: v, prs: 0, issues: 0, releases: 0, other: 0 }));
  const streak = Number.isFinite(data.streak) ? data.streak : 0;

  const cells = [
    ["Commits", data.type_commits ?? 0, TYPE_COLOR.commits, TYPE_GLYPH.commits],
    ["PRs", data.type_prs ?? 0, TYPE_COLOR.prs, TYPE_GLYPH.prs],
    ["Issues", data.type_issues ?? 0, TYPE_COLOR.issues, TYPE_GLYPH.issues],
    ["Releases", data.type_releases ?? 0, TYPE_COLOR.releases, TYPE_GLYPH.releases],
  ];

  const grid = cells.map(([label, value, c, glyph]) => `
    <div class="status-cell">
      <span class="u-label"><i class="ph-bold ${glyph}" style="color:${c};margin-right:.2em"></i>${escapeHtml(label)}</span>
      <span class="v" style="color:${c}">${escapeHtml(String(value))}</span>
    </div>`).join("");

  // Last 7 days of activity. Server returns oldest-first; labels reflect
  // weekday in that order. Today is the last bar.
  const todayIdx = (new Date().getDay() + 6) % 7; // Mon = 0
  const labels = dailyTyped.map((_, i) => {
    const dow = (todayIdx - (dailyTyped.length - 1 - i) + 7) % 7;
    return DOW_SHORT[dow];
  });

  // Streak pill, only shown when ≥ 2 days so a single-day streak
  // doesn't read as a brag. Sits in the title meta row beside REPOS.
  const streakPill = streak >= 2
    ? `<span class="w-title-meta" style="color:var(--accent-2)">
         <i class="ph-bold ph-flame" style="margin-right:.2em"></i>${streak}d STREAK
       </span>`
    : "";

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="github_activity">
      <div class="w-title">
        <i class="ph-bold ph-github-logo" style="color:var(--accent-5)"></i>
        <h3>${escapeHtml(user)}</h3>
        ${streakPill}
        <span class="w-title-meta">${escapeHtml(String(data.repos_count || 0))} REPOS</span>
      </div>
      <div class="w-body status-body">
        <div class="status-hero">
          <i class="ph-bold ph-pulse" style="color:var(--accent-3)"></i>
          <div class="lockup">
            <span class="status-state">${escapeHtml(String(total))}</span>
            <span class="status-sub">events this week</span>
          </div>
        </div>
        <div class="status-grid">${grid}</div>
        ${dailyTyped.length >= 2 ? `
          <div style="flex:1 1 auto;min-height:3em;display:flex">
            ${stackedHistogram({
              days: dailyTyped,
              labels,
              todayIdx: dailyTyped.length - 1,
              w: 280,
              h: 80,
            })}
          </div>` : ""}
      </div>
    </div>`;
}
