"""Supply / demand zone detection.

Three zone families, all treated as supply/demand:
  BASE - tight 2-6 candle base + impulsive departure (RBR / RBD / DBR / DBD)
  FVG  - 3-candle fair value gap (imbalance) with a displacement middle candle
  OB   - order block: last opposite-colour candle before a displacement leg

Geometry is normalised so "proximal" is always the edge price reaches first:
  demand: proximal = upper edge, distal = lower edge (invalidation)
  supply: proximal = lower edge, distal = upper edge (invalidation)
"""
from __future__ import annotations

import datetime as _dt
import math

TF_CONFIG = {
    "D": dict(label="Daily", atr_period=14, min_base=2, max_base=6, leg_window=3,
              impulse_atr=1.35, base_candle_atr=0.85, base_total_atr=2.00,
              ob_body_atr=0.90, fvg_gap_atr=0.15, fvg_body_atr=0.60,
              horizon=260, lookback=520, near_atr=1.30, watch_cap=8.0, max_width=7.0,
              days_per_bar=1.45, weight=1.00),
    "W": dict(label="Weekly", atr_period=14, min_base=2, max_base=5, leg_window=3,
              impulse_atr=1.25, base_candle_atr=0.85, base_total_atr=2.00,
              ob_body_atr=0.85, fvg_gap_atr=0.15, fvg_body_atr=0.55,
              horizon=200, lookback=300, near_atr=1.20, watch_cap=12.0, max_width=12.0,
              days_per_bar=7.0, weight=1.08),
    "M": dict(label="Monthly", atr_period=14, min_base=1, max_base=4, leg_window=2,
              impulse_atr=1.20, base_candle_atr=0.90, base_total_atr=2.10,
              ob_body_atr=0.85, fvg_gap_atr=0.12, fvg_body_atr=0.50,
              horizon=96, lookback=150, near_atr=1.10, watch_cap=16.0, max_width=18.0,
              days_per_bar=30.4, weight=1.16),
    "Q": dict(label="Quarterly", atr_period=12, min_base=1, max_base=3, leg_window=2,
              impulse_atr=1.10, base_candle_atr=0.95, base_total_atr=2.20,
              ob_body_atr=0.80, fvg_gap_atr=0.10, fvg_body_atr=0.45,
              horizon=56, lookback=90, near_atr=1.00, watch_cap=22.0, max_width=26.0,
              days_per_bar=91.3, weight=1.26),
}

TF_ORDER = ("D", "W", "M", "Q")
STATUS_RANK = {"IN_ZONE": 0, "APPROACHING": 1, "WATCH": 2, "AWAY": 3, "BROKEN": 4}


def atr_series(candles, period=14):
    trs, prev = [], None
    for c in candles:
        h, l, cl = c["h"], c["l"], c["c"]
        tr = h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev))
        trs.append(tr)
        prev = cl
    out, run = [0.0] * len(candles), 0.0
    for i, tr in enumerate(trs):
        run += tr
        if i >= period:
            run -= trs[i - period]
        out[i] = run / min(i + 1, period)
    return out


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _date_str(ts):
    return _dt.datetime.fromtimestamp(ts, _dt.UTC).strftime("%Y-%m-%d")


def _walk_forward(candles, ztype, prox, dist, from_i):
    """Track touches, deepest penetration and invalidation after a zone forms."""
    height = abs(prox - dist)
    if height <= 0:
        height = 1e-9
    touches, in_zone, broken_i, pen = 0, False, None, 0.0
    for j in range(from_i, len(candles)):
        c = candles[j]
        if ztype == "demand":
            pen = max(pen, (prox - c["l"]) / height)
            if c["c"] < dist:
                broken_i = j
                break
            if c["l"] <= prox:
                if not in_zone:
                    touches += 1
                    in_zone = True
            else:
                in_zone = False
        else:
            pen = max(pen, (c["h"] - prox) / height)
            if c["c"] > dist:
                broken_i = j
                break
            if c["h"] >= prox:
                if not in_zone:
                    touches += 1
                    in_zone = True
            else:
                in_zone = False
    return touches, _clamp(pen, 0.0, 1.0), broken_i


