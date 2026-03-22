"""
server.py — VEGA
═══════════════════════════════════════════════════════════════════
Pure WebSocket bridge + Chat Storage API.
Zero AI logic here. Zero routing logic here.
All decisions happen in brain.py and classifier.py.

RESPONSIBILITIES:
  - Serve vega.html
  - Handle WebSocket connection per client
  - Pass user input to brain.process()
  - Send response back to client
  - Detect special response types for rich UI cards
  - Store/load/delete chat history files
  - Compress conversation history when it grows large

SPECIAL RESPONSE TYPES (detected from response text):
  whatsapp_sent  — response contains [EMOTION:cool] after a whatsapp intent
  music_playing  — response contains [EMOTION:music] after a play/resume intent
  response       — everything else
═══════════════════════════════════════════════════════════════════
"""

import os
import json
import asyncio
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ── Config ────────────────────────────────────────────────────────────────────
_groq       = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_LIGHT = "llama-3.1-8b-instant"
HOST        = os.getenv("HOST", "localhost")
PORT        = int(os.getenv("PORT", 2004))
USER_NAME   = os.getenv("USER_NAME",  "Vinay")
AGENT_NAME  = os.getenv("AGENT_NAME", "Vega")

# ── Connected clients ─────────────────────────────────────────────────────────
connected_clients: list = []

# ══════════════════════════════════════════════════════════════════════════════
#  CHAT STORAGE — one .json file per chat in chat_history/
# ══════════════════════════════════════════════════════════════════════════════
CHATS_DIR = Path(__file__).parent / "chat_history"
CHATS_DIR.mkdir(exist_ok=True)


def _safe_title(title: str) -> str:
    """Sanitize chat title for use as a filename."""
    safe = "".join(c for c in title if c.isalnum() or c in " _-").strip()
    return safe[:80] or "chat"


def _find_chat_file(chat_id: str) -> Path | None:
    """Find the .json file that belongs to a given chat ID."""
    for f in CHATS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("id") == chat_id:
                return f
        except Exception:
            pass
    return None


def load_chats() -> dict:
    """Load all chats from chat_history/."""
    result = {}
    for f in CHATS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result[data["id"]] = data
        except Exception:
            pass
    return result


