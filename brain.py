"""
brain.py — VEGA
Smart routing: query classification → local intercept → search → groq.
BS4 filtering for web content. Response length control.
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

# ── Query type classification — pure Python, no AI call ──────────────────────
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