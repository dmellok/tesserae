function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function formatReading(reading, fallbackUnit = "") {
  const value = reading?.value;
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "N/A";
  const unit = reading.unit || fallbackUnit;
  const formatted = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 1,
  }).format(Number(value));
  return `${escapeHtml(formatted)}${unit ? ` ${escapeHtml(unit)}` : ""}`;
}

function energyReading(label, current, yesterday, index, t, showYesterday) {
  const yesterdayValue = yesterday?.value;
  const hasYesterday = yesterdayValue !== null
    && yesterdayValue !== undefined
    && Number.isFinite(Number(yesterdayValue));
  const yesterdayMarkup = !showYesterday
    ? ""
    : hasYesterday
    ? `<span class="hp-yesterday">${escapeHtml(t("yesterday", "Yesterday"))}: ${formatReading(yesterday, "kWh")}</span>`
    : yesterday?.error
      ? `<span class="hp-yesterday">${escapeHtml(t("history_unavailable", "Yesterday unavailable"))}</span>`
    : "";
  return `
    <div class="hp-energy-card hp-energy-${index}">
      <span class="hp-energy-label">${escapeHtml(label)}</span>
      <strong>${formatReading(current, "kWh")}</strong>
      ${yesterdayMarkup}
    </div>`;
}

function modeLabel(value, t) {
  if (!value) return "N/A";
  const normalized = String(value).toLowerCase();
  const key = `mode_${normalized}`;
  return escapeHtml(t(key, normalized.replaceAll("_", " ")));
}

function modeIndicator(value, label, t) {
  const normalized = String(value || "").toLowerCase();
  const active = ["auto", "on", "heat", "heating", "cool", "active", "running"].includes(normalized);
  const color = active ? "var(--accent-3)" : value ? "var(--accent-2)" : "var(--text-muted)";
  const accessibleLabel = `${label}: ${modeLabel(value, t)}`;
  return `<span class="hp-reading-mode" role="img" aria-label="${escapeHtml(accessibleLabel)}" title="${escapeHtml(accessibleLabel)}"><i aria-hidden="true" style="background:${color}"></i><span class="hp-reading-mode-label">${modeLabel(value, t)}</span></span>`;
}

