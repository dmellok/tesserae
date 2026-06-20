/* Settings page controller (2026-06 handoff redesign).
   Drives the redesigned device card + server-settings section cards.
   Scope is intentionally narrow: tabs, dirty tracking + sticky save
   bar, dependent dimming, collapse toggle. No framework, no build. */

(function () {
  'use strict';

  // ---- Device card tabs --------------------------------------------------
  // Tab state is per-card and survives reload via the ``?tab=`` query
  // param so deep links land on the right panel after a POST + 302.
  function initDeviceCard(card) {
    const tabs = card.querySelectorAll('[data-tab]');
    const panels = card.querySelectorAll('[data-panel]');
    if (tabs.length === 0) return;

    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        const target = tab.getAttribute('data-tab');
        tabs.forEach(function (t) {
          const on = t === tab;
          t.classList.toggle('is-active', on);
          t.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        panels.forEach(function (p) {
          const on = p.getAttribute('data-panel') === target;
          p.classList.toggle('is-active', on);
          p.setAttribute('aria-hidden', on ? 'false' : 'true');
        });
        // Replace the query string so a reload (or a redirect-after-save
        // from the form below) returns to the same tab.
        const url = new URL(window.location.href);
        url.searchParams.set('tab', target);
        // Anchor lets multiple device cards co-exist on one page; we
        // bias to the focused card so #device-<id> stays accurate.
        history.replaceState(null, '', url.pathname + url.search + '#' + card.id);
      });
    });
  }

  // ---- Collapse toggle ---------------------------------------------------
  function initCollapse(card) {
    const btn = card.querySelector('[data-device-toggle]');
    if (!btn) return;
    btn.addEventListener('click', function () {
      const collapsed = card.getAttribute('data-collapsed') === 'true';
      card.setAttribute('data-collapsed', collapsed ? 'false' : 'true');
      btn.setAttribute('aria-expanded', collapsed ? 'true' : 'false');
    });
  }

  // ---- Dirty tracking + sticky save bar ----------------------------------
  // The bar is hidden until the user touches any input in the form. Save
  // submits normally (server returns 302 + flash + re-render). Discard
  // resets every field to its initial value and re-hides the bar.
  function initDirtyForm(form) {
    const bar = form.querySelector('[data-save-bar]');
    if (!bar) return;
    let dirty = false;

    function markDirty() {
      if (dirty) return;
      dirty = true;
      bar.hidden = false;
    }
    function clearDirty() {
      dirty = false;
      bar.hidden = true;
    }

    form.addEventListener('input', markDirty);
    form.addEventListener('change', markDirty);
    form.addEventListener('reset', function () {
      // Reset is synchronous but the inputs aren't updated until after
      // the event fires; wait a tick before clearing dirty so a stale
      // ``input`` event from the reset doesn't re-mark us dirty.
      setTimeout(clearDirty, 0);
    });
    // Once Save fires, we'll be redirected by the server. Hide
    // optimistically so the bar doesn't linger after the click.
    form.addEventListener('submit', function () {
      bar.hidden = true;
    });
  }

  // ---- Dependent dim -----------------------------------------------------
  // Groups marked ``data-dep-group`` carry a master switch (the first
  // checkbox or [role=switch] inside) and one or more ``data-dep-target``
  // blocks that dim + disable when the master is off.
  function initDepGroup(group) {
    // ``[data-master]`` is the explicit marker (server-tab section
    // headers use it); fall back to a name match or the first checkbox
    // so the device-card quiet-hours block keeps working unchanged.
    const master =
      group.querySelector('[data-master]') ||
      group.querySelector('input[type="checkbox"][name*="enabled"]') ||
      group.querySelector('input[type="checkbox"]');
    if (!master) return;
    const targets = group.querySelectorAll('[data-dep-target]');

    function sync() {
      const off = !master.checked;
      group.toggleAttribute('data-dep-off', off);
      targets.forEach(function (t) {
        t.querySelectorAll('input, select, textarea, button').forEach(function (el) {
          if (el === master) return;
          el.disabled = off;
        });
      });
    }
    master.addEventListener('change', sync);
    sync();
  }

  // ---- Boot --------------------------------------------------------------
  function ready(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      fn();
    }
  }

  ready(function () {
    document.querySelectorAll('[data-device-card]').forEach(function (card) {
      initDeviceCard(card);
      initCollapse(card);
    });
    document.querySelectorAll('[data-dirty-form]').forEach(initDirtyForm);
    document.querySelectorAll('[data-dep-group]').forEach(initDepGroup);
  });
})();
