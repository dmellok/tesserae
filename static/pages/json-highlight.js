/* Tesserae in-house JSON syntax highlighter.
   ~50 lines, no deps, covers the shapes Tesserae actually surfaces:
   webhook example calls, raw event payloads, schedule/rotation
   conditions JSON. Output is wrapped in <span class="dx-code-*">
   tokens that style via the --t-code-* CSS variables.

   Usage:
     import { highlightJson } from '/static/pages/json-highlight.js';
     codeEl.innerHTML = highlightJson(rawJsonString);

   Or call ``highlightAll()`` once on DOMContentLoaded to walk every
   ``<pre class="dx-code" data-json>`` block on the page; the
   block's textContent stays the source of truth so a server-side
   change re-renders without any JS round-trip. */

const TOKEN_RE = new RegExp(
  [
    '("(?:\\\\.|[^"\\\\])*")(\\s*:)?', // string (possibly followed by colon -> key)
    '\\b(true|false|null)\\b',          // keyword
    '(-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)', // number
    '([{}\\[\\],])',                    // punctuation
  ].join('|'),
  'g',
);

function escape(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function highlightJson(raw) {
  if (raw == null) return '';
  return escape(String(raw)).replace(
    TOKEN_RE,
    function (match, str, colon, kw, num, punct) {
      if (str) {
        // If followed by a colon this is a key, else a value string.
        if (colon) return '<span class="dx-code-key">' + str + '</span><span class="dx-code-punct">' + colon + '</span>';
        return '<span class="dx-code-str">' + str + '</span>';
      }
      if (kw) return '<span class="dx-code-kw">' + kw + '</span>';
      if (num) return '<span class="dx-code-num">' + num + '</span>';
      if (punct) return '<span class="dx-code-punct">' + punct + '</span>';
      return match;
    },
  );
}

export function highlightAll(root) {
  const scope = root || document;
  scope.querySelectorAll('pre.dx-code[data-json]').forEach(function (el) {
    el.innerHTML = highlightJson(el.textContent || '');
  });
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { highlightAll(); });
  } else {
    highlightAll();
  }
}
