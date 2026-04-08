"""
signal_engine.py - Trade signal generation and risk management logic.

Pipeline entry point is ``classify_market(df_15m, state)``, which acts as a
gate: only a "TRENDING" result allows signal logic to proceed.

Responsibilities:
- classify_market(): gate function — returns "VOLATILE", "RANGING", or "TRENDING"
- (forthcoming) evaluate_signal(): long/short entry conditions and risk params
"""

from typing import Optional

import numpy as np
import pandas as pd
from ta.trend import ADXIndicator, EMAIndicator
from ta.volatility import AverageTrueRange

from config import ATR_PERIOD, EMA_PERIOD

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
