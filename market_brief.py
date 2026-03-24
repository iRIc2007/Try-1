#!/usr/bin/env python3
"""
Daily Market Brief Generator
Produces a dated HTML report with market data, watchlist, macro, and news.
Run:  python market_brief.py          (live data)
      python market_brief.py --demo   (sample data for preview)
"""

import os
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import yfinance as yf
from jinja2 import Template

import config

DEMO_MODE = "--demo" in sys.argv


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_quote(ticker_symbol: str) -> dict:
    """Fetch current price, change %, and metadata for one ticker."""
    try:
        tk = yf.Ticker(ticker_symbol)
        info = tk.fast_info
        hist = tk.history(period="5d")

        if hist.empty or len(hist) < 2:
            return None

        current = hist["Close"].iloc[-1]
        prev = hist["Close"].iloc[-2]
        change_pct = ((current - prev) / prev) * 100

        # 52-week data
        hist_1y = tk.history(period="1y")
        high_52 = hist_1y["High"].max() if not hist_1y.empty else current
        low_52 = hist_1y["Low"].min() if not hist_1y.empty else current

        # Volume vs average
        vol = hist["Volume"].iloc[-1]
        avg_vol = hist["Volume"].mean()

        # Weekly change (5 trading days)
        if len(hist) >= 5:
            week_ago = hist["Close"].iloc[0]
            weekly_change = ((current - week_ago) / week_ago) * 100
        else:
            weekly_change = change_pct

        return {
            "price": current,
            "change_pct": change_pct,
            "weekly_change": weekly_change,
            "volume": int(vol),
            "avg_volume": int(avg_vol),
            "vol_ratio": (vol / avg_vol) if avg_vol > 0 else 0,
            "high_52": high_52,
            "low_52": low_52,
            "range_pct": ((current - low_52) / (high_52 - low_52) * 100)
                         if high_52 != low_52 else 50,
        }
    except Exception as e:
        print(f"  ⚠ Could not fetch {ticker_symbol}: {e}")
        return None


def fetch_indices() -> list[dict]:
    """Fetch market overview indices."""
    print("Fetching market indices...")
    results = []
    for name, symbol in config.INDICES.items():
        q = fetch_quote(symbol)
        if q:
            # Trend note based on daily + weekly direction
            if q["change_pct"] > 0.5:
                trend = "Bullish"
            elif q["change_pct"] < -0.5:
                trend = "Bearish"
            else:
                trend = "Flat"

            results.append({"name": name, "symbol": symbol, **q, "trend": trend})
    return results


def fetch_watchlist() -> dict[str, list[dict]]:
    """Fetch data for every stock in the sector-based watchlist."""
    print("Fetching watchlist stocks...")
    sector_data = {}
    for sector, tickers in config.WATCHLIST.items():
        stocks = []
        for t in tickers:
            q = fetch_quote(t)
            if q:
                stocks.append({"ticker": t, **q})
        # Sort by daily change descending — biggest winners first
        stocks.sort(key=lambda s: s["change_pct"], reverse=True)
        sector_data[sector] = stocks
    return sector_data


def fetch_macro() -> list[dict]:
    """Fetch macro assets (DXY, Gold, Oil, BTC)."""
    print("Fetching macro assets...")
    results = []
    for name, symbol in config.MACRO.items():
        q = fetch_quote(symbol)
        if q:
            results.append({"name": name, "symbol": symbol, **q})
    return results


def fetch_news() -> list[dict]:
    """Fetch headlines from RSS feeds."""
    print("Fetching news headlines...")
    articles = []
    for source_name, url in config.RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                pub_dt = datetime(*pub[:6]) if pub else None
                articles.append({
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", "#"),
                    "source": source_name,
                    "published": pub_dt,
                })
        except Exception as e:
            print(f"  ⚠ RSS error ({source_name}): {e}")

    # Remove duplicates by title, sort newest first
    seen = set()
    unique = []
    for a in articles:
        key = a["title"].lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(a)
    unique.sort(key=lambda a: a["published"] or datetime.min, reverse=True)
    return unique[: config.MAX_HEADLINES]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. AI EXECUTIVE SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

