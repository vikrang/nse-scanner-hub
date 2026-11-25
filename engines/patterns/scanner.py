"""Multi-timeframe chart-pattern scan orchestration (stdlib only).

Ports the original app/patterns/__init__.py entry point to candle-dict lists
and wires it to the shared marketdata layer, mirroring the sibling engines'
build_scan / chart_payload conventions.
"""
from __future__ import annotations

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import marketdata
import universes

from .candles import detect_candles
from .classical import DETECTORS
from .geometry import (  # noqa: F401  (config re-exported for callers)
    CANDLE_PATTERN_MIN_CONFIDENCE,
    CATEGORY_LABELS,
    MAX_PATTERNS_PER_SYMBOL,
    TIMEFRAMES,
    build_context,
)

log = logging.getLogger(__name__)

# Daily first so the dashboard has meaningful breadth immediately.
DEFAULT_TIMEFRAMES = ["1d", "1h", "15m", "5m", "1w"]

# timeframe key -> marketdata interval
INTERVAL_BY_TF = {"5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d", "1w": "1wk"}


# --------------------------------------------------------------------------- #
# Detection entry point (port of the source patterns/__init__.py)
# --------------------------------------------------------------------------- #

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def pattern_id(symbol: str, timeframe: str, name: str, start_time: int) -> str:
    raw = f"{symbol}|{timeframe}|{name}|{start_time}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def detect_patterns(candles: list[dict], timeframe: str, symbol: str) -> list[dict]:
    """Run every detector for one symbol/timeframe and return ranked patterns."""
    if timeframe not in TIMEFRAMES or not candles:
        return []
    try:
        ctx = build_context(candles, timeframe, symbol)
    except Exception as exc:  # noqa: BLE001
        log.debug("Context failed for %s %s: %s", symbol, timeframe, exc)
        return []
    if ctx is None:
        return []

    found: list[dict] = []
    for detector in DETECTORS:
        try:
            found.extend(detector(ctx))
        except Exception as exc:  # noqa: BLE001
            log.debug("Detector %s failed on %s %s: %s", detector.__name__, symbol, timeframe, exc)
    try:
        found.extend(detect_candles(ctx))
    except Exception as exc:  # noqa: BLE001
        log.debug("Candles failed on %s %s: %s", symbol, timeframe, exc)

    # Keep the highest-confidence version of each pattern name.
    best_by_name: dict[str, dict] = {}
    for pattern in found:
        if not pattern or not isinstance(pattern, dict):
            continue
        name = pattern["name"]
        current = best_by_name.get(name)
        if current is None or pattern["confidence"] > current["confidence"]:
            best_by_name[name] = pattern

    cleaned = list(best_by_name.values())
    # The same structure often trips two detectors; keep the stronger read.
    for family in (
        ("Double Top", "Triple Top"),
        ("Double Bottom", "Triple Bottom"),
        ("Cup & Handle", "Rounding Bottom"),
    ):
        present = [p for p in cleaned if p["name"] in family]
        if len(present) > 1:
            keep = max(present, key=lambda p: p["confidence"])
            cleaned = [p for p in cleaned if p["name"] not in family or p is keep]

    ranked = sorted(
        cleaned,
        key=lambda p: (
            p["confidence"],
            p["status"] == "confirmed",
            p["end_time"],
        ),
        reverse=True,
    )[:MAX_PATTERNS_PER_SYMBOL]

    last_bar = int(candles[-1]["t"])
    last_price = round(float(candles[-1]["c"]), 2)
    for pattern in ranked:
        pattern["id"] = pattern_id(symbol, timeframe, pattern["name"], pattern["start_time"])
        pattern["slug"] = _slug(pattern["name"])
        pattern["symbol"] = symbol
        pattern["timeframe"] = timeframe
        pattern["last_bar_time"] = last_bar
        pattern["detected_at"] = pattern["end_time"]
        pattern["last_price"] = last_price
    return ranked


# --------------------------------------------------------------------------- #
# Symbol resolution
# --------------------------------------------------------------------------- #

def _yahoo_symbol(raw: str) -> str:
    """Bare NSE ticker -> full Yahoo symbol (passes through .NS / ^ / futures)."""
    sym = str(raw or "").strip().upper()
    if not sym or sym.endswith(".NS") or sym.startswith("^") or "=" in sym:
        return sym
    resolved = universes.resolve_symbol(sym)
    if resolved.endswith(".NS") or resolved.startswith("^") or "=" in resolved:
        return resolved
    return resolved + ".NS"


