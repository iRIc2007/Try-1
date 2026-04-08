"""
test_run.py - Dry-run test suite for the BTC Signal Algorithm pipeline.

Run from inside btc_signal_algo/:
    python test_run.py

No live network connections are made.  The send_alert dry-run test
monkey-patches notifier._send so the email body is printed to the console
instead of dispatched via SMTP.

Tests
-----
1. generate_mock_data()      — sanity-check the mock DataFrame generator
2. compute_indicators()      — key presence, no NaN in numeric fields
3. classify_market()         — three scenarios: TRENDING / RANGING / VOLATILE
4. evaluate_signal()         — correct return-dict structure on mock data
5. send_alert() dry-run      — formatted body printed, no SMTP call made
"""

import sys
import traceback
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

# ── pipeline imports ──────────────────────────────────────────────────────────
import notifier
from indicators import compute_indicators
from signal_engine import classify_market, evaluate_signal

# ── test helpers ──────────────────────────────────────────────────────────────

_PASS = "PASS"
_FAIL = "FAIL"
_results: list[tuple[str, str, str]] = []   # (name, status, detail)


def _record(name: str, passed: bool, detail: str = "") -> None:
    status = _PASS if passed else _FAIL
    _results.append((name, status, detail))
    marker = "✅" if passed else "❌"
    print(f"  {marker}  [{status}]  {name}" + (f"  — {detail}" if detail else ""))


