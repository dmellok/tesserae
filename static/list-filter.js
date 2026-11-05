/*
 * List filter + sort: one helper for every table and list in the admin.
 *
 * Markup contract (everything is opt-in through data attributes):
 *
 *   [data-lf="<key>"]            the root: holds the toolbar and the list.
 *                                <key> names the list in localStorage.
 *   [data-lf-item]               a row / card. Filters and sorting act on these.
 *     data-lf-text="..."         what the search box matches (lowercase);
 *                                falls back to the item's text.
 *     data-lf-<name>="a b"       space-separated values a filter matches.
 *     data-sort-<key>="..."      the value a sort key compares.
 *     data-lf-uid="..."          items sharing a uid count once (an item
 *                                listed in several groups).
 *   [data-lf-tail]               an element that belongs to the item before
 *                                it (an opened row's detail): it moves and
 *                                hides with that item.
 *   [data-lf-group]              a group of items; hidden while none of its
 *                                items match. A [data-lf-group-count] inside
 *                                it counts the group's matches, worded like
 *                                [data-lf-count].
 *   input[data-lf-search]        the search box.
 *   select[data-lf-filter="n"]   a filter over data-lf-n; "" means all.
 *   select[data-lf-sort]         a Sort menu: option values "key:asc" /
 *                                "key:desc", "" for the list's own order;
 *                                data-type="num" on an option compares
 *                                numerically.
 *   [data-lf-sort-key="k"]       a sortable column head (gets a button).
 *     data-lf-sort-type="num"    compare numerically (default: text).
 *   [data-lf-count]              "N <noun>" (data-lf-noun, data-lf-plural),
 *                                "N of M <noun>" while filtered.
 *   [data-lf-empty]              shown when nothing matches.
 *
 * Sorting reorders items inside their own parent, so grouped lists sort
 * within each group and groups keep their order. A column head cycles
 * ascending, descending, then back to the list's own order. The last sort
 * is remembered per list. Rows added later (live logs, refreshed regions)
 * are picked up automatically.
 *
 * The root fires "lf:change" after every pass, with detail
 * { items, visible, unique } for pages that keep their own counters.
 */
