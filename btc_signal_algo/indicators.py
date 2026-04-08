"""
indicators.py - Technical indicator calculations.

Responsibilities:
- Accept a pandas DataFrame of OHLCV candle data as input.
- Compute and return the following indicators using the `ta` library and/or
  numpy/pandas where appropriate:
    - EMA  (Exponential Moving Average) — period defined in config.EMA_PERIOD
    - RSI  (Relative Strength Index)    — period defined in config.RSI_PERIOD
    - Bollinger Bands (upper, middle, lower) — config.BB_PERIOD / config.BB_STD
    - MACD (line, signal, histogram)    — config.MACD_FAST/SLOW/SIGNAL
    - ATR  (Average True Range)         — config.ATR_PERIOD
    - Rolling average volume            — used with config.VOLUME_MULTIPLIER
- Return the enriched DataFrame with indicator columns appended.
"""
