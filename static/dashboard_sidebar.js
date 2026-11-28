// The same dashboard sidebar serves the lineup editor and Lineups. Its callers
// own the lineup, item choices, and save policy.
(function () {
  "use strict";

  const R = window.TesseraeLineupsRender;
  const { esc, ico } = R;
  const ID = "dashboard-sidebar";
  const STRIP = 9;
  const GRID_PAGE = 18;
  const plural = (n, word) => `${n} ${n === 1 ? word.replace(/s$/, "") : word}`;
  const mins = (n) =>
    n >= 60
      ? n % 1440 === 0
        ? plural(n / 1440, "days")
        : `${Math.floor(n / 60)} h${n % 60 ? ` ${n % 60} min` : ""}`
      : `${n} min`;
  const ordinal = (n) =>
    n + (n % 100 >= 11 && n % 100 <= 13 ? "th" : { 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");

  function mount(root, onClose) {
    const wrap = document.createElement("div");
    wrap.className = "ds-shell";
    wrap.inert = true;
    wrap.innerHTML = `<div class="ds-scrim" data-ds-close aria-hidden="true"></div><div class="ds-panel" id="${ID}" aria-labelledby="ds-title"></div>`;
    root.append(wrap);
    const panel = wrap.lastElementChild;
    const phone = matchMedia("(max-width: 700px)");
    let key = null;
    let collapsed = new Set(["conditions"]);
    let unlock = [];
    let markup = "";

    function syncMode() {
      const modal = phone.matches && key !== null;
      panel.setAttribute("role", modal ? "dialog" : "complementary");
      if (modal) panel.setAttribute("aria-modal", "true");
      else panel.removeAttribute("aria-modal");
      unlock.forEach((el) => (el.inert = false));
      unlock = [];
      if (modal) {
        for (let el = wrap; el !== document.body; el = el.parentElement) {
          for (const sibling of el.parentElement.children) {
            if (sibling !== el && !sibling.inert && !sibling.matches(".lu-lb, dialog[open]")) {
              sibling.inert = true;
              unlock.push(sibling);
            }
          }
        }
        if (!panel.contains(document.activeElement)) focus();
      }
    }
    function focus() {
      const target = panel.querySelector(".ds-close");
      target?.focus({ preventScroll: true });
      if (target && document.activeElement !== target) {
        const activeKey = key;
        requestAnimationFrame(() => {
          if (key === activeKey && !panel.contains(document.activeElement))
            panel.querySelector(".ds-close")?.focus({ preventScroll: true });
        });
      }
    }
    function openThumbnail(button) {
      const photo = button.querySelector("img");
      const lightbox = window.TesseraeLineupsLightbox;
      if (!photo || !lightbox) return;
      const { dsPhoto: title, fk } = button.dataset;
      const width = photo.naturalWidth || 16;
      const height = photo.naturalHeight || 10;
      const source = new URL(photo.currentSrc || photo.src);
      if (source.origin === location.origin && source.pathname.includes("/compose/thumbs/v1/"))
        source.searchParams.set("lg", "1");
      lightbox.open(root, {
        title,
        img: { src: button.dataset.dsLarge || source.href, alt: title },
        aspect: `${width}/${height}`,
        orientation: height > width ? "p" : "l",
        onClose: () => {
          const opener = panel.querySelector(`[data-fk="${CSS.escape(fk)}"]`);
          if (opener) opener.focus({ preventScroll: true });
          else if (key !== null) focus();
        },
      });
    }
    panel.addEventListener(
      "click",
      (event) => {
        const thumbnail = event.target.closest("[data-ds-photo]");
        if (thumbnail && !thumbnail.disabled) openThumbnail(thumbnail);
      },
      true
    );

    phone.addEventListener("change", syncMode);
    wrap.addEventListener("click", (event) => {
      if (event.target.closest("[data-ds-close]")) {
        event.stopPropagation();
        onClose();
      }
      const button = event.target.closest("[data-ds-section]");
      if (!button) return;
      const name = button.dataset.dsSection;
      const open = collapsed.has(name);
      if (open) collapsed.delete(name);
      else collapsed.add(name);
      button.setAttribute("aria-expanded", open);
      document.getElementById(button.getAttribute("aria-controls")).inert = !open;
    });
    document.addEventListener("keydown", (event) => {
      if (key === null || event.defaultPrevented || document.querySelector("dialog[open], .lu-lb"))
        return;
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
      if (event.key === "Tab" && phone.matches) {
        const controls = [
          ...panel.querySelectorAll("button, a[href], input, select, textarea, summary"),
        ].filter((el) => !el.disabled && !el.closest("[inert]") && el.getClientRects().length);
        const end = event.shiftKey ? controls[0] : controls.at(-1);
        if (!panel.contains(document.activeElement) || document.activeElement === end) {
          event.preventDefault();
          (event.shiftKey ? controls.at(-1) : controls[0])?.focus();
        }
      }
    });
    function section(name, label, summary, body) {
      const open = !collapsed.has(name);
      return `<section class="ds-section"><h3><button type="button" id="ds-heading-${name}" data-fk="ds-${name}" data-ds-section="${name}" aria-controls="ds-${name}" aria-expanded="${open}"><span>${label}</span><small>${esc(summary)}</small>${ico("chev")}</button></h3><div id="ds-${name}" class="ds-region" role="region" aria-labelledby="ds-heading-${name}"${open ? "" : " inert"}><div><div class="ds-section-body">${body}</div></div></div></section>`;
    }

    function render(next, model) {
      const changed = next !== key;
      key = next;
      document.documentElement.classList.toggle("has-dashboard-sidebar", key !== null);
      wrap.inert = key === null;
      if (key === null) {
        if (changed) syncMode();
        delete panel.dataset.panel;
        panel.replaceChildren();
        markup = "";
        return;
      }
      if (changed) collapsed = new Set(["conditions"]);
      const { p, d, l, s, choice, timing, photos, conditions, actions, edit, status, home } = model;
      const widgets = p.widgets || [];
      const links = widgets
        .map((w) => {
          const href = edit + "?widget=" + encodeURIComponent(w.id);
          const label = `Edit ${w.name} in the dashboard editor (leaves this page)`;
          return `<li><i class="ds-widget-icon ph ${esc(w.icon || "ph-squares-four")}" aria-hidden="true"></i><span><b>${esc(w.name)}</b><small>${esc([w.position, w.detail].filter(Boolean).join(" · "))}</small></span><a class="ds-link" data-fk="ds-widget-${esc(w.id)}" href="${esc(href)}" aria-label="${esc(label)}" title="${esc(label)}">Edit <i class="ph ph-arrow-square-out" aria-hidden="true"></i></a></li>`;
        })
        .join("");
      const pill = s.pill
        ? `<span class="lu-pill p-${esc(s.pill.kind)}" title="${esc(s.pill.title)}">${s.pill.kind === "now" ? ico("play") : ""}${esc(s.pill.label)}</span>`
        : "";
      const [width, height] = (s.aspect || "16/10").split("/").map(Number);
      const scroll = changed ? 0 : panel.querySelector(".ds-body")?.scrollTop || 0;
      panel.dataset.panel = p.id;
      panel.dataset.display = d.id || "";
      const nextMarkup =
        `<header class="ds-header"><div class="ds-heading"><i class="ds-widget-icon ph ${esc(p.icon || "ph-squares-four")}" aria-hidden="true"></i><div class="ds-heading-copy"><div class="ds-name"><h2 id="ds-title">${esc(p.name)}</h2>${edit ? `<a class="lu-ibtn ds-gear" data-fk="ds-gear" href="${esc(edit)}" data-edit-dashboard="${esc(p.id)}" title="Edit dashboard" aria-label="Edit dashboard ${esc(p.name)}">${ico("gear")}</a>` : ""}</div><div class="ds-soft">${esc(p.subtitle)}</div></div><button type="button" class="lu-ibtn ds-close" data-fk="ds-close" data-ds-close title="Close (Esc)" aria-label="Close ${esc(p.name)} details">${ico("x")}</button></div><div class="ds-status">${pill}<span>${ordinal(s.index + 1)} ${s.position?.endsWith("choice") ? "choice" : "dashboard"} in ${esc(l.name)}</span>${home ? `<span>${ico("star")} Home</span>` : ""}${status ? `<span class="ds-saved" role="status">${status === "Saved" ? ico("check") : ""}${esc(status)}</span>` : ""}</div></header>` +
        `<div class="ds-body"><div class="ds-preview" style="--ds-arw:${width || 16};--ds-arh:${height || 10}">${R.stepHtml(
          d,
          l,
          s,
          {
            preview: true,
            pushOverlay: false,
            editGear: false,
            zoomBeside: true,
            itemChoice: () => choice,
            tileExtra: () => model.duplicate || "",
          }
        )}<div class="ds-preview-meta">${p.refresh ? `<span title="Updates while shown">${ico("refresh")} ${esc(mins(p.refresh))}</span>` : ""}<span>${esc(model.previewLabel || (s.composition_digest ? "As last sent" : "Dashboard preview"))}</span></div>${model.previewNotice ? `<div class="ds-preview-note" role="status">${esc(model.previewNotice)}</div>` : ""}</div>` +
        (widgets.length > 1
          ? section(
              "widgets",
              "Widgets",
              `${widgets.length}: ${widgets.map((w) => w.name).join(", ")}`,
              `<ul class="ds-widgets">${links}</ul>`
            )
          : "") +
        (photos
          ? section(
              "photos",
              p.items.noun === "photos" ? "Photos" : "Items",
              photos.summary,
              photos.body
            )
          : "") +
        (timing ? section("timing", "Timing", timing.summary, timing.body) : "") +
        section("conditions", "Conditions", conditions.summary, conditions.body) +
        `</div><footer class="ds-footer"><div>${actions.links}</div><div>${actions.buttons}</div></footer>`;
      if (!changed && nextMarkup === markup) return;
      markup = nextMarkup;
      const active = panel.contains(document.activeElement) ? document.activeElement : null;
      const fk = active?.dataset.fk;
      const selection =
        active && typeof active.selectionStart === "number"
          ? [active.selectionStart, active.selectionEnd]
          : null;
      const picker = changed ? null : panel.querySelector("[data-condition-picker]");
      const template = document.createElement("template");
      template.innerHTML = nextMarkup;
      const incoming = template.content.querySelector("[data-condition-picker]");
      if (
        picker &&
        incoming &&
        picker.querySelector("textarea").value === incoming.querySelector("textarea").value
      )
        incoming.replaceWith(picker);
      panel.replaceChildren(template.content);
      panel.querySelector(".ds-body").scrollTop = scroll;
      if (changed) syncMode();
      else if (active && picker?.contains(active) && active.isConnected)
        active.focus({ preventScroll: true });
      else if (fk) {
        const back = panel.querySelector(`[data-fk="${CSS.escape(fk)}"]`);
        if (back && !back.disabled) {
          back.focus({ preventScroll: true });
          if (selection) back.setSelectionRange(...selection);
        } else focus();
      } else if (active) focus();
    }
    return { render, panel, focus, isMobile: () => phone.matches };
  }

  function conditionBody(id, name, raw, editing, ok) {
    if (!editing) {
      return `<p class="ds-soft">Shows whenever the lineup runs.</p><button type="button" class="ds-link" data-add-cond="${esc(id)}" data-fk="addcond-${esc(id)}" aria-label="Add a condition for ${esc(name)}">Add a condition</button>`;
    }
    const verdict =
      ok === true
        ? "Conditions pass"
        : ok === false
          ? "Conditions fail"
          : raw.trim() && raw.trim() !== "[]"
            ? "Current result unavailable"
            : "";
    const verdictIcon = ok === true ? "check" : ok === false ? "x" : "info";
    const verdictClass = ok === true ? "pass" : ok === false ? "fail" : "";
    return `<p class="ds-soft">Skipped while any condition fails.</p>${verdict ? `<p class="ds-verdict ${verdictClass}">${ico(verdictIcon)}${verdict}</p>` : ""}<div class="condition-picker" data-condition-picker><textarea rows="2" spellcheck="false" data-cond="${esc(id)}" data-fk="cond-${esc(id)}" placeholder="[]" aria-label="Conditions for ${esc(name)}">${esc(raw)}</textarea></div>`;
  }

  function timing(s, context) {
    const { cycle, interval, update, keepReady, cadence, readyLabel, warning } = context;
    const every = (n) => (n ? `every ${mins(n)}` : "off");
    const row = (key, fk, label, value, values, help = "") =>
      `<label class="ds-setting"><span>${esc(label)}</span><select data-${key}="${esc(s.id)}" data-fk="${fk}-${esc(s.id)}">${values.map(([v, text]) => `<option value="${v}"${v === value ? " selected" : ""}>${esc(text)}</option>`).join("")}</select>${help ? `<small class="ds-soft">${help}</small>` : ""}</label>`;
    const options = (values, active, fmt) =>
      [...new Set([...values, ...(active == null ? [] : [active])])]
        .sort((a, b) => a - b)
        .map((v) => [v, fmt(v)]);
    const summary = [];
    const rows = [];
    if (cycle) {
      summary.push(`Shows ${mins(s.dwell || interval)}`);
      rows.push(
        row("dwell", "dwell", "Shows for", s.dwell ?? "", [
          ["", `Lineup default (${mins(interval)})`],
          ...options([5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240], s.dwell, mins),
        ])
      );
    }
    if (update) {
      summary.push(s.refresh ? `Updates ${every(s.refresh)} while shown` : "Updates when sent");
      rows.push(
        row(
          "updates",
          "upd",
          "Updates while shown",
          s.refresh,
          options([0, 1, 5, 10, 15, 30, 60, 120, 360, 720, 1440], s.refresh, (v) =>
            v ? every(v) : "no, only when sent"
          ),
          "Changes this dashboard everywhere."
        )
      );
    }
    if (keepReady) {
      summary.push(`Ready ${every(s.override ?? cadence)}`);
      rows.push(
        row("override", "ov", readyLabel, s.override ?? "", [
          ["", `Lineup's (${every(cadence)})`],
          ...options([0, 5, 15, 30, 60, 120, 360], s.override, every),
        ])
      );
    }
    return {
      summary: summary.join(" · ") || "Uses the lineup timing",
      body: rows.join("") + (warning || "") || '<p class="ds-soft">Uses the lineup timing.</p>',
    };
  }

  const readConditions = (raw) => {
    const rows = JSON.parse(raw.trim() || "[]");
    if (!Array.isArray(rows)) throw new Error("Conditions must be a JSON list.");
    return rows;
  };

  function itemTile(p, it, pinned, current, pinMode) {
    const label = it.label || `#${it.index + 1}`;
    const pic = it.thumb_url
      ? `<img src="${esc(it.thumb_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
      : `<span class="dxe-it-word">${esc(label)}</span>`;
    const mark = !!pinned && pinned === it.key;
    const inner = `<span class="dxe-it-pic">${pic}</span><span class="ds-item-number">#${it.index + 1}</span>`;
    const cls = `dxe-it${current === it.index || mark ? " is-marked" : ""}`;
    const title = esc(`#${it.index + 1} ${label}`);
    const attrs = `data-index="${it.index}" data-label="${esc(label)}" data-key="${esc(it.key)}"`;
    const thumb = `<button type="button" class="${cls}" ${pinMode ? "" : `data-preview-item="${esc(p.id)}"`} ${attrs} data-ds-photo="${esc(label)}" data-ds-large="${esc(it.preview_url || "")}" data-fk="photo-${esc(p.id)}-${esc(it.key)}" aria-haspopup="dialog" title="${title}" aria-label="${title}"${current === it.index ? ' aria-current="true"' : ""}>${inner}</button>`;
    if (!pinMode) return thumb;
    return `<div class="ds-pinnable">${thumb}<button type="button" class="ds-pin" data-pin-item="${esc(p.id)}" ${attrs} data-fk="pin-${esc(p.id)}-${esc(it.key)}" aria-pressed="${mark}" aria-label="Pin ${title}" title="Pin ${title}">${mark ? ico("check", "ds-item-check") : '<i class="ph ph-push-pin" aria-hidden="true"></i>'}</button></div>`;
  }

  function historyThumbnail(id, src, label, index) {
    return `<button type="button" class="dxe-it is-render" data-ds-photo="${esc(label)}" data-fk="history-${esc(id)}-${index}" aria-haspopup="dialog" title="${esc(label)}"><span class="dxe-it-pic"><img src="${esc(src)}" alt="" loading="lazy" referrerpolicy="no-referrer"></span><span class="dxe-it-l">${esc(label)}</span></button>`;
  }

  function childrenHtml(p, state) {
    const it = p.items || {};
    const { pinned, shape, strip, grid, choice, current, canDraw, onScreen } = state;
    if (it.mode === "pick") {
      const pinMode = !!it.pinnable && !!pinned;
      const head =
        `<div class="dxe-kids-head"><span class="dxe-soft">${pinned ? `Shows the chosen one.` : it.order === "random" ? `A random one each visit.` : `Rotates through them.`}</span>` +
        (it.pinnable
          ? `<span class="lu-seg dxe-seg sm" role="group" aria-label="${esc(p.name)}: rotate items or show chosen"><button type="button" data-pin-mode="${esc(p.id)}" data-mode="walk" data-fk="pinmode-${esc(p.id)}-walk" aria-pressed="${!pinned}">Rotate all</button><button type="button" data-pin-mode="${esc(p.id)}" data-mode="pin" data-fk="pinmode-${esc(p.id)}-pin" aria-pressed="${!!pinned}">Only chosen</button></span>`
          : "") +
        // A pin is the dashboard's own setting, so it moves every lineup that shows it.
        (it.pinnable && p.shared
          ? `<small class="dxe-soft dxe-shared">Applies to this dashboard in every lineup.</small>`
          : "") +
        `</div>`;
      let tiles = "";
      if (!strip || strip.busy)
        tiles = Array.from(
          { length: Math.min(STRIP, it.count || STRIP) },
          () =>
            `<span class="dxe-it is-ghost" aria-hidden="true"><span class="dxe-it-pic"></span></span>`
        ).join("");
      else if (strip.err) tiles = `<span class="dxe-soft">${esc(strip.err)}</span>`;
      else {
        const gridOpen = !!grid?.open;
        tiles = strip.data.items
          .slice(0, gridOpen ? 0 : STRIP)
          .map((x) => itemTile(p, x, pinned, current, pinMode))
          .join("");
        const more = it.count - Math.min(STRIP, strip.data.items.length);
        const g = grid;
        if (more > 0)
          tiles += `<button type="button" class="dxe-more" data-grid="${esc(p.id)}" data-fk="grid-${esc(p.id)}" aria-expanded="${!!(g && g.open)}">${ico("layers")}<span><b>${g && g.open ? "Hide" : `+${more}`}</b><small>${g && g.open ? "" : "Browse"}</small></span></button>`;
      }
      return `<div class="dxe-kids${it.fit ? " is-fit" : ""}"${shape || ""} aria-busy="${!strip || !!strip.busy}">${head}<div class="dxe-strip">${tiles}</div>${gridHtml(p, grid, pinned, current)}</div>`;
    }
    if (it.empty) {
      // A list widget that listed nothing: what it last sent, to look at only (those may
      // be filtered out now or the wrong shape), and a line saying why there is no list.
      const sent = (Array.isArray(p.recent) ? p.recent : []).slice(0, STRIP);
      const lastLabel = onScreen ? "On screen" : "Last sent";
      const tiles = sent
        .map((r, k) => historyThumbnail(p.id, r.thumb, k === 0 ? lastLabel : "Sent earlier", k))
        .join("");
      return (
        `<div class="dxe-kids dxe-recent is-empty"${shape || ""}><div class="dxe-kids-head">${sent.length ? `<span class="dxe-mono">Recent photos</span>` : ""}` +
        `<span class="dxe-soft">No matches to list right now.</span></div>` +
        (tiles ? `<div class="dxe-strip">${tiles}</div>` : "") +
        `</div>`
      );
    }
    if (it.mode === "draw") {
      const c = choice;
      const hist = c && c.mode === "draw" ? c.hist : [];
      const pos = c && c.mode === "draw" ? c.pos : -1;
      const ok = canDraw;
      const tile = (k, pic, label, on, large = "") =>
        `<button type="button" class="dxe-it is-render${on ? " is-marked" : ""}" ${ok ? `data-draw-pos="${esc(p.id)}" data-pos="${k}" aria-pressed="${on}"` : ""} data-ds-photo="${esc(label)}" data-ds-large="${esc(large)}" data-fk="dpos-${esc(p.id)}-${k}" aria-haspopup="dialog" title="${esc(label)}"><span class="dxe-it-pic">${pic}</span><span class="dxe-it-l">${esc(label)}</span></button>`;
      // Newest first: pictures drawn here and not sent yet, then what this dashboard
      // actually sent (the push log), the latest marked. Up to STRIP in all.
      const sent = Array.isArray(p.recent) ? p.recent : [];
      const lastLabel = onScreen ? "On screen" : "Last sent";
      const drawn = hist
        .map((e, k) =>
          tile(
            k,
            e.thumb
              ? `<img src="${esc(e.thumb)}" alt="">`
              : `<span class="dxe-it-word">${e.state === "error" ? "!" : "…"}</span>`,
            e.state === "error" ? e.note : e.state === "ready" ? `Draw ${e.n}` : "Drawing…",
            k === pos,
            e.large || e.large_url
          )
        )
        .reverse();
      const sentTiles = (sent.length ? sent : p.thumb ? [{ thumb: p.thumb }] : []).map((r, k) => {
        const pic = `<img src="${esc(r.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
        return k === 0
          ? tile(-1, pic, lastLabel, pos < 0)
          : historyThumbnail(p.id, r.thumb, "Sent earlier", k);
      });
      const strip = [...drawn, ...sentTiles].slice(0, STRIP).join("");
      return (
        `<div class="dxe-kids dxe-recent"${shape || ""}><div class="dxe-kids-head"><span class="dxe-mono">Recent photos</span>` +
        `<span class="dxe-soft">${c && c.note ? esc(c.note) : "A random photo each time it's sent."}</span></div>` +
        `<div class="dxe-strip">${strip}</div></div>`
      );
    }
    return "";
  }

  function gridHtml(p, g, pinned, current) {
    if (!g || !g.open) return "";
    const it = p.items || {};
    const pinMode = !!it.pinnable && !!pinned;
    const data = g.data;
    const pages = data ? Math.max(1, Math.ceil(data.matched / GRID_PAGE)) : 1;
    const cur = Math.floor(g.offset / GRID_PAGE) + 1;
    let pager = "";
    if (pages > 1) {
      const nums = [];
      for (let n = 1; n <= pages; n++)
        if (n === 1 || n === pages || Math.abs(n - cur) <= 1) nums.push(n);
        else if (nums[nums.length - 1] !== "…") nums.push("…");
      pager =
        `<nav class="dxe-pager" aria-label="Pages">` +
        `<button type="button" class="lu-ibtn sm" data-gpage="${cur - 1}" data-fk="gprev-${esc(p.id)}"${cur <= 1 ? " disabled" : ""} aria-label="Previous page">${ico("chevl")}</button>` +
        nums
          .map((n) =>
            n === "…"
              ? `<span class="dxe-soft">…</span>`
              : `<button type="button" class="dxe-pg" data-gpage="${n}" data-fk="gpage-${esc(p.id)}-${n}"${n === cur ? ' aria-current="page"' : ""}>${n}</button>`
          )
          .join("") +
        `<button type="button" class="lu-ibtn sm" data-gpage="${cur + 1}" data-fk="gnext-${esc(p.id)}"${cur >= pages ? " disabled" : ""} aria-label="Next page">${ico("chev")}</button></nav>`;
    }
    const range = data
      ? data.matched
        ? `${g.offset + 1}–${Math.min(data.matched, g.offset + GRID_PAGE)} of ${data.matched}`
        : `None match`
      : "";
    const body = !data
      ? `<p class="dxe-soft dxe-loading">Loading…</p>`
      : `<div class="dxe-grid-items${g.busy ? " is-busy" : ""}"${g.busy ? ' aria-busy="true"' : ""}>${data.items.map((x) => itemTile(p, x, pinned, current, pinMode)).join("")}</div>`;
    return (
      `<div class="dxe-grid" data-grid-for="${esc(p.id)}">` +
      `<div class="dxe-grid-tb"><label class="lu-tb-search dxe-search">${ico("search")}<input type="search" data-fk="q-${esc(p.id)}" data-grid-q="${esc(p.id)}" value="${esc(g.q)}" placeholder="Search ${esc(plural(it.count, it.noun || "items"))}" aria-label="Search ${esc(p.name)}" autocomplete="off" spellcheck="false"></label>` +
      `<span class="dxe-mono dxe-range" aria-live="polite">${esc(range)}</span></div>` +
      body +
      pager +
      `</div>`
    );
  }

  function photos(p, state) {
    const body = childrenHtml(p, state);
    if (!body) return null;
    const it = p.items;
    const summary = it.empty
      ? "No matches right now"
      : state.pinned
        ? "Only the chosen item"
        : it.count
          ? `All ${plural(it.count, it.noun || "items")}, one per visit`
          : "Draws a picture each time";
    return { body, summary };
  }

  function duplicateControl(id, name, busy, preview = false) {
    return `<button type="button" class="lu-zoom dxe-duplicate" data-duplicate="${esc(id)}" data-fk="dup-${preview ? "preview-" : ""}${esc(id)}" title="Duplicate ${esc(name)}" aria-label="Duplicate ${esc(name)}${preview ? " from preview" : ""}"${busy ? " disabled" : ""}>${ico("copy")}</button>`;
  }

  async function duplicatePage(url) {
    const body = new FormData();
    body.append("respond", "json");
    const res = await fetch(url, { method: "POST", body, headers: { Accept: "application/json" } });
    const data = await res.json();
    if (!res.ok || !data.ok)
      throw new Error(data.error || "The dashboard could not be duplicated.");
    return data.page;
  }

  window.TesseraeDashboardSidebar = {
    ID,
    STRIP,
    GRID_PAGE,
    mount,
    conditionBody,
    readConditions,
    timing,
    photos,
    duplicateControl,
    duplicatePage,
  };
})();
