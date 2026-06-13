// Variables textarea, click-to-insert variable chips.
//
// Pairs with a textarea inside [data-variables-textarea]: the chip
// row beneath the textarea carries [data-variables-picker] with a
// data-target pointing at the textarea's id. Each chip is a
// <button data-variable-value="{path}">. Clicking the chip drops
// that path at the textarea's current selection, with the cursor
// placed immediately after the inserted text so the next chip
// click stacks naturally.
//
// Falls back to append-at-end if the textarea has lost focus and
// the browser cleared the selection range. Fires a synthetic
// 'input' event on the textarea so the editor's existing
// auto-save listener picks the change up like any other edit.
(function () {
  "use strict";

  function insertAtCursor(textarea, text) {
    if (textarea.disabled || textarea.readOnly) return;
    const start = typeof textarea.selectionStart === "number" ? textarea.selectionStart : textarea.value.length;
    const end = typeof textarea.selectionEnd === "number" ? textarea.selectionEnd : textarea.value.length;
    const before = textarea.value.slice(0, start);
    const after = textarea.value.slice(end);
    textarea.value = before + text + after;
    const caret = start + text.length;
    try {
      textarea.setSelectionRange(caret, caret);
    } catch (e) {
      // Some textareas (e.g. type=hidden via display:none) throw.
      // Already inserted the text; the caret position is best-effort.
    }
    // Make sure the editor's change/input listeners react.
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new Event("change", { bubbles: true }));
    textarea.focus();
  }

  function bindOne(picker) {
    const targetId = picker.getAttribute("data-target");
    if (!targetId) return;
    picker.addEventListener("click", (ev) => {
      const chip = ev.target.closest("[data-variable-value]");
      if (!chip) return;
      // Don't submit any enclosing form when the chip is clicked.
      ev.preventDefault();
      const textarea = document.getElementById(targetId);
      if (!textarea) return;
      insertAtCursor(textarea, chip.getAttribute("data-variable-value") || "");
    });
  }

  function bindAll() {
    document.querySelectorAll("[data-variables-picker]").forEach(bindOne);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindAll);
  } else {
    bindAll();
  }
})();
