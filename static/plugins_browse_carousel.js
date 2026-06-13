/**
 * Marketplace browse-page screenshot carousels.
 *
 * Wires the explicit prev/next arrow buttons and dot indicators on
 * every ``[data-carousel]`` instance the server rendered. CSS handles
 * touch swipe and keyboard arrow-key navigation natively via
 * ``scroll-snap-type: x mandatory`` on the track, so this script is
 * intentionally small: it only handles the buttons, syncs the active
 * dot to whichever slide the scroll position centred on, and honours
 * ``prefers-reduced-motion`` by skipping the smooth-scroll animation.
 *
 * Cards with a single screenshot (the common case for every catalog
 * entry today) never get the ``[data-carousel]`` wrapper, so this
 * script effectively no-ops for them.
 */

(function () {
  "use strict";

  /** True when the user's OS reports a reduced-motion preference. */
  function prefersReducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (_err) {
      return false;
    }
  }

  /** Smooth scroll behaviour, or instant when reduced-motion is on. */
  function scrollBehavior() {
    return prefersReducedMotion() ? "instant" : "smooth";
  }

  /**
   * Set up one carousel root: a ``[data-carousel]`` element with one
   * ``[data-carousel-track]`` child holding the slides, optional
   * prev/next buttons, and one dot per slide.
   */
  function init(root) {
    const track = root.querySelector("[data-carousel-track]");
    if (!track) return;
    const slides = Array.from(track.children);
    if (slides.length <= 1) {
      // Defensive: the server shouldn't emit a carousel wrapper
      // unless there's more than one slide, but if some future
      // bug ships an N=1 carousel we bail rather than render
      // useless controls.
      return;
    }
    const prev = root.querySelector("[data-carousel-prev]");
    const next = root.querySelector("[data-carousel-next]");
    const dots = Array.from(root.querySelectorAll("[data-carousel-dot]"));

    function pageWidth() {
      return track.clientWidth;
    }

    function scrollToIndex(idx) {
      const target = slides[idx];
      if (!target) return;
      track.scrollTo({ left: target.offsetLeft, behavior: scrollBehavior() });
    }

    if (prev) {
      prev.addEventListener("click", () => {
        track.scrollBy({ left: -pageWidth(), behavior: scrollBehavior() });
      });
    }
    if (next) {
      next.addEventListener("click", () => {
        track.scrollBy({ left: pageWidth(), behavior: scrollBehavior() });
      });
    }
    dots.forEach((dot) => {
      dot.addEventListener("click", () => {
        const idx = Number.parseInt(dot.dataset.slideIndex, 10);
        if (Number.isFinite(idx)) scrollToIndex(idx);
      });
    });

    // Sync ``aria-current`` on the dot whose slide the scroll
    // position is centred on. rAF-debounced so we don't thrash
    // during a swipe; the active dot updates after each frame at
    // most. The active slide is whichever's center is closest to
    // the track's viewport center.
    let raf = 0;
    function syncActive() {
      raf = 0;
      const center = track.scrollLeft + track.clientWidth / 2;
      let bestIdx = 0;
      let bestDist = Infinity;
      slides.forEach((slide, idx) => {
        const slideCenter = slide.offsetLeft + slide.clientWidth / 2;
        const dist = Math.abs(slideCenter - center);
        if (dist < bestDist) {
          bestDist = dist;
          bestIdx = idx;
        }
      });
      dots.forEach((dot, idx) => {
        if (idx === bestIdx) dot.setAttribute("aria-current", "true");
        else dot.removeAttribute("aria-current");
      });
    }
    track.addEventListener(
      "scroll",
      () => {
        if (!raf) raf = window.requestAnimationFrame(syncActive);
      },
      { passive: true }
    );
  }

  function bootstrap() {
    document.querySelectorAll("[data-carousel]").forEach(init);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap, { once: true });
  } else {
    bootstrap();
  }
})();
