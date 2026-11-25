"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (r.status === 401) {
    const d = await r.json().catch(() => ({}));
    if (d && d.error === "auth_required") { window.location.href = d.login || "/auth/login"; }
    throw new Error("Please sign in.");
  }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
};
const post = (body) => api("/api/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const HEADINGS = {
  patterns: { label: "Patterns", tools: ["patterns", "triangle"] },
  sd: { label: "Supply & Demand", tools: ["sd", "zd"] },
  volume: { label: "Volume", tools: ["volume"] },
};
const TOOLS = {
  patterns: { name: "Pattern Radar", sub: "Classical + candlestick · 5m–1W" },
  triangle: { name: "Triangle Breakout", sub: "Daily · volume-confirmed" },
  sd: { name: "SD Radar", sub: "Zones · Daily/Weekly/Monthly/Quarterly" },
  zd: { name: "ZoneDesk", sub: "Seven income trade types" },
  volume: { name: "Nifty 50 Volume Delta", sub: "Candle-based approximation" },
};
const TF_LABEL = { D: "Daily", W: "Weekly", M: "Monthly", Q: "Quarterly" };

const state = {
  heading: "sd", tool: "sd", universe: "nifty50", custom: "",
  volWindow: "session", volInterval: "5m", volIndices: false,
  zdTypes: ["DIT", "WIT", "MIT"], zdMin: 4,
  triVol: 1.3, triDays: 2,
  patternTfs: ["1d", "1h", "15m", "5m", "1w"], patternFilter: "all",
  sort: {}, polling: null, meta: null, lastResult: null,
};

// ---------- formatting ----------
const nf = (v, d = 2) => (v == null || isNaN(v)) ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const intf = (v) => (v == null) ? "—" : Math.round(v).toLocaleString("en-IN");
const compact = (v) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e7) return (v / 1e7).toFixed(2) + " Cr";
  if (a >= 1e5) return (v / 1e5).toFixed(2) + " L";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + " K";
  return Math.round(v).toString();
};
const cls = (v) => v > 0 ? "pos" : v < 0 ? "neg" : "neu";
const sign = (v, d = 2) => (v > 0 ? "+" : "") + nf(v, d);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function sparkSVG(vals, w = 90, h = 26, color) {
  if (!vals || vals.length < 2) return "";
  const min = Math.min(...vals), max = Math.max(...vals), rng = (max - min) || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - (v - min) / rng * (h - 3) - 1.5).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  const c = color || (up ? "var(--green)" : "var(--red)");
  return `<svg class="spark" width="${w}" height="${h}"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.4"/></svg>`;
}
function heatColor(h) {
  if (h >= 70) return "background:rgba(34,197,94,.2);color:var(--green)";
  if (h >= 45) return "background:rgba(245,158,11,.18);color:var(--amber)";
  return "background:rgba(139,160,189,.14);color:var(--muted)";
}

// ---------- navigation ----------
function renderTooltabs() {
  const tools = HEADINGS[state.heading].tools;
  $("#tooltabs").innerHTML = tools.map(t =>
    `<button class="tooltab ${t === state.tool ? "active" : ""}" data-tool="${t}">${TOOLS[t].name}<span class="sub">${TOOLS[t].sub}</span></button>`).join("");
  $$(".tooltab").forEach(b => b.onclick = () => { state.tool = b.dataset.tool; state.sort = {}; renderTooltabs(); renderOpts(); loadState(); });
}
function renderOpts() {
  const el = $("#toolOpts");
  if (state.tool === "volume") {
    el.innerHTML = `<label>Interval</label><div class="opts-inline">
      ${["1m", "5m"].map(i => `<label class="chk"><input type="radio" name="vi" value="${i}" ${state.volInterval === i ? "checked" : ""}>${i}</label>`).join("")}
      <label style="margin-left:8px">Window</label>
      <select id="volWindow">${["session", "5m", "15m", "30m", "1h", "2h", "4h"].map(w => `<option value="${w}" ${state.volWindow === w ? "selected" : ""}>${w === "session" ? "Session" : w}</option>`).join("")}</select>
      <label class="chk" style="margin-left:8px"><input type="checkbox" id="volIdx" ${state.volIndices ? "checked" : ""}>Indices</label></div>`;
    $$("input[name=vi]", el).forEach(r => r.onchange = () => { state.volInterval = r.value; });
    $("#volIdx", el).onchange = e => { state.volIndices = e.target.checked; };
    $("#volWindow", el).onchange = e => { state.volWindow = e.target.value; renderResults(); };
  } else if (state.tool === "zd") {
    const trades = state.meta?.trades || {};
    el.innerHTML = `<label>Trade types</label><div class="opts-inline">
      ${(state.meta?.trade_order || ["HIT", "DIT", "WIT", "MIT", "QIT", "HYIT", "YIT"]).map(t =>
        `<label class="chk" title="${trades[t]?.name || ""}"><input type="checkbox" value="${t}" ${state.zdTypes.includes(t) ? "checked" : ""}>${t}</label>`).join("")}
      <label style="margin-left:6px">Min score</label><input type="text" id="zdMin" value="${state.zdMin}" style="min-width:54px;width:54px"></div>`;
    $$("input[type=checkbox]", el).forEach(c => c.onchange = () => {
      state.zdTypes = $$("input[type=checkbox]:checked", el).map(x => x.value);
    });
    $("#zdMin", el).onchange = e => { state.zdMin = parseFloat(e.target.value) || 4; };
  } else if (state.tool === "triangle") {
    el.innerHTML = `<label>Filters</label><div class="opts-inline">
      <span class="small muted">Vol ≥</span><input type="text" id="triVol" value="${state.triVol}" style="min-width:54px;width:54px">
      <span class="small muted">× · last</span><input type="text" id="triDays" value="${state.triDays}" style="min-width:48px;width:48px">
      <span class="small muted">sessions</span></div>`;
    $("#triVol", el).onchange = e => { state.triVol = parseFloat(e.target.value) || 1.3; };
    $("#triDays", el).onchange = e => { state.triDays = parseInt(e.target.value) || 0; };
  } else if (state.tool === "patterns") {
    el.innerHTML = `<label>Timeframes</label><div class="opts-inline">
      ${["5m", "15m", "1h", "1d", "1w"].map(t => `<label class="chk"><input type="checkbox" value="${t}" ${state.patternTfs.includes(t) ? "checked" : ""}>${t}</label>`).join("")}</div>`;
    $$("input[type=checkbox]", el).forEach(c => c.onchange = () => {
      state.patternTfs = $$("input[type=checkbox]:checked", el).map(x => x.value);
    });
  } else { el.innerHTML = ""; }
}

