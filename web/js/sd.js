/* SD Radar workspace — retest cards + multi-timeframe matrix. */
(function () {
  const Hub = window.Hub;
  const { $, $$, esc, nf, sign, cls, heatColor } = Hub;
  const st = Hub.state;
  const sdState = { tf: "ALL", side: "all", status: "to_retest", q: "", sort: "priority" };
  const TF_ORDER = ["D", "W", "M", "Q"];
  const STATUS_LABEL = { IN_ZONE: "In zone", APPROACHING: "Approaching", WATCH: "Watch" };

  const sd = {
    params() { return {}; },
    mount(root) {
      root.innerHTML = `
      <div class="ws sd">
        <header class="ws-head">
          <div class="ws-brand">
            <div class="ws-mark sd-mark">SD</div>
            <div class="ws-title"><h1>SD Radar</h1><p>NSE 500 · supply &amp; demand · daily / weekly / monthly / quarterly</p></div>
          </div>
          <div class="ws-status"><span class="dot" id="sdDot"></span>
            <span><b id="sdState">connecting…</b><br><span id="sdMeta" style="font-size:10.5px;color:var(--dim)">waiting for first scan</span></span>
          </div>
          <div class="ws-actions">
            ${Hub.universeSelect()}
            <input class="uniCustom hidden" type="text" placeholder="SYM1, SYM2…">
            <button class="btn" id="sdChartQ" title="Add a ticker to a custom list">+ Ticker</button>
            <button class="btn" id="sdScan">Rescan</button>
            <button class="btn btn-primary" id="sdForce">▶ Scan</button>
          </div>
        </header>
        <div class="sd-scanbar"><i id="sdProg"></i></div>

        <section class="sd-filters">
          <div class="sd-chips" id="sdTf">
            ${["ALL", ...TF_ORDER].map(t => `<button data-v="${t}" class="${sdState.tf === t ? "on" : ""}">${t === "ALL" ? "All TF" : t === "D" ? "Daily" : t === "W" ? "Weekly" : t === "M" ? "Monthly" : "Quarterly"}</button>`).join("")}
          </div>
          <div class="sd-chips" id="sdSide">
            <button data-v="all" class="on">Both</button>
            <button data-v="demand" class="dem">Demand</button>
            <button data-v="supply" class="sup">Supply</button>
          </div>
          <div class="sd-chips" id="sdStatus">
            <button data-v="to_retest" class="on">To retest</button>
            <button data-v="IN_ZONE">In zone</button>
            <button data-v="APPROACHING">Approaching</button>
            <button data-v="WATCH">Watch</button>
          </div>
          <div class="spacer"></div>
          <label class="sd-search">⌕ <input id="sdQ" placeholder="filter symbol" autocomplete="off"></label>
          <select id="sdSort">
            <option value="priority">Sort: institutional priority</option>
            <option value="inst">Sort: INST score</option>
            <option value="heat">Sort: score</option>
            <option value="distance">Sort: closest</option>
            <option value="rr">Sort: R:R</option>
          </select>
        </section>

        <section class="kpis" id="sdKpis"></section>

        <section class="sd-block">
          <div class="sd-block-head">
            <div><h2>About to retest</h2>
              <p>Zones price is approaching, sitting inside, or close to — ranked by institutional footprint: a clean, untested
              base with the fewest candles and an outsized departure leg, blended with proximity and higher-timeframe confluence.</p></div>
            <div class="count" id="sdCount"></div>
          </div>
          <div class="sd-cards" id="sdCards"></div>
        </section>

        <section class="sd-block">
          <div class="sd-block-head">
            <div><h2>Timeframe matrix</h2>
              <p>▲ demand · ▼ supply · percentage is the distance from live price to the zone edge. Click any cell for the chart.</p></div>
          </div>
          <div class="sd-matrix-wrap" id="sdMatrix"></div>
        </section>
      </div>`;
      Hub.bindUniverse(root);
      $("#sdScan", root).onclick = () => Hub.scan(false);
      $("#sdForce", root).onclick = () => Hub.scan(true);
      $("#sdChartQ", root).onclick = () => { Hub.toast("Choose Custom list… in the universe selector, then Scan."); };
      $("#sdQ", root).oninput = e => { sdState.q = e.target.value.trim().toUpperCase(); renderCards(); };
      $("#sdSort", root).onchange = e => { sdState.sort = e.target.value; renderCards(); };
      chipGroup("#sdTf", v => { sdState.tf = v; renderCards(); renderMatrix(); });
      chipGroup("#sdSide", v => { sdState.side = v; renderCards(); renderMatrix(); });
      chipGroup("#sdStatus", v => { sdState.status = v; renderCards(); });
      renderAll(st.results.sd);
    },
    progress(done, total) {
      const el = $("#sdProg"); if (el) el.style.width = (total ? done / total * 100 : 0) + "%";
    },
    status(text) {
      const el = $("#sdState"), meta = $("#sdMeta"), dot = $("#sdDot");
      if (el) el.textContent = text;
      if (dot) dot.classList.toggle("idle", !/Scanning/.test(text));
      if (meta) {
        const r = st.results.sd;
        if (r && /symbols/.test(text)) meta.textContent = `${r.stats?.signals ?? 0} signals · ${r.stats?.symbols ?? 0} symbols scanned`;
        else if (/Scanning/.test(text)) meta.textContent = "scanning zones…";
      }
    },
    busy(flag) {
      const b = $("#sdForce"); if (b) { b.disabled = flag; b.textContent = flag ? "Scanning…" : "▶ Scan"; }
      if (!flag) { const p = $("#sdProg"); if (p) p.style.width = "100%"; }
    },
    render(result) { renderAll(result); },
  };

  function chipGroup(sel, onPick) {
    const root = $(sel); if (!root) return;
    $$("button", root).forEach(b => b.onclick = () => {
      $$("button", root).forEach(x => x.classList.toggle("on", x === b));
      onPick(b.dataset.v);
    });
  }

  function renderAll(r) {
    renderKpis(r); renderCards(); renderMatrix();
  }

  function renderKpis(r) {
    const el = $("#sdKpis"); if (!el) return;
    if (!r) { el.innerHTML = ""; return; }
    const s = r.stats || {};
    const k = (icon, name, v, sub, c = "") =>
      `<div class="kpi ${c}"><div class="k"><i>${icon}</i>${name}</div><div class="v">${v}</div><div class="s">${sub}</div></div>`;
    el.innerHTML = k("◎", "Signals", s.signals ?? 0, `${s.symbols ?? 0} symbols scanned`, "accent")
      + k("▲", "In zone", s.in_zone ?? 0, "price inside a zone", "green")
      + k("→", "Approaching", s.approaching ?? 0, "within the retest band", "")
      + k("▲", "Demand", s.demand ?? 0, "buy-side zones", "green")
      + k("▼", "Supply", s.supply ?? 0, "sell-side zones", "red")
      + k("≈", "Confluence", s.confluence ?? 0, "2+ timeframes agree", "")
      + k("✶", "Fresh zones", s.fresh ?? 0, "untested bases", "");
  }

  function filteredSignals() {
    const r = st.results.sd; if (!r) return [];
    let rows = r.signals || [];
    if (sdState.tf !== "ALL") rows = rows.filter(x => x.tf === sdState.tf);
    if (sdState.side !== "all") rows = rows.filter(x => x.type === sdState.side);
    if (sdState.status === "to_retest") rows = rows.filter(x => x.status !== "WATCH");
    else rows = rows.filter(x => x.status === sdState.status);
    if (sdState.q) rows = rows.filter(x => String(x.symbol).toUpperCase().includes(sdState.q) || String(x.name).toUpperCase().includes(sdState.q));
    const key = sdState.sort;
    const val = x => key === "inst" ? x.inst : key === "heat" ? x.heat : key === "distance" ? -(x.distance_pct ?? 99) : key === "rr" ? (x.rr ?? -1) : x.priority;
    return [...rows].sort((a, b) => val(b) - val(a));
  }

  function renderCards() {
    const el = $("#sdCards"); if (!el) return;
    const r = st.results.sd;
    if (!r) { el.innerHTML = `<div class="pr-empty">Press <b>Scan</b> to map zones across the universe.</div>`; return; }
    const rows = filteredSignals();
    const count = $("#sdCount"); if (count) count.textContent = `${rows.length} zones`;
    if (!rows.length) { el.innerHTML = `<div class="pr-empty">No zones match these filters.</div>`; return; }
    el.innerHTML = rows.slice(0, 60).map(z => {
      const side = z.type === "demand";
      const lo = Math.min(z.proximal, z.distal), hi = Math.max(z.proximal, z.distal);
      const conf = (z.conf_tfs || []).join(" · ");
      return `<div class="sd-card ${z.type}" data-sym="${esc(z.symbol)}" data-tf="${z.tf}">
        <div class="top">
          <div><div class="sym">${esc(z.symbol.replace(".NS", ""))}</div>
            <div class="nm">${esc(z.name || "")}</div></div>
          <div class="px"><div class="p" data-live="price">${nf(z.price)}</div>
            <div class="c ${cls(z.price - (z.entry || z.price))}">${esc(z.tf_label)}</div></div>
        </div>
        <div class="badges">
          <span class="${side ? "dem" : "sup"}">${side ? "▲ Demand" : "▼ Supply"}</span>
          <span>${esc(z.kind)}</span>
          <span>${esc(STATUS_LABEL[z.status] || z.status)}</span>
          <span>${esc(z.quality)}</span>
          ${z.fresh ? "<span>fresh</span>" : `<span>${z.touches}× tested</span>`}
        </div>
        <div class="zone"><span class="muted">Zone</span><span class="range">${nf(lo)}–${nf(hi)}</span>
          <span class="dist ${(z.status === "IN_ZONE") ? "pos" : ""}">${z.status === "IN_ZONE" ? "inside" : nf(z.distance_pct) + "%"}</span></div>
        <div class="heat"><i style="width:${Math.max(4, z.heat)}%"></i></div>
        <div class="stats">
          <div><div class="k">Heat</div><div class="v">${z.heat}</div></div>
          <div><div class="k">INST</div><div class="v">${z.inst}</div></div>
          <div><div class="k">R:R</div><div class="v">${nf(z.rr, 1)}</div></div>
          <div><div class="k">Conf</div><div class="v">${z.confluence}TF</div></div>
        </div>
        <div class="eta"><span>${esc(z.eta_text || "")}</span><span>${esc(conf)}</span></div>
      </div>`;
    }).join("");
    $$("#sdCards .sd-card").forEach(c => c.onclick = () => Hub.openSDChart(c.dataset.sym, c.dataset.tf));
  }

  function renderMatrix() {
    const el = $("#sdMatrix"); if (!el) return;
    const r = st.results.sd;
    if (!r) { el.innerHTML = `<div class="pr-empty" style="padding:30px">No matrix yet.</div>`; return; }
    const tfs = sdState.tf === "ALL" ? TF_ORDER : [sdState.tf];
    const byKey = {};
    (r.signals || []).forEach(z => {
      const key = z.symbol + "|" + z.tf;
      if (!byKey[key] || z.heat > byKey[key].heat) byKey[key] = z;
    });
    let rows = (r.symbols || []).filter(x => x.error == null);
    rows = rows.filter(x => tfs.some(tf => byKey[x.symbol + "|" + tf]));
    if (sdState.side !== "all") rows = rows.filter(x => tfs.some(tf => byKey[x.symbol + "|" + tf]?.type === sdState.side));
    if (sdState.q) rows = rows.filter(x => String(x.symbol).toUpperCase().includes(sdState.q) || String(x.name).toUpperCase().includes(sdState.q));
    rows.sort((a, b) => (b.confluence || 0) - (a.confluence || 0) || String(a.symbol).localeCompare(String(b.symbol)));
    if (!rows.length) { el.innerHTML = `<div class="pr-empty" style="padding:30px">No symbols with zones in this view.</div>`; return; }
    el.innerHTML = `<table class="sd-matrix">
      <thead><tr><th>Symbol</th>${tfs.map(tf => `<th>${TF_Name(tf)}</th>`).join("")}</tr></thead>
      <tbody>${rows.slice(0, 80).map(row => `<tr>
        <td><span class="sym">${esc(row.symbol.replace(".NS", ""))}</span><span class="px">${nf(row.price)}</span></td>
        ${tfs.map(tf => {
          const z = byKey[row.symbol + "|" + tf];
          if (!z) return `<td><span class="sd-cell none">—</span></td>`;
          const dem = z.type === "demand";
          const label = z.status === "IN_ZONE" ? "inside" : nf(z.distance_pct) + "%";
          return `<td><span class="sd-cell ${dem ? "dem" : "sup"}" data-sym="${esc(row.symbol)}" data-tf="${tf}">
            <b>${dem ? "▲" : "▼"} ${z.heat}</b><small>${label} · ${z.kind}</small></span></td>`;
        }).join("")}
      </tr>`).join("")}</tbody></table>`;
    $$("#sdMatrix .sd-cell[data-sym]").forEach(c => c.onclick = () => Hub.openSDChart(c.dataset.sym, c.dataset.tf));
  }
  function TF_Name(tf) { return { D: "Daily", W: "Weekly", M: "Monthly", Q: "Quarterly" }[tf] || tf; }

  Hub.register("sd", sd);
})();
