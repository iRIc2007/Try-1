"""
data_feed.py - Live market data ingestion via Binance WebSocket.

Responsibilities:
- Open and maintain a WebSocket connection to the Binance kline stream
  for the configured symbol and interval (e.g., btcusdt@kline_15m).
- Parse incoming kline (candlestick) messages into structured records.
- Maintain a rolling DataFrame of closed candles for use by the signal engine.
- Handle reconnection logic on dropped connections.
- Expose a callback/hook so the signal engine is notified when a new candle closes.
"""
