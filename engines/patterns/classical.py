"""Classic chart-pattern detectors (stdlib port).

Every detector receives a Context and returns a list of serialisable pattern
dicts. Rules favour shapes whose boundaries are actually respected by price,
and every signal carries a measured target and protective stop.
"""
from __future__ import annotations

from .geometry import (
    Context,
    build_pattern,
    curve_points,
    fit_line,
    horizontal_line,
    line_from,
    line_value,
    max_residual,
    points_from_pivots,
    touch_count,
    volume_near,
)


def inr(value: float) -> str:
    return f"₹{value:,.2f}"


def _mean(vals) -> float:
    vals = list(vals)
    return sum(vals) / len(vals) if vals else 0.0


def _ptp(vals) -> float:
    vals = list(vals)
    return max(vals) - min(vals) if vals else 0.0


def _argmin(vals) -> int:
    return min(range(len(vals)), key=vals.__getitem__)


def _linspace01(n: int) -> list[float]:
    return [k / (n - 1) for k in range(n)] if n > 1 else [0.0]


def _polyfit2(xs: list[float], ys: list[float]) -> list[float]:
    """Quadratic least squares (degree-2 polyfit) via normal equations.

    Returns coefficients highest-power-first: [a2, a1, a0].
    Raises ValueError on a singular system (callers guard with try/except).
    """
    n = len(xs)
    s1 = sum(xs)
    s2 = sum(x * x for x in xs)
    s3 = sum(x * x * x for x in xs)
    s4 = sum(x * x * x * x for x in xs)
    t0 = sum(ys)
    t1 = sum(x * y for x, y in zip(xs, ys))
    t2 = sum(x * x * y for x, y in zip(xs, ys))
    mat = [
        [s4, s3, s2, t2],
        [s3, s2, s1, t1],
        [s2, s1, float(n), t0],
    ]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(mat[r][col]))
        if abs(mat[piv][col]) < 1e-12:
            raise ValueError("singular fit")
        mat[col], mat[piv] = mat[piv], mat[col]
        for r in range(col + 1, 3):
            f = mat[r][col] / mat[col][col]
            for k in range(col, 4):
                mat[r][k] -= f * mat[col][k]
    a0 = mat[2][3] / mat[2][2]
    a1 = (mat[1][3] - mat[1][2] * a0) / mat[1][1]
    a2 = (mat[0][3] - mat[0][2] * a0 - mat[0][1] * a1) / mat[0][0]
    return [a2, a1, a0]


def _polyval(coeff: list[float], x: float) -> float:
    out = 0.0
    for c in coeff:
        out = out * x + c
    return out


def _has_room(direction: str, last: float, target: float | None, atr: float) -> bool:
    if target is None:
        return True
    if direction == "bullish":
        return last < target - 0.15 * atr
    if direction == "bearish":
        return last > target + 0.15 * atr
    return True


def _highs(ctx: Context) -> list[dict]:
    return [p for p in ctx.pivots if p["kind"] == "H"]


def _lows(ctx: Context) -> list[dict]:
    return [p for p in ctx.pivots if p["kind"] == "L"]


# --------------------------------------------------------------------------- #
# Double top / bottom
# --------------------------------------------------------------------------- #

def detect_double_top(ctx: Context) -> list[dict]:
    highs = _highs(ctx)
    lows = _lows(ctx)
    best: dict | None = None
    best_score = 0.0
    for ai, first in enumerate(highs[:-1]):
        for second in highs[ai + 1:]:
            sep = second["i"] - first["i"]
            if sep < 5:
                continue
            if second["i"] < ctx.last_i - ctx.cfg["recent_window"] * 0.45:
                continue
            if sep > ctx.cfg["recent_window"] * 0.8:
                continue
            average = (first["price"] + second["price"]) / 2
            tolerance = max(average * 0.012, ctx.atr * 0.45)
            diff = abs(first["price"] - second["price"])
            if diff > tolerance:
                continue
            between = [p for p in lows if first["i"] < p["i"] < second["i"]]
            if not between:
                continue
            trough = min(between, key=lambda p: p["price"])
            depth = min(first["price"], second["price"]) - trough["price"]
            if depth < max(average * 0.018, ctx.atr * 0.9):
                continue
            last = ctx.last_close
            if last > max(first["price"], second["price"]) * 1.003:
                continue
            confirmed = last < trough["price"] - 0.10 * ctx.atr
            forming = (
                not confirmed
                and last <= second["price"] - 0.30 * depth
                and last >= trough["price"] * 0.995
            )
            if not confirmed and not forming:
                continue
            target = trough["price"] - depth
            if not _has_room("bearish", last, target, ctx.atr):
                continue
            stop = max(first["price"], second["price"]) + 0.25 * ctx.atr
            vol_first = volume_near(ctx.candles, first["i"])
            vol_second = volume_near(ctx.candles, second["i"])
            vol_fade = vol_second < vol_first * 0.98
            prior_look = max(0, first["i"] - 25)
            prior_up = bool(
                len(ctx.close[prior_look:first["i"]]) > 3
                and first["price"] - min(ctx.close[prior_look:first["i"]])
                > 0.45 * depth
            )
            symmetry = 1 - diff / tolerance
            score = (
                49
                + 14 * symmetry
                + min(10, (depth / ctx.atr) * 2.0)
                + (8 if confirmed else 0)
                + (6 if vol_fade else 0)
                + (5 if prior_up else 0)
            )
            if score <= best_score:
                continue
            status = "confirmed" if confirmed else "forming"
            confidence = min(score, 80 if not confirmed else 93)
            description = (
                f"Twin peaks at {inr(first['price'])} and {inr(second['price'])}, "
                f"neckline {inr(trough['price'])}. Height {inr(depth)} gives a measured "
                f"target of {inr(trough['price'])} − {inr(depth)} = {inr(target)}. "
                f"{'Neckline broken' if confirmed else 'Price is rolling over toward the neckline'}"
                f"{' on fading volume at the second peak' if vol_fade else ''}."
            )
            best = build_pattern(
                ctx,
                name="Double Top",
                category="reversal",
                direction="bearish",
                confidence=confidence,
                status=status,
                description=description,
                points=points_from_pivots(
                    ctx.candles,
                    [
                        (first["i"], first["price"], "Top 1"),
                        (trough["i"], trough["price"], "Neckline"),
                        (second["i"], second["price"], "Top 2"),
                    ],
                ),
                lines=[
                    horizontal_line(
                        ctx.candles, trough["price"], first["i"], ctx.last_i, "neckline"
                    )
                ],
                breakout=trough["price"],
                target=target,
                stop=stop,
                start_i=first["i"],
                rr_entry=trough["price"],
            )
            best_score = score
    return [best] if best else []


