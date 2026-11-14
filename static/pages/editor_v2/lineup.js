// Dashboard editor v2: step 5 lists rotating and scheduled lineups together,
// with the quick schedule dialog (#280). Joining a lineup on another display asks first,
// then adds that display (POST /pages/<id>/lineups/<deck>/join).
(function () {
  "use strict";
  const EV2 = window.EV2;
  if (!EV2) return;
  const root = EV2.root;
  const box = root.querySelector("[data-ev2-lineups]");
  const base = EV2.cfg.lineupsUrl;
  const usingTest = EV2.cfg.usingTest === "1";
  // Lineups that hold only this dashboard: leaving deletes them.
  let emptied = [];
  try {
    emptied = JSON.parse(root.dataset.emptiedLineups || "[]");
  } catch {
    emptied = [];
  }

  if (box) {
    const pick = box.querySelector("[data-ev2-lpick]");
    const addRow = box.querySelector("[data-ev2-ladd-row]");
    const conf = box.querySelector("[data-ev2-lconfirm]");
    let pendingId = null;

    const showPick = (on) => {
      if (pick && addRow) {
        pick.hidden = !on;
        addRow.hidden = on;
      }
    };
    const showConfirm = (row) => {
      pendingId = row ? row.dataset.ev2Ladd : null;
      conf.hidden = !row;
      if (pick) pick.hidden = Boolean(row);
      if (addRow) addRow.hidden = true;
      if (!row) return;
      const where = row.dataset.where;
      conf.querySelector("[data-ev2-lc-title]").textContent =
        `“${row.dataset.name}” runs on ${where}.`;
      conf.querySelector("[data-ev2-lc-sub]").textContent =
        `This dashboard isn't set up for ${where} yet.`;
      conf.querySelector("[data-ev2-lc-body]").textContent =
        `Joining adds ${where} to its displays (step 1)` +
        (usingTest ? ", in place of the test display" : "") +
        ". The layout stretches to fit; check it in the preview.";
      conf.querySelector("[data-ev2-ljoin]").textContent = `Add ${where} and join`;
      conf.querySelector("[data-ev2-ljoin]").focus();
    };

    function join(id, addDevice) {
      return EV2.flush()
        .then(() =>
          EV2.post(
            `${base}/${encodeURIComponent(id)}/join`,
            { add_device: addDevice },
            {
              json: true,
              // 409: the lineup's display isn't on this dashboard yet; ask.
              quiet: (err) => err.status === 409,
            }
          )
        )
        .then(() => EV2.reload())
        .catch((err) => {
          if (err && err.status === 409) {
            const row = box.querySelector(`[data-ev2-ladd="${CSS.escape(id)}"]`);
            if (row) showConfirm(row);
          }
        });
    }

    box.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-ev2-lopen]")) {
        showPick(true);
        box.querySelector("[data-ev2-ladd]")?.focus();
        return;
      }
      if (ev.target.closest("[data-ev2-lclose]")) {
        showPick(false);
        box.querySelector("[data-ev2-lopen]")?.focus();
        return;
      }
      const row = ev.target.closest("[data-ev2-ladd]");
      if (row) {
        if (row.dataset.same === "1") join(row.dataset.ev2Ladd, false);
        else showConfirm(row);
        return;
      }
      if (ev.target.closest("[data-ev2-lcancel]")) {
        const back = pendingId && box.querySelector(`[data-ev2-ladd="${CSS.escape(pendingId)}"]`);
        showConfirm(null);
        back?.focus();
        return;
      }
      if (ev.target.closest("[data-ev2-ljoin]") && pendingId) {
        join(pendingId, true);
        return;
      }
      const rm = ev.target.closest("[data-ev2-lremove]");
      if (rm) {
        const name = rm.closest("li")?.querySelector("b")?.textContent || "this lineup";
        if (rm.dataset.deletes === "1" || emptied.includes(name)) {
          const message =
            rm.dataset.schedule === "1"
              ? `Remove ${name}? This deletes this dashboard's schedule.`
              : `${name} has no other dashboard, so leaving it deletes the lineup too. Leave and delete it?`;
          if (!window.confirm(message)) return;
        }
        EV2.flush()
          .then(() =>
            EV2.post(
              `${base}/${encodeURIComponent(rm.dataset.ev2Lremove)}/leave`,
              {},
              { json: true }
            )
          )
          .then(() => EV2.reload())
          .catch(() => {});
      }
    });
  }

  // ------------------------------------------------ schedule dialogs (#280)
  const dialogs = document.querySelectorAll("[data-schedule-dialog]");
  if (!dialogs.length || typeof HTMLDialogElement === "undefined") return;

  function noteFor(dialog, text, isError) {
    const note = dialog.querySelector("[data-schedule-dialog-note]");
    if (!note) return;
    note.textContent = text || "";
    note.classList.toggle("is-error", Boolean(isError));
    note.hidden = !text;
  }
  function openDialog(dialog) {
    if (dialog.open) return;
    noteFor(dialog, "");
    dialog.showModal();
    const name = dialog.querySelector('input[name="name"]');
    if (name) {
      name.focus();
      name.select();
    }
  }
  document.querySelectorAll("[data-open-schedule-dialog]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const dialog = document.getElementById(btn.dataset.openScheduleDialog);
      if (dialog) openDialog(dialog);
    });
  });
  dialogs.forEach((dialog) => {
    dialog
      .querySelectorAll("[data-schedule-dialog-close]")
      .forEach((btn) => btn.addEventListener("click", () => dialog.close()));
    dialog.addEventListener("click", (ev) => {
      if (ev.target === dialog) dialog.close();
    });
    const form = dialog.querySelector("form.schedule-form");
    if (!form) return;
    let cleared = false;
    form.addEventListener("submit", (ev) => {
      if (cleared || !EV2.pending()) return;
      ev.preventDefault();
      noteFor(dialog, "Saving the dashboard first...");
      window.tesseraeEditor.saveAll().then((ok) => {
        if (!ok) {
          noteFor(
            dialog,
            "The dashboard could not be saved. Fix that first, then save the schedule.",
            true
          );
          return;
        }
        cleared = true;
        if (typeof form.requestSubmit === "function") form.requestSubmit();
        else form.submit();
      });
    });
  });
  // A failed submit comes back with ?schedule_error=1&schedule_edit=<id>.
  const params = new URLSearchParams(location.search);
  if (params.get("schedule_error")) {
    const dialog = document.getElementById(
      "schedule-dialog-" + (params.get("schedule_edit") || "new")
    );
    if (dialog) openDialog(dialog);
    params.delete("schedule_error");
    params.delete("schedule_edit");
    const rest = params.toString();
    history.replaceState(
      null,
      "",
      location.pathname + (rest ? "?" + rest : "") + (EV2.isNew ? "#lineup" : "#schedules")
    );
  }
})();