def _bare_symbol(raw: str) -> str:
    sym = str(raw or "").strip().upper()
    return sym[:-3] if sym.endswith(".NS") else sym


def _row_symbols(row: dict) -> tuple[str, str]:
    """Return (bare, yahoo) for a scan row that may carry either form."""
    raw = str(row.get("symbol") or "").strip().upper()
    yahoo = str(row.get("yahooSymbol") or "").strip().upper()
    if not yahoo:
        yahoo = _yahoo_symbol(raw)
    bare = _bare_symbol(raw) if raw else _bare_symbol(yahoo)
    return bare, yahoo


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #

def _scan_symbol(row: dict, timeframes: list[str]) -> tuple[list[dict], int]:
    bare, yahoo = _row_symbols(row)
    company = str(row.get("name") or "").strip()
    hits: list[dict] = []
    errors = 0
    for tf in timeframes:
        interval = INTERVAL_BY_TF.get(tf, "1d")
        try:
            data = marketdata.fetch(yahoo, interval)
            candles = data.get("candles") or []
            if not company:
                company = data.get("name") or bare
            patterns = detect_patterns(candles, tf, bare)
        except Exception as exc:  # noqa: BLE001
            log.debug("Pattern scan failed for %s %s: %s", yahoo, tf, exc)
            errors += 1
            continue
        for p in patterns:
            hit = dict(p)
            hit["symbol"] = bare
            hit["yahooSymbol"] = yahoo
            hit["name"] = company or bare          # company name for the table
            hit["pattern"] = p["name"]             # pattern label for the table
            hit["tf"] = tf
            hit["timeframe"] = tf
            hit["price"] = p.get("last_price")
            hits.append(hit)
    return hits, errors


def build_scan(rows, on_progress=None, timeframes=None) -> dict:
    """Scan rows x timeframes; return {"hits": [...], "stats": {...}}."""
    rows = list(rows or [])
    tfs = [tf for tf in (timeframes or DEFAULT_TIMEFRAMES) if tf in TIMEFRAMES]
    if not tfs:
        tfs = list(DEFAULT_TIMEFRAMES)
    hits: list[dict] = []
    errors = 0
    total = len(rows)
    done = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_scan_symbol, r, tfs): r for r in rows}
        for fut in as_completed(futures):
            try:
                sym_hits, sym_errors = fut.result()
                hits.extend(sym_hits)
                errors += sym_errors
            except Exception as exc:  # noqa: BLE001
                log.debug("Symbol worker failed: %s", exc)
                errors += 1
            done += 1
            if on_progress:
                try:
                    on_progress(done, total)
                except Exception:  # noqa: BLE001
                    pass
    hits.sort(key=lambda h: (-h["confidence"], h["status"] != "confirmed", -h["end_time"]))
    return {
        "hits": hits,
        "stats": {"symbols": total, "patterns": len(hits), "errors": errors},
    }


# --------------------------------------------------------------------------- #
# Drawer chart payload
# --------------------------------------------------------------------------- #

def chart_payload(symbol: str, tf: str = "1d", name: str = "") -> dict:
    """Candles + detected patterns for the drawer chart of one symbol/tf."""
    tf = tf if tf in TIMEFRAMES else "1d"
    yahoo = _yahoo_symbol(symbol)
    bare = _bare_symbol(symbol)
    data = marketdata.fetch(yahoo, INTERVAL_BY_TF.get(tf, "1d"))
    candles = data.get("candles") or []
    patterns = detect_patterns(candles, tf, bare)
    n = int(TIMEFRAMES[tf]["chart_bars"])
    sl = candles[-n:] if len(candles) > n else list(candles)
    price = data.get("price") or (candles[-1]["c"] if candles else 0.0)
    return {
        "symbol": bare,
        "yahooSymbol": yahoo,
        "name": data.get("name") or name or bare,
        "tf": tf,
        "timeframe": tf,
        "tf_label": TIMEFRAMES[tf]["label"],
        "price": round(float(price), 2),
        "candles": sl,
        "patterns": patterns,
    }