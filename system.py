"""
system.py — Tier 1 Zero-LLM Handler for VEGA
Handles: PC stats, math, unit conversion, datetime
No API calls. No LLM. Pure Python.
"""

import ast
import re
import math
from datetime import datetime


# ─────────────────────────────────────────────
# ENTRY POINT — called from brain.py
# ─────────────────────────────────────────────

def handle_system(query: str) -> str | None:
    """
    Routes query to correct Tier 1 handler.
    Returns a formatted string response, or None if not a system query.
    brain.py checks: if result is not None, skip LLM entirely.
    """
    q = query.lower().strip()

    result = (
        _handle_pc_stats(q) or
        _handle_math(q) or
        _handle_unit_conversion(query) or  # pass original for number parsing
        _handle_datetime(q)
    )
    return result


# ─────────────────────────────────────────────
# PC STATS
# ─────────────────────────────────────────────

_PC_TRIGGERS = [
    "cpu", "ram", "memory", "battery", "disk", "storage",
    "pc stats", "system stats", "system info", "how much ram",
    "how much battery", "how much cpu", "performance"
]

def _handle_pc_stats(q: str) -> str | None:
    if not any(t in q for t in _PC_TRIGGERS):
        return None

    import psutil  # lazy — only loaded when actually needed
    parts = []

    # CPU
    if any(t in q for t in ["cpu", "processor", "performance", "pc stats", "system stats", "system info"]):
        cpu = psutil.cpu_percent(interval=0.5)
        cpu_count = psutil.cpu_count(logical=True)
        parts.append(f"🖥️ CPU: {cpu}% usage ({cpu_count} logical cores)")

    # RAM
    if any(t in q for t in ["ram", "memory", "pc stats", "system stats", "system info"]):
        ram = psutil.virtual_memory()
        used = round(ram.used / (1024 ** 3), 1)
        total = round(ram.total / (1024 ** 3), 1)
        percent = ram.percent
        parts.append(f"🧠 RAM: {used}GB / {total}GB ({percent}% used)")

    # Battery
    if any(t in q for t in ["battery", "charge", "pc stats", "system stats", "system info"]):
        try:
            batt = psutil.sensors_battery()
            if batt:
                status = "charging ⚡" if batt.power_plugged else "on battery 🔋"
                parts.append(f"🔋 Battery: {round(batt.percent)}% — {status}")
            else:
                parts.append("🔋 Battery: not detected (desktop?)")
        except Exception:
            parts.append("🔋 Battery: unavailable")

    # Disk
    if any(t in q for t in ["disk", "storage", "space", "pc stats", "system stats", "system info"]):
        disk = psutil.disk_usage("/")
        used = round(disk.used / (1024 ** 3), 1)
        total = round(disk.total / (1024 ** 3), 1)
        free = round(disk.free / (1024 ** 3), 1)
        parts.append(f"💾 Disk: {used}GB used / {total}GB total ({free}GB free)")

    if not parts:
        # Generic full stats fallback
        return _handle_pc_stats("pc stats")

    return "\n".join(parts)


# ─────────────────────────────────────────────
# MATH / CALCULATOR
# ─────────────────────────────────────────────

_MATH_TRIGGERS = [
    "calculate", "calc", "what is", "what's", "solve",
    "how much is", "equals", "compute", "evaluate",
    "multiply", "multiplied", "divide", "divided",
    "times", "plus", "minus", "squared", "cubed",
    "percent of", "modulo", " mod ", "power of", "to the power",
    "sum of", "product of", "difference of", "quotient of",
]

def _translate_math_words(q: str) -> str:
    """Translate natural language math words to operators before eval."""
    q = re.sub(r"\bsquared\b", "**2", q)
    q = re.sub(r"\bcubed\b", "**3", q)
    q = re.sub(r"\bto\s+the\s+power\s+of\b", "**", q)
    q = re.sub(r"\bpower\s+of\b", "**", q)
    q = re.sub(r"\bmultiplied\s+by\b", "*", q)
    q = re.sub(r"\bdivided\s+by\b", "/", q)
    q = re.sub(r"\btimes\b", "*", q)
    q = re.sub(r"\bplus\b", "+", q)
    q = re.sub(r"\bminus\b", "-", q)
    q = re.sub(r"\bmodulo\b|\bmod\b", "%", q)
    # Handle "X percent of Y" → (X/100)*Y
    q = re.sub(r"(\d+\.?\d*)\s*%?\s*percent\s+of\s+(\d+\.?\d*)",
               lambda m: str(float(m.group(1))/100 * float(m.group(2))), q)
    return q

