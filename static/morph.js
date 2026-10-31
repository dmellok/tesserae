/* Morph: a panel grows out of the control that opened it and shrinks
 * back into it on close. Every dropdown, menu, popover, picker, dialog
 * and lightbox in the Paper design does this; the classic design opens
 * them as before (this file returns straight away there).
 *
 * Two shapes of the same move:
 *  - the panel covers its trigger: the panel is clipped to the
 *    trigger's exact rectangle and corners, and the clip grows out to
 *    the whole panel;
 *  - the panel opens beside, below or away from its trigger: the panel
 *    is scaled onto the trigger's rectangle and grows out from there.
 * The panel's contents fade in once it has mostly grown, and fade out
 * first on the way back.
 *
 * Panels are the PANELS list below, plus anything marked data-morph
 * (trigger: data-morph-from, a selector). Nothing else has to change:
 * a MutationObserver sees a panel shown (hidden attribute, <details
 * open>, a class, <dialog open>, or added to the page) before the
 * browser paints. Closing by hidden or <details> is caught the same
 * way and held on screen for the shrink. A dialog's close() is wrapped
 * to shrink first. Code that removes a panel outright calls
 * TesseraeMorph.remove(el) instead of el.remove().
 *
 * The batteries pill has its own morph in the base template and
 * paper.css; it is not listed here.
 */