def _make(candles, ztype, kind, prox, dist, created_i, depart_i, impulse, tightness, atr,
          max_width_pct=100.0, base_bars=1):
    height = abs(prox - dist)
    if height <= 0:
        return None
    last_close = candles[-1]["c"]
    if last_close > 0 and height / last_close * 100.0 > max_width_pct:
        return None
    touches, fill, broken_i = _walk_forward(candles, ztype, prox, dist, depart_i + 1)
    n = len(candles)
    return {
        "type": ztype,
        "kind": kind,
        "proximal": prox,
        "distal": dist,
        "created_i": created_i,
        "depart_i": depart_i,
        "created_t": candles[created_i]["t"],
        "created": _date_str(candles[created_i]["t"]),
        "base_bars": base_bars,
        "base_height": height,
        "tightness": _clamp(tightness, 0.0, 1.0),
        "impulse": impulse,
        "touches": touches,
        "fill": fill,
        "broken_i": broken_i,
        "age_bars": n - 1 - depart_i,
        "atr": atr,
    }


# --------------------------------------------------------------------------- #
# family 1: base + impulse
# --------------------------------------------------------------------------- #
def _detect_base(candles, cfg, atr, look_from):
    n = len(candles)
    zones = []
    i = look_from
    while i < n - cfg["leg_window"] - 1:
        a = atr[i]
        if a <= 0 or (candles[i]["h"] - candles[i]["l"]) > cfg["base_candle_atr"] * a:
            i += 1
            continue
        s = i
        run_hi, run_lo = candles[i]["h"], candles[i]["l"]
        e = i
        while e + 1 < n - cfg["leg_window"] - 1:
            nx = candles[e + 1]
            nhi, nlo = max(run_hi, nx["h"]), min(run_lo, nx["l"])
            if (nx["h"] - nx["l"]) <= cfg["base_candle_atr"] * a \
                    and (nhi - nlo) <= cfg["base_total_atr"] * a \
                    and (e + 2 - s) <= cfg["max_base"]:
                e += 1
                run_hi, run_lo = nhi, nlo
            else:
                break
        base_bars = e - s + 1
        if base_bars >= cfg["min_base"]:
            leg_end = e + cfg["leg_window"]
            base_hi = max(c["h"] for c in candles[s:e + 1])
            base_lo = min(c["l"] for c in candles[s:e + 1])
            leg = candles[e + 1:leg_end + 1]
            up_ext = max(c["h"] for c in leg) - base_hi
            dn_ext = base_lo - min(c["l"] for c in leg)
            leg_close_max = max(c["c"] for c in leg)
            leg_close_min = min(c["c"] for c in leg)
            need = cfg["impulse_atr"] * a
            height = max(base_hi - base_lo, 1e-9)
            tight = 1 - height / (cfg["base_total_atr"] * a)
            if up_ext >= need and up_ext >= dn_ext and leg_close_max > base_hi:
                z = _make(candles, "demand", "base", base_hi, base_lo, s, leg_end, up_ext / a,
                          tight, a, cfg["max_width"], base_bars=base_bars)
                if z:
                    zones.append(z)
            elif dn_ext >= need and dn_ext > up_ext and leg_close_min < base_lo:
                z = _make(candles, "supply", "base", base_lo, base_hi, s, leg_end, dn_ext / a,
                          tight, a, cfg["max_width"], base_bars=base_bars)
                if z:
                    zones.append(z)
        i = e + 1
    return zones


