// Shared helper for the f1_* widget family.
//
// One fetch per browser session — the JSON is small (~46KB) but every
// widget on the panel would otherwise pull it independently. Module
// scope cache + an in-flight promise so concurrent renders coalesce.

let cache = null;
let pending = null;

export async function loadCircuits() {
  if (cache) return cache;
  if (pending) return pending;
  pending = fetch("/plugins/f1_core/static/circuits.json")
    .then((r) => {
      if (!r.ok) throw new Error(`circuits.json ${r.status}`);
      return r.json();
    })
    .then((data) => {
      cache = data;
      pending = null;
      return data;
    })
    .catch((err) => {
      pending = null;
      throw err;
    });
  return pending;
}

// Returns { name, location, length_m, viewBox, d } or null when the
// circuitId isn't in the bundle. Callers should fall back gracefully
// — a new circuit can appear on the calendar before the bundle is
// rebuilt.
export async function getCircuit(circuitId) {
  if (!circuitId) return null;
  const all = await loadCircuits();
  return all[circuitId] || null;
}

// Render a circuit outline as an inline SVG. Bolder stroke than the
// older inline version every widget used to ship — stroke-width 22
// is roughly 2.2% of the viewBox width, so the track reads as a
// confident bauhaus line that scales with the panel rather than the
// hairline 6 it used to be. The track strip is hidden on cells too
// narrow to render it cleanly (see ``.f1-track`` in
// spectra-widgets.css), so we don't need a small-size floor here.
//
// ``stroke`` is the colour. ``--text-primary`` by default so the track
// reads as neutral; callers can pass an accent to tint it (f1_next
// uses accent-1 to echo the "next race" headline colour).
//
// Returns an empty string when the circuit data is missing so the
// caller can drop the wrapper element entirely.
export function trackSvg(circuit, opts = {}) {
  if (!circuit || !circuit.d) return "";
  const stroke = opts.stroke || "var(--text-primary)";
  // No width/height on the SVG — the .f1-track CSS rule sizes it down
  // and centers it via flex so there's visible breathing room around
  // the path instead of edge-to-edge. The viewBox + preserveAspectRatio
  // keeps the path's own proportions while CSS controls the box.
  return `
    <svg viewBox="${circuit.viewBox}" preserveAspectRatio="xMidYMid meet"
         style="display:block">
      <path d="${circuit.d}" fill="none" stroke="${stroke}"
            stroke-width="22" stroke-linejoin="round" stroke-linecap="round" />
    </svg>`;
}
