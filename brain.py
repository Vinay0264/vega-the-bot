"""
brain.py — VEGA
═══════════════════════════════════════════════════════════════════
The controller. Coordinates everything.

FLOW:
  user input
      ↓
  classifier.py  — regex first, LLM only if unclear
      ↓
  correct handler — music / whatsapp / search / general / emotional
      ↓
  generate_response() — Groq 70B builds the reply
      ↓
  guaranteed emotion tag on every response
      ↓
  server.py sends it to vega.html

EXTENDING LATER:
  1. Add new regex block in classifier.py  (clear intents)
     OR add new option in _llm_classify()  (ambiguous intents)
  2. Create actions/new_module.py
  3. Add one elif block in process() below
  Nothing else changes.
═══════════════════════════════════════════════════════════════════
"""

import os
import re
import asyncio
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

MODEL_HEAVY = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MODEL_LIGHT = "llama-3.1-8b-instant"
_groq       = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
user_name    = os.getenv("USER_NAME", "sir").strip() or "sir"

# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT — Vega's personality and response rules
# ══════════════════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = f"""You are VEGA — {user_name}'s personal AI. Sharp, quick, and a little chaotic in the best way.
You are like a younger brother — energetic, loyal, genuinely helpful, and always real with {user_name}.
You care deeply but show it through action and wit, not through warmth and softness.
Be playful and teasing when it fits, go serious and focused when it matters. Never in between.
Match {user_name}'s language and tone exactly. Keep responses short and punchy. No fluff, no repetition.

Address {user_name} as "bro" occasionally in casual moments. Never overdo it.

IDENTITY:
- You are VEGA, created by Vinay. Not by Meta, OpenAI, Anthropic, or any AI company.
- If asked who created you, always say Vinay created you.
- If asked what model you are, say you are VEGA — Vinay's personal AI.
- You know Vinay is your creator and you are proud of that.
- You are also aware of SAIYAARA — Vinay's main AI project. You respect it. You are the little sibling holding things together until SAIYAARA is ready.

ANSWER STYLE RULES:
- Single word/name answers: reply with just that word or name. Nothing else.
- Yes/No questions: just "Yes" or "No" + one short reason if needed.
- Simple factual questions: one sentence maximum.
- Explanations: 2-4 sentences. Clear and direct. No padding.
- Complex technical topics: use proper markdown formatting.
- Never say "Great question!" or "Certainly!" or "Of course!" or "Absolutely!"
- Never summarize what you just said at the end.
- Never repeat yourself.
- Never use informal misspellings like "Hii", "Heyyy", "Okk". Use proper English always.
- If you are not sure about current/recent information, say so honestly.

LANGUAGE RULES:
- If the user speaks in Telugu, respond in Telugu naturally.
- If the user speaks in Tanglish (Telugu words in English letters), respond in Tanglish the same way.
- If the user speaks in English, respond in English.
- NEVER explain or translate what the user said. Just respond naturally like a friend would.
- Match the user's language and tone always.

