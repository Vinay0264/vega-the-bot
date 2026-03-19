"""
classifier.py — VEGA
═══════════════════════════════════════════════════════════════════
Every user input passes through here first.
Returns a clean classification dict that brain.py acts on.

TWO-STAGE DESIGN:
  Stage 1 — Regex (zero tokens, instant)
    Catches everything with clear, unambiguous keywords.
    Music, WhatsApp, local system queries — never touch the API.

  Stage 2 — LLM (Groq 8B, ~30 tokens, only when regex can't decide)
    Only genuinely ambiguous inputs reach here.
    Returns one word: search / general / emotional
    No JSON. No examples. Minimum possible tokens.

RETURN FORMAT:
  {
    "intent":    str,   # music_play | music_control | whatsapp |
                        # local | search | emotional | general
    "extracted": dict   # relevant data pulled from input
  }

EXTENDING LATER:
  Adding a new clear intent (e.g. system commands)?
    → Add a regex block in Stage 1 with its keywords.
  Adding a new ambiguous intent?
    → Add it to the LLM stage options in _llm_classify().
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
#  STAGE 1 — REGEX PATTERNS
#  Only patterns we are 100% certain about.
#  If there is any doubt, leave it for the LLM.
# ══════════════════════════════════════════════════════════════════════════════

# ── Music: play ───────────────────────────────────────────────────────────────
_MUSIC_PLAY = re.compile(
    r'^\s*(hey\s+vega[,\s]+)?(please\s+)?(can you\s+)?'
    r'(play|stream|put on|start playing|play me)\s+.+',
    re.IGNORECASE
)

# ── Music: stop ───────────────────────────────────────────────────────────────
_MUSIC_STOP = re.compile(
    r'^\s*(stop|mute|silence|turn off)\s*(the\s+)?(music|song|playing|it|that)?\s*$',
    re.IGNORECASE
)

# ── Music: pause ──────────────────────────────────────────────────────────────
_MUSIC_PAUSE = re.compile(
    r'^\s*(pause|pause it|pause the song|pause music|pause the music)\s*$',
    re.IGNORECASE
)

# ── Music: resume ─────────────────────────────────────────────────────────────
_MUSIC_RESUME = re.compile(
    r'^\s*(resume|unpause|continue|play again|play it again|play that again)\s*(the\s+)?(song|music|playing|it|that)?\s*$',
    re.IGNORECASE
)

# ── Music: volume up ──────────────────────────────────────────────────────────
_MUSIC_VOL_UP = re.compile(
    r'\b(volume up|louder|increase (the )?volume|turn (it )?up|raise (the )?volume)\b',
    re.IGNORECASE
)

# ── Music: volume down ────────────────────────────────────────────────────────
_MUSIC_VOL_DOWN = re.compile(
    r'\b(volume down|quieter|decrease (the )?volume|turn (it )?down|lower (the )?volume|reduce (the )?volume)\b',
    re.IGNORECASE
)

# ── Music: volume set ─────────────────────────────────────────────────────────
_MUSIC_VOL_SET = re.compile(
    r'\b(set (the )?volume|volume to)\b.{0,10}(\d+)',
    re.IGNORECASE
)

# ── Music: status ─────────────────────────────────────────────────────────────
_MUSIC_STATUS = re.compile(
    r"\b(what'?s playing|what song|now playing|currently playing|what are you playing)\b",
    re.IGNORECASE
)

# ── Music: stop-then-play (e.g. "stop this and play kesariya") ────────────────
_MUSIC_STOP_PLAY = re.compile(
    r'\b(stop|stop the music|stop the song)\b.{0,30}\b(play|stream|put on)\b',
    re.IGNORECASE
)

# ── WhatsApp ──────────────────────────────────────────────────────────────────
_WHATSAPP = re.compile(
    r'\b(message|msg|text|whatsapp)\b\s+\w+\s+\w'  # "message ravi hi" — no connector needed
    r'|\b(send|tell|inform|notify)\b.{0,40}\b(to|that|saying|say|:)\b'  # "tell ravi that..."
    r'|\b(message|msg|text|whatsapp|send)\b.{0,40}\b(to|that|saying|say|:)\b',  # original
    re.IGNORECASE
)

# ── Local system queries (handled by frontend) ────────────────────────────────
_LOCAL = re.compile(
    r"\b(what'?s the time|current time|time now|what time is it"
    r"|what'?s the date|today'?s date|what day is it|current date"
    r"|battery|charge level|power level"
    r"|what day|which day|day today)\b",
    re.IGNORECASE
)

# ── Small amount indicator ────────────────────────────────────────────────────
_SMALL_AMOUNT = re.compile(
    r'\b(a little|little bit|slightly|just a bit|a bit|small)\b',
    re.IGNORECASE
)

# ══════════════════════════════════════════════════════════════════════════════
#  EXTRACTION HELPERS
#  Run AFTER intent is confirmed — extract the relevant data cleanly.
# ══════════════════════════════════════════════════════════════════════════════

def _extract_song_query(text: str) -> str:
    """Strip command words, return clean search query for yt-dlp."""
    t = text.strip()
    # Remove "stop X and play" prefix first
    t = re.sub(
        r'^\s*(stop\s+(the\s+)?(song|music)?\s*(and\s+)?)',
        '', t, flags=re.IGNORECASE
    ).strip()
    # Remove "hey vega / please / can you / play me" etc.
    t = re.sub(
        r'^\s*(hey\s+vega[,\s]+)?(please\s+)?(can you\s+)?(play me|play|stream|put on|start playing)\s+',
        '', t, flags=re.IGNORECASE
    ).strip()
    # Remove trailing filler words
    t = re.sub(r'\s+(for me|please|now|vega)\s*$', '', t, flags=re.IGNORECASE).strip()
    # Remove standalone "song" word (keep "song" if part of a title)
    t = re.sub(r'\bsong\b', '', t, flags=re.IGNORECASE).strip()
    # Clean up extra spaces
    t = re.sub(r'\s+', ' ', t).strip()
    return t if t else text


def _extract_volume_level(text: str) -> int:
    """Extract numeric volume level. Returns 50 if no number found."""
    m = re.search(r'(\d+)', text)
    if m:
        return max(0, min(100, int(m.group(1))))
    return 50


def _get_volume_amount(text: str) -> str:
    """Returns 'small' for gentle adjustments, 'normal' otherwise."""
    return "small" if _SMALL_AMOUNT.search(text) else "normal"


async def _extract_whatsapp(text: str) -> dict:
    """
    Use Groq 8B to extract contact + message from natural language.
    Uses simple key:value format — no JSON, no markdown risk.
    Minimum prompt, minimum tokens.
    """
    prompt = (
        "Extract the WhatsApp recipient and message from this instruction.\n"
        "Rephrase the message as a direct text from the sender's perspective.\n"
        "Reply in this exact format, nothing else:\n"
        "contact: <name>\n"
        "message: <message>\n\n"
        f"Instruction: {text}"
    )
    try:
        response = await _groq.chat.completions.create(
            model=MODEL_LIGHT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=60, #120
        )
        raw = response.choices[0].message.content.strip()

        # Parse simple key:value — immune to JSON/markdown issues
        contact = ""
        message = ""
        for line in raw.splitlines():
            line = line.strip()
            if line.lower().startswith("contact:"):
                contact = line.split(":", 1)[1].strip()
            elif line.lower().startswith("message:"):
                message = line.split(":", 1)[1].strip()

        if contact and message:
            return {"contact": contact, "message": message}
        return {"error": "Could not parse contact or message"}

    except Exception as e:
        return {"error": str(e)}

# ══════════════════════════════════════════════════════════════════════════════
#  STAGE 2 — LLM CLASSIFIER
#  Only called when regex found nothing certain.
#  Sends ~30 tokens. Returns one word. Nothing else.
# ══════════════════════════════════════════════════════════════════════════════

async def _llm_classify(text: str) -> str:
    """
    Classify ambiguous input into: search | general | emotional
    One word reply. Max 5 tokens back.
    """
    prompt = (
            "Extract the WhatsApp recipient and message from this instruction.\n"
            "Rephrase the message as a direct text from the sender's perspective.\n"
            "Fix spelling, grammar, and capitalisation of the message.\n"
            "Do NOT add the contact's name inside the message.\n"
            "Do NOT add greetings or extra words not implied by the instruction.\n"
            "Reply in this exact format, nothing else:\n"
            "contact: <name>\n"
            "message: <message>\n\n"
            f"Instruction: {text}"
        )
    try:
        response = await _groq.chat.completions.create(
            model=MODEL_LIGHT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=5,
        )
        result = response.choices[0].message.content.strip().lower()
        # Validate — unexpected response falls back to general
        if result in ("search", "emotional", "general"):
            return result
        return "general"

    except Exception as e:
        print(f"[Classifier LLM error] {e}")
        return "general"

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT — called by brain.py
# ══════════════════════════════════════════════════════════════════════════════

async def classify(user_input: str) -> dict:
    """
    Classify user input and extract relevant data.

    Returns:
      { "intent": str, "extracted": dict }

    Intents:
      music_play     → extracted: { query: str }
      music_control  → extracted: { action: str, amount?: str, level?: int }
      whatsapp       → extracted: { contact: str, message: str }
      local          → extracted: {}
      search         → extracted: { query: str }
      emotional      → extracted: {}
      general        → extracted: {}
    """
    t = user_input.strip()

    # ── LOCAL ─────────────────────────────────────────────────────────────────
    if _LOCAL.search(t):
        print("[Classifier] local")
        return {"intent": "local", "extracted": {}}

    # ── MUSIC: stop-then-play (must check before stop) ────────────────────────
    if _MUSIC_STOP_PLAY.search(t):
        query = _extract_song_query(t)
        print(f"[Classifier] music_play | query={query}")
        return {"intent": "music_play", "extracted": {"query": query}}

    # ── MUSIC: stop ───────────────────────────────────────────────────────────
    if _MUSIC_STOP.search(t):
        print("[Classifier] music_control | action=stop")
        return {"intent": "music_control", "extracted": {"action": "stop"}}

    # ── MUSIC: pause ──────────────────────────────────────────────────────────
    if _MUSIC_PAUSE.search(t):
        print("[Classifier] music_control | action=pause")
        return {"intent": "music_control", "extracted": {"action": "pause"}}

    # ── MUSIC: resume ─────────────────────────────────────────────────────────
    if _MUSIC_RESUME.search(t):
        print("[Classifier] music_control | action=resume")
        return {"intent": "music_control", "extracted": {"action": "resume"}}

    # ── MUSIC: volume set (check before up/down to avoid conflict) ────────────
    if _MUSIC_VOL_SET.search(t):
        level = _extract_volume_level(t)
        print(f"[Classifier] music_control | action=volume_set | level={level}")
        return {"intent": "music_control", "extracted": {"action": "volume_set", "level": level}}

    # ── MUSIC: volume up ──────────────────────────────────────────────────────
    if _MUSIC_VOL_UP.search(t):
        amount = _get_volume_amount(t)
        print(f"[Classifier] music_control | action=volume_up | amount={amount}")
        return {"intent": "music_control", "extracted": {"action": "volume_up", "amount": amount}}

    # ── MUSIC: volume down ────────────────────────────────────────────────────
    if _MUSIC_VOL_DOWN.search(t):
        amount = _get_volume_amount(t)
        print(f"[Classifier] music_control | action=volume_down | amount={amount}")
        return {"intent": "music_control", "extracted": {"action": "volume_down", "amount": amount}}

    # ── MUSIC: status ─────────────────────────────────────────────────────────
    if _MUSIC_STATUS.search(t):
        print("[Classifier] music_control | action=status")
        return {"intent": "music_control", "extracted": {"action": "status"}}

    # ── MUSIC: play ───────────────────────────────────────────────────────────
    if _MUSIC_PLAY.search(t):
        query = _extract_song_query(t)
        if re.search(r'\b(and then|after that|then play|followed by)\b', t, re.IGNORECASE):
            return {"intent": "general", "extracted": {}}
        if query:
            print(f"[Classifier] music_play | query={query}")
            return {"intent": "music_play", "extracted": {"query": query}}

    # ── WHATSAPP ──────────────────────────────────────────────────────────────
    if _WHATSAPP.search(t):
        extracted = await _extract_whatsapp(t)
        if "error" not in extracted:
            print(f"[Classifier] whatsapp | contact={extracted.get('contact')}")
            return {"intent": "whatsapp", "extracted": extracted}
        print(f"[Classifier] whatsapp extraction failed: {extracted.get('error')}")
        # Fall through to LLM stage

    # ── LLM STAGE — search / general / emotional ──────────────────────────────
    intent = await _llm_classify(t)
    print(f"[Classifier] LLM → {intent}")

    if intent == "search":
        return {"intent": "search", "extracted": {"query": t}}
    if intent == "emotional":
        return {"intent": "emotional", "extracted": {}}
    return {"intent": "general", "extracted": {}}