def build_summary_prompt(indices, watchlist, macro, news) -> str:
    """Build the prompt that asks Claude to write an executive summary."""
    lines = ["Here is today's market data. Write exactly 5 concise bullet points "
             "summarizing the key themes, notable movers, and what investors should "
             "watch. Be specific with numbers. No preamble — just the 5 bullets.\n"]

    lines.append("== INDICES ==")
    for idx in indices:
        lines.append(f"{idx['name']}: {idx['price']:.2f} ({idx['change_pct']:+.2f}%)")

    lines.append("\n== TOP MOVERS BY SECTOR ==")
    for sector, stocks in watchlist.items():
        if stocks:
            top = stocks[0]
            bot = stocks[-1]
            lines.append(f"{sector}: best {top['ticker']} {top['change_pct']:+.2f}%, "
                         f"worst {bot['ticker']} {bot['change_pct']:+.2f}%")

    lines.append("\n== MACRO ==")
    for m in macro:
        lines.append(f"{m['name']}: {m['price']:.2f} ({m['change_pct']:+.2f}%)")

    lines.append("\n== TOP HEADLINES ==")
    for n in news[:10]:
        lines.append(f"- {n['title']}")

    return "\n".join(lines)


def generate_ai_summary(indices, watchlist, macro, news) -> list[str]:
    """Call Claude API to produce 5 bullet-point executive summary."""
    api_key = config.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        print("  ℹ No ANTHROPIC_API_KEY found — using fallback summary.")
        return generate_fallback_summary(indices, watchlist, macro)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = build_summary_prompt(indices, watchlist, macro, news)

        print("Generating AI executive summary...")
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        # Parse bullets — look for lines starting with •, -, *, or numbered
        bullets = []
        for line in text.split("\n"):
            line = line.strip()
            if line and (line[0] in "•-*" or (len(line) > 1 and line[0].isdigit() and line[1] in ".)")):
                # Strip the leading bullet character
                cleaned = line.lstrip("•-*0123456789.) ").strip()
                if cleaned:
                    bullets.append(cleaned)
        return bullets[:5] if bullets else [text[:300]]

    except Exception as e:
        print(f"  ⚠ AI summary failed: {e}  — using fallback.")
        return generate_fallback_summary(indices, watchlist, macro)


def generate_fallback_summary(indices, watchlist, macro) -> list[str]:
    """Rule-based summary when no API key is available."""
    bullets = []

    # Index summary
    idx_map = {i["name"]: i for i in indices}
    sp = idx_map.get("S&P 500")
    if sp:
        direction = "higher" if sp["change_pct"] > 0 else "lower"
        bullets.append(
            f"U.S. equities traded {direction} — S&P 500 at {sp['price']:,.0f} "
            f"({sp['change_pct']:+.2f}%), NASDAQ {idx_map.get('NASDAQ', {}).get('change_pct', 0):+.2f}%."
        )

    # VIX
    vix = idx_map.get("VIX")
    if vix:
        level = "elevated" if vix["price"] > 20 else "subdued"
        bullets.append(f"Volatility {level} with VIX at {vix['price']:.1f} ({vix['change_pct']:+.2f}%).")

    # Biggest sector winner
    best_stock = None
    for sector, stocks in watchlist.items():
        if stocks and (best_stock is None or stocks[0]["change_pct"] > best_stock[1]["change_pct"]):
            best_stock = (sector, stocks[0])
    if best_stock:
        s, st = best_stock
        bullets.append(f"Top mover: {st['ticker']} ({s}) surged {st['change_pct']:+.2f}% on the day.")

    # Macro
    for m in macro:
        if "Gold" in m["name"] and abs(m["change_pct"]) > 0.5:
            direction = "rallied" if m["change_pct"] > 0 else "declined"
            bullets.append(f"Gold {direction} to ${m['price']:,.0f} ({m['change_pct']:+.2f}%).")
            break

    for m in macro:
        if "Bitcoin" in m["name"]:
            bullets.append(f"Bitcoin at ${m['price']:,.0f} ({m['change_pct']:+.2f}%).")
            break

    return bullets[:5]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. HTML REPORT TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════════

