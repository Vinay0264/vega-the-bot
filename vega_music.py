"""
vega_music.py — VEGA Music Player
====================================
Streams audio from YouTube via yt-dlp into VLC.
Uses VLC RC Interface for true pause/resume + volume control.

REQUIREMENTS:
  pip install yt-dlp

VLC must be installed:
  https://www.videolan.org/vlc/
"""

import subprocess
import threading
import socket
import time
import os

# ── VLC paths ─────────────────────────────────────────────────────────────────
VLC_PATHS = [
    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
]

# ── RC Interface config ───────────────────────────────────────────────────────
RC_HOST = "localhost"
RC_PORT = 9999

# ── State ─────────────────────────────────────────────────────────────────────
_vlc_process   = None
_rc_socket     = None
_current_song  = ""
_current_artist= ""
_is_playing    = False
_is_paused     = False
_last_query    = ""
_last_song     = ""


# ── Find VLC ──────────────────────────────────────────────────────────────────
def _find_vlc() -> str:
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


# ── RC socket: send command to VLC ───────────────────────────────────────────
def _rc_send(command: str) -> str:
    """Send a command to VLC RC interface. Returns response."""
    global _rc_socket
    for attempt in range(2):
        try:
            if _rc_socket is None:
                _rc_socket = socket.create_connection((RC_HOST, RC_PORT), timeout=3)
                time.sleep(0.3)
                # Flush welcome message
                _rc_socket.settimeout(0.5)
                try: _rc_socket.recv(2048)
                except: pass
                _rc_socket.settimeout(3)

            _rc_socket.sendall((command + "\n").encode())
            time.sleep(0.15)
            try:
                _rc_socket.settimeout(0.5)
                response = _rc_socket.recv(1024).decode().strip()
                _rc_socket.settimeout(3)
                return response
            except:
                return ""
        except Exception as e:
            print(f"[RC] attempt {attempt+1} failed: {e}")
            _rc_socket = None
            time.sleep(0.3)
    return ""


def _rc_close():
    """Close RC socket."""
    global _rc_socket
    if _rc_socket:
        try: _rc_socket.close()
        except: pass
        _rc_socket = None


# ── Get audio URL via yt-dlp ──────────────────────────────────────────────────
def _get_audio_url(query: str) -> dict:
    try:
        import yt_dlp

        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'noplaylist': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if 'entries' in info:
                info = info['entries'][0]

            audio_url = None
            if 'url' in info:
                audio_url = info['url']
            elif 'formats' in info:
                for f in reversed(info['formats']):
                    if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                        audio_url = f['url']
                        break
                if not audio_url:
                    audio_url = info['formats'][-1]['url']

            title    = info.get('title', query)
            uploader = info.get('uploader', info.get('channel', ''))
            duration = info.get('duration', 0)

            # Clean YouTube title noise
            for noise in ['(Official Video)', '(Official Music Video)', '(Lyric Video)',
                          '(Audio)', '(Full Song)', '[Official Video]', '(HD)', '(4K)',
                          '(Official)', '| Official', '- Official']:
                title = title.replace(noise, '').strip()

            return {"url": audio_url, "title": title, "artist": uploader, "duration": duration}

    except ImportError:
        return {"error": "yt-dlp not installed. Run: pip install yt-dlp"}
    except Exception as e:
        return {"error": str(e)}


