/*
 * Shared touch Interaction editor (issue #49).
 *
 * A framework-free control that renders the tap / swipe / slider action
 * pickers (plus the Home Assistant service form) used by BOTH the canvas
 * editor (static/panels/editor.js) and the grid editor
 * (static/pages/editor.js), so the two never drift.
 *
 *   const node = TouchInteraction.render({
 *     value:        { on_tap, on_swipe, on_slide },   // current values (any may be null)
 *     pagesUrl:     "…/dashboards.json",              // for the "go to page" picker
 *     haActionsUrl: "…/ha-actions.json",              // for the HA form
 *     allowSlide:   true,                             // show the "make a slider" control
 *     onChange:     (value) => { … },                 // fires on every edit
 *   });
 *
 * ``value`` is normalised on the way out: empty tap -> null, empty swipe
 * map -> null, actionless slide -> null. Action specs are the
 * button_actions grammar strings ("page:<id>", "webhook:<url>", …) or a
 * structured Home Assistant object {action:"ha", domain, service, data}.
 */
(function () {
  "use strict";

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  var _seq = 0;
  function uid() { return "ti" + ++_seq; }

  var SWIPE_DIRS = ["up", "down", "left", "right"];
  var ACTIONS = [
    { id: "", label: "None" },
    { id: "refresh", label: "Refresh" },
    { id: "rotate_next", label: "Next in rotation" },
    { id: "rotate_prev", label: "Previous in rotation" },
    { id: "step", label: "Jump to step…", arg: "number" },
    { id: "page", label: "Go to page…", arg: "page" },
    { id: "webhook", label: "Webhook…", arg: "url" },
    { id: "ha", label: "Home Assistant…", arg: "ha" },
  ];

  // Per-URL fetch caches shared across every editor instance on the page.
  var _cache = {};
  function fetchJSON(url, cb) {
    if (!url) { cb(null); return; }
    if (_cache[url]) { cb(_cache[url]); return; }
    fetch(url)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (j) { _cache[url] = j; cb(j); })
      .catch(function () { cb(null); });
  }

  function specParts(spec) {
    if (spec && typeof spec === "object") return { action: String(spec.action || ""), arg: "" };
    var s = typeof spec === "string" ? spec : "";
    var i = s.indexOf(":");
    if (i < 0) return { action: s, arg: "" };
    return { action: s.slice(0, i), arg: s.slice(i + 1) };
  }
  function specJoin(action, arg) {
    if (!action) return "";
    var def = ACTIONS.filter(function (a) { return a.id === action; })[0];
    if (def && def.arg) return arg ? action + ":" + arg : "";
    return action;
  }

  // One action picker: a <select> of the vocabulary plus a conditional
  // argument control (page list / webhook URL / step number / HA form).
  // Calls onChange with the resolved spec (string, HA object, or "").
  // ``exclude`` drops action ids (swipes exclude "ha").
  function actionControl(spec, urls, onChange, exclude) {
    exclude = exclude || [];
    var wrap = el("span");
    wrap.style.cssText = "display:flex;flex-direction:column;gap:4px;flex:1;min-width:0";
    var parts = specParts(spec);
    var sel = el("select", "psel");
    sel.innerHTML = ACTIONS.filter(function (a) { return exclude.indexOf(a.id) < 0; })
      .map(function (a) { return '<option value="' + a.id + '">' + esc(a.label) + "</option>"; })
      .join("");
    if (!ACTIONS.some(function (a) { return a.id === parts.action; })) {
      sel.innerHTML += '<option value="__custom">Custom: ' + esc(String(spec).slice(0, 28)) + "</option>";
      sel.value = "__custom";
    } else {
      sel.value = parts.action;
    }
    wrap.appendChild(sel);
    var argHost = el("span");
    argHost.style.display = "block";
    wrap.appendChild(argHost);

    function renderHaForm() {
      var cur = spec && typeof spec === "object" && spec.action === "ha" ? spec : {};
      var data = cur.data || {};
      var svcVal = cur.domain && cur.service ? cur.domain + "." + cur.service : "";
      var entVal = data.entity_id || "";
      var extra = {};
      Object.keys(data).forEach(function (k) { if (k !== "entity_id") extra[k] = data[k]; });
      var sid = uid(), eid = uid();
      var svc = el("input", "dinput");
      svc.setAttribute("list", sid);
      svc.placeholder = "light.turn_on";
      svc.value = svcVal;
      svc.style.cssText = "width:100%;text-align:left;font:12px var(--t-font-mono);margin-bottom:4px";
      var slist = el("datalist"); slist.id = sid;
      var ent = el("input", "dinput");
      ent.setAttribute("list", eid);
      ent.placeholder = "light.lounge (entity)";
      ent.value = entVal;
      ent.style.cssText = "width:100%;text-align:left;font:12px var(--t-font-mono);margin-bottom:4px";
      var elist = el("datalist"); elist.id = eid;
      var extraTa = el("textarea", "dinput");
      extraTa.rows = 2;
      extraTa.placeholder = '{"brightness_pct": "{value}"}';
      extraTa.value = Object.keys(extra).length ? JSON.stringify(extra) : "";
      extraTa.style.cssText = "width:100%;font:11px var(--t-font-mono);resize:vertical";
      extraTa.title = 'Optional service data (JSON). On a slider, "{value}" becomes the 0-100 stroke value.';
      var hint = el("div", "note");
      function commitHa() {
        var m = svc.value.trim().match(/^([a-z0-9_]+)\.([a-z0-9_]+)$/i);
        if (!m) return;
        var payload = {};
        var raw = extraTa.value.trim();
        if (raw) {
          try { payload = JSON.parse(raw) || {}; }
          catch (err) { hint.textContent = "Extra data is not valid JSON."; return; }
        }
        hint.textContent = "";
        if (ent.value.trim()) payload.entity_id = ent.value.trim();
        onChange({ action: "ha", domain: m[1], service: m[2], data: payload });
      }
      [svc, ent, extraTa].forEach(function (inp) {
        inp.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
        inp.addEventListener("change", commitHa);
      });
      argHost.appendChild(svc); argHost.appendChild(slist);
      argHost.appendChild(ent); argHost.appendChild(elist);
      argHost.appendChild(extraTa); argHost.appendChild(hint);
      fetchJSON(urls.haActionsUrl, function (ha) {
        if (!ha || !ha.configured) {
          hint.textContent = "Home Assistant isn't configured (Settings → Plugins → Home Assistant Core).";
          return;
        }
        slist.innerHTML = (ha.services || []).map(function (s) {
          return '<option value="' + esc(s.id) + '">' + esc(s.name || "") + "</option>";
        }).join("");
        elist.innerHTML = (ha.entities || []).map(function (x) {
          return '<option value="' + esc(x.id) + '">' + esc(x.name || "") + "</option>";
        }).join("");
      });
    }

    function renderArg() {
      argHost.textContent = "";
      var def = ACTIONS.filter(function (a) { return a.id === sel.value; })[0];
      if (!def || !def.arg) return;
      if (def.arg === "ha") { renderHaForm(); return; }
      if (def.arg === "page") {
        var psel = el("select", "psel");
        psel.innerHTML = '<option value="">Choose page…</option>';
        argHost.appendChild(psel);
        fetchJSON(urls.pagesUrl, function (j) {
          var pages = (j && j.pages) || [];
          psel.innerHTML = '<option value="">Choose page…</option>' + pages.map(function (p) {
            return '<option value="' + esc(p.id) + '">' + esc(p.name) + "</option>";
          }).join("");
          if (parts.action === "page") psel.value = parts.arg;
        });
        psel.addEventListener("change", function () { commit(psel.value); });
      } else {
        var inp = el("input", "dinput");
        inp.style.cssText = "width:100%;text-align:left";
        if (def.arg === "number") { inp.type = "number"; inp.min = "0"; inp.placeholder = "step #"; }
        else { inp.placeholder = "https://…"; }
        if (parts.action === sel.value) inp.value = parts.arg;
        inp.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
        inp.addEventListener("change", function () { commit(inp.value.trim()); });
        argHost.appendChild(inp);
      }
    }
    function commit(arg) {
      if (sel.value === "__custom") return;
      onChange(specJoin(sel.value, arg));
    }
    sel.addEventListener("change", function () {
      parts = { action: sel.value, arg: "" };
      renderArg();
      var def = ACTIONS.filter(function (a) { return a.id === sel.value; })[0];
      if (!def || !def.arg) commit("");
    });
    renderArg();
    return wrap;
  }

  function render(opts) {
    opts = opts || {};
    var urls = { pagesUrl: opts.pagesUrl, haActionsUrl: opts.haActionsUrl };
    var allowSlide = opts.allowSlide !== false;
    var v = opts.value || {};
    var on_tap = v.on_tap != null ? v.on_tap : "";
    var on_swipe = v.on_swipe ? JSON.parse(JSON.stringify(v.on_swipe)) : null;
    var on_slide = v.on_slide ? JSON.parse(JSON.stringify(v.on_slide)) : null;
    var root = el("div", "ti-root");

    function emit() {
      if (!opts.onChange) return;
      opts.onChange({
        on_tap: on_tap || null,
        on_swipe: on_swipe && Object.keys(on_swipe).length ? on_swipe : null,
        on_slide: on_slide && on_slide.action ? on_slide : null,
      });
    }
    function rebuild() { root.textContent = ""; build(); }

    function build() {
      // On tap
      var trow = el("div", "prow"); trow.innerHTML = '<span class="plab">On tap</span>';
      trow.appendChild(actionControl(on_tap, urls, function (spec) {
        on_tap = spec || "";
        emit();
      }));
      root.appendChild(trow);

      // Swipe rows
      var swipe = on_swipe || {};
      SWIPE_DIRS.forEach(function (dir) {
        if (!(dir in swipe)) return;
        var row = el("div", "prow");
        row.innerHTML = '<span class="plab">Swipe ' + dir + "</span>";
        var box = el("span");
        box.style.cssText = "display:flex;gap:4px;flex:1;min-width:0;align-items:flex-start";
        box.appendChild(actionControl(swipe[dir] || "", urls, function (spec) {
          on_swipe = on_swipe || {};
          if (spec) on_swipe[dir] = spec; else delete on_swipe[dir];
          if (!Object.keys(on_swipe).length) on_swipe = null;
          emit();
        }, ["ha"]));
        var rm = el("button", "minibtn", '<i class="ph-bold ph-x"></i>');
        rm.title = "Remove swipe action";
        rm.addEventListener("click", function () {
          if (on_swipe) delete on_swipe[dir];
          if (on_swipe && !Object.keys(on_swipe).length) on_swipe = null;
          emit(); rebuild();
        });
        box.appendChild(rm);
        row.appendChild(box);
        root.appendChild(row);
      });
      var free = SWIPE_DIRS.filter(function (d) { return !(d in (on_swipe || {})); });
      if (free.length) {
        var addrow = el("div", "prow");
        var addsel = el("select", "psel");
        addsel.innerHTML = '<option value="">+ Add swipe action…</option>' + free.map(function (d) {
          return '<option value="' + d + '">Swipe ' + d + "</option>";
        }).join("");
        addsel.addEventListener("change", function () {
          if (!addsel.value) return;
          on_swipe = on_swipe || {};
          on_swipe[addsel.value] = "";
          rebuild();
        });
        addrow.appendChild(addsel); root.appendChild(addrow);
      }

      // Slider
      if (!allowSlide) return;
      if (on_slide) {
        var srow = el("div", "prow"); srow.innerHTML = '<span class="plab">Slider</span>';
        var sbox = el("span");
        sbox.style.cssText = "display:flex;flex-direction:column;gap:4px;flex:1;min-width:0";
        var axrow = el("span"); axrow.style.cssText = "display:flex;gap:4px;align-items:center";
        var ax = el("select", "psel");
        ax.innerHTML =
          '<option value="y">Vertical (top = 100)</option><option value="x">Horizontal (right = 100)</option>';
        ax.value = on_slide.axis === "x" ? "x" : "y";
        ax.addEventListener("change", function () { on_slide.axis = ax.value; emit(); });
        var srm = el("button", "minibtn", '<i class="ph-bold ph-x"></i>');
        srm.title = "Remove slider";
        srm.addEventListener("click", function () { on_slide = null; emit(); rebuild(); });
        axrow.appendChild(ax); axrow.appendChild(srm);
        sbox.appendChild(axrow);
        sbox.appendChild(actionControl(on_slide.action || "", urls, function (spec) {
          on_slide = on_slide || { axis: ax.value };
          on_slide.action = spec || "";
          emit();
        }));
        sbox.appendChild(el("div", "note",
          'Use "{value}" in the action for the stroke\'s 0-100 position (webhook URLs or HA data).'));
        srow.appendChild(sbox);
        root.appendChild(srow);
      } else {
        var mkrow = el("div", "prow");
        var mk = el("button", "minibtn", '<i class="ph-bold ph-sliders-horizontal"></i> Make this a slider…');
        mk.style.cssText = "width:100%;justify-content:center";
        mk.addEventListener("click", function () {
          on_slide = { axis: opts.defaultAxis === "x" ? "x" : "y", action: "" };
          rebuild();
        });
        mkrow.appendChild(mk); root.appendChild(mkrow);
      }
    }

    build();
    return root;
  }

  // ``actionControl`` is exposed for the code-element "Actions" card
  // (canvas editor), which edits a map of named single actions.
  window.TouchInteraction = {
    render: render,
    actionControl: function (spec, urls, onChange, exclude) {
      return actionControl(spec, urls || {}, onChange, exclude);
    },
  };
})();
