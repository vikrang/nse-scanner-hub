"""ZoneDesk seven-trade-type scan (reuses zd_engine verbatim)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from engines import zd_engine as zd

TRADE_ORDER = zd.TRADE_ORDER
TRADES = zd.TRADES


def build_scan(rows, trade_types=None, min_score=4.0, on_progress=None, live_map=None):
    types = [t for t in TRADE_ORDER if t in set(trade_types or ["DIT", "WIT", "MIT"])]
    if not types:
        types = ["DIT"]
    live_map = live_map or {}
    results, errors = [], 0
    total, done = len(rows), 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(zd.analyze_symbol, r["symbol"], r.get("name", ""), "NSE",
                               types, min_score, None, live_map.get(r["symbol"])): r for r in rows}
        for fut in as_completed(futures):
            r = futures[fut]
            try:
                res = fut.result()
                results.append(res)
                if res.get("status") == "error":
                    errors += 1
            except Exception:  # noqa: BLE001
                errors += 1
                results.append({"symbol": r["symbol"].replace(".NS", ""), "name": r.get("name", ""),
                                "available": False, "tradeable": False, "status": "error",
                                "statusLabel": "Data error", "score": None, "grade": "—", "setups": {}})
            done += 1
            if on_progress:
                on_progress(done, total)

    results.sort(key=lambda x: (x.get("score") is None, -(x.get("score") or 0)))
    flat = []
    for res in results:
        for t, s in (res.get("setups") or {}).items():
            if s.get("tradeable"):
                flat.append({
                    "symbol": s.get("symbol"), "yahooSymbol": s.get("yahooSymbol"),
                    "name": s.get("name"), "tradeType": t, "tradeShort": TRADES[t]["short"],
                    "horizon": TRADES[t]["horizon"], "action": s.get("action"),
                    "score": s.get("score"), "grade": s.get("grade"),
                    "statusLabel": s.get("statusLabel"), "plan": s.get("plan"),
                    "zone": s.get("zone"), "freshnessLabel": s.get("freshnessLabel"),
                    "entryGapPct": s.get("entryGapPct"), "confluence": s.get("confluence"),
                })
    flat.sort(key=lambda x: -(x.get("score") or 0))
    by_type = {t: sum(1 for f in flat if f["tradeType"] == t) for t in types}
    stats = {"symbols": total, "tradeable": len(flat), "errors": errors, "by_type": by_type,
             "types": types}
    return {"results": results, "tradeable": flat, "stats": stats}


def get_detail(symbol, name, trade_type="DIT", min_score=4.0, live=None):
    return zd.get_detail(symbol, name, "NSE", trade_type, min_score, 240, live)