def _section(title: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


# ── Test 1: mock data generator ───────────────────────────────────────────────

def generate_mock_data(n: int = 100, start_price: float = 68_000.0,
                       trend_per_bar: float = 10.0,
                       interval_minutes: int = 15) -> pd.DataFrame:
    """
    Generate a realistic-looking BTC OHLCV DataFrame.

    Parameters
    ----------
    n                : Number of candles to produce.
    start_price      : Opening price of the first candle.
    trend_per_bar    : Average price drift per candle (positive = upward).
    interval_minutes : Candle width in minutes (15 for 15m, 60 for 1h).

    Returns
    -------
    pd.DataFrame with columns [timestamp, open, high, low, close, volume],
    all price columns as float64.
    """
    rng = np.random.default_rng(seed=42)

    closes = np.empty(n, dtype=np.float64)
    closes[0] = start_price
    for i in range(1, n):
        # Random walk with slight upward drift
        change = trend_per_bar + rng.normal(0, start_price * 0.003)
        closes[i] = max(closes[i - 1] + change, 1.0)

    # High/Low: ±0.5–1.0% of close
    spread_pct = rng.uniform(0.005, 0.010, size=n)
    highs  = closes * (1 + spread_pct / 2)
    lows   = closes * (1 - spread_pct / 2)
    opens  = np.roll(closes, 1)
    opens[0] = start_price

    # Volume: base ~10 BTC with ±30% normal variance
    volumes = rng.normal(loc=10.0, scale=3.0, size=n).clip(min=0.1)

    start_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    timestamps = [
        start_ts + timedelta(minutes=interval_minutes * i) for i in range(n)
    ]

    df = pd.DataFrame({
        "timestamp": timestamps,
        "open":      opens.astype("float64"),
        "high":      highs.astype("float64"),
        "low":       lows.astype("float64"),
        "close":     closes.astype("float64"),
        "volume":    volumes.astype("float64"),
    })
    return df


def test_mock_data_generator() -> None:
    _section("Test 1 — Mock data generator")

    df15 = generate_mock_data(n=100, interval_minutes=15)
    df1h = generate_mock_data(n=100, interval_minutes=60)

    _record("15m DataFrame has 100 rows",        len(df15) == 100)
    _record("1h  DataFrame has 100 rows",         len(df1h) == 100)
    _record("Expected columns present (15m)",
            set(df15.columns) == {"timestamp", "open", "high", "low", "close", "volume"})
    _record("Price columns are float64 (15m)",
            all(df15[c].dtype == np.float64 for c in ["open", "high", "low", "close", "volume"]))
    _record("High >= Close >= Low for all rows (15m)",
            (df15["high"] >= df15["close"]).all() and (df15["close"] >= df15["low"]).all())
    _record("Upward trend present (last close > first close, 15m)",
            df15["close"].iloc[-1] > df15["close"].iloc[0])

    print(f"\n  15m sample (last 3 rows):\n{df15.tail(3).to_string(index=False)}")
    print(f"\n  1h  sample (last 3 rows):\n{df1h.tail(3).to_string(index=False)}")


# ── Test 2: compute_indicators ────────────────────────────────────────────────

_EXPECTED_STATE_KEYS = {
    "macd_line", "signal_line", "histogram",
    "macd_just_flipped_positive", "macd_just_flipped_negative",
    "rsi",
    "bb_upper", "bb_lower", "bb_width",
    "bb_bounce_long", "bb_bounce_short",
    "volume_ratio",
    "atr", "price",
    "ema_1h", "above_ema",
    "computed_at",
}

_NUMERIC_KEYS = {
    "macd_line", "signal_line", "histogram",
    "rsi", "bb_upper", "bb_lower", "bb_width",
    "volume_ratio", "atr", "price", "ema_1h",
}


def test_compute_indicators() -> None:
    _section("Test 2 — compute_indicators()")

    df15 = generate_mock_data(n=100, interval_minutes=15)
    df1h = generate_mock_data(n=100, interval_minutes=60)

    try:
        state = compute_indicators(df15, df1h)
    except Exception as exc:
        _record("compute_indicators() ran without exception", False, str(exc))
        return

    _record("compute_indicators() ran without exception", True)

    missing = _EXPECTED_STATE_KEYS - set(state.keys())
    _record("All expected keys present in state",
            len(missing) == 0,
            f"missing: {missing}" if missing else "")

    nan_keys = [k for k in _NUMERIC_KEYS if k in state and np.isnan(state[k])]
    _record("No NaN values in numeric state fields",
            len(nan_keys) == 0,
            f"NaN in: {nan_keys}" if nan_keys else "")

    _record("computed_at is a UTC datetime",
            isinstance(state.get("computed_at"), datetime))

    _record("price > 0",  state.get("price", 0) > 0)
    _record("atr  > 0",   state.get("atr",   0) > 0)
    _record("rsi in 0–100", 0 <= state.get("rsi", -1) <= 100)

    print("\n  Full state dict:")
    for k, v in state.items():
        if k == "_df_1h":
            continue
        print(f"    {k:<35} {v}")


# ── Test 3: classify_market ───────────────────────────────────────────────────

def _make_state_from(df15: pd.DataFrame, df1h: pd.DataFrame) -> dict:
    """Compute a state dict and inject df_1h for the EMA slope check."""
    state = compute_indicators(df15, df1h)
    state["_df_1h"] = df1h
    return state


def test_classify_market() -> None:
    _section("Test 3 — classify_market() — three scenarios")

    # ── Scenario A: TRENDING ─────────────────────────────────────────────────
    # Normal mock data with clear upward trend → expect TRENDING.
    df15_trend = generate_mock_data(n=100, trend_per_bar=50, interval_minutes=15)
    df1h_trend = generate_mock_data(n=100, trend_per_bar=200, interval_minutes=60)
    try:
        state_trend = _make_state_from(df15_trend, df1h_trend)
        result_a = classify_market(df15_trend, state_trend)
        _record("Scenario A (trending data) → 'TRENDING'",
                result_a == "TRENDING", f"got: {result_a!r}")
    except Exception as exc:
        _record("Scenario A raised exception", False, str(exc))

    # ── Scenario B: RANGING — flat price forces ADX below 20 ─────────────────
    # A completely flat price series produces zero directional movement,
    # so ADX converges to 0 — well below the 20 threshold.
    df15_flat = generate_mock_data(n=100, trend_per_bar=0, interval_minutes=15).copy()
    flat_price = 70_000.0
    df15_flat["open"]  = flat_price
    df15_flat["high"]  = flat_price * 1.0001   # tiny spread to avoid ATR=0
    df15_flat["low"]   = flat_price * 0.9999
    df15_flat["close"] = flat_price

    df1h_flat = generate_mock_data(n=100, trend_per_bar=0, interval_minutes=60).copy()
    df1h_flat["open"]  = flat_price
    df1h_flat["high"]  = flat_price * 1.0001
    df1h_flat["low"]   = flat_price * 0.9999
    df1h_flat["close"] = flat_price

    try:
        state_flat = _make_state_from(df15_flat, df1h_flat)
        result_b = classify_market(df15_flat, state_flat)
        _record("Scenario B (flat/ranging data) → 'RANGING'",
                result_b == "RANGING", f"got: {result_b!r}")
    except Exception as exc:
        _record("Scenario B raised exception", False, str(exc))

    # ── Scenario C: VOLATILE — spike last candle range to trigger ATR alarm ──
    # Inflate only the final candle's high-low range to ~10× normal.
    # This makes state["atr"] (latest ATR) >> 20-period mean ATR.
    df15_vol = generate_mock_data(n=100, trend_per_bar=10, interval_minutes=15).copy()
    last_close = float(df15_vol["close"].iloc[-1])
    df15_vol.at[df15_vol.index[-1], "high"] = last_close * 1.12   # +12%
    df15_vol.at[df15_vol.index[-1], "low"]  = last_close * 0.88   # -12%
    df1h_vol = generate_mock_data(n=100, trend_per_bar=40, interval_minutes=60)

    try:
        state_vol = _make_state_from(df15_vol, df1h_vol)
        result_c = classify_market(df15_vol, state_vol)
        _record("Scenario C (ATR spike) → 'VOLATILE'",
                result_c == "VOLATILE", f"got: {result_c!r}")
    except Exception as exc:
        _record("Scenario C raised exception", False, str(exc))


# ── Test 4: evaluate_signal ───────────────────────────────────────────────────

_SIGNAL_KEYS_NO_SIGNAL = {"signal", "market_condition", "reason"}
_SIGNAL_KEYS_WITH_SIGNAL = {
    "signal", "market_condition", "entry", "stop_loss",
    "take_profit", "atr", "rsi", "volume_ratio",
    "criteria_met", "conviction", "timestamp", "expiry",
}


def test_evaluate_signal() -> None:
    _section("Test 4 — evaluate_signal()")

    df15 = generate_mock_data(n=100, interval_minutes=15)
    df1h = generate_mock_data(n=100, interval_minutes=60)

    try:
        result = evaluate_signal(df15, df1h)
    except Exception as exc:
        _record("evaluate_signal() ran without exception", False, str(exc))
        traceback.print_exc()
        return

    _record("evaluate_signal() ran without exception", True)

    has_signal_key = "signal" in result
    _record("Return dict contains 'signal' key", has_signal_key)

    has_market_cond = "market_condition" in result
    _record("Return dict contains 'market_condition' key", has_market_cond)

    direction = result.get("signal")
    if direction in ("LONG", "SHORT"):
        missing = _SIGNAL_KEYS_WITH_SIGNAL - set(result.keys())
        _record(f"Signal dict ({direction}) has all required keys",
                len(missing) == 0,
                f"missing: {missing}" if missing else "")
        _record("entry > 0",       result.get("entry", 0) > 0)
        _record("stop_loss > 0",   result.get("stop_loss", 0) > 0)
        _record("take_profit > 0", result.get("take_profit", 0) > 0)
        if direction == "LONG":
            _record("LONG: take_profit > entry > stop_loss",
                    result["take_profit"] > result["entry"] > result["stop_loss"])
        else:
            _record("SHORT: take_profit < entry < stop_loss",
                    result["take_profit"] < result["entry"] < result["stop_loss"])
    else:
        # No signal — must have 'reason' key
        _record("No-signal dict contains 'reason' key", "reason" in result)

    print(f"\n  evaluate_signal() result:")
    for k, v in result.items():
        print(f"    {k:<22} {v}")


# ── Test 5: send_alert dry-run ────────────────────────────────────────────────

def test_send_alert_dry_run() -> None:
    _section("Test 5 — send_alert() dry-run (no SMTP, body printed to console)")

    # Build a synthetic LONG signal dict that bypasses evaluate_signal().
    now = datetime.now(timezone.utc)
    mock_signal: dict = {
        "signal":           "LONG",
        "market_condition": "TRENDING",
        "entry":            70_250.00,
        "stop_loss":        69_835.50,
        "take_profit":      71_038.45,
        "atr":              230.83,
        "rsi":              47.3,
        "volume_ratio":     1.72,
        "criteria_met": [
            "above_ema",
            "macd_just_flipped_positive",
            "rsi_in_range (35–55)",
            "bb_bounce_long",
            "volume_ratio_ok",
        ],
        "conviction":   "HIGH (5/5)",
        "timestamp":    now,
        "computed_at":  now,
        "expiry":       now + timedelta(minutes=30),
    }

    # ── Monkey-patch notifier._send to print instead of connecting to SMTP ───
    _sent_calls: list[tuple[str, str]] = []

    def _fake_send(subject: str, body: str) -> None:
        _sent_calls.append((subject, body))
        print(f"\n  ── Dry-run email ──────────────────────────────────────")
        print(f"  Subject : {subject}")
        print(f"  ── Body ───────────────────────────────────────────────")
        for line in body.splitlines():
            print(f"  {line}")
        print(f"  ── End of email ───────────────────────────────────────\n")

    original_send = notifier._send
    notifier._send = _fake_send          # patch

    try:
        notifier.send_alert(mock_signal)
    except Exception as exc:
        _record("send_alert() ran without exception (dry-run)", False, str(exc))
        return
    finally:
        notifier._send = original_send   # restore

    _record("send_alert() ran without exception (dry-run)", True)
    _record("_send() was called exactly once", len(_sent_calls) == 1,
            f"called {len(_sent_calls)} times")

    if _sent_calls:
        subject, body = _sent_calls[0]
        _record("Subject contains 'LONG'",       "LONG" in subject)
        _record("Subject contains 'BTC/USDT'",   "BTC/USDT" in subject)
        _record("Body contains entry price",      "70250" in body)
        _record("Body contains stop loss",        "69835" in body)
        _record("Body contains take profit",      "71038" in body)
        _record("Body contains R:R ratio",        "1.9:1" in body)
        _record("Body contains 'TRENDING'",       "TRENDING" in body)
        _record("Body contains conviction label", "HIGH (5/5)" in body)

    # Also verify None signal is a no-op.
    notifier._send = _fake_send
    null_calls: list = []
    _real_send2 = notifier._send

    def _counting_send(subject: str, body: str) -> None:
        null_calls.append(1)

    notifier._send = _counting_send
    try:
        notifier.send_alert({"signal": None})
    finally:
        notifier._send = original_send

    _record("send_alert(signal=None) makes no SMTP call", len(null_calls) == 0)


# ── Summary ───────────────────────────────────────────────────────────────────

def _print_summary() -> None:
    print(f"\n{'═' * 60}")
    print("  SUMMARY")
    print(f"{'═' * 60}")
    passed = sum(1 for _, s, _ in _results if s == _PASS)
    failed = sum(1 for _, s, _ in _results if s == _FAIL)
    for name, status, detail in _results:
        marker = "✅" if status == _PASS else "❌"
        line = f"  {marker}  {name}"
        if detail:
            line += f"\n       ↳ {detail}"
        print(line)
    print(f"\n  Total: {passed + failed}  |  Passed: {passed}  |  Failed: {failed}")
    print(f"{'═' * 60}\n")
    if failed:
        sys.exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "═" * 60)
    print("  BTC Signal Algo — Dry-Run Test Suite")
    print("═" * 60)

    test_mock_data_generator()
    test_compute_indicators()
    test_classify_market()
    test_evaluate_signal()
    test_send_alert_dry_run()

    _print_summary()