# Allowed math functions for safe eval
_SAFE_NAMES = {
    "abs": abs, "round": round, "pow": pow,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "pi": math.pi, "e": math.e,
    "floor": math.floor, "ceil": math.ceil,
}

def _safe_eval(expr: str):
    """Safely evaluate a math expression."""
    try:
        # Remove anything that isn't a math character
        cleaned = re.sub(r'[^0-9+\-*/().%^ a-z]', '', expr.lower())
        cleaned = cleaned.replace('^', '**')  # support caret for power
        # Use ast to parse, then eval with restricted namespace
        tree = ast.parse(cleaned, mode='eval')
        result = eval(compile(tree, '<string>', 'eval'), {"__builtins__": {}}, _SAFE_NAMES)
        return result
    except Exception:
        return None

def _handle_math(q: str) -> str | None:
    # Must have a digit to be a math query
    if not re.search(r'\d', q):
        return None
    if not any(t in q.lower() for t in _MATH_TRIGGERS):
        # Still try if it looks purely like an expression: "34 * 56"
        if not re.match(r'^[\d\s+\-*/().^%]+$', q.strip()):
            return None

    # Translate natural language words to operators first
    expr = _translate_math_words(q.lower())

    # Strip trigger/filler words
    strip_words = [
        "calculate", "calc", "what is", "what's", "solve", "how much is",
        "equals", "compute", "evaluate", "multiply", "divide",
        "sum of", "product of", "difference of", "quotient of",
        "whats", "the", "result", "of", "?"
    ]
    for t in strip_words:
        expr = re.sub(r'\b' + re.escape(t) + r'\b', ' ', expr, flags=re.IGNORECASE)
    expr = re.sub(r'\s+', ' ', expr).strip()

    result = _safe_eval(expr)
    if result is None:
        return None

    # Clean output — no .0 for whole numbers
    if isinstance(result, float) and result.is_integer():
        result = int(result)

    # Build readable original expression for display
    display = re.sub(r'\s+', ' ', q.replace("?","").strip())
    return f"🧮 {display} = **{result}**"


# ─────────────────────────────────────────────
# UNIT CONVERSION
# ─────────────────────────────────────────────

def _handle_unit_conversion(query: str) -> str | None:
    q = query.lower().strip()

    # Must contain a number
    if not re.search(r'\d', q):
        return None

    result = (
        _convert_temperature(q) or
        _convert_length(q) or
        _convert_weight(q) or
        _convert_currency_hint(q)
    )
    return result


def _extract_number(q: str) -> float | None:
    match = re.search(r'[-+]?\d*\.?\d+', q)
    return float(match.group()) if match else None


def _convert_temperature(q: str) -> str | None:
    patterns = [
        (r'(\d+\.?\d*)\s*c(elsius)?\s*(to|in)\s*f(ahrenheit)?', 'c2f'),
        (r'(\d+\.?\d*)\s*f(ahrenheit)?\s*(to|in)\s*c(elsius)?',  'f2c'),
        (r'(\d+\.?\d*)\s*c(elsius)?\s*(to|in)\s*k(elvin)?',      'c2k'),
        (r'(\d+\.?\d*)\s*k(elvin)?\s*(to|in)\s*c(elsius)?',      'k2c'),
        (r'(\d+\.?\d*)\s*f(ahrenheit)?\s*(to|in)\s*k(elvin)?',   'f2k'),
        (r'(\d+\.?\d*)\s*k(elvin)?\s*(to|in)\s*f(ahrenheit)?',   'k2f'),
    ]
    for pattern, mode in patterns:
        m = re.search(pattern, q)
        if m:
            val = float(m.group(1))
            if mode == 'c2f': res, unit = round(val * 9/5 + 32, 2), '°F'
            elif mode == 'f2c': res, unit = round((val - 32) * 5/9, 2), '°C'
            elif mode == 'c2k': res, unit = round(val + 273.15, 2), 'K'
            elif mode == 'k2c': res, unit = round(val - 273.15, 2), '°C'
            elif mode == 'f2k': res, unit = round((val - 32) * 5/9 + 273.15, 2), 'K'
            elif mode == 'k2f': res, unit = round((val - 273.15) * 9/5 + 32, 2), '°F'
            return f"🌡️ {val} → **{res}{unit}**"
    return None


