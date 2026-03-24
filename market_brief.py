#!/usr/bin/env python3
"""
Daily Market Brief Generator
Produces a dated HTML report with market data, watchlist, macro, and news.
Run:  python market_brief.py
"""

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import yfinance as yf
from jinja2 import Template

import config


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
# 4. REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_report():
    """Main entry point — fetch everything and produce the HTML report."""
    now = datetime.now()
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%I:%M %p")
    file_date = now.strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"  Market Brief Generator — {date_str}")
    print(f"{'='*60}\n")

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