function scanParams(force) {
  const p = { tool: state.tool, universe: state.universe, symbols: state.universe === "custom" ? state.custom : "", force };
  if (state.tool === "volume") { p.interval = state.volInterval; if (state.volIndices) p.include_indices = true; }
  if (state.tool === "zd") p.opts = { types: state.zdTypes, min_score: state.zdMin };
  if (state.tool === "triangle") p.opts = { vol_mult: state.triVol, days_since: state.triDays };
  if (state.tool === "patterns") p.timeframes = state.patternTfs;
  return p;
}

// ---------- scan lifecycle ----------
async function startScan(force) {
  if (state.universe === "custom" && !state.custom.trim()) { alert("Enter at least one symbol."); return; }
  $("#scanBtn").disabled = true;
  try {
    await post(scanParams(force));
    poll();
  } catch (e) { $("#statusText").textContent = "Error: " + e.message; $("#scanBtn").disabled = false; }
}
function poll() {
  clearInterval(state.polling);
  state.polling = setInterval(loadState, 1200);
  loadState();
}
async function loadState() {
  let s;
  try { s = await api("/api/state?tool=" + state.tool); } catch { return; }
  const pct = s.total ? Math.round(s.progress / s.total * 100) : 0;
  $("#progressBar").style.width = (s.state === "ready" ? 100 : pct) + "%";
  if (s.state === "scanning") {
    $("#statusText").textContent = `Scanning ${s.progress}/${s.total}…`;
    $("#scanBtn").disabled = true;
  } else {
    $("#scanBtn").disabled = false;
    if (s.state === "ready") {
      $("#statusText").textContent = `${s.total} symbols · ${s.duration}s`;
      $("#genInfo").textContent = "Updated " + new Date(s.generated * 1000).toLocaleTimeString();
      state.lastResult = s.result;
      renderStats(); renderResults();
      clearInterval(state.polling);
    } else if (s.state === "error") {
      $("#statusText").textContent = "Error: " + s.error;
      clearInterval(state.polling);
    } else {
      $("#statusText").textContent = "Ready — press Scan";
      $("#statsRow").innerHTML = "";
      $("#results").innerHTML = emptyState();
    }
  }
}
function emptyState() {
  return `<div class="empty"><div class="big">📡</div><p>Press <b>Scan</b> to run ${TOOLS[state.tool].name} on the selected universe.</p>
    <p class="small muted">Yahoo data is ~15 min delayed. Larger universes take longer on the free host.</p></div>`;
}

// ---------- stats ----------
function stat(v, k, c = "") { return `<div class="stat ${c}"><div class="v">${v}</div><div class="k">${k}</div></div>`; }
function renderStats() {
  const r = state.lastResult; if (!r) return;
  let h = "";
  if (state.tool === "sd") {
    const s = r.stats;
    h = stat(s.symbols, "Symbols") + stat(s.signals, "Signals", "accent") + stat(s.approaching, "Approaching", "green") +
      stat(s.in_zone, "In zone", "green") + stat(s.fresh, "Fresh") + stat(s.confluence, "Confluence ≥2TF") +
      stat(s.demand, "Demand", "green") + stat(s.supply, "Supply", "red") + (s.errors ? stat(s.errors, "Errors", "red") : "");
  } else if (state.tool === "triangle") {
    const s = r.stats;
    h = stat(s.symbols, "Symbols") + stat(s.hits, "Breakouts", "accent") + stat(s.vol_mult + "×", "Min volume") +
      stat(s.days_since + 1, "Sessions") + (s.errors ? stat(s.errors, "Errors", "red") : "");
  } else if (state.tool === "volume") {
    const a = r.aggregate, w = state.volWindow === "session" ? a : a.windows[state.volWindow];
    h = stat(compact(a.volume), "Volume") + stat((w.delta > 0 ? "+" : "") + compact(w.delta), "Net delta", cls(w.delta)) +
      stat(compact(w.buy_volume), "Buy vol", "green") + stat(compact(w.sell_volume), "Sell vol", "red") +
      stat(w.advancing, "Advancing", "green") + stat(w.declining, "Declining", "red") +
      (w.top_buy ? stat(w.top_buy.symbol, "Top buy", "green") : "") + (w.top_sell ? stat(w.top_sell.symbol, "Top sell", "red") : "");
  } else if (state.tool === "zd") {
    const s = r.stats;
    h = stat(s.symbols, "Symbols") + stat(s.tradeable, "Trade-ready", "accent") +
      Object.entries(s.by_type).map(([t, n]) => stat(n, t)).join("") + (s.errors ? stat(s.errors, "Errors", "red") : "");
  } else if (state.tool === "patterns") {
    const s = r.stats || {};
    h = stat(s.symbols ?? "—", "Symbols") + stat(s.patterns ?? (r.hits ? r.hits.length : "—"), "Patterns", "accent") +
      (s.errors ? stat(s.errors, "Errors", "red") : "");
  }
  $("#statsRow").innerHTML = h;
}

// ---------- results tables ----------
function sortRows(rows, key, dir) {
  return [...rows].sort((a, b) => {
    let x = a[key], y = b[key];
    if (typeof x === "string") return dir * x.localeCompare(y);
    return dir * ((x ?? -1e18) - (y ?? -1e18));
  });
}
function getSort(tool, defKey) {
  const s = state.sort[tool] || { key: defKey, dir: -1 };
  return s;
}
function th(label, key, tool, left) {
  const s = getSort(tool, key);
  const arrow = s.key === key ? (s.dir < 0 ? " ▾" : " ▴") : "";
  return `<th class="${left ? "l" : ""}" data-key="${key}">${label}${arrow}</th>`;
}
function bindSort(tool, rerender) {
  $$("#results thead th").forEach(h => h.onclick = () => {
    const key = h.dataset.key; if (!key) return;
    const cur = getSort(tool, key);
    state.sort[tool] = { key, dir: cur.key === key ? -cur.dir : -1 };
    rerender();
  });
}

function renderResults() {
  const r = state.lastResult;
  if (!r) { $("#results").innerHTML = emptyState(); return; }
  if (state.tool === "sd") return renderSD(r);
  if (state.tool === "triangle") return renderTriangle(r);
  if (state.tool === "volume") return renderVolume(r);
  if (state.tool === "zd") return renderZD(r);
  if (state.tool === "patterns") return renderPatterns(r);
}

