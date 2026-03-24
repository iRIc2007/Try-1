"""
Configuration for Market Brief Generator.
Edit this file to customize your watchlist and settings.
"""

# ─── Your Anthropic API Key ─────────────────────────────────────────────────
# Get one free at: https://console.anthropic.com/settings/keys
# Paste it between the quotes, or set the ANTHROPIC_API_KEY environment variable.
ANTHROPIC_API_KEY = ""

# ─── Watchlist by Sector ────────────────────────────────────────────────────
# Each entry: ticker symbol → displayed in the report grouped by sector.
# Edit freely — add/remove tickers as you like.
WATCHLIST = {
    "Tech / AI": [
        "AAPL",   # Apple
        "MSFT",   # Microsoft
        "GOOGL",  # Alphabet
        "META",   # Meta Platforms
        "AMZN",   # Amazon
        "CRM",    # Salesforce
        "ORCL",   # Oracle
    ],
    "Semiconductors": [
        "NVDA",   # NVIDIA
        "AMD",    # AMD
        "AVGO",   # Broadcom
        "TSM",    # TSMC
        "INTC",   # Intel
        "QCOM",   # Qualcomm
    ],
    "Healthcare": [
        "UNH",    # UnitedHealth
        "JNJ",    # Johnson & Johnson
        "LLY",    # Eli Lilly
        "PFE",    # Pfizer
        "ABBV",   # AbbVie
        "MRK",    # Merck
    ],
    "Energy": [
        "XOM",    # ExxonMobil
        "CVX",    # Chevron
        "COP",    # ConocoPhillips
        "SLB",    # Schlumberger
        "EOG",    # EOG Resources
    ],
    "Utilities": [
        "NEE",    # NextEra Energy
        "DUK",    # Duke Energy
        "SO",     # Southern Company
        "D",      # Dominion Energy
        "AEP",    # American Electric Power
    ],
    "Infrastructure / Industrials": [
        "CAT",    # Caterpillar
        "DE",     # Deere & Co
        "UNP",    # Union Pacific
        "HON",    # Honeywell
        "GE",     # GE Aerospace
    ],
    "Financials": [
        "JPM",    # JPMorgan Chase
        "GS",     # Goldman Sachs
        "V",      # Visa
        "MA",     # Mastercard
        "BRK-B",  # Berkshire Hathaway
    ],
    "Consumer / Retail": [
        "WMT",    # Walmart
        "COST",   # Costco
        "NKE",    # Nike
        "MCD",    # McDonald's
        "SBUX",   # Starbucks
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
