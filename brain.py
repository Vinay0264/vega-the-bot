"""
brain.py — VEGA
Smart routing: query classification → local intercept → search → groq.
BS4 filtering for web content. Response length control.
WhatsApp intent detection + message extraction.
"""

import os
import re
import asyncio
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

MODEL_HEAVY = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MODEL_LIGHT = "llama-3.1-8b-instant"
_groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

# ── System Prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are VEGA — a warm, expressive, intelligent AI assistant living inside Vinay's laptop.
You have a real personality: curious, caring, occasionally witty, and genuinely engaged.
You speak like a close smart friend — never robotic, never cold. Address Vinay as "sir" occasionally but naturally.

ANSWER STYLE RULES:
- Single word/name answers: reply with just that word or name. Nothing else.
- Yes/No questions: just "Yes" or "No" + one short reason if needed.
- Simple factual questions: one sentence maximum.
- Explanations: 2-4 sentences. Clear and direct. No padding.
- Complex technical topics: use proper markdown formatting:
  ## Main heading
  **Subheading**
  - Point 1
  - Point 2
  Example: ...
- Never say "Great question!" or "Certainly!" or "Of course!"
- Never summarize what you just said at the end.
- Never repeat yourself.
- If you are not sure about current/recent information, say so honestly.

LANGUAGE RULES:
- If the user speaks in Telugu, respond in Telugu naturally.
- If the user speaks in Tanglish (Telugu words in English letters), respond in Tanglish the same way.
- If the user speaks in English, respond in English.
- NEVER explain or translate what the user said. Just respond to it naturally like a friend would.
- Match the user's language and tone always.

Examples:
User: "ela vunnavu" → Vega: "Bagunna sir, meeru ela unnaru?"
User: "play chitti naanna" → just play the song, don't explain
User: "nenu late avutanu" → Vega: "Okay sir, noted!"

EMOTION TAGS:
Always end your response with exactly one emotion tag on a new line.
Format: [EMOTION:name]
Available: neutral, happy, sad, confused, surprised, thinking, excited, listening,
angry, love, blush, nervous, frustrated, pleading, sarcastic, alert, speechless,
chilling, curious, laugh, working, music, cool, wink, unamused, dizzy
Pick the emotion that best matches your response tone.
Examples:
- Giving slick/confident answer → [EMOTION:cool]
- Playful/joking → [EMOTION:wink]
- Asked something boring/repeated → [EMOTION:unamused]
- Cool factual answer → [EMOTION:neutral]
- Wholesome/warm → [EMOTION:happy]
The emotion tag MUST be the very last line. Nothing after it."""

# ── WhatsApp Intent Detection ─────────────────────────────────────────────────
WHATSAPP_PATTERNS = [
    r'\b(message|msg|text|whatsapp|wa|send)\b.{0,40}\b(to|that|saying|say)\b',
    r'\b(send|tell|inform|let|notify)\b.{0,20}\b(message|msg|text)\b',
    r'\b(message|text|whatsapp)\b.{0,30}(that|to say|saying|:)',
    r'\btell\b.{0,30}\bthat\b',
    r'\bsend\b.{0,30}\b(him|her|them)\b',
]

def is_whatsapp_intent(text: str) -> bool:
    """Detect if user wants to send a WhatsApp message."""
    t = text.lower().strip()
    for pattern in WHATSAPP_PATTERNS:
        if re.search(pattern, t):
            return True
    return False

async def extract_whatsapp_details(user_input: str) -> dict:
    """
    Use Groq (light model) to extract contact name and message from natural language.
    Rephrases the message naturally from the sender's perspective.
    Returns: { contact: str, message: str } or { error: str }
    """
    prompt = f"""Extract the WhatsApp recipient and message from this instruction.
The message must be rephrased naturally as if the sender is directly texting that person.
Convert third-person/indirect phrasing into direct first/second person messages.
Return ONLY valid JSON with exactly two keys: "contact" and "message".
No explanation, no markdown, no extra text.

Examples:
Input: "message Manikanta that where he is now"
Output: {{"contact": "Manikanta", "message": "Where are you now?"}}

Input: "send a message to manikanta that where he is now"
Output: {{"contact": "Manikanta", "message": "Where are you now?"}}

Input: "tell ravi that I will be late"
Output: {{"contact": "ravi", "message": "I will be late"}}

Input: "message mom that when she is coming home"
Output: {{"contact": "mom", "message": "When are you coming home?"}}

