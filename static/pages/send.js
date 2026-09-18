// Live History updates for /send. The Saved-page / Resend / URL / etc
// POST handlers now background the push and redirect immediately, so
// the new history row arrives a few seconds *after* the page renders.
// Subscribe to the same SSE stream the Events tab uses, filtered to
// push events, and swap the History list when one lands.
//
// We do a full partial re-fetch rather than building the row client-
// side: the row markup includes a thumbnail, friendly page name,
// per-renderer status pills, and a duration, duplicating all of that
// in JS would drift the moment the server-side template changes. One
// extra GET per push is cheap.

(function () {
  if (typeof EventSource === "undefined") return;

  function swapHistory() {
    // Re-fetch /send?tab=history and swap just the <ul.history> node.
    // The fetch hits the same admin auth as the page itself, so a
    // logged-out tab gracefully fails (the fetch redirects to the
    // login page, the DOMParser finds no .history, and we bail).
    fetch(window.location.pathname + "?tab=history", {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then((r) => (r.ok ? r.text() : null))
      .then((html) => {
        if (!html) return;
        const doc = new DOMParser().parseFromString(html, "text/html");
        const fresh =
          doc.querySelector(".history") ||
          doc.querySelector("[data-history-empty]");
        const current =
          document.querySelector(".history") ||
          document.querySelector("[data-history-empty]");
        if (fresh && current) {
          current.replaceWith(fresh);
        }
      })
      .catch(() => {
        // Network blip is fine, EventSource will deliver the next
        // event and we'll try again then.
      });
  }

  // Debounce, a one-off send fires a single push event but a fan-out
  // to multiple devices emits one per target. Collapse rapid bursts
  // into one re-fetch instead of N.
  let pending = null;
  function scheduleSwap() {
    if (pending !== null) return;
    pending = setTimeout(() => {
      pending = null;
      swapHistory();
    }, 300);
  }

  const prefix = window.TESSERAE_URL_PREFIX || "";
  const es = new EventSource(`${prefix}/events/stream?type=push`);
  // The SSE endpoint names its event "log" (see app/events_routes.py:
  // ``event: log\ndata: …``). Listening for "event" or default
  // ``onmessage`` would silently miss every push.
  es.addEventListener("log", scheduleSwap);
})();

// The one box.
//
// One form, one input. Whatever lands in the box (a dropped file, a
// pasted link, typed text) is classified as Image / Note / Webpage; the
// segmented control shows the guess and lets the user override it. On
// submit the form is pointed at the endpoint for that kind, with the
// text field renamed to what the endpoint reads (``url`` or ``text``).
// The detection rule mirrors ``app.note_render.detect_kind``; keep the
// two in step.
(function () {
  const form = document.querySelector("[data-send-form]");
  if (!form) return;
  const page = form.closest("[data-send-page]") || document;
  const q = (sel, root) => (root || form).querySelector(sel);

  const box = q("[data-send-box]");
  const kindRadios = Array.from(form.querySelectorAll("[data-send-kind]"));
  const autoTag = q("[data-send-auto]");
  const hint = q("[data-send-hint]");
  const emptyBlock = q("[data-send-empty]");
  const valueWrap = q("[data-send-value]");
  const editor = q("[data-send-editor]");
  const textField = q("[data-send-text]");
  const fileRow = q("[data-send-file-row]");
  const fileThumb = q("[data-send-file-thumb]");
  const fileName = q("[data-send-file-name]");
  const fileSize = q("[data-send-file-size]");
  const fileClear = q("[data-send-file-clear]");
  const foot = q("[data-send-foot]");
  const fileInput = q("[data-send-file]");
  const options = q("[data-send-options]");
  const optTitle = q("[data-send-options-title]");
  const optValue = q("[data-send-options-value]");
  const panes = Array.from(form.querySelectorAll("[data-send-pane]"));
  const viewportW = q("[data-send-viewport-w]");
  const viewportH = q("[data-send-viewport-h]");
  const headersField = q("[data-send-headers]");
  const fitSelect = q("[data-send-fit]");
  const rotateSelect = q("[data-send-rotate]");
  const noteOpts = Array.from(form.querySelectorAll("[data-send-note-opt]"));
  const checklist = q("[data-send-device-checklist]");
  const pushBtn = q("[data-send-push]");
  const pushLabel = q("[data-send-push-label]");
  const galleryFields = Array.from(form.querySelectorAll("[data-send-gallery-field]"));

  const frame = q("[data-fit-preview]", page);
  const previewImg = q("[data-send-preview-image]", page);
  const previewBg = q("[data-send-preview-bg]", page);
  const previewWeb = q("[data-send-preview-webpage]", page);
  const previewNote = q("[data-send-preview-note]", page);
  const previewPh = q("[data-send-preview-placeholder]", page);
  const previewPhText = q("[data-send-preview-placeholder-text]", page);
  const previewDims = q("[data-send-preview-dims]", page);
  const previewDesc = q("[data-send-preview-desc]", page);
  const previewDevice = q("[data-send-preview-device]", page);

  const EMPTY_HINT = "Picks itself once you paste or drop something.";
  const FILLED_HINT = "Picked from what you pasted. Change it if the guess is wrong.";
  const EMPTY_PREVIEW = "Preview appears once there is something to send";
  const IMAGE_EXT = /\.(png|jpe?g|gif|bmp|webp|heic)$/i;
  const FIT_LABELS = {
    fit: "fit letterbox",
    fill: "fill crop",
    stretch: "stretch",
    center: "centre",
    blur: "centre + blur",
  };
  const ROTATE_LABELS = {
    0: "no rotation",
    auto: "auto rotation",
    90: "90° cw",
    180: "180°",
    270: "270° cw",
  };
  const ALIGN_LABELS = { left: "left", center: "centre", right: "right" };
  const KIND_TITLES = { image: "Image options", note: "Note options", webpage: "Web page options" };

  const state = {
    text: "",
    file: null,
    fileUrl: "",
    gallery: !!(galleryFields.length && box.dataset.galleryUrl),
    override: null,
    typing: false,
    viewportTouched: false,
    ingesting: null,
  };

  // ----- detection ---------------------------------------------------
  function detectKind(text) {
    const s = text.trim();
    if (!s) return "";
    if (!/\n/.test(s) && /^https?:\/\/\S+$/i.test(s)) {
      let path = "";
      try {
        path = new URL(s).pathname;
      } catch (e) {
        return "webpage";
      }
      return IMAGE_EXT.test(path) ? "image" : "webpage";
    }
    return "note";
  }

  function currentSource() {
    if (state.file) return "file";
    if (state.gallery) return "gallery";
    if (state.text.trim()) return "text";
    return "";
  }
  function autoKind() {
    const src = currentSource();
    if (src === "file" || src === "gallery") return "image";
    if (src === "text") return detectKind(state.text);
    return "";
  }
  function kind() {
    const src = currentSource();
    if (src === "file" || src === "gallery") return "image";
    return state.override || autoKind();
  }

  // ----- summary strings --------------------------------------------
  function headersSummary() {
    const raw = headersField ? headersField.value.trim() : "";
    if (!raw) return "no headers";
    try {
      const obj = JSON.parse(raw);
      const n = obj && typeof obj === "object" ? Object.keys(obj).length : 0;
      return n === 1 ? "1 header" : `${n} headers`;
    } catch (e) {
      return "headers not valid JSON";
    }
  }
  function optionsSummary(k) {
    if (k === "webpage") {
      const w = viewportW ? viewportW.value : "";
      const h = viewportH ? viewportH.value : "";
      return `${w} × ${h} · network idle · ${headersSummary()}`;
    }
    if (k === "image") {
      const fit = fitSelect ? FIT_LABELS[fitSelect.value] || fitSelect.value : "";
      const rot = rotateSelect ? ROTATE_LABELS[rotateSelect.value] || rotateSelect.value : "";
      return `${fit} · ${rot}`;
    }
    if (k === "note") {
      const [size, align, theme] = noteOpts.map((s) => s);
      const themeOpt = theme && theme.selectedOptions[0];
      let themeLabel = themeOpt ? themeOpt.textContent.trim() : "panel default";
      if (theme && theme.value === "default") themeLabel = "panel default";
      else themeLabel = themeLabel.split(" · ")[0];
      return `${size ? size.value : ""} · ${align ? ALIGN_LABELS[align.value] || align.value : ""} · ${themeLabel}`;
    }
    return "";
  }

  // ----- render ------------------------------------------------------
  function render() {
    const src = currentSource();
    const k = kind();
    const fileLike = src === "file" || src === "gallery";
    const showEditor = src === "text" || (state.typing && !src);

    box.dataset.state = src ? (src === "text" ? "filled" : "file") : state.typing ? "typing" : "empty";
    kindRadios.forEach((r) => {
      r.checked = r.value === k;
      r.disabled = fileLike && r.value !== "image";
    });
    autoTag.hidden = !k || (!!state.override && !fileLike);
    hint.textContent = src ? FILLED_HINT : EMPTY_HINT;

    emptyBlock.hidden = !!src || state.typing;
    valueWrap.hidden = !showEditor;
    fileRow.hidden = !fileLike;
    foot.hidden = !showEditor;
    editor.classList.toggle("is-url", k === "image" || k === "webpage");
    editor.classList.toggle("is-note", k === "note" || (!k && showEditor));

    options.hidden = !k;
    panes.forEach((p) => {
      const on = p.dataset.sendPane === k;
      p.hidden = !on;
      p.disabled = !on;
    });
    if (k) {
      optTitle.textContent = KIND_TITLES[k];
      optValue.textContent = optionsSummary(k);
    }

    textField.name = k === "note" ? "text" : "url";
    textField.value = k === "note" ? state.text : state.text.trim();

    const targets = checklist ? checklist.querySelectorAll("input:checked").length : 0;
    pushBtn.disabled = !src || !targets;
    pushLabel.textContent = targets
      ? `Push to ${targets} display${targets === 1 ? "" : "s"}`
      : "Push";
    pushBtn.title = !src
      ? "Paste a link, drop an image, or type a note first."
      : !targets
        ? "Tick at least one target display first."
        : "";

    renderPreview(src, k);
  }

  // ----- preview -----------------------------------------------------
  let previewKey = "";
  let previewTimer = null;
  function showOnly(el) {
    [previewImg, previewWeb, previewNote, previewPh].forEach((x) => {
      if (x) x.hidden = x !== el;
    });
    if (previewBg && el !== previewImg) previewBg.hidden = true;
  }
  function previewSize() {
    const opt = previewDevice && previewDevice.selectedOptions[0];
    if (opt) {
      return {
        w: parseInt(opt.dataset.panelW, 10),
        h: parseInt(opt.dataset.panelH, 10),
        name: opt.textContent.trim(),
      };
    }
    return {
      w: parseInt(form.dataset.defaultPanelW, 10),
      h: parseInt(form.dataset.defaultPanelH, 10),
      name: "",
    };
  }
  function applyDims() {
    const { w, h, name } = previewSize();
    if (!frame || !w || !h) return;
    frame.dataset.panelW = String(w);
    frame.dataset.panelH = String(h);
    frame.style.aspectRatio = `${w} / ${h}`;
    frame.querySelectorAll("iframe").forEach((el) => {
      el.style.width = w + "px";
      el.style.height = h + "px";
    });
    if (previewDims) previewDims.textContent = `${w} × ${h}`;
    if (previewDesc) {
      previewDesc.textContent = name
        ? `Rendered for ${name}. Switch display to check the others.`
        : "What the panel will show after fitting.";
    }
    if (!state.viewportTouched && viewportW && viewportH) {
      viewportW.value = String(w);
      viewportH.value = String(h);
    }
    if (window.tesseraeComponents && window.tesseraeComponents.fitPreview) {
      window.tesseraeComponents.fitPreview(frame);
    }
  }
  function noteParams() {
    const p = new URLSearchParams();
    p.set("text", state.text);
    noteOpts.forEach((s) => p.set(s.name, s.value));
    return p.toString();
  }
  function renderPreview(src, k) {
    let key = "";
    let apply = null;
    if (!src) {
      key = "empty";
      apply = () => {
        previewPhText.textContent = EMPTY_PREVIEW;
        showOnly(previewPh);
        previewImg.removeAttribute("src");
        previewWeb.src = "about:blank";
        previewNote.src = "about:blank";
      };
    } else if (src === "file") {
      key = "file:" + state.fileUrl;
      apply = () => {
        previewImg.src = state.fileUrl;
        showOnly(previewImg);
      };
    } else if (src === "gallery") {
      key = "gallery";
      apply = () => {
        previewImg.src = box.dataset.galleryUrl;
        showOnly(previewImg);
      };
    } else if (k === "image") {
      const url = state.text.trim();
      key = "image:" + url;
      apply = () => {
        previewImg.onerror = () => {
          previewPhText.textContent =
            "The browser could not load that link. The push fetches it server-side.";
          showOnly(previewPh);
        };
        previewImg.onload = () => showOnly(previewImg);
        previewImg.src = url;
      };
    } else if (k === "webpage") {
      const url = state.text.trim();
      key = "webpage:" + url;
      apply = () => {
        try {
          new URL(url);
        } catch (e) {
          previewPhText.textContent = "Enter a full link, starting with http:// or https://";
          showOnly(previewPh);
          return;
        }
        previewWeb.src = url;
        showOnly(previewWeb);
      };
    } else {
      const params = noteParams();
      key = "note:" + params;
      apply = () => {
        previewNote.src = form.dataset.notePreviewUrl + "?" + params;
        showOnly(previewNote);
      };
    }
    if (key === previewKey) return;
    previewKey = key;
    clearTimeout(previewTimer);
    // Text sources are debounced so a keystroke burst is one load.
    const delay = src === "text" ? (k === "note" ? 350 : 600) : 0;
    previewTimer = setTimeout(apply, delay);
  }

  // ----- text input --------------------------------------------------
  function readEditor() {
    // ``innerText`` ends with a stray newline once a second line exists.
    return editor.innerText.replace(/\n$/, "");
  }
  function setText(text, { focus } = {}) {
    state.text = text;
    editor.textContent = text;
    if (!text.trim()) state.override = null;
    render();
    if (focus) {
      editor.focus();
      const sel = window.getSelection();
      if (sel && editor.lastChild) {
        const range = document.createRange();
        range.selectNodeContents(editor);
        range.collapse(false);
        sel.removeAllRanges();
        sel.addRange(range);
      }
    }
  }
  editor.addEventListener("input", () => {
    state.text = readEditor();
    if (!state.text.trim()) state.override = null;
    render();
  });
  editor.addEventListener("paste", (e) => {
    const dt = e.clipboardData;
    if (!dt) return;
    if (dt.files && dt.files.length) {
      e.preventDefault();
      ingestFile(dt.files[0]);
      return;
    }
    const text = dt.getData("text/plain");
    if (text) {
      e.preventDefault();
      document.execCommand("insertText", false, text);
    }
  });
  editor.addEventListener("keydown", (e) => {
    // A link is one line: Enter pushes it. Shift+Enter adds a line, which
    // turns the text into a note.
    if (e.key === "Enter" && !e.shiftKey && kind() !== "note") {
      e.preventDefault();
      if (!pushBtn.disabled) form.requestSubmit();
    }
  });
  editor.addEventListener("blur", () => {
    setTimeout(() => {
      if (!state.text.trim() && !state.file && !state.gallery && document.activeElement !== editor) {
        state.typing = false;
        render();
      }
    }, 150);
  });

  function startTyping(seed) {
    state.typing = true;
    render();
    editor.focus();
    if (seed) document.execCommand("insertText", false, seed);
  }
  emptyBlock.addEventListener("click", (e) => {
    if (e.target.closest("label")) return;
    startTyping();
  });
  emptyBlock.addEventListener("keydown", (e) => {
    if (e.target !== emptyBlock) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      startTyping();
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      startTyping(e.key);
    }
  });

  // Paste anywhere on the page (nothing else focused) lands in the box.
  document.addEventListener("paste", (e) => {
    const a = document.activeElement;
    if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) return;
    const dt = e.clipboardData;
    if (!dt) return;
    if (dt.files && dt.files.length) {
      e.preventDefault();
      ingestFile(dt.files[0]);
      return;
    }
    const text = dt.getData("text/plain");
    if (text && text.trim()) {
      e.preventDefault();
      state.typing = true;
      setText(text.trim(), { focus: true });
    }
  });

  // ----- kind override -----------------------------------------------
  kindRadios.forEach((r) => {
    r.addEventListener("change", () => {
      if (!r.checked) return;
      state.override = r.value === autoKind() ? null : r.value;
      if (!currentSource()) state.typing = true;
      render();
      if (!currentSource()) editor.focus();
    });
  });

  // ----- files -------------------------------------------------------
  function fmt(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(2) + " MB";
  }
  function showFile(file) {
    if (state.fileUrl) URL.revokeObjectURL(state.fileUrl);
    state.file = file;
    state.fileUrl = URL.createObjectURL(file);
    fileThumb.src = state.fileUrl;
    fileName.textContent = file.name;
    fileSize.textContent = fmt(file.size);
    state.override = null;
    render();
  }
  // Read the file's bytes into memory NOW and replace input.files with a
  // fresh File backed by those bytes. Android Chrome lazy-reads the file
  // URI at submit time and fails with ERR_UPLOAD_FILE_CHANGED when Google
  // Photos sync, HEIC conversion, or an EXIF rewrite touch the file
  // between selection and submit. Snapshotting decouples submit from the
  // URI; the submit handler waits for an in-flight snapshot.
  async function ingestFile(rawFile) {
    if (!rawFile) return;
    if (rawFile.__tesserae_snapshotted) return;
    state.ingesting = (async () => {
      let snapped = rawFile;
      try {
        const buf = await rawFile.arrayBuffer();
        snapped = new File([buf], rawFile.name, {
          type: rawFile.type || "application/octet-stream",
          lastModified: rawFile.lastModified,
        });
        Object.defineProperty(snapped, "__tesserae_snapshotted", { value: true });
      } catch (err) {
        console.warn("tesserae: file snapshot failed, falling back", err);
      }
      const dt = new DataTransfer();
      dt.items.add(snapped);
      fileInput.files = dt.files;
      clearGallery();
      showFile(snapped);
    })();
    try {
      await state.ingesting;
    } finally {
      state.ingesting = null;
    }
  }
  function clearGallery() {
    if (!state.gallery) return;
    state.gallery = false;
    galleryFields.forEach((f) => {
      f.disabled = true;
    });
  }
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) ingestFile(fileInput.files[0]);
  });
  fileClear.addEventListener("click", (e) => {
    e.preventDefault();
    fileInput.value = "";
    if (state.fileUrl) URL.revokeObjectURL(state.fileUrl);
    state.file = null;
    state.fileUrl = "";
    clearGallery();
    render();
  });

  ["dragenter", "dragover"].forEach((ev) =>
    box.addEventListener(ev, (e) => {
      e.preventDefault();
      box.classList.add("is-dragging");
    }),
  );
  ["dragleave", "dragend", "drop"].forEach((ev) =>
    box.addEventListener(ev, (e) => {
      e.preventDefault();
      box.classList.remove("is-dragging");
    }),
  );
  box.addEventListener("drop", (e) => {
    const dt = e.dataTransfer;
    if (!dt) return;
    const file = dt.files && dt.files[0];
    if (file) {
      ingestFile(file);
      return;
    }
    const text = (dt.getData("text/uri-list") || dt.getData("text/plain") || "").trim();
    if (text) {
      state.typing = true;
      setText(text, { focus: true });
    }
  });

  // ----- options + targets -------------------------------------------
  [viewportW, viewportH].forEach((el) => {
    if (!el) return;
    el.addEventListener("input", () => {
      state.viewportTouched = true;
    });
  });
  options.addEventListener("input", () => render());
  options.addEventListener("change", () => render());

  // Ticked-state class on each target tile, for browsers without :has().
  function syncTiles() {
    if (!checklist) return;
    checklist.querySelectorAll('input[name="device_id"]').forEach((box) => {
      const tile = box.closest(".tile");
      if (tile) tile.classList.toggle("is-on", box.checked);
    });
  }
  syncTiles();
  if (checklist) {
    checklist.addEventListener("change", (ev) => {
      syncTiles();
      if (!ev.target || !ev.target.matches('input[name="device_id"]')) return;
      // Follow the tick into the preview when the shown display isn't ticked.
      if (previewDevice && ev.target.checked) {
        const shown = previewDevice.value;
        const shownTicked = checklist.querySelector(
          `input[name="device_id"][value="${CSS.escape(shown)}"]:checked`,
        );
        if (!shownTicked) previewDevice.value = ev.target.value;
      }
      applyDims();
      previewKey = "";
      render();
    });
  }
  if (previewDevice) {
    previewDevice.addEventListener("change", () => {
      applyDims();
      render();
    });
  }

  // ----- submit ------------------------------------------------------
  form.addEventListener("submit", (e) => {
    const src = currentSource();
    const k = kind();
    if (!src || pushBtn.disabled) {
      e.preventDefault();
      return;
    }
    if (state.ingesting) {
      e.preventDefault();
      state.ingesting.then(() => form.requestSubmit());
      return;
    }
    let action = "url";
    if (src === "file") action = "file";
    else if (src === "gallery") action = "gallery";
    else if (k === "note") action = "note";
    else if (k === "webpage") action = "webpage";
    form.action = form.dataset["action" + action.charAt(0).toUpperCase() + action.slice(1)];
    textField.name = k === "note" ? "text" : "url";
    textField.value = k === "note" ? state.text : state.text.trim();
  });

  // ----- init --------------------------------------------------------
  if (previewDevice && checklist) {
    const ticked = checklist.querySelector('input[name="device_id"]:checked');
    if (ticked) previewDevice.value = ticked.value;
  }
  if (state.gallery) {
    fileThumb.src = box.dataset.galleryUrl;
    fileName.textContent = box.dataset.galleryName || "";
    fileSize.textContent = box.dataset.galleryFolder ? "from " + box.dataset.galleryFolder : "";
  }
  const initialText = form.dataset.initialText || "";
  if (initialText) {
    state.text = initialText;
    editor.textContent = initialText;
    const initialKind = form.dataset.initialKind || "";
    if (initialKind && initialKind !== detectKind(initialText)) state.override = initialKind;
  }
  applyDims();
  render();
})();
