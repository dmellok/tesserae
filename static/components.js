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

  // Initial attach on DOMContentLoaded; re-attach when the editor reloads
  // its preview iframe (a side-effect of a save).
  function init() {
    attachSliders(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Observe future additions (the page editor swaps cell forms in/out on
  // plugin change). Skipping mutation observer for now since editor.js
  // currently full-reloads on plugin change — re-init isn't needed mid-page.
  window.tesseraeComponents = { attachSliders };
})();
