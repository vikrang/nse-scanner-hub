"""Daily triangle-breakout detection (stdlib port of the NSE Triangle scanner).

Ascending / descending / symmetrical triangles from confirmed pivot highs/lows,
flagging a latest daily close above the upper trendline on above-average volume.
"""
from __future__ import annotations

import datetime as _dt
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import marketdata

PARAMS = {
    "lookback": 90, "pivot_k": 3, "min_bars": 15, "days_since": 2,
    "vol_mult": 1.3, "flat_slope": 0.06, "trend_slope": 0.06, "min_atr_buffer": 0.10,
}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def atr_series(high, low, close, n=14):
    prev = [close[0]] + close[:-1]
    tr = [max(high[i] - low[i], abs(high[i] - prev[i]), abs(low[i] - prev[i]))
          for i in range(len(close))]
    out = [float("nan")] * len(tr)
    for i in range(len(tr)):
        if i < 2:
            continue
        lo = max(0, i - n + 1)
        out[i] = sum(tr[lo:i + 1]) / (i - lo + 1)
    return out


def find_pivots(high, low, k):
    n = len(high)
    ph, pl = [], []
    for i in range(k, n - k):
        if high[i] >= max(high[i - k:i + k + 1]):
            if ph and i - ph[-1] <= 2:
                if high[i] > high[ph[-1]]:
                    ph[-1] = i
            else:
                ph.append(i)
        if low[i] <= min(low[i - k:i + k + 1]):
            if pl and i - pl[-1] <= 2:
                if low[i] < low[pl[-1]]:
                    pl[-1] = i
            else:
                pl.append(i)
    return ph, pl


def fit_line(xs, ys):
    n = len(xs)
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, sy / n if n else 0.0
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def _line(line, x):
    return line[0] * x + line[1]


