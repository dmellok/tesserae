/* Collapsible sections (generic). Markup, see the collapsible_toggle macro in
 * templates/_components.html:
 *
 *   <section data-collapsible id="x">
 *     <header data-collapsible-head> ... <button data-collapsible-toggle
 *             aria-expanded="true" aria-controls="x-body">...</button></header>
 *     <div data-collapsible-body id="x-body"> ... </div>
 *   </section>
 *
 * The chevron button is the keyboard / screen-reader control; the empty space
 * of the header toggles too (links, buttons and fields inside it keep their own
 * job). Every section starts expanded; the choice is remembered per page and
 * section in localStorage (tesserae-collapsed:<path>#<id>). A #hash link, or a
 * form field that fails validation inside a collapsed section, opens it first.
 * The body is hidden with the hidden attribute, so nothing animates layout. */
(function () {
  'use strict';
  var PREFIX = 'tesserae-collapsed:';
  var page = (window.location.pathname.replace(/\/+$/, '') || '/');

  function key(sec) { return PREFIX + page + '#' + sec.id; }
  function stored(sec) {
    try { return window.localStorage.getItem(key(sec)) === '1'; } catch (e) { return false; }
  }
  function store(sec, collapsed) {
    try {
      if (collapsed) window.localStorage.setItem(key(sec), '1');
      else window.localStorage.removeItem(key(sec));
    } catch (e) { /* private mode: the state just isn't remembered */ }
  }
  function titleOf(sec) {
    var t = sec.querySelector('[data-collapsible-title]') ||
      sec.querySelector('.dx-section-title, h1, h2, h3');
    return t ? t.textContent.replace(/\s+/g, ' ').trim() : '';
  }
  function bodyOf(sec) {
    var btn = sec.querySelector('[data-collapsible-toggle]');
    var id = btn && btn.getAttribute('aria-controls');
    return id ? document.getElementById(id) : null;
  }

  function setCollapsed(sec, collapsed, persist) {
    var btn = sec.querySelector('[data-collapsible-toggle]');
    var body = bodyOf(sec);
    if (!btn || !body) return;
    var was = sec.classList.contains('is-collapsed');
    sec.classList.toggle('is-collapsed', collapsed);
    body.hidden = collapsed;
    btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    var label = btn.querySelector('.sr-only');
    var title = titleOf(sec);
    if (label && title) label.textContent = (collapsed ? 'Expand ' : 'Collapse ') + title;
    if (persist && sec.id) store(sec, collapsed);
    if (was !== collapsed) {
      sec.dispatchEvent(new CustomEvent('collapsible:toggle', { bubbles: true, detail: { collapsed: collapsed } }));
      // Charts and other width-measuring widgets inside the body re-measure.
      if (!collapsed) window.dispatchEvent(new Event('resize'));
    }
  }

  // Open the collapsed section that holds ``el``. True when it changed something.
  function expandFor(el, persist) {
    var sec = el && el.closest ? el.closest('[data-collapsible].is-collapsed') : null;
    if (!sec) return false;
    setCollapsed(sec, false, persist);
    return true;
  }

  function reducedMotion() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }
  function reveal(el, smooth) {
    el.scrollIntoView({ behavior: smooth && !reducedMotion() ? 'smooth' : 'auto', block: 'start' });
  }

  function init() {
    var secs = Array.prototype.filter.call(document.querySelectorAll('[data-collapsible]'), function (sec) {
      return sec.id && sec.querySelector('[data-collapsible-toggle]');
    });
    if (!secs.length) return;
    var anyCollapsed = false;
    secs.forEach(function (sec) {
      if (stored(sec)) { setCollapsed(sec, true, false); anyCollapsed = true; }
      var head = sec.querySelector('[data-collapsible-head]');
      if (!head) return;
      head.addEventListener('click', function (ev) {
        // Links and controls in the header keep their own job; only the
        // chevron button (and bare header space) toggles.
        var inner = ev.target.closest('a, button, input, select, textarea, label, summary');
        if (inner && !inner.hasAttribute('data-collapsible-toggle')) return;
        // Dragging across the title to select it is not a click on the header.
        var sel = window.getSelection && window.getSelection();
        if (!inner && sel && !sel.isCollapsed && sel.toString().trim()) return;
        setCollapsed(sec, !sec.classList.contains('is-collapsed'), true);
      });
    });

    // A required field inside a collapsed section would block Save with a
    // "not focusable" error nobody can see: open its section instead.
    document.addEventListener('invalid', function (ev) {
      if (expandFor(ev.target, false)) reveal(ev.target, false);
    }, true);

    // A search or filter (list-filter.js) that matches something inside a
    // collapsed section would otherwise show nothing: open those sections for
    // as long as the page is filtered (not remembered).
    document.addEventListener('lf:change', function (ev) {
      if (!ev.target.classList || !ev.target.classList.contains('lf-is-filtered')) return;
      ev.target.querySelectorAll('[data-lf-item]:not([data-lf-out])').forEach(function (item) {
        expandFor(item, false);
      });
    });

    function targetFromHash() {
      var raw = window.location.hash.slice(1);
      if (!raw) return null;
      try { raw = decodeURIComponent(raw); } catch (e) { /* use as is */ }
      return document.getElementById(raw);
    }
    var first = targetFromHash();
    if (first && (expandFor(first, true) || anyCollapsed)) {
      reveal(first, false);
      // Late layout shifts (fonts, images) can move the target: land on it
      // again once the page settles, unless the person has scrolled.
      var moved = false;
      var stop = function () { moved = true; };
      ['wheel', 'touchstart', 'keydown', 'mousedown'].forEach(function (t) {
        window.addEventListener(t, stop, { once: true, passive: true });
      });
      var again = function () { if (!moved) reveal(first, false); };
      if (document.readyState === 'complete') requestAnimationFrame(again);
      else window.addEventListener('load', function () { requestAnimationFrame(again); }, { once: true });
    }
    window.addEventListener('hashchange', function () {
      var el = targetFromHash();
      if (el && expandFor(el, true)) reveal(el, true);
    });
    // Following a link whose hash is already current fires no hashchange.
    document.addEventListener('click', function (ev) {
      if (ev.defaultPrevented) return;
      var a = ev.target.closest && ev.target.closest('a[href^="#"]');
      if (!a || a.getAttribute('href').length < 2) return;
      var id = a.getAttribute('href').slice(1);
      try { id = decodeURIComponent(id); } catch (e) { /* use as is */ }
      var el = document.getElementById(id);
      if (!el || !expandFor(el, true)) return;
      ev.preventDefault();
      reveal(el, true);
      history.replaceState(null, '', '#' + el.id);
    });
  }

  window.tesseraeCollapsible = { init: init, expandFor: expandFor, setCollapsed: setCollapsed };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
