"""
classifier.py — VEGA
═══════════════════════════════════════════════════════════════════
TWO-STAGE DESIGN:

  Stage 1 — Regex gate (zero tokens, instant)
    Only structurally unambiguous commands: music controls, pc
    stats, unit conversions, pure math, local time/date.
    If there is ANY doubt about intent → skip, let LLM handle it.

  Stage 2 — Single LLM call (Groq 8B)
    Classifies AND extracts in one shot.
    Returns pipe-separated: intent|data1|data2
    Handles music_play, whatsapp, weather, search, emotional, general.
    No separate extraction call. Ever.

RETURN FORMAT (same as before — brain.py unchanged):
  { "intent": str, "extracted": dict }

INTENTS:
  music_play     → { query: str }
  music_control  → { action: str, level?: int, amount?: str }
  whatsapp       → { contact: str, message: str }
  weather        → { city: str }
  search         → { query: str }
  emotional      → {}
  general        → {}
  local          → {}   (frontend handles — time/date/battery)
  system         → {}   (system.py handles — math/stats/conversion)
═══════════════════════════════════════════════════════════════════
"""

import os
import re
import asyncio
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

_groq       = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_LIGHT = "llama-3.1-8b-instant"


# ══════════════════════════════════════════════════════════════════════════════
#  STAGE 1 — REGEX GATE
#  Rule: only match things that are STRUCTURALLY unambiguous.
#  "pause" always means pause. "cpu usage" always means system.
#  "play kesariya" is NOT structurally certain → goes to LLM.
# ══════════════════════════════════════════════════════════════════════════════

# ── Local: time/date handled by frontend JS ───────────────────────────────────
_LOCAL = re.compile(
    r"^\s*(what'?s\s+the\s+(time|date)|current\s+(time|date)|time\s+now"
    r"|what\s+time\s+is\s+it|today'?s\s+date|what\s+day\s+is\s+it"
    r"|which\s+day|day\s+today)\s*\??$",
    re.IGNORECASE
)

# ── System: pc stats, math, unit conversion ───────────────────────────────────
# Unit words only match when paired with a number AND a conversion target.
# Prevents "how many km is mars from earth" from hitting system.
_SYSTEM = re.compile(
    r"\b(cpu|ram|memory usage|battery|disk\s+space|storage|pc\s+stats"
    r"|system\s+stats|system\s+info|processor\s+usage)\b"
    r"|\b(calculate|calc|compute|solve|multiply|multiplied|divide|divided"
    r"|times|squared|cubed|percent\s+of|modulo)\b"
    r"|\d+\.?\d*\s*(celsius|fahrenheit|kelvin)\b"
    r"|\d+\.?\d*\s*(km|miles|kg|lbs|cm|inches|feet|meters|grams|pounds)"
    r"\b.{0,15}\b(to|in|into)\b"
    r"|^\s*[\d\s+\-*/().^%]+\s*$",   # pure math: "34 * 56" or "457 % 98"
    re.IGNORECASE
)

# ── Music controls: structurally unambiguous one-word/short commands ──────────
# These are always music commands regardless of context. No false positives.
_MUSIC_STOP = re.compile(
    r"^\s*(stop|mute|silence)\s*(the\s+)?(music|song|audio|it|that)?\s*$",
    re.IGNORECASE
)
_MUSIC_PAUSE = re.compile(
    r"^\s*pause(\s+the\s+(music|song))?\s*$",
    re.IGNORECASE
)
_MUSIC_RESUME = re.compile(
    r"^\s*(resume|unpause|continue\s+playing|play\s+again"
    r"|play\s+it\s+again|play\s+that\s+again)\s*(the\s+)?(song|music)?\s*$",
    re.IGNORECASE
)
_MUSIC_VOL_SET = re.compile(
    r"\b(set\s+(the\s+)?volume|volume\s+to)\b.{0,10}(\d+)",
    re.IGNORECASE
)
_MUSIC_VOL_UP = re.compile(
    r"\b(volume\s+up|louder|increase\s+(the\s+)?volume"
    r"|turn\s+(it\s+)?up|raise\s+(the\s+)?volume)\b",
    re.IGNORECASE
)
_MUSIC_VOL_DOWN = re.compile(
    r"\b(volume\s+down|quieter|decrease\s+(the\s+)?volume"
    r"|turn\s+(it\s+)?down|lower\s+(the\s+)?volume"
    r"|reduce\s+(the\s+)?volume)\b",
    re.IGNORECASE
)
_MUSIC_STATUS = re.compile(
    r"\b(what'?s\s+playing|what\s+song|now\s+playing"
    r"|currently\s+playing|what\s+are\s+you\s+playing)\b",
    re.IGNORECASE
)

