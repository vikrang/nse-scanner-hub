/* Nifty 50 Volume Delta — toolbar, index strip, KPI ribbon, delta table. */
(function () {
  const Hub = window.Hub;
  const { $, $$, esc, nf, intf, compact, cls, sign } = Hub;
  const st = Hub.state;
  const WINDOWS = ["5m", "15m", "30m", "1h", "2h", "4h"];
  const WINDOW_LABEL = { session: "Session", "5m": "Last 5m", "15m": "Last 15m", "30m": "Last 30m", "1h": "Last 1h", "2h": "Last 2h", "4h": "Last 4h" };
  const vd = { sort: "delta" };

  const vdMod = {
    params() {
      return { interval: st.volInterval, include_indices: st.volIndices ? true : undefined };
    },
    mount(root) {
      root.innerHTML = `
      <div class="ws vd">
        <header class="ws-head">
          <div class="ws-brand">
            <div class="ws-mark vd-mark">Δ</div>
            <div class="ws-title"><h1>Nifty 50 Volume Delta</h1><p>buy / sell initiated flow · approximation</p></div>
          </div>
          <div class="ws-status"><span class="dot" id="vdDot"></span><span id="vdState">Booting</span></div>
          <div class="ws-actions">
            ${Hub.universeSelect()}
            <input class="uniCustom hidden" type="text" placeholder="SYM1, SYM2…">
            <button class="btn" id="vdRefresh">↻ Rescan</button>
            <button class="btn btn-primary" id="vdScan">▶ Scan</button>
          </div>
        </header>

        <div class="vd-toolbar card">
          <div class="grp"><span class="lbl">Bars</span>
            <div class="seg" id="vdInterval">
              <button data-i="1m" class="${st.volInterval === "1m" ? "on" : ""}">1m</button>
              <button data-i="5m" class="${st.volInterval === "5m" ? "on" : ""}">5m</button>
            </div>
          </div>
          <div class="grp"><span class="lbl">Time window</span>
            <select id="vdWindow">
              <option value="session" ${st.volWindow === "session" ? "selected" : ""}>Session (today)</option>
              ${WINDOWS.map(w => `<option value="${w}" ${st.volWindow === w ? "selected" : ""}>${WINDOW_LABEL[w]}</option>`).join("")}
            </select>
          </div>
          <label class="chk"><input type="checkbox" id="vdIndices" ${st.volIndices ? "checked" : ""}><span>Include tradeable indices</span></label>
          <div class="spacer" style="margin-left:auto"></div>
          <label class="search">⌕ <input id="vdQ" placeholder="Filter symbol" autocomplete="off"></label>
        </div>

        <section class="vd-indices hidden" id="vdIndicesWrap"></section>

        <section class="vd-kpis kpis" id="vdKpis"></section>
        <div class="vd-note" id="vdNote"></div>
        <div class="ws-progress"><i id="vdProg"></i></div>

        <section class="card">
          <div class="card-head"><h3>Volume delta by stock</h3>
            <span class="muted small" id="vdSub">—</span></div>
          <div class="vd-legend"><span><i class="sw buy"></i>Buy-initiated</span><span><i class="sw sell"></i>Sell-initiated</span>
            <span class="muted small">bars show net delta share</span></div>
          <div class="table-wrap"><table class="vd-tbl">
            <thead><tr>
              <th>Symbol</th><th>LTP</th><th>Chg</th><th>Volume</th><th>Buy</th><th>Sell</th>
              <th>Net delta</th><th>Δ%</th><th>Trend</th>
            </tr></thead>
            <tbody id="vdBody"></tbody>
          </table></div>
        </section>
      </div>`;

      Hub.bindUniverse(root);
      $("#vdScan", root).onclick = () => Hub.scan(false);
      $("#vdRefresh", root).onclick = () => Hub.scan(true);
      $$("#vdInterval button", root).forEach(b => b.onclick = () => {
        if (st.volInterval === b.dataset.i) return;
        st.volInterval = b.dataset.i;
        $$("#vdInterval button", root).forEach(x => x.classList.toggle("on", x === b));
        Hub.toast("Bar size set to " + b.dataset.i + " — press Scan.");
      });
      $("#vdWindow", root).onchange = e => { st.volWindow = e.target.value; renderKpis(st.results.volume); renderRows(); };
      $("#vdIndices", root).onchange = e => {
        st.volIndices = e.target.checked;
        $("#vdIndicesWrap").classList.toggle("hidden", !st.volIndices);
        if (st.volIndices) loadIndices();
      };
      $("#vdQ", root).oninput = () => renderRows();
      if (st.volIndices) loadIndices();
      renderAll(st.results.volume);
    },
    progress(done, total) { const el = $("#vdProg"); if (el) el.style.width = (total ? done / total * 100 : 0) + "%"; },
    status(text) {
      const el = $("#vdState"), dot = $("#vdDot");
      if (el) el.textContent = text;
      if (dot) dot.classList.toggle("idle", !/Scanning/.test(text));
    },
    busy(flag) {
      const b = $("#vdScan"); if (b) { b.disabled = flag; b.textContent = flag ? "Scanning…" : "▶ Scan"; }
      if (!flag) { const p = $("#vdProg"); if (p) p.style.width = "100%"; }
    },
    render(result) { renderAll(result); },
  };

  let indexCache = null;
  async function loadIndices() {
    const wrap = $("#vdIndicesWrap"); if (!wrap) return;
    wrap.classList.remove("hidden");
    if (indexCache) { renderIndices(indexCache); return; }
    wrap.innerHTML = `<span class="muted small">loading index strip…</span>`;
    try { indexCache = await Hub.api("/api/indices"); renderIndices(indexCache); }
    catch (e) { wrap.innerHTML = `<span class="neg small">${esc(e.message)}</span>`; }
  }
  function renderIndices(items) {
    const wrap = $("#vdIndicesWrap"); if (!wrap) return;
    wrap.innerHTML = items.map(x => `<div class="vd-idx">
      <div class="nm">${esc(x.label || x.symbol)}</div>
      <div class="px">${nf(x.price)}</div>
      <div class="ch ${cls(x.change_pct)}">${sign(x.change_pct)}%</div>
      <svg class="spark" viewBox="0 0 72 22" preserveAspectRatio="none">${sparkPts(x.spark)}</svg>
    </div>`).join("");
  }
  function sparkPts(vals) {
    if (!vals || vals.length < 2) return "";
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const pts = vals.map((v, i) => `${(i / (vals.length - 1) * 72).toFixed(1)},${(20 - (v - lo) / span * 18).toFixed(1)}`).join(" ");
    const up = vals[vals.length - 1] >= vals[0];
    return `<polyline points="${pts}" fill="none" stroke="${up ? "var(--green)" : "var(--red)"}" stroke-width="1.4"/>`;
  }

  function renderAll(res) {
    renderKpis(res); renderRows();
    const sub = $("#vdSub");
    if (sub) sub.textContent = res ? `${intf(res.rows.length)} stocks · ${res.interval} bars${res.errors ? " · " + res.errors + " errors" : ""}` : "—";
  }

  function winBlock(agg) {
    if (!agg) return null;
    if (st.volWindow === "session") return agg;
    return (agg.windows || {})[st.volWindow] || null;
  }

  function renderKpis(res) {
    const el = $("#vdKpis"); if (!el) return;
    const agg = res && res.aggregate;
    const w = winBlock(agg);
    const label = WINDOW_LABEL[st.volWindow];
    if (!w) {
      el.innerHTML = `<div class="empty" style="grid-column:1/-1">Press <b>Scan</b> to compute volume delta for the Nifty 50.</div>`;
      return;
    }
    const note = $("#vdNote");
    if (note) note.innerHTML = res.approximation
      ? `<b>Approximation.</b> Yahoo intraday bars classified by the tick rule (close vs prior close) — not exchange-signed tick data. ${esc(label)} window · ${esc(res.interval)} bars.`
      : "";
    const kpi = (k, v, s, c = "") => `<div class="kpi"><div class="k">${k}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`;
    const tb = w.top_buy_volume, ts = w.top_sell_volume;
    el.innerHTML = [
      kpi("Net volume delta", `${w.delta > 0 ? "+" : ""}${compact(w.delta)}`, label, cls(w.delta)),
      kpi("Buy-initiated", compact(w.buy_volume), "of classified volume", "pos"),
      kpi("Sell-initiated", compact(w.sell_volume), "of classified volume", "neg"),
      kpi("Breadth", `${w.advancing} / ${w.declining}`, `advancing / declining · ${w.unchanged} flat`),
      kpi("Most bought", tb ? esc(tb.symbol) : "—", tb ? compact(tb.volume) + " bought" : "", "pos"),
      kpi("Most sold", ts ? esc(ts.symbol) : "—", ts ? compact(ts.volume) + " sold" : "", "neg"),
    ].join("");
  }

  function deltaKey(r) { return st.volWindow === "session" ? "delta" : "delta_" + st.volWindow; }
  function buyKey(r) { return st.volWindow === "session" ? "buy_volume" : "buy_" + st.volWindow; }
  function sellKey(r) { return st.volWindow === "session" ? "sell_volume" : "sell_" + st.volWindow; }

  function renderRows() {
    const body = $("#vdBody"); if (!body) return;
    const res = st.results.volume;
    if (!res || !res.rows || !res.rows.length) { body.innerHTML = `<tr><td colspan="9" class="muted" style="padding:24px;text-align:center">No data yet.</td></tr>`; return; }
    const q = ($("#vdQ") && $("#vdQ").value || "").trim().toUpperCase();
    let rows = res.rows.filter(r => !q || r.symbol.toUpperCase().includes(q) || String(r.company || "").toUpperCase().includes(q));
    rows = [...rows].sort((a, b) => (b[deltaKey(b)] || 0) - (a[deltaKey(a)] || 0));
    const maxAbs = Math.max(1, ...rows.map(r => Math.abs(r[deltaKey(r)] || 0)));
    body.innerHTML = rows.map(r => {
      const d = r[deltaKey(r)] || 0, buy = r[buyKey(r)] || 0, sell = r[sellKey(r)] || 0;
      const w = Math.abs(d) / maxAbs * 50;
      return `<tr data-sym="${esc(r.symbol)}">
        <td class="sym"><b>${esc(r.symbol)}</b><span class="nm">${esc(r.company || "")}</span></td>
        <td data-live="price">${nf(r.ltp)}</td>
        <td class="${cls(r.change_pct)}" data-live="chg">${sign(r.change_pct)}%</td>
        <td>${compact(r.volume)}</td>
        <td class="pos">${compact(buy)}</td>
        <td class="neg">${compact(sell)}</td>
        <td class="dbar"><div class="track"><i class="${d >= 0 ? "pos" : "neg"}" style="${d >= 0 ? "left:50%" : "right:50%"};width:${w}%"></i></div>
          <b class="${cls(d)}">${d > 0 ? "+" : ""}${compact(d)}</b></td>
        <td class="${cls(r.delta_pct)}">${sign(r.delta_pct)}</td>
        <td>${Hub.sparkSVG(r.spark || [], 76, 20)}</td>
      </tr>`;
    }).join("");
    $$("#vdBody tr").forEach(tr => tr.onclick = () => {
      const row = res.rows.find(x => x.symbol === tr.dataset.sym);
      if (row) Hub.openVolumeDetail(row);
    });
  }

  Hub.register("volume", vdMod);
})();
