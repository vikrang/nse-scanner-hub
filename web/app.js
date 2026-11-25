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
const pct = (v, d = 2) => v == null ? "—" : (v > 0 ? "+" : "") + nf(v, d) + "%";

function sparkSVG(vals, w = 90, h = 26, color) {
  if (!vals || vals.length < 2) return "";
  const min = Math.min(...vals), max = Math.max(...vals), rng = (max - min) || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - (v - min) / rng * (h - 3) - 1.5).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  const c = color || (up ? "var(--green)" : "var(--red)");
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.4"/></svg>`;
}
function heatColor(h) {
  if (h >= 70) return "background:rgba(34,197,94,.2);color:var(--green)";
  if (h >= 45) return "background:rgba(245,158,11,.18);color:var(--amber)";
  return "background:rgba(139,160,189,.14);color:var(--muted)";
}

// ------------------------------------------------------------------------- #
// hub state + module registry
// ------------------------------------------------------------------------- #
const HEADINGS = {
  patterns: { label: "Patterns", tools: ["patterns", "triangle"] },
  sd: { label: "Supply & Demand", tools: ["sd", "zd"] },
  volume: { label: "Volume", tools: ["volume"] },
};
const TOOL_META = {
  patterns: { name: "Pattern Radar", heading: "patterns", universe: "nse500" },
  triangle: { name: "Triangle Breakout", heading: "patterns", universe: "nse500" },
  sd: { name: "SD Radar", heading: "sd", universe: "nse500" },
  zd: { name: "ZoneDesk", heading: "sd", universe: "nifty50" },
  volume: { name: "Volume Delta", heading: "volume", universe: "nifty50" },
};
const state = {
  tool: "zd", heading: "sd",
  universe: {}, custom: "",
  meta: null, results: {}, generated: {}, polling: null, scanned: {},
  volWindow: "session", volInterval: "5m", volIndices: false,
  zdTypes: ["DIT", "WIT", "MIT"], zdMin: 4, zdRoute: "ALL",
  triVol: 1.3, triDays: 2,
  patternTfs: ["1d", "1h", "15m", "5m", "1w"], patternFilter: "all",
};
const modules = {};

const Hub = {
  $, $$, api, esc, nf, intf, compact, sign, cls, pct, heatColor, sparkSVG, toast,
  state, modules, get meta() { return state.meta; },
  register(id, mod) { modules[id] = mod; },
  get tool() { return state.tool; },
  universeKey(tool = state.tool) {
    return state.universe[tool] || TOOL_META[tool].universe || "nifty50";
  },
  universeSelect(extraClass = "") {
    const cur = Hub.universeKey();
    const unis = state.meta?.universes || {
      nifty50: { label: "Nifty 50", count: 50 },
      nifty100: { label: "Nifty 100 (liquid)", count: 100 },
      nse500: { label: "NSE 500", count: 500 },
    };
    return `<select class="uniSel ${extraClass}">${Object.entries(unis).map(([k, v]) =>
      `<option value="${k}" ${cur === k ? "selected" : ""}>${esc(v.label)}${v.count ? " · " + v.count : ""}</option>`).join("")}
      <option value="custom" ${cur === "custom" ? "selected" : ""}>Custom list…</option></select>`;
  },
  bindUniverse(root) {
    const sel = $(".uniSel", root), inp = $(".uniCustom", root);
    if (!sel) return;
    if (inp) {
      inp.value = state.custom || "";
      inp.classList.toggle("hidden", sel.value !== "custom");
      inp.oninput = e => { state.custom = e.target.value; };
    }
    sel.onchange = e => {
      state.universe[state.tool] = e.target.value;
      if (inp) inp.classList.toggle("hidden", e.target.value !== "custom");
      if (e.target.value === "custom" && inp) inp.focus();
    };
  },
  universeCount() {
    const key = Hub.universeKey();
    if (key === "custom") return state.custom.split(/[\s,;]+/).filter(Boolean).length || 0;
    return state.meta?.universes?.[key]?.count ?? 0;
  },
  setBusy(flag) {
    const m = modules[state.tool];
    if (m && m.busy) m.busy(flag);
  },
  progress(done, total) {
    const m = modules[state.tool];
    if (m && m.progress) m.progress(done, total);
  },
  status(text) {
    const m = modules[state.tool];
    if (m && m.status) m.status(text);
  },
  kotakConnected() { return !!kotakCred(); },
  async scan(force) {
    if (Hub.universeKey() === "custom" && !state.custom.trim()) { toast("Enter at least one symbol."); return; }
    try {
      await post(scanParams(force));
      state.scanned[state.tool] = false;
      poll();
    } catch (e) { toast("Scan failed: " + e.message); }
  },
};

