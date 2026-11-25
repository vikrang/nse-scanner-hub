"""
ZoneDesk engine.

Implements the demand/supply rules:
  * LTF location: nearest valid demand + supply zones, 5-band Fibonacci curve
  * ITF trend: price vs the 7-candle-back line and the 50-period MA
  * ETF execution: scored zone (freshness 3/1.5/0, strength 2/2/-1, base 2/1/0)
  * Trade plan: entry at zone proximal, stop beyond distal, target at opposing zone

Data: Yahoo Finance chart API, cached locally. Standard library only.
"""

from __future__ import annotations

import json
import math
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MARKETS = {
    "NSE": {
        "label": "India · NSE",
        "tz": "Asia/Kolkata",
        "open": (9, 15),
        "close": (15, 30),
        "suffix": ".NS",
        "currency": "INR",
        "currencySymbol": "₹",
    },
    "US": {
        "label": "United States",
        "tz": "America/New_York",
        "open": (9, 30),
        "close": (16, 0),
        "suffix": "",
        "currency": "USD",
        "currencySymbol": "$",
    },
}

TRADES = {
    "HIT": {
        "name": "Hourly Income Trade",
        "short": "Hourly",
        "ltf": "75m",
        "itf": "15m",
        "etf": "5m",
        "horizon": "Intraday",
    },
    "DIT": {
        "name": "Daily Income Trade",
        "short": "Daily",
        "ltf": "1d",
        "itf": "75m",
        "etf": "15m",
        "horizon": "1–5 days",
    },
    "WIT": {
        "name": "Weekly Income Trade",
        "short": "Weekly",
        "ltf": "1wk",
        "itf": "1d",
        "etf": "75m",
        "horizon": "1–3 weeks",
    },
    "MIT": {
        "name": "Monthly Income Trade",
        "short": "Monthly",
        "ltf": "1mo",
        "itf": "1wk",
        "etf": "1d",
        "horizon": "1–3 months",
    },
    "QIT": {
        "name": "Quarterly Income Trade",
        "short": "Quarterly",
        "ltf": "3mo",
        "itf": "1mo",
        "etf": "1wk",
        "horizon": "1–2 quarters",
    },
    "HYIT": {
        "name": "Half-Yearly Income Trade",
        "short": "Half-Yearly",
        "ltf": "6mo",
        "itf": "3mo",
        "etf": "1mo",
        "horizon": "2–4 quarters",
    },
    "YIT": {
        "name": "Yearly Income Trade",
        "short": "Yearly",
        "ltf": "1y",
        "itf": "6mo",
        "etf": "3mo",
        "horizon": "1–2 years",
    },
}

TRADE_ORDER = ["HIT", "DIT", "WIT", "MIT", "QIT", "HYIT", "YIT"]

# A setup is only actionable when price is close to the planned entry; the
# tolerance widens with the holding horizon.
MAX_ENTRY_GAP_PCT = {"HIT": 0.6, "DIT": 1.2, "WIT": 2.0, "MIT": 3.0, "QIT": 5.0, "HYIT": 7.0, "YIT": 10.0}

TF_LABELS = {
    "5m": "5 min",
    "15m": "15 min",
    "75m": "75 min",
    "1d": "Daily",
    "1wk": "Weekly",
    "1mo": "Monthly",
    "3mo": "Quarterly",
    "6mo": "Half-Yearly",
    "1y": "Yearly",
}

# timeframe -> Yahoo interval required
TF_INTERVAL = {
    "5m": "5m",
    "15m": "15m",
    "75m": "15m",
    "1d": "1d",
    "1wk": "1d",
    "1mo": "1d",
    "3mo": "1d",
    "6mo": "1d",
    "1y": "1d",
}

HTF_SET = {"1d", "1wk", "1mo", "3mo", "6mo", "1y"}

LOCATION_BANDS = {
    "very_high": {
        "label": "Very High on curve",
        "short": "Very High",
        "zone": "Supply zone",
        "rank": 5,
    },
    "high": {
        "label": "High on curve",
        "short": "High",
        "zone": "67–100% fib",
        "rank": 4,
    },
    "equilibrium": {
        "label": "Equilibrium",
        "short": "Equilibrium",
        "zone": "33–67% fib",
        "rank": 3,
    },
    "low": {
        "label": "Low on curve",
        "short": "Low",
        "zone": "0–33% fib",
        "rank": 2,
    },
    "very_low": {
        "label": "Very Low on curve",
        "short": "Very Low",
        "zone": "Demand zone",
        "rank": 1,
    },
}


class DataError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Bars / math helpers
# --------------------------------------------------------------------------- #


@dataclass
class Bar:
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


def _r(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, digits)


def _pct(value: Optional[float], digits: int = 2) -> Optional[float]:
    return _r(value, digits)


def _iso(ts: int, tz_name: str, intraday: bool = False) -> str:
    dt = datetime.fromtimestamp(int(ts), ZoneInfo(tz_name))
    return dt.strftime("%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d")


def _display_symbol(symbol: str) -> str:
    return symbol.replace(".NS", "").replace(".BO", "")


INDEX_ALIASES = {
    "NSE": {
        "NIFTY": "^NSEI",
        "NIFTY50": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "NIFTYBANK": "^NSEBANK",
        "SENSEX": "^BSESN",
        "BSESENSEX": "^BSESN",
        "NIFTYNEXT50": "^NSMIDCP",
        "NEXT50": "^NSMIDCP",
        "NIFTYMIDCAP50": "^NSEMDCP50",
        "MIDCAP50": "^NSEMDCP50",
        "NIFTY100": "^CNX100",
        "NIFTY500": "^CRSLDX",
        "INDIAVIX": "^INDIAVIX",
        "NIFTYVIX": "^INDIAVIX",
        "NIFTYIT": "^CNXIT",
        "NIFTYAUTO": "^CNXAUTO",
        "NIFTYPHARMA": "^CNXPHARMA",
        "NIFTYFMCG": "^CNXFMCG",
        "NIFTYMETAL": "^CNXMETAL",
        "NIFTYENERGY": "^CNXENERGY",
        "NIFTYREALTY": "^CNXREALTY",
        "NIFTYINFRA": "^CNXINFRA",
        "NIFTYPSUBANK": "^CNXPSUBANK",
    },
    "US": {
        "SPX": "^GSPC",
        "SP500": "^GSPC",
        "GSPC": "^GSPC",
        "NDX": "^NDX",
        "NASDAQ100": "^NDX",
        "COMP": "^IXIC",
        "NASDAQ": "^IXIC",
        "IXIC": "^IXIC",
        "DJI": "^DJI",
        "DOW": "^DJI",
        "RUT": "^RUT",
        "RUSSELL2000": "^RUT",
    },
}

INDEX_NAMES = {
    "^NSEI": "Nifty 50",
    "^NSEBANK": "Bank Nifty",
    "^BSESN": "BSE Sensex",
    "^NSMIDCP": "Nifty Next 50",
    "^NSEMDCP50": "Nifty Midcap 50",
    "^CNX100": "Nifty 100",
    "^CRSLDX": "Nifty 500",
    "^CNXIT": "Nifty IT",
    "^CNXAUTO": "Nifty Auto",
    "^CNXPHARMA": "Nifty Pharma",
    "^CNXFMCG": "Nifty FMCG",
    "^CNXMETAL": "Nifty Metal",
    "^CNXENERGY": "Nifty Energy",
    "^CNXREALTY": "Nifty Realty",
    "^CNXINFRA": "Nifty Infrastructure",
    "^CNXPSUBANK": "Nifty PSU Bank",
    "^INDIAVIX": "India VIX",
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq Composite",
    "^DJI": "Dow Jones Industrial Average",
    "^NDX": "Nasdaq 100",
    "^RUT": "Russell 2000",
    "^VIX": "CBOE Volatility Index",
}


