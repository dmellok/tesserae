// The Comics admin page's niceties. Everything on the page works without
// this file: each change is a plain form that posts and comes back.
(function () {
  'use strict';

  // The preview shows one screen at a time; the picker swaps it in place
  // (without this file its Show button reloads the page on that screen).
  var pick = document.querySelector('[data-screen-pick]');
  if (pick) {
    pick.addEventListener('change', function () {
      document.querySelectorAll('.cx-screen[data-screen]').forEach(function (fig) {
        fig.hidden = fig.getAttribute('data-screen') !== pick.value;
      });
    });
  }

  // Ask before a strip is removed.
  document.querySelectorAll('button[data-confirm]').forEach(function (btn) {
    btn.addEventListener('click', function (ev) {
      if (!window.confirm(btn.getAttribute('data-confirm'))) ev.preventDefault();
    });
  });

  // The log shows open: start it on its newest lines.
  function tail() {
    document.querySelectorAll('.cx-log[data-log-tail]').forEach(function (log) {
      log.scrollTop = log.scrollHeight;
    });
  }
  tail();
  window.addEventListener('load', tail);
})();
