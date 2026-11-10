/*
 * Paper's phone page bar. The top bar is sticky; once the page's title
 * has scrolled under it, <body> gets page-scrolled and paper.css swaps
 * the brand name for the title and the notice chips (the slot is in
 * _base.html). An IntersectionObserver on the page's first h1 does the
 * watching, with the top bar's height taken off the root so the swap
 * happens as the title passes under the bar, not the viewport edge.
 * Nothing moves in the page itself, so there is no jump at the
 * threshold. Loaded only in Paper, outside the locked shell; the class
 * is harmless on desktop, where the slot is never shown.
 */
(function () {
  if (document.documentElement.dataset.ui !== "paper") return;
  const slot = document.querySelector("[data-page-bar]");
  const title = document.querySelector("[data-page-bar-title]");
  const h1 = document.querySelector("main.page h1");
  if (!slot || !title || !h1 || !("IntersectionObserver" in window)) return;
  const copy = h1.cloneNode(true);
  copy.querySelectorAll("svg, i, .sr-only, .lu-sr-only").forEach((n) => n.remove());
  // A title you can edit in place (the lineup editor's name) is an input in
  // the h1: the bar shows its value, and follows it as it is typed.
  const field = h1.querySelector("input");
  const read = () =>
    copy.textContent.replace(/\s+/g, " ").trim() || (field ? field.value.trim() : "");
  const words = read();
  if (!words) return;
  title.textContent = words;
  if (field) field.addEventListener("input", () => (title.textContent = read() || words));
  const bar = document.querySelector(".topbar");
  const barHeight = () => Math.round(bar ? bar.getBoundingClientRect().height : 56);
  let watcher = null;
  function watch() {
    if (watcher) watcher.disconnect();
    const h = barHeight();
    watcher = new IntersectionObserver(
      (entries) => {
        const e = entries[0];
        const under = !e.isIntersecting && e.boundingClientRect.bottom <= h;
        document.body.classList.toggle("page-scrolled", under);
      },
      { rootMargin: "-" + h + "px 0px 0px 0px", threshold: 0 }
    );
    watcher.observe(h1);
  }
  watch();
  // The bar's height changes with the viewport (the classic nav wraps;
  // Paper's bar is fixed at 56px but the font may load late), so re-arm
  // on resize with the measured height.
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(watch, 150);
  });
})();
