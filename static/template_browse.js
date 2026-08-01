/* Community-templates section of the Browse page (template marketplace).
 *
 * Renders cards from the server-side catalog proxy and drives the install
 * modal: missing-requirements notice (deep links into the widget install
 * flow), the template's declared inputs as a form (secret inputs masked),
 * then POST install -> redirect to the new dashboard in the panels editor.
 * Author names are seeded pseudonyms; sponsors show a small heart emblem.
 */
(function () {
  "use strict";

  var grid = document.getElementById("tpl-market-grid");
  if (!grid) return;

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { node.appendChild(c); });
    return node;
  }

  function authorLine(author) {
    var wrap = el("span", { style: "opacity:.75;font-size:.9em" });
    wrap.appendChild(document.createTextNode("by " + ((author && author.name) || "unknown")));
    if (author && author.sponsor) {
      var emblem = el("i", {
        class: "ph-fill ph-heart",
        title: "Tesserae sponsor",
        style: "color:var(--accent-1,#c0392b);margin-left:4px;vertical-align:middle",
      });
      wrap.appendChild(emblem);
    }
    return wrap;
  }

  function installModal(entry) {
    var overlay = el("div", {
      style: "position:fixed;inset:0;z-index:400;background:rgba(10,10,10,.45);" +
        "display:flex;align-items:center;justify-content:center;padding:24px",
    });
    var card = el("div", {
      style: "background:var(--t-surface,#fff);border-radius:14px;max-width:560px;width:100%;" +
        "max-height:90vh;overflow-y:auto;padding:20px;box-shadow:0 12px 48px rgba(0,0,0,.3)",
    });
    overlay.appendChild(card);
    overlay.addEventListener("click", function (ev) { if (ev.target === overlay) overlay.remove(); });

    card.appendChild(el("h3", { text: entry.title, style: "margin:0 0 2px" }));
    card.appendChild(authorLine(entry.author));
    var img = el("img", { src: entry.preview_url, alt: "", style: "max-width:100%;border-radius:8px;margin:10px 0;border:1px solid var(--t-border,#ddd)" });
    card.appendChild(img);
    if (entry.description) card.appendChild(el("p", { text: entry.description, style: "margin:4px 0 10px" }));

    var missing = entry.missing_requires || [];
    if (missing.length) {
      var note = el("div", {
        style: "background:rgba(184,134,11,.08);border:1px solid rgba(184,134,11,.4);border-radius:8px;padding:10px 12px;margin:8px 0",
      });
      note.appendChild(el("div", { text: "Needs these marketplace items first:" }));
      missing.forEach(function (id) {
        var link = el("a", { href: "/plugins/browse?q=" + encodeURIComponent(id), text: id, style: "margin-right:8px" });
        note.appendChild(link);
      });
      card.appendChild(note);
    }

    var form = el("div", { style: "display:flex;flex-direction:column;gap:8px;margin-top:6px" });
    var fields = [];
    var count = Number(entry.inputs || 0);
    if (count > 0) {
      form.appendChild(el("div", { text: "This template asks for " + count + " value(s) at install:", style: "font-weight:600" }));
    }
    // Full input specs ride the doc fetch server-side; the modal collects
    // values keyed by name from the catalog's lightweight spec list.
    (entry.input_specs || []).forEach(function (spec) {
      var label = el("label", { text: spec.label || spec.name, style: "display:flex;flex-direction:column;gap:3px;font-size:.95em" });
      var input;
      if (spec.type === "textarea") input = el("textarea", { rows: "2" });
      else if (spec.type === "boolean") input = el("input", { type: "checkbox" });
      else if (spec.type === "select") {
        input = el("select", {});
        (spec.choices || []).forEach(function (c) {
          var opt = el("option", { value: String(c.value), text: c.label || String(c.value) });
          input.appendChild(opt);
        });
      } else input = el("input", { type: spec.secret ? "password" : (spec.type === "number" ? "number" : "text") });
      if (input.style) input.style.cssText += ";padding:7px 9px;border:1px solid var(--t-border,#ccc);border-radius:7px;font:inherit;background:transparent;color:inherit";
      if (spec.default != null && input.type !== "checkbox") input.value = spec.default;
      label.appendChild(input);
      form.appendChild(label);
      fields.push({ spec: spec, input: input });
    });
    card.appendChild(form);

    var status = el("div", { style: "margin-top:8px;opacity:.85" });
    var actions = el("div", { style: "display:flex;gap:8px;justify-content:flex-end;margin-top:12px" });
    var cancel = el("button", { class: "dx-btn-ghost-sm", text: "Cancel" });
    cancel.addEventListener("click", function () { overlay.remove(); });
    var go = el("button", { class: "dx-btn-sm", text: missing.length ? "Install anyway" : "Install" });
    go.addEventListener("click", function () {
      go.disabled = true;
      status.textContent = "Installing…";
      var values = {};
      fields.forEach(function (f) {
        values[f.spec.name] = f.input.type === "checkbox" ? f.input.checked : f.input.value;
      });
      fetch("/plugins/templates/install", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug: entry.slug, inputs: values }),
      }).then(function (resp) { return resp.json().then(function (b) { return { ok: resp.ok, body: b }; }); })
        .then(function (r) {
          if (!r.ok) {
            status.textContent = r.body.error || "Install failed";
            go.disabled = false;
            return;
          }
          status.textContent = "Installed. Opening the editor…";
          location.href = "/experiments/composer/c/" + r.body.page_id;
        })
        .catch(function () { status.textContent = "Install failed: network error"; go.disabled = false; });
    });
    actions.appendChild(cancel);
    actions.appendChild(go);
    card.appendChild(actions);
    card.appendChild(status);
    document.body.appendChild(overlay);
  }

  function card(entry) {
    var node = el("article", { class: "dx-section-card", style: "padding:12px;display:flex;flex-direction:column;gap:8px" });
    var img = el("img", { src: entry.preview_url, alt: "", loading: "lazy", style: "width:100%;border-radius:8px;border:1px solid var(--t-border,#ddd);aspect-ratio:" + (entry.w || 4) + "/" + (entry.h || 3) + ";object-fit:cover" });
    node.appendChild(img);
    var head = el("div", { style: "display:flex;justify-content:space-between;gap:8px;align-items:baseline" });
    head.appendChild(el("strong", { text: entry.title }));
    if (entry.installs) head.appendChild(el("span", { text: entry.installs + " installs", style: "opacity:.65;font-size:.85em;white-space:nowrap" }));
    node.appendChild(head);
    node.appendChild(authorLine(entry.author));
    if (entry.description) {
      node.appendChild(el("div", { text: entry.description.slice(0, 140), style: "opacity:.8;font-size:.92em" }));
    }
    var open = el("button", { class: "dx-btn-sm", text: "View & install" });
    open.addEventListener("click", function () { installModal(entry); });
    node.appendChild(open);
    return node;
  }

  fetch("/plugins/templates/index.json")
    .then(function (resp) { return resp.json().then(function (b) { return { ok: resp.ok, body: b }; }); })
    .then(function (r) {
      grid.textContent = "";
      if (!r.ok) {
        grid.appendChild(el("div", { text: r.body.error || "Template catalog unavailable right now.", style: "opacity:.7" }));
        return;
      }
      var templates = r.body.templates || [];
      if (!templates.length) {
        grid.appendChild(el("div", { text: "No templates published yet. Share one from the panels editor!", style: "opacity:.7" }));
        return;
      }
      templates.forEach(function (t) { grid.appendChild(card(t)); });
    })
    .catch(function () {
      grid.textContent = "";
      grid.appendChild(el("div", { text: "Template catalog unavailable right now.", style: "opacity:.7" }));
    });
})();