(function () {
  "use strict";

  const PREFIX = "tesserae-lf:";

  function load(key) {
    if (!key) return null;
    try {
      return JSON.parse(window.localStorage.getItem(PREFIX + key) || "null");
    } catch {
      return null;
    }
  }
  function save(key, value) {
    if (!key) return;
    try {
      if (value) window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
      else window.localStorage.removeItem(PREFIX + key);
    } catch {
      /* storage unavailable: the sort just isn't remembered */
    }
  }

  const collator =
    window.Intl && Intl.Collator
      ? new Intl.Collator(undefined, { numeric: true, sensitivity: "base" })
      : null;
  function compareText(a, b) {
    return collator ? collator.compare(a, b) : a.localeCompare(b);
  }

  function plural(el, n) {
    const one = el.getAttribute("data-lf-noun") || "item";
    const many = el.getAttribute("data-lf-plural") || one + "s";
    return n === 1 ? one : many;
  }

  // "12 dashboards", or "3 of 12 dashboards" while a filter is on.
  function phrase(el, n, total, filtered) {
    const shown = filtered && n !== total ? n + " of " + total : String(n);
    return shown + " " + plural(el, filtered ? total : n);
  }

  function init(root) {
    if (root.__lf) return root.__lf;
    const key = root.getAttribute("data-lf") || "";
    // Controls belong to this root, not to a list nested inside it.
    function own(sel) {
      return Array.prototype.filter.call(root.querySelectorAll(sel), function (el) {
        return el.closest("[data-lf]") === root;
      });
    }
    const search = own("[data-lf-search]")[0] || null;
    const filters = own("select[data-lf-filter]");
    const sortMenu = own("select[data-lf-sort]")[0] || null;
    const heads = own("[data-lf-sort-key]");
    const counts = own("[data-lf-count]");
    const empties = own("[data-lf-empty]");

    let seq = 0;
    let sort = null; // { key, dir, type }

    function items() {
      return own("[data-lf-item]");
    }
    function index(all, force) {
      all.forEach(function (it, i) {
        if (force || it.__lfIdx === undefined) it.__lfIdx = force ? i : -++seq;
      });
    }
    function tails(item) {
      const out = [];
      let n = item.nextElementSibling;
      while (n && n.hasAttribute("data-lf-tail")) {
        out.push(n);
        n = n.nextElementSibling;
      }
      return out;
    }
    function text(item) {
      const t = item.getAttribute("data-lf-text");
      return (t !== null ? t : item.textContent || "").toLowerCase();
    }

    // ---- sort heads: wrap each label in a button with a caret ----------
    heads.forEach(function (h) {
      if (h.querySelector(".lf-sort-btn")) return;
      const label = (h.textContent || "").trim();
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "lf-sort-btn";
      btn.innerHTML =
        '<span class="lf-sort-label"></span><i class="ph ph-caret-up-down lf-caret" aria-hidden="true"></i>';
      btn.querySelector(".lf-sort-label").textContent = label;
      h.textContent = "";
      h.appendChild(btn);
      h.removeAttribute("aria-hidden");
      if (!h.matches("th")) h.setAttribute("role", "columnheader");
      h.classList.add("lf-sortable");
      btn.addEventListener("click", function () {
        const k = h.getAttribute("data-lf-sort-key");
        let next;
        if (!sort || sort.key !== k) next = { key: k, dir: "asc" };
        else if (sort.dir === "asc") next = { key: k, dir: "desc" };
        else next = null;
        if (next) next.type = h.getAttribute("data-lf-sort-type") || "text";
        setSort(next, true);
      });
    });

    function typeFromMenu(k) {
      if (!sortMenu) return null;
      const opt = Array.prototype.find.call(sortMenu.options, function (o) {
        return o.value.split(":")[0] === k;
      });
      return opt ? opt.getAttribute("data-type") || "text" : null;
    }
    function typeFromHead(k) {
      const h = heads.find(function (x) {
        return x.getAttribute("data-lf-sort-key") === k;
      });
      return h ? h.getAttribute("data-lf-sort-type") || "text" : null;
    }

    function paintSortControls() {
      heads.forEach(function (h) {
        const on = sort && sort.key === h.getAttribute("data-lf-sort-key");
        const state = on ? (sort.dir === "asc" ? "ascending" : "descending") : "none";
        h.setAttribute("aria-sort", state);
        h.classList.toggle("is-sorted", !!on);
        const caret = h.querySelector(".lf-caret");
        if (caret)
          caret.className =
            "ph lf-caret " +
            (on ? (sort.dir === "asc" ? "ph-caret-up" : "ph-caret-down") : "ph-caret-up-down");
        const btn = h.querySelector(".lf-sort-btn");
        if (btn) {
          const label = (h.querySelector(".lf-sort-label") || btn).textContent;
          btn.setAttribute(
            "aria-label",
            "Sort by " + label + (on ? (sort.dir === "asc" ? ", ascending" : ", descending") : "")
          );
        }
      });
      if (sortMenu) {
        const want = sort ? sort.key + ":" + sort.dir : "";
        const has = Array.prototype.some.call(sortMenu.options, function (o) {
          return o.value === want;
        });
        sortMenu.value = has ? want : "";
      }
    }

    function sortValue(item, s) {
      const raw = item.getAttribute("data-sort-" + s.key);
      if (s.type === "num") {
        const n = raw === null || raw === "" ? NaN : parseFloat(raw);
        return isNaN(n) ? null : n;
      }
      const t = raw !== null ? raw : "";
      return t === "" ? null : t;
    }

    function reorder() {
      const all = items();
      // One pass per parent: groups sort inside themselves.
      const parents = [];
      all.forEach(function (it) {
        if (parents.indexOf(it.parentNode) === -1) parents.push(it.parentNode);
      });
      parents.forEach(function (parent) {
        const mine = all.filter(function (it) {
          return it.parentNode === parent;
        });
        const blocks = mine.map(function (it) {
          return { it: it, tails: tails(it) };
        });
        const last = blocks[blocks.length - 1];
        const anchor = (last.tails.length ? last.tails[last.tails.length - 1] : last.it)
          .nextSibling;
        const sorted = blocks.slice().sort(function (a, b) {
          if (sort) {
            const va = sortValue(a.it, sort);
            const vb = sortValue(b.it, sort);
            if (va === null && vb !== null) return 1; // blanks last either way
            if (vb === null && va !== null) return -1;
            if (va !== null && vb !== null) {
              const c = sort.type === "num" ? va - vb : compareText(va, vb);
              if (c) return sort.dir === "desc" ? -c : c;
            }
          }
          return a.it.__lfIdx - b.it.__lfIdx;
        });
        const same = sorted.every(function (b, i) {
          return b === blocks[i];
        });
        if (same) return;
        sorted.forEach(function (b) {
          parent.insertBefore(b.it, anchor);
          b.tails.forEach(function (t) {
            parent.insertBefore(t, anchor);
          });
        });
      });
    }

    function filterPass() {
      const q = search ? search.value.trim().toLowerCase() : "";
      const all = items();
      const visible = [];
      const uids = {};
      let unique = 0;
      const totalUids = {};
      let totalUnique = 0;
      all.forEach(function (it) {
        let show = !q || text(it).indexOf(q) !== -1;
        if (show) {
          for (let i = 0; i < filters.length; i++) {
            const f = filters[i];
            const v = f.value;
            if (!v) continue;
            const have = (
              it.getAttribute("data-lf-" + f.getAttribute("data-lf-filter")) || ""
            ).split(/\s+/);
            if (have.indexOf(v) === -1) {
              show = false;
              break;
            }
          }
        }
        const uid = it.getAttribute("data-lf-uid");
        if (uid === null || !totalUids[uid]) {
          totalUnique += 1;
          if (uid !== null) totalUids[uid] = true;
        }
        if (show) {
          visible.push(it);
          if (uid === null || !uids[uid]) {
            unique += 1;
            if (uid !== null) uids[uid] = true;
          }
        }
        it.toggleAttribute("data-lf-out", !show);
        tails(it).forEach(function (t) {
          t.toggleAttribute("data-lf-out", !show);
        });
      });
      const filtered =
        !!q ||
        filters.some(function (f) {
          return !!f.value;
        });
      own("[data-lf-group]").forEach(function (g) {
        const vis = visible.filter(function (it) {
          return g.contains(it);
        }).length;
        const has = all.filter(function (it) {
          return g.contains(it);
        }).length;
        g.toggleAttribute("data-lf-out", has > 0 && !vis);
        g.querySelectorAll("[data-lf-group-count]").forEach(function (c) {
          if (c.closest("[data-lf-group]") === g) c.textContent = phrase(c, vis, has, filtered);
        });
      });
      root.classList.toggle("lf-is-filtered", filtered);
      root.classList.toggle("lf-is-searching", !!q);
      counts.forEach(function (c) {
        c.textContent = phrase(c, unique, totalUnique, filtered);
      });
      empties.forEach(function (e) {
        e.hidden = !(all.length && !visible.length);
      });
      root.dispatchEvent(
        new CustomEvent("lf:change", {
          bubbles: true,
          detail: {
            items: all,
            visible: visible,
            unique: unique,
            total: totalUnique,
            filtered: filtered,
          },
        })
      );
    }

    function apply() {
      reorder();
      filterPass();
    }

    function setSort(next, remember) {
      sort = next;
      if (remember) save(key + ":sort", sort ? { key: sort.key, dir: sort.dir } : null);
      paintSortControls();
      apply();
    }

    index(items(), true);
    const stored = load(key + ":sort");
    if (stored && stored.key) {
      const type = typeFromHead(stored.key) || typeFromMenu(stored.key);
      if (type) sort = { key: stored.key, dir: stored.dir === "desc" ? "desc" : "asc", type: type };
    }
    paintSortControls();

    if (search) search.addEventListener("input", filterPass);
    filters.forEach(function (f) {
      f.addEventListener("change", filterPass);
    });
    if (sortMenu) {
      sortMenu.addEventListener("change", function () {
        const v = sortMenu.value;
        if (!v) {
          setSort(null, true);
          return;
        }
        const parts = v.split(":");
        const opt = sortMenu.options[sortMenu.selectedIndex];
        setSort(
          {
            key: parts[0],
            dir: parts[1] === "desc" ? "desc" : "asc",
            type: (opt && opt.getAttribute("data-type")) || "text",
          },
          true
        );
      });
    }

    // Rows that arrive later (a live log, a refreshed region) join in.
    if (window.MutationObserver) {
      new MutationObserver(function (records) {
        let fresh = false;
        for (let i = 0; i < records.length && !fresh; i++) {
          const added = records[i].addedNodes;
          for (let j = 0; j < added.length; j++) {
            const n = added[j];
            if (n.nodeType !== 1) continue;
            if (
              (n.matches("[data-lf-item]") && n.__lfIdx === undefined) ||
              Array.prototype.some.call(n.querySelectorAll("[data-lf-item]"), function (x) {
                return x.__lfIdx === undefined;
              })
            ) {
              fresh = true;
              break;
            }
          }
        }
        if (!fresh) return;
        // Observer callbacks run before the next paint, so new rows never
        // show unfiltered. Moving known rows doesn't come back here.
        index(items(), !sort);
        apply();
      }).observe(root, { childList: true, subtree: true });
    }

    apply();
    const api = {
      apply: apply,
      refresh: function () {
        index(items(), !sort);
        apply();
      },
      root: root,
    };
    root.__lf = api;
    return api;
  }

  function initAll(scope) {
    Array.prototype.forEach.call((scope || document).querySelectorAll("[data-lf]"), init);
  }

  window.TesseraeListFilter = { init: init, initAll: initAll };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initAll();
    });
  } else {
    initAll();
  }
})();
