"""Symbol universes: NSE 500 and Nifty 50, plus custom watchlists.

Bundled lists ship with the app so it works offline-of-NSE (Yahoo still needs
internet). Bare NSE tickers map to Yahoo's `.NS` suffix.
"""
from __future__ import annotations

import csv
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


def _load_nse500():
    path = os.path.join(DATA_DIR, "nse500.json")
    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)
    out = []
    for it in items:
        sym = str(it.get("symbol") or "").strip().upper()
        if sym:
            out.append({"symbol": sym, "name": it.get("name") or sym,
                        "industry": it.get("industry") or ""})
    return out


def _load_nifty50():
    path = os.path.join(DATA_DIR, "nifty50.csv")
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            sym = (row.get("symbol") or "").strip().upper()
            if sym:
                out.append({"symbol": sym + ".NS", "name": (row.get("company") or sym).strip(),
                            "industry": (row.get("industry") or "").strip()})
    return out


NSE500 = _load_nse500()
NIFTY50 = _load_nifty50()
NIFTY50_SET = {r["symbol"] for r in NIFTY50}

# A liquid subset used as the fast default for heavy multi-timeframe scans.
NIFTY100 = NIFTY50 + [r for r in NSE500 if r["symbol"] not in NIFTY50_SET][:50]

UNIVERSES = {
    "nifty50": {"label": "Nifty 50", "rows": NIFTY50},
    "nifty100": {"label": "Nifty 100 (liquid)", "rows": NIFTY100},
    "nse500": {"label": "NSE 500", "rows": NSE500},
}

_NAMES = {r["symbol"]: r["name"] for r in NSE500}
_NAMES.update({r["symbol"]: r["name"] for r in NIFTY50})
_INDUSTRY = {r["symbol"]: r["industry"] for r in NSE500}
_BASE = {}
for _r in NSE500 + NIFTY50:
    _BASE.setdefault(_r["symbol"].split(".")[0], _r["symbol"])

INDICES = {
    "^NSEI": "NIFTY 50",
    "^NSEBANK": "BANK NIFTY",
    "^BSESN": "SENSEX",
    "^CNXIT": "NIFTY IT",
}


def resolve_symbol(raw: str) -> str:
    sym = str(raw or "").strip().upper()
    if not sym or sym.endswith(".NS") or sym.startswith("^") or "=" in sym or "-" in sym:
        return sym
    return _BASE.get(sym, sym + ".NS")


def name_of(symbol: str) -> str:
    return _NAMES.get(symbol, symbol.replace(".NS", ""))


def industry_of(symbol: str) -> str:
    return _INDUSTRY.get(symbol, "")


def get_universe(key: str):
    key = (key or "nifty50").lower()
    if key in UNIVERSES:
        return UNIVERSES[key]["rows"]
    return NIFTY50


def parse_custom(text: str):
    rows, seen = [], set()
    for chunk in str(text or "").replace("\n", ",").replace(";", ",").split(","):
        sym = resolve_symbol(chunk)
        if sym and sym not in seen:
            seen.add(sym)
            rows.append({"symbol": sym, "name": name_of(sym), "industry": industry_of(sym)})
    return rows