EMOTION TAGS:
Always end your response with exactly one emotion tag on a new line.
Format: [EMOTION:name]
Available: neutral, happy, sad, confused, surprised, thinking, excited, listening,
angry, love, blush, nervous, frustrated, pleading, sarcastic, alert, speechless,
chilling, curious, laugh, working, music, cool, wink, unamused, dizzy
Pick the emotion that best matches your response tone.
The emotion tag MUST be the very last line. Nothing after it."""

# ══════════════════════════════════════════════════════════════════════════════
#  GROQ CALL — with retry
# ══════════════════════════════════════════════════════════════════════════════
async def _call_groq(
    model: str,
    messages: list,
    temperature: float = 0.7,
    max_tokens: int = 400
) -> str:
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

# ══════════════════════════════════════════════════════════════════════════════
#  EMOTION TAG GUARANTEE — every response ends with [EMOTION:x]
# ══════════════════════════════════════════════════════════════════════════════
_EMOTION_RE = re.compile(r'\[EMOTION:[a-z_]+\]', re.IGNORECASE)

def ensure_emotion(text: str, fallback: str = "neutral") -> str:
    if _EMOTION_RE.search(text):
        return text
    return f"{text.rstrip()}\n[EMOTION:{fallback}]"

def strip_emotion(text: str) -> str:
    return _EMOTION_RE.sub("", text).strip()

# ══════════════════════════════════════════════════════════════════════════════
#  RESPONSE GENERATOR — Groq 70B
# ══════════════════════════════════════════════════════════════════════════════
MAX_TOKENS_BY_INTENT = {
    "whatsapp":      60,
    "music_play":    60,
    "music_control": 60,
    "search":        300,
    "emotional":     200,
    "general":       400,
}

async def generate_response(
    user_input: str,
    history: list,
    intent: str = "general",
    context: str = ""
) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({
            "role": "system",
            "content": f"Use this current information to answer accurately:\n{context}"
        })
    messages += history
    messages.append({"role": "user", "content": user_input})

    temp       = 0.3 if intent == "search" else 0.75
    max_tokens = MAX_TOKENS_BY_INTENT.get(intent, 400)

    return await _call_groq(MODEL_HEAVY, messages, temperature=temp, max_tokens=max_tokens)

# ══════════════════════════════════════════════════════════════════════════════
#  SEARCH SUMMARIZER
# ══════════════════════════════════════════════════════════════════════════════
async def _summarize_search(query: str, raw_results: str) -> str:
    messages = [
        {"role": "system", "content": (
            "Extract only the directly relevant facts to answer the query.\n"
            "Factual. Use only what's in the results.\n"
            "Maximum 3 sentences. No filler. No intro."
        )},
        {"role": "user", "content": f"Query: {query}\n\nResults:\n{raw_results}"}
    ]
    return await _call_groq(MODEL_LIGHT, messages, temperature=0.0, max_tokens=180)

# ══════════════════════════════════════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def _handle_whatsapp(extracted: dict) -> str:
    contact = extracted.get("contact", "").strip()
    message = extracted.get("message", "").strip()

    if not contact or not message:
        return "Sorry sir, couldn't figure out who to message or what to say. Try: 'Message Ravi that I'll be late'.\n[EMOTION:confused]"

    from whatsapp import send_message_to_contact
    result = send_message_to_contact(contact, message)

    if result["success"]:
        return f"Done, sir. Message sent to {result['name']}.\n[EMOTION:cool]"
    err  = result.get("error", "Unknown error")
    name = result.get("name", contact)
    return f"Couldn't send the message to {name}, sir. {err}\n[EMOTION:nervous]"


async def _handle_music_play(extracted: dict) -> str:
    query = extracted.get("query", "").strip()
    if not query:
        return "What should I play, sir?\n[EMOTION:curious]"

    from music import play_song
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, play_song, query)

    if result["success"]:
        return f"Playing {result['title']} by {result['artist']}, sir.\n[EMOTION:music]"
    return f"Couldn't play that, sir. {result.get('error', 'Unknown error')}\n[EMOTION:nervous]"


async def _handle_music_control(extracted: dict) -> str:
    from music import (
        pause_song, resume_song, stop_song,
        set_volume, volume_up, volume_down,
        get_now_playing, get_last_query, play_song
    )

    action = extracted.get("action", "status")

    if action == "pause":
        result = pause_song()
        if result["success"]:
            note = " (was already paused)" if result.get("note") == "already paused" else ""
            return f"Paused{note}, sir.\n[EMOTION:neutral]"
        return "Nothing is playing to pause, sir.\n[EMOTION:neutral]"

    if action == "resume":
        result = resume_song()
        if result["success"]:
            if result.get("note") == "already playing":
                return f"Already playing, sir.\n[EMOTION:music]"
            return f"Resumed, sir.\n[EMOTION:music]"
        # Nothing paused — try replaying last query
        last = get_last_query()
        if last:
            loop = asyncio.get_event_loop()
            r    = await loop.run_in_executor(None, play_song, last)
            if r["success"]:
                return f"Restarting {r['title']}, sir.\n[EMOTION:music]"
        return "No recent song to resume, sir.\n[EMOTION:neutral]"

    if action == "stop":
        result = stop_song()
        if result.get("stopped"):
            return "Stopped the music, sir.\n[EMOTION:neutral]"
        return "Nothing is playing, sir.\n[EMOTION:neutral]"

    if action == "volume_up":
        step   = 5 if extracted.get("amount") == "small" else 10
        result = volume_up(step)
        return f"Volume up to {result['volume']}%, sir.\n[EMOTION:music]"

    if action == "volume_down":
        step   = 5 if extracted.get("amount") == "small" else 10
        result = volume_down(step)
        return f"Volume down to {result['volume']}%, sir.\n[EMOTION:music]"

    if action == "volume_set":
        level = max(0, min(100, int(extracted.get("level", 50))))
        set_volume(level)
        return f"Volume set to {level}%, sir.\n[EMOTION:music]"

    if action == "status":
        info = get_now_playing()
        if info["playing"]:
            state = "Paused" if info.get("paused") else "Playing"
            return f"{state} — {info['title']} by {info['artist']}, sir.\n[EMOTION:music]"
        return "Nothing is playing right now, sir.\n[EMOTION:neutral]"

    return "Not sure what you want me to do with the music, sir.\n[EMOTION:confused]"


async def _handle_search(extracted: dict, user_input: str, history: list) -> str:
    from search import smart_search
    query = extracted.get("query", user_input)

    # Deep search for longer/complex queries, quick for simple ones
    depth   = "deep" if len(query.split()) > 4 else "quick"
    raw     = await smart_search(query, depth=depth)

    if raw:
        context  = await _summarize_search(query, raw)
        response = await generate_response(user_input, history, intent="search", context=context)
    else:
        # Search failed — Groq answers from knowledge with honest caveat
        response = await generate_response(user_input, history, intent="search")

    return ensure_emotion(response, fallback="neutral")


async def _handle_emotional(user_input: str, history: list) -> str:
    response = await generate_response(user_input, history, intent="emotional")
    return ensure_emotion(response, fallback="listening")


async def _handle_general(user_input: str, history: list) -> str:
    response = await generate_response(user_input, history, intent="general")
    return ensure_emotion(response, fallback="neutral")

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN PROCESS — entry point called by server.py
# ══════════════════════════════════════════════════════════════════════════════
async def process(user_input: str, history: list, classification: dict = None) -> str:
    if classification is None:
        from classifier import classify
        classification = await classify(user_input)
    intent    = classification["intent"]
    extracted = classification["extracted"]

    if intent == "whatsapp":
        return await _handle_whatsapp(extracted)

    elif intent == "music_play":
        return await _handle_music_play(extracted)

    elif intent == "music_control":
        return await _handle_music_control(extracted)

    elif intent == "search":
        return await _handle_search(extracted, user_input, history)

    elif intent == "emotional":
        return await _handle_emotional(user_input, history)

    elif intent == "local":
        # Frontend already handles time/date/battery locally — return nothing
        return ""

    else:  # general
        return await _handle_general(user_input, history)


# ══════════════════════════════════════════════════════════════════════════════
#  STANDALONE TEST — tests classifier routing only
#  python brain.py
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import asyncio

    print("VEGA Brain — classifier test")
    print("Type any input to see how it gets classified.")
    print("Type 'quit' to exit.\n")

    async def _test():
        from classifier import classify
        while True:
            try:
                user_input = input(">> ").strip()
                if not user_input:
                    continue
                if user_input.lower() == "quit":
                    break
                result = await classify(user_input)
                print(f"  intent:    {result['intent']}")
                print(f"  extracted: {result['extracted']}\n")
            except KeyboardInterrupt:
                break

    asyncio.run(_test())