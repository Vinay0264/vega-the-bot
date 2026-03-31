"""
actions/music.py — VEGA Music Player
═══════════════════════════════════════════════════════════════════
Streams audio from YouTube via yt-dlp into VLC.
Uses VLC RC Interface for pause/resume/volume/status.

KEY FIX vs old version:
  Old: _is_paused flag was internal — could drift from VLC's real state
       if user manually paused/resumed VLC outside of Vega.
  New: get_vlc_status() queries VLC directly via RC before any toggle.
       Internal flags are updated from VLC's actual response, not assumed.

FIXES in this version:
  1. volume up/down now reads VLC's real volume before stepping
  2. RC ready-wait replaced with poll loop (no more flat 2.5s sleep)
  3. set_volume(100) on play removed — volume persists across songs
  4. Standalone test CLI: play <query> inline, no second prompt
  5. stop_song() early-returns if nothing is playing

REQUIREMENTS:
  pip install yt-dlp

VLC must be installed:
  https://www.videolan.org/vlc/
═══════════════════════════════════════════════════════════════════
"""

import os
import re
import subprocess
import threading
import socket
import time
import state

# ── VLC paths ─────────────────────────────────────────────────────────────────
VLC_PATHS = [
    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
]

# ── RC Interface config ───────────────────────────────────────────────────────
RC_HOST = "localhost"
RC_PORT = 9999

# ── State ─────────────────────────────────────────────────────────────────────
_vlc_process    = None
_rc_socket      = None
_current_song   = ""
_current_artist = ""
_is_playing     = False   # True if VLC process is alive
_is_paused      = False   # mirrors VLC's actual state — updated via RC query
_last_query     = ""      # original search query — used for resume/restart
_current_volume = 100     # tracks last known volume level

