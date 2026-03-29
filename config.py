"""
Configuration for Market Brief Generator.
Edit this file to customize your watchlist and settings.
"""

# ─── Watchlist by Sector ────────────────────────────────────────────────────
# Each entry: ticker symbol → displayed in the report grouped by sector.
# Edit freely — add/remove tickers as you like.
WATCHLIST = {
    "Tech / AI": [
        "AAPL",   # Apple
        "MSFT",   # Microsoft
        "GOOGL",  # Alphabet
    ],
    "Semiconductors": [
        "NVDA",   # NVIDIA
        "AMD",    # AMD
        "TSM",    # TSMC
    ],
    "Healthcare": [
        "LLY",    # Eli Lilly
        "UNH",    # UnitedHealth
        "ABBV",   # AbbVie
    ],
    "Energy": [
        "XOM",    # ExxonMobil
        "COP",    # ConocoPhillips
        "CVX",    # Chevron
    ],
    "Utilities": [
        "NEE",    # NextEra Energy
        "SO",     # Southern Company
        "DUK",    # Duke Energy
    ],
    "Industrials": [
        "CAT",    # Caterpillar
        "GE",     # GE Aerospace
        "DE",     # Deere & Co
    ],
    "Financials": [
        "JPM",    # JPMorgan Chase
        "GS",     # Goldman Sachs
        "V",      # Visa
    ],
    "Consumer": [
        "COST",   # Costco
        "WMT",    # Walmart
        "NKE",    # Nike
    ],
}

# ─── Market Overview Indices ────────────────────────────────────────────────
# These are fetched automatically — no need to change unless you want to.
INDICES = {
    "S&P 500":    "^GSPC",
    "NASDAQ":     "^IXIC",
    "DOW":        "^DJI",
    "VIX":        "^VIX",
    "10Y Treasury": "^TNX",
}

# ─── Macro Assets ───────────────────────────────────────────────────────────
MACRO = {
    "USD Index (DXY)": "DX-Y.NYB",
    "Gold":            "GC=F",
    "Crude Oil (WTI)": "CL=F",
    "Bitcoin":         "BTC-USD",
    "EUR/USD":         "EURUSD=X",
    "USD/JPY":         "JPY=X",
}

# ─── News RSS Feeds ─────────────────────────────────────────────────────────
# Free RSS feeds — no API key needed. Add or remove as you like.
RSS_FEEDS = [
    ("Yahoo Finance",  "https://finance.yahoo.com/news/rssindex"),
    ("CNBC Top News",  "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("Reuters Business","https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"),
    ("MarketWatch",    "https://feeds.marketwatch.com/marketwatch/topstories/"),
]

# ─── Portfolio Positions ───────────────────────────────────────────────────
# Each position: ticker, shares held, average cost (in native currency).
# NFLX is in USD — will be converted to EUR at runtime using EUR/USD rate.
PORTFOLIO = [
    {"ticker": "PPFB.DE",  "name": "iShares Physical Gold",     "shares": 20,  "avg_cost": 86.2075, "currency": "EUR"},
    {"ticker": "GUARD.PA", "name": "BNP Europe Defense",         "shares": 60,  "avg_cost": 12.185,  "currency": "EUR"},
    {"ticker": "UTIW.MI",  "name": "Amundi World Utilities",     "shares": 100, "avg_cost": 13.885,  "currency": "EUR"},
    {"ticker": "CSKR.MI",  "name": "iShares MSCI Korea",         "shares": 4,   "avg_cost": 312.50,  "currency": "EUR"},
    {"ticker": "ESIH.DE",  "name": "iShares Europe Health Care", "shares": 120, "avg_cost": 7.5415,  "currency": "EUR"},
    {"ticker": "VWCE.DE",  "name": "Vanguard FTSE All-World",    "shares": 95,  "avg_cost": 147.61,  "currency": "EUR"},
    {"ticker": "NFLX",     "name": "Netflix",                    "shares": 10,  "avg_cost": 83.00831,"currency": "USD"},
    {"ticker": "SXLE.MI",  "name": "SPDR US Energy Select",      "shares": 10,  "avg_cost": 32.21,   "currency": "EUR"},
]

# Benchmark for portfolio comparison
PORTFOLIO_BENCHMARK = {"name": "MSCI World", "ticker": "VWCE.DE"}

# ─── Report Settings ────────────────────────────────────────────────────────
MIN_HEADLINES = 6           # Exact headline count
MAX_HEADLINES = 6           # Exact headline count
REPORT_DIR = "reports"      # Folder where HTML reports are saved
