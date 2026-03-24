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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
import yfinance as yf
from jinja2 import Template

import config

DEMO_MODE = "--demo" in sys.argv
HEALTHCHECK_MODE = "--healthcheck" in sys.argv
FETCH_TIMEOUT = 30  # seconds per individual ticker / feed fetch


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_quote_inner(ticker_symbol: str) -> dict | None:
    """Core fetch logic for one ticker (no timeout wrapper)."""
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


def fetch_quote(ticker_symbol: str) -> dict | None:
    """Fetch one ticker with a 30-second timeout. Returns None on any failure."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_fetch_quote_inner, ticker_symbol)
        try:
            return future.result(timeout=FETCH_TIMEOUT)
        except FuturesTimeout:
            print(f"  ⚠ Timeout ({FETCH_TIMEOUT}s) fetching {ticker_symbol}")
            return None
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


def _fetch_one_feed(source_name: str, url: str) -> list[dict]:
    """Fetch a single RSS feed with a requests timeout, return article list."""
    resp = requests.get(url, timeout=FETCH_TIMEOUT)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for entry in feed.entries[:10]:
        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        pub_dt = datetime(*pub[:6]) if pub else None
        items.append({
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", "#"),
            "source": source_name,
            "published": pub_dt,
        })
    return items


def fetch_news() -> list[dict]:
    """Fetch headlines from RSS feeds — each feed gets a 30s timeout."""
    print("Fetching news headlines...")
    articles = []
    for source_name, url in config.RSS_FEEDS:
        try:
            items = _fetch_one_feed(source_name, url)
            articles.extend(items)
            print(f"  ✓ {source_name}: {len(items)} articles")
        except requests.Timeout:
            print(f"  ⚠ Timeout ({FETCH_TIMEOUT}s) fetching RSS: {source_name}")
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
# 2. AUTO-GENERATED SUMMARY (top 3 movers + biggest macro move + risk signal)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_summary(indices, watchlist, macro) -> list[str]:
    """Build a simple summary from fetched data — no API needed."""
    bullets = []

    # ── Top 3 watchlist movers (by absolute daily change) ──
    all_stocks = []
    for sector, stocks in watchlist.items():
        for st in stocks:
            all_stocks.append((sector, st))
    all_stocks.sort(key=lambda x: abs(x[1]["change_pct"]), reverse=True)

    for sector, st in all_stocks[:3]:
        direction = "up" if st["change_pct"] > 0 else "down"
        bullets.append(
            f"{st['ticker']} ({sector}) {direction} {st['change_pct']:+.2f}% "
            f"to ${st['price']:,.2f}."
        )

    # ── Biggest macro move ──
    if macro:
        top_macro = max(macro, key=lambda m: abs(m["change_pct"]))
        direction = "up" if top_macro["change_pct"] > 0 else "down"
        bullets.append(
            f"{top_macro['name']} {direction} {top_macro['change_pct']:+.2f}% "
            f"to ${top_macro['price']:,.2f} — biggest macro move of the day."
        )

    # ── Key risk signal ──
    idx_map = {i["name"]: i for i in indices}
    vix = idx_map.get("VIX")
    if vix:
        if vix["price"] >= 25:
            bullets.append(
                f"⚠ Risk elevated — VIX at {vix['price']:.1f} ({vix['change_pct']:+.2f}%), "
                f"signaling high market fear."
            )
        elif vix["price"] >= 20:
            bullets.append(
                f"⚠ Risk rising — VIX at {vix['price']:.1f} ({vix['change_pct']:+.2f}%), "
                f"above the long-term average."
            )
        elif vix["change_pct"] > 10:
            bullets.append(
                f"⚠ VIX spiked {vix['change_pct']:+.1f}% to {vix['price']:.1f} — "
                f"watch for volatility expansion."
            )
        else:
            bullets.append(
                f"Risk subdued — VIX at {vix['price']:.1f} ({vix['change_pct']:+.2f}%), "
                f"markets calm."
            )

    return bullets


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SECTOR HEATMAP HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def compute_heatmap(watchlist: dict) -> list[dict]:
    """Return sectors sorted best→worst with pre-computed tile colours."""
    result = []
    for sector, stocks in watchlist.items():
        if not stocks:
            continue
        avg = sum(s["change_pct"] for s in stocks) / len(stocks)
        if avg >= 1.5:
            bg, fg = "#0a3320", "#00c853"
        elif avg >= 0.5:
            bg, fg = "#0d2a1c", "#4ade80"
        elif avg > 0:
            bg, fg = "#0d1f18", "#86efac"
        elif avg <= -1.5:
            bg, fg = "#330a0a", "#ff3d3d"
        elif avg <= -0.5:
            bg, fg = "#2a0d0d", "#f87171"
        elif avg < 0:
            bg, fg = "#1f0d0d", "#fca5a5"
        else:
            bg, fg = "#111d33", "#94a3b8"
        result.append({"sector": sector, "avg": avg, "bg": bg, "fg": fg})
    result.sort(key=lambda x: x["avg"], reverse=True)
    return result


# ─── Market status ─────────────────────────────────────────────────────────────

def get_market_status() -> dict:
    """Return the current US equity market session label and colour."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    wd = now_et.weekday()           # 0 = Mon … 6 = Sun
    t  = now_et.hour * 60 + now_et.minute  # minutes since midnight ET

    if wd >= 5:
        return {"label": "Market Closed", "color": "#94a3b8", "bg": "rgba(148,163,184,0.15)"}
    if 4 * 60 <= t < 9 * 60 + 30:
        return {"label": "Pre-Market",    "color": "#f59e0b", "bg": "rgba(245,158,11,0.15)"}
    if 9 * 60 + 30 <= t < 16 * 60:
        return {"label": "● Market Open", "color": "#00c853", "bg": "rgba(0,200,83,0.15)"}
    if 16 * 60 <= t < 20 * 60:
        return {"label": "After-Hours",   "color": "#f59e0b", "bg": "rgba(245,158,11,0.15)"}
    return     {"label": "Market Closed", "color": "#94a3b8", "bg": "rgba(148,163,184,0.15)"}