// ------------------------------------------------------------------------- #
// navigation
// ------------------------------------------------------------------------- #
function renderHubNav() {
  $$("#headings .heading").forEach(h => h.classList.toggle("active", h.dataset.heading === state.heading));
  const tools = HEADINGS[state.heading].tools;
  $("#tooltabs").innerHTML = tools.map(t =>
    `<button class="htool ${t === state.tool ? "active" : ""}" data-tool="${t}">${TOOL_META[t].name}</button>`).join("");
  $$("#tooltabs .htool").forEach(b => b.onclick = () => switchTool(b.dataset.tool));
}
function switchTool(tool, push = true) {
  if (!modules[tool]) return;
  state.tool = tool;
  state.heading = TOOL_META[tool].heading;
  document.body.dataset.tool = tool;
  if (push) history.replaceState(null, "", "#" + tool);
  renderHubNav();
  const m = modules[tool];
  m.mount($("#app"));
  if (state.results[tool]) { m.render(state.results[tool]); m.status(scanStatusText(tool)); }
  loadState();
}

// ------------------------------------------------------------------------- #
// scan lifecycle
// ------------------------------------------------------------------------- #
function scanParams(force) {
  const tool = state.tool;
  const p = { tool, universe: Hub.universeKey(), symbols: Hub.universeKey() === "custom" ? state.custom : "", force };
  const extra = modules[tool].params ? modules[tool].params() : {};
  return Object.assign(p, extra);
}
function scanStatusText(tool) {
  return `${Hub.state.generated[tool] ? "Updated " + new Date(state.generated[tool] * 1000).toLocaleTimeString() : "Scan ready"}`;
}
function poll() {  clearInterval(state.polling);
  state.polling = setInterval(loadState, 1200);
  loadState();
}
let loadSeq = 0;
async function loadState() {
  const tool = state.tool;
  const seq = ++loadSeq;
  let s;
  try { s = await api("/api/state?tool=" + tool); } catch { return; }
  if (seq !== loadSeq || state.tool !== tool) return;
  const m = modules[tool];
  const pctDone = s.total ? Math.round(s.progress / s.total * 100) : 0;
  if (s.state === "scanning") {
    Hub.setBusy(true);
    Hub.progress(s.progress, s.total);
    Hub.status(`Scanning ${s.progress}/${s.total}…`);
  } else if (s.state === "ready") {
    Hub.setBusy(false);
    Hub.status(`${s.total} symbols · ${s.duration}s`);
    if (s.generated !== state.generated[tool] || !state.results[tool]) {
      state.results[tool] = s.result;
      state.generated[tool] = s.generated;
      m.render(s.result);
    }
    $("#genInfo").textContent = "Updated " + new Date(s.generated * 1000).toLocaleTimeString();
    clearInterval(state.polling);
  } else if (s.state === "error") {
    Hub.setBusy(false);
    Hub.status("Error: " + s.error);
    clearInterval(state.polling);
  } else {
    Hub.setBusy(false);
    Hub.status("Ready — press Scan");
    if (!state.results[tool]) { m.render(null); Hub.progress(0, 0); }
    clearInterval(state.polling);
  }
}

