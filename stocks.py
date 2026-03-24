"""
stocks.py — VEGA
═══════════════════════════════════════════════════════════════════
Live NSE/BSE stock prices via Yahoo Finance v8 API.
No API key. No extra library. Pure httpx (already in requirements).

FLOW:
  user: "tata steel stock price"
  → extract_symbol()  — name → NSE ticker
  → get_stock_price() — Yahoo Finance v8 → formatted string
  → brain.py returns it directly, no LLM needed

SYMBOL RESOLUTION — two-pass:
  Pass 1: KNOWN_STOCKS dict  (instant, covers popular ones)
  Pass 2: Yahoo Finance search API  (handles anything not in dict)

ADDING MORE STOCKS:
  Just add to KNOWN_STOCKS below. Format: 'common name': 'NSE_SYMBOL'
═══════════════════════════════════════════════════════════════════
"""

import re
import asyncio
import httpx

# ── Common stock name → NSE symbol ────────────────────────────────────────────
# Covers the most searched stocks. Yahoo search handles everything else.
KNOWN_STOCKS = {
    # Nifty 50 heavyweights
    "reliance": "RELIANCE",
    "reliance industries": "RELIANCE",
    "tcs": "TCS",
    "tata consultancy": "TCS",
    "infosys": "INFY",
    "infy": "INFY",
    "hdfc bank": "HDFCBANK",
    "hdfc": "HDFCBANK",
    "icici bank": "ICICIBANK",
    "icici": "ICICIBANK",
    "sbi": "SBIN",
    "state bank": "SBIN",
    "wipro": "WIPRO",
    "hcl": "HCLTECH",
    "hcl tech": "HCLTECH",
    "bajaj finance": "BAJFINANCE",
    "bajaj finserv": "BAJAJFINSV",
    "kotak": "KOTAKBANK",
    "kotak bank": "KOTAKBANK",
    "axis bank": "AXISBANK",
    "axis": "AXISBANK",
    "maruti": "MARUTI",
    "maruti suzuki": "MARUTI",
    "asian paints": "ASIANPAINT",
    "ultratech": "ULTRACEMCO",
    "ultratech cement": "ULTRACEMCO",
    "titan": "TITAN",
    "itc": "ITC",
    "bharti airtel": "BHARTIARTL",
    "airtel": "BHARTIARTL",
    "larsen": "LT",
    "l&t": "LT",
    "sun pharma": "SUNPHARMA",
    "sun pharmaceutical": "SUNPHARMA",
    "ntpc": "NTPC",
    "power grid": "POWERGRID",
    "ongc": "ONGC",
    "bpcl": "BPCL",
    "hindunilvr": "HINDUNILVR",
    "hindustan unilever": "HINDUNILVR",
    "hul": "HINDUNILVR",
    "nestle": "NESTLEIND",
    "dr reddy": "DRREDDY",
    "cipla": "CIPLA",
    "divis": "DIVISLAB",
    "tech mahindra": "TECHM",
    "tata motors": "TATAMOTORS",
    "m&m": "M&M",
    "mahindra": "M&M",
    "jswsteel": "JSWSTEEL",
    "jsw steel": "JSWSTEEL",
    "hindalco": "HINDALCO",
    "tata steel": "TATASTEEL",
    # Others frequently searched
    "adani enterprises": "ADANIENT",
    "adani": "ADANIENT",
    "adani ports": "ADANIPORTS",
    "adani green": "ADANIGREEN",
    "adani power": "ADANIPOWER",
    "adani total gas": "ATGL",
    "zomato": "ZOMATO",
    "paytm": "PAYTM",
    "nykaa": "NYKAA",
    "delhivery": "DELHIVERY",
    "policybazaar": "POLICYBZR",
    "irctc": "IRCTC",
    "irfc": "IRFC",
    "coal india": "COALINDIA",
    "vedanta": "VEDL",
    "indusind bank": "INDUSINDBK",
    "indusind": "INDUSINDBK",
    "yes bank": "YESBANK",
    "bandhan bank": "BANDHANBNK",
    "bank of baroda": "BANKBARODA",
    "canara bank": "CANBK",
    "pnb": "PNB",
    "punjab national bank": "PNB",
    "idfc first": "IDFCFIRSTB",
    "tata power": "TATAPOWER",
    "tata chemicals": "TATACHEM",
    "tata consumer": "TATACONSUM",
    "tata communications": "TATACOMM",
    "godrej consumer": "GODREJCP",
    "godrej properties": "GODREJPROP",
    "pidilite": "PIDILITIND",
    "berger paints": "BERGEPAINT",
    "bajaj auto": "BAJAJ-AUTO",
    "hero motocorp": "HEROMOTOCO",
    "hero moto": "HEROMOTOCO",
    "eicher motors": "EICHERMOT",
    "tvs motor": "TVSMOTOR",
    "motherson": "MOTHERSON",
    "havells": "HAVELLS",
    "voltas": "VOLTAS",
    "whirlpool": "WHIRLPOOL",
    "dixon": "DIXON",
    "amber enterprises": "AMBER",
    "mrf": "MRF",
    "apollo tyres": "APOLLOTYRE",
    "balkrishna industries": "BALKRISIND",
    "bki": "BALKRISIND",
    "grasim": "GRASIM",
    "shree cement": "SHREECEM",
    "acc": "ACC",
    "ambuja cement": "AMBUJACEM",
    "dalmia bharat": "DALBHARAT",
    "nifty": "^NSEI",       # Nifty 50 index
    "nifty 50": "^NSEI",
    "sensex": "^BSESN",     # BSE Sensex index
    "banknifty": "^NSEBANK",
    "bank nifty": "^NSEBANK",
}