function renderSD(r) {
  const s = getSort("sd", "priority");
  const rows = sortRows(r.signals, s.key, s.dir);
  if (!rows.length) { $("#results").innerHTML = `<div class="empty"><div class="big">🧱</div><p>No zones near price right now.</p></div>`; return; }
  const head = `<tr>${th("Symbol", "symbol", "sd", 1)}${th("TF", "tf", "sd")}${th("Side", "type", "sd")}${th("Kind", "kind", "sd")}${th("Status", "status", "sd")}${th("Price", "price", "sd")}${th("Zone", "proximal", "sd")}${th("Dist %", "distance_pct", "sd")}${th("ETA", "eta_text", "sd")}${th("Heat", "heat", "sd")}${th("Inst", "inst", "sd")}${th("Tests", "touches", "sd")}${th("R:R", "rr", "sd")}${th("Conf", "confluence", "sd")}</tr>`;
  const body = rows.map(z => {
    const side = z.type === "demand" ? "tag-demand" : "tag-supply";
    const lo = Math.min(z.proximal, z.distal), hi = Math.max(z.proximal, z.distal);
    const st = z.status === "IN_ZONE" ? "pos" : z.status === "APPROACHING" ? "pos" : "neu";
    return `<tr data-sym="${esc(z.symbol)}" data-tf="${z.tf}">
      <td class="l"><span class="sym">${esc(z.symbol.replace(".NS", ""))}</span><span class="symname">${esc(z.name)}</span></td>
      <td><span class="tag tag-tf">${z.tf}</span></td>
      <td><span class="tag ${side}">${z.type}</span></td>
      <td>${z.kind}</td><td class="${st}">${z.status.replace("_", " ").toLowerCase()}</td>
      <td data-live="price">${nf(z.price)}</td><td>${nf(lo)}–${nf(hi)}</td>
      <td class="${z.distance_pct <= z.threshold_pct ? "pos" : ""}">${z.status === "IN_ZONE" ? "IN" : nf(z.distance_pct)}</td>
      <td>${z.eta_text}</td><td><span class="heat" style="${heatColor(z.heat)}">${z.heat}</span></td>
      <td>${z.inst}</td><td>${z.fresh ? "fresh" : z.touches + "×"}</td><td>${nf(z.rr, 1)}</td>
      <td>${z.confluence >= 2 ? `<span class="pill">${z.confluence}TF</span>` : "—"}</td></tr>`;
  }).join("");
  $("#results").innerHTML = `<div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
  bindSort("sd", renderResults);
  $$("#results tbody tr").forEach(tr => tr.onclick = () => openSDChart(tr.dataset.sym, tr.dataset.tf));
}

function renderTriangle(r) {
  const s = getSort("triangle", "score");
  const rows = sortRows(r.hits, s.key, s.dir);
  if (!rows.length) { $("#results").innerHTML = `<div class="empty"><div class="big">📐</div><p>No confirmed triangle breakouts matched the filters.</p><p class="small muted">Try a larger universe or more sessions.</p></div>`; return; }
  const head = `<tr>${th("Symbol", "symbol", "triangle", 1)}${th("Pattern", "pattern", "triangle")}${th("Signal", "days_since", "triangle")}${th("Close", "close", "triangle")}${th("Level", "level", "triangle")}${th("% Above", "pct_above", "triangle")}${th("Vol ×20d", "vol_ratio", "triangle")}${th("Touches", "touches_up", "triangle")}${th("Bars", "bars", "triangle")}${th("Contract", "tightness", "triangle")}${th("Stop", "stop", "triangle")}${th("Target", "target", "triangle")}${th("R:R", "rr", "triangle")}${th("Score", "score", "triangle")}</tr>`;
  const body = rows.map(h => `<tr data-sym="${esc(h.yahooSymbol)}">
    <td class="l"><span class="sym">${esc(h.symbol)}</span><span class="symname">${esc(h.name || "")}</span></td>
    <td><span class="tag tag-tf">${h.pattern}</span></td>
    <td class="${h.days_since === 0 ? "pos" : "neu"}">${h.days_since === 0 ? "today" : h.days_since + "d ago"}</td>
    <td data-live="price">${nf(h.close)}</td><td>${nf(h.level)}</td><td class="pos">${sign(h.pct_above)}</td>
    <td class="${h.vol_ratio >= 2 ? "pos" : ""}">${nf(h.vol_ratio)}×</td>
    <td>${h.touches_up}/${h.touches_dn}</td><td>${h.bars}</td><td>${nf(h.tightness)}</td>
    <td>${nf(h.stop)}</td><td>${nf(h.target)}</td><td>${nf(h.rr, 1)}</td>
    <td><span class="heat" style="${heatColor(h.score)}">${h.score}</span></td></tr>`).join("");
  $("#results").innerHTML = `<div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
  bindSort("triangle", renderResults);
  $$("#results tbody tr").forEach(tr => tr.onclick = () => {
    const h = r.hits.find(x => x.yahooSymbol === tr.dataset.sym); openTriangleChart(h);
  });
}