# ── Small volume step indicator ───────────────────────────────────────────────
_SMALL_AMOUNT = re.compile(
    r"\b(a\s+little|little\s+bit|slightly|just\s+a\s+bit|a\s+bit|small)\b",
    re.IGNORECASE
)


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS — used by Stage 1 only
# ══════════════════════════════════════════════════════════════════════════════

def _extract_volume_level(text: str) -> int:
    m = re.search(r"(\d+)", text)
    return max(0, min(100, int(m.group(1)))) if m else 50

def _get_volume_amount(text: str) -> str:
    return "small" if _SMALL_AMOUNT.search(text) else "normal"


# ══════════════════════════════════════════════════════════════════════════════
#  STAGE 2 — SINGLE LLM CALL
#  Classifies intent AND extracts data in one shot.
#  Returns pipe-separated string: intent|field1|field2
#
#  Why pipe-separated and not JSON?
#  - JSON risks: model adds markdown fences, extra keys, wrong quotes.
#  - Pipe format: model has been shown to be rock-solid with this.
#  - Parsing is one split("|") — no try/except needed for format errors.
# ══════════════════════════════════════════════════════════════════════════════

_CLASSIFIER_PROMPT = """\
You are a classifier for a personal AI assistant called VEGA.
Classify the user's input and extract relevant data.

Reply with EXACTLY ONE LINE in this format: intent|data
No explanation. No punctuation at end. Nothing else.

INTENTS AND FORMAT:
music_play|<song or artist query>
  → user wants to play music. Extract only the song/artist name.
  → Examples: "play kesariya" → music_play|kesariya
  →           "play some arijit singh" → music_play|arijit singh
  → NOT music_play — these are general conversation:
     "play chess", "play it cool", "play a role" → general|
     "my wish" (said in conversation, not a command) → general|
     "nice", "ok", "yes", "yeah", "sure" → general|
     Short replies with no clear music intent → general|

whatsapp|<contact name>|<message text>
  → user wants to send a WhatsApp message. Rephrase message as direct text.
  → "tell ravi I'll be late" → whatsapp|Ravi|I'll be late
  → "message mom that dinner is ready" → whatsapp|Mom|Dinner is ready
  → CRITICAL — NOT whatsapp (these are general conversation):
     "i will tell vinay" → general|
     "i will tell him" → general|
     "tell me that story" → general|
     "send me that link" → general|
     "i'll let them know" → general|
     Only whatsapp if user is COMMANDING VEGA to send a message TO someone else.

weather|<city name>
  → user wants weather. Extract city if mentioned, else leave blank.
  → "weather in hyderabad" → weather|Hyderabad
  → "what's the weather" → weather|

stock|<company name or symbol>
  → user wants a live stock price. Extract only the company name or symbol.
  → "tata steel stock price" → stock|tata steel
  → "what is reliance share price" → stock|reliance
  → "nifty 50 today" → stock|nifty 50
  → "INFY stock" → stock|INFY

search|<clean search query>
  → needs live/current info: news, scores, exam results, sports, events.
  → NOT for stock prices — use stock intent for those.
  → "who won ipl yesterday" → search|IPL match result yesterday
  → "gold price today" → search|gold price today India

emotional|
  → user is venting, expressing feelings, personal struggle, loneliness.
  → "I'm really stressed about my exams" → emotional|

general|
  → everything else: explanations, coding help, definitions, advice.
  → "what is recursion" → general|
  → "help me write an email" → general|

Recent conversation (last 2 turns for context — use this to resolve ambiguous follow-ups):
{context}

Input: {input}"""