HTML_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Market Brief — {{ date }}</title>
<style>
  :root {
    --green: #16a34a; --green-bg: #f0fdf4;
    --red: #dc2626;   --red-bg: #fef2f2;
    --gray: #6b7280;  --gray-bg: #f9fafb;
    --border: #e5e7eb;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f3f4f6; color: #111827; line-height: 1.5;
  }
  .container { max-width: 960px; margin: 0 auto; padding: 24px 16px; }
  .header {
    background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
    color: white; padding: 32px; border-radius: 12px; margin-bottom: 24px;
    text-align: center;
  }
  .header h1 { font-size: 28px; font-weight: 700; margin-bottom: 4px; }
  .header .date { opacity: 0.8; font-size: 14px; }
  .card {
    background: white; border-radius: 10px; padding: 24px;
    margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }
  .card h2 {
    font-size: 18px; font-weight: 700; margin-bottom: 16px;
    padding-bottom: 8px; border-bottom: 2px solid var(--border);
  }
  .summary-list { list-style: none; padding: 0; }
  .summary-list li {
    padding: 10px 14px; margin-bottom: 8px; border-radius: 8px;
    background: #eef2ff; border-left: 4px solid #4f46e5;
    font-size: 14px; line-height: 1.6;
  }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th {
    text-align: left; padding: 10px 12px; background: var(--gray-bg);
    font-weight: 600; font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.5px; color: var(--gray); border-bottom: 2px solid var(--border);
  }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); }
  tr:hover { background: #fafafa; }
  .pos { color: var(--green); font-weight: 600; }
  .neg { color: var(--red); font-weight: 600; }
  .neutral { color: var(--gray); }
  .tag {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 600;
  }
  .tag-green { background: var(--green-bg); color: var(--green); }
  .tag-red   { background: var(--red-bg); color: var(--red); }
  .tag-gray  { background: var(--gray-bg); color: var(--gray); }
  .range-bar {
    width: 100%; height: 6px; background: #e5e7eb; border-radius: 3px;
    position: relative; margin-top: 4px;
  }
  .range-fill {
    height: 100%; border-radius: 3px; background: linear-gradient(90deg, var(--red), #eab308, var(--green));
  }
  .range-dot {
    width: 10px; height: 10px; background: #111827; border: 2px solid white;
    border-radius: 50%; position: absolute; top: -2px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.3);
  }
  .sector-label {
    font-size: 14px; font-weight: 700; color: #4f46e5;
    margin: 20px 0 8px 0; padding: 6px 0;
    border-bottom: 1px dashed #c7d2fe;
  }
  .sector-label:first-of-type { margin-top: 0; }
  .news-item {
    padding: 10px 0; border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; gap: 12px;
  }
  .news-item:last-child { border-bottom: none; }
  .news-item a {
    color: #111827; text-decoration: none; font-size: 14px; font-weight: 500;
  }
  .news-item a:hover { color: #4f46e5; }
  .news-source { font-size: 11px; color: var(--gray); white-space: nowrap; }
  .footer {
    text-align: center; font-size: 12px; color: var(--gray);
    margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border);
  }
  .vol-high { color: #b45309; font-weight: 600; }
  @media (max-width: 640px) {
    .container { padding: 12px 8px; }
    .card { padding: 16px; }
    table { font-size: 12px; }
    th, td { padding: 8px 6px; }
  }
</style>
</head>
<body>
<div class="container">

  <!-- HEADER -->
  <div class="header">
    <h1>Daily Market Brief</h1>
    <div class="date">{{ date }} &middot; Generated at {{ time }}</div>
  </div>

  <!-- EXECUTIVE SUMMARY -->
  <div class="card">
    <h2>Executive Summary</h2>
    <ul class="summary-list">
      {% for bullet in summary %}
      <li>{{ bullet }}</li>
      {% endfor %}
    </ul>
  </div>

  <!-- MARKET OVERVIEW -->
  <div class="card">
    <h2>Market Overview</h2>
    <table>
      <thead>
        <tr><th>Index</th><th>Price</th><th>Daily Chg</th><th>Trend</th></tr>
      </thead>
      <tbody>
        {% for i in indices %}
        <tr>
          <td><strong>{{ i.name }}</strong></td>
          <td>{{ "{:,.2f}".format(i.price) }}</td>
          <td class="{{ 'pos' if i.change_pct > 0 else ('neg' if i.change_pct < 0 else 'neutral') }}">
            {{ "{:+.2f}".format(i.change_pct) }}%
          </td>
          <td>
            <span class="tag {{ 'tag-green' if 'Bull' in i.trend else ('tag-red' if 'Bear' in i.trend else 'tag-gray') }}">
              {{ i.trend }}
            </span>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <!-- WATCHLIST BY SECTOR -->
  <div class="card">
    <h2>Watchlist — by Sector</h2>
    {% for sector, stocks in watchlist.items() %}
    <div class="sector-label">{{ sector }}</div>
    <table>
      <thead>
        <tr>
          <th>Ticker</th><th>Price</th><th>Day</th><th>Week</th>
          <th>Volume vs Avg</th><th>52-Week Range</th>
        </tr>
      </thead>
      <tbody>
        {% for s in stocks %}
        <tr>
          <td><strong>{{ s.ticker }}</strong></td>
          <td>{{ "{:,.2f}".format(s.price) }}</td>
          <td class="{{ 'pos' if s.change_pct > 0 else ('neg' if s.change_pct < 0 else 'neutral') }}">
            {{ "{:+.2f}".format(s.change_pct) }}%
          </td>
          <td class="{{ 'pos' if s.weekly_change > 0 else ('neg' if s.weekly_change < 0 else 'neutral') }}">
            {{ "{:+.2f}".format(s.weekly_change) }}%
          </td>
          <td class="{{ 'vol-high' if s.vol_ratio > 1.5 else '' }}">
            {{ "{:,.0f}".format(s.volume) }}
            ({{ "{:.1f}x".format(s.vol_ratio) }})
          </td>
          <td style="min-width:120px;">
            <div style="font-size:11px;color:#6b7280;">
              {{ "{:,.0f}".format(s.low_52) }} — {{ "{:,.0f}".format(s.high_52) }}
            </div>
            <div class="range-bar">
              <div class="range-fill" style="width:100%"></div>
              <div class="range-dot" style="left:calc({{ s.range_pct|round(1) }}% - 5px)"></div>
            </div>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% endfor %}
  </div>

  <!-- MACRO DASHBOARD -->
  <div class="card">
    <h2>Macro Dashboard</h2>
    <table>
      <thead>
        <tr><th>Asset</th><th>Price</th><th>Daily Chg</th><th>Weekly Chg</th></tr>
      </thead>
      <tbody>
        {% for m in macro %}
        <tr>
          <td><strong>{{ m.name }}</strong></td>
          <td>{{ "{:,.2f}".format(m.price) }}</td>
          <td class="{{ 'pos' if m.change_pct > 0 else ('neg' if m.change_pct < 0 else 'neutral') }}">
            {{ "{:+.2f}".format(m.change_pct) }}%
          </td>
          <td class="{{ 'pos' if m.weekly_change > 0 else ('neg' if m.weekly_change < 0 else 'neutral') }}">
            {{ "{:+.2f}".format(m.weekly_change) }}%
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <!-- NEWS HEADLINES -->
  <div class="card">
    <h2>Top Headlines</h2>
    {% for n in news %}
    <div class="news-item">
      <a href="{{ n.link }}" target="_blank">{{ n.title }}</a>
      <span class="news-source">{{ n.source }}</span>
    </div>
    {% endfor %}
  </div>

  <div class="footer">
    Market Brief Generator &middot; Data via Yahoo Finance &middot; News via RSS
  </div>

</div>
</body>
</html>
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DEMO / SAMPLE DATA (used with --demo flag)
# ═══════════════════════════════════════════════════════════════════════════════

def _demo_stock(ticker, base_price, daily_pct, weekly_pct, vol, avg_vol, low_52, high_52):
    """Helper to build a stock data dict for demo mode."""
    price = base_price
    rng = ((price - low_52) / (high_52 - low_52) * 100) if high_52 != low_52 else 50
    return {
        "ticker": ticker, "price": price, "change_pct": daily_pct,
        "weekly_change": weekly_pct, "volume": vol, "avg_volume": avg_vol,
        "vol_ratio": vol / avg_vol if avg_vol else 0,
        "high_52": high_52, "low_52": low_52, "range_pct": rng,
    }


def demo_indices():
    return [
        {"name": "S&P 500",      "symbol": "^GSPC", "price": 5842.15, "change_pct":  0.87, "weekly_change":  1.42, "trend": "Bullish",
         "volume": 3_800_000_000, "avg_volume": 3_500_000_000, "vol_ratio": 1.09, "high_52": 5920.0, "low_52": 4890.0, "range_pct": 92.5},
        {"name": "NASDAQ",       "symbol": "^IXIC", "price": 18634.50, "change_pct":  1.24, "weekly_change":  2.10, "trend": "Bullish",
         "volume": 5_100_000_000, "avg_volume": 4_800_000_000, "vol_ratio": 1.06, "high_52": 18900.0, "low_52": 15200.0, "range_pct": 92.8},
        {"name": "DOW",          "symbol": "^DJI",  "price": 42876.30, "change_pct":  0.34, "weekly_change":  0.65, "trend": "Flat",
         "volume": 310_000_000, "avg_volume": 330_000_000, "vol_ratio": 0.94, "high_52": 43200.0, "low_52": 37500.0, "range_pct": 94.3},
        {"name": "VIX",          "symbol": "^VIX",  "price": 14.62,    "change_pct": -3.18, "weekly_change": -8.50, "trend": "Bearish",
         "volume": 0, "avg_volume": 0, "vol_ratio": 0, "high_52": 38.0, "low_52": 11.5, "range_pct": 11.8},
        {"name": "10Y Treasury", "symbol": "^TNX",  "price": 4.28,     "change_pct": -0.47, "weekly_change": -1.15, "trend": "Flat",
         "volume": 0, "avg_volume": 0, "vol_ratio": 0, "high_52": 4.99, "low_52": 3.62, "range_pct": 48.2},
    ]


def demo_watchlist():
    data = {
        "Tech / AI": [
            _demo_stock("NVDA",  142.87,  3.45,  6.20,  68_000_000, 52_000_000, 75.0, 152.0),
            _demo_stock("META",  542.30,  2.18,  4.10,  22_000_000, 18_000_000, 340.0, 560.0),
            _demo_stock("AMZN",  198.45,  1.65,  3.30,  35_000_000, 30_000_000, 140.0, 205.0),
            _demo_stock("GOOGL", 178.92,  1.12,  2.80,  24_000_000, 22_000_000, 130.0, 185.0),
            _demo_stock("MSFT",  448.20,  0.85,  1.50,  19_000_000, 21_000_000, 360.0, 460.0),
            _demo_stock("AAPL",  232.15,  0.42,  0.90,  45_000_000, 48_000_000, 170.0, 240.0),
            _demo_stock("CRM",   318.75,  0.28, -0.40,  5_200_000,  6_000_000, 220.0, 335.0),
            _demo_stock("ORCL",  178.40, -0.35, -1.20,  8_500_000,  9_200_000, 105.0, 192.0),
        ],
        "Semiconductors": [
            _demo_stock("AVGO",  192.45,  2.80,  5.60,  12_000_000, 10_000_000, 110.0, 200.0),
            _demo_stock("AMD",   168.30,  2.15,  4.80,  42_000_000, 38_000_000, 100.0, 180.0),
            _demo_stock("TSM",   182.60,  1.45,  3.10,   9_000_000,  8_500_000, 105.0, 195.0),
            _demo_stock("QCOM",  185.20,  0.62,  1.20,  6_400_000,  7_000_000, 130.0, 195.0),
            _demo_stock("INTC",   28.45, -1.30, -3.50, 38_000_000, 30_000_000,  20.0,  42.0),
        ],
        "Healthcare": [
            _demo_stock("LLY",   835.40,  1.90,  3.70, 3_200_000, 2_800_000, 540.0, 860.0),
            _demo_stock("ABBV",  192.80,  0.75,  1.40, 5_600_000, 6_200_000, 140.0, 200.0),
            _demo_stock("UNH",   528.30,  0.40,  0.90, 2_900_000, 3_200_000, 440.0, 560.0),
            _demo_stock("MRK",   118.65, -0.22, -0.60, 8_400_000, 9_000_000,  95.0, 132.0),
            _demo_stock("JNJ",   158.20, -0.55, -1.30, 6_800_000, 7_100_000, 145.0, 172.0),
            _demo_stock("PFE",    26.85, -1.10, -2.80,28_000_000,24_000_000,  24.0,  32.0),
        ],
        "Energy": [
            _demo_stock("COP",  112.40,  1.35,  2.90,  5_800_000, 5_200_000, 88.0, 125.0),
            _demo_stock("EOG",  128.75,  1.10,  2.20,  3_400_000, 3_600_000, 98.0, 140.0),
            _demo_stock("XOM",  108.30,  0.65,  1.50, 12_000_000,14_000_000, 92.0, 118.0),
            _demo_stock("CVX",  158.90,  0.30,  0.80,  5_200_000, 6_000_000, 138.0, 168.0),
            _demo_stock("SLB",   48.25, -0.45, -1.10,  8_000_000, 8_500_000, 40.0, 58.0),
        ],
        "Utilities": [
            _demo_stock("NEE",   82.45,  0.90,  2.10,  8_200_000, 7_500_000, 58.0, 88.0),
            _demo_stock("AEP",   98.30,  0.55,  1.30,  2_800_000, 3_000_000, 78.0, 105.0),
            _demo_stock("SO",    78.65,  0.40,  0.90,  4_100_000, 4_500_000, 65.0, 82.0),
            _demo_stock("DUK",  108.20,  0.15,  0.40,  2_500_000, 2_800_000, 90.0, 115.0),
            _demo_stock("D",     55.80, -0.30, -0.70,  3_600_000, 3_800_000, 42.0, 60.0),
        ],
        "Infrastructure / Industrials": [
            _demo_stock("GE",   198.50,  1.85,  4.20,  6_200_000, 5_000_000, 120.0, 210.0),
            _demo_stock("CAT",  368.40,  0.95,  2.40,  2_100_000, 2_400_000, 260.0, 385.0),
            _demo_stock("UNP",  248.60,  0.50,  1.10,  2_800_000, 3_100_000, 210.0, 260.0),
            _demo_stock("DE",   412.80,  0.22,  0.50,  1_400_000, 1_600_000, 345.0, 430.0),
            _demo_stock("HON",  218.35, -0.40, -0.90,  2_600_000, 2_900_000, 185.0, 228.0),
        ],
        "Financials": [
            _demo_stock("GS",   548.90,  1.60,  3.80,  2_400_000, 2_100_000, 380.0, 565.0),
            _demo_stock("JPM",  238.45,  1.10,  2.50,  8_200_000, 7_600_000, 175.0, 248.0),
            _demo_stock("MA",   512.30,  0.72,  1.80,  2_000_000, 2_200_000, 410.0, 530.0),
            _demo_stock("V",    298.15,  0.45,  1.00,  4_800_000, 5_200_000, 250.0, 310.0),
            _demo_stock("BRK-B",458.60,  0.18,  0.30,  3_200_000, 3_600_000, 380.0, 470.0),
        ],
        "Consumer / Retail": [
            _demo_stock("COST", 928.40,  1.25,  2.80,  1_800_000, 2_000_000, 650.0, 950.0),
            _demo_stock("WMT",  178.60,  0.60,  1.20, 5_400_000, 6_000_000, 145.0, 185.0),
            _demo_stock("MCD",  298.45,  0.30,  0.70, 2_600_000, 3_000_000, 250.0, 310.0),
            _demo_stock("SBUX",  98.20, -0.85, -2.10, 6_800_000, 5_500_000,  85.0, 112.0),
            _demo_stock("NKE",   78.35, -1.50, -4.20,10_200_000, 8_000_000,  68.0, 110.0),
        ],
    }
    # Each sector list is already sorted by change_pct descending
    return data


def demo_macro():
    return [
        {"name": "USD Index (DXY)", "symbol": "DX-Y.NYB", "price": 103.42, "change_pct": -0.35,
         "weekly_change": -0.82, "volume": 0, "avg_volume": 0, "vol_ratio": 0,
         "high_52": 110.0, "low_52": 99.5, "range_pct": 37.3},
        {"name": "Gold",            "symbol": "GC=F",     "price": 3028.50, "change_pct":  0.92,
         "weekly_change":  2.15, "volume": 182_000, "avg_volume": 165_000, "vol_ratio": 1.1,
         "high_52": 3080.0, "low_52": 2280.0, "range_pct": 93.6},
        {"name": "Crude Oil (WTI)", "symbol": "CL=F",     "price": 68.72,   "change_pct": -0.58,
         "weekly_change": -1.40, "volume": 320_000, "avg_volume": 350_000, "vol_ratio": 0.91,
         "high_52": 85.0, "low_52": 62.0, "range_pct": 29.2},
        {"name": "Bitcoin",         "symbol": "BTC-USD",  "price": 87245.80, "change_pct":  2.34,
         "weekly_change":  5.60, "volume": 28_000_000_000, "avg_volume": 24_000_000_000, "vol_ratio": 1.17,
         "high_52": 94000.0, "low_52": 52000.0, "range_pct": 83.9},
    ]


def demo_news():
    headlines = [
        ("Fed Officials Signal Patience on Rate Cuts Amid Sticky Inflation Data", "Reuters Business"),
        ("NVIDIA Surges 3.5% as New AI Chip Orders Exceed Expectations", "CNBC Top News"),
        ("Gold Hits Fresh Record Above $3,000 on Central Bank Buying Spree", "MarketWatch"),
        ("Tesla Unveils Affordable Model Q Targeting $25,000 Price Point", "Yahoo Finance"),
        ("Bitcoin Tops $87,000 as Institutional ETF Inflows Accelerate", "CNBC Top News"),
        ("Amazon Web Services Announces $10B Data Center Expansion in Europe", "Reuters Business"),
        ("U.S. Manufacturing PMI Surprises to Upside, Signals Recovery", "MarketWatch"),
        ("Eli Lilly Weight-Loss Drug Shows 25% Efficacy Gain in Phase 3 Trial", "Yahoo Finance"),
        ("China Cuts Reserve Requirement Ratio to Boost Slowing Economy", "Reuters Business"),
        ("Goldman Sachs Raises S&P 500 Year-End Target to 6,200", "CNBC Top News"),
        ("Oil Slips Below $69 on Demand Concerns Despite OPEC+ Cuts", "MarketWatch"),
        ("Apple Vision Pro 2 Rumored for September Launch with 50% Price Cut", "Yahoo Finance"),
        ("European Central Bank Holds Rates Steady, Signals June Cut", "Reuters Business"),
        ("Nike Drops 1.5% After Downbeat China Revenue Guidance", "CNBC Top News"),
        ("Intel Restructuring Plan Includes 5,000 Additional Layoffs", "MarketWatch"),
        ("GE Aerospace Wins $4.8B Pentagon Contract for Next-Gen Engines", "Yahoo Finance"),
        ("Starbucks Same-Store Sales Decline for Third Straight Quarter", "Reuters Business"),
        ("Broadcom AI Revenue Doubles Year-Over-Year, Stock Jumps 2.8%", "CNBC Top News"),
        ("U.S. 10-Year Yield Dips Below 4.3% on Flight to Safety", "MarketWatch"),
        ("Costco Tops Earnings Estimates with 8.2% Revenue Growth", "Yahoo Finance"),
    ]
    now = datetime.now()
    return [
        {
            "title": title,
            "link": "#",
            "source": source,
            "published": now - timedelta(hours=i, minutes=random.randint(0, 59)),
        }
        for i, (title, source) in enumerate(headlines)
    ]


def demo_summary():
    return [
        "U.S. equities rallied broadly — S&P 500 gained +0.87% to 5,842 and NASDAQ surged +1.24%, "
        "led by mega-cap tech and semiconductors hitting near-record highs.",
        "NVIDIA led the market higher (+3.45%) on strong AI chip demand signals, with the broader "
        "semiconductor sector (AVGO +2.8%, AMD +2.15%) confirming sustained AI infrastructure spending.",
        "Gold broke above $3,028 (+0.92%) to a new all-time high as central banks accelerate reserve "
        "diversification and the DXY weakened -0.35%, while crude oil slipped below $69 on demand concerns.",
        "Defensive rotation evident in utilities (NEE +0.90%) and healthcare (LLY +1.90%) outperforming "
        "on a risk-adjusted basis, while consumer discretionary lagged with NKE -1.50% on weak China guidance.",
        "Bitcoin surged past $87,000 (+2.34%) on record institutional ETF inflows, while the VIX "
        "collapsed to 14.62 (-3.18%), signaling market complacency that warrants caution at these levels.",
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_report():
    """Main entry point — fetch everything and produce the HTML report."""
    now = datetime.now()
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%I:%M %p")
    file_date = now.strftime("%Y-%m-%d")

    mode_label = "DEMO" if DEMO_MODE else "LIVE"
    print(f"\n{'='*60}")
    print(f"  Market Brief Generator [{mode_label}] — {date_str}")
    print(f"{'='*60}\n")

    if DEMO_MODE:
        print("Using sample data (--demo mode)...")
        indices = demo_indices()
        watchlist = demo_watchlist()
        macro = demo_macro()
        news = demo_news()
        summary = demo_summary()
    else:
        # Fetch all data
        indices = fetch_indices()
        watchlist = fetch_watchlist()
        macro = fetch_macro()
        news = fetch_news()
        # Generate summary
        summary = generate_ai_summary(indices, watchlist, macro, news)

    # Render HTML
    print("Rendering HTML report...")
    html = HTML_TEMPLATE.render(
        date=date_str,
        time=time_str,
        summary=summary,
        indices=indices,
        watchlist=watchlist,
        macro=macro,
        news=news,
    )

    # Save to reports/ folder
    reports_dir = Path(config.REPORT_DIR)
    reports_dir.mkdir(exist_ok=True)
    filepath = reports_dir / f"{file_date}.html"
    filepath.write_text(html, encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"  ✓ Report saved: {filepath}")
    print(f"  Open in your browser to view.")
    print(f"{'='*60}\n")
    return filepath


if __name__ == "__main__":
    generate_report()
