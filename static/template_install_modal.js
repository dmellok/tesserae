/* Community-template install modal.
 *
 * Exposed as ``window.tesseraeTemplateInstallModal(entry)`` and driven by
 * the Browse catalog page, which owns the browsing side of templates now
 * (they're one type in the one catalog). This file is only the install
 * dialog: missing-requirements notice deep-linking into the widget
 * install flow, the template's declared inputs rendered as a form
 * (server-resolved so an entity question becomes a picker over the
 * installer's own Home Assistant entities), then POST install ->
 * redirect to the new dashboard in the panels editor. Also carries the
 * takedown-report affordance.
 */
(function () {
  "use strict";

  // The app's script root, empty outside a subpath deployment. Under the
  // Home Assistant App every one of these URLs sits beneath
  // /api/hassio_ingress/<token>/, so a bare "/plugins/..." misses
  // Tesserae. (``preview_url`` is already absolutised server-side.)
  var PREFIX = window.TESSERAE_URL_PREFIX || "";

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      node.appendChild(c);
    });
    return node;
  }

  function authorLine(author) {
    var wrap = el("span", { class: "tpl-modal-author" });
    wrap.appendChild(document.createTextNode("by " + ((author && author.name) || "unknown")));
    if (author && author.sponsor) {
      wrap.appendChild(
        el("i", {
          class: "ph-fill ph-heart tpl-modal-sponsor",
          title: "Tesserae sponsor",
        })
      );
    }
    return wrap;
  }

  function installModal(entry) {
    var overlay = el("div", { class: "tpl-modal-scrim" });
    var card = el("div", {
      class: "tpl-modal",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": "Install " + (entry.title || "template"),
    });
    overlay.appendChild(card);
    overlay.addEventListener("click", function (ev) {
      if (ev.target === overlay) close();
    });

    function close() {
      overlay.remove();
      document.removeEventListener("keydown", onKey, true);
    }
    function onKey(ev) {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        close();
      }
    }
    document.addEventListener("keydown", onKey, true);

    card.appendChild(el("h3", { text: entry.title, class: "tpl-modal-title" }));
    card.appendChild(authorLine(entry.author));
    if (entry.preview_url) {
      card.appendChild(el("img", { src: entry.preview_url, alt: "", class: "tpl-modal-preview" }));
    }
    if (entry.description) card.appendChild(el("p", { text: entry.description }));

    var missing = entry.missing_requires || [];
    if (missing.length) {
      var note = el("div", { class: "tpl-modal-note" });
      note.appendChild(el("div", { text: "Needs these catalog items first:" }));
      missing.forEach(function (id) {
        note.appendChild(
          el("a", {
            href: PREFIX + "/plugins/browse?q=" + encodeURIComponent(id),
            text: id,
          })
        );
      });
      card.appendChild(note);
    }

    // The install questions are rendered SERVER-side and injected here:
    // each input resolves against this install's widget option schemas,
    // so an entity question becomes a picker over the installer's own
    // Home Assistant entities instead of a text box they have to guess
    // at. The author cannot know what is valid on someone else's system.
    var form = el("form", { class: "tpl-modal-form" });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
    });
    if (Number(entry.inputs || 0) > 0) {
      form.appendChild(
        el("div", { text: "This template needs a few details from you:", class: "tpl-modal-ask" })
      );
    }
    var fields = el("div", {});
    fields.appendChild(el("div", { text: "Loading…", class: "tpl-modal-muted" }));
    form.appendChild(fields);
    form.appendChild(el("input", { type: "hidden", name: "slug", value: entry.slug }));
    card.appendChild(form);

    fetch(PREFIX + "/plugins/templates/" + encodeURIComponent(entry.slug) + "/inputs")
      .then(function (resp) {
        if (!resp.ok) throw new Error("inputs");
        return resp.text();
      })
      .then(function (html) {
        fields.innerHTML = html;
        // Light up the injected controls: location search, multiselects,
        // sliders, preset numbers, and entity overrides all bind by data
        // attribute and expose a rebind hook for dynamically added markup.
        var c = window.tesseraeComponents;
        if (c) {
          if (c.attachLocationSearch) c.attachLocationSearch(fields);
          if (c.attachMultiSelect) c.attachMultiSelect(fields);
          if (c.attachSliders) c.attachSliders(fields);
          if (c.attachPresetNumbers) c.attachPresetNumbers(fields);
        }
        if (typeof window.tesseraeEntityOverridesBindAll === "function") {
          window.tesseraeEntityOverridesBindAll();
        }
      })
      .catch(function () {
        fields.textContent = "";
        fields.appendChild(
          el("div", {
            text: "Couldn't load this template's questions. Installing will use its defaults.",
            class: "tpl-modal-muted",
          })
        );
      });

    var status = el("div", { class: "tpl-modal-status", role: "status" });
    var actions = el("div", { class: "tpl-modal-actions" });
    var cancel = el("button", { class: "dx-btn-ghost-sm", type: "button", text: "Cancel" });
    cancel.addEventListener("click", close);
    var go = el("button", {
      class: "dx-btn-sm",
      type: "button",
      text: missing.length ? "Install anyway" : "Install",
    });
    go.addEventListener("click", function () {
      go.disabled = true;
      status.textContent = "Installing…";
      fetch(PREFIX + "/plugins/templates/install", { method: "POST", body: new FormData(form) })
        .then(function (resp) {
          return resp.json().then(function (b) {
            return { ok: resp.ok, body: b };
          });
        })
        .then(function (r) {
          if (!r.ok) {
            status.textContent = r.body.error || "Install failed";
            go.disabled = false;
            return;
          }
          status.textContent = "Installed. Opening the editor…";
          // page_url comes from url_for() and already carries the script root.
          location.href = r.body.page_url || PREFIX + "/pages/canvas/c/" + r.body.page_id;
        })
        .catch(function () {
          status.textContent = "Install failed: network error";
          go.disabled = false;
        });
    });
    // Takedown request. Anyone can file one, including the template's own
    // author (this is how they pull their own work back); it goes to the
    // same human review queue rather than acting directly.
    var report = el("button", {
      class: "dx-btn-ghost-sm tpl-modal-report",
      type: "button",
      text: "Report",
      title: "Ask the moderators to take this template down",
    });
    report.addEventListener("click", function () {
      var reason = window.prompt(
        'Report "' +
          entry.title +
          '" for takedown?\n\n' +
          "Say briefly what's wrong (shows private data, doesn't work, not yours, " +
          "inappropriate). This goes to the moderators, not the author."
      );
      if (reason === null) return;
      report.disabled = true;
      status.textContent = "Sending report…";
      fetch(PREFIX + "/plugins/templates/report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug: entry.slug, reason: reason }),
      })
        .then(function (resp) {
          return resp.json().then(function (b) {
            return { ok: resp.ok, body: b };
          });
        })
        .then(function (r) {
          status.textContent = r.ok
            ? "Report sent. A moderator will take a look."
            : r.body.error || "Couldn't send the report";
          if (!r.ok) report.disabled = false;
        })
        .catch(function () {
          status.textContent = "Couldn't send the report: network error";
          report.disabled = false;
        });
    });
    actions.appendChild(report);
    actions.appendChild(cancel);
    actions.appendChild(go);
    card.appendChild(actions);
    card.appendChild(status);
    document.body.appendChild(overlay);
    go.focus();
  }

  window.tesseraeTemplateInstallModal = installModal;
})();
