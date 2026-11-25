"""Shared Yahoo Finance OHLCV access (stdlib only).

One cache for every tool: daily history resampled to W/M/Q, plus intraday
intervals for patterns and volume delta. No API key. Quotes are ~15 min delayed.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, ".cache")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# interval -> (default range, cache TTL seconds)
INTERVALS = {
    "1m": ("1d", 60),
    "5m": ("5d", 120),
    "15m": ("60d", 300),
    "1h": ("365d", 600),
    "1d": ("20y", 600),
    "1wk": ("10y", 1800),
}
FULL_TTL = 86400

_lock = threading.Lock()
_mem: dict[str, dict] = {}
_sem = threading.Semaphore(8)


def _cache_path(symbol: str, interval: str, rng: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{symbol}_{interval}_{rng}".upper())
    return os.path.join(CACHE_DIR, safe + ".json")


def _retryable(exc) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 425, 429, 500, 502, 503, 504)
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))


def _http_json(url: str, timeout: int = 25):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with _sem:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 404):
                break
            if attempt >= 2 or not _retryable(exc):
                break
            time.sleep(0.6 * (attempt + 1))
    raise last


def _parse(payload: dict, interval: str) -> dict:
    res = payload["chart"]["result"][0]
    meta = res.get("meta", {})
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    o, h, l, c = q.get("open", []), q.get("high", []), q.get("low", []), q.get("close", [])
    v = q.get("volume") or [0] * len(ts)
    candles = []
    for i in range(len(ts)):
        try:
            oo, hh, ll, cc = o[i], h[i], l[i], c[i]
        except IndexError:
            continue
        if None in (oo, hh, ll, cc) or min(oo, hh, ll, cc) <= 0:
            continue
        candles.append({"t": int(ts[i]), "o": float(oo), "h": float(hh),
                        "l": float(ll), "c": float(cc), "v": float(v[i] or 0)})
    seen, clean = set(), []
    for k in candles:
        if k["t"] in seen:
            continue
        seen.add(k["t"])
        clean.append(k)
    clean.sort(key=lambda x: x["t"])
    price = meta.get("regularMarketPrice") or (clean[-1]["c"] if clean else 0.0)
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    # For daily bars, append a synthetic live bar when the exchange price is newer.
    if interval == "1d":
        rt = meta.get("regularMarketTime")
        if clean and rt and price:
            try:
                rt_day = _dt.datetime.fromtimestamp(int(rt), _dt.UTC).date()
                last_day = _dt.datetime.fromtimestamp(clean[-1]["t"], _dt.UTC).date()
            except (TypeError, ValueError, OSError):
                rt_day = last_day = None
            if rt_day and rt_day > last_day and (rt_day - last_day).days <= 3:
                t = clean[-1]["t"] + 86400
                if _dt.datetime.fromtimestamp(t, _dt.UTC).date() == rt_day:
                    op = clean[-1]["c"]
                    clean.append({"t": t, "o": op, "h": max(op, float(price)),
                                  "l": min(op, float(price)), "c": float(price), "v": 0.0})
    return {
        "symbol": meta.get("symbol") or "",
        "name": meta.get("shortName") or meta.get("longName") or "",
        "currency": meta.get("currency") or "",
        "exchange": meta.get("fullExchangeName") or "",
        "price": float(price or 0.0),
        "prev_close": float(prev_close) if prev_close else 0.0,
        "candles": clean,
    }


def _download(symbol: str, interval: str, rng: str, min_bars: int = 1) -> dict:
    sym = urllib.parse.quote(symbol, safe="")
    errors = []
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?interval={interval}&range={rng}&includePrePost=false&events=div%2Csplit")
        try:
            data = _parse(_http_json(url), interval)
            if len(data["candles"]) < min_bars:
                raise ValueError(f"only {len(data['candles'])} bars")
            return data
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{host}/{rng}: {exc}")
            time.sleep(0.3)
    raise RuntimeError("; ".join(errors[-2:]))


def _read_cache(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("candles") else None
    except (OSError, ValueError):
        return None


def _write_cache(path: str, data: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass


def _merge(cached, fresh):
    by_t = {c["t"]: c for c in cached}
    for c in fresh:
        by_t[c["t"]] = c
    return [by_t[t] for t in sorted(by_t)]


def fetch(symbol: str, interval: str = "1d", rng: str | None = None, force: bool = False) -> dict:
    """Fetch OHLCV for a Yahoo symbol with memory + disk caching."""
    interval = interval if interval in INTERVALS else "1d"
    def_rng, ttl = INTERVALS[interval]
    rng = rng or def_rng
    key = f"{symbol}|{interval}|{rng}".upper()
    now = time.time()

    with _lock:
        cached = _mem.get(key)
    if cached and not force and now - cached.get("fetched", 0) < ttl:
        return cached

    path = _cache_path(symbol, interval, rng)
    disk = _read_cache(path)
    if disk and not force and now - disk.get("fetched", 0) < ttl:
        with _lock:
            _mem[key] = disk
        return disk

    # Daily: refresh only recent bars when full history is still fresh.
    if interval == "1d" and disk and not force:
        full_at = disk.get("full_at") or 0
        if full_at and now - full_at < FULL_TTL:
            try:
                fresh = _download(symbol, "1d", "5d")
                disk.update({
                    "symbol": fresh["symbol"] or disk.get("symbol") or symbol,
                    "name": fresh["name"] or disk.get("name") or symbol,
                    "price": fresh["price"] or disk.get("price") or 0.0,
                    "prev_close": fresh["prev_close"] or disk.get("prev_close") or 0.0,
                    "candles": _merge(disk["candles"], fresh["candles"]),
                    "fetched": now,
                })
                _write_cache(path, disk)
                with _lock:
                    _mem[key] = disk
                return disk
            except Exception:  # noqa: BLE001
                pass

    if interval == "1d":
        data = None
        for r in (rng, "10y", "5y"):
            try:
                data = _download(symbol, "1d", r, min_bars=40)
                break
            except Exception:  # noqa: BLE001
                continue
        if data is None:
            raise RuntimeError(f"{symbol}: no daily history")
        data["full_at"] = now
    else:
        data = _download(symbol, interval, rng)
    data["fetched"] = now
    if not data["name"]:
        data["name"] = symbol
    _write_cache(path, data)
    with _lock:
        _mem[key] = data
    return data


def resample(candles: list, tf: str) -> list:
    """Daily candles -> W / M / Q buckets (UTC calendar)."""
    if tf == "D" or len(candles) < 2:
        return candles

    def bucket(t):
        d = _dt.datetime.fromtimestamp(t, _dt.UTC).date()
        if tf == "W":
            iso = d.isocalendar()
            return (iso[0], iso[1])
        if tf == "M":
            return (d.year, d.month)
        return (d.year, (d.month - 1) // 3)

    out, cur, key = [], None, None
    for c in candles:
        k = bucket(c["t"])
        if k != key:
            if cur:
                out.append(cur)
            key = k
            cur = {"t": c["t"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"]}
        else:
            cur["h"] = max(cur["h"], c["h"])
            cur["l"] = min(cur["l"], c["l"])
            cur["c"] = c["c"]
            cur["v"] += c["v"]
    if cur:
        out.append(cur)
    return out


def slice_candles(candles: list, n: int) -> list:
    return candles[-n:] if len(candles) > n else list(candles)