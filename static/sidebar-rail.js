/*
 * Paper's sidebar folds to a 64px icon rail on desktop (paper.css). The
 * head script in _base.html puts the saved state on <html> before paint;
 * this wires the toggle, the rail's tooltips and the dropdowns, which open
 * as flyouts to the right while the sidebar is folded. Loaded only in
 * Paper, outside the locked shell.
 */
(function () {
  const root = document.documentElement;
  const btn = document.querySelector("[data-sidebar-toggle]");
  if (!btn || root.dataset.ui !== "paper") return;
  const desk = window.matchMedia("(min-width: 901px)");
  const isFolded = () => root.classList.contains("sidebar-collapsed") && desk.matches;
  const flyouts = Array.from(document.querySelectorAll(".topnav .nav-dropdown"));
  let slideTimer = null;
  function closeFlyouts(except) {
    flyouts.forEach((d) => {
      if (d !== except) d.open = false;
    });
  }
  function paint() {
    const folded = root.classList.contains("sidebar-collapsed");
    const word = folded ? "Expand sidebar" : "Collapse sidebar";
    btn.setAttribute("aria-expanded", folded ? "false" : "true");
    btn.setAttribute("aria-label", word);
    btn.title = word;
    btn.querySelector(".sidebar-toggle-label").textContent = folded ? "Expand" : "Collapse";
  }
  // One tooltip, placed to the right of whichever rail item is hovered
  // or focused. Fixed, so the sidebar's own scrolling can't clip it.
  const tip = document.createElement("div");
  tip.className = "rail-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.appendChild(tip);
  const ITEMS =
    ".topnav > a, .topnav .nav-dropdown > summary, .topnav .theme-toggle--drawer, " +
    ".topnav .ui-switch--nav, .topbar > .topbar-discovered--topbar, .topbar-optin button, " +
    ".topbar-restart, .sidebar-toggle";
  function hideTip() {
    tip.hidden = true;
  }
  function showTip(el) {
    if (!isFolded()) return;
    const d = el.closest(".nav-dropdown");
    if (d && d.open) return;
    const words = (el.title || el.textContent || el.getAttribute("aria-label") || "")
      .trim()
      .replace(/\s+/g, " ");
    if (!words) return;
    tip.textContent = words;
    const r = el.getBoundingClientRect();
    tip.style.left = r.right + 10 + "px";
    tip.style.top = r.top + r.height / 2 + "px";
    tip.hidden = false;
  }
  document.querySelectorAll(ITEMS).forEach((el) => {
    el.addEventListener("mouseenter", () => showTip(el));
    el.addEventListener("mouseleave", hideTip);
    el.addEventListener("focus", () => {
      if (el.matches(":focus-visible")) showTip(el);
    });
    el.addEventListener("blur", hideTip);
  });
  // Dropdowns: inline while the sidebar is open, a flyout to the right
  // of the rail while it is folded.
  flyouts.forEach((d) => {
    const summary = d.querySelector("summary");
    const panel = d.querySelector(".nav-dropdown-panel");
    d.addEventListener("toggle", () => {
      if (!d.open || !isFolded()) return;
      hideTip();
      closeFlyouts(d);
      const r = summary.getBoundingClientRect();
      const room = window.innerHeight - 12;
      panel.style.setProperty(
        "--flyout-top",
        Math.max(8, Math.min(r.top, room - panel.offsetHeight)) + "px"
      );
    });
  });
  document.addEventListener(
    "mousedown",
    (ev) => {
      if (!isFolded()) return;
      flyouts.forEach((d) => {
        if (d.open && !d.contains(ev.target)) d.open = false;
      });
    },
    true
  );
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || !isFolded()) return;
    const open = flyouts.find((d) => d.open);
    if (open) {
      open.open = false;
      open.querySelector("summary").focus();
    }
  });
  // The dropdown holding the current page is drawn open (_base.html): on
  // the open sidebar that shows where you are; folded, it would be a
  // flyout hanging open, so it starts closed and opens again on unfolding.
  const current = flyouts.filter((d) => d.classList.contains("is-active"));
  if (isFolded()) closeFlyouts();
  btn.addEventListener("click", () => {
    const folded = !root.classList.contains("sidebar-collapsed");
    clearTimeout(slideTimer);
    root.classList.add("sidebar-sliding");
    slideTimer = setTimeout(() => root.classList.remove("sidebar-sliding"), 260);
    root.classList.toggle("sidebar-collapsed", folded);
    try {
      if (folded) localStorage.setItem("tesserae-sidebar", "collapsed");
      else localStorage.removeItem("tesserae-sidebar");
    } catch {
      /* not remembered */
    }
    closeFlyouts();
    if (!folded)
      current.forEach((d) => {
        d.open = true;
      });
    hideTip();
    paint();
  });
  window.addEventListener("resize", hideTip);
  window.addEventListener("scroll", hideTip, { passive: true });
  paint();
})();
