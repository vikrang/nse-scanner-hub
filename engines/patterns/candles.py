"""Candlestick patterns evaluated on the most recent bars (stdlib port)."""
from __future__ import annotations

from .geometry import CANDLE_PATTERN_MIN_CONFIDENCE, Context, build_pattern, points_from_pivots


def inr(value: float) -> str:
    return f"₹{value:,.2f}"


def _candle(ctx: Context, i: int) -> dict:
    o = float(ctx.open[i])
    h = float(ctx.high[i])
    l = float(ctx.low[i])
    c = float(ctx.close[i])
    rng = max(h - l, 1e-9)
    return {
        "i": i,
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "body": abs(c - o),
        "range": rng,
        "upper": h - max(o, c),
        "lower": min(o, c) - l,
        "bull": c > o,
    }


def _build(
    ctx: Context,
    *,
    name: str,
    direction: str,
    confidence: float,
    start_i: int,
    description: str,
    marker_price: float | None = None,
    rr: bool = True,
) -> dict:
    last = ctx.last_close
    atr = ctx.atr
    if direction == "bullish":
        stop = (
            min(ctx.low[start_i:ctx.last_i + 1]) - 0.2 * atr
            if rr
            else None
        )
        target = last + max(1.8 * atr, (last - stop) * 1.8) if rr and stop else None
        marker_label = name
        point_value = float(ctx.low[ctx.last_i]) * 0.998
    elif direction == "bearish":
        stop = (
            max(ctx.high[start_i:ctx.last_i + 1]) + 0.2 * atr
            if rr
            else None
        )
        target = last - max(1.8 * atr, (stop - last) * 1.8) if rr and stop else None
        marker_label = name
        point_value = float(ctx.high[ctx.last_i]) * 1.002
    else:
        stop = target = None
        marker_label = name
        point_value = float(ctx.high[ctx.last_i])
    return build_pattern(
        ctx,
        name=name,
        category="candlestick",
        direction=direction,
        confidence=confidence,
        status="confirmed",
        description=description,
        points=points_from_pivots(ctx.candles, [(ctx.last_i, point_value, marker_label)]),
        lines=[],
        breakout=None,
        target=target,
        stop=stop,
        start_i=start_i,
        rr_entry=last if rr else None,
    )