def detect_double_bottom(ctx: Context) -> list[dict]:
    highs = _highs(ctx)
    lows = _lows(ctx)
    best: dict | None = None
    best_score = 0.0
    for ai, first in enumerate(lows[:-1]):
        for second in lows[ai + 1:]:
            sep = second["i"] - first["i"]
            if sep < 5:
                continue
            if second["i"] < ctx.last_i - ctx.cfg["recent_window"] * 0.45:
                continue
            if sep > ctx.cfg["recent_window"] * 0.8:
                continue
            average = (first["price"] + second["price"]) / 2
            tolerance = max(average * 0.012, ctx.atr * 0.45)
            diff = abs(first["price"] - second["price"])
            if diff > tolerance:
                continue
            between = [p for p in highs if first["i"] < p["i"] < second["i"]]
            if not between:
                continue
            peak = max(between, key=lambda p: p["price"])
            height = peak["price"] - max(first["price"], second["price"])
            if height < max(average * 0.018, ctx.atr * 0.9):
                continue
            last = ctx.last_close
            if last < min(first["price"], second["price"]) * 0.997:
                continue
            confirmed = last > peak["price"] + 0.10 * ctx.atr
            forming = (
                not confirmed
                and last >= second["price"] + 0.30 * height
                and last <= peak["price"] * 1.005
            )
            if not confirmed and not forming:
                continue
            target = peak["price"] + height
            if not _has_room("bullish", last, target, ctx.atr):
                continue
            stop = min(first["price"], second["price"]) - 0.25 * ctx.atr
            vol_first = volume_near(ctx.candles, first["i"])
            vol_second = volume_near(ctx.candles, second["i"])
            vol_fade = vol_second < vol_first * 0.98
            prior_look = max(0, first["i"] - 25)
            prior_down = bool(
                len(ctx.close[prior_look:first["i"]]) > 3
                and max(ctx.close[prior_look:first["i"]]) - first["price"]
                > 0.45 * height
            )
            symmetry = 1 - diff / tolerance
            score = (
                49
                + 14 * symmetry
                + min(10, (height / ctx.atr) * 2.0)
                + (8 if confirmed else 0)
                + (6 if vol_fade else 0)
                + (5 if prior_down else 0)
            )
            if score <= best_score:
                continue
            status = "confirmed" if confirmed else "forming"
            description = (
                f"Twin troughs at {inr(first['price'])} and {inr(second['price'])}, "
                f"neckline {inr(peak['price'])}. Height {inr(height)} gives a measured "
                f"target of {inr(peak['price'])} + {inr(height)} = {inr(target)}. "
                f"{'Neckline reclaimed' if confirmed else 'Price is recovering toward the neckline'}"
                f"{' with selling drying up at the second low' if vol_fade else ''}."
            )
            best = build_pattern(
                ctx,
                name="Double Bottom",
                category="reversal",
                direction="bullish",
                confidence=min(score, 80 if not confirmed else 93),
                status=status,
                description=description,
                points=points_from_pivots(
                    ctx.candles,
                    [
                        (first["i"], first["price"], "Bottom 1"),
                        (peak["i"], peak["price"], "Neckline"),
                        (second["i"], second["price"], "Bottom 2"),
                    ],
                ),
                lines=[
                    horizontal_line(
                        ctx.candles, peak["price"], first["i"], ctx.last_i, "neckline"
                    )
                ],
                breakout=peak["price"],
                target=target,
                stop=stop,
                start_i=first["i"],
                rr_entry=peak["price"],
            )
            best_score = score
    return [best] if best else []


# --------------------------------------------------------------------------- #
# Triple top / bottom
# --------------------------------------------------------------------------- #

def detect_triple_top(ctx: Context) -> list[dict]:
    highs = _highs(ctx)
    lows = _lows(ctx)
    for start in range(len(highs) - 3, -1, -1):
        trio = highs[start:start + 3]
        if len(trio) < 3:
            continue
        h1, h2, h3 = trio
        if h3["i"] - h1["i"] < 10:
            continue
        if h3["i"] < ctx.last_i - ctx.cfg["recent_window"] * 0.40:
            continue
        average = (h1["price"] + h2["price"] + h3["price"]) / 3
        tolerance = max(average * 0.016, ctx.atr * 0.5)
        if max(p["price"] for p in trio) - min(p["price"] for p in trio) > tolerance:
            continue
        t1 = [p for p in lows if h1["i"] < p["i"] < h2["i"]]
        t2 = [p for p in lows if h2["i"] < p["i"] < h3["i"]]
        if not t1 or not t2:
            continue
        trough1 = min(t1, key=lambda p: p["price"])
        trough2 = min(t2, key=lambda p: p["price"])
        neckline = (trough1["price"] + trough2["price"]) / 2
        if abs(trough1["price"] - trough2["price"]) > max(average * 0.025, ctx.atr * 0.8):
            continue
        depth = average - neckline
        if depth < max(average * 0.02, ctx.atr):
            continue
        last = ctx.last_close
        if last > max(p["price"] for p in trio) * 1.003:
            continue
        confirmed = last < min(trough1["price"], trough2["price"]) - 0.1 * ctx.atr
        forming = (
            not confirmed
            and last <= h3["price"] - 0.30 * depth
            and last >= neckline * 0.995
        )
        if not confirmed and not forming:
            continue
        target = neckline - depth
        if not _has_room("bearish", last, target, ctx.atr):
            continue
        stop = max(p["price"] for p in trio) + 0.3 * ctx.atr
        symmetry = 1 - (
            max(p["price"] for p in trio) - min(p["price"] for p in trio)
        ) / tolerance
        score = 51 + 12 * symmetry + min(9, depth / ctx.atr * 1.8) + (8 if confirmed else 0)
        description = (
            f"Three rejections near {inr(average)} with a neckline through "
            f"{inr(trough1['price'])} / {inr(trough2['price'])}. Height {inr(depth)}; "
            f"measured target = {inr(neckline)} − {inr(depth)} = {inr(target)}. "
            f"{'Neckline broken.' if confirmed else 'Price is testing the lower half of the range.'}"
        )
        return [
            build_pattern(
                ctx,
                name="Triple Top",
                category="reversal",
                direction="bearish",
                confidence=min(score, 80 if not confirmed else 92),
                status="confirmed" if confirmed else "forming",
                description=description,
                points=points_from_pivots(
                    ctx.candles,
                    [
                        (h1["i"], h1["price"], "Top 1"),
                        (trough1["i"], trough1["price"], "Neckline"),
                        (h2["i"], h2["price"], "Top 2"),
                        (trough2["i"], trough2["price"], "Neckline"),
                        (h3["i"], h3["price"], "Top 3"),
                    ],
                ),
                lines=[
                    horizontal_line(ctx.candles, neckline, h1["i"], ctx.last_i, "neckline")
                ],
                breakout=neckline,
                target=target,
                stop=stop,
                start_i=h1["i"],
                rr_entry=neckline,
            )
        ]
    return []


