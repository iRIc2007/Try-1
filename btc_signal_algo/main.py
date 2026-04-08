"""
main.py - Entry point for the BTC Signal Algorithm.

Responsibilities:
- Initialize the application and load environment variables.
- Start the WebSocket data feed to receive live BTC/USDT candlestick data.
- Orchestrate the pipeline: data ingestion -> indicator calculation -> signal evaluation.
- Trigger the notifier when a trade signal is generated.
- Schedule periodic tasks (e.g., heartbeat checks, reconnects) via the `schedule` library.
"""
