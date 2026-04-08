"""
signal_engine.py - Trade signal generation and risk management logic.

Pipeline:
  evaluate_signal(df_15m, df_1h)
      └─ compute_indicators()   → state dict
      └─ classify_market()      → gate ("TRENDING" / "RANGING" / "VOLATILE")
      └─ long / short criteria  → signal dict or None

Responsibilities:
- classify_market(): gate — returns "VOLATILE", "RANGING", or "TRENDING"
- evaluate_signal(): runs indicators, gate, then all-or-nothing entry logic
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from ta.trend import ADXIndicator, EMAIndicator
from ta.volatility import AverageTrueRange

from config import ATR_MULTIPLIER_SL, ATR_PERIOD, EMA_PERIOD, MAX_TRADE_MINUTES, RR_RATIO, VOLUME_MULTIPLIER
from indicators import compute_indicators

# ── Thresholds ────────────────────────────────────────────────────────────────

_ATR_VOLATILE_MULTIPLIER = 2.0   # current ATR > 2× 20-period mean → VOLATILE
_ATR_MEAN_WINDOW = 20

_ADX_PERIOD = 14
_ADX_RANGING_THRESHOLD = 20      # ADX < 20 → RANGING

_EMA_SLOPE_LOOKBACK = 6          # candles back to measure 1h EMA slope
_EMA_FLAT_THRESHOLD = 0.002      # <0.2% change over 6 × 1h candles → RANGING


def classify_market(df_15m: pd.DataFrame, state: dict) -> str:
    """
    Classify current market conditions; must be the first call in the signal
    pipeline.  If the result is not ``"TRENDING"`` no signal logic runs.

    Checks are evaluated in priority order:

    1. **VOLATILE** — ``state["atr"]`` exceeds 2× the 20-period mean of the
       full ATR series on ``df_15m``.
    2. **RANGING**  — ADX(14) on ``df_15m`` is below 20, *or* the 1h EMA(50)
       slope is flat (< 0.2 % change over the last 6 × 1h candles).  The slope
       check requires ``df_1h`` to be injected into ``state`` under the key
       ``"_df_1h"`` by the caller; if absent the slope check is skipped.
    3. **TRENDING** — neither condition above is met.

    Parameters
    ----------
    df_15m :
        DataFrame of closed 15m candles with columns
        ``[timestamp, open, high, low, close, volume]``.
    state :
        Dict produced by ``indicators.compute_indicators``.  Keys consumed:
        ``"atr"`` (float).  Optional key ``"_df_1h"`` (DataFrame) enables the
        1h EMA slope check; inject it before calling this function.

    Returns
    -------
    str
        One of ``"VOLATILE"``, ``"RANGING"``, or ``"TRENDING"``.
    """
    # Pull pre-computed ATR scalar from state — no recomputation needed.
    atr_current: float = state["atr"]

    high_15m  = df_15m["high"].astype("float64")
    low_15m   = df_15m["low"].astype("float64")
    close_15m = df_15m["close"].astype("float64")

    # ── 1. VOLATILE ───────────────────────────────────────────────────────────
    # Full ATR series needed to compute the 20-period mean; the scalar in state
    # is only the latest value, so we compute the series here.
    atr_series = AverageTrueRange(
        high=high_15m, low=low_15m, close=close_15m, window=ATR_PERIOD
    ).average_true_range().dropna()

    atr_mean_20 = float(np.mean(atr_series.iloc[-_ATR_MEAN_WINDOW:].to_numpy()))
    if atr_mean_20 > 0 and atr_current > _ATR_VOLATILE_MULTIPLIER * atr_mean_20:
        return "VOLATILE"

    # ── 2a. RANGING — ADX ────────────────────────────────────────────────────
    adx_series = ADXIndicator(
        high=high_15m, low=low_15m, close=close_15m, window=_ADX_PERIOD
    ).adx()
    adx_current = float(adx_series.iloc[-1])

    if adx_current < _ADX_RANGING_THRESHOLD:
        return "RANGING"

    # ── 2b. RANGING — 1h EMA slope ───────────────────────────────────────────
    # Requires the 1h DataFrame; caller injects it via state["_df_1h"].
    df_1h: Optional[pd.DataFrame] = state.get("_df_1h")
    if df_1h is not None and len(df_1h) >= EMA_PERIOD + _EMA_SLOPE_LOOKBACK:
        close_1h = df_1h["close"].astype("float64")
        ema_1h_series = EMAIndicator(
            close=close_1h, window=EMA_PERIOD
        ).ema_indicator()

        ema_now  = float(ema_1h_series.iloc[-1])
        ema_prev = float(ema_1h_series.iloc[-1 - _EMA_SLOPE_LOOKBACK])

        if ema_prev > 0 and abs(ema_now - ema_prev) / ema_prev < _EMA_FLAT_THRESHOLD:
            return "RANGING"

    # ── 3. TRENDING ───────────────────────────────────────────────────────────
    return "TRENDING"


# ── Signal evaluation ─────────────────────────────────────────────────────────

def evaluate_signal(df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> dict:
    """
    Run the full signal pipeline for one candle cycle.

    Steps
    -----
    1. Compute all indicators via ``indicators.compute_indicators``.
    2. Gate on market regime via ``classify_market``; abort if not TRENDING.
    3. Evaluate all five LONG criteria — all must be True (all-or-nothing).
    4. Evaluate all five SHORT criteria — all must be True (all-or-nothing).
    5. If a direction fires, compute entry / stop-loss / take-profit and return
       a fully populated signal dict.  If neither fires, return a no-signal dict.

    Parameters
    ----------
    df_15m : DataFrame of closed 15m candles.
    df_1h  : DataFrame of closed 1h  candles.

    Returns
    -------
    dict
        Always returns a dict.  Key ``"signal"`` is ``"LONG"``, ``"SHORT"``,
        or ``None``.
    """
    # ── Step 1: indicators ───────────────────────────────────────────────────
    state = compute_indicators(df_15m, df_1h)
    # Inject df_1h so classify_market can run the EMA slope check.
    state["_df_1h"] = df_1h

    # ── Step 2: market regime gate ───────────────────────────────────────────
    market_condition = classify_market(df_15m, state)
    if market_condition != "TRENDING":
        return {
            "signal": None,
            "market_condition": market_condition,
            "reason": "Market not in trending condition — no trade",
        }

    # Convenience aliases pulled directly from state.
    above_ema               = state["above_ema"]
    macd_just_flipped_pos   = state["macd_just_flipped_positive"]
    macd_just_flipped_neg   = state["macd_just_flipped_negative"]
    rsi                     = state["rsi"]
    bb_bounce_long          = state["bb_bounce_long"]
    bb_bounce_short         = state["bb_bounce_short"]
    volume_ratio            = state["volume_ratio"]
    price                   = state["price"]
    atr                     = state["atr"]

    # ── Step 3: LONG criteria (all five required) ────────────────────────────
    long_criteria = {
        "above_ema":                above_ema is True,
        "macd_just_flipped_positive": macd_just_flipped_pos is True,
        "rsi_in_range (35–55)":     35 <= rsi <= 55,
        "bb_bounce_long":           bb_bounce_long is True,
        "volume_ratio_ok":          volume_ratio >= VOLUME_MULTIPLIER,
    }
    long_triggered = all(long_criteria.values())

    # ── Step 4: SHORT criteria (all five required) ───────────────────────────
    short_criteria = {
        "above_ema_false":           above_ema is False,
        "macd_just_flipped_negative": macd_just_flipped_neg is True,
        "rsi_in_range (45–65)":      45 <= rsi <= 65,
        "bb_bounce_short":           bb_bounce_short is True,
        "volume_ratio_ok":           volume_ratio >= VOLUME_MULTIPLIER,
    }
    short_triggered = all(short_criteria.values())

    # ── Step 5: build signal dict ────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(minutes=MAX_TRADE_MINUTES)

    if long_triggered:
        stop_loss   = price - ATR_MULTIPLIER_SL * atr
        take_profit = price + (price - stop_loss) * RR_RATIO
        return {
            "signal":           "LONG",
            "market_condition": "TRENDING",
            "entry":            round(price, 2),
            "stop_loss":        round(stop_loss, 2),
            "take_profit":      round(take_profit, 2),
            "atr":              round(atr, 2),
            "rsi":              round(rsi, 2),
            "volume_ratio":     round(volume_ratio, 3),
            "criteria_met":     [k for k, v in long_criteria.items() if v],
            "conviction":       "HIGH (5/5)",
            "timestamp":        now,
            "expiry":           expiry,
        }

    if short_triggered:
        stop_loss   = price + ATR_MULTIPLIER_SL * atr
        take_profit = price - (stop_loss - price) * RR_RATIO
        return {
            "signal":           "SHORT",
            "market_condition": "TRENDING",
            "entry":            round(price, 2),
            "stop_loss":        round(stop_loss, 2),
            "take_profit":      round(take_profit, 2),
            "atr":              round(atr, 2),
            "rsi":              round(rsi, 2),
            "volume_ratio":     round(volume_ratio, 3),
            "criteria_met":     [k for k, v in short_criteria.items() if v],
            "conviction":       "HIGH (5/5)",
            "timestamp":        now,
            "expiry":           expiry,
        }

    # ── No signal ────────────────────────────────────────────────────────────
    return {
        "signal":           None,
        "market_condition": "TRENDING",
        "reason":           "Criteria not fully met",
    }
