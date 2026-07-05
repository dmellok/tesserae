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
  // v0.69.9: reverts the v0.69.6 always-visible-muted variant now
  // that the device card's nested-form regression is fixed (fields
  // and the save bar are correctly associated with the outer form via
  // the HTML5 ``form=""`` attribute). Bar hides until the first input
  // event, then shows; Discard / Save re-hide.
  //
  // Because the save bar and many of its associated inputs may sit
  // OUTSIDE the outer form in the DOM (form="..." attribute
  // association, not descendant), we resolve the bar by lookup:
  //   1. Descendant of ``form`` (the legacy shape when nothing's nested).
  //   2. ``[data-save-bar-for="<form.id>"]`` on any element in the
  //      document (opt-in for out-of-tree bars).
  // Input events also use ``target.form === form`` filtering on the
  // document rather than form-scoped bubbling, since form="..."
  // associations don't bubble events to the form element.
  function initDirtyForm(form) {
    let bar = form.querySelector('[data-save-bar]');
    if (!bar && form.id) {
      bar = document.querySelector('[data-save-bar-for="' + form.id + '"]');
    }
    // Bar is optional (v0.69.10): the ``data-dirty`` attribute on
    // the form is enough for CSS-based dirty-state styling on any
    // in-form button (e.g. the palette-tone Save row's "Unsaved tone
    // changes" flag). Forms without a bar still track dirty state.
    let dirty = false;

    function markDirty() {
      if (dirty) return;
      dirty = true;
      form.setAttribute('data-dirty', '1');
      if (bar) bar.hidden = false;
    }
    function clearDirty() {
      dirty = false;
      form.removeAttribute('data-dirty');
      if (bar) bar.hidden = true;
    }

    document.addEventListener('input', function (ev) {
      if (ev.target && ev.target.form === form) markDirty();
    });
    document.addEventListener('change', function (ev) {
      if (ev.target && ev.target.form === form) markDirty();
    });
    form.addEventListener('reset', function () {
      // Reset is synchronous but the inputs aren't updated until after
      // the event fires; wait a tick before clearing dirty so a stale
      // ``input`` event from the reset doesn't re-mark us dirty.
      setTimeout(clearDirty, 0);
    });
    // Once Save fires we'll be redirected by the server. Hide
    // optimistically so the bar doesn't linger after the click.
    form.addEventListener('submit', clearDirty);
  }

  // ---- Save bar stacking (v0.69.12) ---------------------------------
  // Multiple sticky save bars can be visible at once: the outer
  // combined-form bar + the tone-form bar on a single device card,
  // or the combined bars of multiple device cards on the Devices tab.
  // Without offset, they overlap at the same sticky ``bottom: 20px``.
  // Assign each visible bar a cumulative offset (via CSS custom
  // property) so they stack above one another instead. Recomputes
  // whenever a bar's ``hidden`` attribute changes.
  const SAVE_BAR_GAP = 8;
  function recomputeSaveBarStack() {
    const visibleBars = Array.from(
      document.querySelectorAll('[data-save-bar]:not([hidden])')
    );
    let cumulative = 0;
    visibleBars.forEach(function (bar) {
      bar.style.setProperty('--dx-save-bar-offset', cumulative + 'px');
      // Force reflow so offsetHeight reads the current layout.
      cumulative += bar.offsetHeight + SAVE_BAR_GAP;
    });
  }

  function initSaveBarStackObserver() {
    const bars = document.querySelectorAll('[data-save-bar]');
    if (!bars.length) return;
    // MutationObserver on hidden attribute is cheap and only fires
    // when initDirtyForm toggles a bar's visibility.
    const obs = new MutationObserver(recomputeSaveBarStack);
    bars.forEach(function (bar) {
      obs.observe(bar, { attributes: true, attributeFilter: ['hidden'] });
    });
    // Also recompute on window resize since bar height can change if
    // the layout reflows (viewport width change wrapping the message
    // to two lines).
    window.addEventListener('resize', recomputeSaveBarStack);
    recomputeSaveBarStack();
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

  // ---- Transport segmented control (Add device card, issue #16) -------
  // Two buttons inside [data-segmented-group]; clicking flips the active
  // button + swaps [hidden] on [data-transport-branch="rest|mqtt"]. Both
  // branches stay in the DOM, so typed values in the inactive branch are
  // preserved across flips.
  function initSegmented(group) {
    const card = group.closest('[data-add-device-card]') || group;
    const buttons = group.querySelectorAll('[data-segmented-btn]');
    if (buttons.length === 0) return;
    const branches = card.querySelectorAll('[data-transport-branch]');
    const helps = {
      rest: card.querySelector('[data-segmented-help-rest]'),
      mqtt: card.querySelector('[data-segmented-help-mqtt]'),
    };
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        const target = btn.getAttribute('data-segmented-btn');
        buttons.forEach(function (b) {
          const on = b === btn;
          b.classList.toggle('is-active', on);
          b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        branches.forEach(function (br) {
          const on = br.getAttribute('data-transport-branch') === target;
          br.hidden = !on;
        });
        Object.keys(helps).forEach(function (k) {
          if (helps[k]) helps[k].hidden = k !== target;
        });
        card.setAttribute('data-transport', target);
      });
    });
  }

  // ---- Test-pattern form (Calibration tab) ----------------------------
  // Live preview: rebuild the preview <img> src whenever the pattern
  // radio or colour <select> changes, so what the user sees below the
  // picker is exactly what "Send to panel" will push. Also toggles the
  // colour picker's visibility based on the current radio's
  // data-needs-color attribute.
  function initTestPatternForm(form) {
    const preview = form.querySelector('[data-preview-img]');
    if (!preview) return;
    const base = preview.getAttribute('data-preview-base');
    if (!base) return;
    const colorGroup = form.querySelector('[data-color-picker]');
    const colorSelect = form.querySelector('[data-color-select]');
    const colorChip = form.querySelector('[data-color-chip]');

    function update() {
      const checked = form.querySelector('input[name="pattern"]:checked');
      if (!checked) return;
      const pattern = checked.value;
      const needsColor = checked.getAttribute('data-needs-color') === '1';
      if (colorGroup) colorGroup.hidden = !needsColor;
      let src = base + '?pattern=' + encodeURIComponent(pattern);
      if (needsColor && colorSelect) {
        src += '&color_index=' + encodeURIComponent(colorSelect.value);
        if (colorChip) {
          const opt = colorSelect.options[colorSelect.selectedIndex];
          const hex = opt && opt.getAttribute('data-hex');
          if (hex) colorChip.style.background = hex;
        }
      }
      // Cache-bust so a subsequent send-with-same-params still refetches
      // when the user tabs back. no-store on the response also helps.
      src += '&_t=' + Date.now();
      preview.src = src;
    }

    form.querySelectorAll('input[name="pattern"]').forEach(function (radio) {
      radio.addEventListener('change', update);
    });
    if (colorSelect) colorSelect.addEventListener('change', update);
    update();
  }

  // ---- Palette apply form (Calibration tab) ---------------------------
  // The picker's attribution + notes lines are baked into the initial
  // render for the currently-active slug. When the user picks a different
  // profile in the dropdown, refresh the attribution / notes so the
  // labels update before they've clicked Apply.
  function initPaletteApplyForm(form) {
    const select = form.querySelector('[data-palette-slug]');
    if (!select) return;
    const card = form.closest('.dx-calib-section');
    if (!card) return;
    const attrEl = card.querySelector('[data-palette-attribution]');
    const notesEl = card.querySelector('[data-palette-notes]');
    function apply() {
      const opt = select.options[select.selectedIndex];
      if (!opt) return;
      const attribution = opt.getAttribute('data-attribution') || '';
      const notes = opt.getAttribute('data-notes') || '';
      if (attrEl) {
        if (attribution) {
          attrEl.innerHTML =
            'via <a href="' + attribution + '" target="_blank" rel="noopener">' +
            attribution + '</a>';
          attrEl.hidden = false;
        } else {
          attrEl.hidden = true;
        }
      }
      if (notesEl) {
        notesEl.textContent = notes;
        notesEl.hidden = !notes;
      }
    }
    select.addEventListener('change', apply);
  }

  // ---- Kind defaults rows (issue #22) ---------------------------------
  // Whole-row click toggles the inline form. Reset button swaps the
  // actions row for an inline confirm bar; Cancel reverts.
  function initKindRow(row) {
    const toggle = row.querySelector('[data-kind-toggle]');
    const body = row.querySelector('[data-kind-body]');
    const actions = row.querySelector('[data-kind-actions]');
    const confirm = row.querySelector('[data-kind-confirm]');
    const resetBtn = row.querySelector('[data-kind-reset]');
    const cancelBtn = row.querySelector('[data-kind-confirm-cancel]');

    if (toggle && body) {
      toggle.addEventListener('click', function () {
        const open = body.hidden;
        body.hidden = !open;
        row.classList.toggle('is-open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
    }
    if (resetBtn && actions && confirm) {
      resetBtn.addEventListener('click', function () {
        actions.hidden = true;
        confirm.hidden = false;
      });
    }
    if (cancelBtn && actions && confirm) {
      cancelBtn.addEventListener('click', function () {
        confirm.hidden = true;
        actions.hidden = false;
      });
    }
  }

  ready(function () {
    document.querySelectorAll('[data-device-card]').forEach(function (card) {
      initDeviceCard(card);
      initCollapse(card);
    });
    document.querySelectorAll('[data-dirty-form]').forEach(initDirtyForm);
    initSaveBarStackObserver();
    document.querySelectorAll('[data-dep-group]').forEach(initDepGroup);
    document.querySelectorAll('[data-segmented-group]').forEach(initSegmented);
    document.querySelectorAll('[data-kind-row]').forEach(initKindRow);
    document.querySelectorAll('[data-test-pattern-form]').forEach(initTestPatternForm);
    document.querySelectorAll('[data-palette-apply-form]').forEach(initPaletteApplyForm);
    // Delete-details cancel (v0.69.2): the Cancel button inside the
    // expanded delete confirmation closes the <details> without
    // reloading the page. Purely UX; the submit button posts as normal.
    document.querySelectorAll('[data-delete-cancel]').forEach(function (btn) {
      const details = btn.closest('[data-delete-details]');
      if (!details) return;
      btn.addEventListener('click', function () {
        details.removeAttribute('open');
      });
    });
    // Live label for each tone slider so the user sees the exact
    // numeric value they're dragging to. Cheap DOM: one <output> per
    // slider, updated on input. Also rebuilds the test-pattern
    // preview URL with the current slider values so the preview
    // reflects tone changes before the user hits Save. Query params
    // override the applied profile's stored defaults on the server
    // side (see devices_test_pattern_preview).
    function refreshTonePreview(card) {
      const preview = card.querySelector('[data-preview-img]');
      if (!preview) return;
      const base = preview.getAttribute('data-preview-base');
      if (!base) return;
      const checked = card.querySelector('input[name="pattern"]:checked');
      const pattern = checked ? checked.value : 'palette_swatches';
      const params = new URLSearchParams();
      params.set('pattern', pattern);
      if (checked && checked.getAttribute('data-needs-color') === '1') {
        const cs = card.querySelector('[data-color-select]');
        if (cs) params.set('color_index', cs.value);
      }
      const toneForm = card.querySelector('.dx-palette-tone-form');
      if (toneForm) {
        ['exposure', 's_curve', 'lab_compress_min', 'lab_compress_max',
         'smoothing_radius'].forEach(function (name) {
          const el = toneForm.querySelector('[name="' + name + '"]');
          if (el && el.value !== '') params.set(name, el.value);
        });
      }
      // Live palette preview (v0.68): read the six / seven colour
      // swatches and pipe them through as ``#rrggbb`` query params so
      // the preview repaints with the currently-picked colours. Orange
      // is optional (only ACeP / 7-colour Inky profiles render it).
      const paletteForm = card.querySelector('.dx-palette-colors-form');
      if (paletteForm) {
        ['black', 'white', 'yellow', 'red', 'blue', 'green', 'orange']
          .forEach(function (name) {
            const el = paletteForm.querySelector('[name="' + name + '"]');
            if (el && el.value) params.set(name, el.value);
          });
      }
      params.set('_t', Date.now());
      preview.src = base + '?' + params.toString();
    }

    document.querySelectorAll('[data-device-card]').forEach(function (card) {
      const inputs = card.querySelectorAll('[data-tone-input]');
      inputs.forEach(function (input) {
        const label = input.parentElement.querySelector('[data-tone-value]');
        if (label) {
          input.addEventListener('input', function () {
            label.textContent = input.value;
          });
        }
        input.addEventListener('input', function () {
          refreshTonePreview(card);
        });
      });
    });
    // Palette editor: live hex readout below each colour input so the
    // user sees the exact ``#rrggbb`` they're picking. Native colour
    // pickers already show hex in their popovers but the readout stays
    // visible on-card without requiring the popover to be open.
    // Also refreshes the tone preview so the swatch change paints
    // into the preview on the same tick.
    document.querySelectorAll('[data-palette-color-input]').forEach(function (input) {
      const readout = input.parentElement.querySelector('[data-palette-color-hex]');
      if (readout) {
        input.addEventListener('input', function () {
          readout.textContent = input.value;
        });
      }
      const card = input.closest('[data-device-card]');
      if (card) {
        input.addEventListener('input', function () {
          refreshTonePreview(card);
        });
      }
    });
  });
})();