def detect_triple_bottom(ctx: Context) -> list[dict]:
    highs = _highs(ctx)
    lows = _lows(ctx)
    for start in range(len(lows) - 3, -1, -1):
        trio = lows[start:start + 3]
        if len(trio) < 3:
            continue
        l1, l2, l3 = trio
        if l3["i"] - l1["i"] < 10:
            continue
        if l3["i"] < ctx.last_i - ctx.cfg["recent_window"] * 0.40:
            continue
        average = (l1["price"] + l2["price"] + l3["price"]) / 3
        tolerance = max(average * 0.016, ctx.atr * 0.5)
        if max(p["price"] for p in trio) - min(p["price"] for p in trio) > tolerance:
            continue
        p1 = [p for p in highs if l1["i"] < p["i"] < l2["i"]]
        p2 = [p for p in highs if l2["i"] < p["i"] < l3["i"]]
        if not p1 or not p2:
            continue
        peak1 = max(p1, key=lambda p: p["price"])
        peak2 = max(p2, key=lambda p: p["price"])
        neckline = (peak1["price"] + peak2["price"]) / 2
        if abs(peak1["price"] - peak2["price"]) > max(average * 0.025, ctx.atr * 0.8):
            continue
        height = neckline - average
        if height < max(average * 0.02, ctx.atr):
            continue
        last = ctx.last_close
        if last < min(p["price"] for p in trio) * 0.997:
            continue
        confirmed = last > max(peak1["price"], peak2["price"]) + 0.1 * ctx.atr
        forming = (
            not confirmed
            and last >= l3["price"] + 0.30 * height
            and last <= neckline * 1.005
        )
        if not confirmed and not forming:
            continue
        target = neckline + height
        if not _has_room("bullish", last, target, ctx.atr):
            continue
        stop = min(p["price"] for p in trio) - 0.3 * ctx.atr
        symmetry = 1 - (
            max(p["price"] for p in trio) - min(p["price"] for p in trio)
        ) / tolerance
        score = 51 + 12 * symmetry + min(9, height / ctx.atr * 1.8) + (8 if confirmed else 0)
        description = (
            f"Three defences near {inr(average)} with a neckline through "
            f"{inr(peak1['price'])} / {inr(peak2['price'])}. Height {inr(height)}; "
            f"measured target = {inr(neckline)} + {inr(height)} = {inr(target)}. "
            f"{'Neckline reclaimed.' if confirmed else 'Price is pushing into the upper half of the base.'}"
        )
        return [
            build_pattern(
                ctx,
                name="Triple Bottom",
                category="reversal",
                direction="bullish",
                confidence=min(score, 80 if not confirmed else 92),
                status="confirmed" if confirmed else "forming",
                description=description,
                points=points_from_pivots(
                    ctx.candles,
                    [
                        (l1["i"], l1["price"], "Bottom 1"),
                        (peak1["i"], peak1["price"], "Neckline"),
                        (l2["i"], l2["price"], "Bottom 2"),
                        (peak2["i"], peak2["price"], "Neckline"),
                        (l3["i"], l3["price"], "Bottom 3"),
                    ],
                ),
                lines=[
                    horizontal_line(ctx.candles, neckline, l1["i"], ctx.last_i, "neckline")
                ],
                breakout=neckline,
                target=target,
                stop=stop,
                start_i=l1["i"],
                rr_entry=neckline,
            )
        ]
    return []


# --------------------------------------------------------------------------- #
# Head & shoulders
# --------------------------------------------------------------------------- #

def detect_head_shoulders(ctx: Context) -> list[dict]:
    pivots = ctx.pivots
    best = None
    best_score = 0.0
    for i in range(len(pivots) - 4):
        window = pivots[i:i + 5]
        if [p["kind"] for p in window] != ["H", "L", "H", "L", "H"]:
            continue
        left, trough1, head, trough2, right = window
        if right["i"] < ctx.last_i - ctx.cfg["recent_window"] * 0.18:
            continue
        if right["i"] - left["i"] < 15:
            continue
        height = head["price"] - max(left["price"], right["price"])
        if height < max(ctx.atr * 0.8, head["price"] * 0.012):
            continue
        if head["price"] <= max(left["price"], right["price"]):
            continue
        shoulder_avg = (left["price"] + right["price"]) / 2
        if abs(left["price"] - right["price"]) > shoulder_avg * 0.07:
            continue
        if trough2["price"] <= trough1["price"] * 0.85:
            continue
        fitted = fit_line([trough1, trough2])
        if not fitted:
            continue
        slope, intercept, _ = fitted
        neck_at_right = line_value(slope, intercept, right["i"])
        neck_height = head["price"] - line_value(slope, intercept, head["i"])
        if neck_height <= 0:
            continue
        tilt = abs(neck_at_right - line_value(slope, intercept, trough1["i"])) / neck_height
        if tilt > 0.45:
            continue
        neckline_now = line_value(slope, intercept, ctx.last_i)
        last = ctx.last_close
        if last > head["price"] * 1.003:
            continue
        confirmed = last < neckline_now - 0.1 * ctx.atr
        forming = (
            not confirmed
            and last < right["price"] - 0.2 * neck_height
            and last > neckline_now
        )
        if not confirmed and not forming:
            continue
        target = neckline_now - neck_height
        if not _has_room("bearish", last, target, ctx.atr):
            continue
        stop = right["price"] + 0.35 * ctx.atr
        vol_head = volume_near(ctx.candles, head["i"])
        vol_shoulders = max(volume_near(ctx.candles, left["i"]), volume_near(ctx.candles, right["i"]))
        vol_confirm = vol_head > vol_shoulders * 1.05
        prior_look = max(0, left["i"] - 25)
        prior_up = bool(
            len(ctx.close[prior_look:left["i"]]) > 3
            and left["price"] - min(ctx.close[prior_look:left["i"]])
            > 0.4 * neck_height
        )
        symmetry = 1 - abs(left["price"] - right["price"]) / (shoulder_avg * 0.07)
        score = (
            52
            + 12 * symmetry
            + max(0, 8 * (1 - tilt / 0.45))
            + min(8, neck_height / ctx.atr * 1.5)
            + (8 if confirmed else 0)
            + (5 if vol_confirm else 0)
            + (5 if prior_up else 0)
        )
        if score <= best_score:
            continue
        description = (
            f"Left shoulder {inr(left['price'])}, head {inr(head['price'])}, "
            f"right shoulder {inr(right['price'])}. Neckline slopes through "
            f"{inr(trough1['price'])} → {inr(trough2['price'])} and sits at "
            f"{inr(neck_at_right)} under the right shoulder. Height {inr(neck_height)}; "
            f"target = {inr(neckline_now)} − {inr(neck_height)} = {inr(target)}. "
            f"{'Neckline broken' if confirmed else 'Right shoulder is rolling over'}"
            f"{' on heavier head volume' if vol_confirm else ''}."
        )
        best = build_pattern(
            ctx,
            name="Head & Shoulders",
            category="reversal",
            direction="bearish",
            confidence=min(score, 78 if not confirmed else 94),
            status="confirmed" if confirmed else "forming",
            description=description,
            points=points_from_pivots(
                ctx.candles,
                [
                    (left["i"], left["price"], "L Shoulder"),
                    (trough1["i"], trough1["price"], "Neckline"),
                    (head["i"], head["price"], "Head"),
                    (trough2["i"], trough2["price"], "Neckline"),
                    (right["i"], right["price"], "R Shoulder"),
                ],
            ),
            lines=[line_from(ctx.candles, slope, intercept, trough1["i"], ctx.last_i, "neckline")],
            breakout=neckline_now,
            target=target,
            stop=stop,
            start_i=left["i"],
            rr_entry=neckline_now,
        )
        best_score = score
    return [best] if best else []