function temperatureGauge(reading, label, accent) {
  if (reading?.value === null || reading?.value === undefined) return "";
  const value = Number(reading?.value);
  if (!Number.isFinite(value)) return "";
  const fahrenheit = reading?.unit === "°F";
  const min = fahrenheit ? 32 : 0;
  const max = fahrenheit ? 176 : 80;
  const percentage = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
  const unit = fahrenheit ? "°F" : "°C";
  return `
    <div class="hp-temperature-gauge" role="meter" aria-label="${escapeHtml(label)}"
         aria-valuemin="${min}" aria-valuemax="${max}" aria-valuenow="${escapeHtml(value)}">
      <div class="hp-temperature-track" aria-hidden="true">
        <span class="hp-temperature-fill" style="width:${percentage}%;background:var(${accent})"></span>
      </div>
      <div class="hp-temperature-scale" aria-hidden="true"><span>${min}${unit}</span><span>${max}${unit}</span></div>
    </div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const t = ctx?.t || ((key, fallback) => fallback ?? key);
  const fragment = ctx?.fragment || ctx?.cell?.fragment || "full";
  const size = ["xs", "sm", "md", "lg"].includes(ctx?.cell?.size) ? ctx.cell.size : "md";
  const place = data.place && data.place !== "Heat pump"
    ? data.place
    : t("heat_pump", "Heat pump");
  const stylesheet = '<link rel="stylesheet" href="/static/style/spectra-widgets.css">';

  if (data.error) {
    shadow.innerHTML = `
      ${stylesheet}
      <div class="w hp hp-${size}" data-widget="ha_heat_pump">
        <div class="w-title"><i class="ph-bold ph-warning-circle" style="color:var(--accent-1)"></i><h3>${escapeHtml(place)}</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const compressor = data.compressor || {};
  const active = compressor.active;
  const statusText = active === true
    ? t("running", "Running")
    : active === false
      ? t("idle", "Idle")
      : t("status_unavailable", "Status unavailable");
  const stateClass = active === true ? "is-running" : active === false ? "is-idle" : "is-unknown";
  const stateColor = active === true ? "var(--accent-3)" : active === false ? "var(--accent-2)" : "var(--text-muted)";
  const stateSurface = active === true
    ? "var(--accent-3-soft)"
    : active === false
      ? "var(--surface-sunken)"
      : "var(--accent-2-soft)";
  const heating = data.heating_temperature || {};
  const hotWater = data.hot_water_temperature || {};
  const heatingMode = data.heating_mode || {};
  const hotWaterMode = data.hot_water_mode || {};
  const options = ctx?.cell?.options || {};
  const showHeating = options.show_heating !== false && options.show_heating !== "false";
  const showHotWater = options.show_hot_water !== false && options.show_hot_water !== "false";
  const showYesterday = options.show_yesterday !== false && options.show_yesterday !== "false";
  const showTemperatureGauge = options.show_temperature_gauge === true
    || options.show_temperature_gauge === "true";
  const includeHeating = fragment === "heating" || (showHeating && fragment === "full");
  const includeHotWater = fragment === "hot_water" || (showHotWater && fragment === "full");
  const singleReading = includeHeating !== includeHotWater;
  const power = data.power || {};
  const overallSpf = data.overall_spf || {};
  const energy = data.energy || {};
  const dailyTotal = energy.daily_total || {};
  const energyItems = [
    {
      label: t("daily_total", "Total"),
      current: energy.daily_total,
      yesterday: energy.daily_total_yesterday,
      index: "total",
    },
    {
      label: t("heating", "Heating"),
      current: energy.heating,
      yesterday: energy.heating_yesterday,
      index: "heating",
    },
    {
      label: t("hot_water", "Hot water"),
      current: energy.hot_water,
      yesterday: energy.hot_water_yesterday,
      index: "water",
    },
  ].filter(({ current, yesterday }) => current?.name || current?.value != null || yesterday?.value != null);
  const energyCards = Object.fromEntries(energyItems.map(({ label, current, yesterday, index }) => [
    index,
    energyReading(label, current, yesterday, index, t, showYesterday),
  ]));
  const details = energyItems.length
    ? Object.values(energyCards).join("")
    : `<p class="u-muted hp-no-energy">${escapeHtml(t("no_energy_data", "Configure daily energy sensors to see details."))}</p>`;
  const readings = [];
  if (includeHeating) {
    readings.push(`
      <div class="hp-reading hp-reading--heating status-cell ${singleReading ? "hp-reading--full" : ""}">
        <div class="hp-reading-top">
          <div class="hp-reading-caption">
            <i class="ph-bold ph-thermometer-simple hp-reading-icon" aria-hidden="true"></i>
            <span class="hp-reading-label">${escapeHtml(t("heating_temperature", "Heating"))}</span>
            ${modeIndicator(heatingMode.mode, t("heating_mode", "Heating mode"), t)}
          </div>
          <strong class="hp-reading-value">${formatReading(heating, "°C")}</strong>
        </div>
        ${size === "lg" || (fragment === "heating" && showTemperatureGauge) ? temperatureGauge(heating, t("heating_temperature", "Heating"), "--accent-1") : ""}
      </div>`);
  }
  if (includeHotWater) {
    readings.push(`
      <div class="hp-reading hp-reading--water status-cell ${singleReading ? "hp-reading--full" : ""}">
        <div class="hp-reading-top">
          <div class="hp-reading-caption">
            <i class="ph-bold ph-drop hp-reading-icon" aria-hidden="true"></i>
            <span class="hp-reading-label">${escapeHtml(size === "xs" ? t("water", "Water") : t("hot_water_temperature", "Hot water"))}</span>
            ${modeIndicator(hotWaterMode.mode, t("hot_water_mode", "Hot-water mode"), t)}
          </div>
          <strong class="hp-reading-value">${formatReading(hotWater, "°C")}</strong>
        </div>
        ${size === "lg" || (fragment === "hot_water" && showTemperatureGauge) ? temperatureGauge(hotWater, t("hot_water_temperature", "Hot water"), "--accent-2") : ""}
      </div>`);
  }
  const readingsMarkup = readings.length
    ? `<div class="status-grid hp-readings ${singleReading ? "hp-readings--single" : ""}">${readings.join("")}</div>`
    : "";

  const layout = `
    .hp { container: heat-pump / size; color: var(--text-primary); }
    .hp-body { gap: var(--space-3); overflow: hidden; }
    .hp-fragment-content { width: 100%; height: 100%; box-sizing: border-box; }
    .hp-fragment-compressor .hp-hero { width: 100%; height: 100%; box-sizing: border-box; }
    .hp-fragment-total_energy .hp-energy-card,
    .hp-fragment-heating_energy .hp-energy-card,
    .hp-fragment-hot_water_energy .hp-energy-card { display: flex; width: 100%; height: 100%; box-sizing: border-box; }
    .hp-fragment-heating .hp-temperature-gauge,
    .hp-fragment-hot_water .hp-temperature-gauge { display: flex; }
    .hp-hero {
      display: flex;
      align-items: center;
      gap: var(--space-4);
      min-width: 0;
      padding: var(--space-3) var(--space-4);
      color: ${stateColor};
      background: ${stateSurface};
      border-radius: var(--radius-1);
    }
    .hp-hero > .ph-bold {
      flex: 0 0 auto;
      font-size: clamp(var(--icon-lg), 12cqw, calc(var(--icon-lg) * 2));
      line-height: var(--lh-tight);
    }
    .hp-hero-lockup { min-width: max-content; flex: 0 0 auto; }
    .hp-hero-spf { display: none; min-width: 0; margin-left: auto; text-align: right; }
    .hp-hero-spf .hp-hero-label, .hp-hero-spf-value { display: block; }
    .hp-hero-spf-value { color: var(--text-primary); font-size: var(--fs-jumbo); font-weight: var(--fw-black); font-variant-numeric: tabular-nums; line-height: var(--lh-tight); }
    .hp-hero-label { color: var(--text-muted); font-size: var(--fs-label); font-weight: var(--fw-bold); }
    .hp-state { overflow: visible; font-size: var(--fs-jumbo); font-weight: var(--fw-black); line-height: var(--lh-tight); white-space: nowrap; }
    .hp-readings { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
    .hp-readings--single .hp-reading--full { grid-column: 1 / -1; }
    .hp-reading {
      display: flex;
      min-width: 0;
      flex-direction: column;
      justify-content: center;
      gap: var(--space-1);
      padding: var(--space-3) var(--space-4);
      border-radius: var(--radius-1);
    }
    .hp-reading--heating { background: var(--accent-1-soft); }
    .hp-reading--water { background: var(--accent-4-soft); }
    .hp-reading-top { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); min-width: 0; }
    .hp-reading-lockup { display: flex; min-width: 0; flex-direction: column; gap: var(--space-1); }
    .hp-reading-caption { display: flex; min-width: 0; flex-direction: row; align-items: center; gap: var(--space-1); }
    .hp-reading-icon { color: var(--text-muted); font-size: var(--fs-label); }
    .hp-reading-mode { display: none; width: 0.65em; height: 0.65em; flex: 0 0 auto; border-radius: 50%; background: var(--surface-sunken); }
    .hp-reading-mode i { display: block; width: 100%; height: 100%; border-radius: inherit; }
    .hp-reading-mode-label { display: none; }
    .hp-reading-label, .hp-energy-label { color: var(--text-muted); font-size: var(--fs-label); font-weight: var(--fw-bold); }
    .hp-reading-value { overflow: hidden; color: var(--text-primary); font-size: clamp(var(--fs-lead), 5cqw, var(--fs-display)); font-weight: var(--fw-black); line-height: var(--lh-tight); text-overflow: ellipsis; white-space: nowrap; font-variant-numeric: tabular-nums; }
    .hp-temperature-gauge { display: none; flex-direction: column; gap: var(--space-1); width: 100%; }
    .hp-temperature-track { height: var(--space-2); overflow: hidden; border-radius: var(--radius-1); background: var(--surface-sunken); }
    .hp-temperature-fill { display: block; height: 100%; border-radius: inherit; }
    .hp-temperature-scale { display: flex; justify-content: space-between; color: var(--text-muted); font-size: var(--fs-caption); font-weight: var(--fw-bold); font-variant-numeric: tabular-nums; }
    .hp-current-power { display: none; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-3) var(--space-4); border-radius: var(--radius-1); background: var(--accent-3-soft); }
    .hp-current-power .ph-bold { color: var(--accent-3); font-size: var(--icon-md); }
    .hp-power-lockup { display: flex; min-width: 0; flex-direction: column; gap: var(--space-1); }
    .hp-power-value { font-size: var(--fs-lead); font-weight: var(--fw-black); font-variant-numeric: tabular-nums; }
    .hp-energy-details { display: none; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-3); }
    .hp-daily-total { display: none; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-3) var(--space-4); border-radius: var(--radius-1); background: var(--surface-sunken); }
    .hp-daily-total-value { color: var(--text-primary); font-size: var(--fs-lead); font-weight: var(--fw-black); font-variant-numeric: tabular-nums; }
    .hp-energy-card { display: flex; min-width: 0; flex-direction: column; gap: var(--space-2); padding: var(--space-3); border-radius: var(--radius-1); }
    .hp-energy-total { background: var(--accent-3-soft); }
    .hp-energy-heating { background: var(--accent-1-soft); }
    .hp-energy-water { background: var(--accent-4-soft); }
    .hp-energy-card strong { color: var(--text-primary); font-size: var(--fs-lead); font-weight: var(--fw-black); font-variant-numeric: tabular-nums; }
    .hp-yesterday { color: var(--text-muted); font-size: var(--fs-caption); font-weight: var(--fw-bold); font-variant-numeric: tabular-nums; }
    .hp-no-energy { grid-column: 1 / -1; margin: 0; }
    .hp-xs .hp-body, .hp-sm .hp-body { gap: var(--space-2); }
    .hp-xs .hp-hero { gap: var(--space-3); padding: var(--space-2) var(--space-3); }
    .hp-xs .hp-hero > .ph-bold { font-size: clamp(var(--icon-md), 15cqw, var(--icon-lg)); }
    .hp-xs .hp-state { font-size: var(--fs-lead); }
    .hp-sm .hp-hero { padding-bottom: var(--space-4); }
    .hp-xs .hp-reading { padding: var(--space-2) var(--space-3); }
    .hp-xs .hp-reading-value { font-size: var(--fs-body); }
    .hp-xs .hp-body { overflow: visible; }
    .hp-xs .hp-readings { grid-template-columns: minmax(0, 1fr); grid-auto-rows: max-content; gap: var(--space-2); }
    .hp-xs .hp-reading { min-height: max-content; }
    .hp-xs .hp-reading-top { align-items: center; justify-content: space-between; gap: var(--space-3); }
    .hp-xs .hp-reading-caption { flex: 0 0 auto; flex-direction: row; align-items: center; }
    .hp-xs .hp-reading-value { flex: 1 1 auto; text-align: right; }
    .hp-xs .hp-reading-value { overflow: visible; }
    .hp-sm .hp-readings { grid-auto-rows: max-content; }
    .hp-sm .hp-reading { min-height: max-content; }
    .hp-sm .hp-reading-top { align-items: flex-start; flex-direction: column; gap: 0; }
    .hp-sm .hp-reading-value, .hp-md .hp-reading-value { width: 100%; text-align: right; }
    .hp-lg .hp-current-power { display: flex; }
    .hp-lg .hp-energy-details { display: grid; }
    .hp-lg .hp-daily-total { display: none; }
    .hp-md .hp-hero-spf, .hp-lg .hp-hero-spf { display: block; }
    .hp-md .hp-reading-mode, .hp-lg .hp-reading-mode { display: block; }
    .hp-md .hp-yesterday { display: none; }
    .hp-md .hp-state { font-size: var(--fs-lead); }
    .hp-md .hp-hero-spf-value { font-size: var(--fs-lead); }
    .hp-md .hp-hero { min-height: calc(var(--fs-jumbo) + var(--space-3) * 2); box-sizing: border-box; }
    .hp-md .hp-reading { justify-content: center; }
    .hp-md .hp-reading-top { align-items: flex-start; flex-direction: column; }
    .hp-md .hp-reading-caption { gap: var(--space-1); }
    .hp-md .hp-reading-mode { display: none; }
    .hp-md .hp-reading-value { flex: 0 0 auto; }
    .hp-fragment-total_energy .hp-yesterday,
    .hp-fragment-heating_energy .hp-yesterday,
    .hp-fragment-hot_water_energy .hp-yesterday { display: block; }
    .hp-lg .hp-reading-mode {
      display: inline-flex;
      width: auto;
      height: auto;
      align-items: center;
      gap: var(--space-1);
      border-radius: 0;
      background: none;
      color: var(--text-secondary);
      font-size: var(--fs-label);
      font-weight: var(--fw-bold);
      text-align: right;
    }
    .hp-lg .hp-reading-mode i { width: 0.65em; height: 0.65em; }
    .hp-lg .hp-reading-mode-label { display: inline; }
    .hp-lg .hp-temperature-gauge { display: flex; }
    .hp-md .hp-energy-details,
    .hp-md .hp-current-power,
    .hp-md .hp-daily-total { display: none; }
    .hp-fragment-current_power .hp-current-power { display: flex; }
    @container heat-pump (max-height: 260px) {
      .hp-hero { padding-block: var(--space-2); }
      .hp-reading { padding-block: var(--space-2); }
    }
  `;

  const heroMarkup = `
    <div class="status-hero hp-hero ${stateClass}">
      <i class="ph-bold ph-fan" aria-hidden="true"></i>
      <div class="lockup hp-hero-lockup">
        <span class="hp-hero-label">${escapeHtml(t("compressor", "Compressor"))}</span>
        <strong class="status-state hp-state">${escapeHtml(statusText)}</strong>
      </div>
      <div class="hp-hero-spf">
        <span class="hp-hero-label">${escapeHtml(t("overall_spf", "Overall SPF"))}</span>
        <strong class="hp-hero-spf-value">${formatReading(overallSpf)}</strong>
      </div>
    </div>`;
  const powerMarkup = `
    <div class="hp-current-power">
      <i class="ph-bold ph-lightning" aria-hidden="true"></i>
      <div class="hp-power-lockup">
        <span class="hp-energy-label">${escapeHtml(t("current_consumption", "Current consumption"))}</span>
        <strong class="hp-power-value">${formatReading(power, "W")}</strong>
      </div>
    </div>`;
  const energyMarkup = `
    <div class="hp-daily-total">
      <span class="hp-energy-label">${escapeHtml(t("electricity_today", "Electricity today"))}</span>
      <strong class="hp-daily-total-value">${formatReading(dailyTotal, "kWh")}</strong>
    </div>
    <div class="hp-energy-details" aria-label="${escapeHtml(t("energy_details", "Daily electricity use"))}">
      ${details}
    </div>`;
  const fullMarkup = `
    <div class="w-title">
      <h3>${escapeHtml(place)}</h3>
      <span class="w-title-meta">${escapeHtml(data.time || "")}</span>
    </div>
    <div class="w-body status-body hp-body">
      ${heroMarkup}
      ${readingsMarkup}
      ${power.value != null ? powerMarkup : ""}
      ${energyMarkup}
    </div>`;
  const fragmentBody = {
    compressor: heroMarkup,
    heating: readingsMarkup,
    hot_water: readingsMarkup,
    current_power: powerMarkup,
    total_energy: energyCards.total ?? "",
    heating_energy: energyCards.heating ?? "",
    hot_water_energy: energyCards.water ?? "",
  }[fragment];
  const content = fragment === "full"
    ? fullMarkup
    : `<div class="w-body status-body hp-body hp-fragment-content">${fragmentBody ?? ""}</div>`;

  shadow.innerHTML = `
    ${stylesheet}
    <style>${layout}</style>
    <div class="w hp hp-${size} ${fragment === "full" ? "" : `hp-fragment-${fragment}`}" data-widget="ha_heat_pump">
      ${content}
    </div>`;
}
