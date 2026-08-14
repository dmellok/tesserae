/* Browse catalog (/plugins/browse).
 *
 * One list over three item types: widgets and themes from the static
 * catalog index (handed over as JSON by the route) and community
 * templates from api.tesserae.ink (fetched through the server proxy at
 * /plugins/templates/index.json). Everything below the page shell is
 * rendered here: the filter rail's type counts and category chips, the
 * result rows, the resolution groups Templates mode switches into, and
 * the detail sheet.
 *
 * Nothing navigates. Filters, search and sort are pure functions of the
 * in-memory model, install/uninstall POST via fetch and patch the row in
 * place, and the filter state is mirrored into the URL with
 * replaceState so a reload or a shared link lands on the same view.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-catalog]");
  if (!root) return;

  // Script root, empty outside a subpath deployment. Under the Home
  // Assistant App every URL sits beneath /api/hassio_ingress/<token>/.
  var PREFIX = window.TESSERAE_URL_PREFIX || "";

  var payload = readPayload();
  var CATS = payload.categories || [];
  var MY_RES = payload.my_resolutions || [];
  var RES_DEVICES = payload.resolution_devices || {};

  var SORTS = [
    { key: "installs", label: "Most installed" },
    { key: "name", label: "Name A–Z" },
    { key: "recent", label: "Recently added" },
  ];

  var state = {
    type: payload.initial && payload.initial.type ? payload.initial.type : "All",
    status: payload.initial && payload.initial.status ? payload.initial.status : "All",
    cat: (payload.initial && payload.initial.tag) || "All",
    q: (payload.initial && payload.initial.q) || "",
    sort: "installs",
    fitsOnly: false,
    selectedId: null,
    // id -> "install" | "uninstall" while a request is in flight.
    pending: {},
  };

  var items = (payload.items || []).map(fromCatalogEntry);
  var templatesLoaded = false;
  var templatesError = null;

  // A ?kind= deep link (the old widget-Browse chip URLs) maps onto the
  // new Type facet so existing links keep narrowing the same way.
  var initialKind = payload.initial && payload.initial.kind;
  if (initialKind === "widget") state.type = "Widgets";
  else if (initialKind === "theme") state.type = "Themes";
  else if (initialKind === "font") state.type = "Fonts";

  // -- elements ----------------------------------------------------------

  var elTypes = root.querySelector("[data-cat-types]");
  var elStatus = root.querySelector("[data-cat-status]");
  var elChips = root.querySelector("[data-cat-chips]");
  var elClearCat = root.querySelector("[data-cat-clear]");
  var elFitSection = root.querySelector("[data-cat-fit-section]");
  var elFits = root.querySelector("[data-cat-fits]");
  var elMyRes = root.querySelector("[data-cat-my-res]");
  var elSearch = root.querySelector("[data-cat-search]");
  var elSearchForm = root.querySelector("[data-cat-search-form]");
  var elCount = root.querySelector("[data-cat-count]");
  var elSort = root.querySelector("[data-cat-sort]");
  var elSortLabel = root.querySelector("[data-cat-sort-label]");
  var elResults = root.querySelector("[data-cat-results]");
  var sheetRoot = document.querySelector("[data-cat-sheet]");
  var sheetPanel = sheetRoot ? sheetRoot.querySelector("[data-cat-sheet-panel]") : null;

  // -- model -------------------------------------------------------------

  function readPayload() {
    var node = document.getElementById("catalog-data");
    if (!node) return {};
    try {
      return JSON.parse(node.textContent) || {};
    } catch {
      return {};
    }
  }

  function fromCatalogEntry(e) {
    return {
      id: e.id,
      kind: e.kind === "theme" || e.kind === "font" ? e.kind : "widget",
      name: e.name,
      desc: e.description || "",
      version: e.version || "",
      author: e.author_name || "",
      authorGithub: e.author_github || null,
      cats: e.tags || [],
      folders: e.folders || [],
      source: e.source || null,
      installs: e.installs || 0,
      stars: e.stars || 0,
      verified: !!e.official,
      installed: !!e.installed,
      installedFromDisk: !!e.installed_from_disk,
      installedVersion: e.installed_version || null,
      updateAvailable: !!e.update_available,
      icon: e.icon || "ph-puzzle-piece",
      iconUrl: e.icon_url || null,
      previews: e.screenshot_urls || [],
      date: null,
      res: null,
      resKey: null,
    };
  }

  function fromTemplateEntry(t) {
    var w = Number(t.w || 0);
    var h = Number(t.h || 0);
    var key = w + "x" + h;
    return {
      id: "tpl:" + t.slug,
      slug: t.slug,
      kind: "template",
      name: t.title || t.slug,
      desc: t.description || "",
      version: "",
      author: (t.author && t.author.name) || "unknown",
      sponsor: !!(t.author && t.author.sponsor),
      authorGithub: null,
      cats: t.tags || [],
      // A template doesn't install plugin folders; it needs widgets to
      // already be here. Same column, different meaning, and the sheet
      // relabels it "Uses widgets".
      folders: t.requires || [],
      missing: t.missing_requires || [],
      source: null,
      installs: t.installs || 0,
      stars: 0,
      verified: false,
      installed: false,
      installedFromDisk: false,
      installedVersion: null,
      updateAvailable: false,
      icon: "ph-layout",
      iconUrl: null,
      previews: t.preview_url ? [t.preview_url] : [],
      inputs: Number(t.inputs || 0),
      date: t.created_at || null,
      w: w,
      h: h,
      res: w + " × " + h,
      resKey: key,
      mine: fitsMyPanels(key),
      raw: t,
    };
  }

  function transpose(key) {
    var parts = String(key).split("x");
    return parts[1] + "x" + parts[0];
  }

  function fitsMyPanels(key) {
    return MY_RES.indexOf(key) !== -1 || MY_RES.indexOf(transpose(key)) !== -1;
  }

  function deviceNamesFor(key) {
    var exact = RES_DEVICES[key] || [];
    var rotated = (RES_DEVICES[transpose(key)] || []).map(function (n) {
      return n + " (rotated)";
    });
    return exact.concat(rotated);
  }

  function typeOf(item) {
    if (item.kind === "widget") return "Widgets";
    if (item.kind === "theme") return "Themes";
    if (item.kind === "font") return "Fonts";
    return "Templates";
  }

  function typeRows() {
    var rows = ["All", "Widgets", "Themes"];
    if (
      items.some(function (i) {
        return i.kind === "font";
      })
    )
      rows.push("Fonts");
    if (payload.templates_enabled) rows.push("Templates");
    return rows;
  }

  // -- search ------------------------------------------------------------
  //
  // Tokenised AND match with a relevance score, so "ha album" finds the
  // Home Assistant album-art widget and a name hit outranks a mention
  // buried in someone's description. Every token has to land somewhere;
  // the score is what orders the survivors.

  function tokenize(q) {
    return String(q || "")
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);
  }

  function subsequence(hay, needle) {
    // "wthr" matches "weather": cheap typo/abbreviation tolerance, and
    // only ever used as the weakest signal.
    if (needle.length < 3) return false;
    var i = 0;
    for (var c = 0; c < hay.length && i < needle.length; c++) {
      if (hay[c] === needle[i]) i++;
    }
    return i === needle.length;
  }

  function tokenScore(item, token) {
    var name = item.name.toLowerCase();
    var id = String(item.slug || item.id).toLowerCase();
    var desc = item.desc.toLowerCase();
    var author = item.author.toLowerCase();
    var cats = item.cats.join(" ").toLowerCase();
    var folders = item.folders.join(" ").toLowerCase();
    if (name === token) return 1000;
    if (name.indexOf(token) === 0) return 500;
    if (new RegExp("\\b" + token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).test(name)) return 300;
    if (name.indexOf(token) !== -1) return 200;
    if (id.indexOf(token) !== -1) return 150;
    if (cats.indexOf(token) !== -1) return 120;
    if (author.indexOf(token) !== -1) return 80;
    if (folders.indexOf(token) !== -1) return 60;
    if (desc.indexOf(token) !== -1) return 40;
    if (subsequence(name, token)) return 25;
    return 0;
  }

  function searchScore(item, tokens) {
    if (!tokens.length) return 0;
    var total = 0;
    for (var i = 0; i < tokens.length; i++) {
      var s = tokenScore(item, tokens[i]);
      if (!s) return -1; // every token must match something
      total += s;
    }
    return total;
  }

  // -- filtering ---------------------------------------------------------

  function matchesExceptType(item, tokens) {
    if (state.status === "Installed" && !item.installed) return false;
    if (state.status === "Available" && item.installed) return false;
    if (state.cat !== "All" && item.cats.indexOf(state.cat) === -1) return false;
    if (state.fitsOnly && item.kind === "template" && !item.mine) return false;
    if (tokens.length && searchScore(item, tokens) < 0) return false;
    return true;
  }

  function visible(type, tokens) {
    return items.filter(function (i) {
      return matchesExceptType(i, tokens) && (type === "All" || typeOf(i) === type);
    });
  }

  function sortItems(list, tokens) {
    var out = list.slice();
    out.sort(function (a, b) {
      if (tokens.length) {
        // Relevance leads while there's a query; the sort control is the
        // tiebreak, not the other way round.
        var d = searchScore(b, tokens) - searchScore(a, tokens);
        if (d) return d;
      }
      if (state.sort === "name") return a.name.localeCompare(b.name);
      if (state.sort === "recent") {
        // Only templates carry a real timestamp; the widget catalog
        // publishes no release date, so dated entries lead and the rest
        // fall back to install count rather than to a fabricated order.
        var da = a.date ? Date.parse(a.date) : 0;
        var db = b.date ? Date.parse(b.date) : 0;
        if (da !== db) return db - da;
        return b.installs - a.installs;
      }
      if (b.installs !== a.installs) return b.installs - a.installs;
      return a.name.localeCompare(b.name);
    });
    return out;
  }

  function groupsFor(list) {
    if (state.type !== "Templates") {
      return [{ label: null, items: list }];
    }
    var byRes = {};
    list.forEach(function (i) {
      (byRes[i.resKey] = byRes[i.resKey] || []).push(i);
    });
    var keys = Object.keys(byRes);
    keys.sort(function (a, b) {
      // The user's own panels first, then the busiest resolutions, then
      // by area so the order is stable run to run.
      var ma = fitsMyPanels(a) ? 0 : 1;
      var mb = fitsMyPanels(b) ? 0 : 1;
      if (ma !== mb) return ma - mb;
      if (byRes[b].length !== byRes[a].length) return byRes[b].length - byRes[a].length;
      function area(k) {
        var p = k.split("x");
        return Number(p[0]) * Number(p[1]);
      }
      return area(b) - area(a);
    });
    return keys.map(function (key) {
      var names = deviceNamesFor(key);
      var n = byRes[key].length;
      return {
        label: key.split("x").join(" × "),
        mine: fitsMyPanels(key),
        sub:
          (names.length ? "fits " + names.join(", ") + "  ·  " : "custom size  ·  ") +
          n +
          (n === 1 ? " template" : " templates"),
        items: byRes[key],
      };
    });
  }

  // -- DOM helpers -------------------------------------------------------

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else if (k === "class") node.className = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) {
        node.setAttribute(k, attrs[k]);
      }
    });
    (children || []).forEach(function (c) {
      if (c) node.appendChild(c);
    });
    return node;
  }

  function icon(name) {
    return el("i", { class: "ph " + name, "aria-hidden": "true" });
  }

  function plural(n, word) {
    return n + " " + word + (n === 1 ? "" : "s");
  }

  function fmtInstalls(n) {
    return Number(n || 0).toLocaleString();
  }

  /* Match highlighting without innerHTML: split the text on each token
     and wrap the hits in <mark>. Keeps the row scannable when a search
     narrows to a handful of results. */
  function highlighted(text, tokens, className) {
    var frag = document.createDocumentFragment();
    if (!tokens.length || !text) {
      frag.appendChild(document.createTextNode(text || ""));
      return frag;
    }
    var lower = text.toLowerCase();
    var marks = [];
    tokens.forEach(function (t) {
      var from = 0;
      var at = lower.indexOf(t, from);
      while (at !== -1) {
        marks.push([at, at + t.length]);
        from = at + t.length;
        at = lower.indexOf(t, from);
      }
    });
    if (!marks.length) {
      frag.appendChild(document.createTextNode(text));
      return frag;
    }
    marks.sort(function (a, b) {
      return a[0] - b[0];
    });
    var merged = [marks[0]];
    marks.slice(1).forEach(function (m) {
      var last = merged[merged.length - 1];
      if (m[0] <= last[1]) last[1] = Math.max(last[1], m[1]);
      else merged.push(m);
    });
    var cursor = 0;
    merged.forEach(function (m) {
      if (m[0] > cursor) frag.appendChild(document.createTextNode(text.slice(cursor, m[0])));
      frag.appendChild(
        el("mark", { class: className || "cat-mark", text: text.slice(m[0], m[1]) })
      );
      cursor = m[1];
    });
    if (cursor < text.length) frag.appendChild(document.createTextNode(text.slice(cursor)));
    return frag;
  }

  function previewFrame(item, size) {
    var frame = el("div", { class: "cat-thumb cat-thumb--" + size });
    var src = item.previews[0] || null;
    if (item.iconUrl) {
      // A project's own mark stands in for the preview: for a widget
      // that renders whatever the user's library holds, a screenshot is
      // one arbitrary photo and the logo is what people scan for.
      frame.classList.add("is-mark");
      var logo = el("img", { src: item.iconUrl, alt: "", loading: "lazy" });
      logo.addEventListener("error", function () {
        frame.classList.remove("is-mark");
        frame.textContent = "";
        frame.appendChild(icon(item.icon));
      });
      frame.appendChild(logo);
    } else if (src) {
      var img = el("img", { src: src, alt: "", loading: "lazy" });
      img.addEventListener("error", function () {
        img.remove();
        frame.classList.add("is-glyph");
        frame.appendChild(icon(item.icon));
      });
      frame.appendChild(img);
    } else {
      frame.classList.add("is-glyph");
      frame.appendChild(icon(item.icon));
    }
    return frame;
  }

  /* The sheet's preview, plus a thumbnail strip when the entry ships
     more than one screenshot. The old card grid gave those entries an
     inline carousel; a dense row has no space for one, so the extra
     shots live here instead of being dropped. */
  function sheetPreview(item) {
    var frame = previewFrame(item, "sheet");
    // The row thumbnails are small by design; the sheet is where a
    // preview is worth looking at, so clicking it opens the shot at
    // full size rather than at whatever the sheet's width allows.
    var shots = item.iconUrl ? [] : item.previews;
    if (shots.length) {
      frame.classList.add("is-zoomable");
      frame.addEventListener("click", function () {
        openLightbox(item, shots, sheetShotIndex);
      });
    }
    if (item.previews.length < 2) return frame;
    var wrap = el("div", { class: "cat-sheet-gallery" }, [frame]);
    var strip = el("div", { class: "cat-sheet-strip" });
    item.previews.forEach(function (src, idx) {
      var thumb = el(
        "button",
        {
          type: "button",
          class: "cat-sheet-strip-btn" + (idx === sheetShotIndex ? " is-active" : ""),
          "aria-label": "Screenshot " + (idx + 1) + " of " + item.previews.length,
        },
        [el("img", { src: src, alt: "", loading: "lazy" })]
      );
      thumb.addEventListener("click", function () {
        var img = frame.querySelector("img");
        if (img) img.src = src;
        sheetShotIndex = idx;
        Array.prototype.forEach.call(strip.children, function (b) {
          b.classList.toggle("is-active", b === thumb);
        });
      });
      strip.appendChild(thumb);
    });
    wrap.appendChild(strip);
    return wrap;
  }

  // -- lightbox ----------------------------------------------------------

  // Which shot the sheet is currently showing, so the lightbox opens on
  // the one you clicked rather than always on the first.
  var sheetShotIndex = 0;
  var lightbox = null;

  function openLightbox(item, shots, startIndex) {
    closeLightbox();
    var idx = Math.min(Math.max(startIndex || 0, 0), shots.length - 1);
    var overlay = el("div", { class: "cat-lightbox" });
    var img = el("img", { src: shots[idx], alt: item.name + " preview" });
    overlay.appendChild(img);

    var caption = el("div", { class: "cat-lightbox-caption" });
    function paint() {
      img.src = shots[idx];
      caption.textContent =
        shots.length > 1 ? item.name + "  ·  " + (idx + 1) + " / " + shots.length : item.name;
    }
    function step(delta) {
      idx = (idx + delta + shots.length) % shots.length;
      paint();
    }
    if (shots.length > 1) {
      [
        ["is-prev", "ph-caret-left", -1, "Previous screenshot"],
        ["is-next", "ph-caret-right", 1, "Next screenshot"],
      ].forEach(function (spec) {
        var btn = el(
          "button",
          { type: "button", class: "cat-lightbox-nav " + spec[0], "aria-label": spec[3] },
          [icon(spec[1])]
        );
        btn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          step(spec[2]);
        });
        overlay.appendChild(btn);
      });
    }
    var close = el(
      "button",
      { type: "button", class: "cat-lightbox-close", "aria-label": "Close preview" },
      [icon("ph-x")]
    );
    close.addEventListener("click", closeLightbox);
    overlay.appendChild(close);
    overlay.appendChild(caption);
    paint();

    // Click anywhere outside the image closes; the image itself doesn't,
    // so dragging or right-clicking to save it stays possible.
    overlay.addEventListener("click", function (ev) {
      if (ev.target === overlay) closeLightbox();
    });
    img.addEventListener("click", function (ev) {
      ev.stopPropagation();
    });

    lightbox = { el: overlay, step: step };
    document.body.appendChild(overlay);
    close.focus();
  }

  function closeLightbox() {
    if (!lightbox) return;
    lightbox.el.remove();
    lightbox = null;
    if (sheetPanel && state.selectedId) sheetPanel.focus();
  }

  function actionLabel(item) {
    if (state.pending[item.id]) {
      return state.pending[item.id] === "install" ? "Working…" : "Removing…";
    }
    if (item.kind === "template") return "View & install";
    if (item.updateAvailable) return "Update to v" + item.version;
    return item.installed ? "Uninstall" : "Install";
  }

  function actionClass(item) {
    if (item.kind === "template") return "cat-btn cat-btn--primary";
    if (item.updateAvailable) return "cat-btn cat-btn--primary";
    return item.installed ? "cat-btn cat-btn--danger" : "cat-btn cat-btn--primary";
  }

  function runAction(item, btn) {
    if (state.pending[item.id]) return;
    if (item.kind === "template") {
      if (typeof window.tesseraeTemplateInstallModal === "function") {
        window.tesseraeTemplateInstallModal(item.raw);
      }
      return;
    }
    if (item.installed && !item.updateAvailable) {
      uninstall(item, btn, deleteDataChecked());
    } else {
      install(item, btn);
    }
  }

  /* The sheet's "also delete data" opt-in, when the sheet is the thing
     the click came from. A row-button uninstall always keeps the data
     directory: there's nowhere on a dense row to say otherwise. */
  function deleteDataChecked() {
    if (!state.selectedId || !sheetPanel) return false;
    var box = sheetPanel.querySelector("[data-delete-data]");
    return !!(box && box.checked);
  }

  // -- rendering ---------------------------------------------------------

  function renderRail(tokens) {
    // Type counts are live: each row shows what you'd get if you picked
    // it, with every OTHER filter still applied. That's what makes the
    // rail informative rather than decorative.
    elTypes.textContent = "";
    typeRows().forEach(function (t) {
      var active = state.type === t;
      var count = visible(t, tokens).length;
      var row = el(
        "button",
        {
          type: "button",
          class: "cat-type" + (active ? " is-active" : ""),
          "aria-pressed": active ? "true" : "false",
        },
        [
          el("span", { text: t }),
          el("span", { class: "cat-mono cat-type-count", text: String(count) }),
        ]
      );
      row.addEventListener("click", function () {
        state.type = t;
        render();
      });
      elTypes.appendChild(row);
    });

    elChips.textContent = "";
    ["All"].concat(CATS).forEach(function (c) {
      var active = state.cat === c;
      var chip = el("button", {
        type: "button",
        class: "cat-chip" + (active ? " is-active" : ""),
        "aria-pressed": active ? "true" : "false",
        text: c,
      });
      chip.addEventListener("click", function () {
        state.cat = c;
        render();
      });
      elChips.appendChild(chip);
    });
    elClearCat.hidden = state.cat === "All";

    Array.prototype.forEach.call(elStatus.querySelectorAll("[data-status]"), function (b) {
      var active = b.getAttribute("data-status") === state.status;
      b.classList.toggle("is-active", active);
      b.setAttribute("aria-pressed", active ? "true" : "false");
    });

    var showFit = payload.templates_enabled && MY_RES.length > 0;
    elFitSection.hidden = !showFit;
    if (showFit) {
      elFits.checked = state.fitsOnly;
      // An install with a shelf full of panels can carry a dozen
      // resolutions; the rail lists a few and keeps the rest in the
      // title rather than growing to the height of the chip set.
      var shown = MY_RES.slice(0, 5).map(function (r) {
        return r.split("x").join("×");
      });
      elMyRes.textContent =
        shown.join(" · ") +
        (MY_RES.length > shown.length ? " +" + (MY_RES.length - shown.length) : "");
      elMyRes.title = MY_RES.join(", ");
    }
  }

  function renderRow(item, tokens) {
    var row = el("div", {
      class: "cat-row" + (item.installed ? " is-installed" : ""),
      role: "button",
      tabindex: "0",
      "aria-label": item.name + ", open details",
    });
    row.appendChild(previewFrame(item, "row"));

    var text = el("div", { class: "cat-row-text" });
    var line1 = el("div", { class: "cat-row-title" });
    var name = el("span", { class: "cat-row-name" });
    name.appendChild(highlighted(item.name, tokens));
    line1.appendChild(name);
    if (item.verified) {
      line1.appendChild(
        el(
          "span",
          {
            class: "cat-verified",
            title: "Reviewed and maintained by the catalog owner",
          },
          [icon("ph-seal-check"), el("span", { text: "verified" })]
        )
      );
    }
    line1.appendChild(el("span", { class: "cat-kind", text: item.kind }));
    if (item.updateAvailable) {
      line1.appendChild(el("span", { class: "cat-flag", text: "update available" }));
    }
    text.appendChild(line1);

    var desc = el("div", { class: "cat-row-desc" });
    desc.appendChild(highlighted(item.desc, tokens));
    text.appendChild(desc);

    var meta = el("div", { class: "cat-mono cat-row-meta" });
    if (item.version) meta.appendChild(el("span", { text: "v" + item.version }));
    meta.appendChild(el("span", { text: "by " + item.author }));
    if (item.installs) {
      meta.appendChild(
        el("span", { title: "Installs reported by opted-in Tesserae installs" }, [
          icon("ph-download-simple"),
          el("span", { text: fmtInstalls(item.installs) }),
        ])
      );
    }
    if (item.stars) {
      meta.appendChild(
        el("span", { title: "GitHub stars on the source repo" }, [
          icon("ph-star"),
          el("span", { text: String(item.stars) }),
        ])
      );
    }
    meta.appendChild(
      el("span", {
        text: item.kind === "template" ? item.res : plural(item.folders.length, "folder"),
      })
    );
    text.appendChild(meta);
    row.appendChild(text);

    var actions = el("div", { class: "cat-row-actions" });
    if (item.installedFromDisk) {
      actions.appendChild(
        el("span", {
          class: "cat-installed",
          title:
            "Plugin folders are on disk but weren't installed from the catalog, " +
            "typically an upgrade where the widget moved out of the bundle.",
          text: "on disk",
        })
      );
    } else if (item.installed) {
      actions.appendChild(
        el("span", { class: "cat-installed" }, [
          icon("ph-check"),
          el("span", { text: "installed" }),
        ])
      );
    }
    var btn = el("button", {
      type: "button",
      class: actionClass(item),
      text: actionLabel(item),
      disabled: state.pending[item.id] ? "disabled" : null,
    });
    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      runAction(item, btn);
    });
    actions.appendChild(btn);
    row.appendChild(actions);

    row.addEventListener("click", function () {
      openSheet(item.id);
    });
    row.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        openSheet(item.id);
      }
    });
    return row;
  }

  function renderResults(groups, tokens) {
    elResults.textContent = "";
    elResults.setAttribute("aria-busy", "false");

    if (!groups.length || !groups[0].items.length) {
      elResults.appendChild(emptyState());
      return;
    }
    groups.forEach(function (group) {
      var wrap = el("div", { class: "cat-group" });
      if (group.label) {
        var head = el("div", { class: "cat-group-head" }, [
          el("span", { class: "cat-mono cat-group-res", text: group.label }),
        ]);
        if (group.mine) head.appendChild(el("span", { class: "cat-badge", text: "your device" }));
        head.appendChild(el("span", { class: "cat-group-sub", text: group.sub }));
        wrap.appendChild(head);
      }
      var list = el("div", { class: "cat-rows" });
      group.items.forEach(function (item) {
        list.appendChild(renderRow(item, tokens));
      });
      wrap.appendChild(list);
      elResults.appendChild(wrap);
    });

    if (state.type === "Templates" && !templatesLoaded) {
      elResults.appendChild(el("div", { class: "cat-loading", text: "Loading templates…" }));
    }
  }

  function emptyState() {
    var wrap = el("div", { class: "cat-empty" });
    if (state.type === "Templates" && !payload.templates_online) {
      wrap.appendChild(
        el("div", { class: "cat-empty-title", text: "Templates need online features" })
      );
      wrap.appendChild(
        el("div", {
          class: "cat-empty-body",
          text:
            "Community templates are served from api.tesserae.ink, so they need " +
            "Online features switched on. Widgets and themes come from GitHub and " +
            "work either way.",
        })
      );
      wrap.appendChild(
        el("a", {
          class: "cat-btn",
          href: root.getAttribute("data-online-settings-url"),
          text: "Enable online features",
        })
      );
      return wrap;
    }
    if (state.type === "Templates" && templatesError) {
      wrap.appendChild(
        el("div", { class: "cat-empty-title", text: "Template catalog unavailable" })
      );
      wrap.appendChild(el("div", { class: "cat-empty-body", text: templatesError }));
      return wrap;
    }
    if (state.type === "Templates" && !templatesLoaded) {
      wrap.appendChild(el("div", { class: "cat-empty-body", text: "Loading templates…" }));
      return wrap;
    }
    wrap.appendChild(
      el("div", { class: "cat-empty-title", text: "Nothing matches those filters" })
    );
    wrap.appendChild(
      el("div", {
        class: "cat-empty-body",
        text: "Try clearing the category or widening the status filter.",
      })
    );
    var reset = el("button", {
      type: "button",
      class: "cat-btn cat-btn--quiet",
      text: "Reset filters",
    });
    reset.addEventListener("click", function () {
      state.type = "All";
      state.status = "All";
      state.cat = "All";
      state.q = "";
      state.fitsOnly = false;
      elSearch.value = "";
      render();
    });
    wrap.appendChild(reset);
    return wrap;
  }

  function render() {
    var tokens = tokenize(state.q);
    var vis = sortItems(visible(state.type, tokens), tokens);
    renderRail(tokens);
    elCount.textContent = vis.length + " shown";
    elSortLabel.textContent = sortLabel();
    renderResults(groupsFor(vis), tokens);
    if (state.selectedId) renderSheet();
    syncUrl();
  }

  function sortLabel() {
    for (var i = 0; i < SORTS.length; i++) {
      if (SORTS[i].key === state.sort) return SORTS[i].label;
    }
    return SORTS[0].label;
  }

  // -- detail sheet ------------------------------------------------------

  function byId(id) {
    for (var i = 0; i < items.length; i++) {
      if (items[i].id === id) return items[i];
    }
    return null;
  }

  var lastFocus = null;

  function openSheet(id) {
    lastFocus = document.activeElement;
    state.selectedId = id;
    sheetShotIndex = 0;
    renderSheet();
    sheetRoot.hidden = false;
    // Next frame so the transition has a "closed" state to run from.
    requestAnimationFrame(function () {
      sheetRoot.classList.add("is-open");
    });
    sheetPanel.focus();
  }

  function closeSheet() {
    if (!state.selectedId) return;
    closeLightbox();
    state.selectedId = null;
    sheetRoot.classList.remove("is-open");
    sheetRoot.hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function detailRows(item) {
    var rows = [
      ["Type", item.kind],
      ["Category", item.cats.length ? item.cats.join(", ") : "—"],
    ];
    if (item.kind === "template") {
      rows.push(["Resolution", item.res]);
      rows.push(["Uses widgets", item.folders.length ? item.folders.join("  ") : "none"]);
      rows.push(["Needs config", item.inputs ? "yes — " + plural(item.inputs, "question") : "no"]);
    } else {
      rows.push(["Panel", "any"]);
      rows.push(["Plugin folders", item.folders.join("  ")]);
    }
    if (item.installedVersion) rows.push(["Installed", "v" + item.installedVersion]);
    if (item.installs) rows.push(["Installs", fmtInstalls(item.installs)]);
    if (item.stars) rows.push(["Stars", String(item.stars)]);
    return rows;
  }

  function renderSheet() {
    var item = byId(state.selectedId);
    if (!item || !sheetPanel) return;
    sheetPanel.textContent = "";

    var head = el("div", { class: "cat-sheet-head" });
    var headText = el("div", { class: "cat-sheet-head-text" });
    var title = el("h2", { class: "cat-sheet-title", id: "cat-sheet-title", text: item.name });
    var titleRow = el("div", { class: "cat-sheet-title-row" }, [title]);
    if (item.verified) {
      titleRow.appendChild(
        el("span", { class: "cat-verified" }, [
          icon("ph-seal-check"),
          el("span", { text: "verified" }),
        ])
      );
    }
    headText.appendChild(titleRow);
    var meta = el("div", { class: "cat-mono cat-sheet-meta" });
    if (item.version) meta.appendChild(el("span", { text: "v" + item.version }));
    meta.appendChild(el("span", { text: "by " + item.author }));
    if (item.installs) {
      meta.appendChild(
        el("span", {}, [
          icon("ph-download-simple"),
          el("span", { text: fmtInstalls(item.installs) }),
        ])
      );
    }
    headText.appendChild(meta);
    head.appendChild(headText);
    var close = el("button", { type: "button", class: "cat-sheet-close", "aria-label": "Close" }, [
      icon("ph-x"),
    ]);
    close.addEventListener("click", closeSheet);
    head.appendChild(close);
    sheetPanel.appendChild(head);

    sheetPanel.appendChild(sheetPreview(item));

    if (item.kind === "template" && item.missing && item.missing.length) {
      var note = el("div", { class: "cat-sheet-note" });
      note.appendChild(el("div", { text: "Needs these catalog items installed first:" }));
      var links = el("div", { class: "cat-sheet-note-links" });
      item.missing.forEach(function (id) {
        var a = el("a", { href: PREFIX + "/plugins/browse?q=" + encodeURIComponent(id), text: id });
        links.appendChild(a);
      });
      note.appendChild(links);
      sheetPanel.appendChild(note);
    }

    sheetPanel.appendChild(
      el("p", { class: "cat-sheet-desc", text: item.desc || "No description provided." })
    );

    var details = el("div", { class: "cat-sheet-details" }, [
      el("div", { class: "cat-rail-label", text: "Details" }),
    ]);
    detailRows(item).forEach(function (pair) {
      details.appendChild(
        el("div", { class: "cat-detail-row" }, [
          el("span", { class: "cat-detail-key", text: pair[0] }),
          el("span", { class: "cat-mono cat-detail-val", text: pair[1] }),
        ])
      );
    });
    sheetPanel.appendChild(details);

    // Uninstall keeps the plugin's data directory unless this is ticked,
    // same default the form-post page had. Rendered above the buttons so
    // the choice is visible before the click, not after it.
    if (item.installed && item.kind !== "template") {
      sheetPanel.appendChild(
        el("label", { class: "cat-check cat-check--sheet" }, [
          el("input", { type: "checkbox", "data-delete-data": "" }),
          el("span", { text: "Also delete this widget's data directory when uninstalling" }),
        ])
      );
    }

    var actions = el("div", { class: "cat-sheet-actions" });
    var primary = el("button", {
      type: "button",
      class: actionClass(item) + " cat-btn--wide",
      text: actionLabel(item),
      disabled: state.pending[item.id] ? "disabled" : null,
    });
    primary.addEventListener("click", function () {
      runAction(item, primary);
    });
    actions.appendChild(primary);
    // An update-available entry's primary button updates, so uninstall
    // needs its own; when the primary IS Uninstall, one is enough.
    if (item.updateAvailable) {
      var remove = el("button", {
        type: "button",
        class: "cat-btn cat-btn--danger",
        text: "Uninstall",
      });
      remove.addEventListener("click", function () {
        uninstall(item, remove, deleteDataChecked());
      });
      actions.appendChild(remove);
    }
    var src = sourceLink(item);
    if (src) actions.appendChild(src);
    sheetPanel.appendChild(actions);
    if (item.kind === "template") {
      sheetPanel.appendChild(
        el("p", {
          class: "cat-sheet-foot",
          text: "Installing a template creates a new dashboard page you can edit and bind to a device.",
        })
      );
    }
  }

  function sourceLink(item) {
    if (!item.source) return null;
    return el("a", {
      class: "cat-btn cat-btn--quiet",
      href: item.source,
      target: "_blank",
      rel: "noopener",
      text: "Source",
    });
  }

  // -- mutations ---------------------------------------------------------

  function post(url, body) {
    return fetch(PREFIX + url, {
      method: "POST",
      headers: { "X-Requested-With": "tesserae-fetch" },
      body: body,
    }).then(function (resp) {
      return resp
        .json()
        .catch(function () {
          return { ok: false, message: "Unexpected response from the server." };
        })
        .then(function (json) {
          return { ok: resp.ok && json.ok !== false, body: json };
        });
    });
  }

  function install(item, btn) {
    state.pending[item.id] = "install";
    btn.disabled = true;
    btn.textContent = "Installing…";
    var form = new FormData();
    form.append("catalog_id", item.id);
    post(root.getAttribute("data-install-url"), form)
      .then(function (r) {
        delete state.pending[item.id];
        if (!r.ok) {
          flash(r.body.message || "Install failed.", "error");
          render();
          return;
        }
        item.installed = true;
        item.updateAvailable = false;
        item.installedVersion = r.body.version || item.version;
        item.installs += 1;
        flash(r.body.message, "ok");
        showRestartAffordance();
        render();
      })
      .catch(function () {
        delete state.pending[item.id];
        flash("Install failed: network error.", "error");
        render();
      });
    render();
  }

  function uninstall(item, btn, deleteData) {
    if (
      !window.confirm(
        "Uninstall " +
          item.name +
          "?" +
          (deleteData ? " Its data directory will be deleted too." : " Its data directory is kept.")
      )
    ) {
      return;
    }
    state.pending[item.id] = "uninstall";
    btn.disabled = true;
    btn.textContent = "Removing…";
    var form = new FormData();
    form.append("catalog_id", item.id);
    if (deleteData) form.append("delete_data", "1");
    post(root.getAttribute("data-uninstall-url"), form)
      .then(function (r) {
        delete state.pending[item.id];
        if (!r.ok) {
          flash(r.body.message || "Uninstall failed.", "error");
          render();
          return;
        }
        item.installed = false;
        item.installedFromDisk = false;
        item.installedVersion = null;
        item.updateAvailable = false;
        flash(r.body.message, "ok");
        showRestartAffordance();
        render();
      })
      .catch(function () {
        delete state.pending[item.id];
        flash("Uninstall failed: network error.", "error");
        render();
      });
    render();
  }

  /* Installs only take effect on a process restart, so the topbar's
     "Restart required" button is the end of every install flow. It's
     rendered server-side when the flag is already set; light it up here
     for the first install of this page view. */
  function showRestartAffordance() {
    if (document.querySelector(".topbar-restart-form")) return;
    var bar = document.querySelector(".topbar");
    var toggle = bar ? bar.querySelector("[data-theme-toggle]") : null;
    if (!bar || !toggle) return;
    var form = el(
      "form",
      {
        method: "post",
        action: PREFIX + "/plugins/browse/restart",
        class: "inline topbar-restart-form",
        "data-restart-form": "",
        "data-restart-label": "Restarting Tesserae",
      },
      [
        el(
          "button",
          {
            type: "submit",
            class: "topbar-restart",
            title: "Restart Tesserae to load installed/uninstalled widgets",
          },
          [
            el("i", { class: "ph-bold ph-arrow-clockwise", "aria-hidden": "true" }),
            el("span", { class: "topbar-restart-label", text: "Restart required" }),
          ]
        ),
      ]
    );
    bar.insertBefore(form, toggle);
    if (typeof window.tesseraeBindRestartForms === "function") {
      window.tesseraeBindRestartForms();
    }
  }

  function flash(message, kind) {
    if (!message) return;
    var host = document.querySelector(".flashes");
    if (!host) {
      host = el("div", { class: "flashes", role: "status", "aria-live": "polite" });
      document.body.appendChild(host);
    }
    var card = el("div", { class: "flash flash--" + (kind || "ok") }, [
      el("i", {
        class: "ph-fill " + (kind === "error" ? "ph-x-circle" : "ph-check-circle") + " flash-icon",
        "aria-hidden": "true",
      }),
      el("span", { class: "flash-msg", text: message }),
    ]);
    var close = el("button", { type: "button", class: "flash-close", "aria-label": "Dismiss" }, [
      icon("ph-x"),
    ]);
    function dismiss() {
      if (card.classList.contains("is-leaving")) return;
      card.classList.add("is-leaving");
      card.addEventListener(
        "transitionend",
        function () {
          card.remove();
        },
        { once: true }
      );
      setTimeout(function () {
        card.remove();
      }, 600);
    }
    close.addEventListener("click", dismiss);
    card.appendChild(close);
    host.appendChild(card);
    setTimeout(dismiss, 7000);
  }

  // -- URL state ---------------------------------------------------------

  var urlTimer = null;
  function syncUrl() {
    if (urlTimer) clearTimeout(urlTimer);
    urlTimer = setTimeout(function () {
      var params = new URLSearchParams();
      if (state.type !== "All") params.set("type", state.type);
      if (state.status !== "All") params.set("status", state.status);
      if (state.cat !== "All") params.set("tag", state.cat);
      if (state.q) params.set("q", state.q);
      var qs = params.toString();
      history.replaceState(null, "", qs ? "?" + qs : location.pathname);
    }, 250);
  }

  // -- templates ---------------------------------------------------------

  function loadTemplates() {
    var url = root.getAttribute("data-templates-url");
    if (!url || !payload.templates_enabled) {
      templatesLoaded = true;
      return;
    }
    if (!payload.templates_online) {
      templatesLoaded = true;
      templatesError = null;
      return;
    }
    fetch(PREFIX + url, { headers: { "X-Requested-With": "tesserae-fetch" } })
      .then(function (resp) {
        return resp.json().then(function (body) {
          return { ok: resp.ok, body: body };
        });
      })
      .then(function (r) {
        templatesLoaded = true;
        if (!r.ok) {
          templatesError = r.body.error || "Template catalog unreachable right now.";
          render();
          return;
        }
        items = items.concat((r.body.templates || []).map(fromTemplateEntry));
        render();
      })
      .catch(function () {
        templatesLoaded = true;
        templatesError = "Template catalog unreachable right now.";
        render();
      });
  }

  // -- wiring ------------------------------------------------------------

  // Debounced so a fast typist gets one render per pause rather than one
  // per keystroke; the input itself is never re-rendered, so focus and
  // caret position are never touched.
  var searchTimer = null;
  elSearch.addEventListener("input", function () {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.q = elSearch.value;
      render();
    }, 90);
  });
  elSearchForm.addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (searchTimer) clearTimeout(searchTimer);
    state.q = elSearch.value;
    render();
  });
  elSearch.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && elSearch.value) {
      ev.stopPropagation();
      elSearch.value = "";
      state.q = "";
      render();
    }
  });

  elStatus.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-status]");
    if (!btn) return;
    state.status = btn.getAttribute("data-status");
    render();
  });

  elClearCat.addEventListener("click", function () {
    state.cat = "All";
    render();
  });

  elFits.addEventListener("change", function () {
    state.fitsOnly = elFits.checked;
    render();
  });

  elSort.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-sort]");
    if (!btn) return;
    state.sort = btn.getAttribute("data-sort");
    Array.prototype.forEach.call(elSort.querySelectorAll("[data-sort]"), function (b) {
      b.setAttribute("aria-checked", b === btn ? "true" : "false");
    });
    elSort.open = false;
    render();
  });
  document.addEventListener("click", function (ev) {
    if (elSort.open && !elSort.contains(ev.target)) elSort.open = false;
  });

  sheetRoot.addEventListener("click", function (ev) {
    if (ev.target.hasAttribute("data-cat-sheet-close")) closeSheet();
  });

  document.addEventListener("keydown", function (ev) {
    // The lightbox is the topmost layer, so it takes Escape and the
    // arrows before the sheet sees them.
    if (lightbox) {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        closeLightbox();
      } else if (ev.key === "ArrowLeft") {
        lightbox.step(-1);
      } else if (ev.key === "ArrowRight") {
        lightbox.step(1);
      }
      return;
    }
    if (ev.key === "Escape") {
      if (state.selectedId) {
        closeSheet();
        return;
      }
      if (elSort.open) elSort.open = false;
      return;
    }
    if (ev.key === "Tab" && state.selectedId) {
      trapFocus(ev);
      return;
    }
    // "/" jumps to search the way it does in most catalogs, as long as
    // the user isn't already typing somewhere.
    var tag = (document.activeElement && document.activeElement.tagName) || "";
    if (ev.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA" && !ev.metaKey && !ev.ctrlKey) {
      ev.preventDefault();
      elSearch.focus();
      elSearch.select();
    }
  });

  function trapFocus(ev) {
    var focusables = sheetPanel.querySelectorAll(
      "a[href], button:not([disabled]), input, [tabindex]:not([tabindex='-1'])"
    );
    if (!focusables.length) return;
    var first = focusables[0];
    var last = focusables[focusables.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault();
      first.focus();
    }
  }

  render();
  loadTemplates();
})();