def detect_inverse_head_shoulders(ctx: Context) -> list[dict]:
    pivots = ctx.pivots
    best = None
    best_score = 0.0
    for i in range(len(pivots) - 4):
        window = pivots[i:i + 5]
        if [p["kind"] for p in window] != ["L", "H", "L", "H", "L"]:
            continue
        left, peak1, head, peak2, right = window
        if right["i"] < ctx.last_i - ctx.cfg["recent_window"] * 0.18:
            continue
        if right["i"] - left["i"] < 15:
            continue
        height = min(left["price"], right["price"]) - head["price"]
        if height < max(ctx.atr * 0.8, head["price"] * 0.012):
            continue
        if head["price"] >= min(left["price"], right["price"]):
            continue
        shoulder_avg = (left["price"] + right["price"]) / 2
        if abs(left["price"] - right["price"]) > shoulder_avg * 0.07:
            continue
        if peak2["price"] >= peak1["price"] * 1.15:
            continue
        fitted = fit_line([peak1, peak2])
        if not fitted:
            continue
        slope, intercept, _ = fitted
        neck_at_right = line_value(slope, intercept, right["i"])
        neck_height = line_value(slope, intercept, head["i"]) - head["price"]
        if neck_height <= 0:
            continue
        tilt = abs(neck_at_right - line_value(slope, intercept, peak1["i"])) / neck_height
        if tilt > 0.45:
            continue
        neckline_now = line_value(slope, intercept, ctx.last_i)
        last = ctx.last_close
        if last < head["price"] * 0.997:
            continue
        confirmed = last > neckline_now + 0.1 * ctx.atr
        forming = (
            not confirmed
            and last > right["price"] + 0.2 * neck_height
            and last < neckline_now
        )
        if not confirmed and not forming:
            continue
        target = neckline_now + neck_height
        if not _has_room("bullish", last, target, ctx.atr):
            continue
        stop = right["price"] - 0.35 * ctx.atr
        vol_head = volume_near(ctx.candles, head["i"])
        vol_shoulders = max(volume_near(ctx.candles, left["i"]), volume_near(ctx.candles, right["i"]))
        vol_confirm = vol_head > vol_shoulders * 1.05
        prior_look = max(0, left["i"] - 25)
        prior_down = bool(
            len(ctx.close[prior_look:left["i"]]) > 3
            and max(ctx.close[prior_look:left["i"]]) - left["price"]
            > 0.4 * neck_height
        )
        symmetry = 1 - abs(left["price"] - right["price"]) / (shoulder_avg * 0.07)
        score = (
            52
            + 12 * symmetry
            + max(0, 8 * (1 - tilt / 0.45))
            + min(8, neck_height / ctx.atr * 1.5)
            + (8 if confirmed else 0)
            + (5 if vol_confirm else 0)
            + (5 if prior_down else 0)
        )
        if score <= best_score:
            continue
        description = (
            f"Inverse shoulders at {inr(left['price'])} / {inr(right['price'])} and a "
            f"head at {inr(head['price'])}. Neckline sits at {inr(neckline_now)} with "
            f"height {inr(neck_height)}; target = {inr(neckline_now)} + "
            f"{inr(neck_height)} = {inr(target)}. "
            f"{'Neckline reclaimed' if confirmed else 'Right shoulder is turning up'}"
            f"{' on heavier head volume' if vol_confirm else ''}."
        )
        best = build_pattern(
            ctx,
            name="Inverse Head & Shoulders",
            category="reversal",
            direction="bullish",
            confidence=min(score, 78 if not confirmed else 94),
            status="confirmed" if confirmed else "forming",
            description=description,
            points=points_from_pivots(
                ctx.candles,
                [
                    (left["i"], left["price"], "L Shoulder"),
                    (peak1["i"], peak1["price"], "Neckline"),
                    (head["i"], head["price"], "Head"),
                    (peak2["i"], peak2["price"], "Neckline"),
                    (right["i"], right["price"], "R Shoulder"),
                ],
            ),
            lines=[line_from(ctx.candles, slope, intercept, peak1["i"], ctx.last_i, "neckline")],
            breakout=neckline_now,
            target=target,
            stop=stop,
            start_i=left["i"],
            rr_entry=neckline_now,
        )
        best_score = score
    return [best] if best else []


# --------------------------------------------------------------------------- #
# Triangles, wedges, channels, rectangles
# --------------------------------------------------------------------------- #

def _fit_side(points: list[dict]):
    use = points[-3:] if len(points) >= 3 else points
    fitted = fit_line(use)
    if not fitted:
        return None
    slope, intercept, r2 = fitted
    if len(use) >= 3 and r2 < 0.28:
        use = points[-2:]
        fitted = fit_line(use)
        if not fitted:
            return None
        slope, intercept, r2 = fitted
    return slope, intercept, r2, use


