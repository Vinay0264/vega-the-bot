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
_current_volume = 100     # always start at full volume

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
    time.sleep(0.5)

    try:
        CREATE_NO_WINDOW = 0x08000000  # Windows: hide console window

        _vlc_process = subprocess.Popen(
            [
                vlc_path,
                fetch["url"],
                "--no-video",           # audio only — no video window
                "--no-fullscreen",
                "--intf", "dummy",      # no VLC GUI window
                "--extraintf", "rc",    # RC control interface
                "--rc-host", f"{RC_HOST}:{RC_PORT}",
                "--play-and-exit",
                "--quiet",
            ],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _current_song   = fetch["title"]
        _current_artist = fetch["artist"]
        _is_playing     = True
        _is_paused      = False
        _last_query     = query

        # Wait for RC interface to initialise, then reset socket for fresh connection
        time.sleep(1.5)
        _rc_close()

        # Always start at full volume
        set_volume(100)

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

    # Ask VLC what it is actually doing right now — fixes state sync issue
    actual_state = get_vlc_status()

    if actual_state == "paused":
        # Already paused — don't toggle, just confirm
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

    # Ask VLC what it is actually doing right now — fixes state sync issue
    actual_state = get_vlc_status()

    if actual_state == "playing":
        # Already playing — nothing to resume
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
    global _current_volume
    _current_volume = max(0, min(100, _current_volume + step))
    vlc_vol = int((_current_volume / 100) * 256)
    _rc_send(f"volume {vlc_vol}")
    return {"success": True, "volume": _current_volume}


def volume_down(step: int = 10) -> dict:
    global _current_volume
    _current_volume = max(0, min(100, _current_volume - step))
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
            cmd = input(">> ").strip().lower()

            if not cmd:
                continue

            if cmd == "quit":
                stop_song()
                print("Stopped. Bye.")
                break

            elif cmd == "play":
                query = input("Song name: ").strip()
                if not query:
                    print("No song entered.")
                    continue
                print(f"Searching: {query}")
                result = play_song(query)
                if result["success"]:
                    print(f"Playing: {result['title']} by {result['artist']}")
                else:
                    print(f"Error: {result['error']}")

            elif cmd == "pause":
                result = pause_song()
                print(f"Paused: {result}")

            elif cmd == "resume":
                result = resume_song()
                print(f"Resumed: {result}")

            elif cmd == "stop":
                result = stop_song()
                print(f"Stopped: {result}")

            elif cmd.startswith("volume"):
                parts = cmd.split()
                if len(parts) == 2 and parts[1].isdigit():
                    result = set_volume(int(parts[1]))
                    print(f"Volume set to {result['volume']}%")
                elif "up" in cmd:
                    result = volume_up()
                    print(f"Volume up to {result['volume']}%")
                elif "down" in cmd:
                    result = volume_down()
                    print(f"Volume down to {result['volume']}%")
                else:
                    print("Usage: volume 70 / volume up / volume down")

            elif cmd == "status":
                info = get_now_playing()
                if info["playing"]:
                    state = "paused" if info["paused"] else "playing"
                    print(f"{state.capitalize()}: {info['title']} by {info['artist']}")
                else:
                    print("Nothing playing.")

            else:
                print("Unknown. Try: play <song> / pause / resume / stop / volume <n> / status / quit")

        except KeyboardInterrupt:
            stop_song()
            print("\nStopped. Bye.")
            sys.exit(0)