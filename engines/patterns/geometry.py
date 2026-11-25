"""Shared geometry, indicators and pattern serialisation helpers (stdlib port).

The dataframe of the original engine is replaced by a list of candle
dicts [{"t","o","h","l","c","v"}] plus parallel Python lists of floats.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Configuration (copied from the source app/config.py)
# --------------------------------------------------------------------------- #

TIMEFRAMES: dict[str, dict] = {
    "5m": {
        "label": "5m", "long": "5 minute", "period": "30d", "interval": "5m",
        "ttl": 600, "scan_interval": 600, "min_bars": 180, "recent_window": 240,
        "pivot_span": 3, "chart_bars": 420, "sort": 0,
    },
    "15m": {
        "label": "15m", "long": "15 minute", "period": "60d", "interval": "15m",
        "ttl": 900, "scan_interval": 900, "min_bars": 180, "recent_window": 240,
        "pivot_span": 3, "chart_bars": 420, "sort": 1,
    },
    "1h": {
        "label": "1H", "long": "1 hour", "period": "365d", "interval": "1h",
        "ttl": 1200, "scan_interval": 1200, "min_bars": 150, "recent_window": 220,
        "pivot_span": 3, "chart_bars": 400, "sort": 2,
    },
    "1d": {
        "label": "1D", "long": "Daily", "period": "3y", "interval": "1d",
        "ttl": 1800, "scan_interval": 1800, "min_bars": 120, "recent_window": 200,
        "pivot_span": 5, "chart_bars": 320, "sort": 3,
    },
    "1w": {
        "label": "1W", "long": "Weekly", "period": "10y", "interval": "1wk",
        "ttl": 3600, "scan_interval": 3600, "min_bars": 60, "recent_window": 150,
        "pivot_span": 3, "chart_bars": 200, "sort": 4,
    },
}

CATEGORY_LABELS = {
    "reversal": "Reversal",
    "continuation": "Continuation",
    "breakout": "Breakout",
    "candlestick": "Candlestick",
}

MAX_PATTERNS_PER_SYMBOL = 8
CANDLE_PATTERN_MIN_CONFIDENCE = 52


def candle_lists(candles: list[dict]) -> dict[str, list]:
    """Turn candle dicts into parallel Python lists (floats / epoch ints)."""
    return {
        "high": [float(c["h"]) for c in candles],
        "low": [float(c["l"]) for c in candles],
        "close": [float(c["c"]) for c in candles],
        "open": [float(c["o"]) for c in candles],
        "volume": [float(c.get("v") or 0) for c in candles],
        "times": [int(c["t"]) for c in candles],
    }


def _mean(vals) -> float:
    vals = list(vals)
    return sum(vals) / len(vals) if vals else 0.0


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #

@dataclass
class Context:
    symbol: str
    timeframe: str
    candles: list[dict]
    cfg: dict
    high: list[float]
    low: list[float]
    close: list[float]
    open: list[float]
    volume: list[float]
    atr_series: list[float]
    atr: float
    last_i: int
    start_i: int
    raw_pivots: list[dict] = field(default_factory=list)
    pivots: list[dict] = field(default_factory=list)
    trend: str = "range"
    vol_ratio: float = 1.0
    ema20: float = 0.0
    ema50: float = 0.0

    @property
    def last_close(self) -> float:
        return float(self.close[self.last_i])

    def price_at(self, i: int) -> float:
        i = int(max(0, min(self.last_i, i)))
        return float(self.close[i])

    def atr_at(self, i: int) -> float:
        i = int(max(0, min(self.last_i, i)))
        value = float(self.atr_series[i])
        return value if value > 0 else self.atr


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #

def _atr(high: list[float], low: list[float], close: list[float], period: int = 14) -> list[float]:
    """Rolling mean of true range (period 14, min_periods 5), bfill/ffill."""
    n = len(close)
    tr: list[float] = []
    for i in range(n):
        if i == 0:
            tr.append(high[i] - low[i])
        else:
            pc = close[i - 1]
            tr.append(max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc)))
    out = [float("nan")] * n
    for i in range(4, n):  # min_periods=5 -> first valid mean at index 4
        lo = max(0, i - period + 1)
        out[i] = sum(tr[lo:i + 1]) / (i - lo + 1)
    tail = [high[i] - low[i] for i in range(max(0, n - 30), n)]
    fallback = _mean(tail) or 0.01
    if n >= 5:
        # bfill leading NaNs with the first valid value; ffill is a no-op here.
        for i in range(4):
            out[i] = out[4]
    else:
        out = [fallback] * n
    return [v if math.isfinite(v) else fallback for v in out]


def _ema(series: list[float], period: int) -> float:
    """ewm(span=period, adjust=False).mean() last value."""
    if not series:
        return 0.0
    alpha = 2.0 / (period + 1)
    ema = float(series[0])
    for value in series[1:]:
        ema = alpha * float(value) + (1 - alpha) * ema
    return float(ema)


def _trend_state(close: list[float]) -> str:
    n = len(close)
    if n < 30:
        return "range"
    ema20 = _ema(close, 20)
    ema50 = _ema(close, min(50, n))
    move = (float(close[-1]) / float(close[max(0, n - 16)]) - 1) * 100
    if ema20 > ema50 and move > 0.35:
        return "up"
    if ema20 < ema50 and move < -0.35:
        return "down"
    return "range"


def _volume_ratio(volume: list[float], lookback: int = 20) -> float:
    if len(volume) < 3:
        return 1.0
    avg = _mean(volume[-lookback - 1:-1]) or 0
    if avg <= 0:
        return 1.0
    return float(volume[-1]) / avg


# --------------------------------------------------------------------------- #
# Pivots
# --------------------------------------------------------------------------- #

def find_pivots(high: list[float], low: list[float], close: list[float], span: int) -> list[dict]:
    """Local swing highs/lows using a symmetrical fractal window."""
    span = max(1, int(span))
    n = len(close)
    pivots: list[dict] = []
    if n < span * 2 + 2:
        return pivots

    for i in range(span, n - span):
        window_high = high[i - span:i + span + 1]
        window_low = low[i - span:i + span + 1]
        wh_max = max(window_high)
        wl_min = min(window_low)
        is_high = high[i] >= wh_max - 1e-9
        is_low = low[i] <= wl_min + 1e-9
        if not is_high and not is_low:
            continue
        if is_high and is_low:
            # Outside bar: keep the side that actually dominated the close.
            anchor = float(close[i])
            mid = (wh_max + wl_min) / 2
            is_high = anchor >= mid
            is_low = not is_high
        if is_high:
            pivots.append({"i": i, "price": float(high[i]), "kind": "H"})
        else:
            pivots.append({"i": i, "price": float(low[i]), "kind": "L"})
    return pivots


def alternate_pivots(pivots: list[dict]) -> list[dict]:
    """Keep a strict high-low-high sequence, retaining the most extreme swing."""
    ordered = sorted(pivots, key=lambda p: (p["i"], p["kind"]))
    out: list[dict] = []
    for pivot in ordered:
        if out and pivot["i"] == out[-1]["i"]:
            continue
        if out and pivot["kind"] == out[-1]["kind"]:
            better = (
                pivot["price"] >= out[-1]["price"]
                if pivot["kind"] == "H"
                else pivot["price"] <= out[-1]["price"]
            )
            if better:
                out[-1] = pivot
        else:
            out.append(pivot)
    return out


# --------------------------------------------------------------------------- #
# Trendlines
# --------------------------------------------------------------------------- #

def fit_line(points: list[dict]) -> tuple[float, float, float] | None:
    """Least-squares line through pivots. Returns (slope, intercept, r2)."""
    if len(points) < 2:
        return None
    xs = [float(p["i"]) for p in points]
    ys = [float(p["price"]) for p in points]
    if max(xs) - min(xs) == 0:
        return None
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    if n >= 3:
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
        ym = sy / n
        ss_tot = sum((y - ym) ** 2 for y in ys)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    else:
        r2 = 1.0
    return float(slope), float(intercept), float(r2)


def line_value(slope: float, intercept: float, index: float) -> float:
    return float(slope) * float(index) + float(intercept)


def touch_count(
    pivots: list[dict],
    slope: float,
    intercept: float,
    tolerance: float,
    kind: str | None = None,
    min_index: int | None = None,
) -> int:
    count = 0
    for pivot in pivots:
        if kind and pivot["kind"] != kind:
            continue
        if min_index is not None and pivot["i"] < min_index:
            continue
        expected = line_value(slope, intercept, pivot["i"])
        if abs(pivot["price"] - expected) <= tolerance:
            count += 1
    return count


def max_residual(points: list[dict], slope: float, intercept: float) -> float:
    if not points:
        return 0.0
    return max(
        abs(p["price"] - line_value(slope, intercept, p["i"])) for p in points
    )


# --------------------------------------------------------------------------- #
# Series / overlay helpers
# --------------------------------------------------------------------------- #

def epoch_at(candles: list[dict], index: int) -> int:
    index = int(max(0, min(len(candles) - 1, index)))
    try:
        return int(candles[index]["t"])
    except Exception:  # noqa: BLE001
        return 0


def points_from_pivots(candles: list[dict], items: list[tuple]) -> list[dict]:
    points = []
    for item in items:
        i, price = item[0], item[1]
        label = item[2] if len(item) > 2 else None
        point = {"time": epoch_at(candles, int(i)), "value": round(float(price), 2)}
        if label:
            point["label"] = label
        points.append(point)
    return points


def line_from(
    candles: list[dict],
    slope: float,
    intercept: float,
    start_i: int,
    end_i: int,
    role: str,
    dash: bool = False,
) -> dict:
    start_i = int(max(0, start_i))
    end_i = int(min(len(candles) - 1, end_i))
    if end_i <= start_i:
        end_i = min(len(candles) - 1, start_i + 1)
    points = [
        {
            "time": epoch_at(candles, i),
            "value": round(line_value(slope, intercept, i), 2),
        }
        for i in range(start_i, end_i + 1, max(1, (end_i - start_i) // 24 or 1))
    ]
    if not points or points[-1]["time"] != epoch_at(candles, end_i):
        points.append(
            {"time": epoch_at(candles, end_i), "value": round(line_value(slope, intercept, end_i), 2)}
        )
    return {"role": role, "points": points, "dash": bool(dash)}


def horizontal_line(candles: list[dict], level: float, start_i: int, end_i: int, role: str, dash: bool = True) -> dict:
    start_i = int(max(0, start_i))
    end_i = int(min(len(candles) - 1, end_i))
    return {
        "role": role,
        "points": [
            {"time": epoch_at(candles, start_i), "value": round(float(level), 2)},
            {"time": epoch_at(candles, end_i), "value": round(float(level), 2)},
        ],
        "dash": dash,
    }


def curve_points(candles: list[dict], values: list[float], start_i: int) -> list[dict]:
    return [
        {"time": epoch_at(candles, start_i + offset), "value": round(float(value), 2)}
        for offset, value in enumerate(values)
    ]


# --------------------------------------------------------------------------- #
# Scoring / construction
# --------------------------------------------------------------------------- #

def clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def risk_reward(entry: float | None, target: float | None, stop: float | None) -> float | None:
    if not entry or not target or not stop:
        return None
    risk = abs(float(entry) - float(stop))
    reward = abs(float(target) - float(entry))
    if risk <= 0 or reward <= 0:
        return None
    return round(reward / risk, 2)


def build_pattern(
    ctx: Context,
    *,
    name: str,
    category: str,
    direction: str,
    confidence: float,
    status: str,
    description: str,
    points: list[dict],
    lines: list[dict] | None = None,
    breakout: float | None = None,
    target: float | None = None,
    stop: float | None = None,
    start_i: int,
    end_i: int | None = None,
    rr_entry: float | None = None,
) -> dict:
    end_i = ctx.last_i if end_i is None else int(end_i)
    entry = rr_entry if rr_entry is not None else ctx.last_close
    if stop is not None and entry:
        # Pattern stops often sit inside the last bar; enforce ~0.9 ATR of
        # breathing room so R:R stays meaningful.
        buffer = max(ctx.atr * 0.9, abs(float(entry)) * 0.002)
        if direction == "bearish":
            stop = max(float(stop), float(entry) + buffer)
        else:
            stop = min(float(stop), float(entry) - buffer)
        stop = round(float(stop), 2)
    rr = risk_reward(entry, target, stop)
    label = CATEGORY_LABELS.get(category, category.title())
    return {
        "name": name,
        "category": category,
        "category_label": label,
        "direction": direction,
        "confidence": clamp(confidence),
        "status": status,
        "description": description,
        "points": points,
        "lines": lines or [],
        "breakout": round(float(breakout), 2) if breakout else None,
        "target": round(float(target), 2) if target else None,
        "stop": round(float(stop), 2) if stop else None,
        "rr": rr,
        "bars": int(max(1, end_i - start_i + 1)),
        "start_time": epoch_at(ctx.candles, start_i),
        "end_time": epoch_at(ctx.candles, end_i),
        "start_index": int(start_i),
        "end_index": int(end_i),
    }


def volume_near(candles: list[dict], index: int, radius: int = 1) -> float:
    start = max(0, index - radius)
    end = min(len(candles), index + radius + 1)
    try:
        vals = [float(c.get("v") or 0) for c in candles[start:end]]
        return _mean(vals) or 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def build_context(candles: list[dict], timeframe: str, symbol: str) -> Context | None:
    cfg = TIMEFRAMES[timeframe]
    candles = [
        c for c in candles
        if c and all(c.get(k) is not None for k in ("o", "h", "l", "c"))
    ]
    if len(candles) < cfg["min_bars"]:
        return None
    lists = candle_lists(candles)
    high = lists["high"]
    low = lists["low"]
    close = lists["close"]
    open_ = lists["open"]
    volume = lists["volume"]
    atr_series = _atr(high, low, close)
    atr_value = float(atr_series[-1])
    if not math.isfinite(atr_value) or atr_value <= 0:
        atr_value = max(_mean([high[i] - low[i] for i in range(max(0, len(high) - 20), len(high))]), 0.01)
    last_i = len(candles) - 1
    start_i = max(0, last_i - int(cfg["recent_window"]))
    raw = find_pivots(high, low, close, int(cfg["pivot_span"]))
    in_window = [p for p in raw if p["i"] >= start_i]
    ctx = Context(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        cfg=cfg,
        high=high,
        low=low,
        close=close,
        open=open_,
        volume=volume,
        atr_series=atr_series,
        atr=atr_value,
        last_i=last_i,
        start_i=start_i,
        raw_pivots=raw,
        pivots=alternate_pivots(in_window),
        trend=_trend_state(close),
        vol_ratio=_volume_ratio(volume),
        ema20=_ema(close, 20),
        ema50=_ema(close, min(50, len(candles))),
    )
    return ctx