def detect_triangle(ctx: Context) -> list[dict]:
    highs = _highs(ctx)
    lows = _lows(ctx)
    if len(highs) < 2 or len(lows) < 2:
        return []

    hi_pts = highs[-4:]
    lo_pts = lows[-4:]
    res_fit = _fit_side(hi_pts)
    sup_fit = _fit_side(lo_pts)
    if not res_fit or not sup_fit:
        return []
    res_slope, res_int, res_r2, res_use = res_fit
    sup_slope, sup_int, sup_r2, sup_use = sup_fit

    start_i = min(res_use[0]["i"], sup_use[0]["i"])
    span = ctx.last_i - start_i
    if span < 12 or span > ctx.cfg["recent_window"]:
        return []
    if abs(res_use[0]["i"] - sup_use[0]["i"]) > ctx.cfg["recent_window"] * 0.38:
        return []

    atr = ctx.atr
    width_start = line_value(res_slope, res_int, start_i) - line_value(sup_slope, sup_int, start_i)
    res_now = line_value(res_slope, res_int, ctx.last_i)
    sup_now = line_value(sup_slope, sup_int, ctx.last_i)
    width_now = res_now - sup_now
    if width_start < atr * 1.1 or width_now <= atr * 0.03:
        return []
    if width_now > width_start * 1.45:
        return []

    closes = ctx.close[start_i:ctx.last_i + 1]
    inside_count = 0
    for offset, close_v in enumerate(closes):
        i = start_i + offset
        res_v = res_slope * i + res_int
        sup_v = sup_slope * i + sup_int
        if sup_v - 0.8 * atr <= close_v <= res_v + 0.8 * atr:
            inside_count += 1
    if inside_count / len(closes) < 0.82:
        return []

    s_res = res_slope / atr
    s_sup = sup_slope / atr
    flat = 0.022
    ratio = width_now / width_start
    name = None
    bias = "neutral"
    category = "continuation"

    if abs(s_res) <= flat and s_sup > flat * 1.15 and ratio < 0.97:
        name, bias = "Ascending Triangle", "bullish"
    elif abs(s_sup) <= flat and s_res < -flat * 1.15 and ratio < 0.97:
        name, bias = "Descending Triangle", "bearish"
    elif s_res < -flat and s_sup > flat and ratio < 0.92:
        name, bias = "Symmetrical Triangle", "neutral"
    elif s_sup > flat and s_res > flat * 0.5:
        if (s_sup - s_res) > flat * 0.7 and ratio < 0.88:
            name, bias, category = "Rising Wedge", "bearish", "reversal"
        else:
            name, bias = "Rising Channel", "bullish"
    elif s_sup < -flat and s_res < -flat * 0.5:
        if (s_res - s_sup) < -flat * 0.7 and ratio < 0.88:
            name, bias, category = "Falling Wedge", "bullish", "reversal"
        else:
            name, bias = "Falling Channel", "bearish"
    elif abs(s_res) <= flat and abs(s_sup) <= flat and 0.85 <= ratio <= 1.15:
        name, bias = "Rectangle", "neutral"
    if name is None:
        return []

    if ratio < 0.98:
        denom = sup_slope - res_slope
        if denom != 0:
            apex = (res_int - sup_int) / denom
            if apex < ctx.last_i - 2 and ctx.last_close <= res_now + 0.15 * atr and ctx.last_close >= sup_now - 0.15 * atr:
                return []

    last = ctx.last_close
    buffer = 0.15 * atr
    if last > res_now + buffer:
        confirmed, direction = "up", "bullish"
    elif last < sup_now - buffer:
        confirmed, direction = "down", "bearish"
    else:
        confirmed, direction = None, bias

    max_run = max(2.2 * atr, 0.35 * width_start)
    if last > res_now + max_run or last < sup_now - max_run:
        return []

    if direction == "bullish":
        target = res_now + width_start
        stop = sup_now - 0.25 * atr
        entry = res_now
    elif direction == "bearish":
        target = sup_now - width_start
        stop = res_now + 0.25 * atr
        entry = sup_now
    else:
        target = None
        stop = None
        entry = last

    if direction != "neutral" and target is not None and not _has_room(direction, last, target, atr):
        return []

    tol = max(0.45 * atr, ctx.last_close * 0.0035)
    res_touches = touch_count(ctx.raw_pivots, res_slope, res_int, tol, "H", start_i)
    sup_touches = touch_count(ctx.raw_pivots, sup_slope, sup_int, tol, "L", start_i)
    touches = res_touches + sup_touches
    residual = max(
        max_residual(res_use, res_slope, res_int),
        max_residual(sup_use, sup_slope, sup_int),
    )
    if res_touches < 2 or sup_touches < 2:
        return []
    if residual > max(0.8 * atr, ctx.last_close * 0.008):
        return []
    quality = (res_r2 + sup_r2) / 2 if len(res_use) >= 3 or len(sup_use) >= 3 else 0.7
    convergence_bonus = max(0.0, 1 - ratio) * 10 if name.endswith(("Triangle", "Wedge")) else max(0.0, 1 - abs(1 - ratio)) * 6
    score = (
        47
        + 13 * quality
        + min(9, touches * 1.6)
        + convergence_bonus
        + (8 if confirmed else 0)
        + (5 if ctx.vol_ratio >= 1.15 else 0)
        + (4 if span >= 25 else 0)
        - min(8, residual / atr * 2.5)
    )
    status = "confirmed" if confirmed else "forming"
    if confirmed == "up":
        breakout_note = f"Price closed above resistance at {inr(res_now)}"
        target_formula = f"{inr(res_now)} + pattern height {inr(width_start)} = {inr(target)}"
    elif confirmed == "down":
        breakout_note = f"Price closed below support at {inr(sup_now)}"
        target_formula = f"{inr(sup_now)} − pattern height {inr(width_start)} = {inr(target)}"
    else:
        breakout_note = (
            f"Price is inside the pattern between {inr(sup_now)} support and "
            f"{inr(res_now)} resistance"
        )
        if direction == "bullish":
            target_formula = (
                f"{inr(res_now)} + {inr(width_start)} = {inr(target)} on a break above resistance"
            )
        elif direction == "bearish":
            target_formula = (
                f"{inr(sup_now)} − {inr(width_start)} = {inr(target)} on a break below support"
            )
        else:
            target_formula = f"width {inr(width_start)} projects in the breakout direction"
    description = (
        f"{name} over {span} bars with {touches} boundary touches. "
        f"{breakout_note}; measured move {target_formula}. "
        f"Bias is {bias if bias != 'neutral' else 'two-sided'}"
        f"{' and the breakout is confirmed.' if confirmed else '; wait for the close through the boundary.'}"
    )
    lines = [
        line_from(ctx.candles, res_slope, res_int, start_i, ctx.last_i, "resistance"),
        line_from(ctx.candles, sup_slope, sup_int, start_i, ctx.last_i, "support"),
    ]
    points = points_from_pivots(
        ctx.candles,
        [(p["i"], p["price"], "High") for p in res_use]
        + [(p["i"], p["price"], "Low") for p in sup_use],
    )
    return [
        build_pattern(
            ctx,
            name=name,
            category=category,
            direction=direction,
            confidence=min(score, 76 if not confirmed else 92),
            status=status,
            description=description,
            points=points,
            lines=lines,
            breakout=res_now if direction == "bullish" else sup_now if direction == "bearish" else None,
            target=target,
            stop=stop,
            start_i=start_i,
            rr_entry=entry,
        )
    ]


