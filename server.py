"""
server.py — VEGA
FastAPI WebSocket bridge + Chat Storage API.
Local chat storage in project folder (chats.json).
Zero AI logic here.
"""

import os
import json
import asyncio
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_groq       = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_LIGHT = "llama-3.1-8b-instant"
HOST        = os.getenv("HOST", "localhost")
PORT        = int(os.getenv("PORT", 2004))
USER_NAME   = os.getenv("USER_NAME",  "Vinay")
AGENT_NAME  = os.getenv("AGENT_NAME", "Vega")

# ── Chat storage path ─────────────────────────────────────────────────────────
CHATS_FILE = Path(__file__).parent / "chats.json"

def load_chats() -> dict:
    if CHATS_FILE.exists():
        try:
            return json.loads(CHATS_FILE.read_text(encoding="utf-8"))
        except:
            return {}
    return {}

def save_chats(data: dict):
    CHATS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ── Chat API endpoints ────────────────────────────────────────────────────────
@app.get("/chats")
async def get_chats():
    return JSONResponse(load_chats())

@app.post("/chats/{chat_id}")
async def save_chat(chat_id: str, request_body: dict):
    """Save or update a single chat."""
    chats = load_chats()
    chats[chat_id] = request_body
    save_chats(chats)
    return {"ok": True}

@app.delete("/chats/{chat_id}")
async def delete_chat(chat_id: str):
    """Delete a single chat."""
    chats = load_chats()
    if chat_id in chats:
        del chats[chat_id]
        save_chats(chats)
        return {"ok": True}
    return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

@app.delete("/chats")
async def delete_all_chats():
    save_chats({})
    return {"ok": True}

# ── History compression ───────────────────────────────────────────────────────
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
    if (_compression_cache["compressed"] is not None
            and _compression_cache["older_len"] == len(older)):
        return _compression_cache["compressed"] + recent
    older_text = "\n".join([
        f"{'User' if m['role']=='user' else 'VEGA'}: {m['content']}"
        for m in older
    ])
    try:
        response = await _groq.chat.completions.create(
            model=MODEL_LIGHT,
            messages=[
                {"role":"system","content":(
                    "Summarize this conversation in 2-3 sentences.\n"
                    "Cover: main topics, decisions, important context.\n"
                    "Past tense. Factual. No filler."
                )},
                {"role":"user","content":older_text}
            ],
            temperature=0.0, max_tokens=120,
        )
        summary     = response.choices[0].message.content.strip()
        summary_msg = [{"role":"system","content":f"Earlier in this conversation: {summary}"}]
        _compression_cache["compressed"] = summary_msg
        _compression_cache["older_len"]  = len(older)
        print(f"[History] Compressed {len(older)} msgs → 1 summary.")
        return summary_msg + recent
    except Exception as e:
        print(f"[Compression error] {e}")
        return recent

# ── HTML serving ──────────────────────────────────────────────────────────────
def load_html() -> str:
    html_path = Path(__file__).parent / "vega.html"
    html = html_path.read_text(encoding="utf-8")
    html = html.replace("{{ USER_NAME }}",  USER_NAME)
    html = html.replace("{{ AGENT_NAME }}", AGENT_NAME)
    return html

@app.get("/")
async def root():
    return HTMLResponse(load_html())

@app.get("/health")
async def health():
    return {"status":"online","agent":"VEGA","port":PORT}

# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    history: list = []

    hour = datetime.now().hour
    if hour >= 22 or hour < 5:
        greeting = "VEGA online."
    elif hour >= 17:
        greeting = "VEGA online. Good evening, sir."
    elif hour >= 12:
        greeting = "VEGA online. Good afternoon, sir."
    else:
        greeting = "VEGA online. Good morning, sir."

    await websocket.send_json({"type":"response","text":greeting})

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_json({"type":"ping"})
                continue

            msg_type = data.get("type","message")
            if msg_type == "pong":
                continue

            user_input = data.get("text","").strip()
            if not user_input:
                continue

            compressed = await compress_history(history)

            from brain import process
            response_text = await process(user_input, compressed)

            history.append({"role":"user",      "content":user_input})
            history.append({"role":"assistant",  "content":response_text})

            if len(history) > 60:
                history = history[-40:]

            await websocket.send_json({"type":"response","text":response_text})

    except WebSocketDisconnect:
        print("[WS] Client disconnected.")
    except Exception as e:
        print(f"[WS error] {e}")
        try:
            await websocket.send_json({"type":"error","text":"Something went wrong."})
        except:
            pass

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print(f"\n  VEGA running → http://{HOST}:{PORT}\n")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)