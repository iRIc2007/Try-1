"""
signal_engine.py - Trade signal generation and risk management logic.

Responsibilities:
- Receive an enriched DataFrame (with indicators) from indicators.py.
- Evaluate entry conditions for LONG and SHORT signals based on:
    - Price relative to EMA (trend filter)
    - RSI overbought / oversold levels
    - Bollinger Band breakouts / mean-reversion touches
    - MACD crossover confirmation
    - Volume spike confirmation (config.VOLUME_MULTIPLIER)
- Calculate trade parameters when a signal fires:
    - Entry price
    - Stop-loss  = entry ± ATR * config.ATR_MULTIPLIER_SL
    - Take-profit = entry ± (stop distance * config.RR_RATIO)
    - Signal expiry based on config.MAX_TRADE_MINUTES
- Return a signal dictionary (or None) with keys:
    direction, entry, stop_loss, take_profit, timestamp, expiry
"""
