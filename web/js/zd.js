/* ZoneDesk workspace — scanner + location-curve detail panel. */
(function () {
  const Hub = window.Hub;
  const { $, $$, esc, nf, sign, cls } = Hub;
  const st = Hub.state;
  const zdState = { sel: null, selType: null, roOnly: true, confOnly: false, entry: true, range: "all", q: "", sort: "score", view: "table" };

  const ALL_TYPES = ["HIT", "DIT", "WIT", "MIT", "QIT", "HYIT", "YIT"];
  const typeName = t => Hub.meta?.trades?.[t]?.name || t;

  function istClock() {
    const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, weekday: "short" }).formatToParts(new Date());
    const get = k => parts.find(p => p.type === k)?.value || "";
    const mins = (+get("hour")) * 60 + (+get("minute"));
    const day = get("weekday");
    const weekday = !["Sat", "Sun"].includes(day);
    const open = weekday && mins >= 555 && mins <= 930;
    return { text: `${get("hour")}:${get("minute")}:${get("second")} IST`, open };
  }

  function setupOf(row) { return row; } // analyze_symbol returns the best setup flattened
  function bestRows(result) {
    if (!result || !result.results) return [];
    return result.results.filter(r => r.available !== false);
  }
  function bandMeter(code) {
    const n = { very_low: 1, low: 2, equilibrium: 3, high: 4, very_high: 5 }[code] || 0;
    return `<span class="meter">${[1, 2, 3, 4, 5].map(i => `<i class="${i <= n ? "f" : ""}"></i>`).join("")}</span>`;
  }
  function zoneScoreTone(score) {
    if (score == null) return "";
    if (score >= 6) return "pos";
    if (score >= 4) return "";
    return "neg";
  }

  const zd = {
    params() {
      const types = st.zdRoute === "ALL" ? ALL_TYPES : [st.zdRoute];
      st.zdTypes = types;
      return { opts: { types, min_score: st.zdMin } };
    },
    mount(root) {
      root.innerHTML = `
      <div class="ws zd">
        <header class="ws-head">
          <div class="ws-brand">
            <div class="ws-mark zd-mark">Z</div>
            <div class="ws-title"><h1>ZoneDesk</h1><p>Demand &amp; supply scanner</p></div>
          </div>
          <div class="zd-status">
            <span class="dot" id="zdDot"></span><b id="zdMarket">NSE</b>
            <span id="zdState">—</span><span class="clock" id="zdClock">—</span>
          </div>
          <div class="ws-actions">
            <div class="zd-seg"><button class="on" type="button">NSE</button><button type="button" disabled title="US market not configured">US</button></div>
            ${Hub.universeSelect()}
            <input class="uniCustom hidden" type="text" placeholder="SYM1, SYM2…">
            <button class="btn" id="zdLive" title="Refresh LTP from Kotak Neo">⚡ Kotak live</button>
            <button class="btn" id="zdDir" title="Refresh cached data">↻</button>
            <button class="btn btn-primary" id="zdScan">▶ Scan</button>
          </div>
        </header>

        <div class="zd-route">
          <span class="lbl">ROUTE</span>
          <select id="zdRoute">${["ALL", ...ALL_TYPES].map(t =>
            `<option value="${t}" ${st.zdRoute === t ? "selected" : ""}>${t === "ALL" ? "All 7 types" : `${t} · ${typeName(t)}`}</option>`).join("")}</select>
          <span class="chip" id="zdLtf">LTF —</span><span class="arrow">→</span>
          <span class="chip" id="zdEtf">ETF —</span>
          <span class="routes">HIT → YIT → all routes</span>
        </div>

        <div class="ws-progress"><i id="zdProg"></i></div>
        <section class="kpis zd-kpis" id="zdKpis"></section>

        <div class="zd-grid">
          <section class="card zd-scan">
            <div class="card-head">
              <h2>Scanner</h2><span class="sub" id="zdScanSub">—</span>
              <div class="spacer"></div>
              <label class="search">⌕ <input id="zdSearch" placeholder="Search symbol" autocomplete="off"></label>
              <select id="zdSort">
                <option value="score">Sort: zone score</option>
                <option value="rr">Sort: R:R</option>
                <option value="gap">Sort: entry gap</option>
                <option value="chg">Sort: change %</option>
                <option value="band">Sort: location</option>
              </select>
              <div class="seg" id="zdView"><button class="on" data-v="table" type="button">Table</button><button data-v="map" type="button">Map</button></div>
            </div>
            <div class="zd-filters">
              <label class="tgl on"><input type="checkbox" id="zdRo" checked>Trade-ready only</label>
              <label class="tgl"><input type="checkbox" id="zdConf">Confidence only</label>
              <label class="tgl on"><input type="checkbox" id="zdEntry" checked>Entry range</label>
              <select id="zdRange">
                <option value="all">Range: All</option>
                <option value="in">Range: In zone</option>
                <option value="1">Range: ≤ 1%</option>
                <option value="2">Range: ≤ 2%</option>
                <option value="5">Range: ≤ 5%</option>
              </select>
              <div class="legend" style="margin-left:auto">
                <span><i style="background:var(--green)"></i>Buy</span>
                <span><i style="background:var(--red)"></i>Sell</span>
                <span><i style="background:var(--amber)"></i>Pending</span>
                <span><i style="background:var(--dim)"></i>Watch</span>
              </div>
            </div>
            <div id="zdBody"><div class="zd-empty">Press <b>Scan</b> to build the ZoneDesk feed.</div></div>
          </section>
          <aside class="zd-detail" id="zdDetail">
            <div class="card"><div class="zd-empty">Click any row for its location curve, trade ticket and confluence.</div></div>
          </aside>
        </div>
      </div>`;

      Hub.bindUniverse(root);
      $("#zdScan", root).onclick = () => Hub.scan(false);
      $("#zdDir", root).onclick = () => Hub.scan(true);
      $("#zdLive", root).onclick = () => Hub.refreshLive();
      $("#zdRoute", root).onchange = e => { st.zdRoute = e.target.value; Hub.toast("Route set — press Scan to refresh."); };
      $("#zdSort", root).onchange = e => { zdState.sort = e.target.value; renderRows(); };
      $("#zdSearch", root).oninput = e => { zdState.q = e.target.value.trim().toUpperCase(); renderRows(); };
      $("#zdRo", root).onchange = e => { zdState.roOnly = e.target.checked; e.target.closest(".tgl").classList.toggle("on", e.target.checked); renderRows(); };
      $("#zdConf", root).onchange = e => { zdState.confOnly = e.target.checked; e.target.closest(".tgl").classList.toggle("on", e.target.checked); renderRows(); };
      $("#zdEntry", root).onchange = e => { zdState.entry = e.target.checked; e.target.closest(".tgl").classList.toggle("on", e.target.checked); renderRows(); };
      $("#zdRange", root).onchange = e => { zdState.range = e.target.value; renderRows(); };
      $$("#zdView button", root).forEach(b => b.onclick = () => {
        zdState.view = b.dataset.v;
        $$("#zdView button", root).forEach(x => x.classList.toggle("on", x === b));
        renderRows();
      });
      tickClock();
      renderKpis(st.results.zd);
      if (st.results.zd) renderRows();
    },
    progress(done, total) {
      const el = $("#zdProg"); if (el) el.style.width = (total ? done / total * 100 : 0) + "%";
    },
    status(text) {
      const el = $("#zdState"); if (el) el.textContent = text;
      const dot = $("#zdDot"); if (dot) dot.classList.toggle("idle", !/Scanning/.test(text));
    },
    busy(flag) {
      const b = $("#zdScan"); if (b) { b.disabled = flag; b.textContent = flag ? "Scanning…" : "▶ Scan"; }
      if (!flag) { const p = $("#zdProg"); if (p) p.style.width = "100%"; }
    },
    render(result) {
      renderKpis(result);
      renderRows();
    },
  };

  let clockTimer = null;
  function tickClock() {
    const c = istClock();
    const clock = $("#zdClock"), stateEl = $("#zdState"), market = $("#zdMarket");
    if (!clock) return;
    clock.textContent = c.text;
    market.textContent = "NSE " + (c.open ? "OPEN" : "CLOSED");
    if (!/Scanning|Error|Updated|Ready|symbol/.test(stateEl.textContent)) stateEl.textContent = "";
    if (!clockTimer) clockTimer = setInterval(tickClock, 1000);
  }

  function renderKpis(result) {
    const el = $("#zdKpis"); if (!el) return;
    if (!result) { el.innerHTML = ""; const sub = $("#zdScanSub"); if (sub) sub.textContent = "no scan yet"; return; }
    const s = result.stats || {};
    const total = s.symbols || 0, trade = s.tradeable || 0;
    const grades = s.grades || {};
    const gradeTxt = ["A+", "A", "B", "C"].filter(g => grades[g]).map(g => `${g} ${grades[g]}`).join(" · ") || "—";
    const align = s.aligned ?? 0, pend = s.pending ?? 0, watch = s.watch ?? 0;
    const scanned = s.scanned ?? 0;
    el.innerHTML = `
      ${kpi("◎", "Trade-ready", `${trade}<small> / ${total}</small>`, `${align} trend-aligned · ${pend} pending · ${watch} watch`, "accent")}
      ${kpi("↕", "Buy / Sell", `<span class="buy">${s.buys ?? 0}</span> <small>buy</small> <span class="sell">${s.sells ?? 0}</span> <small>sell</small>`, "actionable setups only", "")}
      ${kpi("★", "Definite signals", `${s.definite ?? 0}`, "price inside HTF demand / supply", "")}
      ${kpi("▤", "Avg zone score", `${nf(s.avgScore ?? 0, 1)}<small> /7</small>`, gradeTxt, "")}
      ${kpi("≈", "High confluence", `${s.strongConfluence ?? 0}<small> / ${trade}</small>`, `${s.confluence ?? 0} ≥ filter · pattern + volume`, "")}
      ${kpi("◉", "Coverage", `${total ? Math.round(scanned / total * 100) : 0}<small>%</small>`, `${scanned} scanned · ${s.errors ?? 0} data issues`, "")}`;
    const sub = $("#zdScanSub");
    if (sub) sub.textContent = `${trade} of ${total} setups · ${trade} trade-ready · ${pend} pending`;
  }
  function kpi(icon, k, v, s, c) {
    return `<div class="kpi ${c}"><div class="k"><i>${icon}</i>${esc(k)}</div><div class="v">${v}</div><div class="s">${esc(s)}</div></div>`;
  }

  function filteredRows() {
    const r = st.results.zd;
    if (!r) return [];
    let rows = bestRows(r);
    if (zdState.roOnly) rows = rows.filter(x => x.tradeable || x.status === "trade");
    if (zdState.confOnly) rows = rows.filter(x => ((x.confluence || {}).score || 0) >= 1.2);
    if (zdState.entry) rows = rows.filter(x => x.inRange || (x.plan && x.plan.priceInside));
    if (zdState.range === "in") rows = rows.filter(x => x.plan && x.plan.priceInside);
    else if (zdState.range !== "all") {
      const lim = +zdState.range;
      rows = rows.filter(x => (x.entryGapPct ?? 99) <= lim);
    }
    if (zdState.q) rows = rows.filter(x => String(x.symbol || "").toUpperCase().includes(zdState.q) || String(x.name || "").toUpperCase().includes(zdState.q));
    const key = zdState.sort;
    const val = x => key === "rr" ? (x.plan?.rr ?? -1) : key === "gap" ? -(x.entryGapPct ?? 999)
      : key === "chg" ? (x.changePct ?? -999) : key === "band" ? ({ very_low: 1, low: 2, equilibrium: 3, high: 4, very_high: 5 }[x.location?.code] || 0)
      : (x.score ?? -1);
    return rows.sort((a, b) => val(b) - val(a));
  }

  function renderRows() {
    const body = $("#zdBody"); if (!body) return;
    const r = st.results.zd;
    if (!r) { body.innerHTML = `<div class="zd-empty">Press <b>Scan</b> to build the ZoneDesk feed.</div>`; return; }
    const rows = filteredRows();
    if (!rows.length) { body.innerHTML = `<div class="zd-empty">No setups match these filters.</div>`; renderDetail(null); return; }
    if (zdState.view === "map") {
      body.innerHTML = `<div class="zd-map">${rows.map(x => {
        const p = x.plan || {};
        const a = x.action || "";
        return `<div class="tile ${a === "BUY" ? "buy" : "sell"}" data-sym="${esc(x.yahooSymbol || x.symbol)}">
          <span class="sc">${nf(x.score, 1)}</span><b>${esc(x.symbol)}</b>
          <span>${esc(a)} · ${esc(x.location?.short || "—")}</span><span>R:R ${nf(p.rr, 1)}</span></div>`;
      }).join("")}</div>`;
      $$("#zdBody .tile").forEach(t => t.onclick = () => selectSymbol(t.dataset.sym));
      return;
    }
    body.innerHTML = `<div class="zd-rows"><table class="zd-tbl">
      <thead><tr>
        <th class="l">Symbol</th><th>LTP</th><th>Route</th><th>Action</th><th>Location</th><th>Trend</th>
        <th class="l">Levels</th><th>Status</th><th>Score</th><th>R:R</th><th>Grade</th>
      </tr></thead>
      <tbody>${rows.map(x => {
        const p = x.plan || {};
        const action = x.action || "—";
        const def = x.definite ? "DEF. " + action : action;
        const stt = x.statusLabel || "—";
        return `<tr data-sym="${esc(x.yahooSymbol || x.symbol)}" class="${zdState.sel === (x.yahooSymbol || x.symbol) ? "sel" : ""}">
          <td class="l"><span class="zd-sym">${esc(x.symbol)}</span><span class="zd-sub">${esc(x.name || "")}</span></td>
          <td><span class="zd-ltp" data-live="price">${nf(x.price)}</span><span class="zd-chg ${cls(x.changePct)}" data-live="chg">${sign(x.changePct)}%</span></td>
          <td><span class="zd-badge tf">${esc(x.tradeType || "")}</span></td>
          <td><span class="zd-badge ${action === "BUY" ? "buy" : "sell"}">${esc(def)}</span></td>
          <td><span class="zd-band ${x.location?.code === "equilibrium" ? "empty" : ""}"><span>${esc(x.location?.short || "—")}</span>${bandMeter(x.location?.code)}</span></td>
          <td class="zd-dir">${x.trend?.dir === "up" ? "↑ Up" : x.trend?.dir === "down" ? "↓ Down" : "→ Flat"}</td>
          <td class="l"><span class="zd-levels">
            <span class="tagk">E</span><span>${nf(p.entry)}</span>
            <span class="tagk">SL</span><span>${nf(p.stop)}</span>
            <span class="tagk">TP</span><span>${nf(p.target)}</span></span></td>
          <td>${x.inRange ? `<span class="zd-inrange">IN RANGE</span>` : `<span class="muted small">${nf(x.entryGapPct, 2)}%</span>`}</td>
          <td><span class="zd-score ${zoneScoreTone(x.score)}">${nf(x.score, 2)}</span></td>
          <td>${nf(p.rr, 2)}</td>
          <td><span class="zd-grade">${esc(x.grade || "—")}</span></td>
        </tr>`;
      }).join("")}</tbody></table></div>`;
    $$("#zdBody tbody tr").forEach(tr => tr.onclick = () => selectSymbol(tr.dataset.sym));
    if (!zdState.sel || !rows.find(x => (x.yahooSymbol || x.symbol) === zdState.sel)) selectSymbol(rows[0].yahooSymbol || rows[0].symbol, false);
    else renderDetail(currentRow());
  }

  function currentRow() {
    const r = st.results.zd; if (!r) return null;
    return bestRows(r).find(x => (x.yahooSymbol || x.symbol) === zdState.sel) || null;
  }
  function selectSymbol(sym, rerender = true) {
    zdState.sel = sym;
    zdState.selType = null;
    if (rerender) $$("#zdBody tbody tr").forEach(tr => tr.classList.toggle("sel", tr.dataset.sym === sym));
    renderDetail(currentRow());
  }

  function renderDetail(row) {
    const el = $("#zdDetail"); if (!el) return;
    if (!row) { el.innerHTML = `<div class="card"><div class="zd-empty">Click any row for its location curve, trade ticket and confluence.</div></div>`; return; }
    const setups = row.setups || {};
    const type = zdState.selType && setups[zdState.selType] ? zdState.selType : (row.tradeType || Object.keys(setups)[0]);
    const s = setups[type] || row;
    const plan = s.plan || {};
    const loc = s.location || {};
    const zone = s.zone || {};
    const conf = s.confluence || {};
    const tf = s.timeframes || {};
    const live = !!Hub.kotakConnected();
    const score = s.score ?? 0;
    const band = loc.code || "equilibrium";
    const bandOrder = ["very_high", "high", "equilibrium", "low", "very_low"];
    const bandName = { very_high: "Very High", high: "High", equilibrium: "Equilibrium", low: "Low", very_low: "Very Low" };
    const bandCls = { very_high: "vh", high: "h", equilibrium: "eq", low: "l", very_low: "vl" };
    const ranges = {
      very_high: [loc.supplyProx, loc.supplyTop], high: [loc.f67, loc.supplyProx],
      equilibrium: [loc.f33, loc.f67], low: [loc.demandProx, loc.f33], very_low: [loc.demandBottom, loc.demandProx],
    };
    const bands = bandOrder.map(code => {
      const r = ranges[code] || [];
      return `<div class="band ${bandCls[code]} ${code === band ? "cur" : ""}">
        <span class="nm">${bandName[code]}</span>
        <span class="rg">${r[0] != null ? nf(Math.min(r[0], r[1])) + " – " + nf(Math.max(r[0], r[1])) : "—"}</span>
        <span style="width:26px;text-align:right;font-family:var(--mono);font-size:9px">${code === band ? "◉ now" : ""}</span>
      </div>`;
    }).join("");
    const bars = [
      ["Freshness", zone.freshness, 3], ["Legout", zone.strength, 2], ["Base", zone.timeScore, 2],
    ].map(([k, v, max]) => `<div class="row"><span>${k}</span><span class="bar"><i style="width:${Math.max(0, (v || 0) / max * 100)}%"></i></span><b>${v == null ? "—" : Number(v).toFixed(1)}</b></div>`).join("");
    const chips = [
      zone.pattern, zone.baseCount != null ? `${zone.baseCount} base` : null,
      zone.legoutCount != null ? `${zone.legoutCount} legout` : null,
      s.freshnessLabel ? s.freshnessLabel.toLowerCase() : null,
      s.aligned === true ? "trend aligned" : s.aligned === false ? "counter-trend" : null,
    ].filter(Boolean);
    const patterns = (conf.patterns || []).map(p =>
      `<div class="item"><span>${esc(p.label)}</span><b>+${nf(p.points, 1)}</b>${p.description ? `<span class="why">${esc(p.description)}</span>` : ""}</div>`).join("")
      || `<div class="item"><span>No pattern or volume build-up detected.</span></div>`;
    const vol = conf.volume || {};
    const vstats = [
      ["RVOL", vol.rvol != null ? vol.rvol + "×" : "—"],
      ["Base dry-up", vol.baseDryUp != null ? vol.baseDryUp + "×" : "—"],
      ["Legout", vol.legoutExpansion != null ? vol.legoutExpansion + "×" : "—"],
      ["Last vol", vol.last != null ? Hub.compact(vol.last) : "—"],
      ["Up/down", vol.upDownRatio != null ? vol.upDownRatio : "—"],
      ["OBV slope", vol.obvSlope != null ? vol.obvSlope : "—"],
    ].map(([k, v]) => `<div><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
    const typeChips = Object.entries(setups).map(([t, su]) =>
      `<button class="btn ${t === type ? "btn-primary" : "btn-ghost"}" data-type="${t}" ${su.available === false ? "disabled" : ""} style="padding:3px 8px;font-size:10.5px">${t}</button>`).join("");

    el.innerHTML = `<div class="card">
      <div class="zd-d-head">
        <div class="zd-d-top"><h3>${esc(s.symbol || row.symbol)}</h3><span class="yahoo">Yahoo ↗</span></div>
        <div class="zd-d-name">${esc(s.name || row.name || "")}</div>
        <div class="zd-d-price"><span class="p">₹${nf(s.price ?? row.price)}</span>
          <span class="c ${cls(s.changePct)}">${sign(s.changePct)}%</span></div>
        <div class="zd-d-badges">
          <span class="zd-badge ${s.action === "BUY" ? "buy" : "sell"}">${esc((s.definite ? "DEF. " : "") + (s.action || "—"))}</span>
          <span class="zd-badge mut">${nf(score, 1)}/7 · ${esc(s.grade || "—")}</span>
          <span class="zd-badge mut">${esc(s.statusLabel || "—")}</span>
          <span class="live">${live ? "⚡ Kotak live" : "Yahoo ≈15m"}</span>
        </div>
        <div class="zd-d-badges" style="margin-top:6px">${typeChips}</div>
      </div>

      <div class="zd-block">
        <h4>Location curve</h4>
        <div class="zd-loc">${bands}</div>
      </div>

      <div class="zd-block">
        <h4>Trade ticket</h4>
        <div class="zd-ticket">
          <div class="cell"><div class="k">Entry</div><div class="v">${nf(plan.entry)}</div><div class="s">${esc(plan.entryType || "—")}</div></div>
          <div class="cell"><div class="k">Stop</div><div class="v neg">${nf(plan.stop)}</div><div class="s">${plan.riskPct ? plan.riskPct + "% risk" : "—"}</div></div>
          <div class="cell"><div class="k">Target</div><div class="v pos">${nf(plan.target)}</div><div class="s">${esc((plan.targetSource || "").slice(0, 18))}</div></div>
        </div>
        <div class="zd-rr">
          <div class="cell"><span>R:R</span><b>${nf(plan.rr, 2)}</b></div>
          <div class="cell"><span>Risk</span><b class="neg">${plan.riskPct != null ? plan.riskPct + "%" : "—"}</b></div>
          <div class="cell"><span>Reward</span><b class="pos">${plan.rewardPct != null ? plan.rewardPct + "%" : "—"}</b></div>
        </div>
      </div>

      <div class="zd-block">
        <h4>Execution score</h4>
        <div class="zd-xscore">
          <div class="zd-ring" style="--p:${Math.min(7, Math.max(0, score))}"><div class="in">${nf(score, 1)}</div></div>
          <div class="zd-bars">${bars}</div>
        </div>
        <div class="zd-chips">${chips.map(c => `<span>${esc(c)}</span>`).join("")}</div>
        <div class="zd-note">${esc(zoneNote(s, tf))}</div>
      </div>

      <div class="zd-block">
        <h4>Pattern &amp; volume</h4>
        <div class="zd-pv-head"><span class="muted small">Confluence at the execution zone</span><b>${nf(conf.score ?? 0, 1)} / ${nf(conf.max ?? 3, 0)}</b></div>
        <div class="zd-pv-list">${patterns}</div>
        <div class="zd-vstats">${vstats}</div>
      </div>

      <div class="zd-d-foot">
        <button class="btn" id="zdChart">▤ Chart</button>
        <button class="btn" id="zdCopy">⧉ Copy plan</button>
      </div>
    </div>`;

    $$("#zdDetail .zd-d-badges button").forEach(b => b.onclick = () => { zdState.selType = b.dataset.type; renderDetail(row); });
    const chartBtn = $("#zdChart"); if (chartBtn) chartBtn.onclick = () => Hub.openZDChart(row.yahooSymbol || row.symbol, type);
    const copyBtn = $("#zdCopy"); if (copyBtn) copyBtn.onclick = () => {
      const txt = `${s.symbol} ${type} ${s.action}\nEntry ${nf(plan.entry)} · SL ${nf(plan.stop)} · TP ${nf(plan.target)} · R:R ${nf(plan.rr, 2)}`;
      navigator.clipboard?.writeText(txt).then(() => Hub.toast("Trade plan copied.")).catch(() => Hub.toast(txt));
    };
    const ltf = $("#zdLtf"), etf = $("#zdEtf");
    if (ltf) ltf.textContent = "LTF " + (tf.ltfLabel || "—");
    if (etf) etf.textContent = "ETF " + (tf.etfLabel || "—");
  }

  function zoneNote(s, tf) {
    const z = s.zone;
    if (!z) return s.reasons?.[0] || "No execution zone mapped.";
    const range = `${nf(Math.min(z.bottom, z.top))}–${nf(Math.max(z.bottom, z.top))}`;
    const parts = [`${tf.etfLabel || "ETF"} ${z.direction} zone ${range}`];
    if (s.plan?.priceInside) parts.push("price is inside the zone");
    if (s.execNote) parts.push(s.execNote);
    else if (s.inRange) parts.push("entry is in range");
    return parts.join(" · ") + ".";
  }

  Hub.register("zd", zd);
})();
