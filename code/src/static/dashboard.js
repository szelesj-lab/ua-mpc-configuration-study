"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  status: null,
  resultsStamp: null,
  results: null,
  config: null,
  schedule: [],
  modes: [],
  logOpen: false,
  lastJobStatus: null,
  liveHours: 6,
  liveTimer: null,
};
const DATA_STALE_DAYS = 30;

// ------------------------------------------------------------------ api

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

function showAlert(el, message, ok = false) {
  el.textContent = message;
  el.classList.toggle("ok", ok);
  el.hidden = !message;
  if (ok) setTimeout(() => { el.hidden = true; }, 3500);
}

// ------------------------------------------------------------------ formatting

const fmt = (v, digits = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits }));
const timeOf = (s) => String(s).slice(11, 16);
const dayOf = (s) => String(s).slice(5, 10).replace("-", ".");
const prettyStamp = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : "–");

function niceTicks(min, max, count = 5) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return { lo, hi, ticks, step };
}

// ------------------------------------------------------------------ charts

const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();


// ------------------------------------------------------------------ models tab

function renderModels(st) {
  const card = (title, desc, group, jobId) => `
    <div class="card">
      <h3>${title}</h3>
      <div class="model-status ${group.ready ? "ok" : "bad"}">${group.ready ? "✓ Trained" : "✗ Not trained"}</div>
      <p class="model-meta">${group.ready ? `Updated ${prettyStamp(group.modified)} · ${group.size_mb} MB` : `Missing: ${group.missing.join(", ")}`}<br>${desc}</p>
      <button class="btn" data-job="${jobId}" ${st.job.status === "running" ? "disabled" : ""}>${group.ready ? "Retrain" : "Train now"}</button>
    </div>`;
  const html =
    card("SVR grid price model", "Trained on data/ENTSOE_HU_Dataset_2025.csv. Takes about 2 minutes.", st.models.svr, "train_svr") +
    card("GPR solar & wind models", "Trained on the past forecast and measured weather data. Takes a few minutes (about 4 with the default 5000 samples).", st.models.gpr, "train_gpr");
  const el = $("#model-cards");
  if (el.dataset.html !== html) {
    el.innerHTML = html;
    el.dataset.html = html;
    $$("[data-job]", el).forEach((b) => b.addEventListener("click", () => startJob(b.dataset.job)));
  }
  const plotCard = $("#svr-plot-card");
  plotCard.hidden = !st.svr_plot;
  if (st.svr_plot) {
    const src = `/api/svr-plot.png?t=${encodeURIComponent(st.svr_plot.modified)}`;
    if ($("#svr-plot").getAttribute("src") !== src) $("#svr-plot").setAttribute("src", src);
  }
}


// ------------------------------------------------------------------ config

const CONFIG_META = {
  general: {
    title: "Horizon",
    fields: { horizon: ["MPC horizon", "steps"], deltaT: ["Step length", "seconds"], timezone: ["Site time zone", "e.g. Europe/Budapest"] },
  },
  SoC: {
    title: "Battery",
    fields: {
      E_capacity: ["Capacity", "Wh"], eta_batt: ["Efficiency", "0–1"], initial_soc: ["Initial SoC", "0–1"],
      x_ref: ["Reference SoC", "0–1"], soc_min: ["Minimum SoC", "0–1"], soc_max: ["Maximum SoC", "0–1"],
    },
  },
  mpc: {
    title: "MPC model",
    note: "Original formulation: charging off, smoothness total, z = 0.",
    fields: {
      charge_is_decision: ["Charging is a decision", "allows curtailing renewable power"],
      smoothness: ["Smoothness on", "inputs = each input, total = grid + battery", ["inputs", "total"]],
      soc_chance_z: ["SoC safety margin z", "× forecast std · 0 = off, 1 ≈ 84 %, 2 ≈ 98 %"],
      uncertainty_scaling: ["Uncertainty scaling", "relative: λ is dimensionless, σ / σ_ref · absolute: original λσ² (λ in 1/W²)", ["relative", "absolute"]],
      sigma_ref_w: ["σ reference power", "W · 0 = installed PV + wind power"],
      sigma_model: ["σ calibration model", "constant = one factor · weather = follows forecast gusts and cloud variability · weather_floor = weather, never below the constant factor", ["constant", "weather", "weather_floor"]],
      sigma_scale: ["σ calibration factor", "× GPR std (constant model) · from calibrate_uncertainty.py"],
      error_autocorr: ["Forecast error autocorrelation", "per MPC step, 0–1 · widens the SoC margin"],
      margin_window: ["SoC margin window", "horizon = error builds up over the whole plan · replan = only until the next MPC run (live: MPC interval) · steps = SoC margin steps", ["horizon", "replan", "steps"]],
      margin_steps: ["SoC margin steps", "MPC steps, used when the window is 'steps'"],
      soc_backoff: ["Constant SoC back-off", "SoC fraction added to both margins, independent of the uncertainty · 0 = off"],
    },
  },
  live: {
    title: "Live measurements",
    fields: {
      enabled: ["Live data enabled", ""],
      api_token: ["API token", "required in the X-API-Key header if set"],
      stale_after_s: ["Stale after", "seconds without data"],
      use_measured_soc: ["Start MPC from measured SoC", ""],
      measured_soc_max_age_s: ["Max. age of measured SoC", "seconds"],
      auto_mpc: ["Run the MPC automatically", "while live data is on"],
      mpc_interval_min: ["MPC interval", "minutes (aligned, e.g. :00/:15/:30/:45)"],
      deviation_abs_w: ["Plan deviation, absolute", "W · measured vs plan difference flagged above this"],
      deviation_rel: ["Plan deviation, relative", "share of the planned value · 0.25 = 25 %; the larger limit applies"],
      deviation_soc: ["SoC deviation", "0–1 · 0.05 = 5 percentage points"],
    },
  },
  limits: {
    title: "Power limits",
    fields: {
      p_charge_max_w: ["Max. charge power", "W · renewable above this is curtailed"],
      p_discharge_max_w: ["Max. discharge power", "W"],
      p_grid_max_w: ["Max. grid import", "W"],
    },
  },
  pv: {
    title: "PV system",
    note: "Retrain the GPR models after changing these.",
    fields: { rated_power_w: ["Rated power", "W at 1000 W/m²"], efficiency: ["Efficiency", "0–1"] },
  },
  wind: {
    title: "Wind turbine",
    note: "Retrain the GPR models after changing these.",
    fields: {
      air_density: ["Air density", "kg/m³"], rotor_area_m2: ["Rotor area", "m²"], power_coefficient: ["Power coefficient", "Cp"],
      cut_in_speed_ms: ["Cut-in speed", "m/s"], rated_speed_ms: ["Rated speed", "m/s"], cut_out_speed_ms: ["Cut-out speed", "m/s"],
    },
  },
  degradation: { title: "Degradation", fields: { c_deg: ["Degradation cost", "€ per Wh throughput"] } },
  production: { title: "Production", fields: { cat_time_s: ["Time per unit", "seconds"], cat_power_w: ["Power while producing", "W"] } },
  weights: {
    title: "Cost function weights",
    fields: {
      w_x: ["w_x", "SoC tracking"], w_u: ["w_u", "grid + degradation cost"], w_delta_u: ["w_Δu", "control smoothness"],
      w_T: ["w_T", "terminal SoC"], w_ff: ["w_ff", "renewable feed-forward"], lambda: ["λ", "uncertainty sensitivity (see MPC model → scaling)"],
    },
  },
  replay: {
    title: "Replay and evaluation",
    note: "Closed-loop replay on past data: python src/replay.py",
    fields: {
      schedule_csv: ["Weekly production schedule", "file in data/"],
      replan_steps: ["Re-plan every", "MPC steps"],
      validation_days: ["Validation days", "first days of the held-out period, for tuning and calibration"],
      test_days: ["Test days", "0 = the rest of the held-out period"],
      heldout_start: ["Held-out data from", "local date (YYYY-MM-DD) where the GPR training data ends · empty = last 20 % of the weather data"],
      calibration_days: ["Calibration days", "σ calibration · 0 = older split (validation / test days)"],
      tuning_days: ["Tuning days", "Bayesian tuning of every controller"],
      development_days: ["Development days", "ablations, decision-rule map, design decisions · the rest is the final test, run once with --final"],
      workers: ["Parallel processes", "0 = all CPU cores"],
      violation_penalty_eur_per_pp_h: ["SoC violation penalty", "€ per SoC %-point and hour outside [soc_min, soc_max]"],
      solver_ftol: ["Solver tolerance in replays", "0 = use the optimizer setting"],
      plant: ["Simulated cell", "physical = only SoC 0–100 % (leaving the band counts as a violation) · bms = battery management stops at soc_min/soc_max, the grid covers the rest", ["physical", "bms"]],
    },
  },
  tuning: {
    title: "Bayesian tuning",
    note: "python src/tune_mpc.py --controller ua · objectives and search ranges (params_*) are edited in dataConfig.json",
    fields: {
      mode: ["Mode", "pareto = several KPIs at once (Pareto front) · single = one cost objective", ["pareto", "single"]],
      trials: ["Trials", "replay evaluations per controller"],
      initial_points: ["Initial design points", "before the Gaussian process guides the search"],
      episodes: ["Episodes", "replay windows per trial"],
      episode_days: ["Episode length", "days"],
      random_state: ["Random seed", ""],
    },
  },
  openmeteo: {
    title: "Site (weather forecast)",
    fields: {
      latitude: ["Latitude", "°"], longitude: ["Longitude", "°"], tilt: ["PV tilt", "°"],
      azimuth: ["PV azimuth", "Open-Meteo: 0 = south, −90 = east, 90 = west, ±180 = north"], forecast_days: ["Forecast days", "days"],
      gti_variable: ["Irradiance value", "instant = at the time step, mean = preceding-interval mean", ["instant", "mean"]],
    },
  },
  entsoe: {
    title: "Grid prices (ENTSO-E)",
    fields: {
      country_code: ["Bidding zone", "e.g. HU"], history_days: ["History to fetch", "days (≥ 8 for the price lags)"],
      cache_minutes: ["Cache lifetime", "minutes · 0 = no cache"],
    },
  },
  gpr: {
    title: "GPR training",
    note: "Used when the GPR models are (re)trained.",
    fields: {
      max_train_samples: ["Max. training samples", "per model · more = slower, larger"],
      train_months: ["Training window", "months of most recent data · 0 = all"],
      random_state: ["Random seed", ""],
    },
  },
  optimizer: { title: "Optimizer", fields: { maxiter: ["Max. iterations", ""], ftol: ["Tolerance", "ftol"] } },
};