# ─── News enrichment ───────────────────────────────────────────────────────────

_SOURCE_BADGES = {
    "cnbc":        {"label": "CNBC",         "color": "#ff3d3d", "bg": "rgba(255,61,61,0.18)"},
    "reuters":     {"label": "Reuters",       "color": "#f97316", "bg": "rgba(249,115,22,0.18)"},
    "marketwatch": {"label": "MarketWatch",   "color": "#3b82f6", "bg": "rgba(59,130,246,0.18)"},
    "yahoo":       {"label": "Yahoo Finance", "color": "#a855f7", "bg": "rgba(168,85,247,0.18)"},
    "bloomberg":   {"label": "Bloomberg",     "color": "#4fc3f7", "bg": "rgba(79,195,247,0.18)"},
    "ft":          {"label": "FT",            "color": "#f2a900", "bg": "rgba(242,169,0,0.18)"},
    "wsj":         {"label": "WSJ",           "color": "#e2e8f0", "bg": "rgba(226,232,240,0.12)"},
}

_CATEGORIES = [
    ("CRYPTO",      ["bitcoin","crypto","ethereum","blockchain","btc","eth","coin"]),
    ("AI",          ["artificial intelligence"," ai ","openai","chatgpt","large language","llm","generative"]),
    ("TECH",        ["nvidia","apple","microsoft","google","amazon","meta","chip","semiconductor","software","tech"]),
    ("MACRO",       ["fed ","federal reserve","rate cut","rate hike","inflation","gdp","economy","treasury","yield","central bank","recession","dollar","monetary policy"]),
    ("ENERGY",      ["oil","gas","crude","opec","pipeline","wti","brent","barrel","lng"]),
    ("HEALTH",      ["fda","drug","vaccine","clinical trial","pharma","biotech","weight-loss","obesity"]),
    ("GEOPOLITICAL",["china","russia","ukraine","war","sanction","trade war","tariff","nato","middle east"]),
    ("EARNINGS",    ["earnings","revenue","profit","loss"," eps ","quarterly","guidance","beat","miss"]),
    ("MARKETS",     ["s&p","nasdaq","dow jones","rally","selloff","bull market","bear market"]),
]

