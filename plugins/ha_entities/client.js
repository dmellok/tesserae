// Stripped widget render. Theming + design system removed in v0.17
// to clear the slate for a redesign. Renders the raw ctx.data as
// semantic HTML so the widget is still visible while the new design
// system is built.

export default function render(shadow, ctx) {
  const data = ctx?.data ?? null;
  const pluginId = ctx?.cell?.plugin_id ?? ctx?.cell?.plugin ?? "widget";
  const parts = [`<h2>${escapeHtml(pluginId)}</h2>`];
  if (data && typeof data === "object" && !Array.isArray(data) && typeof data.error === "string") {
    parts.push(`<p>error: ${escapeHtml(data.error)}</p>`);
  } else if (data == null) {
    parts.push(`<p>no data</p>`);
  } else {
    parts.push(renderValue(data));
  }
  shadow.innerHTML = parts.join("");
}

function renderValue(v) {
  if (v === null || v === undefined) return `<p>null</p>`;
  if (typeof v === "string") return `<p>${escapeHtml(v)}</p>`;
  if (typeof v === "number" || typeof v === "boolean") return `<p>${escapeHtml(String(v))}</p>`;
  if (Array.isArray(v)) {
    if (!v.length) return `<p>empty list</p>`;
    return `<ul>${v.map((item) => `<li>${renderValue(item)}</li>`).join("")}</ul>`;
  }
  if (typeof v === "object") {
    const entries = Object.entries(v);
    if (!entries.length) return `<p>empty object</p>`;
    return `<dl>${entries.map(([k, val]) => `<dt>${escapeHtml(k)}</dt><dd>${renderValue(val)}</dd>`).join("")}</dl>`;
  }
  return `<p>${escapeHtml(String(v))}</p>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}