function renderConfig(cfg) {
  const form = $("#config-form");
  form.innerHTML = "";
  const order = [...Object.keys(CONFIG_META).filter((k) => k in cfg), ...Object.keys(cfg).filter((k) => !(k in CONFIG_META))];
  for (const section of order) {
    const values = cfg[section];
    if (typeof values !== "object" || values === null) continue;
    const meta = CONFIG_META[section] || { title: section, fields: {} };
    const group = document.createElement("fieldset");
    group.className = "config-group";
    group.innerHTML = `<h3>${meta.title}</h3>${meta.note ? `<p class="hint group-note">${meta.note}</p>` : ""}`;
    for (const [key, val] of Object.entries(values)) {
      if (!["number", "string", "boolean"].includes(typeof val)) continue;
      const [label, help, choices] = meta.fields[key] || [key, ""];
      const id = `cfg-${section}-${key}`;
      const row = document.createElement("div");
      row.className = "config-field";
      const attrs = `id="${id}" data-section="${section}" data-key="${key}"`;
      let input;
      if (typeof val === "boolean") {
        input = `<label class="switch"><input type="checkbox" ${attrs} data-type="boolean" ${val ? "checked" : ""}><span></span></label>`;
      } else if (choices) {
        input = `<select ${attrs} data-type="string">${choices.map((c) => `<option ${c === val ? "selected" : ""}>${c}</option>`).join("")}</select>`;
      } else if (typeof val === "number") {
        input = `<input type="number" step="any" value="${val}" ${attrs} data-type="number">`;
      } else {
        input = `<input type="text" value="${String(val).replace(/"/g, "&quot;")}" ${attrs} data-type="string" data-optional="${key === "api_token"}">`;
      }
      row.innerHTML = `<label for="${id}">${label}${help ? `<small>${help}</small>` : ""}</label>${input}`;
      group.appendChild(row);
    }
    form.appendChild(group);
  }
}

async function loadConfig() {
  state.config = await api("/api/config");
  renderConfig(state.config);
}

async function saveConfig() {
  const cfg = structuredClone(state.config);
  for (const input of $$("#config-form [data-section]")) {
    if (input.dataset.type === "boolean") {
      cfg[input.dataset.section][input.dataset.key] = input.checked;
      continue;
    }
    if (input.dataset.type === "string") {
      const text = input.value.trim();
      if (!text && input.dataset.optional !== "true") {
        showAlert($("#config-alert"), `${input.labels[0].firstChild.textContent} cannot be empty.`);
        input.focus();
        return;
      }
      cfg[input.dataset.section][input.dataset.key] = text;
      continue;
    }
    const v = input.value.trim() === "" ? NaN : Number(input.value);
    if (!Number.isFinite(v)) {
      showAlert($("#config-alert"), `${input.labels[0].firstChild.textContent} must be a number.`);
      input.focus();
      return;
    }
    cfg[input.dataset.section][input.dataset.key] = v;
  }
  try {
    await api("/api/config", { method: "PUT", body: cfg });
    state.config = cfg;
    showAlert($("#config-alert"), "Configuration saved.", true);
  } catch (e) {
    showAlert($("#config-alert"), e.message);
  }
}


// ------------------------------------------------------------------ schedule

function renderSchedule() {
  const tbody = $("#schedule-table tbody");
  tbody.innerHTML = "";
  state.schedule.forEach((row, i) => {
    const tr = document.createElement("tr");
    const ts = String(row.Timestamp).replace(" ", "T").slice(0, 16);
    tr.innerHTML = `
      <td><input type="datetime-local" value="${ts}" data-i="${i}" data-f="Timestamp"></td>
      <td><select data-i="${i}" data-f="Mode">${state.modes.map((m) => `<option ${m === row.Mode ? "selected" : ""}>${m}</option>`).join("")}</select></td>
      <td><input type="number" min="0" step="1" value="${row.Target_Output}" data-i="${i}" data-f="Target_Output" ${row.Mode === "FIXED_COUNT" ? "" : "disabled"}></td>
      <td><button class="icon-btn" title="Remove row" data-remove="${i}">×</button></td>`;
    tbody.appendChild(tr);
  });
}

async function loadSchedule() {
  const data = await api("/api/production");
  state.schedule = data.rows;
  state.modes = data.modes;
  renderSchedule();
}

