"""
notifier.py - Alert delivery via email (Gmail SMTP).

Provides two public functions:

- send_alert(signal_dict)                       — sends a trade signal email
- send_suppression_notice(reason, market_condition) — sends a no-trade notice
  (rate-limited to once per hour)

Credentials are loaded from .env via python-dotenv.
Transport: smtplib SMTP_SSL on smtp.gmail.com:465.
"""

import logging
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from dotenv import load_dotenv
import os

# Load .env from the same directory as this file.
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

# ── Credentials (resolved once at import time) ────────────────────────────────

_GMAIL_ADDRESS       = os.getenv("GMAIL_ADDRESS", "")
_GMAIL_APP_PASSWORD  = os.getenv("GMAIL_APP_PASSWORD", "")
_ALERT_RECIPIENT     = os.getenv("ALERT_RECIPIENT_EMAIL", "")

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 465

# ── Suppression-notice rate-limit state ──────────────────────────────────────

_last_suppression_sent: datetime | None = None
_SUPPRESSION_COOLDOWN = timedelta(hours=1)

# ── Criteria label map (ordered to match body template) ──────────────────────

_LONG_CRITERIA_LABELS = [
    ("above_ema",                 "Price above 1h 50 EMA"),
    ("macd_just_flipped_positive","MACD histogram flipped positive"),
    ("rsi_in_range (35\u201355)", "RSI at {rsi} \u2014 room to run"),
    ("bb_bounce_long",            "Bollinger Band bounce confirmed"),
    ("volume_ratio_ok",           "Volume {volume_ratio}x average"),
]

_SHORT_CRITERIA_LABELS = [
    ("above_ema_false",            "Price below 1h 50 EMA"),
    ("macd_just_flipped_negative", "MACD histogram flipped negative"),
    ("rsi_in_range (45\u201365)",  "RSI at {rsi} \u2014 extended, reverting"),
    ("bb_bounce_short",            "Bollinger Band rejection confirmed"),
    ("volume_ratio_ok",            "Volume {volume_ratio}x average"),
]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _credentials_ok() -> bool:
    """Return True if all three credential env-vars are populated."""
    if not _GMAIL_ADDRESS or not _GMAIL_APP_PASSWORD or not _ALERT_RECIPIENT:
        logger.error(
            "Email credentials incomplete. Check GMAIL_ADDRESS, "
            "GMAIL_APP_PASSWORD, and ALERT_RECIPIENT_EMAIL in .env."
        )
        return False
    return True


def _send(subject: str, body: str) -> None:
    """Build and dispatch a plain-text email via Gmail SMTP_SSL."""
    msg = MIMEMultipart()
    msg["From"]    = _GMAIL_ADDRESS
    msg["To"]      = _ALERT_RECIPIENT
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT) as server:
            server.login(_GMAIL_ADDRESS, _GMAIL_APP_PASSWORD)
            server.sendmail(_GMAIL_ADDRESS, _ALERT_RECIPIENT, msg.as_string())
        logger.info("Email sent → %s | subject: %s", _ALERT_RECIPIENT, subject)
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed — check GMAIL_APP_PASSWORD.")
    except smtplib.SMTPException as exc:
        logger.error("SMTP error while sending alert: %s", exc)
    except OSError as exc:
        logger.error("Network error while sending alert: %s", exc)


def _format_criteria_lines(signal_dict: dict, direction: str) -> str:
    """Return the ✅-prefixed criteria lines for the email body."""
    rsi          = signal_dict.get("rsi", "?")
    volume_ratio = signal_dict.get("volume_ratio", "?")
    label_map    = _LONG_CRITERIA_LABELS if direction == "LONG" else _SHORT_CRITERIA_LABELS

    lines = []
    for _key, template in label_map:
        label = template.format(rsi=rsi, volume_ratio=volume_ratio)
        lines.append(f"\u2705 {label}")
    return "\n".join(lines)


def _format_body(signal_dict: dict) -> str:
    """Render the full plain-text email body from a signal dict."""
    direction    = signal_dict["signal"]
    entry        = signal_dict.get("entry", "?")
    stop_loss    = signal_dict.get("stop_loss", "?")
    take_profit  = signal_dict.get("take_profit", "?")
    rsi          = signal_dict.get("rsi", "?")
    volume_ratio = signal_dict.get("volume_ratio", "?")
    condition    = signal_dict.get("market_condition", "TRENDING")
    conviction   = signal_dict.get("conviction", "HIGH (5/5)")
    computed_at  = signal_dict.get("computed_at") or signal_dict.get("timestamp", "?")

    if isinstance(computed_at, datetime):
        computed_at = computed_at.strftime("%Y-%m-%d %H:%M:%S")

    criteria_lines = _format_criteria_lines(signal_dict, direction)

    body = (
        f"Direction:     {direction}\n"
        f"Entry zone:    ${entry}\n"
        f"Stop loss:     ${stop_loss}  (R:R ratio: 1.9:1)\n"
        f"Take profit:   ${take_profit}\n"
        f"Max hold time: 30 minutes\n"
        f"---\n"
        f"Criteria met:\n"
        f"{criteria_lines}\n"
        f"---\n"
        f"Market condition: {condition}\n"
        f"Conviction: {conviction}\n"
        f"Generated at: {computed_at} UTC"
    )
    return body


# ── Public API ────────────────────────────────────────────────────────────────

def send_alert(signal_dict: dict) -> None:
    """
    Send a trade signal email if ``signal_dict["signal"]`` is not None.

    Subject:
      "🟢 LONG SIGNAL — BTC/USDT"  or  "🔴 SHORT SIGNAL — BTC/USDT"

    Does nothing (logs a debug message) when signal is None.

    Parameters
    ----------
    signal_dict : dict returned by ``signal_engine.evaluate_signal``.
    """
    if signal_dict.get("signal") is None:
        logger.debug("send_alert: signal is None — nothing to send.")
        return

    if not _credentials_ok():
        return

    direction = signal_dict["signal"]
    emoji     = "\U0001f7e2" if direction == "LONG" else "\U0001f534"   # 🟢 / 🔴
    subject   = f"{emoji} {direction} SIGNAL \u2014 BTC/USDT"
    body      = _format_body(signal_dict)

    _send(subject, body)


def send_suppression_notice(reason: str, market_condition: str) -> None:
    """
    Send a brief no-trade notice email, rate-limited to once per hour.

    Subject: "📵 BTC Algo — No Trade Conditions"

    Parameters
    ----------
    reason           : Human-readable explanation (e.g. "Criteria not fully met").
    market_condition : e.g. "RANGING", "VOLATILE", or "TRENDING".
    """
    global _last_suppression_sent

    now = datetime.now(timezone.utc)
    if (
        _last_suppression_sent is not None
        and now - _last_suppression_sent < _SUPPRESSION_COOLDOWN
    ):
        logger.debug(
            "send_suppression_notice: rate-limited (last sent %s).",
            _last_suppression_sent.strftime("%H:%M:%S UTC"),
        )
        return

    if not _credentials_ok():
        return

    subject = "\U0001f4f5 BTC Algo \u2014 No Trade Conditions"
    body = (
        f"No trade signal was generated on this candle close.\n\n"
        f"Reason:           {reason}\n"
        f"Market condition: {market_condition}\n"
        f"Time:             {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    _send(subject, body)
    _last_suppression_sent = now