_CAT_COLORS = {
    "CRYPTO":       ("#f59e0b", "rgba(245,158,11,0.15)"),
    "AI":           ("#a855f7", "rgba(168,85,247,0.15)"),
    "TECH":         ("#4fc3f7", "rgba(79,195,247,0.15)"),
    "MACRO":        ("#3b82f6", "rgba(59,130,246,0.15)"),
    "ENERGY":       ("#f97316", "rgba(249,115,22,0.15)"),
    "HEALTH":       ("#10b981", "rgba(16,185,129,0.15)"),
    "GEOPOLITICAL": ("#ff3d3d", "rgba(255,61,61,0.15)"),
    "EARNINGS":     ("#00c853", "rgba(0,200,83,0.15)"),
    "MARKETS":      ("#94a3b8", "rgba(148,163,184,0.15)"),
}

_SNAPSHOT_EMOJIS = [
    ("⚠️",  ["⚠", "risk elevated", "spike", "fear", "volatility expansion"]),
    ("🛢️", ["oil", "crude", "wti", "brent", "barrel"]),
    ("₿",  ["bitcoin", "btc", "crypto"]),
    ("💵", ["dollar", "dxy", "usd index"]),
    ("🏦", ["rate ", "fed ", "treasury", "yield", "central bank"]),
    ("💊", ["pharma", "drug", "fda", "lilly", "pfizer"]),
    ("🏭", ["manufacturing", "industrial", "pmi"]),
    ("🥇", ["gold"]),
    ("📉", ["down ", "decline", "drop", "fell ", "slips", "slump"]),
    ("📈", ["up ", "rally", "surge", "jump", "gain", "rise", "record"]),
    ("😌", ["subdued", "calm", "markets calm"]),
]

def enrich_summary(bullets: list[str]) -> list[str]:
    """Prepend a contextual emoji to each summary bullet."""
    out = []
    for b in bullets:
        low = b.lower()
        emoji = ""
        for em, keywords in _SNAPSHOT_EMOJIS:
            if any(kw in low for kw in keywords):
                emoji = em
                break
        out.append(f"{emoji} {b}" if emoji else b)
    return out


def compute_radar(watchlist: dict) -> list[dict]:
    """Identify stocks that need attention: big movers or unusual volume."""
    flags = []
    for sector, stocks in watchlist.items():
        for s in stocks:
            reasons = []
            if abs(s["change_pct"]) >= 2.5:
                reasons.append(f"{'▲' if s['change_pct'] > 0 else '▼'} {s['change_pct']:+.2f}% today")
            if s.get("vol_ratio", 0) >= 1.8:
                reasons.append(f"{s['vol_ratio']:.1f}× avg volume")
            if s.get("range_pct", 50) >= 95:
                reasons.append("Near 52-wk high")
            elif s.get("range_pct", 50) <= 5:
                reasons.append("Near 52-wk low")
            if reasons:
                color = "#00c853" if s["change_pct"] > 0 else ("#ff3d3d" if s["change_pct"] < 0 else "#f59e0b")
                flags.append({
                    "ticker": s["ticker"], "sector": sector,
                    "price": s["price"], "change_pct": s["change_pct"],
                    "reasons": reasons, "color": color,
                })
    flags.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return flags[:8]


def enrich_news(news: list) -> list:
    """Add source badge and category tag to each news item."""
    out = []
    for item in news:
        src_lower = item["source"].lower()
        badge = {"label": item["source"], "color": "#94a3b8", "bg": "rgba(148,163,184,0.12)"}
        for key, b in _SOURCE_BADGES.items():
            if key in src_lower:
                badge = b
                break

        title_lower = item["title"].lower()
        category, cat_color, cat_bg = "", "#64748b", "rgba(100,116,139,0.15)"
        for cat, keywords in _CATEGORIES:
            if any(kw in title_lower for kw in keywords):
                category = cat
                cat_color, cat_bg = _CAT_COLORS.get(cat, ("#64748b", "rgba(100,116,139,0.15)"))
                break

        out.append({**item, "badge": badge,
                    "category": category, "cat_color": cat_color, "cat_bg": cat_bg})
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 4. HTML REPORT TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════════

HTML_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Market Brief — {{ date }}</title>
</head>

