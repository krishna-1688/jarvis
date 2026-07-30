"""
features/spotify.py — Spotify playback control via the Web API (spotipy).

ONE-TIME SETUP (required before any of this works):
  1. Create an app at https://developer.spotify.com/dashboard
  2. On that app, add this exact Redirect URI: http://127.0.0.1:8888/callback
     (must match config.SPOTIFY_REDIRECT_URI exactly)
  3. Put the app's Client ID/Secret into backend/.env:
       SPOTIFY_CLIENT_ID=...
       SPOTIFY_CLIENT_SECRET=...
  4. The first real command opens a browser for one-time Spotify login +
     consent. After that, spotipy caches a refresh token to
     data/.spotify_cache and never asks again.

HARD REQUIREMENTS (Spotify's own, not this code's):
  - Playback CONTROL (play/pause/skip/volume) needs Spotify PREMIUM —
    the Web API returns 403 "Premium required" for free accounts. Only
    reading what's currently playing works on free accounts.
  - The Web API can only control a device that's already open somewhere
    (phone app, desktop app, or open.spotify.com) — it can't launch
    Spotify from nothing. If no device is open, every command here
    returns a clear "open Spotify somewhere first" message instead of a
    confusing API error.
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI
from features.base import FeatureResult

SCOPES = (
    "user-modify-playback-state user-read-playback-state "
    "user-read-currently-playing"
)

NOT_CONFIGURED_MSG = (
    "Spotify isn't set up yet, boss. Create an app at "
    "developer.spotify.com/dashboard, add http://127.0.0.1:8888/callback "
    "as a Redirect URI on it, then put the Client ID and Secret into "
    "backend/.env as SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET."
)

_client = None


def _get_client():
    """
    Lazily builds the authenticated spotipy client — never at import
    time, since credentials might not be configured yet and importing
    this module (e.g. just to register the router intent) shouldn't
    crash the whole app over an optional feature. Returns None if
    credentials are missing.
    """
    global _client
    if _client is not None:
        return _client

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None

    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, ".spotify_cache")

    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPES,
        cache_path=cache_path,
        open_browser=True,
    )
    _client = spotipy.Spotify(auth_manager=auth_manager)
    return _client


def _no_device_message() -> str:
    return ("I can't find an open Spotify anywhere — open the Spotify app "
            "(phone, desktop, or web player) and try again.")


def _handle_spotify_error(e: Exception) -> FeatureResult:
    """
    Translates the two error shapes actually worth distinguishing for
    the user (Premium requirement, auth/config trouble) into a plain
    sentence — everything else falls back to a generic apology rather
    than leaking a raw exception string.
    """
    import spotipy

    msg_lower = str(e).lower()
    if isinstance(e, spotipy.exceptions.SpotifyException) and getattr(e, "http_status", None) == 403:
        msg = ("Spotify says that needs Premium, boss — playback control (play/pause/skip/volume) "
               "isn't available on a free account, only reading what's playing.")
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="premium_required")
    if "no active device" in msg_lower or "device not found" in msg_lower:
        msg = _no_device_message()
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_device")
    if isinstance(e, spotipy.exceptions.SpotifyOauthError) or "invalid_client" in msg_lower:
        msg = "Spotify login failed — double check the Client ID/Secret in backend/.env are correct."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="auth_error")

    msg = "Spotify didn't cooperate there — try again in a bit."
    print(f"[spotify] unexpected error: {e}")
    return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="spotify_error")


def _get_active_device_id(sp) -> str | None:
    """The currently-active device, or the first available one if none
    is marked active (Spotify still accepts commands targeted at a
    specific device even if it isn't the 'active' one), or None if
    nothing's open anywhere."""
    devices = sp.devices().get("devices", [])
    if not devices:
        return None
    for d in devices:
        if d.get("is_active"):
            return d["id"]
    return devices[0]["id"]


def _require_client():
    """Returns (client, None) or (None, FeatureResult-if-not-configured)."""
    sp = _get_client()
    if sp is None:
        return None, FeatureResult(ok=False, data={}, display=NOT_CONFIGURED_MSG,
                                    spoken=NOT_CONFIGURED_MSG, error="not_configured")
    return sp, None


# ══════════════════════════════════════════════════════════
#   PUBLIC FEATURE-RESULT API
# ══════════════════════════════════════════════════════════

def play_song(query: str) -> FeatureResult:
    """'play <song>' / 'play <song> by <artist>' — searches Spotify and
    starts playback on whatever device is open."""
    sp, err = _require_client()
    if err:
        return err

    query = (query or "").strip()
    if not query:
        return resume_playback()

    try:
        results = sp.search(q=query, type="track", limit=1)
        tracks = results.get("tracks", {}).get("items", [])
        if not tracks:
            msg = f'Couldn\'t find "{query}" on Spotify.'
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")

        track = tracks[0]
        track_name = track["name"]
        artist_name = ", ".join(a["name"] for a in track["artists"])

        device_id = _get_active_device_id(sp)
        if device_id is None:
            msg = _no_device_message()
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_device")

        sp.start_playback(device_id=device_id, uris=[track["uri"]])
        msg = f"Playing {track_name} by {artist_name}."
        return FeatureResult(
            ok=True, data={"track": track_name, "artist": artist_name, "uri": track["uri"]},
            display=msg, spoken=msg,
        )
    except Exception as e:
        return _handle_spotify_error(e)


def resume_playback() -> FeatureResult:
    """Bare 'play'/'resume'/'unpause' — continues whatever was last
    loaded, or 'open spotify and play' with nothing specific queued."""
    sp, err = _require_client()
    if err:
        return err

    try:
        device_id = _get_active_device_id(sp)
        if device_id is None:
            msg = _no_device_message()
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_device")

        sp.start_playback(device_id=device_id)
        msg = "Resumed."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    except Exception as e:
        return _handle_spotify_error(e)


def pause_playback() -> FeatureResult:
    sp, err = _require_client()
    if err:
        return err

    try:
        sp.pause_playback()
        msg = "Paused."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    except Exception as e:
        return _handle_spotify_error(e)


def next_track() -> FeatureResult:
    sp, err = _require_client()
    if err:
        return err

    try:
        sp.next_track()
        msg = "Skipped."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    except Exception as e:
        return _handle_spotify_error(e)


def previous_track() -> FeatureResult:
    sp, err = _require_client()
    if err:
        return err

    try:
        sp.previous_track()
        msg = "Back a track."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    except Exception as e:
        return _handle_spotify_error(e)


def set_volume(level: int) -> FeatureResult:
    sp, err = _require_client()
    if err:
        return err

    level = max(0, min(100, level))
    try:
        sp.volume(level)
        msg = f"Volume set to {level}%."
        return FeatureResult(ok=True, data={"volume": level}, display=msg, spoken=msg)
    except Exception as e:
        return _handle_spotify_error(e)


def adjust_volume(delta: int) -> FeatureResult:
    """Relative volume change ('turn spotify up/down') — reads current
    volume from the active device first since the API only accepts an
    absolute value."""
    sp, err = _require_client()
    if err:
        return err

    try:
        devices = sp.devices().get("devices", [])
        active = next((d for d in devices if d.get("is_active")), devices[0] if devices else None)
        if active is None:
            msg = _no_device_message()
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_device")

        current = active.get("volume_percent") or 50
        new_level = max(0, min(100, current + delta))
        sp.volume(new_level)
        msg = f"Volume {'up' if delta > 0 else 'down'} to {new_level}%."
        return FeatureResult(ok=True, data={"volume": new_level}, display=msg, spoken=msg)
    except Exception as e:
        return _handle_spotify_error(e)


def get_now_playing() -> FeatureResult:
    """'what's playing' — the one thing that still works on a free
    account (read-only)."""
    sp, err = _require_client()
    if err:
        return err

    try:
        current = sp.current_playback()
        if not current or not current.get("item"):
            msg = "Nothing's playing on Spotify right now."
            return FeatureResult(ok=True, data={}, display=msg, spoken=msg)

        item = current["item"]
        track_name = item["name"]
        artist_name = ", ".join(a["name"] for a in item["artists"])
        is_playing = current.get("is_playing", False)
        status = "Playing" if is_playing else "Paused"
        msg = f"{status}: {track_name} by {artist_name}."
        return FeatureResult(
            ok=True, data={"track": track_name, "artist": artist_name, "is_playing": is_playing},
            display=msg, spoken=msg,
        )
    except Exception as e:
        return _handle_spotify_error(e)


def open_and_play(query: str = "") -> FeatureResult:
    """
    'open spotify and play' / 'open spotify and play <song>' — tries the
    desktop app first (so there's actually a device for the Web API to
    target a moment later), then falls through to the same search+play
    or resume logic.
    """
    from features.pc_control import open_app
    import time as _time

    open_app("spotify")
    _time.sleep(3)  # give the desktop app a moment to register as a device

    if query.strip():
        return play_song(query)
    return resume_playback()
