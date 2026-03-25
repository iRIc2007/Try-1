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
}

# ─── News RSS Feeds ─────────────────────────────────────────────────────────
# Free RSS feeds — no API key needed. Add or remove as you like.
RSS_FEEDS = [
    ("Yahoo Finance",  "https://finance.yahoo.com/news/rssindex"),
    ("CNBC Top News",  "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("Reuters Business","https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"),
    ("MarketWatch",    "https://feeds.marketwatch.com/marketwatch/topstories/"),
]

# ─── Report Settings ────────────────────────────────────────────────────────
MAX_HEADLINES = 20          # How many news headlines to show
REPORT_DIR = "reports"      # Folder where HTML reports are saved
