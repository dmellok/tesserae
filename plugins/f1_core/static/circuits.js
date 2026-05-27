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
