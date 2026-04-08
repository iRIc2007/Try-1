"""
config.py - Central configuration and constants for the BTC Signal Algorithm.

All tunable parameters are defined here so they can be adjusted in one place
without touching business logic in other modules.
"""

# Trading pair and timeframe
SYMBOL = "btcusdt"
INTERVAL = "15m"

# Indicator periods
EMA_PERIOD = 50
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Volume filter
VOLUME_MULTIPLIER = 1.5  # Current volume must exceed avg volume by this factor

# Risk management
ATR_PERIOD = 14
ATR_MULTIPLIER_SL = 1.8   # Stop-loss distance = ATR * this multiplier
RR_RATIO = 1.9             # Reward-to-risk ratio for take-profit calculation
MAX_TRADE_MINUTES = 30     # Maximum time to hold an open trade before cancelling