# --------------------------------------------------------------------------- #
# family 2: fair value gap / imbalance
# --------------------------------------------------------------------------- #
def _detect_fvg(candles, cfg, atr, look_from):
    n = len(candles)
    zones = []
    for i in range(max(look_from, 2), n):
        a = atr[i - 1]
        if a <= 0:
            continue
        mid = candles[i - 1]
        change = mid["c"] - candles[i - 2]["c"]  # body is unreliable on some feeds (FX)
        if candles[i]["l"] > candles[i - 2]["h"]:
            gap = candles[i]["l"] - candles[i - 2]["h"]
            if gap >= cfg["fvg_gap_atr"] * a and change >= cfg["fvg_body_atr"] * a:
                prox, dist = candles[i]["l"], candles[i - 2]["h"]
                imp = (candles[i]["c"] - dist) / a
                z = _make(candles, "demand", "fvg", prox, dist, i - 2, i - 1, imp,
                          1 - gap / (2.2 * a), a, cfg["max_width"], base_bars=3)
                if z:
                    zones.append(z)
        elif candles[i]["h"] < candles[i - 2]["l"]:
            gap = candles[i - 2]["l"] - candles[i]["h"]
            if gap >= cfg["fvg_gap_atr"] * a and -change >= cfg["fvg_body_atr"] * a:
                prox, dist = candles[i]["h"], candles[i - 2]["l"]
                imp = (dist - candles[i]["c"]) / a
                z = _make(candles, "supply", "fvg", prox, dist, i - 2, i - 1, imp,
                          1 - gap / (2.2 * a), a, cfg["max_width"], base_bars=3)
                if z:
                    zones.append(z)
    return zones


# --------------------------------------------------------------------------- #
# family 3: order block (last opposite candle before displacement)
# --------------------------------------------------------------------------- #
def _detect_ob(candles, cfg, atr, look_from):
    n = len(candles)
    zones = []
    for i in range(max(look_from, 2), n):
        c = candles[i]
        a = atr[i]
        if a <= 0:
            continue
        prev_close = candles[i - 1]["c"]
        change = c["c"] - prev_close
        rng = max(c["h"] - c["l"], 1e-9)
        if change >= cfg["ob_body_atr"] * a and c["c"] > c["h"] - 0.25 * rng:
            j = i - 1
            o = candles[j]
            if o["c"] < candles[j - 1]["c"] and c["c"] > o["h"]:
                z = _make(candles, "demand", "ob", o["h"], o["l"], j, i,
                          (c["c"] - o["h"]) / a, 1 - (o["h"] - o["l"]) / (1.8 * a), a,
                          cfg["max_width"], base_bars=1)
                if z:
                    zones.append(z)
        elif -change >= cfg["ob_body_atr"] * a and c["c"] < c["l"] + 0.25 * rng:
            j = i - 1
            o = candles[j]
            if o["c"] > candles[j - 1]["c"] and c["c"] < o["l"]:
                z = _make(candles, "supply", "ob", o["l"], o["h"], j, i,
                          (o["l"] - c["c"]) / a, 1 - (o["h"] - o["l"]) / (1.8 * a), a,
                          cfg["max_width"], base_bars=1)
                if z:
                    zones.append(z)
    return zones


def detect_zones(candles, tf):
    cfg = TF_CONFIG[tf]
    n = len(candles)
    if n < 40:
        return []
    atr = atr_series(candles, cfg["atr_period"])
    look_from = max(cfg["atr_period"] + 2, n - cfg["lookback"])
    zones = _detect_base(candles, cfg, atr, look_from)
    zones += _detect_fvg(candles, cfg, atr, look_from)
    zones += _detect_ob(candles, cfg, atr, look_from)
    return zones