function onScheduleInput(ev) {
  const { i, f } = ev.target.dataset;
  if (i === undefined) return;
  const row = state.schedule[Number(i)];
  if (f === "Timestamp") row.Timestamp = ev.target.value.replace("T", " ") + ":00";
  else if (f === "Target_Output") row.Target_Output = ev.target.value === "" ? 0 : Number(ev.target.value);
  else if (f === "Mode") {
    row.Mode = ev.target.value;
    if (row.Mode !== "FIXED_COUNT") row.Target_Output = 0;
    renderSchedule();
  }
}

function addScheduleRow() {
  const last = state.schedule[state.schedule.length - 1];
  let next = "2026-01-01 00:00:00";
  if (last) {
    const d = new Date(String(last.Timestamp).replace(" ", "T"));
    d.setHours(d.getHours() + 1);
    const p = (n) => String(n).padStart(2, "0");
    next = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:00`;
  }
  state.schedule.push({ Timestamp: next, Mode: "STOPPED", Target_Output: 0 });
  renderSchedule();
  const inputs = $$("#schedule-table tbody tr:last-child input");
  if (inputs[0]) inputs[0].focus();
}

async function saveSchedule() {
  try {
    const res = await api("/api/production", { method: "PUT", body: { rows: state.schedule } });
    await loadSchedule();
    showAlert($("#schedule-alert"), `Schedule saved (${res.rows} rows).`, true);
  } catch (e) {
    showAlert($("#schedule-alert"), e.message);
  }
}


// ------------------------------------------------------------------ training data

function renderData(data) {
  if (!data) return;
  const names = { prices: "Grid prices (ENTSO-E) – SVR", weather_forecast: "Past weather forecasts – GPR input", weather_real: "Past actual weather – GPR target" };
  const rows = Object.entries(data).map(([k, d]) => {
    const old = d.age_days !== null && d.age_days > DATA_STALE_DAYS;
    return `<tr><td>${names[k] || k}<br><small class="hint">${d.file}</small></td><td>${d.last ? prettyStamp(d.last) : "–"}</td>
      <td class="${old ? "warn" : ""}">${d.age_days === null ? "no data" : d.age_days < 1 ? "today" : `${Math.round(d.age_days)} days ago`}</td></tr>`;
  });
  const html = `<thead><tr><th>Dataset</th><th>Newest data</th><th>Age</th></tr></thead><tbody>${rows.join("")}</tbody>`;
  const el = $("#data-table");
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
}


// ------------------------------------------------------------------ live helpers

const LIVE_TILES = [
  ["pv_w", "PV", "W", "--c-renewable"],
  ["wind_w", "Wind", "W", "--c-renewable"],
  ["load_w", "Load", "W", "--c-demand"],
  ["grid_w", "Grid import", "W", "--c-grid"],
  ["battery_w", "Battery", "W", "--c-battery"],
  ["curtailed_w", "Curtailed", "W", "--c-renewable"],
  ["soc", "State of charge", "%", "--c-soc"],
];

const ago = (s) => (s === null || s === undefined ? "–" : s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`);

function renderLiveSetup() {
  const origin = window.location.origin;
  $("#live-endpoint").textContent = `POST ${origin}/api/live`;
  $("#live-example").textContent =
`{
  "timestamp": "2026-10-16T14:30:00+02:00",
  "pv_w": 12.4, "wind_w": 3.1, "load_w": 24.0,
  "grid_w": 16.2, "battery_w": 7.8, "curtailed_w": 0.0, "soc": 0.56
}
# power balance: load_w = grid_w + battery_w + pv_w + wind_w - curtailed_w

# PowerShell
Invoke-RestMethod -Method Post -Uri ${origin}/api/live -ContentType "application/json" \`
  -Headers @{ "X-API-Key" = "<token>" } \`
  -Body '{"pv_w": 12.4, "load_w": 24.0, "soc": 0.56}'

# Python
requests.post("${origin}/api/live", json={"pv_w": 12.4, "soc": 0.56}, headers={"X-API-Key": "<token>"})`;
}

// ------------------------------------------------------------------ money & units

// All amounts in EUR; small amounts get more decimals so they do not round to zero
const money = (eur) => {
  if (eur === null || eur === undefined || !Number.isFinite(eur)) return "–";
  const a = Math.abs(eur);
  return `${fmt(eur, a >= 1 ? 2 : a >= 0.01 ? 3 : 4)} €`;
};
const eurPerKwh = (eurMwh) => (eurMwh === null || eurMwh === undefined ? "–" : `${fmt(eurMwh / 1000, 3)} €/kWh`);

// ------------------------------------------------------------------ status & jobs

function renderPills(st) {
  const svr = st.models.svr.ready, gpr = st.models.gpr.ready;
  const pills = [
    [svr && gpr ? "ok" : "bad", svr && gpr ? "Models ready" : "Models missing"],
    [st.api_key_set ? "ok" : "bad", st.api_key_set ? "API key set" : "API key missing"],
  ];
  const live = st.live || {};
  if (live.enabled) {
    const cls = { receiving: "ok", waiting: "busy", stale: "bad" }[live.state] || "";
    pills.push([cls, { receiving: "Live data", waiting: "Live: waiting", stale: "Live: no new data" }[live.state] || "Live"]);
  }
  const ages = Object.values(st.data || {}).map((d) => d.age_days).filter((a) => a !== null);
  if (ages.length && Math.max(...ages) > DATA_STALE_DAYS) pills.push(["bad", `Training data ${Math.round(Math.max(...ages))} days old`]);
  $("#status-pills").innerHTML = pills.map(([cls, text]) => `<span class="pill ${cls}">${text}</span>`).join("");
}

function renderJob(job) {
  const running = job.status === "running";
  $$("[data-job]").forEach((b) => { b.disabled = running; });
  $("#stop-job").hidden = !running;
  const bar = $("#jobbar");
  if (job.status === "idle" || (state.jobHiddenFor && state.jobHiddenFor === job.finished_at)) {
    bar.hidden = true;
  } else {
    bar.hidden = false;
  }
  $("#job-dot").className = `job-dot ${job.status}`;
  const who = job.trigger === "auto" ? " (automatic)" : "";
  const texts = {
    running: `${job.label}${who} – running for ${Math.round(job.elapsed_s)} s`,
    success: `${job.label}${who} – finished in ${Math.round(job.elapsed_s)} s`,
    failed: `${job.label}${who} – failed after ${Math.round(job.elapsed_s)} s`,
    stopped: `${job.label} – stopped`,
  };
  $("#job-text").textContent = texts[job.status] || "";
  const log = $("#job-log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.textContent = (job.log || []).join("\n");
  if (atBottom) log.scrollTop = log.scrollHeight;
  if (job.status === "failed" && state.lastJobStatus === "running" && job.trigger !== "auto") state.logOpen = true;
  log.hidden = !state.logOpen;
  $("#toggle-log").textContent = state.logOpen ? "Hide log" : "Show log";
  state.lastJobStatus = job.status;
}

async function refreshStatus() {
  try {
    const st = await api("/api/status");
    state.status = st;
    renderPills(st);
    renderJob(st.job);
    renderModels(st);
    renderData(st.data);
    $("#live-tab-dot").className = `tab-dot ${(st.live || {}).state || ""}`;
    const stamp = st.results ? st.results.modified : null;
    if (stamp !== state.resultsStamp) {
      state.resultsStamp = stamp;
      loadOverview();
    }
    setTimeout(refreshStatus, st.job.status === "running" ? 1000 : 3000);
  } catch (e) {
    $("#status-pills").innerHTML = `<span class="pill bad">Dashboard server not reachable</span>`;
    setTimeout(refreshStatus, 4000);
  }
}

async function startJob(jobId) {
  showAlert($("#run-alert"), "");
  try {
    await api(`/api/jobs/${jobId}`, { method: "POST" });
    state.logOpen = jobId !== "run_mpc";
    state.jobHiddenFor = null;
    refreshStatusSoon();
  } catch (e) {
    showAlert($("#run-alert"), e.message);
    activateTab("overview");
  }
}

let soonTimer = null;
function refreshStatusSoon() {
  clearTimeout(soonTimer);
  soonTimer = setTimeout(async () => {
    const st = await api("/api/status");
    state.status = st;
    renderPills(st);
    renderJob(st.job);
  }, 150);
}

function kpi(label, value, unit, sub = "") {
  return `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}<span class="kpi-unit">${unit}</span></div>${sub ? `<div class="kpi-sub">${sub}</div>` : ""}</div>`;
}

// ------------------------------------------------------------------ overview

async function loadOverview() {
  try {
    const [ov, tl] = await Promise.all([api("/api/overview"), api("/api/timeline?past_hours=6")]);
    state.overview = ov;
    state.timeline = tl;
    const has = !!ov.decision || !!(ov.costs && ov.costs.plan);
    $("#overview").hidden = !has;
    $("#overview-empty").hidden = has;
    if (!has) return;
    renderNow(ov);
    renderCosts(ov.costs);
    renderMpcStrip(ov);
    renderLiveBlock(ov);
    renderTimeline();
    loadLiveResults();
  } catch (e) {
    showAlert($("#run-alert"), `Could not load the overview: ${e.message}`);
  }
}

// ------------------------------------------------------------------ results of the live operation

const fmtN = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));