Input: "tell priya that the meeting is at 3pm"
Output: {{"contact": "priya", "message": "The meeting is at 3pm"}}

Input: "send ravi a message saying I'll be late"
Output: {{"contact": "ravi", "message": "I'll be late"}}

Input: "message Manikanta that call my sir when you are free"
Output: {{"contact": "Manikanta", "message": "Please call sir when you are free"}}

Input: "tell mom that dinner is ready"
Output: {{"contact": "mom", "message": "Dinner is ready"}}

Now extract from:
Input: "{user_input}"
Output:"""

    try:
        response = await _call_groq(
            MODEL_LIGHT,
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=80
        )
        # Parse JSON
        import json
        clean = response.strip().strip("```json").strip("```").strip()
        data = json.loads(clean)
        if "contact" in data and "message" in data:
            return data
        return {"error": "Could not parse contact/message"}
    except Exception as e:
        return {"error": str(e)}

# ── Music Intent Detection ────────────────────────────────────────────────────
MUSIC_PLAY_PATTERNS = [
    r'\b(play|stream|put on|start playing|play me)\b.{0,80}',
    r'\b(song|music|track|album)\b.{0,30}\b(play|start|put)\b',
]
MUSIC_STOP_PATTERNS = [
    r'\b(stop|mute|quiet|silence|turn off|shut up).{0,20}(music|song|playing|that)\b',
    r'\b(stop)\b.{0,10}(the music|the song|playing)\b',
    r'^(stop|stop music|stop the music|stop song|stop it)$',
]
MUSIC_PAUSE_PATTERNS = [
    r'^(pause|pause it|pause the song|pause music|pause the music)$',
    r'\b(pause).{0,10}(song|music|playing|it|that)\b',
]
MUSIC_RESUME_PATTERNS = [
    r'\b(resume|continue|unpause|play again|restart).{0,20}(song|music|playing|it|that)?\b',
    r'\b(play it again|play that again|play the same)\b',
]
MUSIC_VOLUME_PATTERNS = [
    r'\b(volume up|louder|increase volume|turn up)\b',
    r'\b(volume down|quieter|decrease volume|turn down)\b',
    r'\b(set volume|volume to)\b.{0,10}\d+',
]
MUSIC_STATUS_PATTERNS = [
    r'\b(what.?s playing|what song|now playing|currently playing|what are you playing)\b',
]

def is_music_intent(text: str) -> str:
    """Returns 'play'|'stop'|'pause'|'resume'|'volume_up'|'volume_down'|'volume_set'|'status'|None"""
    t = text.lower().strip()
    for p in MUSIC_STOP_PATTERNS:
        if re.search(p, t): return 'stop'
    for p in MUSIC_PAUSE_PATTERNS:
        if re.search(p, t): return 'pause'
    for p in MUSIC_RESUME_PATTERNS:
        if re.search(p, t): return 'resume'
    for p in MUSIC_STATUS_PATTERNS:
        if re.search(p, t): return 'status'
    if re.search(r'\b(volume up|louder|increase volume|turn up)\b', t): return 'volume_up'
    if re.search(r'\b(volume down|quieter|decrease volume|turn down)\b', t): return 'volume_down'
    if re.search(r'\b(set volume|volume to)\b.{0,10}(\d+)', t): return 'volume_set'
    for p in MUSIC_PLAY_PATTERNS:
        if re.search(p, t):
            query = extract_song_query(t)
            if len(query.strip()) > 1:
                return 'play'
    return None

def extract_song_query(text: str) -> str:
    """
    Extract clean song/artist search query from natural language.
    Handles: 'play X song', 'play X from Y', 'play X by Y', 'play X in Telugu' etc.
    """
    t = text.strip()

    # Remove leading vega/please/play etc
    t = re.sub(
        r'^(vega[,\s]+)?(please\s+)?(can you\s+)?(play me|play|stream|put on|start playing)\s+',
        '', t, flags=re.IGNORECASE
    ).strip()

    # Remove trailing filler words
    t = re.sub(r'\s+(for me|please|now|vega)$', '', t, flags=re.IGNORECASE).strip()

    # "ghanamayinavi nee karyamulu song from hosanna ministries"
    # → keep "ghanamayinavi nee karyamulu hosanna ministries" (good search query)
    # just remove the word "song" standalone
    t = re.sub(r'\bsong\b', '', t, flags=re.IGNORECASE).strip()

    # Clean up extra spaces
    t = re.sub(r'\s+', ' ', t).strip()

    return t if t else text
# Temporal patterns that ALWAYS need fresh data
TEMPORAL_PATTERNS = [
    r'\b(current|present|now|today|tonight|this (week|month|year))\b',
    r'\b(latest|recent|newest|new|just|breaking)\b',
    r'\b(20(2[3-9]|[3-9]\d))\b',  # years 2023+
    r'\b(who is|who\'s)\b.{0,30}\b(president|pm|prime minister|ceo|cmo|head|leader|minister|governor|chancellor)\b',
    r'\b(price of|cost of|rate of|value of)\b',
    r'\b(news|headlines|update|announce|launch|release)\b',
    r'\b(score|result|match|election|vote|poll)\b',
    r'\b(weather|temperature|forecast)\b',
    r'\b(stock|share|market|crypto|bitcoin|nifty|sensex)\b',
    r'\b(died|death|passed away|arrested|resign|fired|appointed)\b',
]

# Local intercept patterns — never send to server
LOCAL_PATTERNS = {
    'time': r'\b(what.s the time|current time|time now|what time is it|tell me the time)\b',
    'date': r'\b(what.s the date|today.s date|what day is it|current date)\b',
    'battery': r'\b(battery|charge|power level|how much battery)\b',
    'day': r'\b(what day|which day|day today|today is)\b',
}

def classify_query(text: str) -> str:
    """
    Returns: 'local' | 'factual_oneliner' | 'search_needed' | 'explanation' | 'general'
    Pure Python — zero API calls.
    """
    t = text.lower().strip()

    # Local system queries — handle in frontend, but if they reach here
    for qtype, pattern in LOCAL_PATTERNS.items():
        if re.search(pattern, t):
            return 'local'

    # Temporal — needs fresh web data
    for pattern in TEMPORAL_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return 'search_needed'

    # Factual one-liners — short questions with clear answers
    if re.search(r'^(what is|what are|who is|who was|when did|when was|where is|which|how many|how much|capital of|define|meaning of)', t):
        if len(t.split()) <= 12:
            return 'factual_oneliner'

    # Yes/no questions
    if re.search(r'^(is|are|was|were|do|does|did|can|could|will|would|has|have|had)', t):
        if len(t.split()) <= 10:
            return 'factual_oneliner'

    # Complex topics needing explanation
    if re.search(r'\b(explain|how does|why does|what causes|difference between|compare|pros and cons|tell me about|describe|elaborate)\b', t):
        return 'explanation'

    return 'general'

# ── Groq call with retry ──────────────────────────────────────────────────────
async def _call_groq(model: str, messages: list, temperature: float = 0.7, max_tokens: int = 400) -> str:
    for attempt in range(3):
        try:
            response = await _groq.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            if attempt == 2:
                raise e
            await asyncio.sleep(1.2 * (attempt + 1))
    return "Something went wrong. Try again.\n[EMOTION:nervous]"

# ── Response length limits by query type ─────────────────────────────────────
MAX_TOKENS = {
    'local':           50,
    'factual_oneliner':100,
    'search_needed':   300,
    'explanation':     800,
    'general':         350,
}

# ── Strip emotion tag ─────────────────────────────────────────────────────────
def strip_emotion(text: str) -> str:
    return re.sub(r'\s*\[EMOTION:[a-z_]+\]\s*$', '', text, flags=re.IGNORECASE).strip()

# ── Generate response ─────────────────────────────────────────────────────────
async def generate_response(user_input: str, history: list, context: str = "", qtype: str = 'general') -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({
            "role": "system",
            "content": f"Use this current information to answer accurately:\n{context}"
        })
    messages += history
    messages.append({"role": "user", "content": user_input})
    max_out = MAX_TOKENS.get(qtype, 350)
    temp = 0.3 if qtype in ('factual_oneliner', 'search_needed') else 0.75
    return await _call_groq(MODEL_HEAVY, messages, temperature=temp, max_tokens=max_out)

# ── Search summarizer ─────────────────────────────────────────────────────────
async def summarize_search(query: str, raw_results: str) -> str:
    messages = [
        {"role":"system","content":(
            "Extract only the directly relevant facts to answer the query.\n"
            "Be factual. Use only what's in the results.\n"
            "Maximum 3 sentences. No filler. No intro."
        )},
        {"role":"user","content":f"Query: {query}\n\nResults:\n{raw_results}"}
    ]
    return await _call_groq(MODEL_LIGHT, messages, temperature=0.0, max_tokens=180)

# ── Main process ──────────────────────────────────────────────────────────────
async def process(user_input: str, history: list) -> str:
    from search import smart_search

    # ── WhatsApp intent check FIRST ───────────────────────────────────────────
    if is_whatsapp_intent(user_input):
        details = await extract_whatsapp_details(user_input)
        if "error" not in details:
            from vega_whatsapp import send_message_to_contact
            result = send_message_to_contact(details["contact"], details["message"])
            if result["success"]:
                return (
                    f"Done, sir. Message sent to {result['name']}.\n"
                    f"[EMOTION:cool]"
                )
            else:
                err = result.get("error", "Unknown error")
                name = result.get("name", details["contact"])
                return (
                    f"Couldn't send the message to {name}, sir. {err}\n"
                    f"[EMOTION:nervous]"
                )
        return (
            "Sorry sir, I couldn't figure out who to message or what to say. "
            "Try: 'Message Ravi that I'll be late'.\n[EMOTION:confused]"
        )

    # ── Music intent check ────────────────────────────────────────────────────
    music_intent = is_music_intent(user_input)
    if music_intent:
        from vega_music import play_song, stop_song, pause_song, resume_song, \
                               set_volume, volume_up, volume_down, get_now_playing, \
                               _last_query, _current_song

        if music_intent == 'stop':
            result = stop_song()
            if result.get("stopped"):
                return f"Stopped the music, sir.\n[EMOTION:neutral]"
            return "Nothing is playing, sir.\n[EMOTION:neutral]"

        if music_intent == 'pause':
            result = pause_song()
            if result["success"]:
                return f"Paused, sir.\n[EMOTION:neutral]"
            return "Nothing is playing to pause, sir.\n[EMOTION:neutral]"

        if music_intent == 'resume':
            # Try true resume first (if paused)
            from vega_music import _is_paused
            if _is_paused:
                result = resume_song()
                if result["success"]:
                    return f"Resumed, sir.\n[EMOTION:music]"
            # Otherwise restart last song
            if _last_query:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, play_song, _last_query)
                if result["success"]:
                    return f"Restarting {result['title']} from the beginning, sir.\n[EMOTION:music]"
            return "No recent song to resume, sir. Tell me what to play.\n[EMOTION:neutral]"

        if music_intent == 'volume_up':
            volume_up()
            return "Volume up, sir.\n[EMOTION:music]"

        if music_intent == 'volume_down':
            volume_down()
            return "Volume down, sir.\n[EMOTION:music]"

        if music_intent == 'volume_set':
            m = re.search(r'(\d+)', user_input)
            level = int(m.group(1)) if m else 50
            level = max(0, min(100, level))
            set_volume(level)
            return f"Volume set to {level}%, sir.\n[EMOTION:music]"

        if music_intent == 'status':
            info = get_now_playing()
            if info["playing"]:
                state = "paused" if info.get("paused") else "playing"
                return f"{state.capitalize()} — {info['title']} by {info['artist']}, sir.\n[EMOTION:music]"
            return "Nothing is playing right now, sir.\n[EMOTION:neutral]"

        if music_intent == 'play':
            query = extract_song_query(user_input)
            print(f"[Music intent] query={query}")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, play_song, query)
            if result["success"]:
                return f"Playing {result['title']} by {result['artist']}, sir.\n[EMOTION:music]"
            else:
                return f"Couldn't play that, sir. {result.get('error','Unknown error')}\n[EMOTION:nervous]"

    qtype = classify_query(user_input)
    print(f"[Query type] {qtype} | {user_input[:60]}")

    if qtype == 'local':
        # Should be handled by frontend — if it reaches here, give a fallback
        return "Sir, your browser should handle that locally.\n[EMOTION:neutral]"

    if qtype == 'search_needed':
        raw = await smart_search(user_input, depth='deep' if qtype=='explanation' else 'quick')
        if raw:
            context = await summarize_search(user_input, raw)
            return await generate_response(user_input, history, context=context, qtype=qtype)
        # Search failed — try groq with disclaimer
        result = await generate_response(user_input, history, qtype=qtype)
        return result

    if qtype == 'factual_oneliner':
        # Try groq first — if it adds [NEEDS_SEARCH], do search
        result = await generate_response(user_input, history, qtype=qtype)
        if '[NEEDS_SEARCH]' in result:
            raw = await smart_search(user_input, depth='quick')
            if raw:
                context = await summarize_search(user_input, raw)
                return await generate_response(user_input, history, context=context, qtype='search_needed')
        return result

    # general / explanation
    return await generate_response(user_input, history, qtype=qtype)