{# ── Colour helpers ── #}
{% set sp = indices | selectattr("name","equalto","S&P 500") | list %}
{% set sp_up = sp and sp[0].change_pct > 0 %}
{% set bar_color = '#00c853' if sp_up else '#ff3d3d' %}

{# ── Shared inline-style strings ── #}
{% set S_BODY   = "margin:0;padding:0;background:#0a0f1e;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#e2e8f0;line-height:1.5;" %}
{% set S_WRAP   = "max-width:960px;margin:0 auto;padding:24px 16px;" %}
{% set S_CARD   = "background:#0d1526;border:1px solid #1e2d45;border-radius:10px;padding:24px;margin-bottom:20px;" %}
{% set S_H2     = "font-size:18px;font-weight:700;color:#4fc3f7;margin:0 0 16px 0;padding-bottom:8px;border-bottom:2px solid #1e2d45;" %}
{% set S_TH     = "text-align:left;padding:9px 12px;background:#111d33;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:#4fc3f7;border-bottom:2px solid #1e2d45;" %}
{% set S_TD     = "padding:9px 12px;border-bottom:1px solid #1e2d45;color:#e2e8f0;" %}
{% set S_TD_END = "padding:9px 12px;color:#e2e8f0;" %}

<body style="{{ S_BODY }}">
<div style="{{ S_WRAP }}">

  {# ══ TOP STATUS BAR (green if S&P up, red if down) ══ #}
  <div style="height:4px;background:{{ bar_color }};border-radius:2px 2px 0 0;"></div>

  {# ══ HEADER ══ #}
  <div style="background:linear-gradient(135deg,#0d2137 0%,#0a0f1e 100%);border:1px solid #1e3a5f;border-top:none;padding:28px 32px;margin-bottom:20px;border-radius:0 0 12px 12px;text-align:center;">
    <div style="font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#4fc3f7;margin-bottom:10px;">DAILY MARKET BRIEF</div>
    <div style="font-size:26px;font-weight:700;color:#ffffff;margin-bottom:8px;">{{ date }}</div>
    <div style="margin-bottom:0;">
      <span style="font-size:13px;color:#64748b;">Generated at {{ time }}</span>
      &nbsp;&nbsp;
      <span style="display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:0.5px;color:{{ market_status.color }};background:{{ market_status.bg }};border:1px solid {{ market_status.color }}44;">{{ market_status.label }}</span>
    </div>
  </div>

  {# ══ TODAY'S SNAPSHOT ══ #}
  <div style="{{ S_CARD }}">
    <h2 style="{{ S_H2 }}">Today's Snapshot</h2>
    {% for bullet in summary %}
    {% if '⚠' in bullet or 'elevated' in bullet or 'spike' in bullet.lower() %}
      {% set b_border = '#ff3d3d' %}{% set b_bg = 'rgba(255,61,61,0.07)' %}
    {% elif 'subdued' in bullet.lower() or 'calm' in bullet.lower() %}
      {% set b_border = '#4fc3f7' %}{% set b_bg = 'rgba(79,195,247,0.07)' %}
    {% elif ' up ' in bullet.lower() or ' +' in bullet %}
      {% set b_border = '#00c853' %}{% set b_bg = 'rgba(0,200,83,0.07)' %}
    {% elif ' down ' in bullet.lower() %}
      {% set b_border = '#ff3d3d' %}{% set b_bg = 'rgba(255,61,61,0.07)' %}
    {% else %}
      {% set b_border = '#4fc3f7' %}{% set b_bg = 'rgba(79,195,247,0.07)' %}
    {% endif %}
    <div style="padding:11px 14px;margin-bottom:8px;border-radius:8px;background:{{ b_bg }};border-left:4px solid {{ b_border }};font-size:14px;line-height:1.65;color:#e2e8f0;">{{ bullet }}</div>
    {% endfor %}
  </div>

  {# ══ MARKET OVERVIEW ══ #}
  <div style="{{ S_CARD }}">
    <h2 style="{{ S_H2 }}">Market Overview</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead>
        <tr>
          <th style="{{ S_TH }}">Index</th>
          <th style="{{ S_TH }}">Price</th>
          <th style="{{ S_TH }}">Daily Chg</th>
          <th style="{{ S_TH }}">Weekly Chg</th>
          <th style="{{ S_TH }}">Trend</th>
        </tr>
      </thead>
      <tbody>
        {% for i in indices %}
        {% set dc  = '#00c853' if i.change_pct > 0   else ('#ff3d3d' if i.change_pct < 0   else '#94a3b8') %}
        {% set wc  = '#00c853' if i.weekly_change > 0 else ('#ff3d3d' if i.weekly_change < 0 else '#94a3b8') %}
        {% set tc  = '#00c853' if 'Bull' in i.trend  else ('#ff3d3d' if 'Bear' in i.trend  else '#94a3b8') %}
        {% set tb  = 'rgba(0,200,83,0.12)' if 'Bull' in i.trend else ('rgba(255,61,61,0.12)' if 'Bear' in i.trend else 'rgba(148,163,184,0.12)') %}
        {% set td_b = S_TD if not loop.last else S_TD_END %}
        {% set zebra = '#111d33' if loop.index is odd else 'transparent' %}
        <tr style="background:{{ zebra }};">
          <td style="{{ td_b }}"><strong style="color:#ffffff;">{{ i.name }}</strong></td>
          <td style="{{ td_b }}">{{ "{:,.2f}".format(i.price) }}</td>
          <td style="{{ td_b }}color:{{ dc }};font-weight:700;">{{ "{:+.2f}".format(i.change_pct) }}%</td>
          <td style="{{ td_b }}color:{{ wc }};font-weight:700;">{{ "{:+.2f}".format(i.weekly_change) }}%</td>
          <td style="{{ td_b }}">
            <span style="display:inline-block;padding:2px 9px;border-radius:4px;font-size:11px;font-weight:700;color:{{ tc }};background:{{ tb }};">{{ i.trend }}</span>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  {# ══ WATCHLIST BY SECTOR ══ #}
  <div style="{{ S_CARD }}">
    <h2 style="{{ S_H2 }}">Watchlist — by Sector</h2>

    {# ── SECTOR HEATMAP ── #}
    <table style="width:100%;border-collapse:separate;border-spacing:6px;margin-bottom:20px;">
      {% for row in heatmap | batch(4, '') %}
      <tr>
        {% for tile in row %}
        {% if tile %}
        <td style="width:25%;background:{{ tile.bg }};border:1px solid {{ tile.fg }}33;border-radius:8px;padding:10px 8px;text-align:center;vertical-align:top;">
          <div style="font-size:10px;font-weight:700;color:{{ tile.fg }}99;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:4px;line-height:1.2;">{{ tile.sector }}</div>
          <div style="font-size:17px;font-weight:800;color:{{ tile.fg }};letter-spacing:0.3px;">{{ '▲' if tile.avg > 0 else ('▼' if tile.avg < 0 else '—') }} {{ "{:+.2f}".format(tile.avg) }}%</div>
        </td>
        {% else %}
        <td style="width:25%;"></td>
        {% endif %}
        {% endfor %}
      </tr>
      {% endfor %}
    </table>

    {% for sector, stocks in watchlist.items() %}
    {% if stocks %}
    <div style="font-size:15px;font-weight:800;color:#4fc3f7;margin:{% if loop.first %}0{% else %}24px{% endif %} 0 10px 0;padding:8px 12px 10px;background:rgba(79,195,247,0.06);border-left:3px solid #4fc3f7;border-bottom:2px solid #4fc3f7;border-radius:0 4px 0 0;letter-spacing:0.6px;text-transform:uppercase;">{{ sector }}</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:4px;">
      <thead>
        <tr>
          <th style="{{ S_TH }}">Ticker</th>
          <th style="{{ S_TH }}">Price</th>
          <th style="{{ S_TH }}">Day %</th>
          <th style="{{ S_TH }}">Week %</th>
          <th style="{{ S_TH }}">Vol vs Avg</th>
          <th style="{{ S_TH }}">52-Wk Range</th>
        </tr>
      </thead>
      <tbody>
        {% for s in stocks %}
        {% set dc     = '#00c853' if s.change_pct > 0   else ('#ff3d3d' if s.change_pct < 0   else '#94a3b8') %}
        {% set wc     = '#00c853' if s.weekly_change > 0 else ('#ff3d3d' if s.weekly_change < 0 else '#94a3b8') %}
        {% set dc_bg  = 'rgba(0,200,83,0.15)'  if s.change_pct > 0   else ('rgba(255,61,61,0.15)'  if s.change_pct < 0   else 'rgba(148,163,184,0.06)') %}
        {% set wc_bg  = 'rgba(0,200,83,0.15)'  if s.weekly_change > 0 else ('rgba(255,61,61,0.15)'  if s.weekly_change < 0 else 'rgba(148,163,184,0.06)') %}
        {% set base_bg = '#111d33' if loop.index is odd else 'transparent' %}
        {% set row_bg = 'rgba(0,200,83,0.08)'  if s.change_pct > 2   else ('rgba(255,61,61,0.08)'  if s.change_pct < -2   else base_bg) %}
        {% set d_arrow = '▲' if s.change_pct > 0   else ('▼' if s.change_pct < 0   else '') %}
        {% set w_arrow = '▲' if s.weekly_change > 0 else ('▼' if s.weekly_change < 0 else '') %}
        {% set vol_c  = '#f59e0b' if s.vol_ratio > 1.5 else '#94a3b8' %}
        {% set td_b   = S_TD if not loop.last else S_TD_END %}
        {# SVG range bar: 150×16, gradient track, white dot at range_pct position #}
        {% set dot_x  = (s.range_pct / 100 * 150) | round(1) %}
        <tr style="background:{{ row_bg }};">
          <td style="{{ td_b }}"><strong style="color:#ffffff;font-size:15px;font-weight:800;letter-spacing:0.4px;">{{ s.ticker }}</strong></td>
          <td style="{{ td_b }}font-size:13px;">{{ "{:,.2f}".format(s.price) }}</td>
          <td style="padding:9px 12px;{% if not loop.last %}border-bottom:1px solid #1e2d45;{% endif %}background:{{ dc_bg }};color:{{ dc }};font-weight:800;font-size:13px;">{{ d_arrow }} {{ "{:+.2f}".format(s.change_pct) }}%</td>
          <td style="padding:9px 12px;{% if not loop.last %}border-bottom:1px solid #1e2d45;{% endif %}background:{{ wc_bg }};color:{{ wc }};font-weight:800;font-size:13px;">{{ w_arrow }} {{ "{:+.2f}".format(s.weekly_change) }}%</td>
          <td style="{{ td_b }}color:{{ vol_c }};font-size:12px;">{{ "{:,.0f}".format(s.volume) }}<br><span style="color:#64748b;">({{ "{:.1f}x".format(s.vol_ratio) }})</span></td>
          <td style="{{ td_b }}min-width:150px;">
            <div style="font-size:10px;color:#64748b;margin-bottom:4px;">
              {{ "{:,.0f}".format(s.low_52) }} — {{ "{:,.0f}".format(s.high_52) }}
              &nbsp;<span style="color:#4fc3f7;font-weight:700;">{{ s.range_pct | round(0) | int }}%</span>
            </div>
            <svg width="150" height="16" style="display:block;overflow:visible;">
              <defs>
                <linearGradient id="rg_{{ s.ticker }}" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%"   stop-color="#ff3d3d"/>
                  <stop offset="50%"  stop-color="#eab308"/>
                  <stop offset="100%" stop-color="#00c853"/>
                </linearGradient>
              </defs>
              <rect x="0" y="2" width="150" height="12" rx="6" fill="#1a2540"/>
              <rect x="0" y="2" width="150" height="12" rx="6" fill="url(#rg_{{ s.ticker }})"/>
              <circle cx="{{ dot_x }}" cy="8" r="6" fill="#ffffff" stroke="#4fc3f7" stroke-width="2.5"/>
            </svg>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% endif %}
    {% endfor %}
  </div>

  {# ══ MACRO DASHBOARD — 2×2 card grid ══ #}
  <div style="{{ S_CARD }}">
    <h2 style="{{ S_H2 }}">Macro Dashboard</h2>
    {% for row in macro | batch(2) %}
    <table style="width:100%;border-collapse:separate;border-spacing:12px;margin-bottom:{% if not loop.last %}0{% else %}0{% endif %};">
      <tr>
        {% for m in row %}
        {% set dc      = '#00c853' if m.change_pct > 0    else ('#ff3d3d' if m.change_pct < 0    else '#94a3b8') %}
        {% set wc      = '#00c853' if m.weekly_change > 0  else ('#ff3d3d' if m.weekly_change < 0  else '#94a3b8') %}
        {% set card_bg = 'rgba(0,200,83,0.06)'  if m.change_pct > 0 else ('rgba(255,61,61,0.06)'  if m.change_pct < 0 else 'rgba(148,163,184,0.04)') %}
        {% set bdr     = '#00c85344' if m.change_pct > 0  else ('#ff3d3d44' if m.change_pct < 0  else '#1e2d45') %}
        {% set d_arrow = '▲' if m.change_pct > 0    else ('▼' if m.change_pct < 0    else '—') %}
        {% set w_arrow = '▲' if m.weekly_change > 0  else ('▼' if m.weekly_change < 0  else '—') %}
        <td style="width:50%;background:{{ card_bg }};border:1px solid {{ bdr }};border-radius:10px;padding:18px 20px;vertical-align:top;">
          <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">{{ m.name }}</div>
          <div style="font-size:26px;font-weight:800;color:#ffffff;letter-spacing:0.5px;margin-bottom:10px;">{{ "{:,.2f}".format(m.price) }}</div>
          <div style="font-size:20px;font-weight:800;color:{{ dc }};margin-bottom:6px;">{{ d_arrow }} {{ "{:+.2f}".format(m.change_pct) }}%</div>
          <div style="font-size:12px;color:#64748b;">Week &nbsp;<span style="color:{{ wc }};font-weight:700;">{{ w_arrow }} {{ "{:+.2f}".format(m.weekly_change) }}%</span></div>
        </td>
        {% if loop.length == 1 %}
        <td style="width:50%;"></td>
        {% endif %}
        {% endfor %}
      </tr>
    </table>
    {% endfor %}
  </div>

  {# ══ TOP HEADLINES ══ #}
  <div style="{{ S_CARD }}">
    <h2 style="{{ S_H2 }}">Top Headlines</h2>
    {% for n in news %}
    {% set row_bg = '#111d33' if loop.index is odd else 'transparent' %}
    <div style="padding:11px 12px;{% if not loop.last %}border-bottom:1px solid #1e2d45;{% endif %}background:{{ row_bg }};border-radius:4px;">
      <div style="margin-bottom:5px;">
        <span style="display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:0.4px;color:{{ n.badge.color }};background:{{ n.badge.bg }};margin-right:6px;">{{ n.badge.label }}</span>
        {% if n.category %}
        <span style="display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:0.4px;color:{{ n.cat_color }};background:{{ n.cat_bg }};">[{{ n.category }}]</span>
        {% endif %}
      </div>
      <a href="{{ n.link }}" target="_blank" style="color:#e2e8f0;text-decoration:none;font-size:14px;font-weight:500;line-height:1.5;">{{ n.title }}</a>
    </div>
    {% endfor %}
  </div>

  {# ══ RADAR — flagged stocks ══ #}
  {% if radar %}
  <div style="{{ S_CARD }}">
    <h2 style="{{ S_H2 }}">🔔 Radar — Stocks to Watch</h2>
    {% for r in radar %}
    {% set row_bg = '#111d33' if loop.index is odd else 'transparent' %}
    <div style="padding:12px 14px;margin-bottom:6px;border-radius:8px;background:{{ row_bg }};border-left:4px solid {{ r.color }};">
      <span style="font-size:17px;font-weight:900;color:#ffffff;letter-spacing:0.5px;">{{ r.ticker }}</span>
      <span style="font-size:13px;color:#64748b;margin-left:8px;">{{ r.sector }}</span>
      <span style="font-size:14px;font-weight:800;color:{{ r.color }};margin-left:12px;">{{ "{:+.2f}".format(r.change_pct) }}%</span>
      <span style="font-size:13px;color:#ffffff;margin-left:4px;">${{ "{:,.2f}".format(r.price) }}</span>
      <div style="margin-top:4px;">
        {% for reason in r.reasons %}
        <span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;color:{{ r.color }};background:{{ r.color }}22;margin-right:6px;margin-top:2px;">{{ reason }}</span>
        {% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {# ══ FOOTER ══ #}
  <div style="text-align:center;font-size:12px;color:#4a5568;padding:20px 16px 10px;border-top:1px solid #1e2d45;line-height:1.8;">
    <div style="margin-bottom:6px;color:#64748b;font-weight:600;">Market Brief Generator</div>
    <div>Data: <span style="color:#94a3b8;">Yahoo Finance API</span> &nbsp;&middot;&nbsp; News: <span style="color:#94a3b8;">CNBC, Reuters, MarketWatch, Yahoo Finance RSS</span></div>
    <div>Generated: <span style="color:#94a3b8;">{{ date }} at {{ time }}</span></div>
    <div style="margin-top:6px;color:#4fc3f7;font-weight:600;">Next brief tomorrow at 7:00 AM Milan time</div>
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
    return generate_summary(demo_indices(), demo_watchlist(), demo_macro())


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
        indices  = demo_indices()
        watchlist = demo_watchlist()
        macro    = demo_macro()
        news     = demo_news()
        summary  = demo_summary()
    else:
        # Fetch all data
        indices   = fetch_indices()
        watchlist = fetch_watchlist()
        macro     = fetch_macro()
        news      = fetch_news()
        summary   = generate_summary(indices, watchlist, macro)

    heatmap       = compute_heatmap(watchlist)
    market_status = get_market_status()
    news          = enrich_news(news)
    summary       = enrich_summary(summary)
    radar         = compute_radar(watchlist)

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
        heatmap=heatmap,
        market_status=market_status,
        radar=radar,
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


def run_healthcheck():
    """Full data-source health check: every ticker, every RSS feed."""
    print(f"\n{'='*60}")
    print(f"  DATA SOURCE HEALTH CHECK")
    print(f"  Timeout per source: {FETCH_TIMEOUT}s")
    print(f"{'='*60}\n")

    results = {"pass": [], "fail": []}

    # ── Yahoo Finance tickers ──────────────────────────────────────────────
    all_tickers = []

    # Indices
    for name, symbol in config.INDICES.items():
        all_tickers.append((symbol, f"Index: {name}"))

    # Watchlist
    for sector, tickers in config.WATCHLIST.items():
        for t in tickers:
            all_tickers.append((t, f"Watchlist: {sector}"))

    # Macro
    for name, symbol in config.MACRO.items():
        all_tickers.append((symbol, f"Macro: {name}"))

    print(f"── Yahoo Finance ({len(all_tickers)} tickers) ──")
    for idx, (symbol, label) in enumerate(all_tickers, 1):
        t0 = time.time()
        q = fetch_quote(symbol)
        elapsed = time.time() - t0
        if q:
            results["pass"].append(symbol)
            print(f"  [{idx:2d}/{len(all_tickers)}] ✓ {symbol:10s} ${q['price']:>12,.2f}  {q['change_pct']:+.2f}%  ({elapsed:.1f}s)  — {label}")
        else:
            results["fail"].append(symbol)
            print(f"  [{idx:2d}/{len(all_tickers)}] ✗ {symbol:10s} FAILED  ({elapsed:.1f}s)  — {label}")

    # ── RSS Feeds ──────────────────────────────────────────────────────────
    print(f"\n── RSS Feeds ({len(config.RSS_FEEDS)} sources) ──")
    for source_name, url in config.RSS_FEEDS:
        t0 = time.time()
        try:
            items = _fetch_one_feed(source_name, url)
            elapsed = time.time() - t0
            results["pass"].append(source_name)
            print(f"  ✓ {source_name:20s} {len(items):2d} articles  ({elapsed:.1f}s)")
        except requests.Timeout:
            elapsed = time.time() - t0
            results["fail"].append(source_name)
            print(f"  ✗ {source_name:20s} TIMEOUT  ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            results["fail"].append(source_name)
            print(f"  ✗ {source_name:20s} ERROR: {e}  ({elapsed:.1f}s)")

    # ── Summary ────────────────────────────────────────────────────────────
    total = len(results["pass"]) + len(results["fail"])
    print(f"\n{'='*60}")
    print(f"  HEALTH CHECK COMPLETE")
    print(f"  ✓ Passed: {len(results['pass'])}/{total}")
    print(f"  ✗ Failed: {len(results['fail'])}/{total}")
    if results["fail"]:
        print(f"  Failed sources: {', '.join(results['fail'])}")
    print(f"{'='*60}\n")
    return results


if __name__ == "__main__":
    if HEALTHCHECK_MODE:
        run_healthcheck()
    else:
        generate_report()