def _convert_length(q: str) -> str | None:
    # All values stored in meters
    units = {
        'kilometer': 1000, 'kilometres': 1000, 'km': 1000,
        'meter': 1, 'metres': 1, 'm': 1,
        'centimeter': 0.01, 'centimetres': 0.01, 'cm': 0.01,
        'millimeter': 0.001, 'mm': 0.001,
        'mile': 1609.344, 'miles': 1609.344,
        'yard': 0.9144, 'yards': 0.9144, 'yd': 0.9144,
        'foot': 0.3048, 'feet': 0.3048, 'ft': 0.3048,
        'inch': 0.0254, 'inches': 0.0254, 'in': 0.0254,
    }
    return _generic_convert(q, units, "📏")


def _convert_weight(q: str) -> str | None:
    # All values stored in grams
    units = {
        'kilogram': 1000, 'kilograms': 1000, 'kg': 1000,
        'gram': 1, 'grams': 1, 'g': 1,
        'milligram': 0.001, 'milligrams': 0.001, 'mg': 0.001,
        'pound': 453.592, 'pounds': 453.592, 'lbs': 453.592, 'lb': 453.592,
        'ounce': 28.3495, 'ounces': 28.3495, 'oz': 28.3495,
        'ton': 1_000_000, 'tonne': 1_000_000, 'tonnes': 1_000_000,
    }
    return _generic_convert(q, units, "⚖️")


def _generic_convert(q: str, units: dict, emoji: str) -> str | None:
    pattern = r'(\d+\.?\d*)\s*(' + '|'.join(re.escape(u) for u in units) + r')\s*(?:to|in)\s*(' + '|'.join(re.escape(u) for u in units) + r')'
    m = re.search(pattern, q)
    if not m:
        return None
    val = float(m.group(1))
    from_unit = m.group(2)
    to_unit = m.group(3)
    base = val * units[from_unit]
    result = round(base / units[to_unit], 4)
    # Clean trailing zeros
    result = int(result) if result == int(result) else result
    return f"{emoji} {val} {from_unit} = **{result} {to_unit}**"


def _convert_currency_hint(q: str) -> str | None:
    """
    Currency note: live rates need scraping (Tier 2).
    This handles the intent detection and returns a prompt
    for search.py to pick up — NOT a hardcoded rate.
    Returns None so it falls through to search.py naturally.
    """
    currency_triggers = ['inr', 'usd', 'eur', 'rupee', 'dollar', 'euro', 'convert currency']
    if any(t in q for t in currency_triggers) and re.search(r'\d', q):
        # Let search.py handle with live rates — return None intentionally
        return None
    return None


# ─────────────────────────────────────────────
# DATETIME
# ─────────────────────────────────────────────

_TIME_TRIGGERS = ["time", "date", "day", "today", "what year", "what month", "current time", "current date"]

def _handle_datetime(q: str) -> str | None:
    if not any(t in q for t in _TIME_TRIGGERS):
        return None

    now = datetime.now()

    if "time" in q:
        return f"🕐 It's **{now.strftime('%I:%M %p')}**"
    if "date" in q or "today" in q:
        return f"📅 Today is **{now.strftime('%A, %d %B %Y')}**"
    if "day" in q:
        return f"📅 Today is **{now.strftime('%A')}**"
    if "year" in q:
        return f"📅 Current year: **{now.year}**"
    if "month" in q:
        return f"📅 Current month: **{now.strftime('%B %Y')}**"

    return None


# ─────────────────────────────────────────────
# CLASSIFIER PATTERNS (paste into classifier.py)
# ─────────────────────────────────────────────
#
# Add to your regex stage in classifier.py:
#
# SYSTEM_PATTERNS = [
#     r'\b(cpu|ram|memory|battery|disk|storage|pc stats|system stats|system info)\b',
#     r'\b(calculate|calc|compute|solve)\b.*\d',
#     r'\d+\s*(celsius|fahrenheit|kelvin|km|miles|kg|lbs|cm|inches|feet|meters)',
#     r'\b(what(\'s|\s+is)\s+the\s+(time|date|day|year|month))\b',
# ]
#
# In brain.py, add before _handle_search():
#
# from system import handle_system
#
# result = handle_system(user_query)
# if result:
#     return result  # skip LLM entirely
#