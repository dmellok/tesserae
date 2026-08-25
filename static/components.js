// Global glue for the form-control component macros in templates/_components.html.
// Loaded by every admin page via _base.html. Lazy-attaches handlers to any
// elements that get added later by the editor's auto-save (which re-renders
// cards on plugin change).

(function () {
  function attachSliders(root) {
    root.querySelectorAll('input[type="range"]:not([data-bound])').forEach((slider) => {
      const number = root.querySelector(`input[data-slider-number-for="${slider.id}"]`);
      // Paint the filled portion of the track in the accent colour
      // (WebKit can't draw progress natively; Firefox uses
      // ::-moz-range-progress for the same effect.)
      const paintFill = () => {
        const min = parseFloat(slider.min || "0");
        const max = parseFloat(slider.max || "100");
        const val = parseFloat(slider.value || "0");
        const span = max - min;
        const pct = span > 0 ? ((val - min) / span) * 100 : 0;
        slider.style.setProperty("--slider-fill", pct + "%");
      };
      const syncFromSlider = () => {
        if (number) number.value = slider.value;
        paintFill();
      };
      const syncFromNumber = () => {
        if (!number || number.value === "") return;
        slider.value = number.value;
        paintFill();
      };
      // The range input is the one that carries the field's name, and it
      // silently clamps anything outside min/max. Typing 9 into a 0.7-1.5
      // box would leave 9 on screen while 1.5 got submitted, so on commit
      // the box is rewritten with the value that will actually be saved.
      const clampNumber = () => {
        if (!number || number.value === "") return;
        syncFromNumber();
        number.value = slider.value;
      };
      slider.addEventListener("input", syncFromSlider);
      slider.addEventListener("change", syncFromSlider);
      if (number) {
        number.addEventListener("input", syncFromNumber);
        number.addEventListener("change", clampNumber);
        number.addEventListener("blur", clampNumber);
      }
      syncFromSlider();
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
      // ``notify`` marks a user-driven change. Assigning ``.value`` in
      // script fires no input event, and the preset <select> carries no
      // name (it isn't submitted) so it has no form association of its
      // own once the card's inputs belong to the form by ``form=""``
      // rather than by nesting. Between the two, picking a preset used
      // to change the value with nothing in the page hearing about it,
      // so the save bar never appeared and the edit looked ignored until
      // some other field was touched (#260). Dispatch on the input that
      // IS associated, and only for real interaction: doing it during
      // the initial sync would mark every form on the page dirty on
      // load.
      const sync = (focusCustom, notify) => {
        if (select.value === "__custom__") {
          custom.hidden = false;
          if (focusCustom) custom.focus();
        } else {
          custom.value = select.value;
          custom.hidden = true;
        }
        if (notify) custom.dispatchEvent(new Event("input", { bubbles: true }));
      };
      select.addEventListener("change", () => sync(true, true));
      sync(false, false);
      field.dataset.presetBound = "1";
    });
  }

  // Multi-select checklists (e.g. the HA entity pickers): a client-side
  // filter box narrows the list, and a count shows how many are ticked.
  // The filter input has no name (never submitted) and stops its own
  // events from bubbling to the form so typing doesn't fire a preview.
  // Shared so both the grid editor (forms present at load) and the canvas
  // editor (forms injected into the config drawer after load) wire it the
  // same way (#130).
  function attachMultiSelect(root) {
    root.querySelectorAll("[data-multiselect]").forEach((ms) => {
      if (ms.dataset.msBound) return;
      ms.dataset.msBound = "1";
      const filter = ms.querySelector("[data-ms-filter]");
      const opts = Array.from(ms.querySelectorAll(".multiselect-opt"));
      const list = ms.querySelector("[data-ms-list]");
      const emptyEl = ms.querySelector("[data-ms-empty]");
      const countEl = ms.querySelector("[data-ms-count]");
      const orderStatus = ms.querySelector("[data-ms-order-status]");
      let dragged = null;
      let dragStartOrder = [];
      let pointerDrag = null;

      const inputFor = (row) => row && row.querySelector('input[type="checkbox"]');
      const rows = () => list ? Array.from(list.querySelectorAll(".multiselect-opt")) : [];
      const selectedRows = () => rows().filter((row) => inputFor(row)?.checked);
      const selectedValues = () => selectedRows().map((row) => inputFor(row).value);
      const sameOrder = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

      function firstUnchecked() {
        return rows().find((row) => !inputFor(row)?.checked) || emptyEl || null;
      }

      function announce(row) {
        if (!orderStatus || !row) return;
        const selected = selectedRows();
        const position = selected.indexOf(row) + 1;
        const label = row.querySelector(".multiselect-label")?.textContent.trim() || "Item";
        orderStatus.textContent = `${label}, priority ${position} of ${selected.length}`;
      }

      function emitOrderChange(row) {
        if (list) list.dispatchEvent(new Event("input", { bubbles: true }));
        announce(row);
      }

      // ``insertBefore`` on a row already in the list removes and re-inserts
      // it, which blurs whatever was focused inside it. (That's the whole
      // reason the DOM grew ``moveBefore``.) Without restoring focus an arrow
      // key steps an entry exactly once before focus falls through to the
      // page, and ticking a checkbox drops focus mid-list. Prefer the native
      // move where it exists, and put focus back where it isn't.
      const canMoveBefore = typeof Element.prototype.moveBefore === "function";
      function place(row, before) {
        if (!list || before === row) return;
        if (canMoveBefore) {
          try {
            list.moveBefore(row, before);
            return;
          } catch {
            // moveBefore throws if the node isn't already in this document;
            // insertBefore below handles it.
          }
        }
        const focused = document.activeElement;
        const restore = focused && row.contains(focused) ? focused : null;
        list.insertBefore(row, before);
        if (restore && restore.isConnected) restore.focus();
      }

      function partitionSelection() {
        if (!list) return;
        const ordered = rows();
        const checked = ordered.filter((row) => inputFor(row)?.checked);
        const unchecked = ordered.filter((row) => !inputFor(row)?.checked);
        [...checked, ...unchecked].forEach((row) => place(row, emptyEl || null));
      }

      function beginDrag(row) {
        if (!row || !inputFor(row)?.checked || (filter && filter.value.trim())) return false;
        dragged = row;
        dragStartOrder = selectedValues();
        row.classList.add("is-dragging");
        return true;
      }

      function reorderAt(clientY) {
        if (!dragged || !list) return;
        const peers = selectedRows().filter((row) => row !== dragged);
        const before = peers.find((row) => {
          const rect = row.getBoundingClientRect();
          return clientY < rect.top + rect.height / 2;
        });
        place(dragged, before || firstUnchecked());
      }

      function finishDrag() {
        if (!dragged) return;
        const row = dragged;
        row.classList.remove("is-dragging");
        dragged = null;
        if (!sameOrder(dragStartOrder, selectedValues())) emitOrderChange(row);
        dragStartOrder = [];
      }

      function moveWithKeyboard(row, key) {
        const selected = selectedRows();
        const current = selected.indexOf(row);
        if (current < 0 || selected.length < 2) return;
        let target = current;
        if (key === "ArrowUp") target -= 1;
        else if (key === "ArrowDown") target += 1;
        else if (key === "Home") target = 0;
        else if (key === "End") target = selected.length - 1;
        target = Math.max(0, Math.min(selected.length - 1, target));
        if (target === current) return;
        const reordered = selected.filter((item) => item !== row);
        reordered.splice(target, 0, row);
        reordered.forEach((item) => place(item, firstUnchecked()));
        emitOrderChange(row);
      }

      function updateCount() {
        if (!countEl) return;
        const n = opts.filter((o) => o.querySelector("input").checked).length;
        countEl.textContent = n ? `${n} selected` : "";
      }
      function applyFilter() {
        const q = (filter ? filter.value : "").trim().toLowerCase();
        let shown = 0;
        opts.forEach((o) => {
          const match = !q || (o.dataset.msText || "").indexOf(q) !== -1;
          o.hidden = !match;
          if (match) shown += 1;
        });
        ms.classList.toggle("is-filtering", Boolean(q));
        if (emptyEl) emptyEl.hidden = shown > 0;
      }
      if (filter) {
        // Keep filter keystrokes local, don't dirty the form or trigger
        // a preview POST (the filter isn't part of the saved value).
        ["input", "change", "keyup", "keydown"].forEach((evt) =>
          filter.addEventListener(evt, (e) => e.stopPropagation()),
        );
        filter.addEventListener("input", applyFilter);
      }
      if (list) {
        list.addEventListener("dragover", (ev) => {
          if (!dragged) return;
          ev.preventDefault();
          if (ev.dataTransfer) ev.dataTransfer.dropEffect = "move";
          reorderAt(ev.clientY);
        });
        list.addEventListener("drop", (ev) => {
          if (!dragged) return;
          ev.preventDefault();
          reorderAt(ev.clientY);
          finishDrag();
        });

        opts.forEach((row) => {
          const handle = row.querySelector("[data-ms-drag]");
          if (!handle) return;
          handle.addEventListener("click", (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
          });
          handle.addEventListener("dragstart", (ev) => {
            if (pointerDrag || !beginDrag(row)) {
              ev.preventDefault();
              return;
            }
            if (ev.dataTransfer) {
              ev.dataTransfer.effectAllowed = "move";
              ev.dataTransfer.setData("text/plain", inputFor(row).value);
            }
          });
          handle.addEventListener("dragend", finishDrag);
          handle.addEventListener("keydown", (ev) => {
            if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(ev.key)) return;
            ev.preventDefault();
            moveWithKeyboard(row, ev.key);
          });

          // Native HTML drag handles mouse input. Pointer events cover touch
          // and pen input, where HTML drag-and-drop is not consistently
          // available across mobile browsers.
          handle.addEventListener("pointerdown", (ev) => {
            if (ev.pointerType === "mouse" || !inputFor(row)?.checked) return;
            if (filter && filter.value.trim()) return;
            pointerDrag = { id: ev.pointerId, row, startY: ev.clientY, moved: false };
            handle.setPointerCapture?.(ev.pointerId);
          });
          handle.addEventListener("pointermove", (ev) => {
            if (!pointerDrag || pointerDrag.id !== ev.pointerId) return;
            if (!pointerDrag.moved && Math.abs(ev.clientY - pointerDrag.startY) < 5) return;
            if (!pointerDrag.moved) {
              pointerDrag.moved = beginDrag(pointerDrag.row);
              if (!pointerDrag.moved) return;
            }
            ev.preventDefault();
            reorderAt(ev.clientY);
          });
          const endPointerDrag = (ev) => {
            if (!pointerDrag || pointerDrag.id !== ev.pointerId) return;
            if (pointerDrag.moved) finishDrag();
            handle.releasePointerCapture?.(ev.pointerId);
            pointerDrag = null;
          };
          handle.addEventListener("pointerup", endPointerDrag);
          handle.addEventListener("pointercancel", endPointerDrag);
        });
      }

      ms.addEventListener("change", (ev) => {
        if (ev.target.matches?.('input[type="checkbox"]')) partitionSelection();
        updateCount();
      });
      updateCount();
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

  // ----- Location search ---------------------------------------------
  //
  // Wires up the ``location_search`` cell option type: a text input with
  // debounced autocomplete against Open-Meteo's free geocoding endpoint
  // (no API key, CC-BY licensed, same provider as Tesserae's weather
  // data). On select, the chosen result writes JSON into a hidden input
  // so the form-submit flow demuxes it like any other field.
  //
  // The Open-Meteo response shape:
  //   { results: [
  //       { id, name, latitude, longitude, country, admin1, admin2, ... }
  //   ]}
  // We keep only ``{name, country, admin1, latitude, longitude}`` server
  // side, see ``_coerce_cell_option`` in app/page_routes.py.
  function attachLocationSearch(root) {
    const fields = root.querySelectorAll("[data-location-search]:not([data-location-bound])");
    fields.forEach((field) => {
      field.dataset.locationBound = "1";
      const display = field.querySelector("[data-location-display]");
      const storage = field.querySelector("[data-location-storage]");
      const results = field.querySelector("[data-location-results]");
      const clearBtn = field.querySelector("[data-location-clear]");
      if (!display || !storage || !results) return;

      let timer = null;
      let lastQuery = "";

      function hideResults() {
        results.hidden = true;
        results.innerHTML = "";
      }

      function fireChange() {
        // editor.js wires both ``input`` and ``change`` listeners on the
        // form; the input listener gates immediate preview updates while
        // ``change`` is the autosave commit point. Dispatching both
        // covers either pathway, important because the storage element
        // is a hidden input (which doesn't have a natural blur event)
        // so the ``change`` listener path is the only "settled" signal
        // the editor sees from us. Both events bubble so the form-level
        // listener catches them.
        storage.dispatchEvent(new Event("input", { bubbles: true }));
        storage.dispatchEvent(new Event("change", { bubbles: true }));
      }

      function renderPill(loc) {
        // Remove any existing pill so we don't end up with two.
        const existing = field.querySelector("[data-location-pill]");
        if (existing) existing.remove();
        if (!loc) return;
        const pill = document.createElement("div");
        pill.className = "location-search-pill";
        pill.dataset.locationPill = "1";
        const parts = [loc.name];
        if (loc.admin1 && loc.admin1 !== loc.name) parts.push(loc.admin1);
        if (loc.country) parts.push(loc.country);
        const coords =
          typeof loc.latitude === "number" && typeof loc.longitude === "number"
            ? loc.latitude.toFixed(4) + ", " + loc.longitude.toFixed(4)
            : "";
        pill.innerHTML =
          '<i class="ph-bold ph-map-pin location-search-pill-icon" aria-hidden="true"></i>' +
          '<div class="location-search-pill-body">' +
          '<span class="location-search-pill-name">' +
          _escapeHtml(parts.join(", ")) +
          "</span>" +
          (coords
            ? '<span class="location-search-pill-coords">' +
              _escapeHtml(coords) +
              "</span>"
            : "") +
          "</div>";
        field.appendChild(pill);
      }

      function setInputState(isSet) {
        const inputBox = field.querySelector(".location-search-input");
        if (!inputBox) return;
        inputBox.classList.toggle("is-set", isSet);
        // Show/hide the clear button to match the state. Created on
        // demand if it doesn't exist yet (fresh cells render without
        // it since there's nothing to clear).
        let clr = inputBox.querySelector("[data-location-clear]");
        if (isSet && !clr) {
          clr = document.createElement("button");
          clr.type = "button";
          clr.className = "location-search-clear";
          clr.dataset.locationClear = "1";
          clr.title = "Clear";
          clr.setAttribute("aria-label", "Clear location");
          clr.innerHTML = '<i class="ph ph-x" aria-hidden="true"></i>';
          clr.addEventListener("click", clearLocation);
          inputBox.appendChild(clr);
        } else if (!isSet && clr) {
          clr.remove();
        }
      }

      function selectResult(loc) {
        storage.value = JSON.stringify(loc);
        display.value = loc.name || "";
        // Auto-fill the sibling Label input in the same form with the
        // picked city's name. The user can then edit it for a custom
        // display ("Home" instead of "Berlin"); on a subsequent
        // location pick we'll overwrite again, which is the simpler-
        // to-explain shape than tracking a per-input "user has
        // edited" flag.
        const form = field.closest("form");
        const labelInput = form
          ? form.querySelector('[name="opt_label"]')
          : null;
        if (labelInput && "value" in labelInput) {
          labelInput.value = loc.name || "";
        }
        renderPill(loc);
        setInputState(true);
        hideResults();
        // ``tesseraeForcePreview`` bypasses the patch-path fingerprint
        // cache that was eating the location-pick re-render
        // (re-fetching weather with the same units produced
        // byte-identical ``data``, so even though ``options`` changed
        // the iframe's per-cell fingerprint check decided to skip
        // the render). Force a full iframe reload instead, which is
        // exactly what the user observed when they nudged units to
        // C/F manually.
        if (typeof window.tesseraeForcePreview === "function") {
          window.tesseraeForcePreview();
        } else if (typeof window.tesseraeSchedulePreview === "function") {
          window.tesseraeSchedulePreview();
        } else {
          fireChange();
        }
      }

      function clearLocation() {
        storage.value = "";
        display.value = "";
        renderPill(null);
        setInputState(false);
        hideResults();
        lastQuery = "";
        if (typeof window.tesseraeForcePreview === "function") {
          window.tesseraeForcePreview();
        } else if (typeof window.tesseraeSchedulePreview === "function") {
          window.tesseraeSchedulePreview();
        } else {
          fireChange();
        }
      }

      async function search(query) {
        if (!query || query.length < 2) {
          hideResults();
          return;
        }
        if (query === lastQuery) return;
        lastQuery = query;
        try {
          const url = new URL("https://geocoding-api.open-meteo.com/v1/search");
          url.searchParams.set("name", query);
          url.searchParams.set("count", "5");
          url.searchParams.set("language", "en");
          url.searchParams.set("format", "json");
          const resp = await fetch(url, { credentials: "omit" });
          if (!resp.ok) {
            hideResults();
            return;
          }
          const data = await resp.json();
          const list = (data && data.results) || [];
          if (!list.length) {
            results.innerHTML =
              '<li class="location-search-empty">No matches.</li>';
            results.hidden = false;
            return;
          }
          results.innerHTML = list
            .map((r, i) => {
              // Use index-based id so we can read the chosen result back
              // out of ``list`` on click without re-parsing.
              const parts = [r.name];
              if (r.admin1 && r.admin1 !== r.name) parts.push(r.admin1);
              if (r.country) parts.push(r.country);
              const label = parts.join(", ");
              return (
                '<li class="location-search-result" tabindex="0" data-idx="' +
                i +
                '">' +
                '<i class="ph ph-map-pin" aria-hidden="true"></i>' +
                '<span class="location-search-result-label">' +
                _escapeHtml(label) +
                "</span>" +
                "</li>"
              );
            })
            .join("");
          results.hidden = false;
          // Pre-bind click handlers, scoped to this batch of results so
          // a stale list doesn't fire selectResult against the wrong list.
          results.querySelectorAll("[data-idx]").forEach((row) => {
            row.addEventListener("click", () => {
              const i = parseInt(row.dataset.idx || "0", 10);
              const r = list[i];
              if (!r) return;
              selectResult({
                name: r.name,
                country: r.country || "",
                admin1: r.admin1 || "",
                latitude: r.latitude,
                longitude: r.longitude,
              });
            });
            row.addEventListener("keydown", (ev) => {
              if (ev.key === "Enter") {
                ev.preventDefault();
                row.click();
              }
            });
          });
        } catch (err) {
          // Network-level fail: silently hide rather than show a
          // confusing error. The user just keeps typing.
          console.warn("[location_search] geocoding fetch failed:", err);
          hideResults();
        }
      }

      display.addEventListener("input", () => {
        clearTimeout(timer);
        const q = display.value.trim();
        timer = setTimeout(() => search(q), 300);
      });

      display.addEventListener("blur", () => {
        // Delay so a click on a result row registers before the dropdown
        // hides. 200ms matches the typical click latency.
        setTimeout(hideResults, 200);
      });

      display.addEventListener("focus", () => {
        if (results.innerHTML) results.hidden = false;
      });

      if (clearBtn) {
        clearBtn.addEventListener("click", clearLocation);
      }
    });
  }

  function _escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Initial attach on DOMContentLoaded; re-attach when the editor reloads
  // its preview iframe (a side-effect of a save).
  function init() {
    attachSliders(document);
    attachPreviewFit(document);
    attachSendFitPreview(document);
    attachPresetNumbers(document);
    attachLocationSearch(document);
    attachMultiSelect(document);
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
    attachLocationSearch,
    attachMultiSelect,
    fitPreview,
  };
})();