function renderVolume(r) {
  const w = state.volWindow;
  const dKey = w === "session" ? "delta" : "delta_" + w;
  const s = getSort("volume", dKey);
  const rows = sortRows(r.rows, s.key, s.dir);
  if (!rows.length) { $("#results").innerHTML = `<div class="empty"><div class="big">📊</div><p>No intraday data.</p></div>`; return; }
  const head = `<tr>${th("Symbol", "symbol", "volume", 1)}${th("LTP", "ltp", "volume")}${th("Chg %", "change_pct", "volume")}${th("Volume", "volume", "volume")}${th("Buy", "buy_volume", "volume")}${th("Sell", "sell_volume", "volume")}${th("Delta", dKey, "volume")}${th("Delta %", "delta_pct", "volume")}${th("Trend", "delta", "volume")}</tr>`;
  const body = rows.map(x => {
    const d = w === "session" ? x.delta : x["delta_" + w];
    return `<tr data-sym="${esc(x.yahooSymbol)}">
      <td class="l"><span class="sym">${esc(x.symbol)}</span><span class="symname">${esc(x.company)}</span></td>
      <td data-live="price">${nf(x.ltp)}</td><td data-live="chg" class="${cls(x.change_pct)}">${sign(x.change_pct)}</td>
      <td>${compact(x.volume)}</td><td class="pos">${compact(x.buy_volume)}</td><td class="neg">${compact(x.sell_volume)}</td>
      <td class="${cls(d)}">${(d > 0 ? "+" : "") + compact(d)}</td>
      <td class="${cls(x.delta_pct)}">${sign(x.delta_pct)}</td><td>${sparkSVG(x.spark)}</td></tr>`;
  }).join("");
  $("#results").innerHTML = `<div class="note">⚠ Volume delta is approximated from ${r.interval} candle direction (tick rule), not Kotak tick data. Connect Kotak for live LTP; true tick delta needs the desktop app.</div>
    <div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
  bindSort("volume", renderResults);
  $$("#results tbody tr").forEach(tr => tr.onclick = () => {
    const x = r.rows.find(v => v.yahooSymbol === tr.dataset.sym); openVolumeDetail(x);
  });
}

function renderZD(r) {
  const rows = r.tradeable && r.tradeable.length ? r.tradeable : null;
  if (rows) {
    const s = getSort("zd", "score");
    const sorted = sortRows(rows, s.key, s.dir);
    const head = `<tr>${th("Symbol", "symbol", "zd", 1)}${th("Type", "tradeType", "zd")}${th("Horizon", "horizon", "zd")}${th("Action", "action", "zd")}${th("Score", "score", "zd")}${th("Grade", "grade", "zd")}${th("Entry", "entry", "zd")}${th("Stop", "stop", "zd")}${th("Target", "target", "zd")}${th("R:R", "rr", "zd")}${th("Gap %", "entryGapPct", "zd")}${th("Zone", "freshnessLabel", "zd")}</tr>`;
    const body = sorted.map(f => {
      const p = f.plan || {};
      return `<tr data-sym="${esc(f.yahooSymbol)}" data-type="${f.tradeType}">
        <td class="l"><span class="sym">${esc(f.symbol)}</span><span class="symname">${esc(f.name || "")}</span></td>
        <td><span class="tag tag-tf">${f.tradeType}</span></td><td>${f.horizon}</td>
        <td><span class="tag ${f.action === "BUY" ? "tag-buy" : "tag-sell"}">${f.action || "—"}</span></td>
        <td><span class="heat" style="${heatColor((f.score || 0) * 12)}">${nf(f.score, 1)}</span></td>
        <td><span class="tag tag-grade">${f.grade}</span></td>
        <td>${nf(p.entry)}</td><td>${nf(p.stop)}</td><td>${nf(p.target)}</td><td>${nf(p.rr, 1)}</td>
        <td>${nf(f.entryGapPct)}</td><td>${f.freshnessLabel || "—"}</td></tr>`;
    }).join("");
    $("#results").innerHTML = `<div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    bindSort("zd", renderResults);
    $$("#results tbody tr").forEach(tr => tr.onclick = () => openZDDetail(tr.dataset.sym, tr.dataset.type));
  } else {
    const s = getSort("zd", "score");
    const sorted = sortRows(r.results.filter(x => x.available !== false), s.key, s.dir);
    const head = `<tr>${th("Symbol", "symbol", "zd", 1)}${th("Best type", "bestTradeType", "zd")}${th("Score", "score", "zd")}${th("Grade", "grade", "zd")}${th("Status", "statusLabel", "zd")}${th("Action", "action", "zd")}</tr>`;
    const body = sorted.map(x => `<tr data-sym="${esc(x.yahooSymbol || (x.symbol + ".NS"))}" data-type="${x.bestTradeType || "DIT"}">
      <td class="l"><span class="sym">${esc(x.symbol)}</span><span class="symname">${esc(x.name || "")}</span></td>
      <td><span class="tag tag-tf">${x.bestTradeType || "—"}</span></td>
      <td>${nf(x.score, 1)}</td><td><span class="tag tag-grade">${x.grade || "—"}</span></td>
      <td>${x.statusLabel || "—"}</td><td>${x.action ? `<span class="tag ${x.action === "BUY" ? "tag-buy" : "tag-sell"}">${x.action}</span>` : "—"}</td></tr>`).join("");
    $("#results").innerHTML = `<div class="note">No trade-ready setups at current prices. Showing the best-scanned setup per symbol — click for the full plan.</div>
      <div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    bindSort("zd", renderResults);
    $$("#results tbody tr").forEach(tr => tr.onclick = () => openZDDetail(tr.dataset.sym, tr.dataset.type));
  }
}

function renderPatterns(r) {
  const hits = r.hits || r.patterns || [];
  if (!hits.length) { $("#results").innerHTML = `<div class="empty"><div class="big">📐</div><p>No patterns detected.</p></div>`; return; }
  const pname = h => h.pattern || h.name || "—";
  const counts = new Map();
  hits.forEach(h => { const n = pname(h); counts.set(n, (counts.get(n) || 0) + 1); });
  const names = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b));
  if (state.patternFilter !== "all" && !counts.has(state.patternFilter)) state.patternFilter = "all";
  const filtered = state.patternFilter === "all" ? hits : hits.filter(h => pname(h) === state.patternFilter);
  const s = getSort("patterns", "confidence");
  const rows = sortRows(filtered, s.key, s.dir);
  const tab = (val, label, n) => `<button class="tooltab ${state.patternFilter === val ? "active" : ""}" data-pat="${esc(val)}">${esc(label)}<span class="sub">${n}</span></button>`;
  const tabs = `<div class="tooltabs" id="patTabs">${tab("all", "All patterns", hits.length)}${names.map(n => tab(n, n, counts.get(n))).join("")}</div>`;
  const head = `<tr>${th("Symbol", "symbol", "patterns", 1)}${th("TF", "tf", "patterns")}${th("Pattern", "pattern", "patterns")}${th("Category", "category", "patterns")}${th("Direction", "direction", "patterns")}${th("Confidence", "confidence", "patterns")}${th("Price", "price", "patterns")}</tr>`;
  const body = rows.map(h => `<tr data-sym="${esc(h.yahooSymbol || h.symbol)}" data-tf="${esc(h.tf || h.timeframe || "")}">
    <td class="l"><span class="sym">${esc((h.symbol || "").replace(".NS", ""))}</span><span class="symname">${esc(h.name || "")}</span></td>
    <td><span class="tag tag-tf">${h.tf || h.timeframe || ""}</span></td>
    <td>${esc(h.pattern || h.name || "")}</td><td>${esc(h.category || "")}</td>
    <td><span class="tag ${h.direction === "bullish" ? "tag-buy" : h.direction === "bearish" ? "tag-sell" : "tag-neutral"}">${h.direction || "—"}</span></td>
    <td><span class="heat" style="${heatColor(h.confidence || 0)}">${h.confidence ?? "—"}</span></td>
    <td data-live="price">${nf(h.price)}</td></tr>`).join("") ||
    `<tr><td colspan="7" class="l muted">No stocks matched this pattern.</td></tr>`;
  $("#results").innerHTML = `${tabs}<div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
  bindSort("patterns", renderResults);
  $$("#patTabs .tooltab").forEach(b => b.onclick = () => { state.patternFilter = b.dataset.pat; renderResults(); });
  $$("#results tbody tr").forEach(tr => tr.onclick = () => {
    const h = rows.find(x => (x.yahooSymbol || x.symbol) === tr.dataset.sym && (x.tf || x.timeframe) === tr.dataset.tf);
    if (h) openPatternChart(h);
  });
}

