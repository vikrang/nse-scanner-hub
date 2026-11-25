"""Nifty 50 volume-delta approximation from Yahoo intraday candles.

True tick-by-tick delta needs Kotak's signed feed. With Yahoo we classify each
candle's volume by the tick rule (close vs previous close) and sum buy/sell over
trailing windows. This is an APPROXIMATION and is labelled as such in the UI.
"""
from __future__ import annotations

import datetime as _dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import marketdata

IST = ZoneInfo("Asia/Kolkata")
WINDOWS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400}


def _ist_date(ts):
    return _dt.datetime.fromtimestamp(ts, IST).date()


def analyze_symbol(symbol: str, name: str = "", industry: str = "", interval: str = "5m") -> dict:
    rng = "1d" if interval == "1m" else "5d"
    data = marketdata.fetch(symbol, interval, rng)
    cs = data["candles"]
    if len(cs) < 2:
        raise RuntimeError("no intraday bars")
    step = 60 if interval == "1m" else 300
    price = data["price"] or cs[-1]["c"]
    prev_close = data["prev_close"]
    if not prev_close:
        # previous session close = last bar before today's first bar
        today = _ist_date(cs[-1]["t"])
        older = [c for c in cs if _ist_date(c["t"]) < today]
        prev_close = older[-1]["c"] if older else cs[0]["o"]
    change_pct = (price - prev_close) / prev_close * 100.0 if prev_close else 0.0

    # classify each candle by tick rule on close
    sides, buys, sells = [], [], []
    prev_c = None
    for c in cs:
        v = c.get("v", 0) or 0
        if prev_c is None:
            side = 0
        elif c["c"] > prev_c:
            side = 1
        elif c["c"] < prev_c:
            side = -1
        else:
            side = 0
        sides.append(side)
        buys.append(v if side > 0 else 0)
        sells.append(v if side < 0 else 0)
        prev_c = c["c"]

    now_t = cs[-1]["t"]
    today = _ist_date(now_t)
    session_buy = sum(b for c, b in zip(cs, buys) if _ist_date(c["t"]) == today)
    session_sell = sum(s for c, s in zip(cs, sells) if _ist_date(c["t"]) == today)
    session_vol = sum(c.get("v", 0) or 0 for c in cs if _ist_date(c["t"]) == today)

    def window(seconds):
        cutoff = now_t - seconds
        b = sum(bu for c, bu in zip(cs, buys) if c["t"] >= cutoff)
        s = sum(se for c, se in zip(cs, sells) if c["t"] >= cutoff)
        return b - s, b, s

    win = {k: window(sec) for k, sec in WINDOWS.items()}
    delta = session_buy - session_sell
    classified = session_buy + session_sell
    spark, cum = [], 0
    for c, b, s in list(zip(cs, buys, sells))[-30:]:
        cum += b - s
        spark.append(cum)

    row = {
        "symbol": symbol.replace(".NS", ""), "yahooSymbol": symbol,
        "company": name or symbol, "industry": industry,
        "ltp": round(price, 2), "prev_close": round(prev_close, 2),
        "change_pct": round(change_pct, 2), "volume": int(session_vol),
        "buy_volume": int(session_buy), "sell_volume": int(session_sell),
        "delta": int(delta), "delta_pct": round(delta / classified * 100.0, 2) if classified else 0.0,
        "spark": spark, "interval": interval,
    }
    for k, (d, b, s) in win.items():
        row[f"delta_{k}"] = int(d)
        row[f"buy_{k}"] = int(b)
        row[f"sell_{k}"] = int(s)
    return row


def build_scan(rows, on_progress=None, interval: str = "5m"):
    out, errors = [], 0
    total, done = len(rows), 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(analyze_symbol, r["symbol"], r.get("name", ""),
                               r.get("industry", ""), interval): r for r in rows}
        for fut in as_completed(futures):
            r = futures[fut]
            try:
                out.append(fut.result())
            except Exception:  # noqa: BLE001
                errors += 1
            done += 1
            if on_progress:
                on_progress(done, total)
    out.sort(key=lambda x: -x["delta"])

    def block(key):
        if key == "session":
            buy = sum(r["buy_volume"] for r in out)
            sell = sum(r["sell_volume"] for r in out)
            deltas = [(r["symbol"], r["delta"]) for r in out]
            buys = [(r["symbol"], r["buy_volume"]) for r in out]
            sells = [(r["symbol"], r["sell_volume"]) for r in out]
        else:
            buy = sum(r[f"buy_{key}"] for r in out)
            sell = sum(r[f"sell_{key}"] for r in out)
            deltas = [(r["symbol"], r[f"delta_{key}"]) for r in out]
            buys = [(r["symbol"], r[f"buy_{key}"]) for r in out]
            sells = [(r["symbol"], r[f"sell_{key}"]) for r in out]
        adv = sum(1 for _, d in deltas if d > 0)
        dec = sum(1 for _, d in deltas if d < 0)
        top_buy = max(deltas, key=lambda x: x[1]) if deltas else None
        top_sell = min(deltas, key=lambda x: x[1]) if deltas else None
        tbv = max(buys, key=lambda x: x[1]) if buys else None
        tsv = max(sells, key=lambda x: x[1]) if sells else None
        return {
            "delta": buy - sell, "buy_volume": buy, "sell_volume": sell,
            "advancing": adv, "declining": dec, "unchanged": len(out) - adv - dec,
            "top_buy": {"symbol": top_buy[0], "delta": top_buy[1]} if top_buy else None,
            "top_sell": {"symbol": top_sell[0], "delta": top_sell[1]} if top_sell else None,
            "top_buy_volume": {"symbol": tbv[0], "volume": tbv[1]} if tbv else None,
            "top_sell_volume": {"symbol": tsv[0], "volume": tsv[1]} if tsv else None,
        }

    aggregate = block("session")
    aggregate["volume"] = sum(r["volume"] for r in out)
    aggregate["windows"] = {k: block(k) for k in WINDOWS}
    return {"rows": out, "aggregate": aggregate, "errors": errors, "interval": interval,
            "approximation": True}