def normalize_symbol(raw: str, market: str, index: bool = False) -> Optional[str]:
    if not raw:
        return None
    s = raw.strip().upper()
    for prefix in ("NSE:", "BSE:", "NASDAQ:", "NYSE:", "AMEX:"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    s = s.replace(" ", "")
    if not s:
        return None
    if index and market == "NSE":
        for suffix in (".NS", ".BO"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
    if index:
        s = INDEX_ALIASES.get(market, {}).get(s, s)
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-^=&")
    if not set(s).issubset(allowed):
        return None
    if len(s) > 24:
        return None
    if market == "NSE" and not index:
        if not (s.endswith(".NS") or s.endswith(".BO")):
            s += ".NS"
    return s


def _move_dir(cur: Bar, prev_close: float) -> int:
    if cur.c > prev_close:
        return 1
    if cur.c < prev_close:
        return -1
    return 0


def _body_ratio(bar: Bar) -> float:
    rng = bar.h - bar.l
    if rng <= 0:
        return 1.0
    return abs(bar.c - bar.o) / rng


def _is_base(bar: Bar) -> bool:
    return (bar.h - bar.l) > 0 and _body_ratio(bar) <= 0.5


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    n = len(values)
    mid = n // 2
    if n % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def atr_series(bars: List[Bar], period: int = 14) -> List[float]:
    if not bars:
        return []
    trs: List[float] = []
    out: List[float] = []
    running: List[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            tr = bar.h - bar.l
        else:
            prev = bars[i - 1].c
            tr = max(bar.h - bar.l, abs(bar.h - prev), abs(bar.l - prev))
        trs.append(tr)
        running.append(tr)
        if len(running) > period:
            running.pop(0)
        out.append(sum(running) / len(running) if running else 0.0)
    fallback = _median([x for x in trs if x > 0]) or 0.0001
    return [x if x > 0 else fallback for x in out]


def _atr_at(atr: List[float], idx: int) -> float:
    if not atr:
        return 0.0001
    idx = max(0, min(idx, len(atr) - 1))
    return atr[idx] if atr[idx] > 0 else 0.0001


# --------------------------------------------------------------------------- #
# Data source
# --------------------------------------------------------------------------- #


class MarketData:
    """Yahoo Finance fetcher with memory + disk cache and stale fallback."""

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: Dict[str, dict] = {}
        self._lock = threading.RLock()
        self._key_locks: Dict[str, threading.Lock] = {}
        self._sem = threading.Semaphore(5)

    def _key(self, symbol: str, interval: str, rng: str) -> str:
        safe = "".join(ch if (ch.isalnum() or ch in ".-_") else "_" for ch in symbol)
        return f"{safe}__{interval}__{rng}"

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _ttl(self, interval: str) -> int:
        return 300 if interval in ("5m", "15m") else 1800

    def _read_disk(self, key: str) -> Optional[dict]:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _write_disk(self, key: str, payload: dict) -> None:
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        try:
            with self._lock:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, separators=(",", ":"))
                os.replace(tmp, path)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def fetch(self, symbol: str, market: str, interval: str, rng: str) -> dict:
        key = self._key(symbol, interval, rng)
        ttl = self._ttl(interval)
        now = time.time()

        with self._lock:
            cached = self._mem.get(key)
        if cached and now - cached.get("_cachedAt", 0) <= ttl:
            return cached

        disk = self._read_disk(key)
        if disk and now - disk.get("fetchedAt", 0) <= ttl:
            disk["_cachedAt"] = disk.get("fetchedAt", now)
            disk["stale"] = False
            with self._lock:
                self._mem[key] = disk
            return disk

        lock = self._key_lock(key)
        with lock:
            with self._lock:
                cached = self._mem.get(key)
            if cached and time.time() - cached.get("_cachedAt", 0) <= ttl:
                return cached

            try:
                payload = self._download(symbol, market, interval, rng)
                payload["fetchedAt"] = time.time()
                payload["_cachedAt"] = payload["fetchedAt"]
                payload["stale"] = False
                with self._lock:
                    self._mem[key] = payload
                self._write_disk(key, payload)
                return payload
            except Exception as exc:
                stale = disk or cached
                if stale and stale.get("bars"):
                    stale = dict(stale)
                    stale["stale"] = True
                    stale["error"] = str(exc)
                    with self._lock:
                        self._mem[key] = stale
                    return stale
                raise DataError(f"{symbol}: {exc}") from exc

    def _download(self, symbol: str, market: str, interval: str, rng: str) -> dict:
        encoded = urllib.parse.quote(symbol, safe="")
        errors: List[str] = []
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            url = (
                f"https://{host}/v8/finance/chart/{encoded}"
                f"?range={rng}&interval={interval}&includePrePost=false&events=div%2Csplit"
            )
            for attempt in range(3):
                try:
                    with self._sem:
                        req = urllib.request.Request(
                            url,
                            headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ZoneDesk/1.0",
                                "Accept": "application/json,text/plain,*/*",
                            },
                        )
                        with urllib.request.urlopen(req, timeout=20) as resp:
                            raw = resp.read()
                    data = json.loads(raw)
                    return self._parse(symbol, market, interval, rng, data)
                except urllib.error.HTTPError as exc:
                    errors.append(f"{host} HTTP {exc.code}")
                    if exc.code in (401, 403, 404):
                        break
                    time.sleep(1.0 * (attempt + 1) + random.random() * 0.4)
                except Exception as exc:
                    errors.append(f"{host} {type(exc).__name__}: {exc}")
                    time.sleep(0.8 * (attempt + 1) + random.random() * 0.3)
        raise DataError("; ".join(errors[:4]))

    def _parse(self, symbol: str, market: str, interval: str, rng: str, data: dict) -> dict:
        chart = data.get("chart") or {}
        if chart.get("error"):
            raise DataError(str(chart["error"].get("description") or chart["error"]))
        results = chart.get("result") or []
        if not results:
            raise DataError("empty chart result")
        result = results[0]
        meta_raw = result.get("meta") or {}
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        bars: List[Bar] = []
        n = min(len(timestamps), len(opens), len(highs), len(lows), len(closes))
        for i in range(n):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            if None in (o, h, l, c):
                continue
            try:
                o, h, l, c = float(o), float(h), float(l), float(c)
            except (TypeError, ValueError):
                continue
            if min(o, h, l, c) <= 0 or h < l:
                continue
            vol = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            bars.append(Bar(int(timestamps[i]), o, h, l, c, float(vol)))

        if len(bars) < 3:
            raise DataError("not enough bars")

        bars.sort(key=lambda b: b.t)
        deduped: List[Bar] = []
        for bar in bars:
            if deduped and deduped[-1].t == bar.t:
                deduped[-1] = bar
            else:
                deduped.append(bar)

        price_for_meta = meta_raw.get("regularMarketPrice")
        prev_close = meta_raw.get("previousClose")
        try:
            price_f = float(price_for_meta) if price_for_meta else 0.0
            prev_f = float(prev_close) if prev_close else 0.0
            if not prev_f or (price_f and not (0.5 <= prev_f / price_f <= 1.5)):
                prev_close = None
        except (TypeError, ValueError):
            prev_close = None

        meta = {
            "symbol": symbol,
            "market": market,
            "currency": meta_raw.get("currency") or MARKETS[market]["currency"],
            "exchange": meta_raw.get("fullExchangeName") or meta_raw.get("exchangeName") or "",
            "regularMarketPrice": meta_raw.get("regularMarketPrice"),
            "previousClose": prev_close,
            "regularMarketTime": meta_raw.get("regularMarketTime"),
            "longName": meta_raw.get("longName") or meta_raw.get("shortName") or "",
            "timezone": meta_raw.get("exchangeTimezoneName") or MARKETS[market]["tz"],
            "interval": interval,
            "range": rng,
        }
        return {
            "symbol": symbol,
            "market": market,
            "interval": interval,
            "range": rng,
            "bars": [[b.t, b.o, b.h, b.l, b.c, b.v] for b in deduped],
            "meta": meta,
        }


DATA = MarketData()


def _bars_from_payload(payload: dict) -> List[Bar]:
    return [Bar(*row) for row in payload.get("bars", [])]


# --------------------------------------------------------------------------- #
# Resampling and completion
# --------------------------------------------------------------------------- #


def _bucket(dt: datetime, tf: str) -> Tuple:
    if tf == "1wk":
        iso = dt.isocalendar()
        return (iso[0], iso[1])
    if tf == "1mo":
        return (dt.year, dt.month)
    if tf == "3mo":
        return (dt.year, (dt.month - 1) // 3 + 1)
    if tf == "6mo":
        return (dt.year, 1 if dt.month <= 6 else 2)
    if tf == "1y":
        return (dt.year,)
    return (dt.year, dt.month, dt.day)


def resample_bars(bars: List[Bar], tf: str, market: str) -> List[Bar]:
    if not bars:
        return []
    if tf in ("1d", "5m", "15m"):
        return list(bars)
    tz = ZoneInfo(MARKETS[market]["tz"])
    groups: Dict[Tuple, List[Bar]] = {}
    order: List[Tuple] = []

    if tf == "75m":
        open_h, open_m = MARKETS[market]["open"]
        open_minutes = open_h * 60 + open_m
        for bar in bars:
            dt = datetime.fromtimestamp(bar.t, tz)
            mins = dt.hour * 60 + dt.minute - open_minutes
            if mins < 0:
                continue
            slot = mins // 75
            key = (dt.year, dt.month, dt.day, slot)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(bar)
    else:
        for bar in bars:
            dt = datetime.fromtimestamp(bar.t, tz)
            key = _bucket(dt, tf)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(bar)

    out: List[Bar] = []
    for key in order:
        chunk = groups[key]
        if not chunk:
            continue
        out.append(
            Bar(
                t=chunk[0].t,
                o=chunk[0].o,
                h=max(b.h for b in chunk),
                l=min(b.l for b in chunk),
                c=chunk[-1].c,
                v=sum(b.v for b in chunk),
            )
        )
    return out


def _period_end(bar: Bar, tf: str, market: str) -> datetime:
    cfg = MARKETS[market]
    tz = ZoneInfo(cfg["tz"])
    dt = datetime.fromtimestamp(bar.t, tz)
    ch, cm = cfg["close"]
    close_time = dtime(ch, cm)

    if tf in ("5m", "15m", "75m"):
        minutes = {"5m": 5, "15m": 15, "75m": 75}[tf]
        end = datetime.fromtimestamp(bar.t, tz) + timedelta(minutes=minutes)
        return end

    if tf == "1d":
        return datetime.combine(dt.date(), close_time, tzinfo=tz)

    if tf == "1wk":
        monday = dt.date() - timedelta(days=dt.weekday())
        friday = monday + timedelta(days=4)
        return datetime.combine(friday, close_time, tzinfo=tz)

    if tf == "1mo":
        if dt.month == 12:
            last = date(dt.year + 1, 1, 1) - timedelta(days=1)
        else:
            last = date(dt.year, dt.month + 1, 1) - timedelta(days=1)
        return datetime.combine(last, close_time, tzinfo=tz)

    if tf == "3mo":
        q = (dt.month - 1) // 3
        end_month = (q + 1) * 3
        if end_month == 12:
            last = date(dt.year + 1, 1, 1) - timedelta(days=1)
        else:
            last = date(dt.year, end_month + 1, 1) - timedelta(days=1)
        return datetime.combine(last, close_time, tzinfo=tz)

    if tf == "6mo":
        if dt.month <= 6:
            last = date(dt.year, 6, 30)
        else:
            last = date(dt.year, 12, 31)
        return datetime.combine(last, close_time, tzinfo=tz)

    if tf == "1y":
        return datetime.combine(date(dt.year, 12, 31), close_time, tzinfo=tz)

    return datetime.combine(dt.date(), close_time, tzinfo=tz)


def trim_complete(bars: List[Bar], tf: str, market: str, now: Optional[float] = None) -> List[Bar]:
    """Drop the final bar while its period is still open (partial candle)."""
    if not bars:
        return []
    now_dt = datetime.fromtimestamp(now or time.time(), ZoneInfo(MARKETS[market]["tz"]))
    out = list(bars)
    for _ in range(3):
        if not out:
            break
        end = _period_end(out[-1], tf, market)
        if now_dt >= end:
            break
        out.pop()
    return out


# --------------------------------------------------------------------------- #
# Zones
# --------------------------------------------------------------------------- #


@dataclass
class Zone:
    direction: str
    top: float
    bottom: float
    pattern: str
    base_count: int
    legout_count: int
    gap: bool
    tests: int
    freshness: float
    strength: float
    time_score: float
    score: float
    formed_idx: int
    formed_time: int
    extended: bool = False
    legout_move_atr: float = 0.0

    @property
    def proximal(self) -> float:
        return self.top if self.direction == "demand" else self.bottom

    @property
    def distal(self) -> float:
        return self.bottom if self.direction == "demand" else self.top

    @property
    def height(self) -> float:
        return max(0.0, self.top - self.bottom)

    def key(self) -> Tuple:
        return (self.direction, round(self.top, 4), round(self.bottom, 4), self.formed_idx)

    def to_dict(self, market: str) -> dict:
        tz_name = MARKETS[market]["tz"]
        width_pct = (self.height / self.bottom * 100.0) if self.bottom else 0.0
        return {
            "direction": self.direction,
            "top": _r(self.top),
            "bottom": _r(self.bottom),
            "proximal": _r(self.proximal),
            "distal": _r(self.distal),
            "pattern": self.pattern,
            "baseCount": self.base_count,
            "legoutCount": self.legout_count,
            "gap": bool(self.gap),
            "tests": int(self.tests),
            "freshness": _r(self.freshness, 1),
            "strength": _r(self.strength, 1),
            "timeScore": _r(self.time_score, 1),
            "score": _r(self.score, 1),
            "formed": _iso(self.formed_time, tz_name, False),
            "formedTs": int(self.formed_time),
            "ageBars": None,
            "extended": bool(self.extended),
            "widthPct": _r(width_pct, 2),
            "legoutMoveAtr": _r(self.legout_move_atr, 2),
        }


def _tag_zone(zone: Zone, bars: List[Bar], market: str) -> dict:
    d = zone.to_dict(market)
    d["ageBars"] = max(0, len(bars) - 1 - zone.formed_idx) if bars else None
    return d


def _overlap_ratio(a: Zone, b: Zone) -> float:
    inter = min(a.top, b.top) - max(a.bottom, b.bottom)
    if inter <= 0:
        return 0.0
    denom = min(a.height, b.height)
    if denom <= 0:
        return 0.0
    return inter / denom


def detect_zones(bars: List[Bar], atr: List[float]) -> List[Zone]:
    n = len(bars)
    if n < 12:
        return []
    candidates: List[Zone] = []

    # ---- Base + legout patterns ------------------------------------------- #
    for j in range(1, n - 1):
        if not _is_base(bars[j]):
            continue
        start = j
        while start > 0 and _is_base(bars[start - 1]):
            start -= 1
            if j - start + 1 > 5:
                break
        base_n = j - start + 1
        if base_n > 5 or start == 0:
            continue

        first = bars[j + 1]
        d = _move_dir(first, bars[j].c)
        if d == 0:
            continue

        if d > 0:
            top = max(b.c for b in bars[start : j + 1])
            bottom = min(b.l for b in bars[start : j + 1])
        else:
            top = max(b.h for b in bars[start : j + 1])
            bottom = min(b.c for b in bars[start : j + 1])
        if top <= bottom:
            continue

        atr_j = _atr_at(atr, j)
        legin = bars[start - 1]
        extended = False
        if d > 0 and legin.l < bottom and (bottom - legin.l) <= 0.3 * atr_j:
            bottom = legin.l
            extended = True
        if d < 0 and legin.h > top and (legin.h - top) <= 0.3 * atr_j:
            top = legin.h
            extended = True

        height = top - bottom
        if height < 0.05 * atr_j or height > 2.0 * atr_j:
            continue
        if (d > 0 and first.c <= top) or (d < 0 and first.c >= bottom):
            continue

        e = j + 1
        prev_close = bars[j].c
        while e < n:
            md = _move_dir(bars[e], prev_close)
            if md != d:
                break
            prev_close = bars[e].c
            e += 1
        legout = bars[j + 1 : e]
        if not legout:
            continue

        if d > 0:
            move = max(b.c for b in legout) - top
        else:
            move = bottom - min(b.c for b in legout)
        if move < max(0.75 * atr_j, 1.0 * height):
            continue

        gap = (legout[0].l > bars[j].h) if d > 0 else (legout[0].h < bars[j].l)
        if len(legout) >= 2:
            strength = 2.0
        elif gap:
            strength = 2.0
        else:
            strength = -1.0

        tests = 0
        in_zone = False
        broken = False
        for bar in bars[e:]:
            overlap = bar.l <= top and bar.h >= bottom
            if overlap and not in_zone:
                tests += 1
                in_zone = True
            elif not overlap:
                in_zone = False
            if d > 0 and bar.c < bottom - 0.3 * height:
                broken = True
                break
            if d < 0 and bar.c > top + 0.3 * height:
                broken = True
                break
        if broken:
            continue

        freshness = 3.0 if tests == 0 else (1.5 if tests == 1 else 0.0)
        time_score = 2.0 if base_n <= 3 else 1.0
        score = max(0.0, freshness + strength + time_score)

        legin_dir = 1 if legin.c > legin.o else (-1 if legin.c < legin.o else 0)
        if d > 0:
            pattern = "DBR" if legin_dir < 0 else "RBR"
        else:
            pattern = "RBD" if legin_dir > 0 else "DBD"

        candidates.append(
            Zone(
                direction="demand" if d > 0 else "supply",
                top=top,
                bottom=bottom,
                pattern=pattern,
                base_count=base_n,
                legout_count=len(legout),
                gap=gap,
                tests=tests,
                freshness=freshness,
                strength=strength,
                time_score=time_score,
                score=score,
                formed_idx=e - 1,
                formed_time=bars[e - 1].t,
                extended=extended,
                legout_move_atr=move / atr_j if atr_j else 0.0,
            )
        )

    # ---- Momentum-candle fallback ---------------------------------------- #
    for i in range(2, n - 1):
        bar = bars[i]
        atr_i = _atr_at(atr, i)
        if (bar.h - bar.l) < 1.5 * atr_i or _body_ratio(bar) < 0.55:
            continue
        d = 1 if bar.c > bar.o else (-1 if bar.c < bar.o else 0)
        if d == 0:
            continue
        legin = bars[i - 1]
        if d > 0:
            top = bar.o
            bottom = legin.l
        else:
            top = legin.h
            bottom = bar.o
        if top <= bottom:
            continue
        height = top - bottom
        if height < 0.08 * atr_i or height > 2.2 * atr_i:
            continue
        if (d > 0 and bar.c <= top) or (d < 0 and bar.c >= bottom):
            continue
        move = (bar.c - top) if d > 0 else (bottom - bar.c)
        if move < 0.9 * atr_i:
            continue
        tests = 0
        in_zone = False
        broken = False
        for later in bars[i + 1 :]:
            overlap = later.l <= top and later.h >= bottom
            if overlap and not in_zone:
                tests += 1
                in_zone = True
            elif not overlap:
                in_zone = False
            if d > 0 and later.c < bottom - 0.3 * height:
                broken = True
                break
            if d < 0 and later.c > top + 0.3 * height:
                broken = True
                break
        if broken:
            continue
        freshness = 3.0 if tests == 0 else (1.5 if tests == 1 else 0.0)
        candidates.append(
            Zone(
                direction="demand" if d > 0 else "supply",
                top=top,
                bottom=bottom,
                pattern="MOM",
                base_count=0,
                legout_count=1,
                gap=False,
                tests=tests,
                freshness=freshness,
                strength=2.0,
                time_score=0.0,
                score=max(0.0, freshness + 2.0),
                formed_idx=i,
                formed_time=bar.t,
                extended=False,
                legout_move_atr=move / atr_i if atr_i else 0.0,
            )
        )

    # ---- Deduplicate overlapping zones (keep best / newest) --------------- #
    candidates.sort(key=lambda z: (-z.score, -z.formed_idx, -(z.top - z.bottom)))
    kept: List[Zone] = []
    for zone in candidates:
        duplicate = False
        for other in kept:
            if zone.direction != other.direction:
                continue
            if _overlap_ratio(zone, other) > 0.7 and abs(zone.formed_idx - other.formed_idx) <= 6:
                duplicate = True
                break
        if not duplicate:
            kept.append(zone)
    kept.sort(key=lambda z: (z.formed_idx, z.top))
    return kept


def _fallback_atr(bars: List[Bar], idx: int) -> float:
    lo = max(0, idx - 14)
    ranges = [b.h - b.l for b in bars[lo : idx + 1] if b.h > b.l]
    return _median(ranges) or 0.0001


# --------------------------------------------------------------------------- #
# Location / trend
# --------------------------------------------------------------------------- #


@dataclass
class Location:
    code: str
    action: str
    definite: bool
    price: float
    demand: Optional[Zone]
    supply: Optional[Zone]
    demand_prox: float
    supply_prox: float
    demand_bottom: float
    supply_top: float
    f33: float
    f67: float
    proxy_demand: bool = False
    proxy_supply: bool = False
    missing_demand_zone: bool = False
    missing_supply_zone: bool = False

    def to_dict(self, market: str) -> dict:
        band = LOCATION_BANDS[self.code]
        positions = {
            "very_low": (self.demand_bottom, self.demand_prox),
            "low": (self.demand_prox, self.f33),
            "equilibrium": (self.f33, self.f67),
            "high": (self.f67, self.supply_prox),
            "very_high": (self.supply_prox, self.supply_top),
        }
        band_low, band_high = positions[self.code]
        return {
            "code": self.code,
            "label": band["label"],
            "short": band["short"],
            "zone": band["zone"],
            "band": {"low": _r(band_low), "high": _r(band_high)},
            "action": self.action,
            "definite": bool(self.definite),
            "price": _r(self.price),
            "demand": _tag_zone(self.demand, [], market) if self.demand else None,
            "supply": _tag_zone(self.supply, [], market) if self.supply else None,
            "demandProx": _r(self.demand_prox),
            "supplyProx": _r(self.supply_prox),
            "demandBottom": _r(self.demand_bottom),
            "supplyTop": _r(self.supply_top),
            "fib": {
                "low": _r(self.demand_prox),
                "f33": _r(self.f33),
                "f67": _r(self.f67),
                "high": _r(self.supply_prox),
            },
            "proxyDemand": bool(self.proxy_demand),
            "proxySupply": bool(self.proxy_supply),
            "missingDemandZone": bool(self.missing_demand_zone),
            "missingSupplyZone": bool(self.missing_supply_zone),
            "note": _location_note(self),
        }


def _location_note(loc: Location) -> str:
    if loc.code == "very_high":
        return "Price is inside the supply zone — definite sell per the rules."
    if loc.code == "very_low":
        return "Price is inside the demand zone — definite buy per the rules."
    if loc.code == "equilibrium":
        return "Price is in equilibrium; direction comes from the ITF trend."
    if loc.code == "high":
        return "Price is in the upper fib band; sell setup."
    return "Price is in the lower fib band; buy setup."


def locate(price: float, zones: List[Zone], bars: List[Bar], atr: float) -> Optional[Location]:
    if not bars:
        return None
    demand_inside = [
        z for z in zones if z.direction == "demand" and z.bottom - 1e-9 <= price <= z.top + 1e-9
    ]
    supply_inside = [
        z for z in zones if z.direction == "supply" and z.bottom - 1e-9 <= price <= z.top + 1e-9
    ]

    if demand_inside:
        demand = max(demand_inside, key=lambda z: (z.score, z.top))
    else:
        below = [z for z in zones if z.direction == "demand" and z.top <= price]
        demand = max(below, key=lambda z: (z.top, z.score)) if below else None

    if supply_inside:
        supply = max(supply_inside, key=lambda z: (z.score, -z.bottom))
    else:
        above = [z for z in zones if z.direction == "supply" and z.bottom >= price]
        supply = min(above, key=lambda z: (z.bottom, -z.score)) if above else None

    recent = bars[-40:]
    proxy_demand = demand is None
    proxy_supply = supply is None

    proxy_pad = max(atr, price * 0.005)
    if demand is None:
        swing_low = min(b.l for b in recent)
        d_prox = min(swing_low, price - 0.25 * proxy_pad)
        d_bottom = d_prox - 0.2 * proxy_pad
    else:
        d_prox = demand.top
        d_bottom = demand.bottom

    if supply is None:
        swing_high = max(b.h for b in recent)
        s_prox = max(swing_high, price + 0.25 * proxy_pad)
        s_top = s_prox + 0.2 * proxy_pad
    else:
        s_prox = supply.bottom
        s_top = supply.top

    if not (d_prox < s_prox):
        pad = max(atr, price * 0.01)
        d_prox = min(d_prox, price - pad * 0.25)
        s_prox = max(s_prox, price + pad * 0.25)
        d_bottom = min(d_bottom, d_prox - pad * 0.2)
        s_top = max(s_top, s_prox + pad * 0.2)
    if not (d_prox < s_prox):
        return None

    rng = s_prox - d_prox
    f33 = d_prox + 0.33 * rng
    f67 = d_prox + 0.67 * rng

    if price >= s_prox:
        code = "very_high"
    elif price >= f67:
        code = "high"
    elif price >= f33:
        code = "equilibrium"
    elif price >= d_prox:
        code = "low"
    else:
        code = "very_low"

    return Location(
        code=code,
        action="SELL" if code in ("very_high", "high") else ("BUY" if code in ("very_low", "low") else "TREND"),
        definite=code in ("very_high", "very_low"),
        price=price,
        demand=demand,
        supply=supply,
        demand_prox=d_prox,
        supply_prox=s_prox,
        demand_bottom=d_bottom,
        supply_top=s_top,
        f33=f33,
        f67=f67,
        proxy_demand=proxy_demand,
        proxy_supply=proxy_supply,
        missing_demand_zone=demand is None,
        missing_supply_zone=supply is None,
    )


@dataclass
class Trend:
    direction: str
    close: float
    line: float
    ma: Optional[float]
    ma_pos: Optional[str]
    detail: str

    def to_dict(self) -> dict:
        return {
            "dir": self.direction,
            "label": "Uptrend" if self.direction == "up" else ("Downtrend" if self.direction == "down" else "No trend"),
            "close": _r(self.close),
            "line": _r(self.line),
            "ma": _r(self.ma),
            "maPos": self.ma_pos,
            "detail": self.detail,
        }


def analyze_trend(bars: List[Bar], price: Optional[float] = None) -> Optional[Trend]:
    if len(bars) < 9:
        return None
    closes = [b.c for b in bars]
    close = price if price is not None else closes[-1]
    line = closes[-8]
    period = min(50, len(closes))
    ma = sum(closes[-period:]) / period
    up = close > line
    direction = "up" if up else "down"
    ma_pos = "above" if close >= ma else "below"
    parts = []
    parts.append("price is above the 7-candle line" if up else "price is below the 7-candle line")
    parts.append(f"and {'above' if close >= ma else 'below'} the {period}-period MA")
    return Trend(direction, close, line, ma, ma_pos, " ".join(parts))


# --------------------------------------------------------------------------- #
# Execution zone / plan
# --------------------------------------------------------------------------- #


def choose_execution_zone(
    zones: List[Zone],
    direction: str,
    ltf_zone: Optional[Zone],
    price: float,
    atr: float,
) -> Tuple[Optional[Zone], bool, bool, str]:
    same = [
        z
        for z in zones
        if z.direction == direction
        and not (direction == "demand" and price < z.bottom - 0.1 * atr)
        and not (direction == "supply" and price > z.top + 0.1 * atr)
    ]
    if not same:
        return None, False, False, "No usable execution zones in the trade direction"

    def distance(z: Zone) -> float:
        if z.bottom <= price <= z.top:
            return 0.0
        if price < z.bottom:
            return z.bottom - price
        return price - z.top

    if ltf_zone is not None:
        ltf_low, ltf_high = ltf_zone.bottom, ltf_zone.top
        ltf_h = max(ltf_high - ltf_low, 0.0001)
        scored = []
        for z in same:
            overlap = min(z.top, ltf_high) - max(z.bottom, ltf_low)
            gap = 0.0 if overlap > 0 else max(ltf_low - z.top, z.bottom - ltf_high, 0.0)
            near = gap <= max(1.2 * atr, 0.5 * ltf_h)
            contains = z.bottom <= price <= z.top
            scored.append((z, overlap > 0, near, contains, gap, distance(z)))

        overlapping = [row for row in scored if row[1]]
        near = [row for row in scored if row[2]]
        if overlapping:
            pool = overlapping
            note = "inside the LTF zone"
        elif near:
            pool = near
            note = "near the LTF zone"
        else:
            pool = scored
            note = "away from the LTF zone"
        pool.sort(key=lambda row: (row[2], row[0].score, row[3], -row[5]), reverse=True)
        z, has_overlap, is_near, _, _, _ = pool[0]
        return z, bool(is_near), bool(has_overlap), note

    same.sort(key=lambda z: (distance(z), -z.score))
    z = same[0]
    return z, True, False, "HTF zone unavailable; using nearest execution zone"


@dataclass
class Plan:
    entry: float
    entry_type: str
    stop: float
    target: float
    risk: float
    reward: float
    rr: float
    risk_pct: float
    reward_pct: float
    target_source: str
    target_zone: Optional[Zone]
    price_inside: bool
    valid: bool
    note: str

    def to_dict(self, market: str) -> dict:
        return {
            "entry": _r(self.entry),
            "entryType": self.entry_type,
            "stop": _r(self.stop),
            "target": _r(self.target),
            "risk": _r(self.risk),
            "reward": _r(self.reward),
            "rr": _r(self.rr, 2),
            "riskPct": _pct(self.risk_pct),
            "rewardPct": _pct(self.reward_pct),
            "targetSource": self.target_source,
            "targetZone": _tag_zone(self.target_zone, [], market) if self.target_zone else None,
            "priceInside": bool(self.price_inside),
            "valid": bool(self.valid),
            "note": self.note,
        }


def build_plan(
    direction: str,
    zone: Zone,
    price: float,
    zones: List[Zone],
    atr: float,
) -> Plan:
    height = max(zone.height, 1e-9)
    buffer = max(0.12 * height, 0.08 * atr)
    inside = zone.bottom <= price <= zone.top

    if direction == "demand":
        if price < zone.bottom:
            return Plan(
                entry=zone.top,
                entry_type="—",
                stop=zone.bottom - buffer,
                target=zone.top,
                risk=0,
                reward=0,
                rr=0,
                risk_pct=0,
                reward_pct=0,
                target_source="—",
                target_zone=None,
                price_inside=False,
                valid=False,
                note="Price has broken below the execution demand zone.",
            )
        entry = price if inside else zone.top
        entry_type = "Market" if inside else "Limit"
        stop = zone.bottom - buffer
        risk = entry - stop
        if risk <= 0:
            risk = height + buffer
            stop = entry - risk
        candidates = [
            z
            for z in zones
            if z.direction == "supply" and z.proximal > entry + 0.25 * risk
        ]
        if candidates:
            target_zone = min(candidates, key=lambda z: z.proximal)
            target = target_zone.proximal
            source = "Opposing ETF supply zone"
        else:
            target_zone = None
            target = entry + 2.0 * risk
            source = "2R projection"
        if target <= entry + 0.5 * risk:
            target = entry + 2.0 * risk
            target_zone = None
            source = "2R projection"
        reward = target - entry
        rr = reward / risk if risk > 0 else 0
        note = "Buy the pullback at the demand zone." if not inside else "Price is inside the demand zone."
    else:
        if price > zone.top:
            return Plan(
                entry=zone.bottom,
                entry_type="—",
                stop=zone.top + buffer,
                target=zone.bottom,
                risk=0,
                reward=0,
                rr=0,
                risk_pct=0,
                reward_pct=0,
                target_source="—",
                target_zone=None,
                price_inside=False,
                valid=False,
                note="Price has broken above the execution supply zone.",
            )
        entry = price if inside else zone.bottom
        entry_type = "Market" if inside else "Limit"
        stop = zone.top + buffer
        risk = stop - entry
        if risk <= 0:
            risk = height + buffer
            stop = entry + risk
        candidates = [
            z
            for z in zones
            if z.direction == "demand" and z.proximal < entry - 0.25 * risk
        ]
        if candidates:
            target_zone = max(candidates, key=lambda z: z.proximal)
            target = target_zone.proximal
            source = "Opposing ETF demand zone"
        else:
            target_zone = None
            target = entry - 2.0 * risk
            source = "2R projection"
        if target >= entry - 0.5 * risk:
            target = entry - 2.0 * risk
            target_zone = None
            source = "2R projection"
        reward = entry - target
        rr = reward / risk if risk > 0 else 0
        note = "Sell the rally at the supply zone." if not inside else "Price is inside the supply zone."

    return Plan(
        entry=entry,
        entry_type=entry_type,
        stop=stop,
        target=target,
        risk=risk,
        reward=reward,
        rr=rr,
        risk_pct=(risk / entry * 100.0) if entry else 0,
        reward_pct=(reward / entry * 100.0) if entry else 0,
        target_source=source,
        target_zone=target_zone,
        price_inside=inside,
        valid=True,
        note=note,
    )


# --------------------------------------------------------------------------- #
# Context / setup build
# --------------------------------------------------------------------------- #


class Frame:
    def __init__(self, tf: str, bars: List[Bar], complete: List[Bar], market: str):
        self.tf = tf
        self.bars = bars
        self.complete = complete
        self.market = market
        self.atr = atr_series(complete)
        self._zones: Optional[List[Zone]] = None

    def zones(self) -> List[Zone]:
        if self._zones is None:
            self._zones = detect_zones(self.complete, self.atr)
        return self._zones


def _live_quote(live: Optional[dict]) -> dict:
    if not isinstance(live, dict):
        return {}
    try:
        ltp = float(live.get("ltp") or 0)
    except (TypeError, ValueError):
        return {}
    if ltp <= 0:
        return {}
    out = {"ltp": ltp, "ts": int(live.get("ts") or time.time())}
    for key in ("change", "changePct", "prevClose"):
        try:
            value = live.get(key)
            out[key] = float(value) if value is not None else None
        except (TypeError, ValueError):
            out[key] = None
    return out


class SymbolContext:
    def __init__(
        self,
        symbol: str,
        name: str,
        market: str,
        payloads: dict,
        now: Optional[float] = None,
        live: Optional[dict] = None,
    ):
        self.symbol = symbol
        self.name = name
        self.market = market
        self.now = now or time.time()
        self.frames: Dict[str, Frame] = {}
        self.meta: dict = {}
        self._build_frames(payloads)
        self._price = self._resolve_price()
        self._change = self._resolve_change()
        self._live = _live_quote(live)
        if self._live:
            self._price = self._live["ltp"]
            if self._live.get("changePct") is not None:
                self._change = float(self._live["changePct"])
        self._trends: Dict[str, Optional[Trend]] = {}

    def _build_frames(self, payloads: dict) -> None:
        daily = _bars_from_payload(payloads["1d"]) if "1d" in payloads else []
        b15 = _bars_from_payload(payloads["15m"]) if "15m" in payloads else []
        b5 = _bars_from_payload(payloads["5m"]) if "5m" in payloads else []
        meta = {}
        for key in ("1d", "15m", "5m"):
            if key in payloads and payloads[key].get("meta"):
                meta = dict(payloads[key]["meta"])
                if key == "1d" or not self.meta:
                    self.meta = meta
        for tf in TF_INTERVAL:
            if tf in ("1d", "1wk", "1mo", "3mo", "6mo", "1y"):
                base = daily
            elif tf == "75m":
                base = b15
            elif tf == "15m":
                base = b15
            elif tf == "5m":
                base = b5
            else:
                continue
            if not base:
                continue
            bars = resample_bars(base, tf, self.market)
            if not bars:
                continue
            complete = trim_complete(bars, tf, self.market, self.now)
            self.frames[tf] = Frame(tf, bars, complete, self.market)

    def _resolve_price(self) -> float:
        price = self.meta.get("regularMarketPrice")
        try:
            if price:
                return float(price)
        except (TypeError, ValueError):
            pass
        for tf in ("1d", "15m", "5m", "75m"):
            frame = self.frames.get(tf)
            if frame and frame.bars:
                return frame.bars[-1].c
        return 0.0

    def _resolve_change(self) -> float:
        daily = self.frames.get("1d")
        prev = None
        if daily and len(daily.bars) >= 2:
            last = daily.bars[-1]
            age_days = (self.now - last.t) / 86400.0
            if age_days <= 8:
                prev = daily.bars[-2].c
        if prev is None:
            try:
                prev = float(self.meta.get("previousClose"))
            except (TypeError, ValueError):
                return 0.0
        if prev <= 0 or not self.price:
            return 0.0
        return (self.price - prev) / prev * 100.0

    @property
    def price(self) -> float:
        return self._price

    @property
    def change(self) -> float:
        return self._change

    def trend(self, tf: str) -> Optional[Trend]:
        if tf not in self._trends:
            frame = self.frames.get(tf)
            self._trends[tf] = analyze_trend(frame.complete, self.price) if frame else None
        return self._trends[tf]

    def meta_out(self) -> dict:
        as_of = self._live.get("ts") if self._live else None
        if not as_of:
            as_of = self.meta.get("regularMarketTime") or (
                self.frames.get("1d").bars[-1].t if self.frames.get("1d") else self.now
            )
        return {
            "price": _r(self.price),
            "changePct": _pct(self.change),
            "currency": self.meta.get("currency") or MARKETS[self.market]["currency"],
            "currencySymbol": MARKETS[self.market]["currencySymbol"],
            "exchange": self.meta.get("exchange", ""),
            "asOf": int(as_of),
            "stale": False,
            "live": bool(self._live),
            "liveTime": int(self._live["ts"]) if self._live else None,
            "priceSource": "Kotak" if self._live else "Yahoo",
        }


def load_context(
    symbol: str,
    name: str,
    market: str,
    tfs: Iterable[str],
    now: Optional[float] = None,
    live: Optional[dict] = None,
) -> SymbolContext:
    need = set(tfs)
    payloads: dict = {}
    intervals = {TF_INTERVAL[tf] for tf in need}
    if "1d" in intervals:
        payloads["1d"] = DATA.fetch(symbol, market, "1d", "20y")
    if "15m" in intervals:
        payloads["15m"] = DATA.fetch(symbol, market, "15m", "60d")
    if "5m" in intervals:
        payloads["5m"] = DATA.fetch(symbol, market, "5m", "60d")
    if not payloads:
        raise DataError("no timeframes requested")
    stale = any(p.get("stale") for p in payloads.values())
    ctx = SymbolContext(symbol, name, market, payloads, now, live)
    if stale:
        ctx.meta["stale"] = True
    return ctx


def _grade(score: float) -> str:
    if score >= 6:
        return "A+"
    if score >= 5:
        return "A"
    if score >= 4:
        return "B"
    if score >= 3:
        return "C"
    return "D"


def _zone_side(loc: Location, action: str) -> Tuple[Optional[Zone], str]:
    if action == "BUY":
        return loc.demand, "demand"
    return loc.supply, "supply"


# --------------------------------------------------------------------------- #
# Pattern + volume confluence at the execution zone
# --------------------------------------------------------------------------- #

CONFLUENCE_MAX = 3.0
CONFLUENCE_FILTER = 1.2


def _avg(values: Iterable[float]) -> float:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return sum(vals) / len(vals) if vals else 0.0


def _obv_slope(bars: List[Bar]) -> float:
    """OBV trend per bar, normalised by average volume (positive = accumulation)."""
    if len(bars) < 3:
        return 0.0
    avg = _avg(bar.v for bar in bars)
    if avg <= 0:
        return 0.0
    obv = 0.0
    first = 0.0
    prev = bars[0].c
    for i, bar in enumerate(bars):
        if bar.c > prev:
            obv += bar.v
        elif bar.c < prev:
            obv -= bar.v
        if i == 0:
            first = obv
        prev = bar.c
    return (obv - first) / (len(bars) - 1) / avg


def _confluence_empty(direction: str, reason: str = "") -> dict:
    return {
        "score": 0.0,
        "max": CONFLUENCE_MAX,
        "label": "None",
        "tone": "none",
        "direction": direction,
        "patterns": [],
        "volume": {
            "available": False,
            "avg20": None,
            "last": None,
            "rvol": None,
            "baseDryUp": None,
            "legoutExpansion": None,
            "retestRvol": None,
            "upDownRatio": None,
            "obvSlope": None,
            "climax": False,
        },
        "note": reason or "No pattern or volume confluence measured at the execution zone.",
    }


def analyze_confluence(
    frame: Frame,
    zone: Optional[Zone],
    direction: str,
    volume_expected: bool = True,
) -> dict:
    """Find accumulation/distribution patterns and volume build-up around an execution zone.

    Mirrored for demand (BUY) and supply (SELL). Volume rules are skipped when the
    instrument publishes no usable volume series (indices), leaving price patterns.
    """
    if zone is None or frame is None:
        return _confluence_empty(direction)
    bars = frame.complete
    n = len(bars)
    if n < 20:
        return _confluence_empty(direction, "Not enough completed candles to test a pattern.")

    atr = _atr_at(frame.atr, n - 1)
    height = max(zone.height, 0.0001)
    top, bottom = zone.top, zone.bottom
    proximal, distal = zone.proximal, zone.distal
    demand = direction == "demand"

    end = min(max(zone.formed_idx, 0), n - 1)
    legout_start = max(1, end - max(zone.legout_count, 1) + 1)
    base_end = max(0, legout_start - 1)
    base_start = max(0, base_end - max(zone.base_count, 1) + 1)
    base_bars = bars[base_start : base_end + 1]
    legout_bars = bars[legout_start : end + 1]
    prior_bars = bars[max(0, base_start - 20) : base_start]

    avg20 = _avg(bar.v for bar in bars[-20:])
    filled = sum(1 for bar in bars[-20:] if bar.v > 0)
    base_vol = _avg(bar.v for bar in base_bars)
    legout_vol = _avg(bar.v for bar in legout_bars)
    prior_vol = _avg(bar.v for bar in prior_bars)
    volume_ok = volume_expected and avg20 > 0 and filled >= 12
    dryup = (base_vol / prior_vol) if prior_vol > 0 and base_vol > 0 else None
    expansion = (legout_vol / base_vol) if base_vol > 0 and legout_vol > 0 else None
    last_rvol = (bars[-1].v / avg20) if volume_ok and bars[-1].v > 0 else None

    tests: List[int] = []
    in_zone = False
    for i in range(end + 1, n):
        bar = bars[i]
        overlap = bar.l <= top and bar.h >= bottom
        if overlap and not in_zone:
            tests.append(i)
            in_zone = True
        elif not overlap:
            in_zone = False

    patterns: List[dict] = []

    def add(
        code: str,
        label: str,
        detail: str,
        points: float,
        idx: Optional[int] = None,
    ) -> None:
        item = {"code": code, "label": label, "detail": detail, "points": round(points, 2)}
        if idx is not None and 0 <= idx < n:
            item["ts"] = bars[idx].t
        patterns.append(item)

    # Spring / upthrust: pierce the distal edge and close back inside the zone.
    spring_idx = None
    for i in range(max(end + 1, n - 30), n):
        bar = bars[i]
        if demand:
            pierced = bar.l < bottom - 0.02 * height
            reclaimed = bar.c > bottom and bar.c >= bar.o
        else:
            pierced = bar.h > top + 0.02 * height
            reclaimed = bar.c < top and bar.c <= bar.o
        if pierced and reclaimed:
            spring_idx = i
    if spring_idx is not None:
        bar = bars[spring_idx]
        rvol = (bar.v / avg20) if volume_ok else 0.0
        vol_txt = f" on {rvol:.1f}× average volume" if volume_ok and rvol > 0 else ""
        add(
            "spring" if demand else "upthrust",
            "Spring reclaim" if demand else "Upthrust rejection",
            ("Wick pierced the zone bottom and closed back inside" if demand
             else "Wick pierced the zone top and closed back inside") + vol_txt + ".",
            1.2,
            spring_idx,
        )

    # Second test with a higher low (demand) / lower high (supply) and lighter volume.
    if len(tests) >= 2:
        first_i, second_i = tests[-2], tests[-1]
        first, second = bars[first_i], bars[second_i]
        if demand:
            structural = second.l >= first.l - 0.15 * height
            lighter = (not volume_ok) or second.v <= max(first.v, avg20) * 0.98
            detail = "Second demand test held a higher low"
        else:
            structural = second.h <= first.h + 0.15 * height
            lighter = (not volume_ok) or second.v <= max(first.v, avg20) * 0.98
            detail = "Second supply test printed a lower high"
        if structural:
            detail += " with lighter volume." if lighter else "."
            add("double_test", "Double tap", detail, 0.9 if lighter else 0.6, second_i)

    # Rejection wick in the last five bars, at the zone.
    for k, bar in enumerate(bars[-5:]):
        rng = bar.h - bar.l
        if rng <= 0:
            continue
        near_zone = bar.l <= top + 0.35 * height if demand else bar.h >= bottom - 0.35 * height
        if not near_zone:
            continue
        wick = (min(bar.o, bar.c) - bar.l) if demand else (bar.h - max(bar.o, bar.c))
        if wick >= 0.5 * rng and ((demand and bar.c >= bar.l + 0.6 * rng) or (not demand and bar.c <= bar.h - 0.6 * rng)):
            add(
                "rejection",
                "Rejection wick" if demand else "Supply wick",
                f"{wick / rng * 100:.0f}% wick with the close back away from the zone.",
                0.6,
                n - 5 + k,
            )
            break

    # Engulfing candle at the zone in the last three bars.
    if n >= 2:
        prev, last = bars[-2], bars[-1]
        near_zone = last.l <= top + 0.5 * height if demand else last.h >= bottom - 0.5 * height
        volume_boost = (not volume_ok) or last.v >= avg20 * 1.05
        if demand and near_zone and prev.c < prev.o and last.c > last.o and last.o <= prev.c and last.c >= prev.o and volume_boost:
            add("engulfing", "Bullish engulfing", "Close engulfed the prior red candle at demand with above-average volume.", 0.6, n - 1)
        elif not demand and near_zone and prev.c > prev.o and last.c < last.o and last.o >= prev.c and last.c <= prev.o and volume_boost:
            add("engulfing", "Bearish engulfing", "Close engulfed the prior green candle at supply with above-average volume.", 0.6, n - 1)

    # Base dry-up followed by legout expansion.
    if volume_ok and dryup is not None and expansion is not None:
        if dryup <= 0.85 and expansion >= 1.2:
            add(
                "dryup_expansion",
                "Dry-up → expansion",
                f"Base volume ran at {dryup:.2f}× the prior range, then the legout expanded to {expansion:.2f}× the base.",
                1.0,
                end,
            )
        elif dryup <= 0.95 and expansion >= 1.1:
            add(
                "dryup_expansion",
                "Mild volume build",
                f"Base volume {dryup:.2f}× prior, legout {expansion:.2f}× base.",
                0.5,
                end,
            )

    # Retest absorbed on high volume while the zone held.
    if volume_ok and tests:
        t = tests[-1]
        bar = bars[t]
        held = bar.c >= proximal if demand else bar.c <= proximal
        rvol = bar.v / avg20
        if held and rvol >= 1.25 and t >= n - 12:
            add(
                "retest_absorbed",
                "Absorbed retest",
                f"Latest zone test held on {rvol:.1f}× average volume.",
                0.6,
                t,
            )

    # OBV accumulation/distribution while price ranges.
    window = bars[-20:]
    slope = _obv_slope(window) if volume_ok else 0.0
    spread = (max(b.h for b in window) - min(b.l for b in window)) if window else 0.0
    if volume_ok and spread <= 2.5 * atr:
        if demand and slope >= 0.08:
            add("obv", "OBV accumulation", "On-balance volume rises while price holds a tight range.", 0.6)
        elif not demand and slope <= -0.08:
            add("obv", "OBV distribution", "On-balance volume falls while price holds a tight range.", 0.6)

    # Volume climax at the zone.
    climax = False
    if volume_ok:
        recent = bars[-10:]
        peak_idx = max(range(len(recent)), key=lambda i: recent[i].v) if recent else None
        peak = recent[peak_idx] if peak_idx is not None else None
        if peak is not None and avg20 > 0 and peak.v >= 2.2 * avg20:
            at_zone = peak.l <= top + 0.5 * height if demand else peak.h >= bottom - 0.5 * height
            reversed_away = peak.c >= peak.o if demand else peak.c <= peak.o
            if at_zone and reversed_away:
                climax = True
                add(
                    "climax",
                    "Climax reversal",
                    f"{peak.v / avg20:.1f}× volume spike reversed at the zone.",
                    0.5,
                    n - len(recent) + peak_idx,
                )

    up_vol = sum(b.v for b in bars[-20:] if b.c >= b.o and b.v > 0)
    down_vol = sum(b.v for b in bars[-20:] if b.c < b.o and b.v > 0)
    retest_rvol = None
    if volume_ok and tests and bars[tests[-1]].v > 0:
        retest_rvol = bars[tests[-1]].v / avg20

    score = min(CONFLUENCE_MAX, round(sum(p["points"] for p in patterns), 2))
    if score >= 2.0:
        label, tone = "Strong", "strong"
    elif score >= 1.0:
        label, tone = "Moderate", "moderate"
    elif score > 0:
        label, tone = "Mild", "mild"
    else:
        label, tone = "None", "none"

    patterns.sort(key=lambda p: -p["points"])
    if patterns:
        note = " + ".join(p["label"] for p in patterns[:3]) + "."
    else:
        note = "Price is at a valid zone but no pattern or volume build-up was detected."
    if not volume_ok:
        note += " No volume series for this instrument — price patterns only."

    return {
        "score": score,
        "max": CONFLUENCE_MAX,
        "label": label,
        "tone": tone,
        "direction": direction,
        "patterns": patterns,
        "volume": {
            "available": bool(volume_ok),
            "avg20": int(round(avg20)) if volume_ok else None,
            "last": int(round(bars[-1].v)) if volume_ok else None,
            "rvol": _r(last_rvol, 2),
            "baseDryUp": _r(dryup, 2),
            "legoutExpansion": _r(expansion, 2),
            "retestRvol": _r(retest_rvol, 2),
            "upDownRatio": _r(up_vol / down_vol, 2) if volume_ok and down_vol > 0 else None,
            "obvSlope": _r(slope, 3) if volume_ok else None,
            "climax": bool(climax),
        },
        "note": note,
    }


def build_setup(ctx: SymbolContext, trade_type: str, min_score: float = 4.0) -> dict:
    cfg = TRADES[trade_type]
    ltf_tf, itf_tf, etf_tf = cfg["ltf"], cfg["itf"], cfg["etf"]
    base = {
        "symbol": _display_symbol(ctx.symbol),
        "yahooSymbol": ctx.symbol,
        "name": ctx.name or ctx.meta.get("longName") or _display_symbol(ctx.symbol),
        "market": ctx.market,
        "tradeType": trade_type,
        "tradeName": cfg["name"],
        "tradeShort": cfg["short"],
        "horizon": cfg["horizon"],
        "timeframes": {
            "ltf": ltf_tf,
            "itf": itf_tf,
            "etf": etf_tf,
            "ltfLabel": TF_LABELS[ltf_tf],
            "itfLabel": TF_LABELS[itf_tf],
            "etfLabel": TF_LABELS[etf_tf],
        },
        "price": _r(ctx.price),
        "changePct": _pct(ctx.change),
        "currency": ctx.meta.get("currency") or MARKETS[ctx.market]["currency"],
        "currencySymbol": MARKETS[ctx.market]["currencySymbol"],
        "asOf": int(ctx.meta.get("regularMarketTime") or ctx.now),
    }

    for tf in (ltf_tf, itf_tf, etf_tf):
        frame = ctx.frames.get(tf)
        if frame is None or len(frame.complete) < 12:
            base.update(
                {
                    "available": False,
                    "tradeable": False,
                    "status": "none",
                    "statusLabel": "No data",
                    "reason": f"Not enough {TF_LABELS[tf]} history for {_display_symbol(ctx.symbol)}.",
                    "score": None,
                    "grade": "—",
                }
            )
            return base

    ltf_frame = ctx.frames[ltf_tf]
    etf_frame = ctx.frames[etf_tf]
    ltf_zones = ltf_frame.zones()
    if not ltf_zones:
        base.update(
            {
                "available": False,
                "tradeable": False,
                "status": "none",
                "statusLabel": "No zones",
                "reason": f"No valid demand/supply zones found on the {TF_LABELS[ltf_tf]} chart.",
                "score": None,
                "grade": "—",
            }
        )
        return base

    atr_ltf = _atr_at(ltf_frame.atr, len(ltf_frame.complete) - 1)
    loc = locate(ctx.price, ltf_zones, ltf_frame.complete, atr_ltf)
    if loc is None:
        base.update(
            {
                "available": False,
                "tradeable": False,
                "status": "none",
                "statusLabel": "No range",
                "reason": "Demand and supply zones could not be mapped around price.",
                "score": None,
                "grade": "—",
            }
        )
        return base

    trend = ctx.trend(itf_tf)
    if loc.code == "equilibrium":
        if trend is None:
            base.update(
                {
                    "available": False,
                    "tradeable": False,
                    "status": "none",
                    "statusLabel": "No trend",
                    "reason": "Price is in equilibrium and the ITF trend could not be established.",
                    "location": loc.to_dict(ctx.market),
                    "score": None,
                    "grade": "—",
                }
            )
            return base
        action = "BUY" if trend.direction == "up" else "SELL"
        definite = False
    else:
        action = loc.action
        definite = loc.definite

    direction = "demand" if action == "BUY" else "supply"
    ltf_side, _ = _zone_side(loc, action)
    atr_etf = _atr_at(etf_frame.atr, len(etf_frame.complete) - 1)
    etf_zones = etf_frame.zones()
    exec_zone, near, overlap, proximity_note = choose_execution_zone(
        etf_zones, direction, ltf_side, ctx.price, atr_etf
    )

    aligned = None
    if trend is not None:
        aligned = (action == "BUY" and trend.direction == "up") or (
            action == "SELL" and trend.direction == "down"
        )

    base["location"] = loc.to_dict(ctx.market)
    for side in ("demand", "supply"):
        side_zone = getattr(loc, side)
        if base["location"].get(side) is not None and side_zone is not None:
            base["location"][side]["ageBars"] = max(
                0, len(ltf_frame.complete) - 1 - side_zone.formed_idx
            )
    base["trend"] = trend.to_dict() if trend else {"dir": "na", "label": "No trend"}
    base["action"] = action
    base["definite"] = definite
    base["aligned"] = aligned
    base["ltfZone"] = _tag_zone(ltf_side, ltf_frame.complete, ctx.market) if ltf_side else None
    base["execNote"] = proximity_note

    warnings: List[str] = []
    if aligned is False:
        if definite:
            warnings.append("Counter-trend: definite location signal overrides the ITF trend.")
        else:
            warnings.append("Counter-trend: ITF trend opposes this location signal.")
    if loc.proxy_demand and action == "BUY":
        warnings.append("No HTF demand zone found; swing-low proxy used for the location range.")
    if loc.proxy_supply and action == "SELL":
        warnings.append("No HTF supply zone found; swing-high proxy used for the location range.")
    if not near:
        warnings.append("Execution zone is not close to the HTF location zone.")
    if exec_zone is not None and exec_zone.score < 4:
        warnings.append("Execution zone scored below 4/7 (tested or weak legout).")

    if exec_zone is None:
        base.update(
            {
                "available": True,
                "tradeable": False,
                "status": "watch",
                "statusLabel": "No execution zone",
                "reason": f"No {direction} zone found on the {TF_LABELS[etf_tf]} execution chart.",
                "zone": None,
                "plan": None,
                "score": None,
                "grade": "—",
                "confluence": _confluence_empty(
                    direction, "No execution zone to measure a pattern against."
                ),
                "warnings": warnings,
                "reasons": [f"No {direction} execution zone near the HTF location zone."],
            }
        )
        return base

    plan = build_plan(direction, exec_zone, ctx.price, etf_zones, atr_etf)
    score = exec_zone.score
    entry_gap = abs(ctx.price - plan.entry) if plan.valid else 0.0
    entry_gap_atr = entry_gap / atr_etf if atr_etf > 0 else 0.0
    entry_gap_pct = (entry_gap / ctx.price * 100.0) if ctx.price else 0.0
    max_gap_pct = MAX_ENTRY_GAP_PCT.get(trade_type, 2.0)
    entry_ready = plan.valid and (
        plan.price_inside or (entry_gap_atr <= 1.0 and entry_gap_pct <= max_gap_pct)
    )
    reasons: List[str] = []

    if plan.valid and score < min_score:
        reasons.append(f"Score {score:.1f}/7 is below the {min_score:.1f} threshold.")
    if plan.valid and plan.rr < 1.2:
        reasons.append(f"Reward:risk {plan.rr:.2f} is below 1.2.")
    if not plan.valid:
        reasons.append(plan.note)
    if not near:
        reasons.append("Execution zone is not near the HTF location zone.")
    if plan.valid and not entry_ready:
        reasons.append(
            f"Entry is {entry_gap_pct:.1f}% away from price; wait for the zone to be reached."
        )
    if aligned is False and not definite:
        reasons.append("Counter-trend: ITF trend opposes this non-definite location signal.")

    base_ok = (
        plan.valid
        and score >= min_score
        and plan.rr >= 1.2
        and near
        and not (aligned is False and not definite)
    )
    tradeable = base_ok and entry_ready

    if tradeable:
        status = "trade"
        status_label = "Trade-ready"
    elif base_ok:
        status = "pending"
        status_label = "Pending entry"
    elif plan.valid:
        status = "watch"
        status_label = "Watch"
    else:
        status = "none"
        status_label = "No setup"

    if exec_zone.tests == 0:
        freshness_label = "Fresh"
    elif exec_zone.tests == 1:
        freshness_label = "Tested once"
    else:
        freshness_label = f"Tested {exec_zone.tests}×"

    base.update(
        {
            "available": True,
            "tradeable": tradeable,
            "status": status,
            "statusLabel": status_label,
            "action": action,
            "definite": definite,
            "zone": _tag_zone(exec_zone, etf_frame.complete, ctx.market),
            "plan": plan.to_dict(ctx.market),
            "score": _r(score, 1),
            "grade": _grade(score),
            "confluence": analyze_confluence(
                etf_frame,
                exec_zone,
                direction,
                volume_expected=not str(ctx.symbol).startswith("^"),
            ),
            "freshnessLabel": freshness_label,
            "warnings": warnings,
            "reasons": reasons,
            "execNear": bool(near),
            "execOverlap": bool(overlap),
            "execTimeframe": TF_LABELS[etf_tf],
            "entryNear": bool(entry_ready),
            "entryGapAtr": _r(entry_gap_atr, 2),
            "entryGapPct": _pct(entry_gap_pct),
            "entryRangePct": _pct(max_gap_pct),
            "inRange": bool(plan.valid and entry_gap_pct <= max_gap_pct),
        }
    )
    if ctx.meta.get("stale"):
        base["warnings"].append("Yahoo data cache is stale; prices may be delayed.")
    return base


def _best_key(item: dict) -> Tuple:
    score = item.get("score") if item.get("score") is not None else -1
    plan = item.get("plan") or {}
    rr = plan.get("rr") if plan.get("rr") is not None else -1
    loc = item.get("location") or {}
    band_rank = LOCATION_BANDS.get(loc.get("code", ""), {}).get("rank", 0)
    status_rank = {"trade": 3, "pending": 2, "watch": 1}.get(item.get("status", ""), 0)
    conf = (item.get("confluence") or {}).get("score") or 0
    return (
        status_rank,
        score,
        conf,
        1 if item.get("definite") else 0,
        band_rank,
        rr,
    )


def analyze_symbol(
    symbol: str,
    name: str,
    market: str,
    trade_types: Iterable[str],
    min_score: float = 4.0,
    now: Optional[float] = None,
    live: Optional[dict] = None,
) -> dict:
    types = [t for t in TRADE_ORDER if t in set(trade_types)]
    if not types:
        types = ["DIT"]
    needed = set()
    for t in types:
        cfg = TRADES[t]
        needed.update([cfg["ltf"], cfg["itf"], cfg["etf"]])

    try:
        ctx = load_context(symbol, name, market, needed, now, live)
    except Exception as exc:
        return {
            "symbol": _display_symbol(symbol),
            "yahooSymbol": symbol,
            "name": name or _display_symbol(symbol),
            "market": market,
            "available": False,
            "tradeable": False,
            "status": "error",
            "statusLabel": "Data error",
            "reason": str(exc),
            "score": None,
            "grade": "—",
            "setups": {},
        }

    setups: Dict[str, dict] = {}
    for t in types:
        try:
            setups[t] = build_setup(ctx, t, min_score)
        except Exception as exc:
            setups[t] = {
                "symbol": _display_symbol(symbol),
                "yahooSymbol": symbol,
                "name": name or _display_symbol(symbol),
                "market": market,
                "tradeType": t,
                "tradeName": TRADES[t]["name"],
                "tradeShort": TRADES[t]["short"],
                "timeframes": {
                    "ltf": TRADES[t]["ltf"],
                    "itf": TRADES[t]["itf"],
                    "etf": TRADES[t]["etf"],
                    "ltfLabel": TF_LABELS[TRADES[t]["ltf"]],
                    "itfLabel": TF_LABELS[TRADES[t]["itf"]],
                    "etfLabel": TF_LABELS[TRADES[t]["etf"]],
                },
                "available": False,
                "tradeable": False,
                "status": "error",
                "statusLabel": "Scan error",
                "reason": str(exc),
                "score": None,
                "grade": "—",
            }

    best_type = max(setups.keys(), key=lambda t: _best_key(setups[t]))
    best = dict(setups[best_type])
    best["setups"] = setups
    best["bestTradeType"] = best_type
    best["meta"] = ctx.meta_out()
    best["meta"]["stale"] = bool(ctx.meta.get("stale"))
    return best


# --------------------------------------------------------------------------- #
# Detail / chart payload
# --------------------------------------------------------------------------- #


def _chart_bars(frame: Frame, limit: int = 240) -> List[Bar]:
    return frame.bars[-limit:]


def get_detail(
    symbol: str,
    name: str,
    market: str,
    trade_type: str,
    min_score: float = 4.0,
    limit: int = 240,
    live: Optional[dict] = None,
) -> dict:
    if trade_type not in TRADES:
        trade_type = "DIT"
    all_types = TRADE_ORDER
    needed = set()
    for t in all_types:
        cfg = TRADES[t]
        needed.update([cfg["ltf"], cfg["itf"], cfg["etf"]])
    ctx = load_context(symbol, name, market, needed, live=live)
    setups = {}
    for t in all_types:
        try:
            setups[t] = build_setup(ctx, t, min_score)
        except Exception as exc:
            setups[t] = {
                "symbol": _display_symbol(symbol),
                "yahooSymbol": symbol,
                "name": name or _display_symbol(symbol),
                "market": market,
                "tradeType": t,
                "tradeName": TRADES[t]["name"],
                "tradeShort": TRADES[t]["short"],
                "available": False,
                "tradeable": False,
                "status": "error",
                "statusLabel": "Scan error",
                "reason": str(exc),
                "score": None,
                "grade": "—",
            }
    setup = setups[trade_type]

    etf_tf = TRADES[trade_type]["etf"]
    frame = ctx.frames[etf_tf]
    bars = _chart_bars(frame, limit)
    zones = frame.zones()
    selected_zone = None
    selected_key = None
    target_key = None
    if setup.get("zone"):
        selected_key = (
            setup["zone"]["direction"],
            round(setup["zone"]["top"], 2),
            round(setup["zone"]["bottom"], 2),
            int(setup["zone"]["formedTs"]),
        )
    if setup.get("plan") and setup["plan"].get("targetZone"):
        tz = setup["plan"]["targetZone"]
        target_key = (tz["direction"], round(tz["top"], 2), round(tz["bottom"], 2), int(tz["formedTs"]))

    visible_low = min(b.l for b in bars) if bars else 0
    visible_high = max(b.h for b in bars) if bars else 0
    pad = (visible_high - visible_low) * 0.08 if bars else 0

    chart_zones = []
    for z in zones:
        overlaps_view = z.bottom <= visible_high + pad and z.top >= visible_low - pad
        key = (z.direction, round(z.top, 2), round(z.bottom, 2), int(z.formed_time))
        is_selected = key == selected_key
        is_target = key == target_key
        if not overlaps_view and not is_selected and not is_target:
            continue
        d = _tag_zone(z, frame.complete, market)
        d["selected"] = is_selected
        d["target"] = is_target
        d["formedIdx"] = z.formed_idx
        chart_zones.append(d)

    if selected_key:
        for z in chart_zones:
            if z["selected"]:
                selected_zone = z
                break

    chart_zones.sort(key=lambda z: (z["direction"], -z["top"]))
    res = {
        "setup": setup,
        "setups": setups,
        "chart": {
            "timeframe": etf_tf,
            "timeframeLabel": TF_LABELS[etf_tf],
            "bars": [[b.t, b.o, b.h, b.l, b.c, b.v] for b in bars],
            "zones": chart_zones,
            "selectedZone": selected_zone,
            "ltfZone": setup.get("ltfZone"),
            "location": setup.get("location"),
            "plan": setup.get("plan"),
            "price": _r(ctx.price),
            "currencySymbol": MARKETS[market]["currencySymbol"],
            "market": market,
        },
        "meta": ctx.meta_out(),
    }
    return res


# --------------------------------------------------------------------------- #
# CLI for local verification
# --------------------------------------------------------------------------- #


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ZoneDesk engine test")
    parser.add_argument("symbol")
    parser.add_argument("--market", default="NSE", choices=sorted(MARKETS))
    parser.add_argument("--type", default="DIT", choices=TRADE_ORDER)
    parser.add_argument("--all", action="store_true", help="scan all trade types")
    parser.add_argument("--min-score", type=float, default=4.0)
    parser.add_argument("--detail", action="store_true", help="include chart payload")
    args = parser.parse_args()

    sym = normalize_symbol(args.symbol, args.market)
    types = TRADE_ORDER if args.all else [args.type]
    result = analyze_symbol(sym, "", args.market, types, args.min_score)
    print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
    if args.detail:
        detail = get_detail(sym, "", args.market, args.type, args.min_score)
        print(json.dumps(detail["chart"], indent=2, ensure_ascii=True, default=str)[:4000])


if __name__ == "__main__":
    _cli()
