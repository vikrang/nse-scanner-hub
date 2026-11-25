/* Triangle Breakout — summary strip, filters, hits table. */
(function () {
  const Hub = window.Hub;
  const { $, $$, esc, nf, sign, cls } = Hub;
  const st = Hub.state;
  const triState = { q: "", sort: "score" };

  const tri = {
    params() { return { opts: { vol_mult: Number(st.triVol), days_since: Number(st.triDays) } }; },
    mount(root) {
      root.innerHTML = `
      <div class="ws tri">
        <header class="ws-head">
          <div class="ws-brand">
            <div class="ws-mark tri-mark">◭</div>
            <div class="ws-title"><h1>Triangle Breakout</h1><p>ascending · descending · symmetrical — confirmed on volume</p></div>
          </div>
          <div class="ws-status"><span class="dot" id="triDot"></span><span id="triState">Booting</span></div>
          <div class="ws-actions">
            ${Hub.universeSelect()}
            <input class="uniCustom hidden" type="text" placeholder="SYM1, SYM2…">
            <button class="btn" id="triRefresh">↻ Rescan</button>
            <button class="btn btn-primary" id="triScan">▶ Scan</button>
          </div>
        </header>

        <section class="kpis tri-summary" id="triSummary"></section>

        <section class="card tri-note">
          <div class="f">Volume ≥ <select id="triVol">
            ${[1.0, 1.3, 1.6, 2.0, 2.5].map(v => `<option value="${v}" ${Number(st.triVol) === v ? "selected" : ""}>${v.toFixed(1)}× 20-day avg</option>`).join("")}
          </select></div>
          <div class="f">Breakout within <select id="triDays">
            ${[1, 2, 3, 5, 10].map(v => `<option value="${v}" ${Number(st.triDays) === v ? "selected" : ""}>last ${v} session${v > 1 ? "s" : ""}</option>`).join("")}
          </select></div>
          <div class="spacer" style="margin-left:auto"></div>
          <label class="search">⌕ <input id="triQ" placeholder="Filter symbol or pattern" autocomplete="off"></label>
        </section>

        <div class="ws-progress"><i id="triProg"></i></div>

        <section class="card">
          <div class="card-head"><h3>Confirmed breakouts</h3>
            <span class="muted small" id="triSub">—</span></div>
          <div class="table-wrap"><table class="tri-tbl">
            <thead><tr>
              <th>Symbol</th><th>Pattern</th><th>Breakout</th><th>Close</th><th>Level</th><th>% above</th>
              <th>Vol ×</th><th>Touches</th><th>Bars</th><th>Contraction</th><th>Stop</th><th>Target</th><th>R:R</th><th>Score</th>
            </tr></thead>
            <tbody id="triBody"></tbody>
          </table></div>
        </section>
      </div>`;

      Hub.bindUniverse(root);
      $("#triScan", root).onclick = () => Hub.scan(false);
      $("#triRefresh", root).onclick = () => Hub.scan(true);
      $("#triVol", root).onchange = e => { st.triVol = Number(e.target.value); Hub.toast("Volume threshold updated — press Scan."); };
      $("#triDays", root).onchange = e => { st.triDays = Number(e.target.value); Hub.toast("Breakout window updated — press Scan."); };
      $("#triQ", root).oninput = e => { triState.q = e.target.value.trim().toUpperCase(); renderRows(); };
      renderAll(st.results.triangle);
    },
    progress(done, total) { const el = $("#triProg"); if (el) el.style.width = (total ? done / total * 100 : 0) + "%"; },
    status(text) {
      const el = $("#triState"), dot = $("#triDot");
      if (el) el.textContent = text;
      if (dot) dot.classList.toggle("idle", !/Scanning/.test(text));
    },
    busy(flag) {
      const b = $("#triScan"); if (b) { b.disabled = flag; b.textContent = flag ? "Scanning…" : "▶ Scan"; }
      if (!flag) { const p = $("#triProg"); if (p) p.style.width = "100%"; }
    },
    render(result) { renderAll(result); },
  };

  function renderAll(res) {
    const el = $("#triSummary");
    const stats = res && res.stats;
    if (el) {
      const kpi = (k, v, s) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
      el.innerHTML = res
        ? kpi("Breakouts found", res.hits.length, `in ${stats.symbols - (stats.errors || 0)} stocks scanned`)
          + kpi("Universe", stats.symbols, "symbols evaluated")
          + kpi("Volume rule", nf(stats.vol_mult, 1) + "×", "vs 20-day average")
          + kpi("Freshness", stats.days_since + "d", "breakout must be this recent")
          + kpi("Data errors", stats.errors || 0, "symbols skipped")
        : `<div class="empty" style="grid-column:1/-1">Press <b>Scan</b> to hunt triangle breakouts.</div>`;
    }
    const sub = $("#triSub");
    if (sub) sub.textContent = res ? `${res.hits.length} hits · sorted by score` : "—";
    renderRows();
  }

  function renderRows() {
    const body = $("#triBody"); if (!body) return;
    const res = st.results.triangle;
    if (!res || !res.hits.length) {
      body.innerHTML = `<tr><td colspan="14" class="muted" style="padding:24px;text-align:center">No breakouts yet.</td></tr>`;
      return;
    }
    let hits = res.hits;
    if (triState.q) hits = hits.filter(h => String(h.symbol).toUpperCase().includes(triState.q) || String(h.pattern).toUpperCase().includes(triState.q) || String(h.name || "").toUpperCase().includes(triState.q));
    body.innerHTML = hits.map(h => `<tr data-sym="${esc(h.symbol)}" data-pat="${esc(h.pattern)}">
      <td class="sym"><b>${esc(h.symbol)}</b><span class="nm">${esc(h.name || "")}</span></td>
      <td><span class="pill">${esc(h.pattern.replace("_", " "))}</span></td>
      <td>${h.days_since === 0 ? `<span class="tag tag-demand">TODAY</span>` : h.days_since + "d ago"}<div class="muted small">${esc(h.breakout_date || "")}</div></td>
      <td>${nf(h.close)}</td>
      <td>${nf(h.level)}</td>
      <td class="pos">${sign(h.pct_above)}%</td>
      <td>${nf(h.vol_ratio, 2)}×</td>
      <td>${h.touches_up}/${h.touches_dn}</td>
      <td>${h.bars}</td>
      <td>${nf(h.tightness, 2)}</td>
      <td class="neg">${nf(h.stop)}</td>
      <td class="pos">${nf(h.target)}</td>
      <td>${nf(h.rr, 2)}</td>
      <td><span class="tri-score ${h.score >= 70 ? "hot" : ""}">${nf(h.score, 1)}</span></td>
    </tr>`).join("");
    $$("#triBody tr").forEach(tr => tr.onclick = () => {
      const h = hits.find(x => x.symbol === tr.dataset.sym && x.pattern === tr.dataset.pat);
      if (h) Hub.openTriangleChart(h);
    });
  }

  Hub.register("triangle", tri);
})();
