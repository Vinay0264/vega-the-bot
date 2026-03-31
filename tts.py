"""
tts.py — VEGA
Edge-TTS + pygame for voice output.
pygame.mixer initialized once at import time.
"""

import asyncio
import tempfile
import os
import re
import time
import edge_tts
import pygame

VOICE = "en-US-GuyNeural"
RATE  = "+0%"
PITCH = "+30Hz"

# init once
pygame.mixer.init()

# ══════════════════════════════════════════════════════════════════════════════
#  TEXT CLEANING
# ══════════════════════════════════════════════════════════════════════════════

def _clean(text: str) -> str:
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'#{1,3}\s', '', text)
    text = re.sub(r'[-*]\s', '', text)
    text = re.sub(r'\n{2,}', '\n', text)
    return text.strip()

_OVERFLOW_PHRASES = [
    'Sir, this answer is a bit detailed — please take a look.',
    'There is more to read here, sir.',
    'I have written out the full answer for you, sir.',
    'Sir, please have a look at the complete response.',
]

def _get_tts_text(text: str) -> str:
    clean = _clean(text)
    lines = [l for l in clean.split('\n') if l.strip()]
    if len(lines) <= 4:
        return clean
    return _OVERFLOW_PHRASES[int(time.time()) % len(_OVERFLOW_PHRASES)]

def _get_voice(text: str) -> str:
    # Count Telugu characters (Unicode range: 0C00–0C7F)
    telugu = len([c for c in text if '\u0C00' <= c <= '\u0C7F'])
    # Count Hindi/Devanagari characters (Unicode range: 0900–097F)
    hindi  = len([c for c in text if '\u0900' <= c <= '\u097F'])
    
    if telugu > 5:
        return "te-IN-MohanNeural"
    if hindi > 5:
        return "hi-IN-MadhurNeural"
    return "en-US-GuyNeural"

# ══════════════════════════════════════════════════════════════════════════════
#  SPEAK
# ══════════════════════════════════════════════════════════════════════════════

async def speak(text: str):
    if not text or not text.strip():
        return
    try:
        tts_text = _get_tts_text(text)
        tts = edge_tts.Communicate(tts_text, voice=_get_voice(tts_text), rate=RATE, pitch=PITCH)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
        await tts.save(path)
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            await asyncio.sleep(0.1)
        pygame.mixer.music.unload()  # release file handle
        await asyncio.sleep(0.1)     # small buffer
        os.unlink(path)
    except Exception as e:
        print(f"[TTS] error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
#  STANDALONE TEST
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    async def _test():
        text = input("Enter text to speak (or press Enter for default): ").strip()
        if not text:
            text = "Hey sir, VEGA is online and ready. What do you need?"
        await speak(text)

    asyncio.run(_test())