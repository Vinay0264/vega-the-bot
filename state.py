"""
state.py — VEGA
Shared session state. Any module reads/writes here directly.
No passing data through layers.
"""

_state = {
    # ── Existing ──────────────────────────────
    "city":          "",    # from browser geolocation
    "user_name":     "",    # from .env
    "now_playing":   {},    # current song info
    "last_query":    "",    # last music search query
    "reminders":     [],    # active reminders
    "language":      "en",  # current UI language

    # ── New ───────────────────────────────────
    "last_result":   "",    # last response of any kind (math, stats, search)
    "last_search":   "",    # last search query — for follow-up context
}

def set(key: str, value):
    _state[key] = value

def get(key: str, default=None):
    return _state.get(key, default)