// ------------------------------------------------------------------------- #
// charts + drawers
// ------------------------------------------------------------------------- #
let chartRedraw = null;
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
  const light = document.documentElement.dataset.theme === "light";
  ctx.strokeStyle = light ? "#d6dfec" : "#22344f"; ctx.fillStyle = light ? "#8494ad" : "#5f7392"; ctx.font = "10px monospace"; ctx.lineWidth = 1;
  for (let g = 0; g <= 4; g++) {
    const v = min + (max - min) * g / 4, yy = y(v);
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
    ctx.fillText(v.toFixed(v >= 100 ? 0 : 2), W - padR + 4, yy + 3);
  }
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
  candles.forEach((c, i) => {
    const up = c.c >= c.o, col = up ? "#22c55e" : "#ef4444";
    const xx = x(i);
    ctx.strokeStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(xx, y(c.h)); ctx.lineTo(xx, y(c.l)); ctx.stroke();
    ctx.fillStyle = col;
    const yo = y(c.o), yc = y(c.c);
    ctx.fillRect(xx - cw / 2, Math.min(yo, yc), cw, Math.max(1, Math.abs(yc - yo)));
  });
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
function toCandles(arr) { return arr.map(c => Array.isArray(c) ? { t: c[0], o: c[1], h: c[2], l: c[3], c: c[4], v: c[5] } : c); }
function openDrawer(title, sub) {
  $("#drawerTitle").textContent = title; $("#drawerSub").textContent = sub || "";
  $("#drawerBody").innerHTML = `<div class="empty"><div class="big">⏳</div><p>Loading…</p></div>`;
  $("#drawer").classList.remove("hidden");
}
function closeDrawer() { $("#drawer").classList.add("hidden"); }
const TF_LABEL = { D: "Daily", W: "Weekly", M: "Monthly", Q: "Quarterly" };

async function openSDChart(sym, tf) {
  openDrawer(sym.replace(".NS", ""), "SD Radar · supply & demand zones");
  let d;
  try { d = await api(`/api/chart?symbol=${encodeURIComponent(sym)}&tf=${tf}`); }
  catch (e) { $("#drawerBody").innerHTML = `<p class="neg">${esc(e.message)}</p>`; return; }
  const tfBtns = ["D", "W", "M", "Q"].map(t => `<button class="btn btn-ghost sd-tf ${t === tf ? "btn-primary" : ""}" data-tf="${t}">${TF_LABEL[t]}</button>`).join("");
  const zones = d.zones.filter(z => !z.broken).slice(0, 12);
  $("#drawerBody").innerHTML = `
    <div style="display:flex;gap:6px;margin-bottom:10px">${tfBtns}</div>
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

async function openPatternChart(h, tfOverride) {
  if (!h) return;
  const sym = h.yahooSymbol || h.symbol;
  const tf = tfOverride || h.tf || h.timeframe || "1d";
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
    <div style="display:flex;gap:6px;margin-bottom:10px">${tfBtns}</div>
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
  $$(".pat-tf").forEach(b => b.onclick = () => openPatternChart(h, b.dataset.tf));
}

function openTriangleChart(h) {
  if (!h) return;
  openDrawer(h.symbol, `Triangle Breakout · ${h.pattern}`);
  const sp = h.spark;
  const candles = sp.close.map((c, i) => ({ t: i, o: c, h: c, l: c, c }));
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

async function openZDChart(sym, type) {
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
    <div style="display:flex;gap:6px;margin-bottom:10px">${types.map(t => `<button class="btn btn-ghost zd-type ${t === type ? "btn-primary" : ""}" data-t="${t}">${t}</button>`).join("")}</div>
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
    ${setup.reasons && setup.reasons.length ? `<div class="section-title">Why</div><div class="setup-list">${setup.reasons.map(r => `<div class="setup-item small">${esc(typeof r === "string" ? r : r.text || JSON.stringify(r))}</div>`).join("")}</div>` : ""}`;
  drawCandles($("#zdCanvas"), toCandles(ch.bars), zones, overlays);
  $$(".zd-type").forEach(b => b.onclick = () => openZDChart(sym, b.dataset.t));
}

Hub.openSDChart = openSDChart;
Hub.openPatternChart = openPatternChart;
Hub.openTriangleChart = openTriangleChart;
Hub.openVolumeDetail = openVolumeDetail;
Hub.openZDChart = openZDChart;
Hub.openDrawer = openDrawer;