# --------------------------------------------------------------------------- #
# Flags and pennants
# --------------------------------------------------------------------------- #

def _flag_candidate(ctx: Context, bullish: bool):
    best = None
    best_score = 0.0
    high, low, close, volume = ctx.high, ctx.low, ctx.close, ctx.volume
    last = ctx.last_i
    atr = ctx.atr
    for cons_len in range(4, 21):
        c0 = last - cons_len + 1
        if c0 < 20:
            continue
        cons_hi = max(high[c0:last + 1])
        cons_lo = min(low[c0:last + 1])
        cons_range = cons_hi - cons_lo
        for pole_len in range(4, 16):
            p0 = c0 - pole_len
            if p0 < 5:
                continue
            pole_hi = max(high[p0:c0])
            pole_lo = min(low[p0:c0])
            move = pole_hi - pole_lo
            if move <= 0:
                continue
            if bullish:
                if move < max(3 * atr, pole_lo * 0.03):
                    continue
                if close[c0 - 1] < pole_lo + 0.70 * move:
                    continue
                if cons_lo < pole_hi - 0.5 * move:
                    continue
                if cons_hi > pole_hi + 0.5 * atr:
                    continue
                if cons_range > 0.55 * move:
                    continue
                if cons_hi <= cons_lo:
                    continue
                trigger = cons_hi
                if ctx.last_close < cons_lo - 0.6 * atr:
                    continue
                if ctx.last_close > trigger + 1.0 * atr:
                    continue
                confirmed = ctx.last_close > trigger + 0.10 * atr
                target = trigger + move
                stop = cons_lo - 0.15 * atr
                direction = "bullish"
            else:
                if move < max(3 * atr, pole_hi * 0.03):
                    continue
                if close[c0 - 1] > pole_hi - 0.70 * move:
                    continue
                if cons_hi > pole_lo + 0.5 * move:
                    continue
                if cons_lo < pole_lo - 0.5 * atr:
                    continue
                if cons_range > 0.55 * move:
                    continue
                if cons_hi <= cons_lo:
                    continue
                trigger = cons_lo
                if ctx.last_close > cons_hi + 0.6 * atr:
                    continue
                if ctx.last_close < trigger - 1.0 * atr:
                    continue
                confirmed = ctx.last_close < trigger - 0.10 * atr
                target = trigger - move
                stop = cons_hi + 0.15 * atr
                direction = "bearish"

            if target is not None and not _has_room(direction, ctx.last_close, target, atr):
                continue

            pole_vol = _mean(volume[p0:c0]) or 0
            cons_vol = _mean(volume[c0:last + 1]) or 0
            vol_contract = cons_vol < pole_vol * 0.9 if pole_vol else False

            try:
                fitted = fit_line([
                    {"i": x, "price": y}
                    for x, y in zip(range(c0, last + 1), close[c0:last + 1])
                ])
                drift = float(fitted[0]) if fitted else 0.0
            except Exception:  # noqa: BLE001
                drift = 0.0
            half = max(1, cons_len // 2)
            range_first = _ptp(high[c0:c0 + half]) if half > 1 else 0.0
            range_second = _ptp(high[last - half + 1:last + 1]) if half > 1 else 0.0
            converging = range_second < range_first * 0.75

            if bullish:
                name = "Bull Pennant" if converging else "Bull Flag"
                pole_line_points = [
                    (p0, pole_lo, "Pole"),
                    (c0 - 1, pole_hi, "Pole"),
                ]
                boundary_role = "resistance"
            else:
                name = "Bear Pennant" if converging else "Bear Flag"
                pole_line_points = [
                    (p0, pole_hi, "Pole"),
                    (c0 - 1, pole_lo, "Pole"),
                ]
                boundary_role = "support"

            tightness = max(0.0, 1 - cons_range / (0.55 * move))
            score = (
                48
                + min(18, (move / atr) * 2.4)
                + 9 * tightness
                + (7 if vol_contract else 0)
                + (7 if confirmed else 0)
                + (4 if cons_len >= 6 else 0)
            )
            if score <= best_score:
                continue
            status = "confirmed" if confirmed else "forming"
            description = (
                f"{'Impulse leg' if bullish else 'Distribution leg'} of {inr(move)} "
                f"({move / atr:.1f} ATR) over {pole_len} bars, followed by a "
                f"{cons_len}-bar {'pennant' if converging else 'flag'} of {inr(cons_range)}. "
                f"Trigger {inr(trigger)}; measured target = trigger "
                f"{'+' if bullish else '−'} pole {inr(move)} = {inr(target)}. "
                f"{'Breakout confirmed' if confirmed else 'Waiting for the breakout'}"
                f"{' on shrinking volume' if vol_contract else ''}."
            )
            best = build_pattern(
                ctx,
                name=name,
                category="continuation",
                direction=direction,
                confidence=min(score, 80 if not confirmed else 92),
                status=status,
                description=description,
                points=points_from_pivots(
                    ctx.candles,
                    pole_line_points
                    + [
                        (c0, trigger, "Breakout"),
                        (last, ctx.last_close, "Last"),
                    ],
                ),
                lines=[
                    horizontal_line(ctx.candles, trigger, c0, last, "breakout"),
                    horizontal_line(
                        ctx.candles,
                        cons_lo if bullish else cons_hi,
                        c0,
                        last,
                        boundary_role,
                    ),
                ],
                breakout=trigger,
                target=target,
                stop=stop,
                start_i=p0,
                rr_entry=trigger,
            )
            best_score = score
    return best


def detect_flags(ctx: Context) -> list[dict]:
    out = []
    bull = _flag_candidate(ctx, True)
    bear = _flag_candidate(ctx, False)
    if bull:
        out.append(bull)
    if bear:
        out.append(bear)
    return out


# --------------------------------------------------------------------------- #
# Cup & handle
# --------------------------------------------------------------------------- #

def detect_cup_handle(ctx: Context) -> list[dict]:
    n = ctx.last_i + 1
    if n < 55:
        return []
    best = None
    best_score = 0.0
    high, low, close = ctx.high, ctx.low, ctx.close
    for handle_len in range(3, 11):
        cup_end = ctx.last_i - handle_len
        handle_low = min(low[cup_end + 1:ctx.last_i + 1])
        handle_high = max(high[cup_end + 1:ctx.last_i + 1])
        for cup_len in range(40, 151, 5):
            start = cup_end - cup_len + 1
            if start < 10:
                continue
            seg_hi = high[start:cup_end + 1]
            seg_lo = low[start:cup_end + 1]
            seg_close = close[start:cup_end + 1]
            rim_slice = max(3, cup_len // 5)
            left_rim = max(seg_hi[:rim_slice])
            right_rim = max(seg_hi[-rim_slice:])
            mid_start = cup_len // 4
            mid_end = cup_len - cup_len // 4
            bottom = min(seg_lo[mid_start:mid_end])
            depth = min(left_rim, right_rim) - bottom
            if depth < max(min(left_rim, right_rim) * 0.06, ctx.atr_at(cup_end) * 2.5):
                continue
            if max(left_rim, right_rim) > min(left_rim, right_rim) * 1.08:
                continue
            bottom_rel = (_argmin(seg_lo) + 0.5) / cup_len
            if not (0.28 <= bottom_rel <= 0.72):
                continue
            edge_avg = (_mean(seg_close[:max(2, cup_len // 8)]) + _mean(seg_close[-max(2, cup_len // 8):])) / 2
            middle_avg = _mean(seg_close[cup_len // 3:2 * cup_len // 3])
            if edge_avg - middle_avg < 0.45 * depth:
                continue
            x = _linspace01(cup_len)
            try:
                coeff = _polyfit2(x, seg_close)
                predicted = [_polyval(coeff, xx) for xx in x]
                ss_res = sum((y - p) ** 2 for y, p in zip(seg_close, predicted))
                ym = _mean(seg_close)
                ss_tot = sum((y - ym) ** 2 for y in seg_close)
                r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
            except Exception:  # noqa: BLE001
                continue
            if coeff[0] <= 0 or r2 < 0.28:
                continue
            rim = max(left_rim, right_rim)
            if handle_high > rim * 1.03:
                continue
            if handle_low < bottom + 0.45 * depth:
                continue
            if handle_low < rim - 0.55 * depth:
                continue
            handle_range = handle_high - handle_low
            if handle_range > 0.55 * depth:
                continue
            last = ctx.last_close
            if last < rim * 0.94:
                continue
            confirmed = last > rim + 0.10 * ctx.atr
            target = rim + depth
            if not _has_room("bullish", last, target, ctx.atr):
                continue
            stop = handle_low - 0.2 * ctx.atr
            cup_vol = _mean(ctx.volume[start:cup_end + 1]) or 0
            handle_vol = _mean(ctx.volume[cup_end + 1:ctx.last_i + 1]) or 0
            dry = handle_vol < cup_vol * 0.95 if cup_vol else False
            score = (
                50
                + min(12, depth / ctx.atr * 1.8)
                + max(0, 8 * (1 - abs(left_rim - right_rim) / (rim * 0.08)))
                + min(8, r2 * 12)
                + (7 if dry else 0)
                + (8 if confirmed else 0)
                + max(0, 5 * (1 - handle_range / (0.55 * depth)))
            )
            if score <= best_score:
                continue
            curve = []
            samples = 40
            for k in range(samples):
                xx = k / (samples - 1)
                value = _polyval(coeff, xx)
                curve.append(max(bottom * 0.98, value))
            status = "confirmed" if confirmed else "forming"
            description = (
                f"{cup_len}-bar rounded cup, {min(left_rim, right_rim):,.2f}–"
                f"{rim:,.2f} rims, bottom {inr(bottom)} (depth {inr(depth)}), "
                f"{handle_len}-bar handle holding {inr(handle_low)}. "
                f"Buy trigger {inr(rim)}; measured target = rim + depth = "
                f"{inr(rim)} + {inr(depth)} = {inr(target)}. "
                f"{'Handle breakout confirmed.' if confirmed else 'Handle is still forming below the rim.'}"
            )
            best = build_pattern(
                ctx,
                name="Cup & Handle",
                category="continuation",
                direction="bullish",
                confidence=min(score, 80 if not confirmed else 93),
                status=status,
                description=description,
                points=points_from_pivots(
                    ctx.candles,
                    [
                        (start, left_rim, "Left rim"),
                        (start + _argmin(seg_lo[mid_start:mid_end]) + mid_start, bottom, "Cup low"),
                        (cup_end, right_rim, "Right rim"),
                        (ctx.last_i, handle_low, "Handle"),
                    ],
                ),
                lines=[
                    horizontal_line(ctx.candles, rim, start, ctx.last_i, "breakout"),
                    {
                        "role": "curve",
                        "points": curve_points(ctx.candles, curve, start),
                        "dash": False,
                    },
                ],
                breakout=rim,
                target=target,
                stop=stop,
                start_i=start,
                rr_entry=rim,
            )
            best_score = score
    return [best] if best else []


# --------------------------------------------------------------------------- #
# Rounding bottom / top
# --------------------------------------------------------------------------- #

def _rounding(ctx: Context, bearish: bool) -> list[dict]:
    best = None
    best_score = 0.0
    if ctx.timeframe in ("5m", "15m"):
        lengths = (120, 150, 180)
    elif ctx.timeframe == "1h":
        lengths = (60, 90, 120, 150)
    elif ctx.timeframe == "1d":
        lengths = (55, 75, 95, 120, 150)
    else:
        lengths = (40, 55, 70, 90, 110)
    for length in lengths:
        if length > ctx.last_i + 1:
            continue
        start = ctx.last_i - length + 1
        closes = ctx.close[start:ctx.last_i + 1]
        x = _linspace01(length)
        try:
            coeff = _polyfit2(x, closes)
            predicted = [_polyval(coeff, xx) for xx in x]
            ss_res = sum((y - p) ** 2 for y, p in zip(closes, predicted))
            ym = _mean(closes)
            ss_tot = sum((y - ym) ** 2 for y in closes)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        except Exception:  # noqa: BLE001
            continue
        if r2 < 0.50:
            continue
        if (coeff[0] <= 0 and not bearish) or (coeff[0] >= 0 and bearish):
            continue
        vertex = -coeff[1] / (2 * coeff[0]) if coeff[0] != 0 else 0.5
        if not (0.25 <= vertex <= 0.75):
            continue
        edge_slice = max(4, length // 10)
        left_edge = _mean(closes[:edge_slice])
        right_edge = _mean(closes[-edge_slice:])
        edge = (left_edge + right_edge) / 2
        extreme = min(closes) if not bearish else max(closes)
        depth = abs(edge - extreme)
        if depth < max(ctx.atr * 3.0, abs(edge) * 0.065):
            continue
        if abs(left_edge - right_edge) > 0.55 * depth:
            continue
        if min(
            abs(left_edge - extreme),
            abs(right_edge - extreme),
        ) < 0.70 * depth:
            continue
        base = closes[int(length * 0.32):int(length * 0.68)]
        if len(base) > 3 and _ptp(base) > 0.5 * depth:
            continue
        last = ctx.last_close
        if bearish:
            trigger = min(ctx.low[ctx.last_i - 14:ctx.last_i + 1])
            if last > trigger + 1.2 * ctx.atr:
                continue
            confirmed = last < trigger - 0.1 * ctx.atr
            target = trigger - depth
            stop = max(ctx.high[ctx.last_i - 14:ctx.last_i + 1]) + 0.25 * ctx.atr
            direction = "bearish"
            name = "Rounding Top"
        else:
            trigger = max(ctx.high[ctx.last_i - 14:ctx.last_i + 1])
            if last < trigger - 1.2 * ctx.atr:
                continue
            confirmed = last > trigger + 0.1 * ctx.atr
            target = trigger + depth
            stop = min(ctx.low[ctx.last_i - 14:ctx.last_i + 1]) - 0.25 * ctx.atr
            direction = "bullish"
            name = "Rounding Bottom"
        if not _has_room(direction, last, target, ctx.atr):
            continue
        score = 50 + min(14, r2 * 18) + min(12, depth / ctx.atr * 2) + (7 if confirmed else 0)
        status = "confirmed" if confirmed else "forming"
        description = (
            f"{length}-bar {'dome' if bearish else 'saucer'} with a quadratic fit R² of "
            f"{r2:.2f} and {inr(depth)} of vertical travel. "
            f"Neckline {inr(trigger)}; measured target = trigger "
            f"{'−' if bearish else '+'} depth = {inr(target)}. "
            f"{'Breakout confirmed.' if confirmed else 'Price is testing the neckline.'}"
        )
        curve = [max(0.0, _polyval(coeff, k / 39)) for k in range(40)]
        best = build_pattern(
            ctx,
            name=name,
            category="reversal",
            direction=direction,
            confidence=min(score, 77 if not confirmed else 89),
            status=status,
            description=description,
            points=points_from_pivots(
                ctx.candles,
                [(ctx.last_i - 14, trigger, "Neckline"), (ctx.last_i, last, "Last")],
            ),
            lines=[
                horizontal_line(ctx.candles, trigger, start + length // 3, ctx.last_i, "neckline"),
                {"role": "curve", "points": curve_points(ctx.candles, curve, start), "dash": False},
            ],
            breakout=trigger,
            target=target,
            stop=stop,
            start_i=start,
            rr_entry=trigger,
        )
        best_score = score
    return [best] if best else []


def detect_rounding(ctx: Context) -> list[dict]:
    out = []
    out.extend(_rounding(ctx, False))
    out.extend(_rounding(ctx, True))
    return out


# --------------------------------------------------------------------------- #
# Range / 52-week breakouts
# --------------------------------------------------------------------------- #

def detect_breakouts(ctx: Context) -> list[dict]:
    if ctx.timeframe in ("5m", "15m"):
        lookback = 24
        label = "Range"
    elif ctx.timeframe == "1h":
        lookback = 40
        label = "Range"
    elif ctx.timeframe == "1d":
        lookback = 200
        label = "52-Week"
    else:
        lookback = 100
        label = "Multi-Month"
    lookback = min(lookback, ctx.last_i)
    if lookback < 15:
        return []
    start = ctx.last_i - lookback
    prior_high = max(ctx.high[start:ctx.last_i])
    prior_low = min(ctx.low[start:ctx.last_i])
    last = ctx.last_close
    atr = ctx.atr
    buffer = 0.1 * atr
    vol = ctx.vol_ratio
    out = []

    if last > prior_high + buffer:
        target = last + max(2.0 * atr, (last - prior_low) * 0.25)
        stop = prior_high - 0.8 * atr
        if _has_room("bullish", last, target, atr):
            score = 55 + min(12, (vol - 1) * 12) + min(8, (last - prior_high) / atr * 4) + (5 if ctx.trend == "up" else 0)
            out.append(
                build_pattern(
                    ctx,
                    name=f"{label} High Breakout",
                    category="breakout",
                    direction="bullish",
                    confidence=min(score, 90),
                    status="confirmed",
                    description=(
                        f"Close {inr(last)} cleared the {lookback}-bar high of "
                        f"{inr(prior_high)} by {inr(last - prior_high)} on "
                        f"{vol:.1f}× average volume. "
                        f"Target {inr(target)} (2 ATR projection); invalidation {inr(stop)}."
                    ),
                    points=points_from_pivots(
                        ctx.candles,
                        [(ctx.last_i - lookback, prior_high, "Resistance"), (ctx.last_i, last, "Breakout")],
                    ),
                    lines=[
                        horizontal_line(ctx.candles, prior_high, start, ctx.last_i, "breakout"),
                    ],
                    breakout=prior_high,
                    target=target,
                    stop=stop,
                    start_i=start,
                    rr_entry=last,
                )
            )
    elif last < prior_low - buffer:
        target = last - max(2.0 * atr, (prior_high - last) * 0.25)
        stop = prior_low + 0.8 * atr
        if _has_room("bearish", last, target, atr):
            score = 55 + min(12, (vol - 1) * 12) + min(8, (prior_low - last) / atr * 4) + (5 if ctx.trend == "down" else 0)
            out.append(
                build_pattern(
                    ctx,
                    name=f"{label} Low Breakdown",
                    category="breakout",
                    direction="bearish",
                    confidence=min(score, 90),
                    status="confirmed",
                    description=(
                        f"Close {inr(last)} lost the {lookback}-bar low of "
                        f"{inr(prior_low)} by {inr(prior_low - last)} on "
                        f"{vol:.1f}× average volume. "
                        f"Target {inr(target)} (2 ATR projection); invalidation {inr(stop)}."
                    ),
                    points=points_from_pivots(
                        ctx.candles,
                        [(ctx.last_i - lookback, prior_low, "Support"), (ctx.last_i, last, "Breakdown")],
                    ),
                    lines=[
                        horizontal_line(ctx.candles, prior_low, start, ctx.last_i, "breakout"),
                    ],
                    breakout=prior_low,
                    target=target,
                    stop=stop,
                    start_i=start,
                    rr_entry=last,
                )
            )
    return out


DETECTORS = [
    detect_head_shoulders,
    detect_inverse_head_shoulders,
    detect_double_top,
    detect_double_bottom,
    detect_triple_top,
    detect_triple_bottom,
    detect_triangle,
    detect_flags,
    detect_cup_handle,
    detect_rounding,
    detect_breakouts,
]