# ══════════════════════════════════════════════════════════════════════════════
#  VLC FINDER
# ══════════════════════════════════════════════════════════════════════════════
def _find_vlc():
    for path in VLC_PATHS:
        if os.path.exists(path):
            return path
    try:
        result = subprocess.run(["where", "vlc"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None

# ══════════════════════════════════════════════════════════════════════════════
#  RC SOCKET — send command, receive response
# ══════════════════════════════════════════════════════════════════════════════
def _rc_send(command: str) -> str:
    """Send a command to VLC RC interface. Returns response string."""
    global _rc_socket
    for attempt in range(2):
        try:
            if _rc_socket is None:
                _rc_socket = socket.create_connection((RC_HOST, RC_PORT), timeout=3)
                time.sleep(0.3)
                # Flush VLC's welcome banner
                _rc_socket.settimeout(0.5)
                try:
                    _rc_socket.recv(2048)
                except Exception:
                    pass
                _rc_socket.settimeout(3)

            _rc_socket.sendall((command + "\n").encode())
            time.sleep(0.15)

            try:
                _rc_socket.settimeout(0.5)
                response = _rc_socket.recv(1024).decode().strip()
                _rc_socket.settimeout(3)
                return response
            except Exception:
                return ""

        except Exception as e:
            print(f"[RC] attempt {attempt + 1} failed: {e}")
            _rc_socket = None
            time.sleep(0.3)
    return ""


def _rc_close():
    """Close and discard the RC socket."""
    global _rc_socket
    if _rc_socket:
        try:
            _rc_socket.close()
        except Exception:
            pass
        _rc_socket = None


def _rc_wait_ready(timeout: float = 3.0) -> bool:
    """
    FIX 2: Poll the RC port until VLC is ready, instead of flat sleep.
    Tries every 200ms. Returns True if ready, False if timed out.
    Average wait drops from 2.5s to ~0.6s.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.create_connection((RC_HOST, RC_PORT), timeout=0.3)
            sock.close()
            return True
        except Exception:
            time.sleep(0.2)
    return False

# ══════════════════════════════════════════════════════════════════════════════
#  VLC STATE QUERY — the fix for state sync
#  Asks VLC directly: "are you paused right now?"
#  Updates internal _is_paused from VLC's real answer.
# ══════════════════════════════════════════════════════════════════════════════
def get_vlc_status() -> str:
    """
    Query VLC's actual playback state via RC.
    Returns: 'playing' | 'paused' | 'stopped' | 'unknown'
    Also syncs internal _is_paused flag to match reality.
    """
    global _is_paused

    if not _is_playing:
        return "stopped"

    response = _rc_send("status")
    if not response:
        return "unknown"

    lower = response.lower()
    if "state playing" in lower:
        _is_paused = False
        return "playing"
    elif "state paused" in lower:
        _is_paused = True
        return "paused"
    elif "state stopped" in lower:
        return "stopped"

    return "unknown"


def _get_vlc_volume() -> int:
    """
    Read VLC's actual current volume via RC.
    Flushes stale RC buffer before sending to avoid reading garbage.
    VLC reports volume as 0–256. We convert back to 0–100.
    Returns -1 if unreadable (caller falls back to _current_volume).
    """
    global _rc_socket
    # Flush any stale data sitting in the buffer before we send our command
    if _rc_socket:
        try:
            _rc_socket.settimeout(0.1)
            _rc_socket.recv(4096)  # discard anything queued
        except Exception:
            pass
        finally:
            _rc_socket.settimeout(3)

    response = _rc_send("volume")
    if not response:
        return -1
    # VLC response format: "> ( audio volume: 256 )"
    match = re.search(r"audio volume:\s*(\d+)", response)
    if match:
        vlc_vol = int(match.group(1))
        return round((vlc_vol / 256) * 100)
    return -1

# ══════════════════════════════════════════════════════════════════════════════
#  AUDIO URL FETCH — yt-dlp
# ══════════════════════════════════════════════════════════════════════════════
_TITLE_NOISE = [
    "(Official Video)", "(Official Music Video)", "(Lyric Video)",
    "(Audio)", "(Full Song)", "[Official Video]", "(HD)", "(4K)",
    "(Official)", "| Official", "- Official", "(Official Audio)",
    "(Full Video)", "[Full Video]", "(Visualizer)",
]

def _get_audio_url(query: str) -> dict:
    """Search YouTube and return direct audio stream URL + metadata."""
    try:
        import yt_dlp

        ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if "entries" in info:
                info = info["entries"][0]

            # Prefer pure audio format, fall back to best available
            audio_url = None
            if "url" in info:
                audio_url = info["url"]
            elif "formats" in info:
                for fmt in reversed(info["formats"]):
                    if fmt.get("acodec") != "none" and fmt.get("vcodec") == "none":
                        audio_url = fmt["url"]
                        break
                if not audio_url:
                    audio_url = info["formats"][-1]["url"]

            title    = info.get("title", query)
            uploader = info.get("uploader", info.get("channel", ""))
            duration = info.get("duration", 0)

            # Strip YouTube title noise
            for noise in _TITLE_NOISE:
                title = title.replace(noise, "").strip()
            # Remove trailing dash or pipe left over after strip
            title = re.sub(r"[\s\-|]+$", "", title).strip()

            return {
                "url":      audio_url,
                "title":    title,
                "artist":   uploader,
                "duration": duration,
            }

    except ImportError:
        return {"error": "yt-dlp not installed. Run: pip install yt-dlp"}
    except Exception as e:
        return {"error": str(e)}

# ══════════════════════════════════════════════════════════════════════════════
#  PLAY
# ══════════════════════════════════════════════════════════════════════════════
def play_song(query: str) -> dict:
    global _vlc_process, _current_song, _current_artist
    global _is_playing, _is_paused, _last_query

    vlc_path = _find_vlc()
    if not vlc_path:
        return {
            "success": False,
            "error": "VLC not found. Install from https://www.videolan.org/vlc/"
        }

    print(f"[Music] searching: {query}")
    fetch = _get_audio_url(query)
    if "error" in fetch:
        return {"success": False, "error": fetch["error"]}

    # Clean stop of any current song before starting new one
    stop_song()

    try:
        # Tell Windows: create window minimized to taskbar
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 6  # SW_MINIMIZE

        _vlc_process = subprocess.Popen(
            [
                vlc_path,
                fetch["url"],
                "--intf", "rc",
                "--rc-host", f"{RC_HOST}:{RC_PORT}",
                "--no-video",
                "--play-and-exit",
                "--quiet",
            ],
            startupinfo=startupinfo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _current_song   = fetch["title"]
        _current_artist = fetch["artist"]
        _is_playing     = True
        _is_paused      = False
        _last_query     = query
        state.set("now_playing", {"title": _current_song, "artist": _current_artist, "playing": True})
        state.set("last_query", query)

        # FIX 2: Poll until RC is ready instead of flat 2.5s sleep
        ready = _rc_wait_ready(timeout=3.0)
        _rc_close()  # reset socket for fresh connection after poll
        if not ready:
            print("[Music] warning: RC not ready after 3s, continuing anyway")

        # FIX 3: Re-apply last known volume — VLC resets to 256 on every new stream.
        # Small sleep needed: port is open but RC isn't fully ready to accept commands yet.
        time.sleep(0.5)
        set_volume(_current_volume)

        print(f"[Music] playing: {_current_song} by {_current_artist}")

        # Background watcher — updates state flags when song ends naturally
        def _watch():
            global _is_playing, _is_paused
            _vlc_process.wait()
            _is_playing = False
            _is_paused  = False
            _rc_close()
            print("[Music] song ended.")

        threading.Thread(target=_watch, daemon=True).start()

        return {
            "success":  True,
            "title":    _current_song,
            "artist":   _current_artist,
            "duration": fetch.get("duration", 0),
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

# ══════════════════════════════════════════════════════════════════════════════
#  PAUSE — queries VLC state first, only sends pause if actually playing
# ══════════════════════════════════════════════════════════════════════════════
def pause_song() -> dict:
    global _is_paused

    if not _is_playing:
        return {"success": False, "error": "Nothing is playing"}

    actual_state = get_vlc_status()

    if actual_state == "paused":
        return {"success": True, "title": _current_song, "note": "already paused"}

    if actual_state in ("playing", "unknown"):
        _rc_send("pause")
        _is_paused = True
        return {"success": True, "title": _current_song}

    return {"success": False, "error": "VLC is not in a pausable state"}

# ══════════════════════════════════════════════════════════════════════════════
#  RESUME — queries VLC state first, only sends resume if actually paused
# ══════════════════════════════════════════════════════════════════════════════
def resume_song() -> dict:
    global _is_paused

    if not _is_playing:
        return {"success": False, "error": "Nothing to resume"}

    actual_state = get_vlc_status()

    if actual_state == "playing":
        return {"success": True, "title": _current_song, "note": "already playing"}

    if actual_state in ("paused", "unknown"):
        _rc_send("pause")   # VLC RC toggle: paused -> playing
        _is_paused = False
        return {"success": True, "title": _current_song}

    return {"success": False, "error": "VLC is not in a resumable state"}

# ══════════════════════════════════════════════════════════════════════════════
#  STOP
# ══════════════════════════════════════════════════════════════════════════════
def stop_song() -> dict:
    global _vlc_process, _is_playing, _is_paused, _current_song, _current_artist

    # FIX 5: Early return if nothing is playing
    if not _is_playing and _vlc_process is None:
        return {"success": True, "stopped": ""}

    _rc_send("stop")
    _rc_close()

    if _vlc_process and _vlc_process.poll() is None:
        _vlc_process.terminate()
        try:
            _vlc_process.wait(timeout=3)
        except Exception:
            _vlc_process.kill()
        _vlc_process = None

    _is_playing     = False
    _is_paused      = False
    stopped         = _current_song
    _current_song   = ""
    _current_artist = ""
    state.set("now_playing", {})

    return {"success": True, "stopped": stopped}

# ══════════════════════════════════════════════════════════════════════════════
#  VOLUME
# ══════════════════════════════════════════════════════════════════════════════
def set_volume(level: int) -> dict:
    global _current_volume
    _current_volume = max(0, min(100, level))
    vlc_vol = int((_current_volume / 100) * 256)
    _rc_send(f"volume {vlc_vol}")
    return {"success": True, "volume": _current_volume}


def volume_up(step: int = 10) -> dict:
    """FIX 1: Read VLC's real volume before stepping up."""
    global _current_volume
    real = _get_vlc_volume()
    base = real if real >= 0 else _current_volume
    _current_volume = min(100, base + step)
    vlc_vol = int((_current_volume / 100) * 256)
    _rc_send(f"volume {vlc_vol}")
    return {"success": True, "volume": _current_volume}


def volume_down(step: int = 10) -> dict:
    """FIX 1: Read VLC's real volume before stepping down."""
    global _current_volume
    real = _get_vlc_volume()
    base = real if real >= 0 else _current_volume
    _current_volume = max(0, base - step)
    vlc_vol = int((_current_volume / 100) * 256)
    _rc_send(f"volume {vlc_vol}")
    return {"success": True, "volume": _current_volume}

# ══════════════════════════════════════════════════════════════════════════════
#  STATUS AND HELPERS — called by brain.py
# ══════════════════════════════════════════════════════════════════════════════
def get_now_playing() -> dict:
    """Returns current playback info. Syncs _is_paused from VLC before returning."""
    if _is_playing and _current_song:
        get_vlc_status()   # sync internal flag with VLC's reality
        return {
            "playing": True,
            "paused":  _is_paused,
            "title":   _current_song,
            "artist":  _current_artist,
        }
    return {"playing": False}


def get_last_query() -> str:
    """Returns the last search query — used by brain.py for restart-on-resume."""
    return _last_query

# ══════════════════════════════════════════════════════════════════════════════
#  STANDALONE TEST — run this file directly to test music playback
#  python actions/music.py
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    print("VEGA Music Player — standalone test")
    print("Commands: play <song> / pause / resume / stop / volume <n> / volume up / volume down / status / quit\n")

    while True:
        try:
            cmd = input(">> ").strip()
            lower = cmd.lower()

            if not cmd:
                continue

            if lower == "quit":
                stop_song()
                print("Stopped. Bye.")
                break

            # FIX 4: play <query> inline — no second prompt
            elif lower.startswith("play "):
                query = cmd[5:].strip()
                if not query:
                    print("Usage: play <song name>")
                    continue
                print(f"Searching: {query}")
                result = play_song(query)
                if result["success"]:
                    print(f"Playing: {result['title']} by {result['artist']}")
                else:
                    print(f"Error: {result['error']}")

            elif lower == "play":
                print("Usage: play <song name>")

            elif lower == "pause":
                result = pause_song()
                print(f"Paused: {result}")

            elif lower == "resume":
                result = resume_song()
                print(f"Resumed: {result}")

            elif lower == "stop":
                result = stop_song()
                print(f"Stopped: {result}")

            elif lower.startswith("volume"):
                parts = lower.split()
                if len(parts) == 2 and parts[1].isdigit():
                    result = set_volume(int(parts[1]))
                    print(f"Volume set to {result['volume']}%")
                elif "up" in lower:
                    result = volume_up()
                    print(f"Volume up to {result['volume']}%")
                elif "down" in lower:
                    result = volume_down()
                    print(f"Volume down to {result['volume']}%")
                else:
                    print("Usage: volume 70 / volume up / volume down")

            elif lower == "status":
                info = get_now_playing()
                if info["playing"]:
                    current_status = "paused" if info["paused"] else "playing"
                    print(f"{current_status.capitalize()}: {info['title']} by {info['artist']}")
                else:
                    print("Nothing playing.")

            else:
                print("Unknown. Try: play <song> / pause / resume / stop / volume <n> / status / quit")

        except KeyboardInterrupt:
            stop_song()
            print("\nStopped. Bye.")
            sys.exit(0)