// ---------- canvas charts ----------
function drawCandles(canvas, candles, zones, overlays) {
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth || 700, H = 300;
  canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.height = H + "px";
  const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);
  if (!candles.length) return;
  const padL = 8, padR = 56, padT = 10, padB = 18;
  const n = candles.length;
  let min = Infinity, max = -Infinity;
  candles.forEach(c => { min = Math.min(min, c.l); max = Math.max(max, c.h); });
  (zones || []).forEach(z => { min = Math.min(min, z.bottom ?? z.distal); max = Math.max(max, z.top ?? z.proximal); });
  const rng = (max - min) || 1; min -= rng * 0.04; max += rng * 0.04;
  const x = i => padL + (i + 0.5) / n * (W - padL - padR);
  const y = v => padT + (max - v) / (max - min) * (H - padT - padB);
  const cw = Math.max(1.5, (W - padL - padR) / n * 0.62);
  // grid + price axis
  const light = document.documentElement.dataset.theme === "light";
  ctx.strokeStyle = light ? "#d6dfec" : "#22344f"; ctx.fillStyle = light ? "#8494ad" : "#5f7392"; ctx.font = "10px monospace"; ctx.lineWidth = 1;
  for (let g = 0; g <= 4; g++) {
    const v = min + (max - min) * g / 4, yy = y(v);
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
    ctx.fillText(v.toFixed(v >= 100 ? 0 : 2), W - padR + 4, yy + 3);
  }
  // zones
  (zones || []).forEach(z => {
    const top = z.top ?? Math.max(z.proximal, z.distal), bot = z.bottom ?? Math.min(z.proximal, z.distal);
    const dem = (z.direction || z.type) === "demand";
    const x0 = z.x0 != null ? x(z.x0) - cw : padL;
    const x1 = z.x1 != null ? x(z.x1) + cw : W - padR;
    ctx.fillStyle = dem ? "rgba(34,197,94,.13)" : "rgba(239,68,68,.13)";
    ctx.fillRect(x0, y(top), Math.max(x1 - x0, 20), y(bot) - y(top));
    ctx.strokeStyle = dem ? "rgba(34,197,94,.5)" : "rgba(239,68,68,.5)";
    ctx.strokeRect(x0, y(top), Math.max(x1 - x0, 20), y(bot) - y(top));
  });
  // candles
  candles.forEach((c, i) => {
    const up = c.c >= c.o, col = up ? "#22c55e" : "#ef4444";
    const xx = x(i);
    ctx.strokeStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(xx, y(c.h)); ctx.lineTo(xx, y(c.l)); ctx.stroke();
    ctx.fillStyle = col;
    const yo = y(c.o), yc = y(c.c);
    ctx.fillRect(xx - cw / 2, Math.min(yo, yc), cw, Math.max(1, Math.abs(yc - yo)));
  });
  // overlays (lines)
  (overlays || []).forEach(ov => {
    ctx.strokeStyle = ov.color; ctx.lineWidth = ov.width || 1.4;
    ctx.setLineDash(ov.dash || []);
    if (ov.points) {
      ctx.beginPath();
      ov.points.forEach((p, i) => { const xx = x(p[0]), yy = y(p[1]); i ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy); });
      ctx.stroke();
    } else if (ov.level != null) {
      ctx.beginPath(); ctx.moveTo(padL, y(ov.level)); ctx.lineTo(W - padR, y(ov.level)); ctx.stroke();
    }
    ctx.setLineDash([]);
  });
  chartRedraw = () => drawCandles(canvas, candles, zones, overlays);
}
let chartRedraw = null;
function toCandles(arr) { return arr.map(c => Array.isArray(c) ? { t: c[0], o: c[1], h: c[2], l: c[3], c: c[4], v: c[5] } : c); }

function openDrawer(title, sub) {
  $("#drawerTitle").textContent = title; $("#drawerSub").textContent = sub || "";
  $("#drawerBody").innerHTML = `<div class="empty"><div class="big">⏳</div><p>Loading…</p></div>`;
  $("#drawer").classList.remove("hidden");
}
function closeDrawer() { $("#drawer").classList.add("hidden"); }

async function openSDChart(sym, tf) {
  openDrawer(sym.replace(".NS", ""), "SD Radar · supply & demand zones");
  let d;
  try { d = await api(`/api/chart?symbol=${encodeURIComponent(sym)}&tf=${tf}`); }
  catch (e) { $("#drawerBody").innerHTML = `<p class="neg">${esc(e.message)}</p>`; return; }
  const tfBtns = ["D", "W", "M", "Q"].map(t => `<button class="btn btn-ghost sd-tf ${t === tf ? "btn-primary" : ""}" data-tf="${t}">${TF_LABEL[t]}</button>`).join("");
  const zones = d.zones.filter(z => !z.broken).slice(0, 12);
  $("#drawerBody").innerHTML = `
    <div class="opts-inline" style="margin-bottom:10px">${tfBtns}</div>
    <div class="chart-box"><canvas id="sdCanvas"></canvas></div>
    <div class="kv">
      <div class="item"><div class="k">Price</div><div class="v">${nf(d.price)}</div></div>
      <div class="item"><div class="k">Timeframe</div><div class="v">${d.tf_label}</div></div>
      <div class="item"><div class="k">Active zones</div><div class="v">${zones.length}</div></div>
    </div>
    <div class="section-title">Zones</div>
    <div class="setup-list">${zones.map(z => `
      <div class="setup-item"><div class="row">
        <span><span class="tag ${z.type === "demand" ? "tag-demand" : "tag-supply"}">${z.type}</span>
        <span class="pill">${z.kind}</span> <b>${nf(Math.min(z.proximal, z.distal))}–${nf(Math.max(z.proximal, z.distal))}</b></span>
        <span class="muted small">heat ${z.heat} · ${z.status.replace("_", " ").toLowerCase()} · ${z.eta_text}${z.rr ? " · R:R " + nf(z.rr, 1) : ""}</span>
      </div></div>`).join("") || '<p class="muted">No active zones.</p>'}</div>`;
  drawCandles($("#sdCanvas"), toCandles(d.candles), zones);
  $$(".sd-tf").forEach(b => b.onclick = () => openSDChart(sym, b.dataset.tf));
}

