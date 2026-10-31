// Lineups page: draw the view-model (app/lineups_view.py, LineupsView.to_dict())
// as markup. This mirrors templates/_lineups.html one macro to one function so
// the page looks the same before and after a redraw; tests/test_lineups_browser.py
// compares the two. Change both together.
//
// Also holds the small schedule model used to preview unsaved edits on the
// day bar (a port of the builder's blocks/holds/dayPieces): the server redraws
// everything properly once the edits are saved.
(function () {
  "use strict";

  const ICONS = JSON.parse(document.querySelector("[data-lu-icons]").textContent);

  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const LETTER = ["M", "T", "W", "T", "F", "S", "S"];
  const DAY_MIN = 1440;
  const WEEK_MIN = 7 * DAY_MIN;

  const esc = (s) =>
    String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  const ico = (name, cls) =>
    `<svg class="lu-ico${cls ? " " + cls : ""}" viewBox="0 0 16 16" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  const pad2 = (n) => String(n).padStart(2, "0");
  const hhmm = (m) => {
    m = ((m % DAY_MIN) + DAY_MIN) % DAY_MIN;
    return pad2(Math.floor(m / 60)) + ":" + pad2(m % 60);
  };
  const endHhmm = (m) => (m > 0 && m % DAY_MIN === 0 ? "24:00" : hhmm(m));
  // Labels read in the page's clock style, vm.time_cycle ("h12" / "h23", set
  // by setTimeCycle). Mirrors clock() in app/lineups_view.py; change both
  // together. hhmm/endHhmm stay the 24-hour data form (inputs, saves).
  let cycle = "h23";
  const setTimeCycle = (c) => {
    cycle = c === "h12" ? "h12" : "h23";
  };
  const clock = (m, end) => {
    if (cycle !== "h12") return end ? endHhmm(m) : hhmm(m);
    m = ((m % DAY_MIN) + DAY_MIN) % DAY_MIN;
    const h = Math.floor(m / 60);
    return `${h % 12 || 12}${m % 60 ? ":" + pad2(m % 60) : ""}\u202f${h < 12 ? "AM" : "PM"}`;
  };
  const clockRange = (s, e) => `${clock(s)}–${clock(e, true)}`;
  const pct = (m) => Math.round((m / DAY_MIN) * 100 * 10000) / 10000;

  function thumb(name, url, aspect, orientation, cls, title, priority, display, extra) {
    const ar = String(aspect || "16/10").split("/");
    const inner = url
      ? `<img src="${esc(url)}" alt="" width="${esc(ar[0])}" height="${esc(ar[1])}" loading="${priority ? "eager" : "lazy"}" decoding="async"${priority ? ' fetchpriority="high"' : ""}>`
      : `<span class="lu-thumb-ph" aria-hidden="true">${esc(
          String(cls || "")
            .split(" ")
            .includes("missing")
            ? "!"
            : String(name || "?").slice(0, 1)
        )}</span>`;
    return `<span class="lu-thumb ${orientation || "l"}${cls ? " " + cls : ""}" style="--ar:${ar[0]}/${ar[1]};--arw:${ar[0]};--arh:${ar[1]}" title="${esc(title === undefined || title === null ? name : title)}" data-gamut="${esc((display && display.gamut) || "color")}" data-grey-levels="${esc((display && display.grey_levels) || 0)}">${inner}${extra || ""}</span>`;
  }
  const im = (icon, value, title, cls) =>
    `<span class="lu-im${cls ? " " + cls : ""}" title="${esc(title)}" aria-label="${esc(title)}">${ico(icon)}<span>${esc(value)}</span></span>`;

  /* ---------------------------------------------------------------- day bar */
  // Advances are faint split lines (the next one a little stronger), thinned
  // to a readable density by lineups.js; each shows its time on hover (an
  // instant tooltip from data-tip, see lineups.js).
  // Mirrors the tick markup in templates/_lineups.html.
  function ticks(s, day) {
    const after = day.is_today && day.now_min !== null && day.now_min !== undefined;
    return (s.ticks || [])
      .filter((t) => t.next || !after || t.minute > day.now_min)
      .map((t) =>
        t.next
          ? `<i class="lu-tick next" style="left:${t.pct}%" data-tip="Next refresh ${clock(t.minute)}" aria-hidden="true"></i>`
          : `<i class="lu-tick" style="left:${t.pct}%" data-tip="Refresh ${clock(t.minute)}" aria-hidden="true"></i>`
      )
      .join("");
  }

  // "Update in 5 min", the flag over the next advance. Mirrors
  // update_in_label() in app/lineups_view.py; change both together. Empty from
  // a day on, where it would only be noise.
  function updateInLabel(minutes) {
    if (minutes >= DAY_MIN) return "";
    if (minutes < 1) return "Update in under a minute";
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return h ? `Update in ${h} h${m ? ` ${m} min` : ""}` : `Update in ${m} min`;
  }

  function dayTrack(d, day, editableIds, tall, extraCls) {
    if (tall === undefined) tall = true;
    let html = "";
    let flag = null; // the next advance's flag, when the bar shows one
    (day.segments || []).forEach((s) => {
      const hold = s.kind === "hold";
      const push = s.kind === "push";
      const ed = tall && !hold && editableIds.includes(s.lineup_id);
      const cls = [
        "lu-blk",
        hold ? "hold" : "",
        push ? "push" : "",
        s.live ? "live" : "",
        s.planned && tall ? "plan" : "",
        s.pending ? "is-pending" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const edAttr = ed
        ? ` data-edit tabindex="0" role="button" aria-label="${esc(s.title)}. Drag or use arrow keys to move${push ? "" : ", Shift+arrows to resize the end, Alt+Shift+arrows the start"}. Click to open."`
        : ` role="group" aria-label="${esc(s.title)}"`;
      let inner =
        ed && !push
          ? '<i class="lu-h" data-h="l" aria-hidden="true"></i><i class="lu-h" data-h="r" aria-hidden="true"></i>'
          : "";
      if (!hold && !push) {
        inner += ticks(s, day);
        inner += `<span class="lu-blk-l">${esc(s.label)}</span>`;
        const nt = tall && (s.ticks || []).find((t) => t.next && t.in_sec != null);
        const text = nt ? updateInLabel(nt.in_min) : "";
        if (text) {
          flag = `<span class="lu-nflag" style="--bl:${s.left_pct};--bw:${s.width_pct};--tp:${nt.pct}" data-in="${nt.in_sec}">${text}</span>`;
        }
      }
      html += `<div class="${cls}" style="left:${s.left_pct}%;width:${s.width_pct}%" data-lineup="${esc(s.lineup_id)}" data-display="${esc(d.id)}" title="${esc(s.title)}"${edAttr}>${inner}</div>`;
      if (push && tall)
        html += `<span class="lu-blk-side" style="left:calc(${s.left_pct}% + 9px)" title="${esc(s.title)}">${esc(s.label)}</span>`;
    });
    (day.pause || []).forEach((p) => {
      html += `<i class="lu-pz" style="left:${p.left_pct}%;width:${p.width_pct}%" title="${esc(p.title)}">${ico("moon")}</i>`;
      html += (p.over || [])
        .map(
          (o) =>
            `<i class="lu-pz-over" style="left:${o.left_pct}%;width:${o.width_pct}%" title="${esc(p.title)}"></i>`
        )
        .join("");
    });
    if (flag) html += flag;
    if (day.is_today && day.now_pct !== null && day.now_pct !== undefined) {
      html += `<i class="lu-nowm" style="left:${day.now_pct}%" title="Now ${clock(day.now_min)}"></i>`;
    }
    return `<div class="lu-trk${tall ? " tall" : ""}${flag ? " flagged" : ""}${extraCls ? " " + extraCls : ""}" role="group" aria-label="${esc(day.aria_label)}" data-day="${day.weekday}">${html}</div>`;
  }

  const scale = (hours, short) =>
    `<div class="lu-scale" aria-hidden="true">${hours.map((h) => `<span style="left:${(h / 24) * 100}%">${cycle === "h12" ? clock(h * 60) : pad2(h) + (short ? "" : ":00")}</span>`).join("")}</div>`;

  // Block start labels for a bar with more than two blocks; twin of
  // start_labels in app/lineups_view.py (same tiers, gap and order).
  const SCALE_TIERS = [36, 40, 44, 48, 52, 56, 60, 64, 68, 72, 76, 80, 84, 88, 92, 96, 108, 120];
  function startLabels(segments) {
    const blocks = (segments || []).filter((s) => s.kind !== "hold");
    if (blocks.length <= 2) return null;
    const longest = new Map();
    blocks.forEach((b) =>
      longest.set(b.start_min, Math.max(longest.get(b.start_min) || 0, b.end_min - b.start_min))
    );
    const mins = [...longest.keys()];
    const order = [...mins].sort((a, b) => longest.get(b) - longest.get(a) || a - b);
    const hide = new Map(mins.map((m) => [m, []]));
    SCALE_TIERS.forEach((w, i) => {
      const kept = [];
      order.forEach((m) => {
        const n = clock(m).length;
        const left = Math.min((m / DAY_MIN) * w, w - n);
        if (kept.every(([a, b]) => left >= b + 0.5 || left + n + 0.5 <= a))
          kept.push([left, left + n]);
        else hide.get(m).push(`h${i}`);
      });
    });
    return mins
      .sort((a, b) => a - b)
      .map((m) => ({
        minute: m,
        pct: pct(m),
        label: clock(m),
        n: clock(m).length,
        hide: hide.get(m).join(" "),
      }));
  }
  function dayScale(day) {
    const starts = startLabels(day.segments);
    if (!starts) return scale([0, 6, 12, 18, 24], true);
    return `<div class="lu-scale st" aria-hidden="true">${starts.map((t) => `<span${t.hide ? ` class="${t.hide}"` : ""} style="--x:${t.pct}%;--n:${t.n}">${esc(t.label)}</span>`).join("")}</div>`;
  }

  function dayPick(d, day, today) {
    const label = `${day.label} · ${day.relative}`;
    const back =
      day.relative !== "today"
        ? `<button type="button" class="lu-dp-label" data-day="${today}" data-display="${esc(d.id)}" title="Showing ${esc(label)}. Click to go back to today">${esc(label)}${ico("x")}</button>`
        : "";
    return `<div class="lu-dpick">${back}<button type="button" class="lu-ibtn sm lu-dp-btn" data-dpick="${esc(d.id)}" aria-haspopup="dialog" aria-expanded="false" aria-label="Pick a day; showing ${esc(label)}" title="Pick a day">${ico("cal")}</button></div>`;
  }

  function dayPop(d, selected, today) {
    const short = (i) => {
      const diff = (i - today + 7) % 7;
      return diff === 0 ? "today" : diff === 1 ? "tmrw" : diff === 6 ? "yday" : "";
    };
    const days = DAYS.map(
      (n, i) =>
        `<button type="button" class="lu-dp-day${i === today ? " is-today" : ""}" data-day="${i}" data-display="${esc(d.id)}" aria-pressed="${i === selected}"${i === selected ? " data-sel" : ""}><b>${n}</b><span>${short(i)}</span></button>`
    ).join("");
    return `<div class="lu-dp-pop" role="dialog" aria-modal="true" aria-label="Pick a day for ${esc(d.name)}"><div class="lu-dp-head"><b>Pick a day</b><button type="button" class="lu-dp-close" data-dp-close title="Close" aria-label="Close">${ico("x")}</button></div><div class="lu-dp-days" role="group">${days}</div><button type="button" class="lu-dp-today" data-day="${today}" data-display="${esc(d.id)}">Today</button></div>`;
  }

  /* ---------------------------------------------------------------- rows */
  function winButton(l) {
    if (!l.window) return "";
    const warn = l.window.overlaps_pause
      ? `<span class="lu-im warn" title="${esc(l.window.pause_title)}" aria-label="Overlaps Quiet hours">${ico("moon")}</span>`
      : "";
    return `<span class="lu-hs-win">${ico("clock")}<button type="button" class="lu-win" data-win-edit="${esc(l.id)}" title="Time window. Click to edit">${esc(l.window.label)}</button>${warn}</span>`;
  }

  function rowState(l) {
    const sep = '<span class="lu-sep-dot">·</span>';
    const w = winButton(l);
    const ws = l.window ? sep : "";
    if (l.status === "live" && l.progress) {
      const p = l.progress;
      let now = "";
      if (l.live_now) {
        const n = l.live_now;
        now = n.in_sync
          ? `${ico("play", "acc")}<span class="lu-hn live" title="On screen now">${esc(n.scheduled_name)}</span>`
          : `<span class="lu-hn q" title="Scheduled, queued: lands ${esc(n.lands || "")}">${esc(n.scheduled_name)}</span>${n.on_screen_name ? `<span class="lu-hn dim" title="Still on screen">${esc(n.on_screen_name)} on screen</span>` : ""}`;
        now = `<span class="lu-hs-now">${now}</span>`;
      }
      return `<div class="lu-hs" title="${esc(p.title)}"><span class="lu-hs-line">${w}${ws}<span>ends in <b>${esc(p.ends_in_label)}</b></span></span><span class="lu-hs-bar" role="progressbar" aria-label="Time through this window" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p.window_pct}"><i style="width:${p.window_pct}%"></i></span>${now}</div>`;
    }
    if (l.status === "live") {
      return `<div class="lu-hs"><span class="lu-hs-line">${w}${ws}<span>${l.kind === "manual" ? "playing now" : "live"}</span></span></div>`;
    }
    if (l.status === "later_today") {
      const title = (l.progress ? l.progress.title : "") || (l.next_run ? l.next_run.note : "");
      return `<div class="lu-hs" title="${esc(title)}"><span class="lu-hs-line">${w}${ws}<span>in <b>${esc(l.progress ? l.progress.starts_in_label : "")}</b></span></span></div>`;
    }
    if (l.status === "off") {
      return `<div class="lu-hs" title="Switched off; nothing runs until it is enabled"><span class="lu-hs-line">${w}${ws}<span>off</span></span></div>`;
    }
    if (l.kind === "manual") {
      return '<div class="lu-hs" title="Runs by hand or on a tap; no schedule"><span class="lu-hs-line"><span>by hand or tap</span></span></div>';
    }
    const nr = l.next_run;
    const title = nr ? (nr.held ? nr.note : "Next run " + nr.main) : "";
    return `<div class="lu-hs" title="${esc(title)}"><span class="lu-hs-line">${w}${nr ? `${sep}<span>next ${esc(nr.day_label)}</span>` : ""}</span></div>`;
  }

  function rowChips(l) {
    const plural = (n) => `${n} dashboard${n === 1 ? "" : "s"}`;
    const missing = l.missing_count
      ? im(
          "layers",
          `+${l.missing_count} missing`,
          `${l.missing_count} deleted dashboard${l.missing_count === 1 ? "" : "s"} still in this lineup; remove ${l.missing_count === 1 ? "it" : "them"}`,
          "warn"
        )
      : "";
    if (l.kind === "daily")
      return (
        im(
          "layers",
          "1",
          `1 dashboard: ${l.steps.length ? l.steps[0].name : ""}, pushed once, then held`
        ) + missing
      );
    if (l.kind === "manual")
      return (
        im("layers", l.step_count, plural(l.step_count)) +
        missing +
        im("hand", "by hand", "Moves on a tap or by hand")
      );
    let s = im("layers", l.step_count, plural(l.step_count)) + missing;
    if (l.step_label)
      s += im(
        "timer",
        l.step_label,
        l.step_minutes ? `Advances every ${l.step_minutes} min` : `Advances every ${l.step_label}`
      );
    if (l.kind === "cycle" && l.cycle_label)
      s += im("span", l.cycle_label, `Full cycle ${l.cycle_label}`, "opt");
    return s;
  }

  function dayTiles(l) {
    const tiles = LETTER.map((c, i) => {
      const on = !!l.days[i];
      const edited = l.pending_days && l.pending_days.includes(i);
      return `<button type="button" class="lu-dt${on ? " on" : ""}${i === l.today_index ? " is-today" : ""}${edited ? " is-edited" : ""}" data-toggle-day="${esc(l.id)}:${i}" aria-pressed="${on ? "true" : "false"}" title="${DAYS[i]}: ${on ? "runs. Click to remove" : "off. Click to add"}">${c}</button>`;
    }).join("");
    return `<span class="lu-days" role="group" aria-label="Days ${esc(l.name)} runs">${tiles}</span>`;
  }

  function rowActions(l, assigned) {
    const a = l.actions;
    const icon = { skip: "skip", play: "play", push: "play" }[a.primary] || "play";
    const url = !assigned
      ? ""
      : (a.primary === "push" ? a.fire_url : a.play_url) || a.fire_url || a.play_url || "";
    let menu = "";
    if (a.edit_url) menu += `<a href="${esc(a.edit_url)}">Edit</a>`;
    if (assigned && l.kind === "cycle" && a.fire_url)
      menu += `<form method="post" action="${esc(a.fire_url)}" class="lu-inline"><button type="submit">Push now</button></form>`;
    if (a.toggle_url)
      menu += `<form method="post" action="${esc(a.toggle_url)}" class="lu-inline"><button type="submit">${l.enabled ? "Disable" : "Enable"}</button></form>`;
    if (a.delete_url)
      menu += `<form method="post" action="${esc(a.delete_url)}" class="lu-inline" data-confirm="Delete ${esc(l.name)}?"><button type="submit" class="danger">Delete</button></form>`;
    return (
      `<div class="lu-row-actions">${assigned ? `<form method="post" action="${esc(url)}" class="lu-inline"><button class="lu-ibtn${l.status === "live" ? " is-primary" : ""}" type="submit" title="${esc(a.primary_label)}" aria-label="${esc(a.primary_label)}"${url ? "" : " disabled"}>${ico(icon)}</button></form>` : ""}` +
      `<details class="lu-more"><summary title="More" aria-label="More actions for ${esc(l.name)}">${ico("dots")}</summary><div class="lu-more-menu">${menu}</div></details></div>`
    );
  }

  function stepHtml(d, l, s) {
    const cls = s.next
      ? "is-next"
      : s.current && !s.on_screen && s.pill
        ? "is-waiting"
        : s.on_screen
          ? "is-now"
          : "";
    const pill = s.pill
      ? `<span class="lu-pill p-${esc(s.pill.kind)}" title="${esc(s.pill.title)}">${s.pill.kind === "now" ? ico("play") : ""}${esc(s.pill.label)}</span>`
      : "";
    const push =
      d.id && l.actions.push_step_url && !s.missing && !s.off_display
        ? `<form method="post" action="${esc(l.actions.push_step_url)}" class="lu-push-form"><input type="hidden" name="index" value="${s.index}"><input type="hidden" name="display_id" value="${esc(d.id)}"><input type="hidden" name="page_id" value="${esc(s.dashboard_id)}"><input type="hidden" name="composition_digest" value="${esc(s.composition_digest || "")}"><button type="submit" class="lu-push-ov" data-push="${esc(l.id)}:${s.index}" title="${esc(s.push_title)}" aria-label="${esc(s.push_title)}"><i>${ico("send")}</i><span>Push now</span></button></form>`
        : "";
    const count =
      s.item_count > 1
        ? `<button type="button" class="lu-count" data-peek="${esc(l.id)}:${s.index}" aria-haspopup="dialog" aria-expanded="false" title="${s.item_count} items in this step. Show them">×${s.item_count}</button>`
        : "";
    const cond = s.conditional
      ? ` <span title="Shows only while its conditions pass">${ico("funnel")}</span>`
      : "";
    const off = s.off_display
      ? `<span class="lu-step-off" title="${esc(s.off_display_note)}">Not on this display</span>${
          d.id && l.actions.bind_url
            ? `<button type="button" class="lu-bind" data-bind="${esc(l.id)}:${s.index}" title="${esc(s.off_display_note)}. Bind it to ${esc(d.name)} too so it can show here.">Bind to ${esc(d.name)}</button>`
            : ""
        }`
      : "";
    const sep =
      s.separator === "arrow"
        ? `<span class="lu-sep" aria-hidden="true">${ico("chev")}</span>`
        : s.separator === "loop"
          ? `<span class="lu-sep loop" title="Loops back to ${esc(l.steps[0].name)}">${ico("loop")}</span>`
          : "";
    return (
      `<li class="lu-step${cls ? " " + cls : ""}${s.missing ? " is-missing" : ""}${s.off_display ? " is-offdisplay" : ""}" data-step="${s.index}"${s.missing ? ' title="This dashboard was deleted; remove it from the lineup"' : s.off_display ? ` title="${esc(s.off_display_note)}"` : ""}><figure><span class="lu-slot">${pill}</span>` +
      `<span class="lu-tile${s.item_count > 1 ? " stacked" : ""}">${thumb(s.name, s.thumb_url, s.aspect, d.orientation || s.orientation, s.missing ? "missing" : s.off_display ? "offdisplay" : "", null, false, d)}<span class="lu-scrim" aria-hidden="true"></span>${push}<span class="lu-pushed-msg" aria-hidden="true"></span>${count}</span>` +
      `<figcaption><span class="lu-step-name">${esc(s.name)}</span><span class="lu-step-meta">${esc(s.position)}${cond}</span>${off}</figcaption></figure>${sep}</li>`
    );
  }

  const STATE_CLS = {
    live: "is-live",
    later_today: "is-later",
    not_today: "is-off",
    off: "is-off is-disabled",
  };
  const TAG_CLS = { live: "live", later_today: "later", not_today: "off", off: "off" };

  // A lineup on several displays draws once per display; the first copy keeps
  // the plain id, the others add the display (ui.seen is reset per draw).
  function row(d, l, ui) {
    let uid = l.id;
    if (ui && ui.seen) {
      if (ui.seen.has(l.id)) uid = `${l.id}--${d.id}`;
      ui.seen.add(l.id);
    }
    const isNew = !!(ui && ui.isNew && ui.isNew(l));
    const folded = !isNew && (ui && ui.folded ? ui.folded(l) : l.folded);
    const rowErr = ui && ui.rowError ? ui.rowError(l) : "";
    const edot = l.pending ? '<i class="lu-edot" title="Unsaved change"></i>' : "";
    const meta = `${l.window ? dayTiles(l) : ""}${rowChips(l)}`;
    return (
      `<li class="lu-row ${STATE_CLS[l.status] || "is-off"}${folded ? " is-folded" : ""}${isNew ? " is-new" : ""}" id="lu-row-${esc(uid)}" data-lineup="${esc(l.id)}" data-display="${esc(d.id)}" data-kind="${esc(l.kind)}" data-status="${esc(l.status)}">` +
      `<div class="lu-row-head"><span class="lu-row-tag"><span class="lu-tag-st ${TAG_CLS[l.status] || "off"}">${l.status === "live" ? '<i class="lu-dot pulse"></i>' : ""}${esc(l.status_label)}</span></span>` +
      `<div class="lu-row-title"><span class="lu-row-name">${esc(l.name)}${edot}</span><span class="lu-row-meta" title="${esc(l.mode_label)}, ${esc(l.days_text)}">${meta}</span>${(l.warnings || []).map((w) => `<span class="lu-row-warning">${ico("info")}${esc(w)}</span>`).join("")}${l.display_removed ? `<span class="lu-row-warning">${ico("info")}Display removed</span>` : ""}${rowErr ? `<span class="lu-row-err" role="alert">${ico("info")}${esc(rowErr)}</span>` : ""}</div>` +
      `<div class="lu-row-state">${rowState(l)}</div>${rowActions(l, d.id)}` +
      `<button class="lu-fold" type="button" aria-expanded="${folded ? "false" : "true"}" aria-controls="lu-body-${esc(uid)}" aria-label="${esc(l.name)}: show or hide dashboards" data-fold="${esc(l.id)}">${ico("caret")}</button></div>` +
      `<div class="lu-row-body" id="lu-body-${esc(uid)}"><div class="lu-strip-scroll" tabindex="0" role="region" aria-label="Dashboards in ${esc(l.name)}; scrolls sideways"><ol class="lu-strip${l.status === "live" ? " has-now" : ""} ${d.orientation || "l"}">${l.steps.map((s) => stepHtml(d, l, s)).join("")}</ol></div></div></li>`
    );
  }

  /* ---------------------------------------------------------------- display */
  function header(vm, d, ui) {
    const op = d.on_screen;
    const editable =
      ui && ui.barReadOnly
        ? []
        : d.lineups
            .filter((l) => l.window && l.window.editable && !l.window.wraps && l.enabled)
            .map((l) => l.id);
    const linkIcon = { connected: "signal", sleeps: "clock" }[d.link.mode] || "info";
    const fr = op ? null : d.frame;
    const ph = d.placeholder;
    const qb = d.queued_badge;
    const qbHtml = qb
      ? `<span class="lu-qbadge" title="${esc(qb.title)}" aria-label="${esc(qb.title)}">${ico("clock")}<span>${esc(qb.label)}</span></span>`
      : "";
    const phIcon = { queued: "clock", delivering: "send", unavailable: "info" };
    const delivering = !!ph && ph.state === "delivering";
    const thumbHtml =
      op && !delivering
        ? thumb(
            op.name,
            op.thumb_url,
            op.aspect,
            op.orientation,
            "",
            null,
            vm.displays[0]?.id === d.id,
            d,
            qbHtml
          )
        : fr && fr.thumb_url
          ? thumb(
              fr.label,
              fr.thumb_url,
              `${d.width || 16}/${d.height || 10}`,
              d.orientation,
              "",
              fr.title,
              vm.displays[0]?.id === d.id,
              d,
              qbHtml
            )
          : `<span class="lu-thumb ${d.orientation || "l"} empty note-${esc(ph.state)}" style="--ar:${d.width || 16}/${d.height || 10};--arw:${d.width || 16};--arh:${d.height || 10}" role="img" aria-label="${esc(ph.title)}" title="${esc(ph.title)}"><span class="lu-thumb-note">${ico(phIcon[ph.state] || "screen")}<b>${esc(ph.label)}</b>${ph.sub ? `<small>${esc(ph.sub)}</small>` : ""}</span></span>`;
    let tags = `<span class="lu-im" title="${esc(d.link.title)}">${ico(linkIcon)}<span>${esc(d.link.label)}</span></span>`;
    if (d.size_label)
      tags += `<span class="lu-im" title="${esc(d.spec)}">${ico(d.orientation === "p" ? "portrait" : "landscape")}<span>${esc(d.size_label)}</span></span>`;
    if (d.pause)
      tags += `<span class="lu-im" title="${esc(d.pause.title)}">${ico("moon")}<span>${esc(d.pause.label)}</span></span>`;
    tags += `<span class="lu-im" title="${d.refreshes_today} refreshes projected today">${ico("refresh")}<span>${d.refreshes_today}</span></span>`;
    const show = op
      ? `<p class="lu-show ${d.in_sync ? "sync" : "queued"}" title="${esc(d.show_title)}"><i class="lu-sdot" aria-hidden="true" title="${esc(d.status_title || d.show_title)}"></i><span class="lu-sr-only">${d.in_sync ? "Synced" : "Waiting to land"}</span><span class="k">Showing</span><b>${esc(op.name)}</b>${d.show_suffix ? `<span class="k">${esc(d.show_suffix)}</span>` : ""}</p>`
      : fr
        ? `<p class="lu-show frame" title="${esc(d.show_title)}"><i class="lu-sdot" aria-hidden="true"></i><span class="k">Showing</span><b>${esc(fr.label)}</b>${fr.since ? `<span class="k">since ${esc(fr.since)}</span>` : ""}</p>`
        : d.nothing_sent
          ? `<p class="lu-show unknown" title="${esc(d.show_title)}"><i class="lu-sdot" aria-hidden="true"></i><b>Nothing sent yet</b></p>`
          : `<p class="lu-show unknown" title="${esc(d.show_title)}"><i class="lu-sdot" aria-hidden="true"></i><span class="k">Showing</span><b>unknown</b>${d.show_note ? `<span class="k">${esc(d.show_note)}</span>` : ""}</p>`;
    const nl = d.next_line;
    const next = `<p class="lu-nextl" title="${esc(nl.title)}">${esc(nl.before)}${nl.at ? ` <b>${esc(nl.at)}</b>` : ""}${nl.after ? ` ${esc(nl.after)}` : ""}</p>`;
    const collapsed = ui && ui.collapsed ? ui.collapsed(d) : false;
    const showBar = !collapsed && !(ui && ui.view === "timeline");
    const today = showBar
      ? `<div class="lu-today"><div class="lu-today-bar">${dayTrack(d, d.day, editable)}${dayScale(d.day)}</div>${dayPick(d, d.day, vm.now.weekday)}</div>`
      : "";
    return (
      `<header class="lu-ds-hdr"><figure class="lu-now-thumb" title="${esc(qb ? qb.title : delivering ? ph.title : op ? `On screen: ${op.name}` : fr ? fr.title : ph.title)}">${thumbHtml}</figure>` +
      `<div class="lu-ds-name-row"><div class="lu-ds-title"><h2>${esc(d.name)}</h2>${d.settings_url ? `<a class="lu-gear" href="${esc(d.settings_url)}" title="${esc(d.name)} settings" aria-label="${esc(d.name)} settings">${ico("gear")}</a>` : ""}</div><span class="lu-ds-tags">${tags}</span></div>` +
      `<div class="lu-ds-state">${show}${next}</div>${today}` +
      `<button class="lu-fold lu-ds-fold" type="button" data-collapse="${esc(d.id)}" aria-expanded="${collapsed ? "false" : "true"}" aria-controls="lu-dsb-${esc(d.id)}" aria-label="${esc(d.name)}: collapse or expand">${ico("caret")}</button></header>`
    );
  }

  function display(vm, d, ui) {
    const collapsed = ui && ui.collapsed ? ui.collapsed(d) : false;
    const lineups = ui && ui.order ? ui.order(d.lineups) : d.lineups;
    let body = "";
    if (!collapsed) {
      if (ui && ui.view === "timeline") body = `<div class="lu-sched">${timeline(vm, d, ui)}</div>`;
      else if (d.lineups.length && !lineups.length && ui && ui.scopeEmpty)
        body = `<div class="lu-sched">${ui.scopeEmpty(d)}</div>`;
      else if (d.lineups.length)
        body = `<div class="lu-sched"><ol class="lu-rows">${lineups.map((l) => row(d, l, ui)).join("")}</ol></div>`;
      else
        body = '<div class="lu-sched"><p class="lu-none">No lineups on this display yet.</p></div>';
    }
    const pending = ui && ui.pendingBar ? ui.pendingBar(d) : "";
    return (
      `<section class="lu-ds${collapsed ? " is-collapsed" : ""}" data-display="${esc(d.id)}" id="display-${esc(d.id)}"><div class="lu-ds-card">${header(vm, d, ui)}` +
      `<div class="lu-ds-body" id="lu-dsb-${esc(d.id)}"${collapsed ? " hidden" : ""}>${body}${pending}</div></div></section>`
    );
  }

  // Today | All with nothing left to show on a display.
  function scopeEmpty(d) {
    const day = d.day || {};
    const when = !day.relative || day.relative === "today" ? "today" : day.label || "that day";
    return `<p class="lu-none lu-scope-empty">Nothing scheduled for ${esc(when)}. <button type="button" class="lu-btn-ghost sm" data-scope="all">Show all</button></p>`;
  }

  function unassigned(vm, ui) {
    const lineups = ui && ui.order ? ui.order(vm.unassigned) : vm.unassigned;
    return [false, true]
      .map((removed) => {
        const group = lineups.filter((l) => !!l.display_removed === removed);
        if (!group.length) return "";
        const name = removed ? "Display removed" : "Not on a display yet";
        const d = { id: "", orientation: "l", name };
        return `<section class="lu-ds lu-ds--orphans" data-display=""><div class="lu-ds-card"><header class="lu-ds-hdr lu-ds-hdr--plain"><div class="lu-ds-name-row"><h2>${name}</h2><span class="lu-ds-tags"><span class="lu-im">${ico("info")}<span>Pick a display in the editor to schedule these</span></span></span></div></header><div class="lu-ds-body"><div class="lu-sched"><ol class="lu-rows">${group.map((l) => row(d, l, ui)).join("")}</ol></div></div></div></section>`;
      })
      .join("");
  }

  function pendingBar(d, count, msg, err) {
    if (!count && !msg) return "";
    const m = msg
      ? `<span class="lu-pend-msg${err ? " err" : ""}">${ico("info")}${esc(msg)}</span>`
      : "";
    const c = count
      ? `<span class="lu-pend-count"><i class="lu-edot"></i>${count} unsaved change${count > 1 ? "s" : ""}</span><button type="button" class="lu-btn-ghost sm" data-undo="${esc(d.id)}">Undo</button><button type="button" class="lu-btn-save" data-save="${esc(d.id)}">Save</button>`
      : "";
    return `<div class="lu-pending" role="status">${m}${c}</div>`;
  }

  /* ---------------------------------------------------------------- peek */
  function peek(d, l, s) {
    // /push-step has no item selection support. The gallery is a viewer.
    const n = s.item_count;
    const items = [];
    for (let k = 0; k < n; k++) {
      const it = (s.items || [])[k];
      const label = it && it.label ? it.label : `${s.name} ${k + 1} of ${n}`;
      const url = (it && it.thumb_url) || s.thumb_url;
      items.push(
        `<li class="lu-pk-item">${thumb(s.name, url, s.aspect, d.orientation || s.orientation, "", label, false, d)}` +
          "</li>"
      );
    }
    return `<div class="lu-peek" role="dialog" aria-modal="true" aria-label="${esc(s.name)}: ${n} items"><div class="lu-peek-head"><b>${esc(s.name)}</b><span class="lu-mono">${n} items · one per visit</span><button type="button" class="lu-ibtn" data-peek-close title="Close" aria-label="Close">${ico("x")}</button></div><ol class="lu-peek-grid ${d.orientation || "l"}">${items.join("")}</ol></div>`;
  }

  /* ---------------------------------------------------------------- timeline */
  // The week as a grid: one row per day (Mon-Sun), each a rounded rod with
  // Quiet hours, lineup blocks and the stretches where a frame is held. A
  // row is folded to that rod; opened, every block shows its steps as
  // thumbnails at their advance times, with the now and next marks on
  // today. lineups.js places the thumbnails (fit) and toggles rows.
  // "holds Night sky · from Tue": what a held stretch keeps on the panel,
  // and the day it began when it was carried over midnight. Older
  // view-models without ``carried`` fall back to the segment's title.
  const HOLD_RE = /keeps (.+) on the panel/;
  function holdLabel(d, day, s) {
    const m = HOLD_RE.exec(s.title || "");
    const what = (s.carried && s.carried.name) || (m ? m[1] : "");
    if (!what) return "";
    let from = s.from_day_label || "";
    if (!from && s.from_prev_day === undefined && s.start_min === 0) {
      const prev = d.week[(day.weekday + 6) % 7];
      from = prev ? prev.label : "";
    }
    return `holds ${what}${from ? ` · from ${from}` : ""}`;
  }

  // A block whose refreshes all show one dashboard draws one thumbnail with
  // a "×N updates" badge instead of N identical pictures; the refresh times
  // move into the tooltip (first six, then "+N more"). A block with a single
  // refresh, or with two or more different dashboards, keeps one thumb per
  // refresh.
  const stepKey = (st) => st.dashboard_id || `${st.name}\0${st.thumb_url}`;
  const sameDashboard = (steps) =>
    steps.length > 1 && steps.every((st) => stepKey(st) === stepKey(steps[0]));
  function collapsedStep(steps, total) {
    const times = steps.slice(0, 6).map((st) => clock(st.minute));
    const more = total - times.length;
    const roles = steps.map((st) => st.role);
    const role = ["now", "waiting", "next"].find((r) => roles.includes(r)) || "";
    return {
      ...steps[0],
      role,
      title: `${steps[0].name}, updates at ${times.join(", ")}${more > 0 ? `, +${more} more` : ""}`,
    };
  }

  // Each advance in a block: the segment's ``steps`` (name, time, current,
  // next) when the view-model has them, else its lane points.
  function blockSteps(s, l) {
    const roles = Object.fromEntries((s.points || []).map((pt) => [pt.minute, pt.role || ""]));
    if (Array.isArray(s.steps)) {
      return s.steps.map((st) => {
        const role = roles[st.at_min] || (st.current ? "now" : st.next ? "next" : "");
        return {
          minute: st.at_min,
          dashboard_id: st.dashboard_id,
          name: st.name,
          thumb_url: st.thumb_url,
          aspect: st.aspect,
          role,
        };
      });
    }
    return (s.points || [])
      .map((pt) => {
        const st = l ? l.steps[pt.step_index] : null;
        return st
          ? {
              minute: pt.minute,
              name: st.name,
              thumb_url: st.thumb_url,
              aspect: st.aspect,
              role: pt.role || "",
            }
          : null;
      })
      .filter(Boolean);
  }

  // One day of the week grid. Blocks are the same draggable .lu-blk as the
  // day bar (lineups.js drives both); an opened row grows and every block
  // shows its steps as thumbnails (placed by lineups.js), today's live block
  // its next advance. Holds are a quiet arrow on the rod, labelled in open
  // rows; a single push is a dot.
  function weekRow(vm, d, day, open, sel, editableIds) {
    const i = day.weekday;
    const byId = Object.fromEntries(d.lineups.map((l) => [l.id, l]));
    const orient = d.orientation || "l";
    const nowAbs =
      day.is_today && day.now_min !== null && day.now_min !== undefined ? day.now_min : null;
    let html = "";
    day.segments.forEach((s, k) => {
      const hold = s.kind === "hold";
      const push = s.kind === "push";
      if (hold) {
        html += `<div class="lu-blk hold${s.live ? " live" : ""}" style="left:${s.left_pct}%;width:${s.width_pct}%" data-lineup="${esc(s.lineup_id)}" data-display="${esc(d.id)}" title="${esc(s.title)}"></div>`;
        if (s.end_min - s.start_min >= 20) {
          const past = nowAbs !== null && nowAbs >= s.start_min;
          const acc = s.is_current || s.live;
          html += `<i class="lu-harrow${past ? "" : " dash"}${acc ? " acc" : ""}" style="left:calc(${s.left_pct}% + 2px);width:calc(${s.width_pct}% - 6px)" role="img" aria-label="${esc(s.title)}" title="${esc(s.title)}"></i>`;
          const lab = holdLabel(d, day, s);
          if (lab) {
            const c = s.carried;
            const pic = c
              ? thumb(c.name, c.thumb_url, c.aspect, c.orientation || orient, "", null, false, d)
              : "";
            html += `<span class="lu-wb-hold" data-hw="${s.width_pct}" style="left:calc(${s.left_pct}% + ${s.from_prev_day ? 24 : 8}px)">${pic}<span>${esc(lab)}</span></span>`;
          }
        }
        return;
      }
      const ed = editableIds.includes(s.lineup_id);
      const cls = [
        "lu-blk",
        push ? "push" : "",
        s.live ? "live" : "",
        s.planned ? "plan" : "",
        open && !push ? "open" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const edAttr = ed ? " data-edit" : "";
      const role = push
        ? ""
        : ` tabindex="0" role="button" aria-expanded="${open}" aria-label="${esc(s.title)}. Enter shows or hides this day's dashboards${ed ? "; arrow keys move it, Shift+arrows resize it" : ""}"`;
      let inner =
        ed && !push
          ? '<i class="lu-h" data-h="l" aria-hidden="true"></i><i class="lu-h" data-h="r" aria-hidden="true"></i>'
          : "";
      if (!push) {
        const l = byId[s.lineup_id];
        const steps = blockSteps(s, l);
        const span = Math.max(1, s.end_min - s.start_min);
        inner += ticks(s, day);
        const one = sameDashboard(steps);
        const total = Math.max(steps.length, s.steps_total || 0);
        const shown = one ? [collapsedStep(steps, total)] : steps;
        shown.forEach((st, j) => {
          const ttl = st.title || `${st.name} at ${clock(st.minute)}`;
          const aria = one ? ` role="img" aria-label="${esc(ttl)}"` : "";
          inner += `<span class="lu-wb-th${st.role ? " r-" + st.role : ""}" data-j="${j}" data-role="${st.role}" title="${esc(ttl)}"${aria}>${thumb(st.name, st.thumb_url, st.aspect, orient, "", ttl, false, d)}</span>`;
          if (one)
            inner += `<span class="lu-wb-rep" aria-hidden="true" title="${esc(ttl)}">\u00d7${total}<span class="lu-wb-rep-w"> updates</span></span>`;
        });
        steps.forEach((st) => {
          if (st.role === "next" && day.is_today) {
            const x = Math.round(((st.minute - s.start_min) / span) * 10000) / 100;
            inner += `<i class="lu-tnext" style="left:${x}%" aria-hidden="true"></i><span class="lu-tnext-l" style="left:calc(${x}% + 5px)"><span>next ${clock(st.minute)}</span><span class="lu-nx-d"> \u00b7 ${esc(st.name)}</span></span>`;
          }
        });
        inner += `<span class="lu-blk-l">${esc(s.label)}${s.pending ? '<i class="lu-edot" title="Unsaved change"></i>' : ""}</span>`;
      }
      html += `<div class="${cls}" style="left:${s.left_pct}%;width:${s.width_pct}%" data-b="${k}" data-lineup="${esc(s.lineup_id)}" data-display="${esc(d.id)}" title="${esc(s.title)}"${edAttr}${role}>${inner}</div>`;
    });
    (day.pause || []).forEach((p) => {
      html += `<i class="lu-pz" style="left:${p.left_pct}%;width:${p.width_pct}%" title="${esc(p.title)}">${ico("moon")}</i>`;
      html += (p.over || [])
        .map(
          (o) =>
            `<i class="lu-pz-over" style="left:${o.left_pct}%;width:${o.width_pct}%" title="${esc(p.title)}"></i>`
        )
        .join("");
    });
    if (nowAbs !== null)
      html += `<i class="lu-nowm" style="left:${day.now_pct}%" title="Now ${clock(nowAbs)}"></i>`;
    const xopen = open && day.segments.some((s) => s.kind === "window");
    const rid = `lu-tlr-${esc(d.id)}-${i}`;
    const notOn = d.lineups.filter((l) => l.window && !l.days[i]).map((l) => l.name);
    const labTitle = `${day.label}${day.is_today ? " (today)" : ""}${notOn.length ? ". Not running: " + notOn.join(", ") : ""}`;
    return (
      `<div class="lu-tlg-row${day.is_today ? " is-today" : ""}${open ? " is-open" : ""}${sel ? " is-sel" : ""}" data-day="${i}" data-display="${esc(d.id)}">` +
      `<button type="button" class="lu-tlg-lab" data-tl-row="${i}" data-display="${esc(d.id)}" aria-expanded="${open}" aria-controls="${rid}" title="${esc(labTitle)}">${day.label}</button>` +
      `<div class="lu-trk tl lu-tlg-trk ${orient}${xopen ? " xopen" : ""}" id="${rid}" role="group" aria-label="${esc(day.aria_label)}" data-day="${i}">${html}</div></div>`
    );
  }

  function timeline(vm, d, ui) {
    const sel = ui && ui.day ? ui.day(d) : d.selected_day;
    const isOpen = ui && ui.tlOpen ? (i) => ui.tlOpen(d, i) : () => false;
    const ro = ui && ui.barReadOnly;
    const editable = ro
      ? []
      : d.lineups
          .filter((l) => l.window && l.window.editable && !l.window.wraps && l.enabled)
          .map((l) => l.id);
    const allOpen = d.week.every((w) => isOpen(w.weekday));
    const rows = d.week
      .map((day) =>
        weekRow(
          vm,
          d,
          day,
          isOpen(day.weekday),
          day.weekday === sel && sel !== vm.now.weekday,
          editable
        )
      )
      .join("");
    return (
      `<div class="lu-tl"><div class="lu-tl-head"><p class="lu-lbl">Week</p><span class="lu-tl-hint">Click a block to see its dashboards</span>` +
      `<button type="button" class="lu-btn-ghost sm lu-tl-all" data-tl-all="${esc(d.id)}" data-mode="${allOpen ? "fold" : "unfold"}">${ico("updown")}<span aria-live="polite">${allOpen ? "Fold all" : "Unfold all"}</span></button></div>` +
      `<div class="lu-tl-scroll" data-tl-scroll="${esc(d.id)}"><div class="lu-tlg">` +
      `<div class="lu-tlg-head"><span class="lu-tlg-corner" aria-hidden="true"></span>${scale([0, 6, 12, 18, 24], false)}</div>${rows}</div></div></div>`
    );
  }

  /* ------------------------------------------------- pending-edit preview */
  // A lineup's window in day minutes: [start, end]; end === start for a push.
  function windowOf(l) {
    if (!l.window) return null;
    return [l.window.start_min, l.window.end_min];
  }

  function points(l, s, e) {
    const n = l.steps.length;
    const pts = [];
    if (l.kind === "daily") return [{ t: s, i: 0 }];
    if (l.kind === "interval") {
      const step = Math.max(1, l.step_minutes || 1);
      for (let t = s, k = 0; t < e; t += step, k++) pts.push({ t, i: k % n });
      return pts;
    }
    const dw = l.steps.map((x) => Math.max(1, x.dwell_minutes || 1));
    for (let t = s, k = 0; t < e && k < 10000; k++) {
      pts.push({ t, i: k % n });
      t += dw[k % n];
    }
    return pts;
  }

  function blocksOf(lineups) {
    const blocks = [];
    lineups.forEach((l) => {
      const w = windowOf(l);
      if (!w || !l.enabled || !l.steps.length) return;
      l.days.forEach((on, day) => {
        if (!on) return;
        const s = day * DAY_MIN + w[0];
        const e = day * DAY_MIN + (l.window.is_point ? w[0] : w[1]);
        blocks.push({ l, s, e, push: !!l.window.is_point, pts: points(l, s, e) });
      });
    });
    blocks.sort((a, b) => a.s - b.s);
    const holds = [];
    blocks.forEach((b, i) => {
      const nx = blocks[(i + 1) % blocks.length];
      const ns = i + 1 < blocks.length ? nx.s : nx.s + WEEK_MIN;
      if (ns > b.e)
        holds.push({
          l: b.l,
          s: b.e,
          e: ns,
          hold: true,
          next: nx,
          last: b.pts.length ? b.pts[b.pts.length - 1].i : 0,
        });
    });
    return { blocks, holds };
  }

  function previewDay(d, model, day, liveId, nowWeekday, nowMin) {
    const a = day * DAY_MIN;
    const z = a + DAY_MIN;
    const isToday = day === nowWeekday;
    const nowAbs = nowWeekday * DAY_MIN + nowMin;
    const pieces = [];
    [...model.blocks, ...model.holds].forEach((x) => {
      [0, WEEK_MIN, -WEEK_MIN].forEach((off) => {
        const s = x.s + off;
        const e = Math.max(x.e + off, s);
        if (x.push && s >= a && s < z) pieces.push({ x, s: s - a, e: s - a, push: true, off });
        else if (!x.push && e > a && s < z && e > s)
          pieces.push({ x, s: Math.max(s, a) - a, e: Math.min(e, z) - a, off });
      });
    });
    pieces.sort((p, q) => p.s - q.s);
    const segments = pieces.map((p) => {
      const l = p.x.l;
      const hold = !!p.x.hold;
      const kind = hold ? "hold" : p.push ? "push" : "window";
      const live =
        isToday && !hold && l.id === liveId && nowAbs >= p.x.s + p.off && nowAbs < p.x.e + p.off;
      const title = hold
        ? `${l.name}: keeps ${l.steps[p.x.last] ? l.steps[p.x.last].name : ""} on the panel until ${p.x.next ? p.x.next.l.name : ""}`
        : p.push
          ? `${l.name} at ${clock(p.s)}`
          : `${l.name} ${clockRange(p.s, p.e)}`;
      const ticks = [];
      const pts = [];
      if (!hold) {
        let nextDone = false;
        p.x.pts.forEach((pt) => {
          const m = pt.t + p.off - a;
          if (m < p.s || m > p.e || (m === p.e && !p.push)) return;
          pts.push({
            minute: m,
            step_index: pt.i,
            dashboard_id: l.steps[pt.i] ? l.steps[pt.i].dashboard_id : "",
            role: "",
          });
          if (!p.push && m > p.s && m < p.e) {
            const nxt = live && !nextDone && pt.t + p.off > nowAbs;
            if (nxt) nextDone = true;
            ticks.push({
              minute: m,
              pct: Math.round(((m - p.s) / (p.e - p.s)) * 1000000) / 10000,
              next: nxt,
            });
          }
        });
      }
      return {
        lineup_id: l.id,
        lineup_name: l.name,
        kind,
        start_min: p.s,
        end_min: p.e,
        left_pct: pct(p.s),
        width_pct: p.push ? 0.4 : pct(p.e - p.s),
        live,
        planned: !isToday && !hold,
        title,
        label: hold ? "" : l.name,
        ticks,
        points: pts,
        pending: !hold && !!l.pending,
      };
    });
    const names = segments
      .filter((s) => s.kind !== "hold")
      .map((s) => `${s.lineup_name} ${clock(s.start_min)}`);
    const base = d.week[day];
    return Object.assign({}, base, {
      segments,
      aria_label: `${DAYS[day]}: ${names.join(", ") || "no lineups"}`,
    });
  }

  function overlapsPause(pause, s, e) {
    if (!pause) return false;
    const a = pause.start_min;
    const b = pause.end_min;
    const parts =
      a > b
        ? [
            [a, DAY_MIN],
            [0, b],
          ]
        : [[a, b]];
    const segs =
      e > DAY_MIN
        ? [
            [s, DAY_MIN],
            [0, e - DAY_MIN],
          ]
        : [[s, e]];
    return segs.some(([ss, ee]) =>
      parts.some(([x, y]) => (ss === ee ? ss >= x && ss < y : ss < y && ee > x))
    );
  }

  /**
   * A copy of display ``d`` with the unsaved edits in ``pend`` (lineup id ->
   * {start_min, end_min, days[7]}) applied: rows show the new window and
   * days, the day bars are recomputed. ``nowWeekday``/``nowMin`` from vm.now.
   */
  function withPending(d, pend, nowWeekday, nowMin) {
    if (!pend || !Object.keys(pend).some((id) => d.lineups.some((l) => l.id === id))) return d;
    const copy = JSON.parse(JSON.stringify(d));
    copy.lineups.forEach((l) => {
      const p = pend[l.id];
      if (!p || !l.window) return;
      const w = l.window;
      const orig = { s: w.start_min, e: w.end_min, days: l.days.slice() };
      w.start_min = p.start_min;
      w.end_min = w.is_point ? p.start_min : p.end_min;
      w.start = hhmm(w.start_min);
      w.end = w.is_point ? null : endHhmm(w.end_min);
      w.label = w.is_point ? `at ${clock(w.start_min)}` : clockRange(w.start_min, w.end_min);
      w.overlaps_pause = overlapsPause(d.pause, w.start_min, w.end_min);
      if (w.overlaps_pause && d.pause)
        w.pause_title = `Part of this window falls in Quiet hours (${d.pause.label}); nothing is pushed then`;
      l.days = p.days.slice();
      l.pending_days = l.days.map((on, i) => (on !== orig.days[i] ? i : -1)).filter((i) => i >= 0);
      l.pending = orig.s !== w.start_min || orig.e !== w.end_min || l.pending_days.length > 0;
      const sel = l.days.map((on, i) => (on ? i : -1)).filter((i) => i >= 0);
      l.days_text = sel.length === 7 ? "every day" : sel.map((i) => DAYS[i]).join(", ");
    });
    const liveSeg = (
      d.day.is_today ? d.day : d.week.find((x) => x.is_today) || d.day
    ).segments.find((s) => s.live && s.kind !== "hold");
    const liveId = liveSeg ? liveSeg.lineup_id : null;
    const model = blocksOf(copy.lineups);
    copy.week = DAYS.map((_, i) => previewDay(copy, model, i, liveId, nowWeekday, nowMin));
    copy.day = copy.week[copy.selected_day];
    return copy;
  }

  window.TesseraeLineupsRender = {
    DAYS,
    esc,
    ico,
    hhmm,
    endHhmm,
    clock,
    updateInLabel,
    clockRange,
    setTimeCycle,
    thumb,
    dayTrack,
    scale,
    startLabels,
    dayScale,
    dayPick,
    scopeEmpty,
    dayPop,
    row,
    header,
    display,
    unassigned,
    pendingBar,
    peek,
    timeline,
    withPending,
    overlapsPause,
  };
})();
