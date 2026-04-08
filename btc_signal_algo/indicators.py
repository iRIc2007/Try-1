"""
indicators.py - Technical indicator calculations.

Exposes a single entry point, ``compute_indicators(df_15m, df_1h)``, that
consumes the rolling DataFrames produced by ``data_feed`` and returns a flat
``state`` dict containing every value the signal engine needs to evaluate
entry conditions.
"""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange, BollingerBands

from config import (
    ATR_PERIOD,
    BB_PERIOD,
    BB_STD,
    EMA_PERIOD,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    RSI_PERIOD,
)

MIN_ROWS = 60


def compute_indicators(df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> dict:
    """
    Compute the full set of indicator values needed for signal evaluation.

    Parameters
    ----------
    df_15m : DataFrame of closed 15m candles (columns: timestamp, open, high,
             low, close, volume).
    df_1h  : DataFrame of closed 1h  candles (same columns).

    Returns
    -------
    dict
        Flat ``state`` dictionary consumed by ``signal_engine``.

    Raises
    ------
    ValueError
        If either input DataFrame has fewer than 60 rows.
    """
    if len(df_15m) < MIN_ROWS:
        raise ValueError(
            f"df_15m has {len(df_15m)} rows; at least {MIN_ROWS} are required."
        )
    if len(df_1h) < MIN_ROWS:
        raise ValueError(
            f"df_1h has {len(df_1h)} rows; at least {MIN_ROWS} are required."
        )

    close_15m = df_15m["close"].astype("float64")
    high_15m = df_15m["high"].astype("float64")
    low_15m = df_15m["low"].astype("float64")
    volume_15m = df_15m["volume"].astype("float64")

    # ── MACD (15m) ───────────────────────────────────────────────────────
    macd = MACD(
        close=close_15m,
        window_fast=MACD_FAST,
        window_slow=MACD_SLOW,
        window_sign=MACD_SIGNAL,
    )
    macd_line = macd.macd()
    signal_line = macd.macd_signal()
    histogram = macd.macd_diff()

    macd_just_flipped_positive = bool(
        histogram.iloc[-1] > 0 and histogram.iloc[-2] <= 0
    )
    macd_just_flipped_negative = bool(
        histogram.iloc[-1] < 0 and histogram.iloc[-2] >= 0
    )

    # ── RSI (15m) ────────────────────────────────────────────────────────
    rsi_series = RSIIndicator(close=close_15m, window=RSI_PERIOD).rsi()
    rsi = float(rsi_series.iloc[-1])

    # ── Bollinger Bands (15m) ────────────────────────────────────────────
    bb = BollingerBands(close=close_15m, window=BB_PERIOD, window_dev=BB_STD)
    bb_upper = bb.bollinger_hband()
    bb_lower = bb.bollinger_lband()
    bb_width_series = bb.bollinger_wband()

    bb_bounce_long = bool(
        close_15m.iloc[-2] <= bb_lower.iloc[-2]
        and close_15m.iloc[-1] > bb_lower.iloc[-1]
    )
    bb_bounce_short = bool(
        close_15m.iloc[-2] >= bb_upper.iloc[-2]
        and close_15m.iloc[-1] < bb_upper.iloc[-1]
    )

    # ── Volume ratio (15m) ───────────────────────────────────────────────
    avg_volume_20 = float(np.mean(volume_15m.iloc[-20:-1].to_numpy()))
    volume_ratio = (
        float(volume_15m.iloc[-1] / avg_volume_20) if avg_volume_20 > 0 else 0.0
    )

    # ── ATR (15m) ────────────────────────────────────────────────────────
    atr_series = AverageTrueRange(
        high=high_15m, low=low_15m, close=close_15m, window=ATR_PERIOD
    ).average_true_range()
    atr = float(atr_series.iloc[-1])

    # ── Price (15m) ──────────────────────────────────────────────────────
    price = float(close_15m.iloc[-1])

    # ── EMA on 1h ────────────────────────────────────────────────────────
    close_1h = df_1h["close"].astype("float64")
    ema_1h_series = EMAIndicator(close=close_1h, window=EMA_PERIOD).ema_indicator()
    ema_1h = float(ema_1h_series.iloc[-1])
    above_ema = bool(close_1h.iloc[-1] > ema_1h)

    # ── Assemble state ───────────────────────────────────────────────────
    state = {
        # MACD
        "macd_line": float(macd_line.iloc[-1]),
        "signal_line": float(signal_line.iloc[-1]),
        "histogram": float(histogram.iloc[-1]),
        "macd_just_flipped_positive": macd_just_flipped_positive,
        "macd_just_flipped_negative": macd_just_flipped_negative,
        # RSI
        "rsi": rsi,
        # Bollinger Bands
        "bb_upper": float(bb_upper.iloc[-1]),
        "bb_lower": float(bb_lower.iloc[-1]),
        "bb_width": float(bb_width_series.iloc[-1]),
        "bb_bounce_long": bb_bounce_long,
        "bb_bounce_short": bb_bounce_short,
        # Volume
        "volume_ratio": volume_ratio,
        # ATR & price
        "atr": atr,
        "price": price,
        # 1h trend
        "ema_1h": ema_1h,
        "above_ema": above_ema,
        # Metadata
        "computed_at": datetime.now(timezone.utc),
    }

    return state