# --------------------------------------------------------------------------- #
# evaluation against live price
# --------------------------------------------------------------------------- #
def evaluate(z, candles, tf, price, atr_now):
    cfg = TF_CONFIG[tf]
    prox, dist = z["proximal"], z["distal"]
    atr_pct = (atr_now / price * 100.0) if price > 0 else 0.0
    near_pct = _clamp(cfg["near_atr"] * atr_pct, 0.30, cfg["watch_cap"] / 2.0)
    watch_pct = min(2.0 * near_pct, cfg["watch_cap"])

    if z["type"] == "demand":
        if price < dist - 0.25 * atr_now:
            status, distance = "BROKEN", 0.0
        elif price <= prox:
            status, distance = "IN_ZONE", 0.0
        else:
            distance = (price - prox) / price * 100.0
            status = "APPROACHING" if distance <= near_pct else "WATCH" if distance <= watch_pct else "AWAY"
    else:
        if price > dist + 0.25 * atr_now:
            status, distance = "BROKEN", 0.0
        elif price >= prox:
            status, distance = "IN_ZONE", 0.0
        else:
            distance = (prox - price) / price * 100.0
            status = "APPROACHING" if distance <= near_pct else "WATCH" if distance <= watch_pct else "AWAY"

    if z["broken_i"] is not None or z["fill"] > 0.90:
        status = "BROKEN"

    mom = 0.0
    look = 4
    if len(candles) > look and atr_now > 0:
        mom = (candles[-1]["c"] - candles[-1 - look]["c"]) / (atr_now * look)
    heading = "falling" if mom < -0.12 else "rising" if mom > 0.12 else "flat"
    aligned = (heading == "falling") if z["type"] == "demand" else (heading == "rising")

    # institutional footprint: short tight base + clean (untested) supply +
    # an outsized displacement leg. 1.2x ATR scores 0, 4.2x ATR or more scores 1.
    leg_n = _clamp((z["impulse"] - 1.2) / 3.0, 0, 1)
    if z["kind"] == "base":
        base_n = {1: 1.0, 2: 1.0, 3: 0.80, 4: 0.50, 5: 0.25, 6: 0.10}.get(z["base_bars"], 0.05)
    else:
        base_n = 0.45
    kind_w = {"base": 1.0, "fvg": 0.78, "ob": 0.62}.get(z["kind"], 0.7)
    tight_n = _clamp(z["tightness"], 0, 1)
    fresh_n = (1.0, 0.72, 0.50, 0.32)[min(z["touches"], 3)] * (1 - 0.55 * z["fill"])
    clean_n = (1.0, 0.65, 0.38, 0.18)[min(z["touches"], 3)] * (1 - 0.90 * z["fill"])
    age_n = _clamp(1 - z["age_bars"] / cfg["horizon"], 0, 1)
    quality = (34 * leg_n + 12 * base_n + 12 * tight_n + 22 * fresh_n
               + 12 * age_n + 8 * kind_w)
    inst = 100.0 * (0.36 * leg_n + 0.34 * base_n + 0.22 * clean_n + 0.08 * age_n)

    proximity = _clamp(1 - distance / watch_pct, 0, 1) * 100.0
    heat = 0.35 * quality + 0.40 * proximity + 0.25 * inst
    if status == "IN_ZONE":
        heat += 6
    if aligned and status in ("APPROACHING", "IN_ZONE"):
        heat += 4
    heat = round(_clamp(heat, 0, 100))

    if distance > 0 and atr_now > 0:
        eta_bars = max(1, math.ceil((distance / 100.0 * price) / (atr_now * 0.9)))
    else:
        eta_bars = 0
    eta_days = eta_bars * cfg["days_per_bar"]
    if eta_days < 1.5:
        eta_text = "~1 day"
    elif eta_days < 11:
        eta_text = f"~{round(eta_days)} days"
    elif eta_days < 50:
        eta_text = f"~{max(1, round(eta_days / 7))} wks"
    else:
        eta_text = f"~{max(1, round(eta_days / 30.4))} mo"

    out = dict(z)
    out.update({
        "tf": tf,
        "tf_label": cfg["label"],
        "status": status,
        "distance_pct": round(distance, 2),
        "threshold_pct": round(near_pct, 2),
        "watch_pct": round(watch_pct, 2),
        "quality": round(quality, 1),
        "proximity": round(proximity, 1),
        "heat": heat,
        "inst": round(inst),
        "clean": bool(z["touches"] == 0 and z["fill"] < 0.10
                      and (z["kind"] != "base" or z["base_bars"] <= 3)),
        "heading": heading,
        "aligned": bool(aligned and status in ("APPROACHING", "IN_ZONE")),
        "eta_bars": eta_bars,
        "eta_text": eta_text if distance > 0 else "at zone",
        "mid": (prox + dist) / 2.0,
        "height_pct": round(abs(prox - dist) / price * 100.0, 2),
        "fresh": z["touches"] == 0 and z["fill"] < 0.20,
        "fill_pct": round(z["fill"] * 100.0),
    })
    return out