async function openPatternChart(h) {
  if (!h) return;
  const sym = h.yahooSymbol || h.symbol;
  const tf = h.tf || h.timeframe || "1d";
  openDrawer(h.symbol, `Pattern Radar · ${h.pattern || h.name || ""}`);
  let d;
  try { d = await api(`/api/patterns/chart?symbol=${encodeURIComponent(sym)}&tf=${tf}`); }
  catch (e) { $("#drawerBody").innerHTML = `<p class="neg">${esc(e.message)}</p>`; return; }
  const candles = toCandles(d.candles);
  const idxByTime = {}; candles.forEach((c, i) => { idxByTime[c.t] = i; });
  const pats = d.patterns || [];
  const sel = pats.find(p => p.name === (h.pattern || h.name)) || pats[0] || h;
  const bull = (sel.direction || h.direction) === "bullish";
  const lineCol = bull ? "#22c55e" : "#ef4444";
  const overlays = [];
  (sel.lines || []).forEach(ln => {
    const pts = (ln.points || []).map(p => [idxByTime[p.time], p.value]).filter(p => p[0] != null);
    if (pts.length >= 2) overlays.push({ points: pts, color: ln.role === "breakout" ? "#38bdf8" : lineCol, width: 1.5, dash: ln.dash ? [5, 4] : [] });
  });
  if (sel.breakout) overlays.push({ level: sel.breakout, color: "#38bdf8", dash: [5, 4] });
  if (sel.target) overlays.push({ level: sel.target, color: "#22c55e", dash: [2, 3] });
  if (sel.stop) overlays.push({ level: sel.stop, color: "#ef4444", dash: [2, 3] });
  const tfBtns = ["1d", "1h", "15m", "5m", "1w"].map(t => `<button class="btn btn-ghost pat-tf ${t === tf ? "btn-primary" : ""}" data-tf="${t}">${t}</button>`).join("");
  $("#drawerBody").innerHTML = `
    <div class="opts-inline" style="margin-bottom:10px">${tfBtns}</div>
    <div class="chart-box"><canvas id="patCanvas"></canvas></div>
    <div class="plan-row">
      <div class="plan-card"><div class="k">Confidence</div><div class="v">${sel.confidence ?? "—"}</div></div>
      <div class="plan-card"><div class="k">Direction</div><div class="v ${bull ? "pos" : "neg"}">${sel.direction || "—"}</div></div>
      <div class="plan-card"><div class="k">Breakout</div><div class="v">${nf(sel.breakout)}</div></div>
      <div class="plan-card"><div class="k">Target</div><div class="v pos">${nf(sel.target)}</div></div>
      <div class="plan-card"><div class="k">Stop</div><div class="v neg">${nf(sel.stop)}</div></div>
      <div class="plan-card"><div class="k">R:R</div><div class="v">${nf(sel.rr, 1)}</div></div>
    </div>
    <div class="kv">
      <div class="item"><div class="k">Category</div><div class="v">${sel.category_label || sel.category || "—"}</div></div>
      <div class="item"><div class="k">Status</div><div class="v">${sel.status || "—"}</div></div>
      <div class="item"><div class="k">Price</div><div class="v">${nf(d.price)}</div></div>
      <div class="item"><div class="k">Pattern bars</div><div class="v">${sel.bars || "—"}</div></div>
    </div>
    ${sel.description ? `<p class="muted small" style="margin:8px 0">${esc(sel.description)}</p>` : ""}
    <div class="section-title">Patterns on ${d.tf_label || tf}</div>
    <div class="setup-list">${pats.map(p => `
      <div class="setup-item"><div class="row">
        <span><span class="tag ${p.direction === "bullish" ? "tag-buy" : p.direction === "bearish" ? "tag-sell" : "tag-neutral"}">${p.direction || "—"}</span>
        <b>${esc(p.name)}</b> <span class="pill">${p.confidence}</span></span>
        <span class="muted small">${p.category_label || p.category || ""} · ${p.status || ""}${p.rr ? " · R:R " + nf(p.rr, 1) : ""}</span>
      </div></div>`).join("") || '<p class="muted">No patterns.</p>'}</div>`;
  drawCandles($("#patCanvas"), candles, [], overlays);
  $$(".pat-tf").forEach(b => b.onclick = () => openPatternChart({ ...h, tf: b.dataset.tf, timeframe: b.dataset.tf }));
}

function openTriangleChart(h) {
  if (!h) return;
  openDrawer(h.symbol, `Triangle Breakout · ${h.pattern}`);
  const sp = h.spark;
  const candles = sp.close.map((c, i) => ({ t: i, o: c, h: c, l: c, c }));
  const n = sp.close.length;
  const up = sp.upper.map((v, i) => [i, v]), dn = sp.lower.map((v, i) => [i, v]);
  $("#drawerBody").innerHTML = `
    <div class="chart-box"><canvas id="triCanvas"></canvas></div>
    <div class="plan-row">
      <div class="plan-card"><div class="k">Close</div><div class="v">${nf(h.close)}</div></div>
      <div class="plan-card"><div class="k">Breakout level</div><div class="v">${nf(h.level)}</div></div>
      <div class="plan-card"><div class="k">Stop</div><div class="v neg">${nf(h.stop)}</div></div>
      <div class="plan-card"><div class="k">Target</div><div class="v pos">${nf(h.target)}</div></div>
      <div class="plan-card"><div class="k">R:R</div><div class="v">${nf(h.rr, 1)}</div></div>
    </div>
    <div class="kv">
      <div class="item"><div class="k">% above level</div><div class="v pos">${sign(h.pct_above)}</div></div>
      <div class="item"><div class="k">Volume ×20d</div><div class="v">${nf(h.vol_ratio)}×</div></div>
      <div class="item"><div class="k">Touches up/dn</div><div class="v">${h.touches_up}/${h.touches_dn}</div></div>
      <div class="item"><div class="k">Contraction</div><div class="v">${nf(h.tightness)}</div></div>
      <div class="item"><div class="k">Pattern bars</div><div class="v">${h.bars}</div></div>
      <div class="item"><div class="k">Score</div><div class="v">${h.score}</div></div>
    </div>`;
  drawCandles($("#triCanvas"), candles, [], [
    { points: up, color: "#38bdf8", width: 1.6 },
    { points: dn, color: "#a78bfa", width: 1.6 },
    { level: h.level, color: "#f59e0b", dash: [4, 3] },
  ]);
}