_TIMEOUT = 7  # seconds


# ── Symbol resolution ─────────────────────────────────────────────────────────

def _name_to_symbol(query: str) -> str | None:
    """
    Pass 1: check KNOWN_STOCKS dict.
    Strips common filler words first for better matching.
    """
    q = re.sub(
        r'\b(stock|share|price|today|now|current|live|nse|bse|equity|ltd|limited)\b',
        '', query, flags=re.IGNORECASE
    ).strip().lower()
    q = re.sub(r'\s+', ' ', q).strip()

    # Exact match
    if q in KNOWN_STOCKS:
        return KNOWN_STOCKS[q]

    # Partial match — find longest key that's a substring of query
    matches = [(k, v) for k, v in KNOWN_STOCKS.items() if k in q]
    if matches:
        return max(matches, key=lambda x: len(x[0]))[1]

    # Check if query itself looks like a raw symbol (all caps, 2-10 chars)
    raw = q.upper().replace(' ', '')
    if re.match(r'^[A-Z&\-]{2,10}$', raw):
        return raw

    return None


async def _yahoo_search_symbol(query: str) -> str | None:
    """
    Pass 2: Yahoo Finance symbol search — handles anything not in KNOWN_STOCKS.
    Returns best NSE/BSE match or None.
    """
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=5&newsCount=0"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            data = r.json()
        quotes = data.get("quotes", [])
        # Prefer NSE (.NS) over BSE (.BO) over others
        for exchange_suffix in (".NS", ".BO"):
            for q in quotes:
                sym = q.get("symbol", "")
                if sym.endswith(exchange_suffix):
                    return sym
        # Fallback — return first result if any
        if quotes:
            return quotes[0].get("symbol")
    except Exception as e:
        print(f"[Stocks] Yahoo search error: {e}")
    return None


def _make_symbol(raw: str) -> str:
    """Add .NS suffix if not already an index or suffixed symbol."""
    if raw.startswith("^") or "." in raw:
        return raw
    return f"{raw}.NS"


# ── Price fetch ───────────────────────────────────────────────────────────────

async def get_stock_price(query: str) -> str:
    """
    Main entry. Called by brain.py.
    Returns formatted price string with emotion tag.
    """
    # Resolve symbol
    symbol_raw = _name_to_symbol(query)

    if not symbol_raw:
        # Try Yahoo search as fallback
        symbol_raw = await _yahoo_search_symbol(query)

    if not symbol_raw:
        return (
            f"Couldn't find a stock matching '{query}', bro. "
            f"Try using the exact company name or NSE symbol.\n[EMOTION:confused]"
        )

    symbol = _make_symbol(symbol_raw)
    print(f"[Stocks] {query!r} → {symbol}")

    # Fetch from Yahoo Finance v8
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            data = r.json()

        result = data.get("chart", {}).get("result")
        error  = data.get("chart", {}).get("error")

        if error or not result:
            return f"Couldn't fetch price for {symbol}. Market might be closed or symbol invalid.\n[EMOTION:nervous]"

        meta   = result[0]["meta"]
        price  = meta.get("regularMarketPrice")
        prev   = meta.get("chartPreviousClose") or meta.get("previousClose")
        name   = meta.get("longName") or meta.get("shortName") or symbol_raw
        high52 = meta.get("fiftyTwoWeekHigh")
        low52  = meta.get("fiftyTwoWeekLow")
        curr   = meta.get("currency", "INR")
        symbol_clean = symbol.replace(".NS", "").replace(".BO", "")

        if not price:
            return f"Price data unavailable for {symbol} right now.\n[EMOTION:nervous]"

        # Format currency symbol
        curr_sym = "₹" if curr == "INR" else curr + " "

        # Change vs previous close
        if prev:
            change = round(price - prev, 2)
            pct    = round((change / prev) * 100, 2)
            sign   = "+" if change >= 0 else ""
            arrow  = "▲" if change >= 0 else "▼"
            change_str = f"{arrow} {sign}{change} ({sign}{pct}%)"
            emotion = "excited" if pct > 2 else "happy" if pct > 0 else "sad" if pct < -2 else "neutral"
        else:
            change_str = ""
            emotion = "neutral"

        # Build response
        lines = [f"📈 {name}  ({symbol_clean})"]
        lines.append(f"{curr_sym}{price:,.2f}  {change_str}".strip())
        if high52 and low52:
            lines.append(f"52W  {curr_sym}{low52:,.2f} – {curr_sym}{high52:,.2f}")

        return "\n".join(lines) + f"\n[EMOTION:{emotion}]"

    except httpx.TimeoutException:
        return f"Stock server timed out. Try again in a moment.\n[EMOTION:nervous]"
    except Exception as e:
        print(f"[Stocks] fetch error: {e}")
        return f"Couldn't fetch {symbol} right now. {str(e)[:60]}\n[EMOTION:nervous]"


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    async def _test():
        queries = [
            "tata steel stock price",
            "reliance share price today",
            "what is infosys stock now",
            "nifty 50",
            "ZOMATO",
            "some random company xyz",
        ]
        for q in queries:
            print(f"\n>> {q}")
            result = await get_stock_price(q)
            print(result)

    asyncio.run(_test())