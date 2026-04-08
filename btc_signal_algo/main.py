"""
main.py - Entry point for the BTC Signal Algorithm.

Run with:  python main.py

Startup sequence:
  1. Bootstrap historical 15m + 1h candles via Binance REST API.
  2. Start the WebSocket listener in a background daemon thread.
  3. Schedule evaluate_signal() at :01 past every quarter-hour.
  4. Run the scheduler loop until KeyboardInterrupt.
"""

import logging
import sys
import time
from datetime import datetime, timezone

import schedule

import data_feed
from notifier import send_alert, send_suppression_notice
from signal_engine import evaluate_signal

# ── Logging setup ─────────────────────────────────────────────────────────────
# Writes to both the console and algo.log in the same directory.

_LOG_FILE = "algo.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")

_MIN_ROWS = 60


# ── Scheduled job ─────────────────────────────────────────────────────────────

def _run_signal_cycle() -> None:
    """
    Called at :01 past every quarter-hour.

    Steps
    -----
    a. Fetch current DataFrames from the data feed.
    b. Guard: skip if either has fewer than 60 rows.
    c. Run the full signal pipeline.
    d. Print result to console with UTC timestamp.
    e. Send email alert if a signal fired.
    f. Send suppression notice (throttled) if market is not TRENDING.
    """
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    df_15m = data_feed.get_15m_df()
    df_1h  = data_feed.get_1h_df()

    # ── Guard: insufficient data ──────────────────────────────────────────────
    if len(df_15m) < _MIN_ROWS or len(df_1h) < _MIN_ROWS:
        msg = (
            f"[{now_utc}] Skipping cycle — insufficient data "
            f"(15m rows: {len(df_15m)}, 1h rows: {len(df_1h)}, need {_MIN_ROWS})."
        )
        print(msg)
        logger.warning(msg)
        return

    # ── Evaluate signal ───────────────────────────────────────────────────────
    try:
        result = evaluate_signal(df_15m, df_1h)
    except Exception as exc:
        logger.error("evaluate_signal raised an exception: %s", exc, exc_info=True)
        return

    signal          = result.get("signal")
    market_condition = result.get("market_condition", "UNKNOWN")
    reason          = result.get("reason", "")

    # ── Console output ────────────────────────────────────────────────────────
    print()
    print(f"{'─' * 60}")
    print(f"  BTC Signal Algo — {now_utc}")
    print(f"{'─' * 60}")
    print(f"  Market condition : {market_condition}")
    if signal:
        print(f"  Signal           : {signal}")
        print(f"  Entry            : ${result.get('entry')}")
        print(f"  Stop loss        : ${result.get('stop_loss')}")
        print(f"  Take profit      : ${result.get('take_profit')}")
        print(f"  RSI              : {result.get('rsi')}")
        print(f"  Volume ratio     : {result.get('volume_ratio')}x")
        print(f"  Conviction       : {result.get('conviction')}")
        print(f"  Expiry           : {result.get('expiry')}")
    else:
        print(f"  Signal           : None")
        print(f"  Reason           : {reason}")
    print(f"{'─' * 60}")
    print()

    # ── File log (compact one-liner per cycle) ────────────────────────────────
    log_line = (
        f"cycle | market={market_condition} | signal={signal} | reason={reason}"
    )
    if signal:
        log_line = (
            f"cycle | market={market_condition} | signal={signal} | "
            f"entry={result.get('entry')} | SL={result.get('stop_loss')} | "
            f"TP={result.get('take_profit')} | rsi={result.get('rsi')} | "
            f"vol_ratio={result.get('volume_ratio')}"
        )
    logger.info(log_line)

    # ── Notifications ─────────────────────────────────────────────────────────
    if signal is not None:
        send_alert(result)
    elif market_condition != "TRENDING":
        send_suppression_notice(
            reason=reason or "No reason provided",
            market_condition=market_condition,
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 60)
    logger.info("BTC Signal Algo — starting up")
    logger.info("=" * 60)

    # ── Step 1: bootstrap historical candles ──────────────────────────────────
    logger.info("Fetching historical candles via REST API…")
    data_feed.start()   # bootstraps REST data then launches WebSocket thread

    # Give the REST bootstrap a moment to finish before reporting counts.
    time.sleep(2)

    df_15m = data_feed.get_15m_df()
    df_1h  = data_feed.get_1h_df()
    print(
        f"\n  Historical data loaded:\n"
        f"    15m candles : {len(df_15m)}\n"
        f"    1h  candles : {len(df_1h)}\n"
    )
    logger.info(
        "Bootstrap complete — 15m: %d rows, 1h: %d rows.", len(df_15m), len(df_1h)
    )

    # ── Step 2: WebSocket already started inside data_feed.start() ───────────
    logger.info("WebSocket listener running in background thread.")

    # ── Step 3: schedule at :01 past every quarter-hour ──────────────────────
    schedule.every().hour.at(":01").do(_run_signal_cycle)
    schedule.every().hour.at(":16").do(_run_signal_cycle)
    schedule.every().hour.at(":31").do(_run_signal_cycle)
    schedule.every().hour.at(":46").do(_run_signal_cycle)

    logger.info(
        "Scheduler armed — signal checks at :01, :16, :31, :46 past each hour."
    )
    print("  Scheduler running. Press Ctrl+C to stop.\n")

    # ── Step 4 & 5: run loop with clean shutdown ──────────────────────────────
    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n  Shutdown requested — stopping.")
        logger.info("KeyboardInterrupt received — algo shutting down cleanly.")


if __name__ == "__main__":
    main()
