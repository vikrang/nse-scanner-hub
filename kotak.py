"""Optional Kotak Neo Trade API live-LTP feed (stdlib only).

Yahoo remains the base feed. When a user supplies a Kotak Neo Trade API
credential, this module refreshes last-traded prices for the visible rows.
The credential is used as the Authorization header exactly as Kotak's
/script-details/1.0 REST API expects. Nothing is persisted server-side; the
browser holds the credential and sends it per request.
"""
from __future__ import annotations

import csv
import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".cache"

KOTAK_BASE_URL = "https://mis.kotaksecurities.com"
KOTAK_MASTER_PATH = "/script-details/1.0/masterscrip/file-paths"
KOTAK_QUOTES_PATH = "/script-details/1.0/quotes/neosymbol/{symbols}/ltp"
QUOTE_BATCH_SIZE = 45
MAP_TTL = 86400
UA = "NSEScannerHub/1.0"

_lock = threading.Lock()
_map_cache: dict[str, str] = {}
_map_at = 0.0
_last_call = 0.0


class KotakError(Exception):
    pass


def _throttle() -> None:
    global _last_call
    wait = 0.25 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _token(value) -> str:
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value or "").strip()


def _get(url: str, key: str | None, timeout: float = 15.0) -> bytes:
    headers = {"User-Agent": UA, "Accept": "application/json" if key else "*/*"}
    if key:
        headers["Authorization"] = key
    _throttle()
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
            detail = str(body.get("message") or body.get("error") or body.get("description") or "").strip()
        except Exception:  # noqa: BLE001
            detail = ""
        if exc.code in (401, 403):
            raise KotakError("Kotak rejected the credential. Generate a fresh Neo Trade API token.") from exc
        raise KotakError(f"Kotak returned HTTP {exc.code}." + (f" {detail[:160]}" if detail else "")) from exc
    except urllib.error.URLError as exc:
        raise KotakError(f"Could not reach Kotak Securities: {exc.reason}") from exc
    except TimeoutError as exc:
        raise KotakError("Kotak Securities timed out.") from exc


def _get_json(url: str, key: str) -> dict:
    payload = json.loads(_get(url, key).decode("utf-8", "replace"))
    if not isinstance(payload, dict):
        raise KotakError("Kotak returned an unexpected response.")
    return payload


def _scrip_url(key: str) -> str:
    payload = _get_json(KOTAK_BASE_URL + KOTAK_MASTER_PATH, key)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    files = data.get("filesPaths") or data.get("files") or []
    candidates = [
        str(u) for u in files
        if "nse_cm" in str(u).lower() and str(u).lower().split("?")[0].endswith(".csv")
    ]
    if not candidates:
        raise KotakError("Kotak did not publish an NSE cash-market scrip file.")
    preferred = [u for u in candidates if "transformed-v1" in u.lower()]
    return (preferred or candidates)[0]


def _download_scrips(key: str) -> dict[str, str]:
    """Return {bare_symbol: token} for NSE equities from Kotak's master."""
    url = _scrip_url(key)
    raw = _get(url, None, timeout=30.0)  # public CDN rejects Authorization
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
    by_trade: dict[str, str] = {}
    for row in rows:
        if (row.get("pExchSeg") or "").strip().lower() != "nse_cm":
            continue
        group = (row.get("pGroup") or "").strip().upper()
        if group not in {"EQ", "BE", "BZ"}:
            continue
        token = _token(row.get("pSymbol"))
        trade = (row.get("pTrdSymbol") or "").strip().upper()
        if not token or not trade:
            continue
        for suffix in ("-EQ", "-BE", "-BZ"):
            if trade.endswith(suffix):
                trade = trade[: -len(suffix)]
                break
        # Prefer EQ over BE/BZ when a symbol appears in multiple groups.
        if trade not in by_trade or group == "EQ":
            by_trade[trade] = token
    if not by_trade:
        raise KotakError("Kotak's scrip file contained no NSE equities.")
    return by_trade


def _mapping(key: str) -> dict[str, str]:
    global _map_cache, _map_at
    with _lock:
        if _map_cache and time.time() - _map_at < MAP_TTL:
            return _map_cache
    fresh = _download_scrips(key)
    with _lock:
        _map_cache = fresh
        _map_at = time.time()
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "kotak_map.json").write_text(json.dumps(fresh), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return fresh


def _quotes(key: str, tokens: list[str]) -> list[dict]:
    out: list[dict] = []
    for start in range(0, len(tokens), QUOTE_BATCH_SIZE):
        batch = tokens[start : start + QUOTE_BATCH_SIZE]
        symbols = ",".join(f"nse_cm|{t}" for t in batch)
        encoded = urllib.parse.quote(symbols, safe="|,")
        url = KOTAK_BASE_URL + KOTAK_QUOTES_PATH.format(symbols=encoded)
        payload = json.loads(_get(url, key).decode("utf-8", "replace"))
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            items = payload["data"]
        elif isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            detail = str(payload.get("message") or payload.get("error") or "").strip()
            raise KotakError("Kotak rejected the quote request." + (f" {detail[:160]}" if detail else ""))
        else:
            raise KotakError("Kotak returned an unexpected response.")
        out.extend(item for item in items if isinstance(item, dict))
    return out


def _parse(raw: list[dict], mapping: dict[str, str]) -> dict[str, dict]:
    reverse = {token: symbol for symbol, token in mapping.items()}
    parsed: dict[str, dict] = {}
    for item in raw:
        token = _token(item.get("exchange_token") or item.get("tk") or item.get("instrument_token"))
        symbol = reverse.get(token)
        if not symbol:
            continue
        try:
            price = float(item.get("ltp") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        change_pct = None
        for field in ("per_change", "net_change_percentage", "change_pct"):
            try:
                if item.get(field) not in (None, ""):
                    change_pct = round(float(item[field]), 2)
                    break
            except (TypeError, ValueError):
                continue
        try:
            as_of = int(float(item.get("lstup_time") or time.time()))
        except (TypeError, ValueError):
            as_of = int(time.time())
        if as_of > 10**12:
            as_of //= 1000
        parsed[symbol] = {"price": round(price, 2), "change_pct": change_pct, "as_of": as_of, "source": "kotak"}
    return parsed


def live_quotes(key: str, symbols: list[str]) -> dict[str, dict]:
    """Fetch fresh LTPs for bare NSE symbols. Raises KotakError on failure."""
    key = (key or "").strip()
    if not key:
        raise KotakError("Enter your Kotak Neo Trade API credential first.")
    mapping = _mapping(key)
    wanted = {s: mapping[s] for s in symbols if s in mapping}
    if not wanted:
        return {}
    return _parse(_quotes(key, list(wanted.values())), wanted)


def test(key: str) -> dict:
    key = (key or "").strip()
    if not key:
        return {"ok": False, "error": "Enter your Kotak Neo Trade API credential first."}
    try:
        mapping = _mapping(key)
        probe = dict(list(mapping.items())[:5])
        quotes = _parse(_quotes(key, list(probe.values())), probe)
        sample = next(iter(quotes.items()), None)
        return {
            "ok": True,
            "matched": len(mapping),
            "sample": {"symbol": sample[0], "price": sample[1]["price"]} if sample else None,
        }
    except KotakError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Unexpected error: {exc}"}