def save_chat_file(chat: dict):
    """
    Save chat as chat_history/<title>.json.
    Deletes old file first if title changed.
    """
    old_file = _find_chat_file(chat["id"])
    if old_file:
        old_file.unlink(missing_ok=True)
    filename = f"{_safe_title(chat.get('title', 'chat'))}.json"
    (CHATS_DIR / filename).write_text(
        json.dumps(chat, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def delete_chat_file(chat_id: str):
    """Delete the file that belongs to a given chat ID."""
    f = _find_chat_file(chat_id)
    if f:
        f.unlink(missing_ok=True)

# ══════════════════════════════════════════════════════════════════════════════
#  CHAT API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/chats")
async def get_chats():
    return JSONResponse(load_chats())


@app.post("/chats/{chat_id}")
async def save_chat(chat_id: str, request_body: dict):
    save_chat_file(request_body)
    return {"ok": True}


@app.delete("/chats/{chat_id}")
async def delete_chat(chat_id: str):
    delete_chat_file(chat_id)
    return {"ok": True}


@app.delete("/chats")
async def delete_all_chats():
    for f in CHATS_DIR.glob("*.json"):
        f.unlink(missing_ok=True)
    return {"ok": True}

# ══════════════════════════════════════════════════════════════════════════════
#  HISTORY COMPRESSION
#  Keeps context window lean as conversations grow.
#  Compresses old messages into a single summary — keeps last 6 raw.
# ══════════════════════════════════════════════════════════════════════════════
RAW_WINDOW     = 6
COMPRESS_AFTER = 10
_compression_cache: dict = {"compressed": None, "older_len": -1}


async def compress_history(history: list) -> list:
    global _compression_cache

    if len(history) <= COMPRESS_AFTER:
        _compression_cache = {"compressed": None, "older_len": -1}
        return history

    older  = history[:-RAW_WINDOW]
    recent = history[-RAW_WINDOW:]

    # Return cached compression if older portion hasn't changed
    if (
        _compression_cache["compressed"] is not None
        and _compression_cache["older_len"] == len(older)
    ):
        return _compression_cache["compressed"] + recent

    older_text = "\n".join([
        f"{'User' if m['role'] == 'user' else 'VEGA'}: {m['content']}"
        for m in older
    ])

    try:
        response = await _groq.chat.completions.create(
            model=MODEL_LIGHT,
            messages=[
                {"role": "system", "content": (
                    "Summarize this conversation in 2-3 sentences.\n"
                    "Cover: main topics, decisions, important context.\n"
                    "Past tense. Factual. No filler."
                )},
                {"role": "user", "content": older_text}
            ],
            temperature=0.0,
            max_tokens=120,
        )
        summary     = response.choices[0].message.content.strip()
        summary_msg = [{"role": "system", "content": f"Earlier in this conversation: {summary}"}]

        _compression_cache["compressed"] = summary_msg
        _compression_cache["older_len"]  = len(older)
        print(f"[History] Compressed {len(older)} msgs → 1 summary.")
        return summary_msg + recent

    except Exception as e:
        print(f"[Compression error] {e}")
        return recent

# ══════════════════════════════════════════════════════════════════════════════
#  HTML + STATIC SERVING
# ══════════════════════════════════════════════════════════════════════════════

def _load_html() -> str:
    html_path = Path(__file__).parent / "vega.html"
    html = html_path.read_text(encoding="utf-8")
    html = html.replace("{{ USER_NAME }}",  USER_NAME)
    html = html.replace("{{ AGENT_NAME }}", AGENT_NAME)
    return html


@app.get("/")
async def root():
    return HTMLResponse(_load_html())


@app.get("/health")
async def health():
    return {"status": "online", "agent": "VEGA", "port": PORT}


@app.get("/{filename}.js")
async def serve_js(filename: str):
    path = Path(__file__).parent / f"{filename}.js"
    if path.exists():
        return FileResponse(path, media_type="application/javascript")
    return JSONResponse({"error": "not found"}, status_code=404)

# ══════════════════════════════════════════════════════════════════════════════
#  RESPONSE TYPE DETECTION
#  Determines which UI card to show based on response content.
#  No routing logic — just reads what brain.py already decided.
# ══════════════════════════════════════════════════════════════════════════════

def _get_response_type(response_text: str, last_intent: str) -> str:
    """
    Returns: 'whatsapp_sent' | 'music_playing' | 'response'

    Uses the intent from classifier (stored as last_intent) combined
    with the emotion tag in the response to decide the UI card type.
    This replaces the old is_whatsapp_intent() / is_music_intent() calls.
    """
    if last_intent == "whatsapp" and "[EMOTION:cool]" in response_text:
        return "whatsapp_sent"
    if last_intent == "music_play" and "[EMOTION:music]" in response_text:
        return "music_playing"
    if last_intent == "music_control" and "[EMOTION:music]" in response_text:
        # resume also shows music card
        return "music_playing"
    return "response"

# ══════════════════════════════════════════════════════════════════════════════
#  WEBSOCKET — main real-time bridge
# ══════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    history: list     = []
    last_intent: str  = "general"  # tracks last classified intent for card detection

    # Time-based greeting
    hour = datetime.now().hour
    if hour >= 22 or hour < 5:
        greeting = "VEGA online."
    elif hour >= 17:
        greeting = "VEGA online. Good evening, sir."
    elif hour >= 12:
        greeting = "VEGA online. Good afternoon, sir."
    else:
        greeting = "VEGA online. Good morning, sir."

    await websocket.send_json({"type": "response", "text": greeting})

    try:
        while True:
            # Wait for message — ping client if idle for 30s
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue

            if data.get("type") == "pong":
                continue

            user_input = data.get("text", "").strip()
            if not user_input:
                continue

            # Classify intent first — needed for card detection later
            from classifier import classify
            classification = await classify(user_input)
            last_intent    = classification["intent"]

            # Compress history, then process
            compressed    = await compress_history(history)
            from brain import process
            response_text = await process(user_input, compressed, classification)

            # Empty response means frontend handled it locally — do nothing
            if not response_text:
                continue

            # Update history
            history.append({"role": "user",      "content": user_input})
            history.append({"role": "assistant",  "content": response_text})
            if len(history) > 60:
                history = history[-40:]

            # Determine which UI card to send
            response_type = _get_response_type(response_text, last_intent)

            if response_type == "whatsapp_sent":
                # Extract contact/message from classification (already done — no extra API call)
                extracted = classification.get("extracted", {})
                await websocket.send_json({
                    "type":    "whatsapp_sent",
                    "text":    response_text,
                    "contact": extracted.get("contact", ""),
                    "message": extracted.get("message", ""),
                })

            elif response_type == "music_playing":
                from music import get_now_playing, get_last_query
                info = get_now_playing()
                await websocket.send_json({
                    "type":   "music_playing",
                    "text":   response_text,
                    "query":  get_last_query(),
                    "title":  info.get("title", ""),
                    "artist": info.get("artist", ""),
                })

            else:
                await websocket.send_json({"type": "response", "text": response_text})

    except WebSocketDisconnect:
        print("[WS] Client disconnected.")
        if websocket in connected_clients:
            connected_clients.remove(websocket)

    except Exception as e:
        print(f"[WS error] {e}")
        if websocket in connected_clients:
            connected_clients.remove(websocket)
        try:
            await websocket.send_json({"type": "error", "text": "Something went wrong."})
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    print(f"\n  VEGA running → http://{HOST}:{PORT}\n")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)