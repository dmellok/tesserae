/* "Bind to this display?" prompt, shared by every screen that can put a
 * dashboard on a display it is not bound to (deck editor, setup wizard,
 * schedule forms). A dashboard is delivered only to the displays it is bound
 * to, so adding a mismatched one would leave a step that can never render.
 *
 *   TesseraeBindPrompt.ask({ display, pages }) -> Promise<"move" | "also" | null>
 *
 * display: {id, name, width, height}; pages: [{id, name, bound_to:[{id, name,
 * width, height}]}] (the shape the server returns as `unbound_pages`).
 * "move" binds the dashboards to the display only, "also" keeps their other
 * display too, null means cancelled. Native <dialog>: Escape cancels, focus is
 * trapped while open and restored to the opener.
 */
(function () {
  "use strict";

  const size = (d) => (d && d.width && d.height ? `${d.width}×${d.height}` : "");
  const label = (d) => (size(d) ? `${d.name} (${size(d)})` : d.name);
  const join = (items) =>
    items.length < 3
      ? items.join(" and ")
      : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function copy(display, pages) {
    const title =
      pages.length === 1
        ? `Bind “${pages[0].name}” to ${display.name}?`
        : `Bind ${pages.length} dashboards to ${display.name}?`;
    const mismatch = pages.some((p) =>
      (p.bound_to || []).some((b) => size(b) && size(display) && size(b) !== size(display))
    );
    let lead;
    if (pages.length === 1) {
      const where = join((pages[0].bound_to || []).map(label));
      lead = `It is currently bound to ${where || "another display"}`;
      lead += mismatch && size(display) ? ` and ${display.name} is ${size(display)}.` : ".";
    } else {
      lead = `${display.name} can only show dashboards bound to it.`;
    }
    const note = mismatch
      ? "It will show scaled or letterboxed until you make a version for this display."
      : "";
    return { title, lead, note };
  }

  function ask({ display, pages }) {
    return new Promise((resolve) => {
      if (!pages || !pages.length || typeof HTMLDialogElement === "undefined") {
        resolve(null);
        return;
      }
      const opener = document.activeElement;
      const { title, lead, note } = copy(display, pages);
      const dlg = el("dialog", "wizard-dialog bind-dialog");
      dlg.setAttribute("aria-labelledby", "bind-dialog-title");
      dlg.setAttribute("aria-describedby", "bind-dialog-body");

      const head = el("div", "wizard-head");
      const icon = el("span", "wizard-head-icon");
      icon.setAttribute("aria-hidden", "true");
      icon.innerHTML = '<i class="ph-bold ph-link"></i>';
      const headText = el("div", "wizard-head-text");
      const h = el("h2", "", title);
      h.id = "bind-dialog-title";
      headText.append(h);
      head.append(icon, headText);

      const body = el("div", "wizard-body bind-dialog-body");
      body.id = "bind-dialog-body";
      body.append(el("p", "bind-dialog-lead", lead));
      if (pages.length > 1) {
        const ul = el("ul", "bind-dialog-list");
        pages.forEach((p) => {
          const li = el("li");
          li.append(
            el("strong", "", p.name),
            el("span", "", ` bound to ${join((p.bound_to || []).map(label)) || "another display"}`)
          );
          ul.append(li);
        });
        body.append(ul);
      }
      if (note) body.append(el("p", "bind-dialog-note", note));
      body.append(
        el(
          "p",
          "bind-dialog-note",
          "Bind to both keeps the other display too. Cancel leaves everything as it is."
        )
      );

      const foot = el("div", "wizard-footer");
      const cancel = el("button", "ghost", "Cancel");
      cancel.type = "button";
      const both = el("button", "", "Bind to both");
      both.type = "button";
      const move = el("button", "primary", `Bind to ${display.name}`);
      move.type = "button";
      foot.append(cancel, el("span", "wizard-footer-spacer"), both, move);

      dlg.append(head, body, foot);
      document.body.append(dlg);

      let answer = null;
      cancel.addEventListener("click", () => dlg.close());
      both.addEventListener("click", () => {
        answer = "also";
        dlg.close();
      });
      move.addEventListener("click", () => {
        answer = "move";
        dlg.close();
      });
      dlg.addEventListener("close", () => {
        dlg.remove();
        if (opener && typeof opener.focus === "function" && document.contains(opener)) {
          opener.focus({ preventScroll: true });
        }
        resolve(answer);
      });
      // A click on the backdrop (the dialog element itself) cancels too.
      dlg.addEventListener("click", (e) => {
        if (e.target === dlg) dlg.close();
      });
      dlg.showModal();
      move.focus();
    });
  }

  window.TesseraeBindPrompt = { ask };
})();