# ── Play song ─────────────────────────────────────────────────────────────────
def play_song(query: str) -> dict:
    global _vlc_process, _current_song, _current_artist
    global _is_playing, _is_paused, _last_query, _last_song

    vlc_path = _find_vlc()
    if not vlc_path:
        return {"success": False, "error": "VLC not found. Install from https://www.videolan.org/vlc/"}

    print(f"[Music] 🔍 Searching: {query}")
    result = _get_audio_url(query)
    if "error" in result:
        return {"success": False, "error": result["error"]}

    # Stop current song cleanly
    stop_song()
    time.sleep(0.5)  # give VLC time to close

    try:
        # Hide the command window on Windows
        CREATE_NO_WINDOW = 0x08000000

        _vlc_process = subprocess.Popen([
            vlc_path,
            result["url"],

            # ── VLC visible in taskbar only (no video window, no fullscreen) ─
            "--no-video",               # audio only — no video window at all
            "--no-fullscreen",          # never fullscreen
            "--qt-start-minimized",     # start minimized to taskbar

            # ── To HIDE VLC completely (no taskbar), uncomment below
            # and comment out the 3 lines above:
            # "--intf", "dummy",
            # "--no-video",
            # ──────────────────────────────────────────────────────────────────

            # ── RC Interface for pause/resume/volume ──────────────────────────
            "--extraintf", "rc",
            "--rc-host", f"{RC_HOST}:{RC_PORT}",

            "--play-and-exit",
            "--quiet",
        ],
        creationflags=CREATE_NO_WINDOW,   # ← hides the black command window
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        )

        _current_song   = result["title"]
        _current_artist = result["artist"]
        _is_playing     = True
        _is_paused      = False
        _last_query     = query
        _last_song      = result["title"]

        # Wait for RC interface to be ready
        time.sleep(1.5)
        _rc_close()  # reset socket so it reconnects fresh

        print(f"[Music] ▶️  Playing: {_current_song} by {_current_artist}")

        # Watch for process end
        def _watch():
            global _is_playing, _is_paused
            _vlc_process.wait()
            _is_playing = False
            _is_paused  = False
            _rc_close()
            print("[Music] ⏹  Song ended.")
        threading.Thread(target=_watch, daemon=True).start()

        return {
            "success": True,
            "title": _current_song,
            "artist": _current_artist,
            "duration": result.get("duration", 0),
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Pause ─────────────────────────────────────────────────────────────────────
def pause_song() -> dict:
    global _is_paused
    if not _is_playing:
        return {"success": False, "error": "Nothing is playing"}
    _rc_send("pause")   # VLC RC: 'pause' toggles play/pause
    _is_paused = True
    return {"success": True, "title": _current_song}


# ── Resume ────────────────────────────────────────────────────────────────────
def resume_song() -> dict:
    global _is_paused
    if not _is_playing:
        return {"success": False, "error": "Nothing to resume"}
    _rc_send("pause")   # VLC RC: same 'pause' command toggles back to playing
    _is_paused = False
    return {"success": True, "title": _current_song}


# ── Stop ──────────────────────────────────────────────────────────────────────
def stop_song() -> dict:
    global _vlc_process, _is_playing, _is_paused, _current_song, _current_artist

    _rc_send("stop")
    _rc_close()

    if _vlc_process and _vlc_process.poll() is None:
        _vlc_process.terminate()
        try: _vlc_process.wait(timeout=3)
        except: _vlc_process.kill()
        _vlc_process = None

    _is_playing     = False
    _is_paused      = False
    stopped         = _current_song
    _current_song   = ""
    _current_artist = ""

    return {"success": True, "stopped": stopped}


# ── Volume ────────────────────────────────────────────────────────────────────
def set_volume(level: int) -> dict:
    """
    Set volume. level = 0 to 100 (maps to VLC 0-320).
    100% = normal, 200% = double (VLC supports up to 320).
    """
    vlc_vol = int((level / 100) * 256)  # VLC uses 0-320, 256 = 100%
    vlc_vol = max(0, min(320, vlc_vol))
    _rc_send(f"volume {vlc_vol}")
    return {"success": True, "volume": level}


def volume_up() -> dict:
    _rc_send("volup 20")
    return {"success": True}


def volume_down() -> dict:
    _rc_send("voldown 20")
    return {"success": True}


# ── Get status ────────────────────────────────────────────────────────────────
def get_now_playing() -> dict:
    if _is_playing and _current_song:
        return {
            "playing": True,
            "paused": _is_paused,
            "title": _current_song,
            "artist": _current_artist,
        }
    return {"playing": False}