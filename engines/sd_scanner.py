"""SD-Radar multi-timeframe supply/demand scan (reuses sd_engine verbatim)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import marketdata
from engines import sd_engine as engine
from engines.sd_engine import TF_ORDER

CHART_BARS = {"D": 200, "W": 160, "M": 90, "Q": 44}
SIGNAL_STATUSES = ("IN_ZONE", "APPROACHING", "WATCH")


def analyze_symbol(symbol: str, name: str = "", force: bool = False) -> dict:
    data = marketdata.fetch(symbol, "1d", force=force)
    candles = data["candles"]
    price = data["price"] if data["price"] and data["price"] > 0 else candles[-1]["c"]
    packs = {}
    for tf in TF_ORDER:
        cs = marketdata.resample(candles, tf)
        if len(cs) < 40:
            packs[tf] = {"candles": cs, "zones": [], "atr": 0.0}
            continue
        zones = engine.detect_zones(cs, tf)
        ev, atr_now = engine.evaluate_all(zones, cs, tf, price)
        packs[tf] = {"candles": cs, "zones": engine.dedupe(ev), "atr": atr_now}

    near = {"demand": set(), "supply": set()}
    for tf, pack in packs.items():
        for z in pack["zones"]:
            if z["status"] in SIGNAL_STATUSES:
                near[z["type"]].add(tf)

    signals, tf_summary = [], {}
    for tf in TF_ORDER:
        pack = packs[tf]
        cfg_w = engine.TF_CONFIG[tf]["weight"]
        near_zones = [z for z in pack["zones"] if z["status"] in SIGNAL_STATUSES]
        near_zones.sort(key=engine.sort_key)
        per_side = {"demand": 0, "supply": 0}
        counted = {"approaching": 0, "in_zone": 0, "watch": 0, "fresh": 0, "best": 0}
        emitted = 0
        for z in near_zones:
            side = z["type"]
            counted[{"APPROACHING": "approaching", "IN_ZONE": "in_zone", "WATCH": "watch"}[z["status"]]] += 1
            if z["fresh"]:
                counted["fresh"] += 1
            counted["best"] = max(counted["best"], z["heat"])
            if per_side[side] >= 4 or emitted >= 6:
                continue
            per_side[side] += 1
            emitted += 1
            conf_tfs = sorted(near[side], key=TF_ORDER.index)
            sig = {
                "symbol": data["symbol"] or symbol, "name": data["name"] or name or symbol,
                "currency": data["currency"], "price": round(price, 6),
                "tf": tf, "tf_label": engine.TF_CONFIG[tf]["label"], "type": side,
                "kind": z["kind"], "status": z["status"], "distance_pct": z["distance_pct"],
                "threshold_pct": z["threshold_pct"], "proximal": round(z["proximal"], 6),
                "distal": round(z["distal"], 6), "mid": round(z["mid"], 6), "heat": z["heat"],
                "quality": z["quality"], "proximity": z["proximity"], "inst": z["inst"],
                "clean": z["clean"], "touches": z["touches"], "fresh": z["fresh"],
                "fill_pct": z["fill_pct"], "age_bars": z["age_bars"], "impulse": round(z["impulse"], 2),
                "base_bars": z["base_bars"], "height_pct": z["height_pct"], "created": z["created"],
                "heading": z["heading"], "aligned": z["aligned"], "eta_bars": z["eta_bars"],
                "eta_text": z["eta_text"], "entry": round(z["entry"], 6), "stop": round(z["stop"], 6),
                "target": round(z["target"], 6), "rr": z["rr"], "risk_pct": z["risk_pct"],
                "confluence": len(conf_tfs), "conf_tfs": conf_tfs,
                "spark": [round(c["c"], 6) for c in pack["candles"][-28:]],
            }
            bonus = 12 if z["status"] == "APPROACHING" else 8 if z["status"] == "IN_ZONE" else 0
            conviction = 0.55 * z["heat"] + 0.45 * z["inst"]
            sig["priority"] = round(conviction * cfg_w * (1 + 0.07 * len(conf_tfs)) + bonus, 2)
            signals.append(sig)
        tf_summary[tf] = counted

    summary = {
        "symbol": data["symbol"] or symbol, "name": data["name"] or name or symbol,
        "price": round(price, 6), "currency": data["currency"], "exchange": data["exchange"],
        "tfs": tf_summary, "confluence": max(len(near["demand"]), len(near["supply"])),
        "confluence_side": "demand" if len(near["demand"]) >= len(near["supply"]) else "supply",
        "error": None,
    }
    return {"summary": summary, "signals": signals}


def build_scan(rows, on_progress=None, force=False):
    signals, out_rows, errors = [], [], 0
    total = len(rows)
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(analyze_symbol, r["symbol"], r.get("name", ""), force): r for r in rows}
        for fut in as_completed(futures):
            r = futures[fut]
            try:
                res = fut.result()
                out_rows.append(res["summary"])
                signals.extend(res["signals"])
            except Exception as exc:  # noqa: BLE001
                errors += 1
                out_rows.append({"symbol": r["symbol"], "name": r.get("name", r["symbol"]),
                                 "price": 0, "currency": "", "tfs": {}, "confluence": 0,
                                 "confluence_side": "demand", "error": str(exc)[:180]})
            done += 1
            if on_progress:
                on_progress(done, total)
    signals.sort(key=lambda s: -s["priority"])
    out_rows.sort(key=lambda r: (r["error"] is not None, -r["confluence"], r["symbol"]))

    def count(status):
        return sum(1 for s in signals if s["status"] == status)

    stats = {
        "symbols": total, "signals": len(signals), "approaching": count("APPROACHING"),
        "in_zone": count("IN_ZONE"), "watch": count("WATCH"),
        "fresh": sum(1 for s in signals if s["fresh"]),
        "demand": sum(1 for s in signals if s["type"] == "demand"),
        "supply": sum(1 for s in signals if s["type"] == "supply"),
        "confluence": sum(1 for r in out_rows if r["confluence"] >= 2), "errors": errors,
    }
    return {"signals": signals, "symbols": out_rows, "stats": stats}


def chart_payload(symbol: str, tf: str = "D", name: str = "") -> dict:
    data = marketdata.fetch(symbol, "1d")
    tf = tf if tf in TF_ORDER else "D"
    cs = marketdata.resample(data["candles"], tf)
    price = data["price"] if data["price"] and data["price"] > 0 else cs[-1]["c"]
    zones = []
    if len(cs) >= 40:
        ev, _ = engine.evaluate_all(engine.detect_zones(cs, tf), cs, tf, price)
        ev = engine.dedupe(ev)
        n = CHART_BARS[tf]
        sl = cs[-n:] if len(cs) > n else list(cs)
        offset = len(cs) - len(sl)
        for z in ev:
            if z["broken_i"] is not None and z["broken_i"] < offset:
                continue
            if z["status"] == "BROKEN" and z["created_i"] < offset:
                continue
            zones.append({
                "type": z["type"], "kind": z["kind"], "proximal": round(z["proximal"], 6),
                "distal": round(z["distal"], 6), "heat": z["heat"], "quality": z["quality"],
                "inst": z["inst"], "clean": z["clean"], "base_bars": z["base_bars"],
                "status": z["status"], "touches": z["touches"], "created": z["created"],
                "age_bars": z["age_bars"], "impulse": round(z["impulse"], 2),
                "distance_pct": z["distance_pct"], "eta_text": z["eta_text"], "rr": z.get("rr"),
                "x0": max(0, z["created_i"] - offset), "x1": max(0, min(len(sl) - 1, z["depart_i"] - offset)),
                "broken": z["status"] == "BROKEN",
            })
    else:
        sl = list(cs)
    zones.sort(key=lambda z: (z["broken"], z["type"] != "demand"))
    return {
        "symbol": data["symbol"] or symbol, "name": data["name"] or name or symbol,
        "currency": data["currency"], "tf": tf, "tf_label": engine.TF_CONFIG[tf]["label"],
        "price": round(price, 6),
        "candles": [[c["t"], round(c["o"], 6), round(c["h"], 6), round(c["l"], 6), round(c["c"], 6)] for c in sl],
        "zones": zones,
    }