function openVolumeDetail(x) {
  if (!x) return;
  openDrawer(x.symbol, `${x.company} · volume delta (${x.interval} approximation)`);
  const wins = ["5m", "15m", "30m", "1h", "2h", "4h"];
  $("#drawerBody").innerHTML = `
    <div class="chart-box"><canvas id="volCanvas"></canvas></div>
    <div class="kv">
      <div class="item"><div class="k">LTP</div><div class="v">${nf(x.ltp)}</div></div>
      <div class="item"><div class="k">Change</div><div class="v ${cls(x.change_pct)}">${sign(x.change_pct)}%</div></div>
      <div class="item"><div class="k">Session delta</div><div class="v ${cls(x.delta)}">${(x.delta > 0 ? "+" : "") + compact(x.delta)}</div></div>
      <div class="item"><div class="k">Delta %</div><div class="v ${cls(x.delta_pct)}">${sign(x.delta_pct)}</div></div>
      <div class="item"><div class="k">Buy vol</div><div class="v pos">${compact(x.buy_volume)}</div></div>
      <div class="item"><div class="k">Sell vol</div><div class="v neg">${compact(x.sell_volume)}</div></div>
    </div>
    <div class="section-title">Delta by window</div>
    <div class="setup-list">${wins.map(w => {
      const d = x["delta_" + w];
      return `<div class="setup-item"><div class="row"><span class="pill">${w}</span>
        <span class="${cls(d)}" style="font-family:var(--mono)">${(d > 0 ? "+" : "") + compact(d)}</span>
        <span class="muted small">buy ${compact(x["buy_" + w])} · sell ${compact(x["sell_" + w])}</span></div></div>`;
    }).join("")}</div>`;
  const sp = x.spark || [];
  const candles = sp.map((v, i) => ({ t: i, o: v, h: v, l: v, c: v }));
  drawCandles($("#volCanvas"), candles, [], [{ points: sp.map((v, i) => [i, v]), color: "#38bdf8", width: 1.8 }]);
}

async function openZDDetail(sym, type) {
  openDrawer(sym.replace(".NS", ""), "ZoneDesk · " + type);
  let d;
  try { d = await api(`/api/zd/detail?symbol=${encodeURIComponent(sym)}&type=${type}`); }
  catch (e) { $("#drawerBody").innerHTML = `<p class="neg">${esc(e.message)}</p>`; return; }
  const ch = d.chart, setup = d.setup, plan = setup.plan || {};
  const zones = (ch.zones || []).map(z => ({ ...z }));
  const overlays = [];
  if (plan.entry) overlays.push({ level: plan.entry, color: "#38bdf8", dash: [5, 3] });
  if (plan.stop) overlays.push({ level: plan.stop, color: "#ef4444", dash: [3, 3] });
  if (plan.target) overlays.push({ level: plan.target, color: "#22c55e", dash: [3, 3] });
  const types = Object.keys(d.setups || {});
  $("#drawerBody").innerHTML = `
    <div class="opts-inline" style="margin-bottom:10px">${types.map(t => `<button class="btn btn-ghost zd-tf ${t === type ? "btn-primary" : ""}" data-t="${t}">${t}</button>`).join("")}</div>
    <div class="chart-box"><canvas id="zdCanvas"></canvas></div>
    <div class="plan-row">
      <div class="plan-card"><div class="k">Action</div><div class="v ${setup.action === "BUY" ? "pos" : "neg"}">${setup.action || "—"}</div></div>
      <div class="plan-card"><div class="k">Entry</div><div class="v">${nf(plan.entry)}</div></div>
      <div class="plan-card"><div class="k">Stop</div><div class="v neg">${nf(plan.stop)}</div></div>
      <div class="plan-card"><div class="k">Target</div><div class="v pos">${nf(plan.target)}</div></div>
      <div class="plan-card"><div class="k">R:R</div><div class="v">${nf(plan.rr, 1)}</div></div>
    </div>
    <div class="kv">
      <div class="item"><div class="k">Score</div><div class="v">${nf(setup.score, 1)}</div></div>
      <div class="item"><div class="k">Grade</div><div class="v">${setup.grade || "—"}</div></div>
      <div class="item"><div class="k">Status</div><div class="v" style="font-size:13px">${setup.statusLabel || "—"}</div></div>
      <div class="item"><div class="k">Execution TF</div><div class="v" style="font-size:13px">${setup.execTimeframe || ch.timeframeLabel}</div></div>
      <div class="item"><div class="k">Zone freshness</div><div class="v" style="font-size:13px">${setup.freshnessLabel || "—"}</div></div>
      <div class="item"><div class="k">Entry gap</div><div class="v">${nf(setup.entryGapPct)}%</div></div>
    </div>
    ${setup.reasons && setup.reasons.length ? `<div class="section-title">Why</div><div class="setup-list">${setup.reasons.map(r => `<div class="setup-item small">${esc(typeof r === "string" ? r : r.text || JSON.stringify(r))}</div>`).join("")}</div>` : ""}
    ${setup.warnings && setup.warnings.length ? `<div class="section-title">Warnings</div><div class="setup-list">${setup.warnings.map(w => `<div class="setup-item small" style="color:var(--amber)">${esc(w)}</div>`).join("")}</div>` : ""}`;
  drawCandles($("#zdCanvas"), toCandles(ch.bars), zones, overlays);
  $$(".zd-tf").forEach(b => b.onclick = () => openZDDetail(sym, b.dataset.t));
}

// ---------- index strip ----------
async function loadIndices() {
  try {
    const items = await api("/api/indices");
    $("#indexStrip").innerHTML = items.map(i =>
      `<div class="idx"><span class="lbl">${esc(i.label)}</span><span class="px">${nf(i.price)}</span>
       <span class="chg ${cls(i.change_pct)}">${sign(i.change_pct)}%</span></div>`).join("");
  } catch { }
}

