// Global glue for the form-control component macros in templates/_components.html.
// Loaded by every admin page via _base.html. Lazy-attaches handlers to any
// elements that get added later by the editor's auto-save (which re-renders
// cards on plugin change).

(function () {
  function attachSliders(root) {
    root.querySelectorAll('input[type="range"]:not([data-bound])').forEach((slider) => {
      const output = root.querySelector(`output[for="${slider.id}"]`);
      const suffix = slider.dataset.outputSuffix || "";
      const sync = () => {
        if (output) output.value = slider.value + suffix;
        // Paint the filled portion of the track in the accent colour
        // (WebKit can't draw progress natively; Firefox uses
        // ::-moz-range-progress for the same effect.)
        const min = parseFloat(slider.min || "0");
        const max = parseFloat(slider.max || "100");
        const val = parseFloat(slider.value || "0");
        const span = max - min;
        const pct = span > 0 ? ((val - min) / span) * 100 : 0;
        slider.style.setProperty("--slider-fill", pct + "%");
      };
      slider.addEventListener("input", sync);
      slider.addEventListener("change", sync);
      sync();
      slider.dataset.bound = "1";
    });
  }

  // Fluid preview frames: the iframe / image inside is sized to the
  // panel's native pixel dims (so the composer renders unscaled).
  // We watch each frame's container width and apply a matching CSS
  // transform so the preview always fits without overflowing.
  function fitPreview(frame) {
    const panelW = parseInt(frame.dataset.panelW || "0", 10);
    if (!panelW) return;
    const rect = frame.getBoundingClientRect();
    if (!rect.width) return;
    const scale = rect.width / panelW;
    // Iframes are sized at the panel's native CSS pixels so the
    // composer renders unscaled — they need a transform to fit. Raster
    // images use object-fit (no transform) so they letterbox cleanly
    // regardless of the source's aspect ratio.
    frame.querySelectorAll("iframe").forEach((el) => {
      el.style.transform = `scale(${scale})`;
    });
  }

  function attachPreviewFit(root) {
    const frames = root.querySelectorAll("[data-fit-preview]:not([data-fit-bound])");
    if (!frames.length) return;
    if (typeof ResizeObserver === "undefined") {
      frames.forEach((f) => { f.dataset.fitBound = "1"; fitPreview(f); });
      window.addEventListener("resize", () => frames.forEach(fitPreview));
      return;
    }
    const ro = new ResizeObserver((entries) => {
      entries.forEach((e) => fitPreview(e.target));
    });
    frames.forEach((f) => {
      f.dataset.fitBound = "1";
      ro.observe(f);
      fitPreview(f);
    });
  }

  // Numeric field paired with a preset dropdown + "Custom…" option that
  // reveals the underlying number input. The number input always carries
  // the field's name — submission picks up whichever value the user
  // last touched.
  function attachPresetNumbers(root) {
    root.querySelectorAll("[data-preset-field]:not([data-preset-bound])").forEach((field) => {
      const select = field.querySelector("[data-preset-select]");
      const custom = field.querySelector("[data-preset-custom]");
      if (!select || !custom) return;
      const sync = (focusCustom) => {
        if (select.value === "__custom__") {
          custom.hidden = false;
          if (focusCustom) custom.focus();
        } else {
          custom.value = select.value;
          custom.hidden = true;
        }
      };
      select.addEventListener("change", () => sync(true));
      sync(false);
      field.dataset.presetBound = "1";
    });
  }

  // Initial attach on DOMContentLoaded; re-attach when the editor reloads
  // its preview iframe (a side-effect of a save).
  function init() {
    attachSliders(document);
    attachPreviewFit(document);
    attachPresetNumbers(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Observe future additions (the page editor swaps cell forms in/out on
  // plugin change). Skipping mutation observer for now since editor.js
  // currently full-reloads on plugin change — re-init isn't needed mid-page.
  window.tesseraeComponents = { attachSliders, attachPreviewFit, attachPresetNumbers, fitPreview };
})();