def plan(z, price, atr_now):
    """Entry at the proximal edge for a pending retest, or at market when price is
    already inside the zone. Stop beyond the distal edge, target at the nearest
    opposing active zone, else 2.5R."""
    prox, dist, t = z["proximal"], z["distal"], z["type"]
    buf = max(0.25 * atr_now, 0.002 * price)
    inside = z.get("status") == "IN_ZONE"
    if t == "demand":
        entry = price if inside else prox
        stop = dist - buf
        risk = max(entry - stop, 0.30 * atr_now, 1e-9)
        target = entry + 2.5 * risk
        for o in z.get("_opposite", []):
            if max(entry + 1.2 * risk, entry * 1.002) <= o["proximal"] < target * 1.15:
                target = o["proximal"]
                break
    else:
        entry = price if inside else prox
        stop = dist + buf
        risk = max(stop - entry, 0.30 * atr_now, 1e-9)
        target = entry - 2.5 * risk
        for o in z.get("_opposite", []):
            if target * 1.15 < o["proximal"] <= min(entry - 1.2 * risk, entry * 0.998):
                target = o["proximal"]
                break
    rr = abs(target - entry) / risk
    z["entry"], z["stop"], z["target"] = entry, stop, target
    z["rr"] = round(rr, 2)
    z["risk_pct"] = round(risk / entry * 100.0, 2)
    return z


def evaluate_all(zones, candles, tf, price):
    atr = atr_series(candles, TF_CONFIG[tf]["atr_period"])
    atr_now = atr[-1] if atr else 0.0
    ev = [evaluate(z, candles, tf, price, atr_now) for z in zones]
    demand = sorted((z for z in ev if z["type"] == "demand" and z["status"] != "BROKEN"),
                    key=lambda z: -z["proximal"])
    supply = sorted((z for z in ev if z["type"] == "supply" and z["status"] != "BROKEN"),
                    key=lambda z: z["proximal"])
    for z in ev:
        z["_opposite"] = supply if z["type"] == "demand" else demand
        if z["status"] != "BROKEN":
            plan(z, price, atr_now)
        z.pop("_opposite", None)
    return ev, atr_now


def _overlap(a, b):
    lo = max(min(a["proximal"], a["distal"]), min(b["proximal"], b["distal"]))
    hi = min(max(a["proximal"], a["distal"]), max(b["proximal"], b["distal"]))
    if hi <= lo:
        return 0.0
    small = min(abs(a["proximal"] - a["distal"]), abs(b["proximal"] - b["distal"]))
    return (hi - lo) / small if small > 0 else 1.0


def dedupe(zones):
    """Drop zones that stack on top of a stronger one of the same side."""
    kept = []
    for z in sorted(zones, key=lambda x: (x["status"] == "BROKEN", -x["quality"])):
        clash = next((k for k in kept if k["type"] == z["type"] and _overlap(k, z) > 0.60), None)
        if clash is not None:
            if z["heat"] > clash["heat"] + 4:
                kept.remove(clash)
                kept.append(z)
            continue
        kept.append(z)
    return kept


def sort_key(z):
    return (STATUS_RANK.get(z["status"], 9), -z["heat"], -z["quality"])