def detect_candles(ctx: Context) -> list[dict]:
    if ctx.last_i < 5:
        return []
    out: list[dict] = []
    last = _candle(ctx, ctx.last_i)
    prev = _candle(ctx, ctx.last_i - 1)
    prev2 = _candle(ctx, ctx.last_i - 2)
    atr = ctx.atr
    trend = ctx.trend
    vol_bonus = 5 if ctx.vol_ratio >= 1.3 else 0
    last_price = inr(last["c"])

    # --- Engulfing ---------------------------------------------------------- #
    if (
        prev["bull"] != last["bull"]
        and last["body"] >= prev["body"] * 1.05
        and last["body"] >= atr * 0.35
    ):
        if (
            not last["bull"]
            and last["o"] >= prev["c"]
            and last["c"] <= prev["o"]
            and trend in ("up", "range")
        ):
            conf = 53 + (7 if trend == "up" else 0) + vol_bonus + min(5, last["body"] / atr * 2)
            out.append(
                _build(
                    ctx,
                    name="Bearish Engulfing",
                    direction="bearish",
                    confidence=conf,
                    start_i=ctx.last_i - 1,
                    description=(
                        f"Last candle opened at {inr(last['o'])}, closed at {last_price} and "
                        f"fully engulfed the prior {inr(prev['body'])} body. "
                        f"Rejection high {inr(last['h'])}; invalidation above it."
                    ),
                    marker_price=last["h"],
                )
            )
        elif (
            last["bull"]
            and last["o"] <= prev["c"]
            and last["c"] >= prev["o"]
            and trend in ("down", "range")
        ):
            conf = 53 + (7 if trend == "down" else 0) + vol_bonus + min(5, last["body"] / atr * 2)
            out.append(
                _build(
                    ctx,
                    name="Bullish Engulfing",
                    direction="bullish",
                    confidence=conf,
                    start_i=ctx.last_i - 1,
                    description=(
                        f"Last candle opened at {inr(last['o'])}, closed at {last_price} and "
                        f"reclaimed the prior {inr(prev['body'])} body. "
                        f"Low {inr(last['l'])} is the invalidation level."
                    ),
                    marker_price=last["l"],
                )
            )

    # --- Hammer / hanging man / shooting star ------------------------------- #
    small_upper = last["upper"] <= max(last["body"] * 0.45, last["range"] * 0.18)
    small_lower = last["lower"] <= max(last["body"] * 0.45, last["range"] * 0.18)
    if last["lower"] >= max(2 * last["body"], last["range"] * 0.55) and small_upper:
        if trend == "down" and last["body"] > 0:
            conf = 55 + vol_bonus + min(7, last["lower"] / atr * 2.5)
            out.append(
                _build(
                    ctx,
                    name="Hammer",
                    direction="bullish",
                    confidence=conf,
                    start_i=ctx.last_i,
                    description=(
                        f"Lower wick of {inr(last['lower'])} rejected the sell-off and the "
                        f"close {last_price} finished in the top of the range. "
                        f"Stop below the wick at {inr(last['l'])}."
                    ),
                    marker_price=last["l"],
                )
            )
        elif trend == "up":
            conf = 51 + vol_bonus + min(5, last["lower"] / atr * 2)
            out.append(
                _build(
                    ctx,
                    name="Hanging Man",
                    direction="bearish",
                    confidence=conf,
                    start_i=ctx.last_i,
                    description=(
                        f"Long lower wick of {inr(last['lower'])} appeared after an advance. "
                        f"Close {last_price}; a break of {inr(last['l'])} would confirm supply."
                    ),
                    marker_price=last["h"],
                )
            )

    if (
        last["upper"] >= max(2 * last["body"], last["range"] * 0.55)
        and small_lower
        and last["body"] > 0
    ):
        if trend == "up" and last["c"] < last["h"] - 0.45 * last["range"]:
            conf = 56 + vol_bonus + min(7, last["upper"] / atr * 2.5)
            out.append(
                _build(
                    ctx,
                    name="Shooting Star",
                    direction="bearish",
                    confidence=conf,
                    start_i=ctx.last_i,
                    description=(
                        f"Upper wick of {inr(last['upper'])} faded into the close at {last_price}. "
                        f"High {inr(last['h'])} is the invalidation level."
                    ),
                    marker_price=last["h"],
                )
            )
        elif trend == "down":
            conf = 52 + vol_bonus + min(5, last["upper"] / atr * 2)
            out.append(
                _build(
                    ctx,
                    name="Inverted Hammer",
                    direction="bullish",
                    confidence=conf,
                    start_i=ctx.last_i,
                    description=(
                        f"Buyers tested {inr(last['h'])} and held the close at {last_price} "
                        f"after a decline. Confirmation needed above the wick."
                    ),
                    marker_price=last["l"],
                )
            )

    # --- Morning / evening star -------------------------------------------- #
    if (
        prev2["body"] >= atr * 0.45
        and prev["body"] <= max(prev2["body"] * 0.45, atr * 0.5)
        and last["body"] >= atr * 0.4
        and not prev2["bull"]
        and last["bull"]
        and last["c"] > (prev2["o"] + prev2["c"]) / 2
        and trend in ("down", "range")
    ):
        conf = 57 + (6 if trend == "down" else 0) + vol_bonus
        out.append(
            _build(
                ctx,
                name="Morning Star",
                direction="bullish",
                confidence=conf,
                start_i=ctx.last_i - 2,
                description=(
                    f"Three-bar reversal: {inr(prev2['body'])} down body, small pause at "
                    f"{inr(prev['c'])}, then a close at {last_price} above the midpoint. "
                    f"Stop under {inr(min(prev2['l'], prev['l'], last['l']))}."
                ),
                marker_price=last["l"],
            )
        )
    if (
        prev2["body"] >= atr * 0.45
        and prev["body"] <= max(prev2["body"] * 0.45, atr * 0.5)
        and last["body"] >= atr * 0.4
        and prev2["bull"]
        and not last["bull"]
        and last["c"] < (prev2["o"] + prev2["c"]) / 2
        and trend in ("up", "range")
    ):
        conf = 57 + (6 if trend == "up" else 0) + vol_bonus
        out.append(
            _build(
                ctx,
                name="Evening Star",
                direction="bearish",
                confidence=conf,
                start_i=ctx.last_i - 2,
                description=(
                    f"Three-bar top: {inr(prev2['body'])} up body, stall near "
                    f"{inr(prev['h'])}, then a close at {last_price} below the midpoint. "
                    f"Stop above {inr(max(prev2['h'], prev['h'], last['h']))}."
                ),
                marker_price=last["h"],
            )
        )

    # --- Piercing / dark cloud --------------------------------------------- #
    if (
        not prev["bull"]
        and last["bull"]
        and last["o"] < prev["c"]
        and last["c"] > (prev["o"] + prev["c"]) / 2
        and last["c"] < prev["o"]
        and prev["body"] >= atr * 0.35
    ):
        conf = 53 + vol_bonus + (5 if trend == "down" else 0)
        out.append(
            _build(
                ctx,
                name="Piercing Line",
                direction="bullish",
                confidence=conf,
                start_i=ctx.last_i - 1,
                description=(
                    f"Price opened below {inr(prev['c'])} and closed at {last_price}, "
                    f"above the midpoint of the prior down body."
                ),
                marker_price=last["l"],
            )
        )
    if (
        prev["bull"]
        and not last["bull"]
        and last["o"] > prev["c"]
        and last["c"] < (prev["o"] + prev["c"]) / 2
        and last["c"] > prev["o"]
        and prev["body"] >= atr * 0.35
    ):
        conf = 53 + vol_bonus + (5 if trend == "up" else 0)
        out.append(
            _build(
                ctx,
                name="Dark Cloud Cover",
                direction="bearish",
                confidence=conf,
                start_i=ctx.last_i - 1,
                description=(
                    f"Price opened above {inr(prev['c'])} but closed at {last_price}, "
                    f"below the midpoint of the prior up body."
                ),
                marker_price=last["h"],
            )
        )

    # --- Harami ------------------------------------------------------------- #
    if (
        prev["body"] >= last["body"] * 1.6
        and min(prev["o"], prev["c"]) <= max(last["o"], last["c"])
        and max(prev["o"], prev["c"]) >= min(last["o"], last["c"])
        and prev["bull"] != last["bull"]
    ):
        if prev["bull"] and trend == "up":
            conf = 51 + vol_bonus
            out.append(
                _build(
                    ctx,
                    name="Bearish Harami",
                    direction="bearish",
                    confidence=conf,
                    start_i=ctx.last_i - 1,
                    description=(
                        f"An inside candle at {last_price} stalled the advance; "
                        f"a close under {inr(last['l'])} confirms the pause."
                    ),
                    marker_price=last["h"],
                )
            )
        elif not prev["bull"] and trend == "down":
            conf = 51 + vol_bonus
            out.append(
                _build(
                    ctx,
                    name="Bullish Harami",
                    direction="bullish",
                    confidence=conf,
                    start_i=ctx.last_i - 1,
                    description=(
                        f"An inside candle at {last_price} halted the decline; "
                        f"a close over {inr(last['h'])} confirms the pause."
                    ),
                    marker_price=last["l"],
                )
            )

    # --- Three soldiers / crows -------------------------------------------- #
    three = [_candle(ctx, ctx.last_i - 2), prev, last]
    if all(c["bull"] for c in three) and all(
        c["body"] >= atr * 0.35 and c["upper"] <= c["body"] * 0.5 for c in three
    ):
        if (
            three[0]["c"] < three[1]["c"] < three[2]["c"]
            and three[0]["o"] >= three[1]["o"] - 0.1 * atr
            and three[1]["o"] >= three[2]["o"] - 0.1 * atr
            and trend in ("down", "range", "up")
        ):
            conf = 55 + vol_bonus + (4 if trend == "down" else 0)
            out.append(
                _build(
                    ctx,
                    name="Three White Soldiers",
                    direction="bullish",
                    confidence=conf,
                    start_i=ctx.last_i - 2,
                    description=(
                        f"Three consecutive green closes ({inr(three[0]['c'])} → "
                        f"{inr(three[1]['c'])} → {last_price}) with small upper wicks."
                    ),
                    marker_price=last["l"],
                )
            )
    if all(not c["bull"] for c in three) and all(
        c["body"] >= atr * 0.35 and c["lower"] <= c["body"] * 0.5 for c in three
    ):
        if (
            three[0]["c"] > three[1]["c"] > three[2]["c"]
            and three[0]["o"] <= three[1]["o"] + 0.1 * atr
            and three[1]["o"] <= three[2]["o"] + 0.1 * atr
        ):
            conf = 55 + vol_bonus
            out.append(
                _build(
                    ctx,
                    name="Three Black Crows",
                    direction="bearish",
                    confidence=conf,
                    start_i=ctx.last_i - 2,
                    description=(
                        f"Three consecutive red closes ({inr(three[0]['c'])} → "
                        f"{inr(three[1]['c'])} → {last_price}) with small lower wicks."
                    ),
                    marker_price=last["h"],
                )
            )

    # --- Doji --------------------------------------------------------------- #
    if last["body"] <= last["range"] * 0.09 and last["range"] >= atr * 0.5 and trend != "range":
        conf = 43 + vol_bonus + (3 if ctx.vol_ratio >= 1.5 else 0)
        out.append(
            _build(
                ctx,
                name="Doji",
                direction="neutral",
                confidence=conf,
                start_i=ctx.last_i,
                description=(
                    f"Open {inr(last['o'])} and close {inr(last['c'])} are effectively equal "
                    f"inside a {inr(last['range'])} range — indecision after a "
                    f"{'rally' if trend == 'up' else 'decline'}."
                ),
                marker_price=last["h"],
                rr=False,
            )
        )

    # --- Marubozu ----------------------------------------------------------- #
    if last["body"] >= last["range"] * 0.9 and last["body"] >= atr * 1.1:
        direction = "bullish" if last["bull"] else "bearish"
        conf = 52 + vol_bonus + min(5, last["body"] / atr * 2)
        out.append(
            _build(
                ctx,
                name="Bullish Marubozu" if last["bull"] else "Bearish Marubozu",
                direction=direction,
                confidence=conf,
                start_i=ctx.last_i,
                description=(
                    f"Full-body {inr(last['body'])} candle closed at {last_price} with "
                    f"almost no wick — {'buyers' if last['bull'] else 'sellers'} controlled "
                    f"the entire session."
                ),
                marker_price=last["l"] if last["bull"] else last["h"],
            )
        )

    return [p for p in out if p["confidence"] >= CANDLE_PATTERN_MIN_CONFIDENCE]