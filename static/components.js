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
    // composer renders unscaled, they need a transform to fit. Raster
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

  // Send-page live preview: reflect the chosen "Image fit" mode on the
  // raster preview so it matches what the renderer pushes. fit/fill/stretch/
  // blur are ratio-based, so object-fit handles them at any preview scale;
  // center is pixel-based, so we scale the <img> to the panel's pixel ratio
  // and clip. blur additionally paints a blurred cover copy behind the
  // contained image (the .preview-bg layer).
  const _SEND_OBJECT_FIT = { fit: "contain", fill: "cover", stretch: "fill", blur: "contain" };

  function attachSendFitPreview(root) {
    root.querySelectorAll(".send-pair").forEach((pair) => {
      if (pair.dataset.fitSync) return;
      const select = pair.querySelector('select[name="fit"]');
      const frame = pair.querySelector("[data-fit-preview]");
      const fg = frame && frame.querySelector("img.preview-image");
      if (!select || !frame || !fg) return; // webpage tab previews an iframe
      pair.dataset.fitSync = "1";
      const bg = frame.querySelector("img.preview-bg");

      function apply() {
        const mode = select.value;
        const ready = !!fg.src && !fg.hidden;
        if (mode === "center") {
          // "No scaling": render at the source's native pixels, but scaled
          // by the preview's panel-px ratio so it reads true to the panel.
          const panelW = parseInt(frame.dataset.panelW || "0", 10);
          const scale = panelW ? frame.clientWidth / panelW : 1;
          fg.style.objectFit = "fill";
          fg.style.inset = "auto";
          fg.style.left = "50%";
          fg.style.top = "50%";
          fg.style.transform = "translate(-50%, -50%)";
          fg.style.width = fg.naturalWidth * scale + "px";
          fg.style.height = fg.naturalHeight * scale + "px";
        } else {
          fg.style.objectFit = _SEND_OBJECT_FIT[mode] || "contain";
          fg.style.inset = "";
          fg.style.left = "";
          fg.style.top = "";
          fg.style.transform = "";
          fg.style.width = "";
          fg.style.height = "";
        }
        // In blur mode the contained image must not paint over the blurred
        // backdrop in its letterbox margins.
        fg.style.background = mode === "blur" ? "transparent" : "";
        if (bg) {
          const show = mode === "blur" && ready;
          if (show && bg.getAttribute("src") !== fg.getAttribute("src")) {
            bg.src = fg.src;
          }
          bg.hidden = !show;
        }
      }

      select.addEventListener("change", apply);
      fg.addEventListener("load", apply); // pixels ready: center needs naturalWidth
      // The File / URL tabs swap the preview's src + hidden as the user picks
      // a source; resync the backdrop + sizing on those mutations.
      if (typeof MutationObserver !== "undefined") {
        new MutationObserver(apply).observe(fg, {
          attributes: true,
          attributeFilter: ["src", "hidden"],
        });
      }
      if (typeof ResizeObserver !== "undefined") {
        new ResizeObserver(apply).observe(frame);
      }
      apply();
    });
  }

  // Numeric field paired with a preset dropdown + "Custom…" option that
  // reveals the underlying number input. The number input always carries
  // the field's name, submission picks up whichever value the user
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

  // Lightbox, delegated click handler that intercepts any
  // `<a data-lightbox href="...">` and shows the image in an in-page
  // overlay instead of a new tab. ESC or backdrop click closes.
  function attachLightbox() {
    if (document.body.dataset.lightboxBound) return;
    document.body.dataset.lightboxBound = "1";

    function close() {
      const overlay = document.querySelector(".lightbox");
      if (!overlay) return;
      overlay.remove();
      document.body.classList.remove("lightbox-open");
    }

    function open(src, label) {
      close();
      const overlay = document.createElement("div");
      overlay.className = "lightbox";
      overlay.setAttribute("role", "dialog");
      overlay.setAttribute("aria-modal", "true");
      if (label) overlay.setAttribute("aria-label", label);
      overlay.innerHTML =
        '<button type="button" class="lightbox-close" aria-label="Close">' +
        '<i class="ph ph-x" aria-hidden="true"></i></button>' +
        '<img class="lightbox-img" alt="" />';
      const img = overlay.querySelector(".lightbox-img");
      img.src = src;
      if (label) img.alt = label;
      overlay.addEventListener("click", (ev) => {
        // Backdrop click closes; clicks on the image itself do not.
        if (ev.target === overlay || ev.target.closest(".lightbox-close")) {
          close();
        }
      });
      document.body.appendChild(overlay);
      document.body.classList.add("lightbox-open");
    }

    document.addEventListener("click", (ev) => {
      const a = ev.target.closest && ev.target.closest("a[data-lightbox]");
      if (!a) return;
      ev.preventDefault();
      const src = a.getAttribute("href");
      if (!src) return;
      const img = a.querySelector("img");
      open(src, (img && img.alt) || a.getAttribute("aria-label") || "");
    });

    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") close();
    });
  }

  // Initial attach on DOMContentLoaded; re-attach when the editor reloads
  // its preview iframe (a side-effect of a save).
  function init() {
    attachSliders(document);
    attachPreviewFit(document);
    attachSendFitPreview(document);
    attachPresetNumbers(document);
    attachLightbox();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Observe future additions (the page editor swaps cell forms in/out on
  // plugin change). Skipping mutation observer for now since editor.js
  // currently full-reloads on plugin change, re-init isn't needed mid-page.
  window.tesseraeComponents = {
    attachSliders,
    attachPreviewFit,
    attachSendFitPreview,
    attachPresetNumbers,
    fitPreview,
  };
})();
