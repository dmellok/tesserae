/* "Show it on this display?" prompt, shared by every screen that can put a
 * dashboard on a display it is not bound to (deck editor, setup wizard,
 * schedule forms). A dashboard is delivered only to the displays it is bound
 * to, so adding a mismatched one would leave a step that can never render.
 *
 *   TesseraeBindPrompt.ask({ display, pages }) -> Promise<choice | null>
 *
 * display: {id, name, width, height}; pages: [{id, name, bound_to:[{id, name,
 * width, height}]}] (the shape the server returns as `unbound_pages`).
 *
 * The dialog lists the target display (always ticked: the add needs it) and
 * every display the dashboards are bound to now (ticked; untick to take them
 * off). It resolves to {mode, modes, keep}: keep maps page id -> the current
 * display ids it stays on; modes maps page id -> "move" (keeps none), "also"
 * (keeps all) or "some"; mode is the shared one ("some" when they differ).
 * null means cancelled. TesseraeBindPrompt.fields(choice, id)
 * turns a choice into the form fields the server reads (bind_pages,
 * bind_also, bind_keep). Native <dialog>: Escape cancels, focus is trapped
 * while open and restored to the opener.
 */
(function () {
  "use strict";

  const size = (d) => (d && d.width && d.height ? `${d.width}×${d.height}` : "");
  const join = (items) =>
    items.length < 3
      ? items.join(" and ")
      : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  /* A small outline in the display's own proportions (a square-ish dashed
     one when the size is unknown), so portrait and landscape read at a glance. */
  function shape(d) {
    const box = el("span", "bind-row-shape");
    box.setAttribute("aria-hidden", "true");
    const s = el("span", "bind-row-shape-frame");
    if (d.width && d.height) {
      const r = d.width / d.height;
      s.style.width = `${Math.round(r >= 1 ? 22 : 22 * r)}px`;
      s.style.height = `${Math.round(r >= 1 ? 22 / r : 22)}px`;
    } else {
      s.classList.add("is-unknown");
    }
    box.append(s);
    return box;
  }

  /* The displays to list: the target first, then each display any of the
     dashboards is bound to now, in the order they come. */
  function rowsOf(display, pages) {
    const rows = [{ d: display, target: true, pages: [] }];
    const seen = new Map([[display.id, rows[0]]]);
    pages.forEach((p) =>
      (p.bound_to || []).forEach((b) => {
        let row = seen.get(b.id);
        if (!row) {
          row = { d: b, target: false, pages: [] };
          seen.set(b.id, row);
          rows.push(row);
        }
        row.pages.push(p);
      })
    );
    return rows;
  }

  function ask({ display, pages }) {
    return new Promise((resolve) => {
      if (!pages || !pages.length || typeof HTMLDialogElement === "undefined") {
        resolve(null);
        return;
      }
      const opener = document.activeElement;
      const one = pages.length === 1;
      const what = one ? `“${pages[0].name}”` : `${pages.length} dashboards`;
      const subject = one ? pages[0].name : `These ${pages.length} dashboards`;
      const rows = rowsOf(display, pages);
      const others = rows.filter((r) => !r.target);

      const dlg = el("dialog", "wizard-dialog bind-dialog");
      dlg.setAttribute("aria-labelledby", "bind-dialog-title");
      dlg.setAttribute("aria-describedby", "bind-dialog-lead");

      const head = el("div", "wizard-head");
      const icon = el("span", "wizard-head-icon");
      icon.setAttribute("aria-hidden", "true");
      icon.innerHTML = '<i class="ph-bold ph-link"></i>';
      const headText = el("div", "wizard-head-text");
      const h = el("h2", "", `Show ${what} on ${display.name}?`);
      h.id = "bind-dialog-title";
      const lead = el(
        "p",
        "bind-dialog-lead",
        others.length
          ? `A dashboard only shows on the displays it's on. Untick any it should come off.`
          : `A dashboard only shows on the displays it's on.`
      );
      lead.id = "bind-dialog-lead";
      headText.append(h, lead);
      head.append(icon, headText);

      const body = el("div", "wizard-body bind-dialog-body");
      const set = el("fieldset", "bind-dialog-set");
      set.append(el("legend", "bind-dialog-legend", "Displays"));
      const list = el("div", "bind-dialog-rows");
      const boxes = [];
      rows.forEach((row, i) => {
        const id = `bind-row-${i}`;
        const label = el("label", "bind-row" + (row.target ? " is-target" : ""));
        label.htmlFor = id;
        const box = el("input");
        box.type = "checkbox";
        box.id = id;
        box.value = row.d.id;
        box.checked = true;
        const text = el("span", "bind-row-text");
        const name = el("span", "bind-row-name", row.d.name);
        name.title = row.d.name;
        name.id = `${id}-name`;
        const meta = el("span", "bind-row-meta");
        meta.id = `${id}-meta`;
        const bits = [];
        if (size(row.d)) bits.push(size(row.d));
        if (row.target) bits.push("this lineup's display");
        else if (!one) bits.push(`has ${join(row.pages.map((p) => p.name))}`);
        meta.textContent = bits.join(" · ");
        const state = el("span", "bind-row-state");
        state.id = `${id}-state`;
        text.append(name, meta);
        box.setAttribute("aria-labelledby", `${id}-name`);
        box.setAttribute("aria-describedby", `${id}-meta ${id}-state`);
        if (row.target) {
          // Stays ticked: this is what the add is for. aria-disabled (not
          // disabled) keeps it focusable, so it is announced and can explain.
          box.setAttribute("aria-disabled", "true");
          state.textContent = "Needed";
        }
        label.append(box, shape(row.d), text, state);
        list.append(label);
        boxes.push({ row, box, label, state });
      });
      set.append(list);
      const hint = el("p", "bind-dialog-hint");
      hint.id = "bind-dialog-hint";
      hint.setAttribute("aria-live", "polite");
      const summary = el("p", "bind-dialog-summary");
      summary.id = "bind-dialog-summary";
      summary.setAttribute("aria-live", "polite");
      body.append(set, hint, summary);

      // Reuse the old fact: on another size the dashboard is scaled to fit.
      const target = size(display);
      const odd = pages.filter((p) =>
        (p.bound_to || []).some((b) => size(b) && target && size(b) !== target)
      );
      if (odd.length) {
        body.append(
          el(
            "p",
            "bind-dialog-note",
            one
              ? `${display.name} is ${target}, so ${pages[0].name} shows scaled there.`
              : `${display.name} is ${target}; dashboards made for another size show scaled there.`
          )
        );
      }

      const foot = el("div", "wizard-footer");
      const cancel = el("button", "ghost bind-dialog-cancel", "Cancel");
      cancel.type = "button";
      const ok = el("button", "primary bind-dialog-ok");
      ok.type = "button";
      foot.append(cancel, el("span", "wizard-footer-spacer"), ok);

      dlg.append(head, body, foot);
      document.body.append(dlg);

      const kept = () => boxes.filter((b) => !b.row.target && b.box.checked).map((b) => b.row);
      const dropped = () => boxes.filter((b) => !b.row.target && !b.box.checked).map((b) => b.row);
      const valid = () => boxes.every((b) => !b.row.target || b.box.checked);

      function paint() {
        const on = [display.name, ...kept().map((r) => r.d.name)];
        const off = dropped().map((r) => r.d.name);
        let line = `${subject} will be on ${join(on)}.`;
        if (off.length) line += ` Removed from ${join(off)}.`;
        summary.textContent = line;
        boxes.forEach((b) => {
          if (b.row.target) return;
          b.label.classList.toggle("is-off", !b.box.checked);
          b.state.textContent = b.box.checked ? "" : "Comes off";
        });
        if (!others.length) ok.textContent = `Show on ${display.name}`;
        else if (!kept().length) ok.textContent = `Move to ${display.name}`;
        else ok.textContent = `Show on ${plural(on.length, "display", "displays")}`;
        ok.disabled = !valid();
      }

      boxes.forEach((b) => {
        if (b.row.target) {
          b.box.addEventListener("click", (e) => {
            e.preventDefault();
            hint.textContent = `${display.name} stays ticked: the lineup runs there. To leave ${
              one ? pages[0].name : "them"
            } out, cancel.`;
            b.label.classList.remove("is-nudged");
            void b.label.offsetWidth; // restart the nudge
            b.label.classList.add("is-nudged");
          });
        } else {
          b.box.addEventListener("change", () => {
            hint.textContent = "";
            paint();
          });
        }
      });
      paint();

      let answer = null;
      cancel.addEventListener("click", () => dlg.close());
      ok.addEventListener("click", () => {
        if (!valid()) return;
        const ids = new Set(kept().map((r) => r.d.id));
        const keep = {};
        const modes = {};
        pages.forEach((p) => {
          const had = (p.bound_to || []).map((b) => b.id);
          keep[p.id] = had.filter((id) => ids.has(id));
          modes[p.id] = !keep[p.id].length
            ? "move"
            : keep[p.id].length === had.length
              ? "also"
              : "some";
        });
        const all = Object.values(modes);
        const mode = all.every((m) => m === all[0]) ? all[0] : "some";
        answer = { mode, modes, keep };
        dlg.close();
      });
      dlg.addEventListener("close", () => {
        dlg.remove();
        if (opener && typeof opener.focus === "function" && document.contains(opener)) {
          opener.focus({ preventScroll: true });
        }
        resolve(answer);
      });
      // Keep Tab inside: a modal <dialog> alone lets it run out to the page's
      // end and the browser's own controls.
      dlg.addEventListener("keydown", (e) => {
        if (e.key !== "Tab") return;
        const stops = [...dlg.querySelectorAll("input, button")].filter((n) => !n.disabled);
        const first = stops[0];
        const last = stops[stops.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      });
      // A click on the backdrop (the dialog element itself) cancels too.
      dlg.addEventListener("click", (e) => {
        if (e.target === dlg) dlg.close();
      });
      dlg.showModal();
      ok.focus();
    });
  }

  /* The form fields for one page's answer: always bind_pages; bind_also when
     it keeps every display it had; bind_keep ({"page": [display ids]}) when
     it keeps only some. A plain "move" is bind_pages alone. */
  function fields(choice, pageId) {
    if (!choice) return [];
    if (typeof choice === "string") choice = { mode: choice };
    const mode = (choice.modes && choice.modes[pageId]) || choice.mode;
    const out = [["bind_pages", pageId]];
    if (mode === "also") out.push(["bind_also", pageId]);
    else if (mode === "some") {
      const ids = (choice.keep && choice.keep[pageId]) || [];
      out.push(["bind_keep", JSON.stringify({ [pageId]: ids })]);
    }
    return out;
  }

  window.TesseraeBindPrompt = { ask, fields };
})();
