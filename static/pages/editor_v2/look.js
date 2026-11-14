// Dashboard editor v2: step 4, the dashboard-wide look (theme, style, font,
// matting, padding, corners, custom CSS), autosaved through the page update
// route, plus the warning that lists cells overriding it.
(function () {
  "use strict";
  const EV2 = window.EV2;
  if (!EV2) return;
  const root = EV2.root;
  const form = root.querySelector("[data-ev2-look-form]");
  const box = root.querySelector("[data-ev2-ovbox]");
  const sum = root.querySelector('[data-ev2-sum="look"]');
  if (!form) return;

  form.addEventListener("submit", (ev) => ev.preventDefault());

  function onEdit(ev) {
    const el = ev.target;
    if (!el.name && !el.dataset.ev2Pair) return;
    const delay = EV2.debounceDelay(el);
    if (ev.type === "input" && delay < 0) return;
    if (ev.type === "change" && el.type === "range") return;
    EV2.saveForm(form, ev.type === "change" ? 0 : delay).catch(() => {});
  }
  form.addEventListener("input", onEdit);
  form.addEventListener("change", onEdit);
  EV2.afterSave(form, () => refreshSummary());

  // Matting swatches set the colour input.
  const color = form.querySelector("[data-ev2-matc]");
  function paintSwatches() {
    const v = (color.value || "").toLowerCase();
    form.querySelectorAll("[data-ev2-mat]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.ev2Mat === v ? "true" : "false"));
  }
  form.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-ev2-mat]");
    if (!b) return;
    color.value = b.dataset.ev2Mat;
    paintSwatches();
    EV2.saveForm(form, 0).catch(() => {});
  });
  color?.addEventListener("input", paintSwatches);

  function selectedText(sel) {
    const o = sel && sel.selectedOptions && sel.selectedOptions[0];
    return o ? o.textContent.trim() : "";
  }
  function overriders() {
    return EV2.widgets
      ? EV2.widgets
          .rows()
          .filter((li) => li.dataset.widget && li.dataset.n)
          .map((li) => {
            const own = [...li.querySelectorAll("[data-ev2-ov]")]
              .filter((s) => s.value)
              .map((s) => ({ label: "own " + s.dataset.ev2Ov, field: s.name, resettable: true }));
            if (li.querySelector("[data-ev2-pad-on]")?.checked) {
              own.push({ label: "own padding", field: "padding_override", resettable: true });
            }
            const rules = JSON.parse(li.dataset.ev2LookRules || "{}");
            if (rules.always) own.push({ label: rules.always, field: "", resettable: false });
            (rules.options || []).forEach((rule) => {
              const fields = [...li.querySelectorAll(`[name="${CSS.escape("opt_" + rule.name)}"]`)];
              const field = fields[0];
              if (!field || field.closest("[data-show-if][hidden]")) return;
              let value = field.value;
              if (rule.type === "boolean") value = field.checked;
              else if (rule.type === "number" || rule.type === "slider") value = value === "" ? "" : Number(value);
              else if (rule.type === "multiselect") {
                value = field.multiple
                  ? [...field.selectedOptions].map((o) => o.value).sort()
                  : fields.filter((f) => f.checked).map((f) => f.value).sort();
              }
              if (JSON.stringify(value) !== JSON.stringify(rule.default)) {
                own.push({ label: rule.message, field: "opt_" + rule.name, resettable: false });
              }
            });
            return { li, own };
          })
          .filter((x) => x.own.length)
      : [];
  }

  function refreshSummary() {
    if (!sum) return;
    const theme = selectedText(form.querySelector('[name="theme"]'));
    const style = selectedText(form.querySelector('[name="style"]')).split(" · ")[0];
    const gap = form.querySelector('[name="gap"]').value;
    const base = `${theme} · ${style} · padding ${gap}`;
    const n = overriders().length;
    sum.classList.toggle("warn", n > 0);
    sum.replaceChildren();
    if (n) {
      const i = document.createElement("i");
      i.className = "ph ph-warning";
      i.setAttribute("aria-hidden", "true");
      sum.append(i, ` ${n} widget${n === 1 ? "" : "s"} override${n === 1 ? "s" : ""} it · `);
    }
    sum.append(base);
  }

  function refreshOverrides() {
    const o = overriders();
    refreshSummary();
    if (!box) return;
    box.hidden = !o.length;
    box.replaceChildren();
    if (!o.length) return;
    const i = document.createElement("i");
    i.className = "ph ph-warning";
    i.setAttribute("aria-hidden", "true");
    const wrap = document.createElement("div");
    const b = document.createElement("b");
    b.textContent = o.length === 1 ? "1 widget has its own look settings." : `${o.length} widgets have their own look settings.`;
    wrap.append(b, " Review these settings if the dashboard look has no effect.");
    const ul = document.createElement("ul");
    o.forEach(({ li, own }) => {
      const row = document.createElement("li");
      const link = document.createElement("button");
      link.type = "button";
      link.className = "ev2-link ev2-link--quiet";
      link.dataset.ev2Showov = li.dataset.cellId;
      link.textContent = li.dataset.item || li.dataset.widget;
      const what = document.createElement("span");
      own.forEach((reason, n) => {
        if (n) what.append(", ");
        const setting = document.createElement("button");
        setting.type = "button";
        setting.className = "ev2-link ev2-link--quiet";
        setting.dataset.ev2Showov = li.dataset.cellId;
        setting.dataset.ev2Setting = reason.field;
        setting.textContent = reason.label;
        what.append(setting);
      });
      row.append(link, what);
      ul.append(row);
    });
    wrap.append(ul);
    if (o.some(({ own }) => own.some((r) => !r.resettable))) {
      const note = document.createElement("p");
      note.textContent = "Review widget options using their links. Resetting cell overrides leaves those options unchanged.";
      wrap.append(note);
    }
    if (o.some(({ own }) => own.some((r) => r.resettable))) {
      const btns = document.createElement("div");
      btns.className = "ev2-row-btns";
      const reset = document.createElement("button");
      reset.type = "button";
      reset.className = "ev2-link";
      reset.dataset.ev2Resetov = "";
      reset.textContent = "Reset cell look overrides";
      btns.append(reset);
      wrap.append(btns);
    }
    box.append(i, wrap);
  }

  box?.addEventListener("click", async (ev) => {
    const show = ev.target.closest("[data-ev2-showov]");
    if (show) {
      const id = show.dataset.ev2Showov;
      EV2.widgets.openRow(id, { scroll: true, block: "center" });
      const li = EV2.widgets.rowFor(id);
      const name = show.dataset.ev2Setting;
      const field = name === "padding_override"
        ? li.querySelector("[data-ev2-pad-choice]")
        : name ? li.querySelector(`[name="${CSS.escape(name)}"]`) : null;
      if (field) {
        for (let parent = field.parentElement; parent && parent !== li; parent = parent.parentElement) {
          if (parent.tagName === "DETAILS") parent.open = true;
        }
      }
      (field || li.querySelector("[data-ev2-row]"))?.focus({ preventScroll: true });
      return;
    }
    if (ev.target.closest("[data-ev2-resetov]")) {
      const saves = overriders().filter(({ own }) => own.some((r) => r.resettable)).map(({ li }) => {
        li.querySelectorAll("[data-ev2-ov]").forEach((s) => (s.value = ""));
        const padding = li.querySelector("[data-ev2-pad-on]");
        if (padding) {
          padding.checked = false;
          li.querySelector("[data-ev2-pad-choice]").value = "";
          li.querySelector("[data-ev2-pad-custom]").hidden = true;
        }
        const f = li.querySelector("[data-ev2-cell-form]");
        EV2.widgets.refreshSums && EV2.widgets.refreshSums(f);
        return EV2.saveForm(f, 0);
      });
      refreshOverrides();
      try {
        await Promise.all(saves);
      } catch {
        return; // The save indicator keeps the failed edit available to retry.
      }
      // The button that was pressed is gone with the box: land on the first
      // look control, and say what happened.
      form.querySelector('[name="theme"]')?.focus({ preventScroll: true });
      EV2.announce && EV2.announce("Cell look overrides reset. Widget options kept.");
    }
  });

  EV2.look = { refreshOverrides, refreshSummary };
  const cells = root.querySelector("[data-ev2-cells]");
  ["input", "change"].forEach((name) => {
    cells?.addEventListener(name, () => queueMicrotask(refreshOverrides));
  });
  EV2.ready(refreshOverrides);
})();
