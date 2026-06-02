// pages/layout_editor.js
//
// Interactive layout editor for the page editor. Renders each cell as
// a positioned <div>, attaches drag handles to shared edges, and shows
// insert affordances on outer perimeters.
//
// Conventions:
// * All cell coords are stored in PANEL pixels (matches the persistent
//   model). The board scales via a CSS transform so 1 panel-pixel maps
//   to whatever the board's width allows.
// * Mutations bundle into a single POST to /pages/<id>/cells/batch so
//   resize (which touches >= 2 cells) is atomic.
// * After a structural change (insert / delete) we re-render the whole
//   board from the server's response — no DOM patching gymnastics.

(function () {
  const root = document.querySelector("[data-layout-editor]");
  if (!root) return;

  const panelW = Number(root.dataset.panelW);
  const panelH = Number(root.dataset.panelH);
  const batchUrl = root.dataset.batchUrl;
  const board = root.querySelector("[data-layout-board]");
  const pageId = root.dataset.pageId;

  // Persist the surrounding <details class="custom-layout"> open state
  // across reloads — insert / delete trigger a full reload to refresh
  // the per-cell forms, and re-collapsing the editor every time would
  // make those flows annoying.
  const details = root.closest("details.custom-layout");
  if (details) {
    const storageKey = `tesserae:custom-layout-open:${pageId}`;
    try {
      if (sessionStorage.getItem(storageKey) === "1") details.open = true;
    } catch {}
    details.addEventListener("toggle", () => {
      try {
        sessionStorage.setItem(storageKey, details.open ? "1" : "0");
      } catch {}
    });
  }

  let cells = JSON.parse(root.dataset.cells || "[]").map((c) => ({
    id: c.id,
    x: c.x,
    y: c.y,
    w: c.w,
    h: c.h,
    plugin: c.plugin || null,
  }));

  // ---------------------------------------------------------------
  // Geometry helpers
  // ---------------------------------------------------------------
  const FREEFORM_SNAP = 8; // px on panel scale — keeps drags from going subpixel

  // Snap-to-grid editing aid. The dashed N×M overlay on the board
  // makes it easy to size cells consistently — every drag rounds to
  // the nearest gridline. State lives in the toggle/inputs at the
  // bottom of the editor; reading it lazily on each drag lets the
  // user retune the grid without picking up the cell first.
  const snapState = {
    enabled: false,
    cols: 12,
    rows: 8,
    storageKey: `tesserae:layout-snap:${pageId}`,
  };

  function snapStep(axis) {
    if (!snapState.enabled) return FREEFORM_SNAP;
    const n = axis === "x" ? snapState.cols : snapState.rows;
    const total = axis === "x" ? panelW : panelH;
    return Math.max(1, Math.round(total / Math.max(2, n)));
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function rangesOverlap(a0, a1, b0, b1) {
    return a0 < b1 && b0 < a1;
  }

  // For a horizontal edge at y, find groups of cells above + below that
  // share a contiguous x range. Returns array of {axis, coord, above, below, x0, x1}.
  function findSharedEdges() {
    const edges = [];

    // Horizontal edges (cell tops / bottoms)
    const ys = new Set();
    cells.forEach((c) => {
      ys.add(c.y);
      ys.add(c.y + c.h);
    });
    for (const y of ys) {
      if (y === 0 || y === panelH) continue;
      const above = cells.filter((c) => c.y + c.h === y);
      const below = cells.filter((c) => c.y === y);
      if (above.length === 0 || below.length === 0) continue;
      // Both sides must collectively cover the SAME contiguous x range.
      const ax0 = Math.min(...above.map((c) => c.x));
      const ax1 = Math.max(...above.map((c) => c.x + c.w));
      const bx0 = Math.min(...below.map((c) => c.x));
      const bx1 = Math.max(...below.map((c) => c.x + c.w));
      if (ax0 !== bx0 || ax1 !== bx1) continue;
      edges.push({ axis: "h", coord: y, above, below, a0: ax0, a1: ax1 });
    }

    // Vertical edges (cell lefts / rights)
    const xs = new Set();
    cells.forEach((c) => {
      xs.add(c.x);
      xs.add(c.x + c.w);
    });
    for (const x of xs) {
      if (x === 0 || x === panelW) continue;
      const left = cells.filter((c) => c.x + c.w === x);
      const right = cells.filter((c) => c.x === x);
      if (left.length === 0 || right.length === 0) continue;
      const ly0 = Math.min(...left.map((c) => c.y));
      const ly1 = Math.max(...left.map((c) => c.y + c.h));
      const ry0 = Math.min(...right.map((c) => c.y));
      const ry1 = Math.max(...right.map((c) => c.y + c.h));
      if (ly0 !== ry0 || ly1 !== ry1) continue;
      edges.push({ axis: "v", coord: x, left, right, a0: ly0, a1: ly1 });
    }

    return edges;
  }

  // For drag, work out the min / max valid edge coord so cells stay at
  // least one freeform step wide. The grid-snap step would be too
  // coarse a floor — a 12-col grid on a 600px panel would refuse to
  // resize below 50px, which is fine in most cases but blocks 8px
  // freeform nudges when the user toggles snap off later.
  function edgeLimits(edge) {
    if (edge.axis === "h") {
      const minY = Math.max(...edge.above.map((c) => c.y)) + FREEFORM_SNAP;
      const maxY = Math.min(...edge.below.map((c) => c.y + c.h)) - FREEFORM_SNAP;
      return [minY, maxY];
    } else {
      const minX = Math.max(...edge.left.map((c) => c.x)) + FREEFORM_SNAP;
      const maxX = Math.min(...edge.right.map((c) => c.x + c.w)) - FREEFORM_SNAP;
      return [minX, maxX];
    }
  }

  // ---------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------
  function pxToPct(v, total) {
    return (v / total) * 100;
  }

  function render() {
    board.style.aspectRatio = `${panelW} / ${panelH}`;
    board.innerHTML = "";

    // Cells
    cells.forEach((c, idx) => {
      const el = document.createElement("div");
      el.className = "le-cell";
      el.dataset.cellId = c.id;
      el.dataset.idx = String(idx);
      el.style.left = pxToPct(c.x, panelW) + "%";
      el.style.top = pxToPct(c.y, panelH) + "%";
      el.style.width = pxToPct(c.w, panelW) + "%";
      el.style.height = pxToPct(c.h, panelH) + "%";
      el.innerHTML = `
        <span class="le-cell-label">${idx + 1}${c.plugin ? " · " + c.plugin : ""}</span>
        <button type="button" class="le-cell-delete" data-delete-cell aria-label="Delete cell ${idx + 1}">
          <i class="ph ph-x"></i>
        </button>
        <button type="button" class="le-insert le-insert--top"    data-insert="top"    aria-label="Insert above"></button>
        <button type="button" class="le-insert le-insert--right"  data-insert="right"  aria-label="Insert to the right"></button>
        <button type="button" class="le-insert le-insert--bottom" data-insert="bottom" aria-label="Insert below"></button>
        <button type="button" class="le-insert le-insert--left"   data-insert="left"   aria-label="Insert to the left"></button>
      `;
      board.appendChild(el);
    });

    // Shared-edge resize handles
    const edges = findSharedEdges();
    edges.forEach((edge) => {
      const handle = document.createElement("div");
      handle.className = "le-edge le-edge--" + edge.axis;
      if (edge.axis === "h") {
        handle.style.top = pxToPct(edge.coord, panelH) + "%";
        handle.style.left = pxToPct(edge.a0, panelW) + "%";
        handle.style.width = pxToPct(edge.a1 - edge.a0, panelW) + "%";
      } else {
        handle.style.left = pxToPct(edge.coord, panelW) + "%";
        handle.style.top = pxToPct(edge.a0, panelH) + "%";
        handle.style.height = pxToPct(edge.a1 - edge.a0, panelH) + "%";
      }
      handle.addEventListener("pointerdown", (e) => beginEdgeDrag(e, edge));
      board.appendChild(handle);
    });
  }

  // ---------------------------------------------------------------
  // Persistence
  // ---------------------------------------------------------------
  async function postBatch(payload, opts = {}) {
    const res = await fetch(batchUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    if (body.cells) {
      cells = body.cells.map((c) => ({
        id: c.id, x: c.x, y: c.y, w: c.w, h: c.h, plugin: c.plugin || null,
      }));
    }
    if (opts.reload) {
      // Structural change — full reload so the per-cell forms refresh.
      window.location.reload();
    } else {
      render();
      refreshPreview();
    }
  }

  // Kick every preview iframe to pick up new geometry. Multi-device
  // pages render one frame per distinct aspect ratio, so refresh them all.
  function refreshPreview() {
    document.querySelectorAll(".preview-frame iframe").forEach((iframe) => {
      const src = iframe.getAttribute("src");
      if (!src) return;
      // Bump a cache-buster so the browser definitely re-fetches.
      const base = src.split("&_=")[0];
      const sep = base.includes("?") ? "&" : "?";
      iframe.setAttribute("src", base + sep + "_=" + Date.now());
    });
  }

  // ---------------------------------------------------------------
  // Drag interactions
  // ---------------------------------------------------------------
  function beginEdgeDrag(evDown, edge) {
    evDown.preventDefault();
    // Pointer capture keeps the move/up events flowing even if the
    // finger / cursor slides off the handle. The capturing element
    // is the handle itself (the pointerdown target).
    const handleEl = evDown.currentTarget;
    if (handleEl && handleEl.setPointerCapture) {
      try { handleEl.setPointerCapture(evDown.pointerId); } catch {}
    }
    const rect = board.getBoundingClientRect();
    const [lo, hi] = edgeLimits(edge);

    document.body.style.cursor = edge.axis === "h" ? "ns-resize" : "ew-resize";
    board.classList.add("is-dragging");

    function onMove(ev) {
      let newCoord;
      if (edge.axis === "h") {
        const yPx = ev.clientY - rect.top;
        newCoord = Math.round((yPx / rect.height) * panelH);
      } else {
        const xPx = ev.clientX - rect.left;
        newCoord = Math.round((xPx / rect.width) * panelW);
      }
      // Snap to the active grid (or 8px freeform) and clamp to limits.
      const step = snapStep(edge.axis === "h" ? "y" : "x");
      newCoord = clamp(Math.round(newCoord / step) * step, lo, hi);
      applyEdgeAt(edge, newCoord);
      render();
    }

    function onUp() {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      document.body.style.cursor = "";
      board.classList.remove("is-dragging");
      const updates = collectChanges(edge);
      if (updates.length) {
        postBatch({ updates }).catch(reportErr);
      }
    }

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  // Mutate the in-memory cells: move the edge to newCoord, growing /
  // shrinking the appropriate side. The render() right after picks it up.
  function applyEdgeAt(edge, newCoord) {
    if (edge.axis === "h") {
      edge.above.forEach((c) => {
        const orig = cells.find((x) => x.id === c.id);
        if (orig) orig.h = newCoord - orig.y;
      });
      edge.below.forEach((c) => {
        const orig = cells.find((x) => x.id === c.id);
        if (orig) {
          const bottom = orig.y + orig.h;
          orig.y = newCoord;
          orig.h = bottom - newCoord;
        }
      });
      edge.coord = newCoord;
    } else {
      edge.left.forEach((c) => {
        const orig = cells.find((x) => x.id === c.id);
        if (orig) orig.w = newCoord - orig.x;
      });
      edge.right.forEach((c) => {
        const orig = cells.find((x) => x.id === c.id);
        if (orig) {
          const right = orig.x + orig.w;
          orig.x = newCoord;
          orig.w = right - newCoord;
        }
      });
      edge.coord = newCoord;
    }
  }

  function collectChanges(edge) {
    const affected = edge.axis === "h" ? [...edge.above, ...edge.below] : [...edge.left, ...edge.right];
    return affected.map((c) => {
      const live = cells.find((x) => x.id === c.id);
      return { id: live.id, x: live.x, y: live.y, w: live.w, h: live.h };
    });
  }

  // ---------------------------------------------------------------
  // Insert / delete
  // ---------------------------------------------------------------
  // Split the clicked cell in half along the perpendicular axis; the
  // new cell takes the side the user clicked.
  function insertOnCell(cell, direction) {
    const updates = [];
    const creates = [];
    const halfW = Math.max(FREEFORM_SNAP, Math.floor(cell.w / 2));
    const halfH = Math.max(FREEFORM_SNAP, Math.floor(cell.h / 2));

    if (direction === "left") {
      creates.push({ x: cell.x, y: cell.y, w: halfW, h: cell.h });
      updates.push({ id: cell.id, x: cell.x + halfW, y: cell.y, w: cell.w - halfW, h: cell.h });
    } else if (direction === "right") {
      updates.push({ id: cell.id, x: cell.x, y: cell.y, w: cell.w - halfW, h: cell.h });
      creates.push({ x: cell.x + cell.w - halfW, y: cell.y, w: halfW, h: cell.h });
    } else if (direction === "top") {
      creates.push({ x: cell.x, y: cell.y, w: cell.w, h: halfH });
      updates.push({ id: cell.id, x: cell.x, y: cell.y + halfH, w: cell.w, h: cell.h - halfH });
    } else if (direction === "bottom") {
      updates.push({ id: cell.id, x: cell.x, y: cell.y, w: cell.w, h: cell.h - halfH });
      creates.push({ x: cell.x, y: cell.y + cell.h - halfH, w: cell.w, h: halfH });
    }
    postBatch({ updates, creates }, { reload: true }).catch(reportErr);
  }

  // Find cells that can absorb the gap left by a deletion. Looks for a
  // direction whose neighbour(s) collectively share the deleted cell's
  // entire edge — those cells get extended to fill the gap. Returns
  // null if no clean absorption is possible (the user gets a true gap
  // they can resize manually).
  function findAbsorbers(deleted) {
    const others = cells.filter((c) => c.id !== deleted.id);
    const dx0 = deleted.x;
    const dx1 = deleted.x + deleted.w;
    const dy0 = deleted.y;
    const dy1 = deleted.y + deleted.h;

    // For each direction, collect candidates whose opposite edge is at
    // the deleted's edge AND whose perpendicular range fits inside the
    // deleted's perpendicular range. Then check whether they tile that
    // range exactly.
    function checkAndExtend(direction) {
      if (direction === "left") {
        const cs = others
          .filter((c) => c.x + c.w === dx0 && c.y >= dy0 && c.y + c.h <= dy1)
          .sort((a, b) => a.y - b.y);
        if (!tilesRange(cs, "y", dy0, dy1)) return null;
        return cs.map((c) => ({ id: c.id, x: c.x, y: c.y, w: c.w + deleted.w, h: c.h }));
      }
      if (direction === "right") {
        const cs = others
          .filter((c) => c.x === dx1 && c.y >= dy0 && c.y + c.h <= dy1)
          .sort((a, b) => a.y - b.y);
        if (!tilesRange(cs, "y", dy0, dy1)) return null;
        return cs.map((c) => ({ id: c.id, x: dx0, y: c.y, w: c.w + deleted.w, h: c.h }));
      }
      if (direction === "top") {
        const cs = others
          .filter((c) => c.y + c.h === dy0 && c.x >= dx0 && c.x + c.w <= dx1)
          .sort((a, b) => a.x - b.x);
        if (!tilesRange(cs, "x", dx0, dx1)) return null;
        return cs.map((c) => ({ id: c.id, x: c.x, y: c.y, w: c.w, h: c.h + deleted.h }));
      }
      if (direction === "bottom") {
        const cs = others
          .filter((c) => c.y === dy1 && c.x >= dx0 && c.x + c.w <= dx1)
          .sort((a, b) => a.x - b.x);
        if (!tilesRange(cs, "x", dx0, dx1)) return null;
        return cs.map((c) => ({ id: c.id, x: c.x, y: dy0, w: c.w, h: c.h + deleted.h }));
      }
      return null;
    }

    for (const dir of ["left", "right", "top", "bottom"]) {
      const updates = checkAndExtend(dir);
      if (updates) return updates;
    }
    return null;
  }

  // Helper: do `cells` (sorted) form a gapless tiling of [lo, hi] along axis?
  function tilesRange(cs, axis, lo, hi) {
    if (cs.length === 0) return false;
    const startKey = axis;
    const lenKey = axis === "x" ? "w" : "h";
    if (cs[0][startKey] !== lo) return false;
    let cursor = lo;
    for (const c of cs) {
      if (c[startKey] !== cursor) return false;
      cursor = c[startKey] + c[lenKey];
    }
    return cursor === hi;
  }

  function deleteCell(cell) {
    if (cells.length <= 1) {
      alert("Can't delete the last cell.");
      return;
    }
    if (!confirm("Delete this cell?")) return;
    const updates = findAbsorbers(cell) || [];
    postBatch({ deletes: [cell.id], updates }, { reload: true }).catch(reportErr);
  }

  function reportErr(err) {
    console.error("layout editor:", err);
    alert("Layout save failed: " + err.message);
  }

  // ---------------------------------------------------------------
  // Event wiring (delegated; survives re-renders)
  // ---------------------------------------------------------------
  board.addEventListener("click", (e) => {
    const insertBtn = e.target.closest("[data-insert]");
    if (insertBtn) {
      e.preventDefault();
      const cellEl = insertBtn.closest(".le-cell");
      if (!cellEl) return;
      const cell = cells.find((c) => c.id === cellEl.dataset.cellId);
      if (cell) insertOnCell(cell, insertBtn.dataset.insert);
      return;
    }
    const deleteBtn = e.target.closest("[data-delete-cell]");
    if (deleteBtn) {
      e.preventDefault();
      const cellEl = deleteBtn.closest(".le-cell");
      if (!cellEl) return;
      const cell = cells.find((c) => c.id === cellEl.dataset.cellId);
      if (cell) deleteCell(cell);
    }
  });

  // Long-press to delete — the 22px X icon is hard to hit on touch.
  // Pressing anywhere on a cell (not on its insert zones or resize
  // handles) for 600ms triggers the same delete confirm.
  const LONG_PRESS_MS = 600;
  const MOVE_TOLERANCE_PX = 10;
  let lpTimer = null;
  let lpStart = null;
  let lpCellEl = null;
  function cancelLongPress() {
    if (lpTimer) clearTimeout(lpTimer);
    lpTimer = null;
    lpStart = null;
    if (lpCellEl) {
      lpCellEl.classList.remove("is-pressing");
      lpCellEl = null;
    }
  }
  board.addEventListener("pointerdown", (e) => {
    // Skip if the press starts on an interactive child — resize
    // handles, insert zones, the explicit delete X.
    if (e.target.closest("[data-insert], [data-delete-cell], .le-edge")) return;
    const cellEl = e.target.closest(".le-cell");
    if (!cellEl) return;
    const cell = cells.find((c) => c.id === cellEl.dataset.cellId);
    if (!cell) return;
    lpStart = { x: e.clientX, y: e.clientY };
    lpCellEl = cellEl;
    cellEl.classList.add("is-pressing");
    lpTimer = setTimeout(() => {
      // Visual feedback at fire moment — the css animation already
      // showed the pulse; clean it up + run the same delete path.
      cellEl.classList.remove("is-pressing");
      lpTimer = null;
      lpStart = null;
      lpCellEl = null;
      deleteCell(cell);
    }, LONG_PRESS_MS);
  });
  board.addEventListener("pointermove", (e) => {
    if (!lpStart) return;
    const dx = e.clientX - lpStart.x;
    const dy = e.clientY - lpStart.y;
    if (dx * dx + dy * dy > MOVE_TOLERANCE_PX * MOVE_TOLERANCE_PX) cancelLongPress();
  });
  board.addEventListener("pointerup", cancelLongPress);
  board.addEventListener("pointercancel", cancelLongPress);
  board.addEventListener("pointerleave", cancelLongPress);

  // ---------------------------------------------------------------
  // Snap-to-grid toggle + grid overlay
  // ---------------------------------------------------------------
  const snapToggle = root.querySelector("[data-snap-toggle]");
  const snapDims = root.querySelector("[data-snap-dims]");
  const snapColsInput = root.querySelector("[data-snap-cols]");
  const snapRowsInput = root.querySelector("[data-snap-rows]");

  function applyGridOverlay() {
    if (!snapState.enabled) {
      board.style.removeProperty("background-image");
      board.style.removeProperty("background-size");
      return;
    }
    const cols = Math.max(2, snapState.cols);
    const rows = Math.max(2, snapState.rows);
    board.style.backgroundImage =
      "linear-gradient(to right, var(--t-border-strong, #c8c8c8) 1px, transparent 1px), " +
      "linear-gradient(to bottom, var(--t-border-strong, #c8c8c8) 1px, transparent 1px)";
    board.style.backgroundSize = `${100 / cols}% ${100 / rows}%`;
  }

  function persistSnap() {
    try {
      sessionStorage.setItem(
        snapState.storageKey,
        JSON.stringify({
          enabled: snapState.enabled,
          cols: snapState.cols,
          rows: snapState.rows,
        }),
      );
    } catch {}
  }

  function restoreSnap() {
    try {
      const raw = sessionStorage.getItem(snapState.storageKey);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (typeof saved.enabled === "boolean") snapState.enabled = saved.enabled;
      if (Number.isFinite(saved.cols)) snapState.cols = saved.cols;
      if (Number.isFinite(saved.rows)) snapState.rows = saved.rows;
    } catch {}
  }

  restoreSnap();
  if (snapToggle) snapToggle.checked = snapState.enabled;
  if (snapColsInput) snapColsInput.value = String(snapState.cols);
  if (snapRowsInput) snapRowsInput.value = String(snapState.rows);
  if (snapDims) snapDims.hidden = !snapState.enabled;

  if (snapToggle) {
    snapToggle.addEventListener("change", () => {
      snapState.enabled = snapToggle.checked;
      if (snapDims) snapDims.hidden = !snapState.enabled;
      applyGridOverlay();
      persistSnap();
    });
  }
  for (const [el, key] of [
    [snapColsInput, "cols"],
    [snapRowsInput, "rows"],
  ]) {
    if (!el) continue;
    el.addEventListener("input", () => {
      const n = Math.max(2, Math.min(48, Math.round(Number(el.value) || 0)));
      snapState[key] = n;
      applyGridOverlay();
      persistSnap();
    });
  }

  applyGridOverlay();
  render();
})();
