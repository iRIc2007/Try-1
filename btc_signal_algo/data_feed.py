"""
data_feed.py - Live market data ingestion via Binance WebSocket.

Connects to the Binance public kline stream for BTC/USDT 15m candles, maintains
a rolling buffer of the last 100 closed candles, and pre-populates both the 15m
and 1h buffers from the REST API on startup so the signal engine has data
immediately without waiting for candles to close naturally.
"""

import json
import logging
import threading
import time

import pandas as pd
import requests
import websocket

from config import SYMBOL, INTERVAL

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

WS_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL}@kline_{INTERVAL}"
REST_URL = "https://api.binance.com/api/v3/klines"
CANDLE_LIMIT = 100
MAX_RETRIES = 5
_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

# ── Shared state (protected by _lock) ────────────────────────────────────────

_lock = threading.Lock()
_df_15m: pd.DataFrame = pd.DataFrame(columns=_COLUMNS)
_df_1h: pd.DataFrame = pd.DataFrame(columns=_COLUMNS)


# ── Public accessors ─────────────────────────────────────────────────────────

def get_15m_df() -> pd.DataFrame:
    """Return a snapshot of the current 15m closed-candle DataFrame."""
    with _lock:
        return _df_15m.copy()


def get_1h_df() -> pd.DataFrame:
    """Return a snapshot of the current 1h closed-candle DataFrame."""
    with _lock:
        return _df_1h.copy()


# ── REST bootstrap ───────────────────────────────────────────────────────────

def _parse_rest_candles(raw: list) -> pd.DataFrame:
    """Convert a Binance REST klines response to a typed DataFrame."""
    records = [
        {
            "timestamp": pd.Timestamp(row[0], unit="ms", tz="UTC"),
            "open":  float(row[1]),
            "high":  float(row[2]),
            "low":   float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in raw
    ]
    df = pd.DataFrame(records, columns=_COLUMNS)
    df = df.astype({"open": "float64", "high": "float64",
                    "low": "float64", "close": "float64", "volume": "float64"})
    return df


def _fetch_rest_candles(interval: str) -> pd.DataFrame:
    """Fetch the last CANDLE_LIMIT closed candles from the Binance REST API."""
    # Request one extra candle; drop the last (currently open) one.
    params = {
        "symbol": SYMBOL.upper(),
        "interval": interval,
        "limit": CANDLE_LIMIT + 1,
    }
    resp = requests.get(REST_URL, params=params, timeout=10)
    resp.raise_for_status()
    rows = resp.json()
    # Drop the still-open candle (last row).
    closed = rows[:-1]
    return _parse_rest_candles(closed)


def _bootstrap() -> None:
    """Pre-populate 15m and 1h DataFrames from the REST API at startup."""
    global _df_15m, _df_1h
    try:
        df_15 = _fetch_rest_candles(INTERVAL)
        df_1h = _fetch_rest_candles("1h")
        with _lock:
            _df_15m = df_15
            _df_1h = df_1h
        logger.info(
            "Bootstrap complete: %d × 15m candles, %d × 1h candles loaded.",
            len(_df_15m), len(_df_1h),
        )
    except Exception as exc:
        logger.error("Bootstrap failed: %s", exc)


# ── WebSocket callbacks ───────────────────────────────────────────────────────

def _on_message(_ws, message: str) -> None:
    global _df_15m
    data = json.loads(message)
    kline = data.get("k", {})

    is_candle_closed: bool = kline.get("x", False)
    if not is_candle_closed:
        return  # Ignore open (in-progress) candles.

    record = {
        "timestamp": pd.Timestamp(kline["t"], unit="ms", tz="UTC"),
        "open":   float(kline["o"]),
        "high":   float(kline["h"]),
        "low":    float(kline["l"]),
        "close":  float(kline["c"]),
        "volume": float(kline["v"]),
    }

    new_row = pd.DataFrame([record], columns=_COLUMNS).astype(
        {"open": "float64", "high": "float64",
         "low": "float64", "close": "float64", "volume": "float64"}
    )

    with _lock:
        _df_15m = (
            pd.concat([_df_15m, new_row], ignore_index=True)
            .tail(CANDLE_LIMIT)
            .reset_index(drop=True)
        )

    logger.debug("New 15m candle closed: %s  close=%.2f", record["timestamp"], record["close"])


def _on_error(_ws, error) -> None:
    logger.error("WebSocket error: %s", error)


def _on_close(_ws, close_status_code, close_msg) -> None:
    logger.warning("WebSocket closed (status=%s): %s", close_status_code, close_msg)


def _on_open(_ws) -> None:
    logger.info("WebSocket connected to %s", WS_URL)


# ── Reconnect loop ────────────────────────────────────────────────────────────

def _run_with_reconnect() -> None:
    """Run the WebSocket client with exponential-backoff reconnect (max 5 retries)."""
    attempt = 0
    while attempt <= MAX_RETRIES:
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=_on_open,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as exc:
            logger.error("WebSocket exception: %s", exc)

        if attempt >= MAX_RETRIES:
            logger.critical(
                "WebSocket: max retries (%d) reached. Giving up.", MAX_RETRIES
            )
            break

        backoff = 2 ** attempt          # 1s, 2s, 4s, 8s, 16s
        attempt += 1
        logger.info(
            "WebSocket reconnect attempt %d/%d in %ds…",
            attempt, MAX_RETRIES, backoff,
        )
        time.sleep(backoff)


# ── Public entry point ────────────────────────────────────────────────────────

def start() -> None:
    """
    Bootstrap historical data from the REST API, then start the WebSocket
    listener in a daemon background thread.
    """
    _bootstrap()

    thread = threading.Thread(target=_run_with_reconnect, name="ws-data-feed", daemon=True)
    thread.start()
    logger.info("Data feed background thread started.")
