/* Share-as-template dialog for the panels editor (template marketplace).
 *
 * Flow: Share button -> POST share/prepare (sanitize + lint + quality gate)
 * -> dialog shows what was redacted, suggested inputs, warnings, and the
 * seeded pseudonym -> Submit -> POST share/submit (server rebuilds and
 * validates; the dialog copy is display-only). Everything install-specific
 * or secret was already stripped server-side by prepare; the dialog's job is
 * metadata + choosing which suggested inputs to keep.
 */
(function () {
  "use strict";

  // Every URL here has to carry the app's script root: under the Home
  // Assistant App the editor is served beneath /api/hassio_ingress/<token>/
  // (same for any reverse proxy on a subpath), so a bare "/panels/..." leaves
  // Tesserae entirely and the reply is never JSON.
  var PREFIX = window.TESSERAE_URL_PREFIX || "";

  var btn = document.getElementById("panels-share");
  if (!btn) return;
  var host = document.querySelector("[data-canvas-id]");
  var canvasId = (host && host.getAttribute("data-canvas-id")) ||
    (location.pathname.match(/\/c\/([a-z0-9]+)/) || [])[1] || "";
  if (!canvasId) return;

  var overlay = null;

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else if (k === "html") node.innerHTML = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { node.appendChild(c); });
    return node;
  }

  function close() {
    if (overlay) { overlay.remove(); overlay = null; }
  }

  // POST and parse JSON, keeping a transport failure distinct from a reply
  // that isn't JSON at all: a login redirect after the session expired, a
  // proxy error page, or the plain-text 404 the share routes return when the
  // templates experiment is off. Rejects with a message worth showing.
  function postJson(path, payload) {
    var init = { method: "POST" };
    if (payload !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(payload);
    }
    return fetch(PREFIX + path, init).then(
      function (resp) {
        return resp.text().then(function (text) {
          var body = null;
          try { body = JSON.parse(text); } catch (err) { body = null; }
          if (!body || typeof body !== "object") {
            throw new Error(
              "the server replied " + resp.status + " " + (resp.statusText || "") +
              " instead of JSON" +
              (resp.redirected ? " (redirected to " + resp.url + "; the session may have expired)" : "")
            );
          }
          return { ok: resp.ok, body: body };
        });
      },
      function () { throw new Error("couldn't reach the server"); }
    );
  }

  function open(prep) {
    close();
    overlay = el("div", { class: "tpl-share-overlay" });
    overlay.style.cssText =
      "position:fixed;inset:0;z-index:400;background:rgba(10,10,10,.45);" +
      "display:flex;align-items:center;justify-content:center;padding:24px";
    var card = el("div", {});
    card.style.cssText =
      "background:var(--t-surface,#fff);color:inherit;border-radius:14px;max-width:680px;" +
      "width:100%;max-height:90vh;overflow-y:auto;padding:20px;box-shadow:0 12px 48px rgba(0,0,0,.3)";
    overlay.appendChild(card);
    overlay.addEventListener("click", function (ev) { if (ev.target === overlay) close(); });

    card.appendChild(el("h2", { text: "Share as a community template" }));

    if (prep.blocking && prep.blocking.length) {
      var blocks = el("div", {});
      blocks.style.cssText = "background:rgba(178,34,34,.08);border:1px solid rgba(178,34,34,.4);" +
        "border-radius:8px;padding:10px 12px;margin:10px 0";
      blocks.appendChild(el("strong", { text: "Can't share yet:" }));
      prep.blocking.forEach(function (b) { blocks.appendChild(el("div", { text: "• " + b })); });
      card.appendChild(blocks);
      card.appendChild(el("button", { class: "pbtn", text: "Close" })).addEventListener("click", close);
      document.body.appendChild(overlay);
      return;
    }

    if (!prep.online) {
      card.appendChild(el("p", { text: "Online features are disabled in Settings, so submitting is unavailable. You can review what an export would look like below." }));
    }
    if (prep.author && prep.author.name) {
      card.appendChild(el("p", { html: "You'll appear as <strong>" + prep.author.name +
        "</strong>" + (prep.author.sponsor ? " ♥" : "") +
        " <span style='opacity:.7'>(a stable pseudonym; sponsors can pick a custom name)</span>" }));
    }

    // The preview is a LIVE render of this dashboard, and it travels with the
    // submission: reviewers see it while the template is pending, and it
    // becomes the public catalog card once approved. Whatever the widgets are
    // showing right now (calendar entries, addresses, sensor readings) is in
    // that image, so say so plainly right next to it rather than burying it.
    var notice = el("div", {});
    notice.style.cssText = "background:rgba(184,134,11,.10);border:1px solid rgba(184,134,11,.45);" +
      "border-radius:8px;padding:10px 12px;margin:10px 0 4px";
    notice.appendChild(el("strong", { text: "This image gets shared, exactly as you see it" }));
    notice.appendChild(el("div", {
      text: "It's a live render of your dashboard right now. Reviewers see it while " +
        "your template is pending, and it becomes the public preview once approved.",
      style: "margin-top:2px",
    }));
    notice.appendChild(el("div", {
      text: "If it's showing anything you'd rather not publish (names, addresses, " +
        "appointments, sensor readings), duplicate this dashboard, swap in " +
        "placeholder values, and share the copy instead.",
      style: "margin-top:6px",
    }));
    card.appendChild(notice);

    var img = el("img", { alt: "Preview" });
    img.style.cssText = "max-width:100%;border:1px solid var(--t-border,#ddd);border-radius:8px;margin:8px 0";
    img.src = PREFIX + "/pages/canvas/c/" + canvasId + "/preview.png?t=" + Date.now();
    card.appendChild(img);

    // Quality warnings from the headless render report.
    var q = prep.quality || {};
    var warnings = [];
    if (q.available) {
      if ((q.overflow || []).length) warnings.push((q.overflow.length) + " element(s) overflow their box");
      if ((q.icon_invalid || []).length) warnings.push((q.icon_invalid.length) + " icon reference(s) resolve to no glyph");
      if ((q.tap_invalid || []).length) warnings.push((q.tap_invalid.length) + " touch region(s) would not fire");
    }
    (prep.lint && prep.lint.warnings || []).forEach(function (w) {
      warnings.push("possible secret at " + w.where + " (" + w.rule + ")");
    });
    if (warnings.length) {
      var warn = el("div", {});
      warn.style.cssText = "background:rgba(184,134,11,.08);border:1px solid rgba(184,134,11,.4);" +
        "border-radius:8px;padding:10px 12px;margin:8px 0";
      warn.appendChild(el("strong", { text: "Worth checking before you submit:" }));
      warnings.forEach(function (w) { warn.appendChild(el("div", { text: "• " + w })); });
      card.appendChild(warn);
    }

    if ((prep.redactions || []).length) {
      var red = el("details", {});
      red.appendChild(el("summary", { text: "Removed before sharing (" + prep.redactions.length + ")" }));
      prep.redactions.forEach(function (r) { red.appendChild(el("div", { text: "• " + r })); });
      red.style.cssText = "margin:8px 0;opacity:.85";
      card.appendChild(red);
    }

    var form = el("div", {});
    form.style.cssText = "display:flex;flex-direction:column;gap:8px;margin-top:8px";
    var title = el("input", { type: "text", maxlength: "80", placeholder: "Template title" });
    title.value = (prep.template && prep.template.title) || "";
    var desc = el("textarea", { rows: "3", maxlength: "1000", placeholder: "What is this dashboard for? What hardware does it look best on?" });
    var tags = el("input", { type: "text", placeholder: "tags, comma separated (e.g. weather, 6-color)" });
    [title, desc, tags].forEach(function (i) {
      i.style.cssText = "padding:8px 10px;border:1px solid var(--t-border,#ccc);border-radius:8px;font:inherit;background:transparent;color:inherit";
      form.appendChild(i);
    });

    // Suggested inputs: each row = include? + label + secret marker.
    var inputRows = [];
    if ((prep.inputs_suggested || []).length) {
      form.appendChild(el("strong", { text: "Install-time questions (from removed values)" }));
      prep.inputs_suggested.forEach(function (s) {
        var row = el("div", {});
        row.style.cssText = "display:flex;gap:8px;align-items:center";
        var include = el("input", { type: "checkbox" });
        include.checked = true;
        var label = el("input", { type: "text" });
        label.value = s.label || s.name;
        label.style.cssText = "flex:1;padding:6px 8px;border:1px solid var(--t-border,#ccc);border-radius:6px;font:inherit;background:transparent;color:inherit";
        row.appendChild(include);
        row.appendChild(label);
        if (s.secret) row.appendChild(el("span", { text: "secret", title: "Installer's value is entered masked and stays on their machine" }));
        form.appendChild(row);
        inputRows.push({ spec: s, include: include, label: label });
      });
    }
    card.appendChild(form);

    var actions = el("div", {});
    actions.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:14px";
    var cancel = el("button", { class: "pbtn", text: "Cancel" });
    cancel.addEventListener("click", close);
    actions.appendChild(cancel);
    var submit = el("button", { class: "pbtn accent", text: "Submit for review" });
    if (!prep.online) submit.disabled = true;
    var status = el("div", {});
    status.style.cssText = "margin-top:8px;opacity:.85";
    submit.addEventListener("click", function () {
      if (!window.confirm(
        "Submit this dashboard as a community template?\n\n" +
        "The preview image above is sent with it: reviewers see it now, and it " +
        "becomes public once approved. Check it isn't showing anything private."
      )) return;
      submit.disabled = true;
      status.textContent = "Submitting…";
      var chosen = inputRows.filter(function (r) { return r.include.checked; }).map(function (r) {
        var spec = r.spec;
        return {
          name: spec.name, label: r.label.value || spec.name, type: spec.type,
          secret: !!spec.secret, required: !!spec.required, default: spec.default || "",
          choices: spec.choices || [], targets: spec.targets,
        };
      });
      postJson("/panels/c/" + canvasId + "/share/submit", {
        title: title.value,
        description: desc.value,
        tags: tags.value.split(",").map(function (t) { return t.trim(); }).filter(Boolean),
        inputs: chosen,
      })
        .then(function (r) {
          if (!r.ok) {
            status.textContent = "Couldn't submit: " + (r.body.error || "unknown error");
            submit.disabled = false;
            return;
          }
          status.textContent = "Submitted. It's pending review and will appear in Browse → Templates once approved. You'll show as " +
            ((r.body.author && r.body.author.name) || "your pseudonym") + ".";
          submit.remove();
          cancel.textContent = "Done";
        })
        .catch(function (err) {
          status.textContent = "Couldn't submit: " + ((err && err.message) || "unknown error");
          submit.disabled = false;
        });
    });
    actions.appendChild(submit);
    card.appendChild(actions);
    card.appendChild(status);
    document.body.appendChild(overlay);
  }

  btn.addEventListener("click", function () {
    btn.classList.add("is-disabled");
    postJson("/panels/c/" + canvasId + "/share/prepare")
      .then(function (r) {
        btn.classList.remove("is-disabled");
        if (!r.ok) throw new Error(r.body.error || "the server returned an error");
        open(r.body);
      })
      .catch(function (err) {
        btn.classList.remove("is-disabled");
        alert("Couldn't prepare the share dialog: " + ((err && err.message) || "unknown error"));
      });
  });
})();
