// Visionect frames on the Firmware page: the version chooser, and a row that
// keeps up with a flash while one runs. Everything is delegated from document,
// because a row is replaced as a flash progresses.
(function () {
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-fw-open]");
    if (opener) {
      const dialog = document.getElementById(opener.getAttribute("data-fw-open"));
      if (dialog && dialog.showModal) dialog.showModal();
      return;
    }
    const closer = event.target.closest("[data-fw-close]");
    if (closer) closer.closest("dialog").close();
  });

  // A version's notes show once it is chosen.
  document.addEventListener("change", (event) => {
    if (!event.target.matches("[data-fw-version]")) return;
    const dialog = event.target.closest("dialog");
    dialog.querySelectorAll("[data-fw-notes]").forEach((notes) => {
      notes.hidden = notes.getAttribute("data-fw-notes") !== event.target.value;
    });
  });

  async function poll() {
    if (document.hidden || !document.querySelector("[data-fw-flash-active]")) return;
    try {
      const resp = await fetch(window.location.href, { credentials: "same-origin" });
      if (!resp.ok) return;
      const fresh = new DOMParser().parseFromString(await resp.text(), "text/html");
      document.querySelectorAll("[data-fw-visionect-row]").forEach((row) => {
        const id = row.getAttribute("data-fw-visionect-row");
        const next = fresh.querySelector('[data-fw-visionect-row="' + CSS.escape(id) + '"]');
        if (next) row.replaceWith(document.importNode(next, true));
      });
    } catch {
      /* offline or restarting: try again next tick */
    }
  }

  setInterval(poll, 2000);
})();