// ------------------------------------------------------------------------- #
// Kotak + live prices
// ------------------------------------------------------------------------- #
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
  if (!b) return;
  if (connected && live) { b.className = "badge badge-kotak"; b.textContent = "Kotak live · " + live; }
  else if (connected) { b.className = "badge badge-kotak"; b.textContent = "Yahoo + Kotak connected"; }
  else { b.className = "badge badge-yahoo"; b.textContent = "Yahoo · ~15 min delayed"; }
}
function applyLive(quotes) {
  let n = 0;
  $$("#app [data-sym]").forEach(node => {
    const bare = String(node.dataset.sym || "").replace(".NS", "");
    const q = quotes[bare];
    if (!q) return;
    node.querySelectorAll('[data-live="price"]').forEach(pc => { pc.textContent = nf(q.price); pc.classList.add("live-cell"); });
    node.querySelectorAll('[data-live="chg"]').forEach(cc => {
      if (q.change_pct != null) { cc.textContent = sign(q.change_pct); cc.className = cls(q.change_pct); cc.classList.add("live-cell"); }
    });
    n++;
  });
  return n;
}
async function refreshLive() {
  const cred = kotakCred();
  if (!cred) { $("#settingsModal").classList.remove("hidden"); loadKotak(); return; }
  const symbols = [...new Set($$("#app [data-sym]").map(el => String(el.dataset.sym).replace(".NS", "")))];
  if (!symbols.length) { toast("Run a scan first, then refresh live prices."); return; }
  try {
    const res = await api("/api/kotak/quotes", { method: "POST", body: JSON.stringify({ key: cred, symbols }) });
    if (res.ok) {
      const n = applyLive(res.quotes || {});
      const t = new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
      updateBadge(t);
      toast(n ? `Live LTP updated for ${n} row${n === 1 ? "" : "s"}.` : "Kotak returned no matching quotes.");
    } else { toast("Kotak: " + (res.error || "quote request failed")); }
  } catch (e) { toast("Kotak request failed: " + e.message); }
}
Hub.refreshLive = refreshLive;

let _toastTimer = null;
function toast(msg) {
  let el = $("#toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; document.body.appendChild(el); }
  el.textContent = msg; el.classList.add("show");
  clearTimeout(_toastTimer); _toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

// ------------------------------------------------------------------------- #
// theme
// ------------------------------------------------------------------------- #
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("theme", t);
  const b = $("#themeBtn");
  if (b) { b.textContent = t === "light" ? "☀️" : "🌙"; b.title = t === "light" ? "Switch to dark theme" : "Switch to light theme"; }
  if (!$("#drawer").classList.contains("hidden") && chartRedraw) chartRedraw();
}
function initTheme() {
  const saved = localStorage.getItem("theme");
  const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  applyTheme(saved || (prefersLight ? "light" : "dark"));
}

// ------------------------------------------------------------------------- #
// init
// ------------------------------------------------------------------------- #
function bindGlobal() {
  $$("#headings .heading").forEach(h => h.onclick = () => {
    const heading = h.dataset.heading;
    const first = HEADINGS[heading].tools[0];
    const keep = HEADINGS[heading].tools.includes(state.tool) ? state.tool : first;
    switchTool(keep);
  });
  $("#drawerClose").onclick = closeDrawer;
  $("#drawer").onclick = e => { if (e.target.id === "drawer") closeDrawer(); };
  $("#settingsBtn").onclick = () => { loadKotak(); $("#settingsModal").classList.remove("hidden"); };
  $("#themeBtn").onclick = () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
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
  $("#kotakClear").onclick = () => {
    localStorage.removeItem("kotakKey"); localStorage.removeItem("kotakToken");
    $("#kotakKey").value = ""; $("#kotakToken").value = ""; updateBadge();
    $("#kotakStatus").textContent = "Disconnected."; $("#kotakStatus").style.color = "var(--muted)";
  };
  document.addEventListener("keydown", e => { if (e.key === "Escape") { closeDrawer(); $("#settingsModal").classList.add("hidden"); } });
  window.addEventListener("hashchange", () => {
    const t = location.hash.slice(1);
    if (modules[t] && t !== state.tool) switchTool(t, false);
  });
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

function init() {
  initTheme();
  bindGlobal();
  loadUser();
  const start = location.hash.slice(1);
  state.tool = modules[start] ? start : "zd";
  state.heading = TOOL_META[state.tool].heading;
  (async () => {
    try { state.meta = await api("/api/universes"); } catch { }
    switchTool(state.tool, false);
    loadKotak();
  })();
}

window.Hub = Hub;
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