(function () {
  if (document.documentElement.dataset.ui !== 'paper') return;
  const DUR = 280;
  const EASE = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
  const reduced = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // The control the person last pressed: what a dialog or lightbox grows from.
  let lastPress = null;
  let lastPressAt = 0;
  document.addEventListener('pointerdown', (ev) => {
    const t = ev.target.closest && ev.target.closest('button, a, summary, [role="button"], input, [data-preview-src]');
    if (t) { lastPress = t; lastPressAt = Date.now(); }
  }, true);
  document.addEventListener('keydown', (ev) => {
    if ((ev.key === 'Enter' || ev.key === ' ') && document.activeElement && document.activeElement !== document.body) {
      lastPress = document.activeElement; lastPressAt = Date.now();
    }
  }, true);
  const recentPress = () => (lastPress && lastPress.isConnected && Date.now() - lastPressAt < 1500 ? lastPress : null);
  const summaryOf = (p) => { const d = p.closest('details'); return d && d.querySelector(':scope > summary'); };

  /* [panel selector, how to find its trigger] */
  const PANELS = [
    ['.info-pop-content', (p) => p.previousElementSibling],
    ['[data-icon-popover]', (p) => { const w = p.closest('[data-icon-picker]'); return w && w.querySelector('[data-icon-trigger]'); }],
    ['[data-location-results]', (p) => { const w = p.parentElement; return w && w.querySelector('input'); }],
    ['.lu-more-menu', summaryOf],
    ['.dx-hist-more-menu', summaryOf],
    ['.cat-sort-menu', summaryOf],
    ['#panels-devices-pop', () => document.getElementById('panels-devices-btn')],
    ['.canvas-menu', () => document.getElementById('panels-canvas-menu')],
    ['dialog.wizard-dialog', recentPress],
    ['.lightbox', recentPress],
    ['.cat-lightbox', recentPress],
    ['.tpl-modal', recentPress],
    ['.restart-modal-card', recentPress],
    ['[data-morph]', (p) => (p.dataset.morphFrom ? document.querySelector(p.dataset.morphFrom) : recentPress())],
  ];
  const SELECTOR = PANELS.map((e) => e[0]).join(', ');

  function triggerFor(panel) {
    for (const [sel, find] of PANELS) {
      if (panel.matches(sel)) { const t = find(panel); if (t && t.isConnected) return t; }
    }
    return null;
  }

  function from(panel, trigger) {
    const p = panel.getBoundingClientRect();
    const t = trigger.getBoundingClientRect();
    if (!p.width || !p.height || !t.width || !t.height) return null;
    const tr = getComputedStyle(trigger).borderTopLeftRadius || '0px';
    const pr = getComputedStyle(panel).borderTopLeftRadius || '0px';
    const covers = t.left >= p.left - 1 && t.right <= p.right + 1 && t.top >= p.top - 1 && t.bottom <= p.bottom + 1;
    if (covers) {
      const ins = [t.top - p.top, p.right - t.right, p.bottom - t.bottom, t.left - p.left].map((v) => Math.max(0, v) + 'px').join(' ');
      return [{ clipPath: 'inset(' + ins + ' round ' + tr + ')' }, { clipPath: 'inset(0px round ' + pr + ')' }];
    }
    return [
      { transformOrigin: '0 0', transform: 'translate(' + (t.left - p.left) + 'px, ' + (t.top - p.top) + 'px) scale(' + t.width / p.width + ', ' + t.height / p.height + ')', opacity: 0.5 },
      { transformOrigin: '0 0', transform: 'none', opacity: 1 },
    ];
  }

  const running = new WeakMap();
  function play(panel, trigger, opening) {
    const prev = running.get(panel);
    if (prev) { prev.forEach((a) => a.cancel()); running.delete(panel); }
    if (reduced() || !trigger) return Promise.resolve();
    const kf = from(panel, trigger);
    if (!kf) return Promise.resolve();
    const anims = [panel.animate(opening ? kf : kf.slice().reverse(), {
      duration: opening ? DUR : DUR - 40, delay: opening ? 0 : 60, easing: EASE, fill: 'both',
    })];
    for (const child of panel.children) {
      anims.push(child.animate(
        opening ? [{ opacity: 0 }, { opacity: 1 }] : [{ opacity: 1 }, { opacity: 0 }],
        opening ? { duration: 160, delay: 110, fill: 'both', easing: 'ease-out' } : { duration: 80, fill: 'both', easing: 'ease-out' },
      ));
    }
    running.set(panel, anims);
    return anims[0].finished.then(() => {
      if (running.get(panel) === anims) { anims.forEach((a) => a.cancel()); running.delete(panel); }
    }, () => {});
  }

  function shown(panel) {
    if (panel.hidden || panel.closest('[hidden]')) return false;
    const det = panel.closest('details');
    if (det && !det.open) return false;
    return panel.getClientRects().length > 0;
  }

  const panels = new Set();
  const state = new WeakMap();
  const lastDisplay = new WeakMap();
  const quiet = new WeakSet();

  function watch(panel, animateIfShown) {
    if (panels.has(panel)) return;
    panels.add(panel);
    const now = shown(panel);
    state.set(panel, now);
    if (now) lastDisplay.set(panel, getComputedStyle(panel).display);
    if (panel.tagName === 'DIALOG') wrapDialog(panel);
    if (now && animateIfShown) play(panel, (panel.__morphFrom = triggerFor(panel)), true);
  }

  function wrapDialog(dlg) {
    const native = HTMLDialogElement.prototype.close;
    dlg.close = function (value) {
      if (!dlg.open || quiet.has(dlg)) return native.call(dlg, value);
      quiet.add(dlg);
      play(dlg, closeTo(dlg), false).then(() => { native.call(dlg, value); state.set(dlg, false); quiet.delete(dlg); });
    };
  }

  // Close back into what the panel opened from (a dialog's last-pressed
  // control by then is its own close button), else its usual trigger.
  function closeTo(panel) {
    const t = panel.__morphFrom;
    return t && t.isConnected ? t : triggerFor(panel);
  }

  function settle(panel) {
    const was = state.get(panel);
    const now = shown(panel);
    if (was === now) return;
    state.set(panel, now);
    if (now) {
      lastDisplay.set(panel, getComputedStyle(panel).display);
      play(panel, (panel.__morphFrom = triggerFor(panel)), true);
      return;
    }
    const trigger = closeTo(panel);
    // Closing: put the panel back on screen for the shrink, then away.
    if (reduced() || !trigger || panel.tagName === 'DIALOG') return;
    const det = panel.closest('details');
    quiet.add(panel);
    if (panel.hidden) {
      panel.hidden = false;
      play(panel, trigger, false).then(() => { panel.hidden = true; quiet.delete(panel); });
    } else if (det && !det.open) {
      det.open = true;
      play(panel, trigger, false).then(() => { det.open = false; quiet.delete(panel); });
    } else if (!panel.closest('[hidden]')) {
      // Hidden by a class the page toggled: keep it drawn during the shrink.
      panel.style.setProperty('display', lastDisplay.get(panel) || 'block', 'important');
      play(panel, trigger, false).then(() => { panel.style.removeProperty('display'); quiet.delete(panel); });
    } else {
      quiet.delete(panel);
    }
  }

  function scan(root, animate) {
    if (root.matches && root.matches(SELECTOR)) watch(root, animate);
    if (root.querySelectorAll) root.querySelectorAll(SELECTOR).forEach((p) => watch(p, animate));
  }

  const mo = new MutationObserver((records) => {
    const touched = new Set();
    for (const r of records) {
      if (r.type === 'childList') { r.addedNodes.forEach((n) => { if (n.nodeType === 1) scan(n, true); }); continue; }
      const t = r.target;
      if (panels.has(t)) { touched.add(t); continue; }
      // A <details> opening, or a class or hidden on an ancestor, can show or hide a panel inside it.
      if (r.attributeName === 'style') continue;
      panels.forEach((p) => { if (!p.isConnected) panels.delete(p); else if (t.contains(p)) touched.add(p); });
    }
    touched.forEach((p) => { if (!quiet.has(p)) settle(p); });
  });

  function start() {
    scan(document.body, false);
    mo.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'open', 'class', 'style'] });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();

  window.TesseraeMorph = {
    /** Shrink a panel back into its trigger, then take el (the panel, or
        the overlay around it) off the page. */
    remove(el, panel) {
      if (!el || el.dataset.morphLeaving !== undefined) return;
      panel = panel || el;
      el.dataset.morphLeaving = '';
      quiet.add(panel);
      play(panel, closeTo(panel), false).then(() => el.remove());
    },
  };
})();