// ---------- settings / Kotak ----------
function kotakCred() {
  return (localStorage.getItem("kotakKey") || localStorage.getItem("kotakToken") || "").trim();
}
function loadKotak() {
  $("#kotakKey").value = localStorage.getItem("kotakKey") || "";
  $("#kotakToken").value = localStorage.getItem("kotakToken") || "";
  updateBadge();
}
function updateBadge(live) {
  const connected = !!kotakCred();
  const b = $("#dataBadge");
  const lb = $("#liveBtn");
  if (lb) lb.classList.toggle("hidden", !connected);
  if (connected && live) { b.className = "badge badge-kotak"; b.textContent = "Kotak live · " + live; }
  else if (connected) { b.className = "badge badge-kotak"; b.textContent = "Yahoo + Kotak connected"; }
  else { b.className = "badge badge-yahoo"; b.textContent = "Yahoo · ~15 min delayed"; }
}
function applyLive(quotes) {
  let n = 0;
  $$("#results tbody tr[data-sym]").forEach(tr => {
    const bare = tr.dataset.sym.replace(".NS", "");
    const q = quotes[bare];
    if (!q) return;
    const pc = tr.querySelector('[data-live="price"]');
    if (pc) { pc.textContent = nf(q.price); pc.classList.add("live-cell"); }
    const cc = tr.querySelector('[data-live="chg"]');
    if (cc && q.change_pct != null) { cc.textContent = sign(q.change_pct); cc.className = cls(q.change_pct); cc.classList.add("live-cell"); }
    n++;
  });
  return n;
}
async function refreshLive() {
  const cred = kotakCred();
  if (!cred) { $("#settingsModal").classList.remove("hidden"); loadKotak(); return; }
  const symbols = [...new Set($$("#results tbody tr[data-sym]").map(tr => tr.dataset.sym.replace(".NS", "")))];
  if (!symbols.length) { toast("Run a scan first, then refresh live prices."); return; }
  const btn = $("#liveBtn"); if (btn) { btn.disabled = true; btn.textContent = "Live…"; }
  try {
    const res = await api("/api/kotak/quotes", { method: "POST", body: JSON.stringify({ key: cred, symbols }) });
    if (res.ok) {
      const n = applyLive(res.quotes || {});
      const t = new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
      updateBadge(t);
      toast(n ? `Live LTP updated for ${n} row${n === 1 ? "" : "s"}.` : "Kotak returned no matching quotes.");
    } else { toast("Kotak: " + (res.error || "quote request failed")); }
  } catch (e) { toast("Kotak request failed: " + e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = "⚡ Live"; } }
}
let _toastTimer = null;
function toast(msg) {
  let el = $("#toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; document.body.appendChild(el); }
  el.textContent = msg; el.classList.add("show");
  clearTimeout(_toastTimer); _toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

// ---------- theme ----------
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("theme", t);
  const b = $("#themeBtn"); if (b) { b.textContent = t === "light" ? "☀️" : "🌙"; b.title = t === "light" ? "Switch to dark theme" : "Switch to light theme"; }
}
function initTheme() {
  const saved = localStorage.getItem("theme");
  const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  applyTheme(saved || (prefersLight ? "light" : "dark"));
}
function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  if (!$("#drawer").classList.contains("hidden") && chartRedraw) chartRedraw();
}

// ---------- init ----------
function bindGlobal() {
  $$(".heading").forEach(h => h.onclick = () => {
    state.heading = h.dataset.heading;
    state.tool = HEADINGS[state.heading].tools[0];
    state.sort = {};
    $$(".heading").forEach(x => x.classList.toggle("active", x === h));
    renderTooltabs(); renderOpts(); loadState();
  });
  $("#universe").onchange = e => {
    state.universe = e.target.value;
    $("#customSymbols").classList.toggle("hidden", state.universe !== "custom");
  };
  $("#customSymbols").oninput = e => { state.custom = e.target.value; };
  $("#scanBtn").onclick = () => startScan(false);
  $("#refreshBtn").onclick = () => startScan(true);
  $("#drawerClose").onclick = closeDrawer;
  $("#drawer").onclick = e => { if (e.target.id === "drawer") closeDrawer(); };
  $("#settingsBtn").onclick = () => { loadKotak(); $("#settingsModal").classList.remove("hidden"); };
  $("#themeBtn").onclick = toggleTheme;
  $("#kotakClose").onclick = () => $("#settingsModal").classList.add("hidden");
  $("#settingsModal").onclick = e => { if (e.target.id === "settingsModal") $("#settingsModal").classList.add("hidden"); };
  $("#kotakSave").onclick = async () => {
    const key = $("#kotakKey").value.trim(), tok = $("#kotakToken").value.trim();
    localStorage.setItem("kotakKey", key);
    localStorage.setItem("kotakToken", tok);
    updateBadge();
    const cred = key || tok;
    const st = $("#kotakStatus");
    if (!cred) { st.textContent = "Enter a credential to connect."; st.style.color = "var(--amber)"; return; }
    st.textContent = "Testing credential…"; st.style.color = "var(--muted)";
    try {
      const res = await api("/api/kotak/test", { method: "POST", body: JSON.stringify({ key: cred }) });
      if (res.ok) {
        st.textContent = `Connected. Matched ${res.matched} NSE symbols` + (res.sample ? ` (e.g. ${res.sample.symbol} ₹${res.sample.price}).` : ".");
        st.style.color = "var(--green)";
      } else { st.textContent = res.error || "Could not connect."; st.style.color = "var(--red)"; }
    } catch (e) { st.textContent = "Test failed: " + e.message; st.style.color = "var(--red)"; }
  };
  const lb = $("#liveBtn"); if (lb) lb.onclick = refreshLive;
  $("#kotakClear").onclick = () => {
    localStorage.removeItem("kotakKey"); localStorage.removeItem("kotakToken");
    $("#kotakKey").value = ""; $("#kotakToken").value = ""; updateBadge();
    $("#kotakStatus").textContent = "Disconnected."; $("#kotakStatus").style.color = "var(--muted)";
  };
  document.addEventListener("keydown", e => { if (e.key === "Escape") { closeDrawer(); $("#settingsModal").classList.add("hidden"); } });
}

async function init() {
  initTheme();
  bindGlobal();
  loadUser();
  try { state.meta = await api("/api/universes"); } catch { }
  renderTooltabs(); renderOpts(); loadKotak();
  loadIndices(); setInterval(loadIndices, 120000);
  loadState();
}
async function loadUser() {
  try {
    const me = await api("/auth/me");
    if (me.enabled && me.email) {
      $("#userEmail").textContent = me.email;
      $("#userChip").classList.remove("hidden");
    }
  } catch { }
}
init();