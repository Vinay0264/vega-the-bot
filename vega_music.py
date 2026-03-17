"""
vega_music.py — VEGA Music Player
====================================
Streams audio from YouTube via yt-dlp directly into VLC.
No browser, no ads, no skipping needed.

REQUIREMENTS:
  pip install yt-dlp

VLC must be installed:
  https://www.videolan.org/vlc/

USAGE (called by brain.py):
  from vega_music import play_song, stop_song, get_now_playing
"""

import subprocess
import threading
import os
import sys

# ── VLC executable path (Windows) ────────────────────────────────────────────
VLC_PATHS = [
    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
]

# ── State ─────────────────────────────────────────────────────────────────────
_vlc_process   = None   # current VLC subprocess
_current_song  = ""     # currently playing song title
_current_artist= ""     # currently playing artist
_is_playing    = False
_last_query    = ""     # last search query — used for resume
_last_song     = ""     # last song title — used for resume


def _find_vlc() -> str:
    """Find VLC executable on Windows."""
    for path in VLC_PATHS:
        if os.path.exists(path):
            return path
    # Try PATH
    try:
        result = subprocess.run(["where", "vlc"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None


def _get_audio_url(query: str) -> dict:
    """
    Use yt-dlp to search YouTube and get direct audio stream URL.
    Returns { url, title, artist, duration } or { error }
    """
    try:
        import yt_dlp

        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'default_search': 'ytsearch1',  # search and pick top result
            'noplaylist': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)

            # Get first result
            if 'entries' in info:
                info = info['entries'][0]

            # Get best audio format URL
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

            # Clean up title — remove common YouTube noise
            clean_title = title
            for noise in ['(Official Video)', '(Official Music Video)', '(Lyric Video)',
                          '(Audio)', '(Full Song)', '[Official Video]', '(HD)', '(4K)',
                          '(Official)', 'ft.', 'feat.']:
                clean_title = clean_title.replace(noise, '').strip()

            return {
                "url": audio_url,
                "title": clean_title,
                "artist": uploader,
                "duration": duration,
            }

    except ImportError:
        return {"error": "yt-dlp not installed. Run: pip install yt-dlp"}
    except Exception as e:
        return {"error": str(e)}


def play_song(query: str) -> dict:
    """
    Search for a song and play it in VLC.
    Returns { success, title, artist } or { success: False, error }
    """
    global _vlc_process, _current_song, _current_artist, _is_playing, _last_query, _last_song

    # Find VLC
    vlc_path = _find_vlc()
    if not vlc_path:
        return {
            "success": False,
            "error": "VLC not found. Please install VLC from https://www.videolan.org/vlc/"
        }

    # Get audio URL
    print(f"[Music] 🔍 Searching: {query}")
    result = _get_audio_url(query)

    if "error" in result:
        return {"success": False, "error": result["error"]}

    # Stop any current song
    stop_song()

    # Launch VLC with audio stream
    try:
        _vlc_process = subprocess.Popen(
            [
                vlc_path,
                result["url"],
                "--intf", "dummy",          # no VLC GUI window
                "--no-video",               # audio only
                "--quiet",                  # no VLC console spam
                "--play-and-exit",          # exit when song ends
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _current_song   = result["title"]
        _current_artist = result["artist"]
        _is_playing     = True
        _last_query     = query
        _last_song      = result["title"]

        print(f"[Music] ▶️  Playing: {_current_song} by {_current_artist}")

        # Watch for process end in background
        def _watch():
            global _is_playing
            _vlc_process.wait()
            _is_playing = False
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


def stop_song() -> dict:
    """Stop currently playing song."""
    global _vlc_process, _is_playing, _current_song, _current_artist

    if _vlc_process and _vlc_process.poll() is None:
        _vlc_process.terminate()
        _vlc_process = None

    _is_playing     = False
    stopped         = _current_song
    _current_song   = ""
    _current_artist = ""

    return {"success": True, "stopped": stopped}


def get_now_playing() -> dict:
    """Return currently playing song info."""
    if _is_playing and _current_song:
        return {
            "playing": True,
            "title": _current_song,
            "artist": _current_artist,
        }
    return {"playing": False}