def _mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def detect(candles, symbol, p=None):
    p = p or PARAMS
    if len(candles) < p["min_bars"] + 30:
        return None
    high = [c["h"] for c in candles]
    low = [c["l"] for c in candles]
    close = [c["c"] for c in candles]
    volume = [c.get("v", 0) or 0 for c in candles]
    atr = atr_series(high, low, close)
    n = len(candles)

    for days_since in range(p["days_since"] + 1):
        b = n - 1 - days_since
        end = b - 1
        start = max(0, end - p["lookback"])
        if end - start + 1 < p["min_bars"]:
            continue
        ph, pl = find_pivots(high[start:end + 1], low[start:end + 1], p["pivot_k"])
        ph = [start + i for i in ph]
        pl = [start + i for i in pl]
        if len(ph) < 2 or len(pl) < 2:
            continue
        ph_use, pl_use = ph[-4:], pl[-4:]
        span = max(ph_use[-1], pl_use[-1]) - min(ph_use[0], pl_use[0])
        if span < max(12, p["min_bars"] // 2):
            continue
        if max(ph_use[-1], pl_use[-1]) < end - 15:
            continue

        upper = fit_line(ph_use, [high[i] for i in ph_use])
        lower = fit_line(pl_use, [low[i] for i in pl_use])
        ref = _mean(close[start:end + 1])
        if ref <= 0:
            continue
        slope_up = upper[0] / ref * 100.0
        slope_dn = lower[0] / ref * 100.0
        ts, fs = p["trend_slope"], p["flat_slope"]
        if slope_up <= -ts and slope_dn >= ts:
            pattern = "Symmetrical"
        elif abs(slope_up) <= fs and slope_dn >= ts:
            pattern = "Ascending"
        elif slope_up <= -ts and abs(slope_dn) <= fs:
            pattern = "Descending"
        else:
            continue

        atr_ref = atr[end]
        if not math.isfinite(atr_ref) or atr_ref <= 0:
            atr_ref = _mean([high[i] - low[i] for i in range(end - 9, end + 1)])
        tol = max(0.006 * ref, 0.30 * atr_ref)
        touches_up = sum(1 for i in ph_use if abs(high[i] - _line(upper, i)) <= tol)
        touches_dn = sum(1 for i in pl_use if abs(low[i] - _line(lower, i)) <= tol)
        if touches_up < 2 or touches_dn < 2:
            continue

        x0 = min(ph_use[0], pl_use[0])
        width0 = _line(upper, x0) - _line(lower, x0)
        width1 = _line(upper, b) - _line(lower, b)
        if width0 <= 0 or width1 <= 0:
            continue
        if width1 < 0.004 * ref:
            continue
        if width1 > 0.75 * width0:
            continue
        if any(_line(upper, x) <= _line(lower, x) for x in range(x0, b + 1)):
            continue

        level = _line(upper, b)
        atr_b = atr[b]
        if not math.isfinite(atr_b) or atr_b <= 0:
            atr_b = _mean([high[i] - low[i] for i in range(b - 9, b + 1)])
        pad = max(0.0015 * close[b], p["min_atr_buffer"] * atr_b)
        if close[b] <= level + pad:
            continue
        if close[b] > level * 1.07:
            continue

        vol_window = volume[max(0, b - 20):b]
        avg_vol = _mean(vol_window)
        vol_ratio = volume[b] / avg_vol if avg_vol > 0 else 0.0
        if vol_ratio < p["vol_mult"]:
            continue

        stop = max(_line(lower, b), min(low[max(0, b - 3):b + 1]))
        risk = close[b] - stop
        if risk <= 0:
            continue
        target = level + width0
        if target <= close[b]:
            continue

        tightness = _clamp(1.0 - width1 / width0, 0.0, 1.0)
        score = 100.0 * (0.45 * tightness + 0.35 * min(vol_ratio, 3.0) / 3.0
                         + 0.20 * (1.0 - days_since / (p["days_since"] + 1)))
        spark_start = max(start, b - 75)
        spark = {
            "close": [round(close[x], 2) for x in range(spark_start, b + 1)],
            "upper": [round(_line(upper, x), 2) for x in range(spark_start, b + 1)],
            "lower": [round(_line(lower, x), 2) for x in range(spark_start, b + 1)],
        }
        return {
            "symbol": symbol.replace(".NS", ""),
            "yahooSymbol": symbol,
            "pattern": pattern,
            "breakout_date": _dt.datetime.fromtimestamp(candles[b]["t"], _dt.UTC).strftime("%Y-%m-%d"),
            "days_since": days_since,
            "close": round(close[b], 2),
            "level": round(level, 2),
            "pct_above": round((close[b] / level - 1.0) * 100.0, 2),
            "vol_ratio": round(vol_ratio, 2),
            "touches_up": touches_up,
            "touches_dn": touches_dn,
            "bars": int(span),
            "tightness": round(tightness, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "risk_pct": round(risk / close[b] * 100.0, 2),
            "rr": round((target - close[b]) / risk, 2),
            "score": round(score, 1),
            "spark": spark,
        }
    return None

def build_scan(rows, on_progress=None, params=None, period="2y"):
    p = dict(PARAMS)
    if params:
        p.update({k: params[k] for k in PARAMS if k in params})
    hits, errors = [], 0
    total, done = len(rows), 0

    def work(r):
        data = marketdata.fetch(r["symbol"], "1d", period)
        return detect(data["candles"], r["symbol"], p)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(work, r): r for r in rows}
        for fut in as_completed(futures):
            try:
                h = fut.result()
                if h:
                    h["name"] = futures[fut].get("name", h["symbol"])
                    hits.append(h)
            except Exception:  # noqa: BLE001
                errors += 1
            done += 1
            if on_progress:
                on_progress(done, total)
    hits.sort(key=lambda h: (-h["score"], h["days_since"], h["symbol"]))
    return {"hits": hits, "stats": {"symbols": total, "hits": len(hits), "errors": errors,
                                    "vol_mult": p["vol_mult"], "days_since": p["days_since"]}}
