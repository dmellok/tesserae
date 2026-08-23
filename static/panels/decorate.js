/* Decoration primitives for the Panels canvas (issue #60).
 *
 * Renders shape / line / icon / text elements, plus two data-aware kinds:
 *   - "data": a widget data field shown as text / number / line / bar /
 *     sparkline (the resolved widget data is passed as the second arg).
 *   - "html": agent- or user-authored markup, isolated in a sandboxed iframe
 *     (no scripts, no network) so it can't touch the page it renders in.
 * Shared by the editor (live preview) and the compose page (Send / preview.png)
 * so both draw identically. A ``color`` is any CSS colour or a Spectra token
 * like "var(--accent-1)" that resolves from the artboard's data-theme. Returns
 * a node that fills its element box.
 */
(function () {
  "use strict";

  function px(v, fallback) {
    var n = Number(v);
    return (isFinite(n) ? n : fallback);
  }

  // Resolve a dotted path against a nested object/array. Grammar:
  //   a.b.c        object keys
  //   a.0.b        array index (or a[0].b)
  //   a.*.b        "pluck": map .b over every item of the array a
  //                (or a[].b), yielding an array — use this to feed charts,
  //                e.g. "series.*.total" over [{date,total},…] -> [t1,t2,…].
  function resolvePath(obj, path) {
    if (obj == null || !path) return undefined;
    var norm = String(path).replace(/\[(\d+)\]/g, ".$1").replace(/\[\s*\*?\s*\]/g, ".*");
    return _walk(obj, norm.split("."), 0);
  }
  function _walk(cur, parts, i) {
    if (i >= parts.length) return cur;
    if (cur == null) return undefined;
    var k = parts[i];
    if (k === "") return _walk(cur, parts, i + 1);
    if (k === "*") {
      if (!Array.isArray(cur)) return undefined;
      return cur.map(function (item) { return _walk(item, parts, i + 1); });
    }
    var next = Array.isArray(cur) && /^\d+$/.test(k) ? cur[Number(k)] : cur[k];
    return _walk(next, parts, i + 1);
  }

  var PRIMITIVE_KINDS = { button: 1, switch: 1, slider: 1, stepper: 1 };

  // Touch-v3 primitive chrome, drawn to primitives.json geometry so it reads
  // like the on-device control. Shared with the editor (which always draws it,
  // being a look at the design rather than a frame) so preview and composition
  // can't drift. Keep the geometry in sync with primitives.json.
  function renderPrimitive(e) {
    var ink = "var(--text-primary, #1B1A16)";
    var paper = "var(--bg, #FFFFFF)";
    var soft = "var(--surface-sunken, #E7E4DC)";
    // The box is opaque whichever way it's drawn: the reserve covers what sits
    // behind it, and so must the painted control, or the two disagree about
    // what a primitive placed over other elements looks like.
    var wrap = document.createElement("div");
    var base = "position:relative;box-sizing:border-box;width:100%;height:100%;overflow:hidden;" +
      "background:" + paper + ";color:" + ink + ";font-family:var(--font-family, inherit)";
    function span(text, css) {
      var s = document.createElement("span");
      s.textContent = text;
      if (css) s.style.cssText = css;
      return s;
    }

    if (e.kind === "button") {
      wrap.style.cssText = base + ";border:2px solid " + ink + ";border-radius:12px;" +
        "display:flex;align-items:center;justify-content:center;gap:8px;font-weight:700";
      if (e.icon) {
        var weight = e.weight || "bold";
        if (!/^(thin|light|regular|bold|fill|duotone)$/.test(weight)) weight = "bold";
        var ic = document.createElement("i");
        ic.className = (weight === "regular" ? "ph" : "ph-" + weight) +
          " ph-" + String(e.icon).replace(/^ph-/, "");
        ic.style.fontSize = "22px";
        wrap.appendChild(ic);
      }
      if (e.label) wrap.appendChild(span(e.label));
      return wrap;
    }

    if (e.kind === "switch") {
      wrap.style.cssText = base;
      var on = e.state === "on";
      var track = document.createElement("div");
      track.style.cssText = "position:absolute;right:4px;top:50%;transform:translateY(-50%);width:64px;" +
        "height:36px;border:2px solid " + ink + ";border-radius:999px;background:" + (on ? soft : paper);
      var thumb = document.createElement("div");
      thumb.style.cssText = "position:absolute;top:50%;transform:translateY(-50%);width:26px;height:26px;" +
        "border-radius:50%;background:" + ink + ";" + (on ? "right:5px" : "left:5px");
      track.appendChild(thumb);
      wrap.appendChild(track);
      if (e.label) wrap.appendChild(span(e.label,
        "position:absolute;left:4px;top:50%;transform:translateY(-50%);font-weight:700"));
      return wrap;
    }

    if (e.kind === "slider") {
      wrap.style.cssText = base;
      var range = Math.max(1, Number(e.value_max) - Number(e.value_min));
      var t = Math.max(0, Math.min(1, (Number(e.value_now) - Number(e.value_min)) / range || 0));
      var trk = document.createElement("div");
      trk.style.cssText = "position:absolute;left:20px;right:20px;top:50%;transform:translateY(-50%);" +
        "height:8px;border-radius:4px;background:" + soft;
      var fill = document.createElement("div");
      fill.style.cssText = "position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:" + ink +
        ";width:" + (t * 100) + "%";
      trk.appendChild(fill);
      var th = document.createElement("div");
      th.style.cssText = "position:absolute;top:50%;transform:translate(-50%,-50%);width:32px;height:32px;" +
        "border:2px solid " + ink + ";border-radius:50%;background:" + paper +
        ";left:calc(20px + " + t + " * (100% - 40px))";
      wrap.appendChild(trk);
      wrap.appendChild(th);
      return wrap;
    }

    // stepper: [ - | value | + ]
    wrap.style.cssText = base + ";border:2px solid " + ink + ";border-radius:12px;" +
      "display:flex;align-items:stretch";
    var cell = "flex:1;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:22px";
    wrap.appendChild(span("−", cell + ";border-right:2px solid " + soft));
    wrap.appendChild(span(String(e.value_now == null ? 0 : e.value_now),
      cell + ";border-right:2px solid " + soft));
    wrap.appendChild(span("+", cell));
    return wrap;
  }

  function render(el, data) {
    var kind = el.kind || "rect";
    var color = el.color || "var(--text-primary, #1B1A16)";

    if (kind === "hotspot") {
      // Touch hotspot (issue #49): carries data-on-tap/-swipe on its wrapper
      // but paints nothing. The editor draws its own dashed affordance.
      var hs = document.createElement("div");
      hs.style.cssText = "width:100%;height:100%";
      return hs;
    }
    if (PRIMITIVE_KINDS[kind]) {
      // Touch-v3 primitives. Who paints the control depends on who owns the
      // pixels: a panel whose firmware draws it (protocol v2, flagged by the
      // compose page) gets a blank rect filled with the canvas background,
      // covering anything behind it, and draws its own control on top. Anywhere
      // else (a display-only panel, a preview, no device at all) nothing would
      // ever fill that rect, so the server paints the control itself (#228).
      if (window.__TESSERAE_DEVICE_DRAWS_TOUCH) {
        var rsv = document.createElement("div");
        rsv.style.cssText = "width:100%;height:100%;background:var(--bg, #FFFFFF)";
        return rsv;
      }
      return renderPrimitive(el);
    }
    if (kind === "html") return renderHtml(el, false);
    if (kind === "svg") return renderHtml(el, true);
    if (kind === "code") return renderCode(el, data);
    if (kind === "data") return renderData(el, data, color);

    if (kind === "icon") {
      var i = document.createElement("i");
      // Phosphor weight class: regular is the bare "ph", others are "ph-<weight>".
      // An unknown weight would build a class matching no stylesheet (blank
      // box), so fall back to bold; render_report's icon_invalid names it.
      var weight = el.weight || "bold";
      if (!/^(thin|light|regular|bold|fill|duotone)$/.test(weight)) weight = "bold";
      var wcls = weight === "regular" ? "ph" : "ph-" + weight;
      // Accept both "star" and "ph-star" (widget icon fields use the ph- prefix).
      var iname = String(el.icon || "star").replace(/^ph-/, "");
      i.className = wcls + " ph-" + iname;
      var size = Math.max(8, Math.round(Math.min(px(el.w, 64), px(el.h, 64)) * 0.82));
      i.style.cssText = "display:flex;align-items:center;justify-content:center;" +
        "width:100%;height:100%;line-height:1;color:" + color + ";font-size:" + size + "px";
      return i;
    }

    if (kind === "text") {
      var t = document.createElement("div");
      var size = el.size && el.size > 0 ? el.size : Math.max(10, Math.round(px(el.h, 40) * 0.5));
      var wmap = { thin: 300, light: 300, regular: 400, bold: 700, fill: 800, duotone: 700 };
      var fw = wmap[el.weight] || 700;
      var align = el.align || "left";
      var justify = align === "center" ? "center" : align === "right" ? "flex-end" : "flex-start";
      t.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:" + justify +
        ";color:" + color + ";font-size:" + size + "px;font-weight:" + fw + ";line-height:1.15;" +
        "text-align:" + align + ";overflow:hidden;font-family:var(--font-family, inherit)";
      t.textContent = el.text != null && el.text !== "" ? el.text : "Text";
      return t;
    }

    if (kind === "line") {
      var wrap = document.createElement("div");
      wrap.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:center";
      var bar = document.createElement("div");
      var t = Math.max(1, px(el.stroke, 2));
      // Draw along the box's longer axis: wide box = horizontal rule, tall box
      // = vertical rule.
      if (px(el.w, 0) >= px(el.h, 0)) {
        bar.style.cssText = "width:100%;height:" + t + "px;background:" + color + ";border-radius:" + (t / 2) + "px";
      } else {
        bar.style.cssText = "height:100%;width:" + t + "px;background:" + color + ";border-radius:" + (t / 2) + "px";
      }
      wrap.appendChild(bar);
      return wrap;
    }

    // rect / ellipse
    var d = document.createElement("div");
    var radius = kind === "ellipse" ? "50%" : (Math.max(0, px(el.radius, 0)) + "px");
    var css = "width:100%;height:100%;box-sizing:border-box;border-radius:" + radius + ";";
    if (el.fill === false) {
      css += "border:" + Math.max(1, px(el.stroke, 2)) + "px solid " + color + ";background:transparent;";
    } else {
      css += "background:" + color + ";";
    }
    d.style.cssText = css;
    return d;
  }

  // Custom HTML/CSS (or SVG) in a locked-down iframe: sandbox="" disables
  // scripts, the network, and same-origin, so authored markup renders but can't
  // run code or reach anything. pointer-events:none keeps the element selectable.
  // ``svg`` = true adds a rule so a pasted <svg> scales to fill the box.
  function renderHtml(el, svg) {
    var f = document.createElement("iframe");
    f.setAttribute("sandbox", "");
    f.setAttribute("scrolling", "no");
    f.style.cssText =
      "width:100%;height:100%;border:0;background:transparent;display:block;pointer-events:none";
    var reset =
      "*{box-sizing:border-box}html,body{margin:0;padding:0;width:100%;height:100%;" +
      "overflow:hidden;font-family:var(--font-family,-apple-system,'Segoe UI',Roboto,sans-serif);" +
      "color:#1B1A16}" +
      (svg ? "svg{width:100%;height:100%;display:block}" : "");
    f.srcdoc =
      "<!doctype html><html><head><meta charset='utf-8'><style>" +
      reset + (el.css || "") + "</style></head><body>" + (el.html || "") + "</body></html>";
    return f;
  }

  // kind "code": author HTML/CSS/JS fed by a widget's live data. Unlike the
  // static "html" kind, this ENABLES scripts, so the isolation must be exact:
  //   sandbox="allow-scripts" WITHOUT allow-same-origin -> the frame is a unique
  //     opaque origin, so it can't read the parent, cookies, storage, or reach
  //     any same-origin (loopback) Tesserae endpoint with credentials.
  //   CSP default-src 'none' -> no readable network: fetch/XHR/WebSocket are
  //     blocked, so a script can't reach a loopback Tesserae endpoint or read
  //     any response back. img-src additionally allows the web so remote artwork
  //     (Spotify covers, Unsplash) loads like it does for ordinary widgets; that
  //     is a one-way GET only. The render runs in headless Chromium navigating a
  //     loopback compose URL, i.e. inside Tesserae's trust boundary.
  // The widget data arrives DELIVERED (injected as window.ctx), never fetched
  // from inside the frame. Runs once at render time (e-ink is static).
  // Vendored libraries made available INSIDE the sandbox by inlining their
  // source (the frame has no network, so it can't load a URL). Each lib is
  // fetched once, synchronously, from its same-origin vendored path and cached;
  // on a headless compose render this runs before the page's load event, so the
  // library is ready by screenshot time. A lib is only inlined when the code
  // actually references it (``test`` against the element's html+css+js), so a
  // lean element stays lean. That inference is a heuristic, so it is both
  // reported (every entry in the element's lib report carries ``inferred`` and
  // the ``matched`` token) and refusable: ``el.autolibs === false`` turns the
  // whole thing off, libs and fonts alike, for an element that hand-authors its
  // own markup and wants no ambient stylesheets. URLs come from
  // ``window.__TESSERAE_LIBS`` (set by the compose + editor templates). None of
  // the minified sources contain a ``</script>`` sequence, so direct inlining
  // is safe.
  //
  //   name       -> label (debug only)
  //   files      -> keys into __TESSERAE_LIBS, inlined in order (deps first)
  //   test       -> RegExp, or a function returning the matched token (or null);
  //                 inline this lib when it hits. The match text rides into the
  //                 render report so an inferred injection can be traced back to
  //                 what triggered it.
  //   kind       -> "js" (inline <script>) or "css" (inline <style>)
  //   init       -> optional JS run right after the lib loads (register/config)
  var SANDBOX_LIBS = [
    { name: "Chart.js", files: ["chart"], test: /\bChart\b/, kind: "js",
      init: "try{Chart.defaults.animation=false;Chart.defaults.plugins.legend.display=false;}catch(e){}" },
    { name: "chartjs-datalabels", files: ["datalabels"], test: /ChartDataLabels/, kind: "js",
      init: "try{Chart.register(ChartDataLabels);}catch(e){}" },
    { name: "canvas-gauges", files: ["gauge"], test: /RadialGauge|LinearGauge/, kind: "js" },
    { name: "day.js", files: ["dayjs", "dayjs_utc", "dayjs_tz"], test: /\bdayjs\b/, kind: "js",
      init: "try{dayjs.extend(window.dayjs_plugin_utc);dayjs.extend(window.dayjs_plugin_timezone);}catch(e){}" },
    { name: "qrcode", files: ["qrcode"], test: /\bqrcode\b/, kind: "js" },
    { name: "marked", files: ["marked"], test: /\bmarked\b/, kind: "js" },
    { name: "chroma", files: ["chroma"], test: /\bchroma\b/, kind: "js" },
    { name: "svg.js", files: ["svgjs"], test: /\bSVG\b/, kind: "js" },
    // Phosphor icons, all six weights. Each weight's self-contained CSS
    // (font embedded as a data: URL) is inlined only when its class appears,
    // so an element that uses one weight doesn't pay for the others.
    // The regular weight is the BARE ``ph`` class, and the stylesheet only ever
    // defines it paired with an icon class (``.ph.ph-heart``), so require BOTH
    // halves to be present. Matching a lone "ph" token pulled the whole icon
    // font into any element that merely mentioned ph in passing.
    { name: "ph-regular", files: ["phosphor_regular"], kind: "css",
      test: function (s) {
        return /(^|[^\w-])ph(?![\w-])/.test(s) && /(^|[^\w-])ph-[a-z0-9]/.test(s) ? "ph" : null;
      } },
    { name: "ph-thin", files: ["phosphor_thin"], test: /\bph-thin\b/, kind: "css" },
    { name: "ph-light", files: ["phosphor_light"], test: /\bph-light\b/, kind: "css" },
    { name: "ph-bold", files: ["phosphor"], test: /\bph-bold\b/, kind: "css" },
    { name: "ph-fill", files: ["phosphor_fill"], test: /\bph-fill\b/, kind: "css" },
    { name: "ph-duotone", files: ["phosphor_duotone"], test: /\bph-duotone\b/, kind: "css" },
  ];

  // The token that made ``lib`` match, or null. Reported alongside the
  // injection so an inferred match is traceable to the text that caused it.
  function libMatch(lib, code) {
    if (typeof lib.test === "function") return lib.test(code) || null;
    var m = code.match(lib.test);
    return m ? m[0] : null;
  }

  var _libCache = {};
  function libSource(url) {
    if (!url) return null;
    if (url in _libCache) return _libCache[url];
    var src = null;
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", url, false); // sync: one-time, same-origin, cached
      xhr.send();
      if (xhr.status >= 200 && xhr.status < 300) src = xhr.responseText;
    } catch (e) { src = null; }
    _libCache[url] = src;
    return src;
  }

  // Touch-region mirroring (issue #49). The code sandbox is an
  // origin-less iframe, so the server-side extraction walker can't see
  // data-on-tap nodes inside it. A collector script injected into the
  // srcdoc posts each annotated node's box out via postMessage, and the
  // parent mirrors them as invisible, absolutely-positioned marker divs
  // beside the iframe, which the walker CAN see. The mirrors carry the
  // raw attribute values (including @name refs, which resolve against
  // the element's data-touch-actions map) and an explicit
  // data-touch-origin="markup" so sandbox markup never inherits the
  // config-origin trust of the element wrapper.
  // The collector re-scans on DOM changes, not just once: a code element
  // that builds its tappable DOM asynchronously (JS, fetched data, a chart)
  // wouldn't have the annotated node yet on a single early pass, so its touch
  // region was silently lost. It posts the FULL current set each time (the
  // parent rebuilds mirrors from it), de-duped so an unrelated animation
  // doesn't spam, and stops observing before the parent's settle cap.
  // The same message also carries overlay value slots ([data-overlay-key]
  // nodes, hybrid render mode): the sandbox reports each slot's box plus
  // its key / suffix / map attributes and computed type metrics, and the
  // parent mirrors them as annotated divs (with the font metrics inlined)
  // so the extraction walker measures them exactly like a direct slot.
  var TOUCH_COLLECT_JS =
    "(function(){var last=null;function snap(){var out=[];var ns=document.querySelectorAll(" +
    "'[data-on-tap],[data-on-swipe],[data-on-slide]');" +
    "for(var i=0;i<ns.length;i++){var n=ns[i];var r=n.getBoundingClientRect();" +
    "if(r.width<=0||r.height<=0)continue;var cs=getComputedStyle(n);" +
    "if(cs.display==='none'||cs.visibility==='hidden')continue;" +
    "out.push({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)," +
    "tap:n.getAttribute('data-on-tap'),swipe:n.getAttribute('data-on-swipe')," +
    "slide:n.getAttribute('data-on-slide'),tid:n.getAttribute('data-touch-id')});}return out;}" +
    "function snapSlots(){var out=[];var ns=document.querySelectorAll('[data-overlay-key]');" +
    "for(var i=0;i<ns.length;i++){var n=ns[i];var r=n.getBoundingClientRect();" +
    "if(r.width<=0||r.height<=0)continue;var cs=getComputedStyle(n);" +
    "if(cs.display==='none'||cs.visibility==='hidden')continue;" +
    "out.push({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)," +
    "key:n.getAttribute('data-overlay-key'),suffix:n.getAttribute('data-overlay-suffix')," +
    "map:n.getAttribute('data-overlay-map'),align:cs.textAlign," +
    "px:Math.round(parseFloat(cs.fontSize)||0),weight:parseInt(cs.fontWeight,10)||400});}return out;}" +
    "function send(){var out=snap();var slots=snapSlots();" +
    "var s=JSON.stringify(out)+JSON.stringify(slots);if(s===last)return;last=s;" +
    "try{parent.postMessage({type:'tesserae-touch-regions',regions:out,slots:slots},'*');}catch(e){}}" +
    "requestAnimationFrame(function(){requestAnimationFrame(send);});" +
    "var t=null;var mo=new MutationObserver(function(){clearTimeout(t);t=setTimeout(send,50);});" +
    "try{mo.observe(document.documentElement,{childList:true,subtree:true,attributes:true," +
    "attributeFilter:['data-on-tap','data-on-swipe','data-on-slide','data-overlay-key'," +
    "'style','class','hidden']});}catch(e){}" +
    "setTimeout(function(){try{mo.disconnect();}catch(e){}},2900);})();";

  // Composed-stylesheet self-check. The sandbox is an opaque origin, so nothing
  // outside it can read the stylesheet the browser actually parsed; the frame
  // reports on itself instead. For each of its <style> blocks it diffs the
  // authored text against the rules the parser kept and posts anything that
  // vanished, so a dropped rule is NAMED in render_report's diagnostics rather
  // than pixel-hunted. ``sheet: "library"`` findings mean the auto-injected CSS
  // is malformed (an injector bug); ``"authored"`` means the element's own CSS
  // is. Stringified with toString() instead of hand-written as a string literal
  // so eslint and prettier still see real code.
  // Block splitting is best-effort, like the render-report's own CSS analysis:
  // a brace inside a quoted value can throw the depth count off.
  function cssSelfCheck() {
    try {
      // The parser rewrites what it keeps (``:before`` comes back as
      // ``::before``), so compare on a shape both spellings collapse to.
      var norm = function (s) {
        return String(s)
          .replace(/\s+/g, "")
          .replace(/['"]/g, "")
          .replace(/::/g, ":")
          .toLowerCase();
      };
      var preludes = function (text) {
        var out = [];
        var depth = 0;
        var buf = "";
        var clean = String(text || "").replace(/\/\*[\s\S]*?\*\//g, " ");
        for (var i = 0; i < clean.length; i++) {
          var ch = clean.charAt(i);
          if (ch === "{") {
            if (depth === 0 && buf.trim()) out.push(buf.trim());
            depth++;
            buf = "";
          } else if (ch === "}") {
            depth = depth > 0 ? depth - 1 : 0;
            buf = "";
          } else if (depth === 0) buf += ch;
        }
        return out;
      };
      var findings = [];
      var counts = {};
      var styles = document.querySelectorAll("style[data-tesserae-css], style[data-tesserae-lib]");
      for (var s = 0; s < styles.length; s++) {
        var st = styles[s];
        var which = st.hasAttribute("data-tesserae-lib") ? "library" : "authored";
        var rules = st.sheet ? st.sheet.cssRules : null;
        if (!rules) continue;
        var kept = {};
        for (var r = 0; r < rules.length; r++) {
          var text = String(rules[r].cssText || "");
          var head = rules[r].selectorText || text.slice(0, text.indexOf("{"));
          kept[norm(head)] = 1;
        }
        var authored = preludes(st.textContent);
        counts[which] = { authored: authored.length, parsed: rules.length };
        // Only diff rule by rule when the counts actually disagree. The
        // selector match is approximate, and reporting a normalisation
        // mismatch as a dropped rule would bury the real ones.
        if (rules.length >= authored.length) continue;
        for (var a = 0; a < authored.length; a++) {
          if (kept[norm(authored[a])]) continue;
          findings.push({
            sheet: which,
            selector: authored[a].replace(/\s+/g, " ").slice(0, 120),
          });
          if (findings.length >= 20) break;
        }
      }
      parent.postMessage(
        { type: "tesserae-css-report", counts: counts, dropped: findings },
        "*",
      );
    } catch { /* diagnostics only: never break the render */ }
  }
  var CSS_CHECK_JS = "(" + cssSelfCheck.toString() + ")();";

  function renderCode(el, data) {
    var wrap = document.createElement("div");
    wrap.style.cssText = "position:relative;width:100%;height:100%";
    var f = document.createElement("iframe");
    f.setAttribute("sandbox", "allow-scripts");
    f.setAttribute("scrolling", "no");
    f.style.cssText =
      "width:100%;height:100%;border:0;background:transparent;display:block;pointer-events:none";
    var reset =
      "*{box-sizing:border-box}html,body{margin:0;padding:0;width:100%;height:100%;" +
      "overflow:hidden;font-family:var(--font-family,-apple-system,'Segoe UI',Roboto,sans-serif);" +
      "color:#1B1A16}";
    var ctx = {
      data: data != null ? data : (el.data != null ? el.data : null),
      options: el.options || {},
      field: el.field || "",
      w: px(el.w, 0),
      h: px(el.h, 0),
    };
    // Escape "<" so a "</script>" inside the injected JSON can't close the tag.
    var ctxJson = JSON.stringify(ctx).replace(/</g, "\\u003c");

    // Per-element record of which vendored bundles were inlined (or matched
    // but couldn't be), read by the render report so "why is my chart/icon
    // missing", and "why did an icon font I never asked for turn up", are
    // answerable without pixel-diffing. Every entry says whether it was
    // ``inferred`` from the code and what token triggered it. Libs always
    // inline BEFORE the ctx + user script in srcdoc document order.
    var autolibs = el.autolibs !== false;
    var libReport = { el: String(el.id || ""), autolibs: autolibs, libs: [] };
    (window.__tesseraeLibReport = window.__tesseraeLibReport || []).push(libReport);

    // Inline only the vendored libs this element references.
    var probe = (el.html || "") + "\n" + (el.css || "") + "\n" + (el.js || "");
    // Custom-property NAMES are the author's own vocabulary, never a library
    // reference: a ``--ph`` or ``--chart-bg`` variable must not drag a bundle
    // in. Strip the names before matching; var() USES survive as ``var( )``.
    var probeLibs = probe.replace(/--[A-Za-z][\w-]*/g, " ");
    var urls = window.__TESSERAE_LIBS || {};
    var headCss = "";
    var libScripts = "";
    var needFont = false;
    // Explicit icon opt-in (el.icons) beats the scan, for the icon libs only.
    // The scan stays the default so every element built before this field keeps
    // behaving identically; declaring the field is the path to prefer, because
    // a text scan can both miss icons a script builds after render and pull the
    // stylesheet in when the author never wanted it. Returns true / false to
    // decide, or null to fall through to the heuristic.
    //   false            -> no icon CSS at all
    //   true             -> the regular weight
    //   ["bold","fill"]  -> exactly those weights
    var ICON_LIBS = {
      "ph-regular": "regular", "ph-thin": "thin", "ph-light": "light",
      "ph-bold": "bold", "ph-fill": "fill", "ph-duotone": "duotone",
    };
    function iconChoice(libName) {
      var want = el.icons;
      var weight = ICON_LIBS[libName];
      if (weight === undefined) return null; // not an icon lib
      if (want === null || want === undefined) return null; // infer
      if (want === false) return false;
      if (want === true) return weight === "regular";
      if (Object.prototype.toString.call(want) === "[object Array]") {
        for (var n = 0; n < want.length; n++) {
          if (String(want[n]).toLowerCase() === weight) return true;
        }
        return false;
      }
      return null;
    }

    for (var i = 0; autolibs && i < SANDBOX_LIBS.length; i++) {
      var lib = SANDBOX_LIBS[i];
      var choice = iconChoice(lib.name);
      var hit;
      if (choice === false) continue;
      if (choice === true) {
        hit = "icons:declared";
      } else {
        hit = libMatch(lib, probeLibs);
      }
      if (!hit) continue;
      var joined = "";
      for (var j = 0; j < lib.files.length; j++) {
        var s = libSource(urls[lib.files[j]]);
        // ";" separates concatenated JS statements. CSS has no such separator:
        // a stray top-level ";" is a parse error that swallows the rule after
        // it, which (headCss sitting ahead of the author's CSS) silently ate
        // the element's first authored rule.
        if (s) joined += s + (lib.kind === "css" ? "\n" : "\n;\n");
      }
      if (!joined) {
        libReport.libs.push({
          name: lib.name, injected: false, inferred: choice !== true, matched: hit,
          reason: "source fetch failed",
        });
        continue;
      }
      libReport.libs.push({
        name: lib.name, injected: true, kind: lib.kind, inferred: choice !== true, matched: hit,
      });
      if (lib.kind === "css") {
        headCss += joined;
        needFont = true; // fonts (phosphor) arrive as data: URLs
      } else {
        libScripts += "<script>" + joined + "</" + "script>";
        if (lib.init) libScripts += "<script>" + lib.init + "</" + "script>";
      }
    }
    // Inline any bundled font whose family name the code references, so a code
    // element can use it by name. The sandbox has no network and a
    // ``font-src data:`` CSP, so the @font-face has to carry the woff2 as a
    // data: URL (the /fonts/face/<id>.css endpoint builds that). Only fonts the
    // code actually names are inlined, so a lean element stays lean.
    var fonts = window.__TESSERAE_FONTS || [];
    for (var k = 0; autolibs && k < fonts.length; k++) {
      var fnt = fonts[k];
      if (!fnt || !fnt.name || probe.indexOf(fnt.name) === -1) continue;
      var fcss = libSource(fnt.url);
      if (fcss) { headCss += fcss + "\n"; needFont = true; }
      var fontEntry = {
        name: "font:" + fnt.name, injected: !!fcss, kind: "css",
        inferred: true, matched: fnt.name,
      };
      if (!fcss) fontEntry.reason = "font css fetch failed";
      libReport.libs.push(fontEntry);
    }
    // img-src allows the web so a code element can show remote artwork
    // (Spotify album covers, Unsplash photos, etc.), the same external images
    // ordinary widgets already paint at render time. This is images ONLY: a
    // one-way GET. connect-src / fetch / XHR / WebSocket stay blocked by
    // default-src 'none', so a script still can't read anything back over the
    // network (or reach a loopback Tesserae endpoint); the worst a URL can do is
    // leak into an <img> request, and the element's content is user-authored.
    var csp =
      "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
      "img-src data: blob: https: http:" +
      (needFont ? "; font-src data:" : "");

    // Surface sandbox failures to the render-report diagnostics. The frame is
    // an opaque origin, so nothing outside can reach in; but console + error
    // events DO propagate to the page target headless Chromium listens on.
    // Tag every entry with the element id so the report names the culprit.
    // Registered before the <style> so CSP violations (a blocked font/img
    // URL) fired during parse are caught too.
    // Same "<" escape as ctxJson so an id can't close the inline script tag.
    var tag = JSON.stringify("[code-el " + String(el.id || "?") + "]").replace(/</g, "\\u003c");
    var diagHooks =
      "<script>(function(){var t=" + tag + ";" +
      "window.addEventListener('error',function(ev){" +
      "console.error(t+' uncaught: '+(ev.message||ev.error)+(ev.lineno?' (line '+ev.lineno+')':''));});" +
      "window.addEventListener('unhandledrejection',function(ev){" +
      "console.error(t+' unhandled rejection: '+String(ev.reason&&ev.reason.message||ev.reason));});" +
      "document.addEventListener('securitypolicyviolation',function(ev){" +
      "console.error(t+' csp blocked '+ev.violatedDirective+': '+(ev.blockedURI||'inline'));});" +
      "})();</" + "script>";

    // Three separate <style> blocks, never one concatenated string. A
    // stylesheet's parse errors stop at its own element, so a malformed
    // injection can no longer consume the author's rules: a stray token used to
    // swallow whichever rule came next, and since headCss sat directly ahead of
    // el.css that was ALWAYS the element's first authored rule (typically the
    // :root block, taking every variable it declared with it). Author CSS still
    // comes last in document order, so it keeps winning ties against the libs.
    var styleBlocks =
      "<style>" + reset + "</style>" +
      (headCss ? "<style data-tesserae-lib>" + headCss + "</style>" : "") +
      "<style data-tesserae-css>" + (el.css || "") + "</style>";

    f.srcdoc =
      "<!doctype html><html><head><meta charset='utf-8'>" +
      "<meta http-equiv='Content-Security-Policy' content=\"" + csp + "\">" +
      diagHooks +
      styleBlocks + "</head><body>" +
      (el.html || "") +
      libScripts +
      "<script>window.ctx=" + ctxJson + ";</" + "script>" +
      // Author JS runs inside a function, not at global scope. At global scope
      // a plain ``var top = ...`` cannot overwrite the read-only ``window.top``:
      // the assignment silently fails, ``top`` stays a cross-origin Window, and
      // the next property read throws "Blocked a frame with origin null",
      // blanking the whole element. Same trap for name, status, length, self,
      // parent and closed. Inside a function those are ordinary locals.
      // The cost is that a top-level ``function foo(){}`` is no longer reachable
      // from an inline ``onclick``; the frame is pointer-events:none and taps go
      // through touch regions, so nothing in a panel can call one anyway.
      "<script>try{(function(){" + (el.js || "") + "\n}).call(window);}catch(e){" +
      "console.error(" + tag + "+' script threw: '+String(e&&e.stack||e));" +
      "document.body.innerHTML='<pre style=\"color:#900;font:12px monospace;white-space:pre-wrap\">'" +
      "+String(e&&e.message||e)+'</pre>';}</" + "script>" +
      "<script>" + CSS_CHECK_JS + "</" + "script>" +
      "<script>" + TOUCH_COLLECT_JS + "</" + "script>" +
      "</body></html>";

    // Pending counter lets the extraction script wait until every code
    // sandbox has reported (or timed out) before walking the DOM.
    window.__tesseraeTouchPending = (window.__tesseraeTouchPending || 0) + 1;
    var settled = false;
    var quietTimer = null;
    function settle() {
      if (settled) return;
      settled = true;
      clearTimeout(quietTimer);
      window.removeEventListener("message", onMsg);
      window.__tesseraeTouchPending = Math.max(0, (window.__tesseraeTouchPending || 1) - 1);
    }
    // Hard cap so a constantly-mutating sandbox can't stall the walk.
    setTimeout(settle, 3000);
    function onMsg(ev) {
      if (ev.source !== f.contentWindow || !ev.data) return;
      // Stylesheet self-check (posted once, at parse time). Recorded against
      // the element id here, since only the parent knows which frame is which.
      if (ev.data.type === "tesserae-css-report") {
        (window.__tesseraeCssReport = window.__tesseraeCssReport || []).push({
          el: String(el.id || ""),
          counts: ev.data.counts || {},
          dropped: ev.data.dropped || [],
        });
        return;
      }
      if (ev.data.type !== "tesserae-touch-regions") return;
      if (settled) return;
      // Each message is the FULL current set; rebuild this iframe's mirrors
      // from it so a late (async) annotated node replaces the earlier (empty)
      // snapshot rather than being missed.
      var old = wrap.querySelectorAll(".touch-mirror, .overlay-mirror");
      for (var i = 0; i < old.length; i++) old[i].remove();
      (ev.data.regions || []).forEach(function (r) {
        if (!r || (!r.tap && !r.swipe && !r.slide)) return;
        var m = document.createElement("div");
        m.className = "touch-mirror";
        m.style.cssText = "position:absolute;pointer-events:none;left:" + (r.x | 0) + "px;top:" +
          (r.y | 0) + "px;width:" + (r.w | 0) + "px;height:" + (r.h | 0) + "px";
        if (r.tap) m.setAttribute("data-on-tap", r.tap);
        if (r.swipe) m.setAttribute("data-on-swipe", r.swipe);
        if (r.slide) m.setAttribute("data-on-slide", r.slide);
        if (r.tid) m.setAttribute("data-touch-id", r.tid);
        m.setAttribute("data-touch-origin", "markup");
        wrap.appendChild(m);
      });
      // Overlay value slots reported from inside the sandbox: mirror with
      // the type metrics inlined so the extraction walker's
      // getComputedStyle sees the sandbox node's font size / weight /
      // alignment on the mirror itself.
      (ev.data.slots || []).forEach(function (s) {
        if (!s || !s.key) return;
        var m = document.createElement("div");
        m.className = "overlay-mirror";
        m.style.cssText = "position:absolute;pointer-events:none;left:" + (s.x | 0) + "px;top:" +
          (s.y | 0) + "px;width:" + (s.w | 0) + "px;height:" + (s.h | 0) + "px;" +
          "font-size:" + (s.px | 0) + "px;font-weight:" + (s.weight | 0) + ";" +
          "text-align:" + (s.align || "left");
        m.setAttribute("data-overlay-key", s.key);
        if (s.suffix) m.setAttribute("data-overlay-suffix", s.suffix);
        if (s.map) m.setAttribute("data-overlay-map", s.map);
        wrap.appendChild(m);
      });
      // Settle a short quiet period after the last report (bounded by the 3s
      // cap), so an async-built region has time to arrive without every push
      // waiting the full timeout.
      clearTimeout(quietTimer);
      quietTimer = setTimeout(settle, 350);
    }
    window.addEventListener("message", onMsg);
    wrap.appendChild(f);
    return wrap;
  }

  // A widget data field, shown as text / number or a small chart.
  function renderData(el, data, color) {
    var value = resolvePath(data, el.field);
    var display = el.display || "text";
    if (display === "line" || display === "bar" || display === "sparkline") {
      return renderChart(el, value, display, color);
    }
    var align = el.align || "left";
    var box = document.createElement("div");
    box.style.cssText =
      "width:100%;height:100%;display:flex;flex-direction:column;justify-content:center;overflow:hidden;" +
      "font-family:var(--font-family, inherit);color:" + color + ";align-items:" +
      (align === "center" ? "center" : align === "right" ? "flex-end" : "flex-start");
    var size = el.size && el.size > 0
      ? el.size
      : Math.max(10, Math.round(px(el.h, 40) * (el.label ? 0.42 : 0.58)));
    var main = document.createElement("div");
    main.style.cssText =
      "font-size:" + size + "px;font-weight:700;line-height:1.05;white-space:nowrap;text-align:" + align;
    var text;
    if (value == null || (Array.isArray(value) && !value.length)) {
      text = "—";
    } else if (el.format) {
      var f = formatValue(value, el.format);
      text = f != null ? f : String(value);
    } else if (display === "number") {
      var n = Number(value);
      text = isFinite(n) ? n.toFixed(Math.max(0, px(el.precision, 0))) : String(value);
    } else {
      text = String(value);
    }
    main.textContent = text + (el.unit || "");
    box.appendChild(main);
    if (el.label) {
      var lab = document.createElement("div");
      lab.style.cssText =
        "font-size:" + Math.max(8, Math.round(size * 0.32)) + "px;font-weight:600;opacity:.68;" +
        "margin-top:2px;white-space:nowrap;text-align:" + align;
      lab.textContent = el.label;
      box.appendChild(lab);
    }
    return box;
  }

  // Concrete colour for a canvas (fillStyle can't resolve var()/tokens): use the
  // element colour when it's a literal, else a dark-ink default (e-ink friendly).
  function inkColor(color) {
    return color && color.charAt(0) !== "v" ? color : "#1B1A16";
  }

  // ---- value formatting (data primitives) -------------------------------
  var _MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var _MONF = ["January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"];
  var _DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var _DOWF = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  function _pad(n) { return (n < 10 ? "0" : "") + n; }
  function toDate(v) {
    if (v == null || v === "") return null;
    var d = typeof v === "number" ? new Date(v < 1e12 ? v * 1000 : v) : new Date(v);
    return isNaN(d.getTime()) ? null : d;
  }
  function formatDate(d, fmt) {
    var h12 = d.getHours() % 12 || 12;
    return fmt.replace(/yyyy|MMMM|MMM|MM|dddd|ddd|dd|HH|hh|mm|ss|a|d|H|h|M/g, function (t) {
      switch (t) {
        case "yyyy": return String(d.getFullYear());
        case "MMMM": return _MONF[d.getMonth()];
        case "MMM": return _MON[d.getMonth()];
        case "MM": return _pad(d.getMonth() + 1);
        case "M": return String(d.getMonth() + 1);
        case "dddd": return _DOWF[d.getDay()];
        case "ddd": return _DOW[d.getDay()];
        case "dd": return _pad(d.getDate());
        case "d": return String(d.getDate());
        case "HH": return _pad(d.getHours());
        case "H": return String(d.getHours());
        case "hh": return _pad(h12);
        case "h": return String(h12);
        case "mm": return _pad(d.getMinutes());
        case "ss": return _pad(d.getSeconds());
        case "a": return d.getHours() < 12 ? "am" : "pm";
        default: return t;
      }
    });
  }
  function relTime(v) {
    var d = toDate(v);
    if (!d) return null;
    var s = (d.getTime() - Date.now()) / 1000, abs = Math.abs(s), fut = s > 0;
    function u(n, w) { n = Math.round(n); return fut ? "in " + n + w : n + w + " ago"; }
    if (abs < 60) return "now";
    if (abs < 3600) return u(abs / 60, "m");
    if (abs < 86400) return u(abs / 3600, "h");
    if (abs < 604800) return u(abs / 86400, "d");
    return formatDate(d, "MMM d");
  }
  // Format ``value`` per ``fmt``: "relative" | a date pattern (HH:mm, MMM d, …) |
  // a number pattern (0, 0.0, 0.00). Returns null if it doesn't apply.
  function formatValue(value, fmt) {
    if (!fmt) return null;
    if (fmt === "relative") return relTime(value);
    if (/[yMdHhmsa]/.test(fmt)) {
      var d = toDate(value);
      if (d) return formatDate(d, fmt);
    }
    var n = Number(value);
    if (isFinite(n)) {
      var m = fmt.match(/^0(?:\.(0+))?$/);
      if (m) return n.toFixed(m[1] ? m[1].length : 0);
    }
    return null;
  }

  function renderChart(el, value, display, color) {
    var wrap = document.createElement("div");
    wrap.style.cssText = "width:100%;height:100%;position:relative";
    var values = Array.isArray(value)
      ? value.map(Number).filter(function (n) { return isFinite(n); })
      : [];
    if (!window.Chart || !values.length) {
      wrap.style.cssText +=
        ";display:flex;align-items:center;justify-content:center;color:var(--text-secondary,#8a8a82);" +
        "font:600 12px var(--font-family,sans-serif)";
      wrap.textContent = window.Chart ? "no data · check field or plugin" : "chart unavailable";
      return wrap;
    }
    var ink = inkColor(color);
    var spark = display === "sparkline";
    var cv = document.createElement("canvas");
    cv.width = Math.max(1, Math.round(px(el.w, 200)));
    cv.height = Math.max(1, Math.round(px(el.h, 100)));
    cv.style.cssText = "width:100%;height:100%";
    wrap.appendChild(cv);
    new window.Chart(cv, {
      type: display === "bar" ? "bar" : "line",
      data: {
        labels: values.map(function (_, i) { return i; }),
        datasets: [{
          data: values,
          borderColor: ink,
          backgroundColor: spark ? "color-mix(in srgb, " + ink + " 20%, transparent)" : ink,
          borderWidth: 2,
          pointRadius: 0,
          fill: spark,
          tension: spark ? 0.35 : 0.2,
        }],
      },
      options: {
        responsive: false,
        animation: false,
        maintainAspectRatio: false,
        layout: { padding: spark ? 1 : 2 },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          x: { display: !spark, grid: { display: false }, ticks: { color: ink, font: { size: 9 } } },
          y: { display: !spark, grid: { display: false }, ticks: { color: ink, font: { size: 9 } } },
        },
      },
    });
    return wrap;
  }

  window.PanelsDecorate = { render: render, resolvePath: resolvePath, primitive: renderPrimitive };
})();