async def _llm_classify(text: str, history: list = None) -> dict:
    """
    Single Groq 8B call. Classifies + extracts in one shot.
    Returns parsed classification dict.
    Falls back to general on any failure.
    history: last N messages from conversation, used to resolve follow-ups.
    """
    # Build context snippet from last 2 turns (4 messages max)
    context = ""
    if history:
        recent = history[-4:]
        lines = []
        for msg in recent:
            role = "User" if msg["role"] == "user" else "VEGA"
            # Strip emotion tags from VEGA responses for cleanliness
            text_clean = msg["content"]
            import re as _re
            text_clean = _re.sub(r'\[EMOTION:[a-z_]+\]', '', text_clean).strip()
            # Keep it short — first 80 chars only
            lines.append(f"{role}: {text_clean[:80]}")
        context = "\n".join(lines)
    else:
        context = "None"

    prompt = _CLASSIFIER_PROMPT.format(input=text, context=context)

    try:
        response = await _groq.chat.completions.create(
            model=MODEL_LIGHT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=60,
        )
        raw = response.choices[0].message.content.strip()
        print(f"[Classifier] LLM raw → {raw!r}")
        return _parse_llm_response(raw, text)

    except Exception as e:
        print(f"[Classifier] LLM error: {e}")
        return {"intent": "general", "extracted": {}}


def _parse_llm_response(raw: str, original_input: str) -> dict:
    """
    Parse pipe-separated LLM response into classification dict.
    Handles malformed output gracefully — always returns something valid.
    """
    # Take only first line — model sometimes adds explanation after
    line = raw.splitlines()[0].strip().lower()

    # Split on pipe
    parts = [p.strip() for p in line.split("|")]
    intent = parts[0] if parts else "general"

    # ── music_play ────────────────────────────────────────────────────────────
    if intent == "music_play":
        query = parts[1] if len(parts) > 1 else ""
        if not query:
            return {"intent": "general", "extracted": {}}
        print(f"[Classifier] LLM → music_play | query={query}")
        return {"intent": "music_play", "extracted": {"query": query}}

    # ── whatsapp ──────────────────────────────────────────────────────────────
    if intent == "whatsapp":
        contact = parts[1] if len(parts) > 1 else ""
        message = parts[2] if len(parts) > 2 else ""
        if not contact or not message:
            print("[Classifier] LLM → whatsapp parse incomplete, falling to general")
            return {"intent": "general", "extracted": {}}
        print(f"[Classifier] LLM → whatsapp | contact={contact}")
        return {"intent": "whatsapp", "extracted": {"contact": contact, "message": message}}

    # ── weather ───────────────────────────────────────────────────────────────
    if intent == "weather":
        city = parts[1] if len(parts) > 1 else ""
        print(f"[Classifier] LLM → weather | city={city or 'auto'}")
        return {"intent": "search", "extracted": {"sub_intent": "weather", "city": city}}

    # ── search ────────────────────────────────────────────────────────────────
    if intent == "stock":
        query = parts[1] if len(parts) > 1 else ""
        if not query:
            return {"intent": "general", "extracted": {}}
        print(f"[Classifier] LLM → stock | {query}")
        return {"intent": "stock", "extracted": {"query": query}}

    if intent == "search":
        query = parts[1] if len(parts) > 1 else original_input
        depth = parts[2] if len(parts) > 2 and parts[2] in ("quick","deep") else None
        print(f"[Classifier] LLM → search | depth={depth or 'auto'} | {query[:50]}")
        return {"intent": "search", "extracted": {"query": query, "depth": depth}}

    # ── emotional ─────────────────────────────────────────────────────────────
    if intent == "emotional":
        print("[Classifier] LLM → emotional")
        return {"intent": "emotional", "extracted": {}}

    # ── general + fallback for anything unexpected ────────────────────────────
    print(f"[Classifier] LLM → general (intent={intent!r})")
    return {"intent": "general", "extracted": {}}


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT — called by brain.py
# ══════════════════════════════════════════════════════════════════════════════