async function loadLiveResults() {
  const period = state.resPeriod || "all";
  $("#res-csv").href = `/api/live-results.csv?period=${period}`;
  try {
    renderLiveResults(await api(`/api/live-results?period=${period}`));
  } catch (e) {
    $("#res-body").innerHTML = `<p class="hint">Could not load the results: ${e.message}</p>`;
  }
}

function renderLiveResults(r) {
  const t = r.total || {};
  const log = r.log || {};
  if (!log.runs && !t.measured_hours) {
    $("#res-body").innerHTML = `<p class="hint">No MPC runs logged yet. Every MPC run (automatic or "Run MPC now") is written to results/runs/; the results appear here together with the live measurements.</p>`;
    $("#res-days-wrap").hidden = true;
    return;
  }
  $("#res-caption").textContent = `${t.start || "–"} – ${t.end || "–"} · ${fmtN(t.measured_hours, 1)} h measured · ${t.mpc_runs || 0} MPC runs logged in this period (${log.runs || 0} in total)`;
  const tile = (color, label, value, unit, sub) => `<div class="tile" style="--tile-color:${color}"><div class="kpi-label">${label}</div><div class="kpi-value">${value}<span class="kpi-unit">${unit}</span></div>${sub ? `<div class="kpi-sub">${sub}</div>` : ""}</div>`;
  const below = t.steps_below_soc_min_pct;
  const target = t.chance_target_below_pct;
  const belowCls = target !== undefined && below !== undefined && below !== null ? (below <= target ? "res-ok" : "res-warn") : "";
  const groups = [
    ["Cost", [
      tile("var(--c-grid)", "Cost (grid + wear)", fmtN(t.total_eur, 3), " €", t.planned_eur !== undefined ? `plan: ${fmtN(t.planned_eur, 3)} €` : ""),
      tile("var(--c-grid)", "Saving vs. grid only", fmtN(t.saving_vs_grid_only_pct, 1), " %", t.grid_only_eur !== undefined ? `grid only: ${fmtN(t.grid_only_eur, 3)} €` : ""),
    ]],
    ["1 · Planned SoC band", [
      tile("var(--warn)", "Violation (depth × time)", fmtN(t.violation_pp_h, 2), " pp·h", `deepest ${fmtN(t.max_violation_pp, 2)} pp · ${fmtN(t.h_below_soc_min, 2)} h below`),
      tile("var(--warn)", "Steps below soc_min", `<span class="${belowCls}">${fmtN(below, 2)}</span>`, " %", target !== undefined ? `chance constraint promises ≤ ${fmtN(target, 1)} %` : "deterministic: no target"),
    ]],
    ["2 · Protection acting  ·  3 · consequence", [
      tile("var(--danger)", "Protection cut the plan", t.protection_steps ?? "–", " steps", t.protection_steps !== undefined ? `${fmtN(t.protection_h, 2)} h · ${t.protection_discharge_steps} discharge stopped at soc_min, ${t.protection_charge_steps} charge stopped at soc_max` : "needs battery and SoC measurements"),
      tile("var(--danger)", "Battery energy not delivered", fmtN(t.undelivered_discharge_kwh, 3), " kWh", `extra grid cost ${fmtN(t.extra_grid_eur, 4)} €`),
    ]],
    ["MPC", [
      tile("var(--c-battery)", "Runs", t.mpc_runs ?? 0, "", `solver converged ${fmtN(t.solver_success_pct, 0)} %`),
      tile("var(--c-battery)", "Solve time (median)", fmtN(t.solve_median_s, 2), " s", `started from measured SoC ${fmtN(t.measured_soc_start_pct, 0)} %`),
    ]],
  ];
  $("#res-body").innerHTML = `<div class="res-grid">${groups.map(([h, tiles]) => `<div class="res-group"><h4>${h}</h4><div class="res-pair">${tiles.join("")}</div></div>`).join("")}</div>`;

  const days = r.days || [];
  $("#res-days-wrap").hidden = !days.length;
  if (days.length) {
    const cols = [["day", "Day", null], ["measured_hours", "Measured h", 1], ["total_eur", "Cost €", 3], ["saving_vs_grid_only_pct", "Saving %", 1],
      ["violation_pp_h", "Violation pp·h", 2], ["steps_below_soc_min_pct", "Below soc_min %", 2], ["protection_steps", "Protection steps", 0],
      ["undelivered_discharge_kwh", "Not delivered kWh", 3], ["extra_grid_eur", "Extra €", 4], ["mpc_runs", "Runs", 0], ["solver_success_pct", "Converged %", 0]];
    $("#res-days").innerHTML = `<thead><tr>${cols.map((c) => `<th>${c[1]}</th>`).join("")}</tr></thead><tbody>${days.slice().reverse()
      .map((d) => `<tr>${cols.map(([k, , n]) => `<td>${n === null ? d[k] : fmtN(d[k], n)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  }
}

function renderNow(ov) {
  const d = ov.decision;
  if (!d) {
    $("#now-slot").textContent = "–";
    $("#now-src").textContent = "";
    $("#now-body").innerHTML = `<p class="hint">The current plan does not cover the present time. Run the MPC to get a new plan.</p>`;
    return;
  }
  $("#now-slot").textContent = `${d.from.slice(11)}–${d.to.slice(11)}`;
  $("#now-src").textContent = d.upcoming ? `starts ${d.from.slice(11)} · plan from ${d.plan_computed ? d.plan_computed.slice(11) : "–"}` : `from the plan computed at ${d.plan_computed ? d.plan_computed.slice(11) : "–"}`;

  const gridPct = d.grid_share !== null ? Math.round(d.grid_share * 100) : 0;
  const batPct = d.battery_share !== null ? 100 - gridPct : 0;
  const load = d.demand_w > 0.05
    ? `<div class="split" role="img" aria-label="Load supplied ${gridPct} % by the grid and ${batPct} % by the battery">
         ${gridPct ? `<span class="split-grid" style="flex:${gridPct}">${gridPct >= 15 ? `Grid ${gridPct} %` : ""}</span>` : ""}
         ${batPct ? `<span class="split-bat" style="flex:${batPct}">${batPct >= 15 ? `Battery ${batPct} %` : ""}</span>` : ""}
       </div>`
    : `<div class="split split-none">No load in this slot – production is stopped</div>`;

  const srcRow = (cls, label, value, note) => `<div class="src ${cls}"><span class="src-dot"></span><span class="src-label">${label}</span><span class="src-val">${value}</span><span class="src-note">${note || ""}</span></div>`;
  const batNote = d.battery_discharge_w > 0.05 ? "discharging to the load" : "idle";
  const chNote = d.battery_charge_w > 0.05 ? `stored in the battery${d.curtailed_w > 0.05 ? ` · ${fmt(d.curtailed_w, 1)} W curtailed` : ""}` : d.renewable_w > 0.05 ? "curtailed" : "no production";

  const mn = ov.measured_now;
  const mCost = mn && mn.cost_rate_eur_h !== null && mn.cost_rate_eur_h !== undefined;
  const costNow = mCost
    ? `<div><span class="kpi-label">Cost right now · measured</span><b>${money(mn.cost_rate_eur_h)}/h</b><span class="hint">grid ${money(mn.grid_cost_rate_eur_h)}/h${mn.wear_cost_rate_eur_h !== null ? ` · wear ${money(mn.wear_cost_rate_eur_h)}/h` : ""} · plan ${money(d.cost_rate_eur_h)}/h</span></div>`
    : `<div><span class="kpi-label">Cost right now · planned</span><b>${money(d.cost_rate_eur_h)}/h</b><span class="hint">grid ${money(d.grid_cost_rate_eur_h)}/h · battery wear ${money(d.wear_cost_rate_eur_h)}/h</span></div>`;
  const val = (r, v) => (v === null || v === undefined ? "–" : r.unit === "%" ? `${fmt(v * 100, 1)} %` : `${fmt(v, 1)} W`);
  const dv = (r) => (r.diff === null ? "" : r.unit === "%" ? `${r.diff > 0 ? "+" : ""}${fmt(r.diff * 100, 1)} pp` : `${r.diff > 0 ? "+" : ""}${fmt(r.diff, 1)} W`);
  const vsBlock = mn
    ? `<div class="pvm${mn.deviation ? " off" : ""}">
        <div class="pvm-head"><span class="kpi-label">Plan vs measured now</span>
          <span class="${mn.deviation ? "warn-text" : "good"}">${mn.deviation ? "Deviates from the plan" : "On plan"}</span>
          <span class="hint">measured ${ago(mn.age_s)}</span></div>
        <div class="pvm-grid">
          <span></span><span class="hint">Plan</span><span class="hint">Measured</span><span class="hint">Difference</span>
          ${mn.rows.map((r) => `<span>${r.label}</span><span>${val(r, r.plan)}</span><span>${val(r, r.measured)}</span><span class="${r.flag ? "warn-text" : "hint"}">${r.flag ? "⚠ " : ""}${dv(r)}</span>`).join("")}
        </div>
        ${mn.balance_w !== null && mn.balance_w !== undefined
          ? `<p class="hint">${mn.balance_ok
            ? `Power balance closes (${fmt(mn.balance_w, 2)} W).`
            : `<span class="warn-text">⚠ Power balance does not close: ${fmt(mn.balance_w, 1)} W of the load is unaccounted for.</span> Check the measurements (load = grid + battery + PV + wind − curtailed).`}</p>`
          : ""}
        ${mn.deviation ? `<p class="hint">The next MPC run starts from the measured state. Large, lasting deviations point to a forecast or demand model error.</p>` : ""}
      </div>`
    : (ov.live && ov.live.enabled ? `<p class="hint pvm-none">No fresh measurement – the values above are the plan only.</p>` : "");
  $("#now-body").innerHTML = `
    <div class="now-load"><span class="now-big">${fmt(d.demand_w, 1)}<small> W</small></span><span class="hint">load of the production cell</span></div>
    ${load}
    <div class="sources">
      ${srcRow("grid", "From the grid", `${fmt(d.grid_w, 1)} W`, d.grid_w > 0.05 ? `at ${fmt(d.price_eur_mwh, 1)} €/MWh` : "")}
      ${srcRow("bat", "From the battery", `${fmt(d.battery_discharge_w, 1)} W`, batNote)}
      ${srcRow("ren", "Renewables", `${fmt(d.renewable_w, 1)} W`, chNote)}
    </div>
    <div class="now-cost">
      <div><span class="kpi-label">Grid price now</span><b>${fmt(d.price_eur_mwh, 1)} €/MWh</b><span class="hint">${eurPerKwh(d.price_eur_mwh)}</span></div>
      ${costNow}
      <div><span class="kpi-label">Planned SoC at slot end</span><b>${d.soc !== null ? fmt(d.soc * 100, 1) : "–"} %</b></div>
    </div>
    ${vsBlock}`;
}

function renderCosts(c) {
  const t = (c && c.today) || {};
  const p = c && c.plan;
  const hm = (iso) => (iso ? iso.slice(11, 16) : "");
  const day = (iso) => {
    if (!iso) return "";
    const d = new Date(`${iso.slice(0, 10)}T12:00:00`);
    return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  };
  const has = (v) => v !== null && v !== undefined && Number.isFinite(v);

  // Today: measured where available, the plan for the rest of the day.
  let today = "";
  if (has(t.expected_eur)) {
    const pctDone = t.period_hours ? Math.min(100, (100 * t.elapsed_hours) / t.period_hours) : 0;
    const pctMeas = t.period_hours ? Math.min(100, (100 * t.measured_hours) / t.period_hours) : 0;
    const parts = [];
    if (has(t.measured_eur)) {
      const dev = has(t.plan_same_eur) && t.plan_same_eur > 0 ? (100 * (t.measured_eur - t.plan_same_eur)) / t.plan_same_eur : null;
      parts.push(`Measured <b>${money(t.measured_eur)}</b> (${fmt(t.measured_hours, 1)} h) · plan for the same hours ${money(t.plan_same_eur)}`
        + (dev !== null ? ` · <span class="${dev <= 0 ? "good" : "warn-text"}">${dev > 0 ? "+" : ""}${fmt(dev, 0)} %</span>` : ""));
    }
    if (t.past_unmeasured_hours > 0.01) parts.push(`Past hours without measurements, planned: ${money(t.past_unmeasured_eur)} (${fmt(t.past_unmeasured_hours, 1)} h)`);
    parts.push(`Rest of the day planned (${t.now}–24:00): ${money(t.rest_planned_eur)}`);
    if (t.uncovered_hours > 0.01) parts.push(`${fmt(t.uncovered_hours, 1)} h of today are not covered by any plan and are not counted.`);
    today = `
      <div class="cost-block">
        <span class="kpi-label">Today · ${day(t.from)}, 00:00–24:00</span>
        <div class="cost-row"><span class="cost-big">${money(t.expected_eur)}</span><span class="hint">expected for the day</span></div>
        <div class="cost-progress" title="Grey: time passed · dark: measured">
          <span style="width:${pctDone}%"></span><span class="meas" style="width:${pctMeas}%"></span>
        </div>
        <ul class="cost-parts">${parts.map((x) => `<li>${x}</li>`).join("")}</ul>
      </div>`;
  } else {
    today = `<div class="cost-block"><span class="kpi-label">Today</span><div class="cost-big">–</div>
      <span class="hint">No plan covers today yet. Run the MPC to get the expected cost of the day.</span></div>`;
  }

  // Current plan horizon: Kairos vs grid only, bill and wear separately.
  let plan = "";
  if (p) {
    const max = Math.max(p.total_eur, p.grid_only_eur, 1e-12);
    const verdict = p.grid_only_eur > 0
      ? (p.savings_eur >= 0
        ? `<span class="good">Saves ${money(p.savings_eur)} (${fmt(p.savings_pct, 0)} %)</span> compared with grid only`
        : `<span class="warn-text">Costs ${money(-p.savings_eur)} (${fmt(-p.savings_pct, 0)} %) more</span> than grid only`)
      : "";
    plan = `
      <div class="cost-block">
        <span class="kpi-label">Current plan · ${day(p.from)} ${hm(p.from)} – ${day(p.to)} ${hm(p.to)}</span>
        <div class="cost-bars">
          <span>With Kairos</span><div class="bar"><span class="kairos" style="width:${(100 * p.total_eur) / max}%"></span></div><b>${money(p.total_eur)}</b>
          <span class="muted">Grid only</span><div class="bar"><span class="gridonly" style="width:${(100 * p.grid_only_eur) / max}%"></span></div><b class="muted">${money(p.grid_only_eur)}</b>
        </div>
        <div class="cost-vs">${verdict}</div>
        <div class="cost-split">
          <div><span>Grid bill</span><b>${money(p.grid_eur)}</b></div>
          <div class="sub"><span>${fmt(p.grid_kwh, 2)} kWh from the grid${has(p.avg_price_eur_kwh) ? ` · avg ${fmt(p.avg_price_eur_kwh, 3)} €/kWh` : ""}</span></div>
          <div class="wear"><span title="Estimated battery ageing from charge and discharge throughput (c_deg). Nobody bills this; it is the value of the battery life used up.">ⓘ Battery wear (estimate, not billed)</span><b>+ ${money(p.wear_eur)}</b></div>
          <div class="sum"><span>= With Kairos</span><b>${money(p.total_eur)}</b></div>
        </div>
      </div>`;
  }
  $("#cost-card").innerHTML = `
    <div class="cost-head"><h3>Costs</h3>${p && p.computed ? `<span class="hint">Plan from the ${hm(p.computed)} run</span>` : ""}</div>
    ${today}${plan}
    <p class="hint cost-note">Grid only = the same load supplied entirely from the grid, without the battery.</p>`;
}

function renderMpcStrip(ov) {
  const el = $("#mpc-info");
  const auto = (ov.live && ov.live.auto_mpc) || {};
  const mpc = ov.mpc;
  const job = (state.status && state.status.job) || {};
  const parts = [];
  if (job.job === "run_mpc" && job.status === "running") parts.push(`<b>MPC running</b>${job.trigger === "auto" ? " (automatic)" : ""}…`);
  else if (auto.active) parts.push(`<b>Automatic MPC</b> every ${fmt(auto.interval_min, 0)} min${auto.next_run ? ` · next ${auto.next_run.slice(11, 16)}` : ""}`);
  else if (ov.live && ov.live.enabled) parts.push(`<b>Automatic MPC is off</b> (Configuration → Live measurements)`);
  else parts.push(`<b>MPC runs on request</b> – turn on live data for automatic runs`);
  if (mpc) {
    const src = mpc.initial_soc_source && mpc.initial_soc_source.startsWith("live") ? "measured" : "configured";
    parts.push(`last run ${prettyStamp(mpc.computed).slice(11)}${mpc.initial_soc !== null && mpc.initial_soc !== undefined ? ` from the ${src} SoC ${fmt(mpc.initial_soc * 100, 0)} %` : ""}`);
    if (mpc.success === false) parts.push(`<span class="warn-text">optimizer did not converge</span>`);
  }
  if (auto.note) parts.push(`<span class="warn-text">${auto.note}</span>`);
  if (job.job === "run_mpc" && job.status === "failed") parts.push(`<span class="warn-text">the last ${job.trigger === "auto" ? "automatic " : ""}run failed – see the log</span>`);
  el.innerHTML = parts.join(" · ");
}

async function renderLiveBlock(ov) {
  const live = ov.live || {};
  $("#live-block").hidden = !live.enabled;
  if (!live.enabled) return;
  const msg = {
    waiting: ["busy", "Live data is on – waiting for the first measurement."],
    receiving: ["ok", `Receiving measurements · last one ${ago(live.age_s)}`],
    stale: ["bad", `No new measurement for ${ago(live.age_s).replace(" ago", "")}. Check the connection to the system.`],
  }[live.state] || ["", ""];
  $("#live-banner").className = `live-banner ${msg[0]}`;
  $("#live-banner").textContent = msg[1];
  const latest = await api("/api/live/latest");
  const vals = (latest.latest && latest.latest.values) || {};
  $("#live-tiles").innerHTML = LIVE_TILES.map(([key, label, unit, color]) => {
    const v = vals[key];
    let value = "–", sub = "no data";
    if (v) {
      value = key === "soc" ? fmt(v.value * 100, 1) : fmt(v.value, 1);
      sub = ago(v.age_s);
      if (key === "battery_w") sub = `${v.value > 0.05 ? "discharging" : v.value < -0.05 ? "charging" : "idle"} · ${sub}`;
    }
    return `<div class="tile ${!v || v.age_s > 300 ? "stale" : ""}" style="--tile-color: var(${color})"><div class="kpi-label">Measured ${label === "PV" ? label : label.charAt(0).toLowerCase() + label.slice(1)}</div><div class="kpi-value">${value}<span class="kpi-unit">${unit}</span></div><div class="kpi-sub">${sub}</div></div>`;
  }).join("");
}

// ------------------------------------------------------------------ timeline (one view at a time)

const TL_VIEWS = {
  sources: {
    title: "Where the energy comes from",
    caption: "How the load is covered: from the grid and from the battery. Left of “now”: what happened; right: what the MPC plans.",
  },
  battery: {
    title: "Battery state of charge",
    caption: "Planned state of charge inside the allowed band. The band is narrower where the renewable forecast is uncertain.",
  },
  renewables: {
    title: "Renewable production",
    caption: "Forecast PV + wind power with its uncertainty (±2σ). The darker area is what the plan stores in the battery.",
  },
  price: {
    title: "Grid price",
    caption: "Day-ahead price. Shaded periods: the battery supplies the load instead of the grid.",
  },
};

function renderTimeline() {
  const tl = state.timeline;
  const el = $("#timeline");
  const view = state.tlView || "sources";
  $("#tl-title").textContent = TL_VIEWS[view].title;
  $("#tl-caption").textContent = TL_VIEWS[view].caption;
  if (!tl || !tl.available) { el.innerHTML = `<p class="hint empty-chart">No plan to show yet.</p>`; $("#timeline-legend").innerHTML = ""; return; }

  const n = tl.date.length;
  const P = tl.plan, M = tl.measured || {};
  const has = (a) => a && a.some((v) => v !== null);
  const C = {
    grid: cssVar("--c-grid"), bat: cssVar("--c-battery"), ren: cssVar("--c-renewable"), band: cssVar("--c-band"),
    load: cssVar("--c-demand"), soc: cssVar("--c-soc"), limit: cssVar("--c-limit"), price: cssVar("--c-price"),
  };
  const sum = (a, b) => a.map((v, i) => (v === null && b[i] === null ? null : (v || 0) + (b[i] || 0)));

  // what to draw in each view
  let spec;
  if (view === "sources") {
    spec = {
      unit: "W",
      areas: [{ label: "Grid", values: P.grid, color: C.grid }, { label: "Battery", values: P.discharge, color: C.bat }],
      lines: has(M.load) ? [{ label: "Measured load", values: M.load, color: C.load }] : [],
      tip: (i) => [["Load", P.demand, "W", C.load], ["Grid", P.grid, "W", C.grid], ["Battery", P.discharge, "W", C.bat], has(M.load) && ["Measured load", M.load, "W", C.load]],
    };
  } else if (view === "battery") {
    spec = {
      unit: "%", fixed: [0, 100],
      zone: has(P.soc_min) ? { lower: P.soc_min, upper: P.soc_max || P.soc_min.map(() => 90), label: "Allowed range" } : null,
      steps: [{ label: "Planned SoC", values: P.soc, color: C.soc, width: 2.4 }],
      lines: has(M.soc) ? [{ label: "Measured SoC", values: M.soc, color: C.load }] : [],
      tip: (i) => [["Planned SoC", P.soc, "%", C.soc], ["Allowed from", P.soc_min, "%"], ["Allowed to", P.soc_max, "%"], ["Charging", P.charge, "W", C.ren], ["Discharging", P.discharge, "W", C.bat], has(M.soc) && ["Measured SoC", M.soc, "%", C.load]],
    };
  } else if (view === "renewables") {
    const up = P.renewable.map((v, i) => (v === null ? null : v + 2 * ((P.sigma && P.sigma[i]) || 0)));
    const lo = P.renewable.map((v, i) => (v === null ? null : Math.max(0, v - 2 * ((P.sigma && P.sigma[i]) || 0))));
    spec = {
      unit: "W",
      band: { lower: lo, upper: up, label: "Uncertainty (±2σ)" },
      areas: [{ label: "Stored in the battery", values: P.charge, color: C.ren, alpha: 0.45 }],
      steps: [{ label: "Forecast", values: P.renewable, color: C.ren, width: 2 }],
      lines: has(M.renewable) ? [{ label: "Measured", values: M.renewable, color: C.load }] : [],
      tip: (i) => [["Forecast", P.renewable, "W", C.ren], ["± 2σ", P.sigma && P.sigma.map((s) => (s === null ? null : 2 * s)), "W"], ["Stored", P.charge, "W", C.ren], ["Curtailed", P.curtailed, "W"], has(M.renewable) && ["Measured", M.renewable, "W", C.load]],
    };
  } else {
    spec = {
      unit: "€/MWh", digits: 0,
      highlight: { values: P.discharge, color: C.bat, label: "Battery supplies the load" },
      steps: [{ label: "Price", values: P.price, color: C.price, width: 2.2 }],
      tip: (i) => [["Price", P.price, "€/MWh", C.price], ["Grid", P.grid, "W", C.grid], ["Battery", P.discharge, "W", C.bat]],
    };
  }

  const W = Math.max(el.clientWidth, 320);
  const H = W < 600 ? 230 : 280;
  const m = { left: 46, right: 14, top: 22, bottom: 34 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
  const bw = iw / n;
  const x = (i) => m.left + i * bw;

  const vals = [];
  const push = (a) => a && a.forEach((v) => v !== null && Number.isFinite(v) && vals.push(v));
  (spec.lines || []).forEach((l) => push(l.values));
  (spec.steps || []).forEach((l) => push(l.values));
  if (spec.band) push(spec.band.upper);
  if (spec.areas) push(spec.areas.length > 1 ? sum(spec.areas[0].values, spec.areas[1].values) : spec.areas[0].values);
  const nt = niceTicks(Math.min(0, ...vals), Math.max(1e-6, ...vals), 4);
  const lo = spec.fixed ? spec.fixed[0] : nt.lo, hi = spec.fixed ? spec.fixed[1] : nt.hi;
  const ticks = spec.fixed ? [0, 25, 50, 75, 100] : nt.ticks;
  const y = (v) => m.top + ih - ((v - lo) / (hi - lo)) * ih;

  const nowFrac = (() => {
    const i = tl.date.findIndex((d) => d > tl.now);
    if (i === -1) return n;
    if (i === 0) return 0;
    const t = (s) => new Date(s.replace(" ", "T")).getTime();
    return i - 1 + Math.min(1, Math.max(0, (t(tl.now) - t(tl.date[i - 1])) / (t(tl.date[i]) - t(tl.date[i - 1]))));
  })();
  const nowIdx = Math.min(n - 1, Math.floor(nowFrac));

  // step-shaped path through values (null breaks the path)
  const stepD = (values, yy = y) => {
    let d = "";
    values.forEach((v, i) => {
      if (v === null || v === undefined) return;
      const prev = i > 0 ? values[i - 1] : null;
      d += prev === null || prev === undefined ? `M${x(i)},${yy(v)}` : ` V${yy(v)}`;
      d += ` H${x(i + 1)}`;
    });
    return d;
  };
  // filled step area between two value series
  const areaD = (top, bottom) => {
    const parts = [];
    let seg = [];
    const flush = () => {
      if (!seg.length) return;
      let d = `M${x(seg[0])},${y(top[seg[0]])}`;
      seg.forEach((i) => { d += ` V${y(top[i])} H${x(i + 1)}`; });
      for (let k = seg.length - 1; k >= 0; k--) { const i = seg[k]; d += ` V${y(bottom[i])} H${x(i)}`; }
      parts.push(d + " Z");
      seg = [];
    };
    top.forEach((v, i) => { if (v === null || bottom[i] === null) flush(); else seg.push(i); });
    flush();
    return parts.join(" ");
  };

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": TL_VIEWS[view].title });
  svg.appendChild(svgEl("rect", { x: m.left, y: m.top, width: Math.max(0, nowFrac * bw), height: ih, fill: "var(--surface-2)" }));
  for (const t of ticks) {
    svg.appendChild(svgEl("line", { x1: m.left, x2: W - m.right, y1: y(t), y2: y(t), stroke: "var(--grid-line)" }));
    const l = svgEl("text", { x: m.left - 8, y: y(t) + 4, "text-anchor": "end" });
    l.textContent = fmt(t, 0);
    svg.appendChild(l);
  }
  const u = svgEl("text", { x: m.left - 8, y: m.top - 9, "text-anchor": "end", class: "tl-unit" });
  u.textContent = spec.unit;
  svg.appendChild(u);

  if (spec.highlight) {
    // one block per continuous period, so there are no seams between quarter-hours
    let start = null;
    spec.highlight.values.forEach((v, i) => {
      const on = v !== null && v > 0.5;
      if (on && start === null) start = i;
      if ((!on || i === n - 1) && start !== null) {
        const end = on ? i + 1 : i;
        svg.appendChild(svgEl("rect", { x: x(start), y: m.top, width: x(end) - x(start), height: ih, fill: spec.highlight.color, opacity: 0.14 }));
        start = null;
      }
    });
  }
  if (spec.zone) {
    svg.appendChild(svgEl("path", { d: areaD(spec.zone.upper, spec.zone.lower), fill: C.soc, opacity: 0.08 }));
    svg.appendChild(svgEl("path", { d: stepD(spec.zone.lower), fill: "none", stroke: C.limit, "stroke-width": 1, "stroke-dasharray": "4 3", opacity: 0.7 }));
    svg.appendChild(svgEl("path", { d: stepD(spec.zone.upper), fill: "none", stroke: C.limit, "stroke-width": 1, "stroke-dasharray": "4 3", opacity: 0.7 }));
  }
  if (spec.band) svg.appendChild(svgEl("path", { d: areaD(spec.band.upper, spec.band.lower), fill: C.band }));
  if (spec.areas) {
    let base = spec.areas[0].values.map((v) => (v === null ? null : 0));
    for (const a of spec.areas) {
      const top = a.values.map((v, i) => (v === null || base[i] === null ? null : base[i] + Math.max(0, v)));
      svg.appendChild(svgEl("path", { d: areaD(top, base), fill: a.color, opacity: a.alpha ?? 0.75 }));
      svg.appendChild(svgEl("path", { d: stepD(top), fill: "none", stroke: a.color, "stroke-width": 1.2 }));
      base = top;
    }
  }
  for (const s of spec.steps || []) svg.appendChild(svgEl("path", { d: stepD(s.values), fill: "none", stroke: s.color, "stroke-width": s.width || 1.8, "stroke-linejoin": "round" }));
  for (const l of spec.lines || []) {
    let d = "";
    l.values.forEach((v, i) => {
      if (v === null) return;
      d += `${i > 0 && l.values[i - 1] !== null ? " L" : "M"}${x(i) + bw / 2},${y(v)}`;
    });
    svg.appendChild(svgEl("path", { d, fill: "none", stroke: l.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  }

  // time axis: a label every few hours, the date where the day changes
  const every = Math.max(1, Math.round(n / Math.max(2, Math.floor(iw / 70))));
  for (let i = 0; i < n; i += every) {
    const t = svgEl("text", { x: x(i), y: H - m.bottom + 16, "text-anchor": "middle" });
    t.textContent = timeOf(tl.date[i]);
    svg.appendChild(t);
    if (i === 0 || dayOf(tl.date[i]) !== dayOf(tl.date[Math.max(0, i - every)])) {
      const d = svgEl("text", { x: x(i), y: H - m.bottom + 29, "text-anchor": "middle" });
      d.textContent = dayOf(tl.date[i]);
      svg.appendChild(d);
    }
  }
  const nx = m.left + nowFrac * bw;
  svg.appendChild(svgEl("line", { x1: nx, x2: nx, y1: m.top - 6, y2: m.top + ih, stroke: "var(--text)", "stroke-width": 1.2, "stroke-dasharray": "3 3" }));
  const nl = svgEl("text", { x: nx + 5, y: m.top - 8, class: "tl-now" });
  nl.textContent = "now";
  svg.appendChild(nl);
  if (nowFrac * bw > 70) {
    const pl = svgEl("text", { x: nx - 6, y: m.top - 8, "text-anchor": "end", class: "tl-unit" });
    pl.textContent = "past";
    svg.appendChild(pl);
  }

  // hover
  const cursor = svgEl("line", { y1: m.top, y2: m.top + ih, stroke: "var(--text)", "stroke-width": 1, opacity: 0 });
  svg.appendChild(cursor);
  const overlay = svgEl("rect", { x: m.left, y: m.top, width: iw, height: ih, fill: "transparent" });
  svg.appendChild(overlay);
  const tip = $("#tooltip");
  overlay.addEventListener("mousemove", (ev) => {
    const rect = svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * W;
    const i = Math.min(n - 1, Math.max(0, Math.floor((px - m.left) / bw)));
    cursor.setAttribute("x1", x(i) + bw / 2); cursor.setAttribute("x2", x(i) + bw / 2); cursor.setAttribute("opacity", 0.35);
    const rows = spec.tip(i).filter(Boolean).map(([label, arr, unit, color]) => {
      const v = arr ? arr[i] : null;
      return `<div class="tt-row"><span>${color ? `<i style="background:${color}"></i>` : ""}${label}</span><span>${v === null || v === undefined ? "–" : `${fmt(v, unit === "€/MWh" ? 1 : 1)} ${unit}`}</span></div>`;
    }).join("");
    const end = i + 1 < n ? timeOf(tl.date[i + 1]) : "";
    tip.innerHTML = `<div class="tt-time">${dayOf(tl.date[i])} ${timeOf(tl.date[i])}–${end}${i < nowIdx ? " · past" : i === nowIdx ? " · now" : ""}</div>${rows}`;
    tip.hidden = false;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = `${ev.clientX + 16 + tw > window.innerWidth ? ev.clientX - tw - 16 : ev.clientX + 16}px`;
    tip.style.top = `${Math.min(ev.clientY + 14, window.innerHeight - th - 8)}px`;
  });
  overlay.addEventListener("mouseleave", () => { tip.hidden = true; cursor.setAttribute("opacity", 0); });
  el.innerHTML = "";
  el.appendChild(svg);

  // legend for this view only
  const items = [];
  (spec.areas || []).forEach((a) => items.push(`<span><i class="band" style="background:${a.color};opacity:${a.alpha ?? 0.75}"></i>${a.label}</span>`));
  if (spec.band) items.push(`<span><i class="band" style="background:${C.band}"></i>${spec.band.label}</span>`);
  if (spec.zone) items.push(`<span><i class="band" style="background:${C.soc};opacity:.25"></i>${spec.zone.label}</span>`);
  (spec.steps || []).forEach((s) => items.push(`<span><i style="border-color:${s.color}"></i>${s.label}</span>`));
  (spec.lines || []).forEach((l) => items.push(`<span><i style="border-color:${l.color}"></i>${l.label}</span>`));
  if (spec.highlight) items.push(`<span><i class="band" style="background:${spec.highlight.color};opacity:.3"></i>${spec.highlight.label}</span>`);
  $("#timeline-legend").innerHTML = items.join("");
}

// ------------------------------------------------------------------ tabs & wiring

function activateTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "overview") renderTimeline();
}

function init() {
  $$(".tab").forEach((t) => t.addEventListener("click", () => activateTab(t.dataset.tab)));
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-job]");
    if (b && !b.closest("#model-cards")) startJob(b.dataset.job);
  });
  $("#stop-job").addEventListener("click", async () => { await api("/api/jobs/stop", { method: "POST" }); refreshStatusSoon(); });
  $("#toggle-log").addEventListener("click", () => {
    state.logOpen = !state.logOpen;
    $("#job-log").hidden = !state.logOpen;
    $("#toggle-log").textContent = state.logOpen ? "Hide log" : "Show log";
  });
  $("#hide-job").addEventListener("click", () => {
    const job = state.status && state.status.job;
    if (job && job.status !== "running") { state.jobHiddenFor = job.finished_at; $("#jobbar").hidden = true; }
  });

  $("#save-config").addEventListener("click", saveConfig);
  $("#reload-config").addEventListener("click", loadConfig);
  const tbody = $("#schedule-table tbody");
  tbody.addEventListener("change", onScheduleInput);
  tbody.addEventListener("click", (ev) => {
    const i = ev.target.dataset.remove;
    if (i !== undefined) { state.schedule.splice(Number(i), 1); renderSchedule(); }
  });
  $("#add-row").addEventListener("click", addScheduleRow);
  $("#save-schedule").addEventListener("click", saveSchedule);

  $$("#res-period button").forEach((b) => b.addEventListener("click", () => {
    $$("#res-period button").forEach((x) => x.classList.toggle("active", x === b));
    state.resPeriod = b.dataset.period;
    loadLiveResults();
  }));
  $$("#tl-views button").forEach((b) => b.addEventListener("click", () => {
    $$("#tl-views button").forEach((x) => x.classList.toggle("active", x === b));
    state.tlView = b.dataset.view;
    renderTimeline();
  }));
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderTimeline, 150); });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderTimeline);

  renderLiveSetup();
  loadConfig();
  loadSchedule();
  refreshStatus();
  loadOverview();
  setInterval(loadOverview, 30000);  // keeps "now", the decision in force and live values current
}

init();
