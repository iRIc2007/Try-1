"""
notifier.py - Alert delivery via email (Gmail SMTP).

Responsibilities:
- Load Gmail credentials and recipient address from environment variables
  (GMAIL_ADDRESS, GMAIL_APP_PASSWORD, ALERT_RECIPIENT_EMAIL).
- Format a human-readable alert message from a signal dictionary, including:
    - Signal direction (LONG / SHORT)
    - Entry price, stop-loss, take-profit
    - Risk-reward ratio
    - Signal timestamp and expiry time
- Send the formatted alert as an email using smtplib over TLS (port 587).
- Log success or failure of each notification attempt.
- Provide a no-op / dry-run mode for testing without sending real emails.
"""
