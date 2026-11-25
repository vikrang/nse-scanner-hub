/* Pattern Radar workspace — hero, stat cards, pattern tabs, signal feed. */
(function () {
  const Hub = window.Hub;
  const { $, $$, esc, nf } = Hub;
  const st = Hub.state;
  const prState = { pattern: "all", tf: "all", dir: "all", cat: "all", q: "", sort: "confidence" };

  const pr = {
    params() { return { timeframes: st.patternTfs }; },
    mount(root) {
      const tfs = ["5m", "15m", "1h", "1d", "1w"];
      root.innerHTML = `
      <div class="ws pr">
        <header class="ws-head">
          <div class="ws-brand">
            <div class="ws-mark pr-mark">◎</div>
            <div class="ws-title"><h1>Pattern Radar</h1><p>NSE 500 · India</p></div>
          </div>
          <div class="ws-status"><span class="dot" id="prDot"></span><span id="prState">Booting</span></div>
          <div class="ws-actions">
            ${Hub.universeSelect()}
            <input class="uniCustom hidden" type="text" placeholder="SYM1, SYM2…">
            <button class="btn" id="prRefresh">↻ Rescan</button>
            <button class="btn btn-primary" id="prScan">▶ Scan</button>
          </div>
        </header>

        <section class="pr-hero">
          <div class="pr-hero-copy">
            <h1>Every chart pattern.<br><span>One radar.</span></h1>
            <p>Classical formations and candlestick signals across five timeframes, ranked by confirmation and
              confidence. Click any card to open the annotated chart.</p>
            <div class="pr-hero-meta">
              <span>Universe <b>${esc(Hub.meta?.universes?.[Hub.universeKey()]?.label || Hub.universeKey())}</b></span>
              <span>Timeframes <b>${tfs.join(" · ")}</b></span>
              <span>Data <b>Yahoo ≈15m</b></span>
            </div>
          </div>
          <div class="pr-hero-actions" style="display:flex;gap:6px;flex-wrap:wrap">
            ${tfs.map(t => `<button class="pr-tab ${st.patternTfs.includes(t) ? "on" : ""}" data-scan-tf="${t}">${t}</button>`).join("")}
          </div>
        </section>

        <section class="pr-stats" id="prStats"></section>

        <section class="card pr-filters">
          <div class="f">Category <select id="prCat"><option value="all">All categories</option></select></div>
          <div class="f">Direction <select id="prDir">
            <option value="all">Both</option><option value="bullish">Bullish</option><option value="bearish">Bearish</option><option value="neutral">Neutral</option>
          </select></div>
          <div class="f">Timeframe <select id="prTf"><option value="all">All timeframes</option></select></div>
          <div class="spacer" style="margin-left:auto"></div>
          <label class="search">⌕ <input id="prQ" placeholder="Search symbol or pattern" autocomplete="off"></label>
        </section>

        <div class="pr-tabs" id="prTabs"></div>
        <div class="ws-progress" style="margin-top:-4px"><i id="prProg"></i></div>
        <section class="pr-feed" id="prFeed"></section>
      </div>`;

      Hub.bindUniverse(root);
      $("#prScan", root).onclick = () => Hub.scan(false);
      $("#prRefresh", root).onclick = () => Hub.scan(true);
      $("#prQ", root).oninput = e => { prState.q = e.target.value.trim().toUpperCase(); renderFeed(); };
      $("#prCat", root).onchange = e => { prState.cat = e.target.value; renderFeed(); };
      $("#prDir", root).onchange = e => { prState.dir = e.target.value; renderFeed(); };
      $("#prTf", root).onchange = e => { prState.tf = e.target.value; renderFeed(); };
      $$("[data-scan-tf]", root).forEach(b => b.onclick = () => {
        const tf = b.dataset.scanTf;
        if (st.patternTfs.includes(tf)) {
          if (st.patternTfs.length === 1) { Hub.toast("Keep at least one timeframe."); return; }
          st.patternTfs = st.patternTfs.filter(t => t !== tf);
        } else st.patternTfs.push(tf);
        b.classList.toggle("on", st.patternTfs.includes(tf));
        Hub.toast("Timeframes updated — press Scan.");
      });
      renderAll(st.results.patterns);
    },
    progress(done, total) {
      const el = $("#prProg"); if (el) el.style.width = (total ? done / total * 100 : 0) + "%";
    },
    status(text) {
      const el = $("#prState"), dot = $("#prDot");
      if (el) el.textContent = text;
      if (dot) dot.classList.toggle("idle", !/Scanning/.test(text));
    },
    busy(flag) {
      const b = $("#prScan"); if (b) { b.disabled = flag; b.textContent = flag ? "Scanning…" : "▶ Scan"; }
      if (!flag) { const p = $("#prProg"); if (p) p.style.width = "100%"; }
    },
    render(result) { renderAll(result); },
  };

  function hits() { return (st.results.patterns && (st.results.patterns.hits || [])) || []; }
  const pname = h => h.pattern || h.name || "—";

  function renderAll() {
    const hs = hits();
    const cats = [...new Set(hs.map(h => h.category_label || h.category).filter(Boolean))].sort();
    const tfs = [...new Set(hs.map(h => h.tf || h.timeframe).filter(Boolean))];
    const catSel = $("#prCat"), tfSel = $("#prTf");
    if (catSel) catSel.innerHTML = `<option value="all">All categories</option>` + cats.map(c => `<option ${prState.cat === c ? "selected" : ""}>${esc(c)}</option>`).join("");
    if (tfSel) tfSel.innerHTML = `<option value="all">All timeframes</option>` + tfs.map(t => `<option ${prState.tf === t ? "selected" : ""}>${esc(t)}</option>`).join("");
    renderStats(hs); renderTabs(hs); renderFeed();
  }

  function renderStats(hs) {
    const el = $("#prStats"); if (!el) return;
    const bull = hs.filter(h => h.direction === "bullish").length;
    const bear = hs.filter(h => h.direction === "bearish").length;
    const conf = hs.filter(h => h.status === "confirmed").length;
    const high = hs.filter(h => (h.confidence || 0) >= 75).length;
    const card = (stat, icon, label, v, sub, c = "") =>
      `<button class="pr-stat" data-stat="${stat}"><div class="label"><i>${icon}</i>${label}</div>
        <div class="value ${c}">${v}</div><div class="sub">${sub}</div></button>`;
    el.innerHTML = card("total", "≡", "Signals in view", hs.length, "across all timeframes", "")
      + card("bullish", "↗", "Bullish", bull, "upside setups", "pos")
      + card("bearish", "↘", "Bearish", bear, "downside setups", "neg")
      + card("confirmed", "✓", "Confirmed", conf, "breakouts triggered", "")
      + card("high", "★", "High conviction", high, "confidence ≥ 75", "");
    $$("#prStats .pr-stat").forEach(b => b.onclick = () => {
      const s = b.dataset.stat;
      if (s === "bullish" || s === "bearish") { prState.dir = s; $("#prDir").value = s; }
      if (s === "confirmed") Hub.toast("Confirmed breakouts are marked in the feed.");
      renderFeed();
    });
  }

  function renderTabs(hs) {
    const el = $("#prTabs"); if (!el) return;
    const counts = new Map();
    hs.forEach(h => counts.set(pname(h), (counts.get(pname(h)) || 0) + 1));
    const names = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b));
    if (prState.pattern !== "all" && !counts.has(prState.pattern)) prState.pattern = "all";
    el.innerHTML = [`<button class="pr-tab ${prState.pattern === "all" ? "on" : ""}" data-p="all">All patterns<b>${hs.length}</b></button>`]
      .concat(names.map(n => `<button class="pr-tab ${prState.pattern === n ? "on" : ""}" data-p="${esc(n)}">${esc(n)}<b>${counts.get(n)}</b></button>`)).join("");
    $$("#prTabs .pr-tab").forEach(b => b.onclick = () => { prState.pattern = b.dataset.p; renderTabs(hs); renderFeed(); });
  }

  function renderFeed() {
    const el = $("#prFeed"); if (!el) return;
    let hs = hits();
    if (!hs.length) { el.innerHTML = `<div class="pr-empty">Press <b>Scan</b> to look for patterns across the universe.</div>`; return; }
    if (prState.pattern !== "all") hs = hs.filter(h => pname(h) === prState.pattern);
    if (prState.tf !== "all") hs = hs.filter(h => (h.tf || h.timeframe) === prState.tf);
    if (prState.dir !== "all") hs = hs.filter(h => h.direction === prState.dir);
    if (prState.cat !== "all") hs = hs.filter(h => (h.category_label || h.category) === prState.cat);
    if (prState.q) hs = hs.filter(h => String(h.symbol).toUpperCase().includes(prState.q) || pname(h).toUpperCase().includes(prState.q) || String(h.name || "").toUpperCase().includes(prState.q));
    hs = [...hs].sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
    if (!hs.length) { el.innerHTML = `<div class="pr-empty">No signals match these filters.</div>`; return; }
    el.innerHTML = hs.slice(0, 90).map(h => {
      const bull = h.direction === "bullish", bear = h.direction === "bearish";
      const conf = h.confidence ?? 0;
      return `<div class="pr-card" data-sym="${esc(h.symbol)}" data-tf="${esc(h.tf || h.timeframe || "")}" data-pat="${esc(pname(h))}">
        <div class="top">
          <div><span class="sym">${esc(String(h.symbol).replace(".NS", ""))}</span>
            <span class="nm">${esc(h.name || "")}</span></div>
          <div class="conf"><b class="${conf >= 75 ? "pos" : ""}">${conf}</b><span>conf</span></div>
        </div>
        <div class="pat">${esc(pname(h))}</div>
        <div class="meta">
          <span>${esc(h.tf || h.timeframe || "")}</span>
          <span class="${bull ? "bull" : bear ? "bear" : ""}">${esc(h.direction || "—")}</span>
          <span>${esc(h.category_label || h.category || "")}</span>
          ${h.status ? `<span>${esc(h.status)}</span>` : ""}
        </div>
        <div class="bar"><i style="width:${Math.min(100, conf)}%"></i></div>
        <div class="foot"><span class="px" data-live="price">${nf(h.price)}</span>
          <span>${h.rr ? "R:R " + nf(h.rr, 1) : (h.bars ? h.bars + " bars" : "")}</span></div>
      </div>`;
    }).join("");
    $$("#prFeed .pr-card").forEach(c => c.onclick = () => {
      const h = hs.find(x => x.symbol === c.dataset.sym && (x.tf || x.timeframe) === c.dataset.tf && pname(x) === c.dataset.pat);
      if (h) Hub.openPatternChart(h);
    });
  }

  Hub.register("patterns", pr);
})();