async def classify(user_input: str, history: list = None) -> dict:
    """
    Classify user input. Returns { "intent": str, "extracted": dict }.
    Stage 1: regex for structurally certain commands (zero tokens).
    Stage 2: single LLM call for everything else.
    history: conversation so far — passed to LLM to resolve ambiguous follow-ups.
    """
    t = user_input.strip()

    # ── LOCAL — frontend handles time/date ────────────────────────────────────
    if _LOCAL.search(t):
        print("[Classifier] local")
        return {"intent": "local", "extracted": {}}

    # ── SYSTEM — system.py handles math/stats/conversion ─────────────────────
    if _SYSTEM.search(t):
        print("[Classifier] system")
        return {"intent": "system", "extracted": {}}

    # ── MUSIC CONTROLS — structurally unambiguous, always these commands ──────
    if _MUSIC_STOP.search(t):
        print("[Classifier] music_control | stop")
        return {"intent": "music_control", "extracted": {"action": "stop"}}

    if _MUSIC_PAUSE.search(t):
        print("[Classifier] music_control | pause")
        return {"intent": "music_control", "extracted": {"action": "pause"}}

    if _MUSIC_RESUME.search(t):
        print("[Classifier] music_control | resume")
        return {"intent": "music_control", "extracted": {"action": "resume"}}

    if _MUSIC_VOL_SET.search(t):
        level = _extract_volume_level(t)
        print(f"[Classifier] music_control | volume_set={level}")
        return {"intent": "music_control", "extracted": {"action": "volume_set", "level": level}}

    if _MUSIC_VOL_UP.search(t):
        amount = _get_volume_amount(t)
        print(f"[Classifier] music_control | volume_up amount={amount}")
        return {"intent": "music_control", "extracted": {"action": "volume_up", "amount": amount}}

    if _MUSIC_VOL_DOWN.search(t):
        amount = _get_volume_amount(t)
        print(f"[Classifier] music_control | volume_down amount={amount}")
        return {"intent": "music_control", "extracted": {"action": "volume_down", "amount": amount}}

    if _MUSIC_STATUS.search(t):
        print("[Classifier] music_control | status")
        return {"intent": "music_control", "extracted": {"action": "status"}}

    # ── SHORT INPUT FAST-PATH — skip LLM for obvious conversational replies ──
    # Single words or very short inputs with no intent keywords → always general.
    # Saves one 8B API call. "yes", "yeah", "ok", "nice", "nooo", "lol" etc.
    _INTENT_HINT = re.compile(
        r"(play|stop|pause|resume|volume|weather|stock|search|message|tell|send"
        r"|remind|open|close|calculate|what|who|when|where|why|how|is|are|was"
        r"|will|can|could|should|would|does|did|has|have)",
        re.IGNORECASE
    )
    if len(t.split()) <= 3 and not _INTENT_HINT.search(t):
        print(f"[Classifier] short reply → general")
        return {"intent": "general", "extracted": {}}

    # ── EVERYTHING ELSE → single LLM call ─────────────────────────────────────
    # music_play, whatsapp, weather, search, emotional, general
    # One call. Classifies + extracts simultaneously.
    return await _llm_classify(t, history=history)


# ══════════════════════════════════════════════════════════════════════════════
#  STANDALONE TEST
#  python classifier.py
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import asyncio

    TEST_CASES = [
        # Music controls — regex gate
        "pause",
        "stop the music",
        "resume",
        "volume up",
        "set volume to 70",
        "what's playing",
        # System — regex gate
        "cpu usage",
        "battery level",
        "100 celsius to fahrenheit",
        "70 kg to lbs",
        "34 * 56",
        # Local — regex gate
        "what's the time",
        "what day is it",
        # LLM stage — music play
        "play kesariya",
        "play some arijit singh songs",
        "play me a sad song",
        # LLM stage — music play false positives (should be general)
        "play chess with me",
        "play it cool",
        "play a role in this story",
        # LLM stage — whatsapp
        "tell ravi that I'll be late",
        "message mom dinner is ready",
        "send priya a message saying happy birthday",
        # Old false positives (should NOT be whatsapp)
        "send me that link",
        "tell me that story",
        # LLM stage — weather
        "weather in hyderabad",
        "what's the weather today",
        # LLM stage — search
        "who won ipl 2025",
        "gold price today",
        "eamcet results 2025",
        "latest news today",
        # LLM stage — informational (no search needed)
        "what is recursion",
        "explain machine learning",
        "how many km is mars from earth",
        # LLM stage — emotional
        "I'm really stressed about my exams",
        "feeling very lonely today",
    ]

    async def _test():
        print("VEGA Classifier — full test\n" + "=" * 50)
        for inp in TEST_CASES:
            result = await classify(inp)
            intent = result["intent"]
            extracted = result["extracted"]
            extras = " | ".join(f"{k}={v}" for k, v in extracted.items()) if extracted else ""
            print(f"  {intent:<16} {extras:<40}  ← {inp}")
        print("=" * 50)

    asyncio.run(_test())