// Lineups page: the one lightbox. A large view of a display's picture or of a
// step's dashboard, optionally with the item chooser (previous / next) and
// Push now. lineups.js owns what it shows (the view-model, the chosen item,
// the push); this file only draws a model and reports what the person did, so
// the header thumbnail and the step thumbnails share it.
//
// It lives under [data-lu] but outside [data-lu-displays], so a redraw of the
// cards from /decks/view.json never touches it; lineups.js calls update() with
// the fresh model afterwards.
//
// Model (all optional except title):
//   title, sub            heading and the line under it
//   img                   {src, alt}; null draws the placeholder
//   loading               true while the picture is being drawn
//   notice                a line under the picture ("Could not draw this item")
//   aspect, orientation, gamut, greyLevels   the display's own look (the same
//                         data-gamut treatment as every Lineups thumbnail)
//   nav                   {text, label, prev?, next?, hasPrev?, hasNext?}; draws previous / next
//   push                  {label, title, busy, disabled}; draws the Push button
//   msg                   {text, kind: "ok" | "queued" | "err"}
//   onStep(delta), onPush(), onClose()
(function () {
  "use strict";

  const icons = () => {
    try {
      return JSON.parse(document.querySelector("[data-lu-icons]").textContent);
    } catch {
      return {};
    }
  };
  const ico = (name) =>
    `<svg class="lu-ico" viewBox="0 0 16 16" aria-hidden="true">${icons()[name] || ""}</svg>`;

  let el = null; // the open lightbox
  let m = null; // its latest model
  let release = null; // frees the inert page (lineups.js)
  let lastImg = "";

  const FOCUSABLE = "button:not(:disabled), a[href], input, [tabindex='0']";

  function skeleton() {
    return (
      `<div class="lu-lb" role="dialog" aria-modal="true" aria-labelledby="lu-lb-title" data-lb>` +
      `<div class="lu-lb-scrim" data-lb-close aria-hidden="true"></div>` +
      `<div class="lu-lb-card">` +
      `<header class="lu-lb-head"><div class="lu-lb-titles"><h2 id="lu-lb-title" data-lb-title></h2><p class="lu-lb-sub" data-lb-sub></p></div>` +
      `<button type="button" class="lu-ibtn lu-lb-x" data-lb-close title="Close" aria-label="Close">${ico("x")}</button></header>` +
      `<div class="lu-lb-stage" data-lb-stage><span class="lu-thumb l" data-lb-thumb><span class="lu-thumb-ph" aria-hidden="true"></span></span>` +
      `<span class="lu-lb-load" data-lb-load hidden role="status"><i class="lu-lb-spin" aria-hidden="true"></i><span>Drawing this one</span></span></div>` +
      `<p class="lu-lb-note" data-lb-note role="status" hidden></p>` +
      `<footer class="lu-lb-foot" data-lb-foot>` +
      `<div class="lu-lb-items" role="group" aria-label="Choose an item" data-lb-nav hidden>` +
      `<button type="button" class="lu-ibtn lu-lb-step" data-lb-step="-1" aria-label="Previous item" title="Previous item (Left arrow)">${ico("chevl")}</button>` +
      `<span class="lu-lb-pos" data-lb-pos aria-live="polite"></span>` +
      `<button type="button" class="lu-ibtn lu-lb-step" data-lb-step="1" aria-label="Next item" title="Next item (Right arrow)">${ico("chev")}</button></div>` +
      `<span class="lu-lb-msg" data-lb-msg role="status"></span>` +
      `<button type="button" class="lu-btn-save lu-lb-push" data-lb-push hidden>${ico("send")}<span>Push now</span></button>` +
      `</footer></div></div>`
    );
  }

  const $ = (sel) => el.querySelector(sel);

  function paint() {
    if (!el || !m) return;
    $("[data-lb-title]").textContent = m.title || "";
    const sub = $("[data-lb-sub]");
    sub.textContent = m.sub || "";
    sub.hidden = !m.sub;

    const thumb = $("[data-lb-thumb]");
    const ar = String(m.aspect || "16/10").split("/");
    thumb.className = `lu-thumb ${m.orientation || "l"}`;
    thumb.style.cssText = `--ar:${ar[0]}/${ar[1]};--arw:${ar[0]};--arh:${ar[1]}`;
    thumb.dataset.gamut = m.gamut || "color";
    thumb.dataset.greyLevels = String(m.greyLevels || 0);
    const src = (m.img && m.img.src) || "";
    let img = thumb.querySelector("img");
    if (src) {
      if (!img) {
        thumb.replaceChildren();
        img = document.createElement("img");
        img.decoding = "async";
        thumb.appendChild(img);
      }
      if (src !== lastImg) {
        lastImg = src;
        img.src = src;
      }
      img.alt = (m.img && m.img.alt) || "";
    } else if (img || !thumb.firstElementChild) {
      lastImg = "";
      thumb.replaceChildren();
      const ph = document.createElement("span");
      ph.className = "lu-thumb-ph";
      ph.setAttribute("aria-hidden", "true");
      ph.textContent = (m.title || "?").slice(0, 1);
      thumb.appendChild(ph);
    }
    el.classList.toggle("is-loading", !!m.loading);
    $("[data-lb-stage]").setAttribute("aria-busy", m.loading ? "true" : "false");
    $("[data-lb-load]").hidden = !m.loading;
    const note = $("[data-lb-note]");
    note.textContent = m.notice || "";
    note.hidden = !m.notice;

    const nav = $("[data-lb-nav]");
    nav.hidden = !m.nav;
    if (m.nav) {
      $("[data-lb-pos]").textContent = m.nav.text || "";
      nav.setAttribute("aria-label", m.nav.label || "Choose an item");
      const prev = el.querySelector('[data-lb-step="-1"]');
      const next = el.querySelector('[data-lb-step="1"]');
      prev.disabled = m.nav.hasPrev === false;
      next.disabled = m.nav.hasNext === false;
      for (const [b, label, dflt] of [
        [prev, m.nav.prev, "Previous item"],
        [next, m.nav.next, "Next item"],
      ]) {
        b.setAttribute("aria-label", label || dflt);
        b.title = label || dflt;
      }
    }
    const push = $("[data-lb-push]");
    push.hidden = !m.push;
    if (m.push) {
      push.querySelector("span").textContent = m.push.label || "Push now";
      push.title = m.push.title || "";
      push.disabled = !!(m.push.busy || m.push.disabled);
      push.setAttribute("aria-busy", m.push.busy ? "true" : "false");
    }
    const msg = $("[data-lb-msg]");
    msg.textContent = (m.msg && m.msg.text) || "";
    msg.className = `lu-lb-msg${m.msg ? " " + m.msg.kind : ""}`;
    $("[data-lb-foot]").hidden = !m.nav && !m.push;
  }

  function onKey(e) {
    if (!el) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      if (!m || !m.nav || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      e.preventDefault();
      if (m.onStep) m.onStep(e.key === "ArrowRight" ? 1 : -1);
    } else if (e.key === "Tab") {
      const items = [...el.querySelectorAll(FOCUSABLE)].filter((x) => x.getClientRects().length);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (!el.contains(document.activeElement)) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  function onClick(e) {
    if (e.target.closest("[data-lb-close]")) {
      close();
    } else if (e.target.closest("[data-lb-push]")) {
      if (m && m.push && !m.push.busy && m.onPush) m.onPush();
    } else {
      const s = e.target.closest("[data-lb-step]");
      if (s && m && m.onStep) m.onStep(Number(s.dataset.lbStep));
    }
  }

  /** Open under ``host`` (the [data-lu] element). ``lock(el)`` isolates the
      rest of the page and returns the function that undoes it. */
  function open(host, model, lock) {
    if (el) teardown();
    m = model;
    lastImg = "";
    host.insertAdjacentHTML("beforeend", skeleton());
    el = host.lastElementChild;
    release = lock ? lock(el) : null;
    el.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey, true);
    paint();
    $(".lu-lb-x").focus({ preventScroll: true });
  }

  function update(model) {
    if (!el) return;
    m = model;
    paint();
  }

  function teardown() {
    document.removeEventListener("keydown", onKey, true);
    const gone = el;
    el = null;
    if (gone) gone.remove();
    if (release) release();
    release = null;
  }

  function close() {
    if (!el) return;
    const done = m && m.onClose;
    teardown();
    m = null;
    if (done) done();
  }

  window.TesseraeLineupsLightbox = {
    open,
    update,
    close,
    isOpen: () => !!el,
  };
})();
