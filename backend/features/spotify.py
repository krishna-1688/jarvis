"""
features/spotify.py — Spotify playback control, two layers:

  1. Web API (spotipy) — used if backend/.env has real
     SPOTIFY_CLIENT_ID/SECRET. Gives precise search-and-play, exact
     volume, accurate now-playing. Playback CONTROL (play/pause/skip/
     volume) needs Spotify PREMIUM on this layer — Spotify's own
     restriction, the Web API 403s "Premium required" for free accounts.

  2. OS-level fallback (no setup, no Premium needed) — used automatically
     whenever the API isn't configured. The author didn't have Premium, so this
     is the layer actually in use:
       - pause/resume/next/previous/now-playing go through Windows'
         System Media Transport Controls (SMTC, via the `winsdk`
         package) — this is the same OS-level media-session layer media
         keys use, works with ANY app that publishes one (Spotify does),
         and is NOT gated by Premium since it never touches Spotify's
         own Web API at all.
       - volume up/down/set goes through pycaw's per-session volume
         (AudioUtilities.GetAllSessions()) — adjusts Spotify's own
         session volume specifically, not the whole system.
       - play_song (search-and-play a specific track) is the one thing
         SMTC genuinely can't do — there's no "search" concept at the
         OS media-session level. This one drives the real Spotify
         desktop window: focuses it (verified, see _force_foreground),
         opens its search box (Ctrl+L), types the query, and selects
         the first actual song result. Empirically verified stable:
         Spotify's search dropdown always shows exactly 4 text-query
         suggestions before real results start, regardless of query.

ONE-TIME SETUP for the Web API layer (optional — only needed for exact
search-and-play precision and only usable at all with Premium):
  1. Create an app at https://developer.spotify.com/dashboard
  2. On that app, add this exact Redirect URI: http://127.0.0.1:8888/callback
     (must match config.SPOTIFY_REDIRECT_URI exactly)
  3. Put the app's Client ID/Secret into backend/.env:
       SPOTIFY_CLIENT_ID=...
       SPOTIFY_CLIENT_SECRET=...
  4. The first real command opens a browser for one-time Spotify login +
     consent. After that, spotipy caches a refresh token to
     data/.spotify_cache and never asks again.
"""

import os
import sys
import time
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
#   OS-LEVEL FALLBACK (no API credentials, no Premium needed)
# ══════════════════════════════════════════════════════════
# Used automatically by every public function below whenever the Web API
# client isn't configured — see the module docstring for why this exists
# and how it's split (SMTC for transport, pycaw for volume, window
# automation only for the one thing SMTC can't do: search-and-play).

def _run_async(coro):
    import asyncio
    return asyncio.run(coro)


def _ensure_com():
    """COM must be explicitly initialized on whatever thread first touches
    it — confirmed live via a real crash through the running server:
    FastAPI/Starlette runs sync route handlers on a worker-thread pool,
    and pywinauto's UIA backend calls comtypes.CoCreateInstance directly
    (not through comtypes' auto-initializing client.CreateObject wrapper)
    the first time it's imported on a given thread, raising "CoInitialize
    has not been called" if that thread never called it. Safe to call
    repeatedly — comtypes.CoInitialize() raising on an already-initialized
    thread just means there's nothing to do."""
    import comtypes
    try:
        comtypes.CoInitialize()
    except (OSError, comtypes.COMError):
        pass


async def _smtc_session():
    from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
    mgr = await MediaManager.request_async()
    return mgr.get_current_session()


def _get_smtc_session():
    """Returns the active OS media session, or None if nothing (Spotify
    or otherwise) currently has one — e.g. Spotify isn't open, or is open
    but has never played anything this run."""
    try:
        return _run_async(_smtc_session())
    except Exception as e:
        print(f"[spotify] SMTC unavailable: {e}")
        return None


async def _smtc_properties(session):
    return await session.try_get_media_properties_async()


def _spotify_pids():
    import psutil
    return {p.pid for p in psutil.process_iter(['name'])
            if p.info['name'] and p.info['name'].lower() == 'spotify.exe'}


def _find_spotify_hwnd():
    """The Spotify desktop app's main visible window, found by owning
    process rather than by title — the title itself isn't stable, it
    changes to the currently-playing "Artist - Track" while music
    plays, which broke a naive exact-title match during testing."""
    import win32gui
    import win32process
    pids = _spotify_pids()
    if not pids:
        return None
    result = []
    def _enum(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).strip():
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pids:
                result.append(hwnd)
    win32gui.EnumWindows(_enum, None)
    return result[0] if result else None


def _force_foreground(hwnd) -> bool:
    """Brings hwnd to the foreground and VERIFIES it actually worked
    before returning True. Windows' foreground-lock can silently ignore
    a plain SetForegroundWindow call from a background process —
    confirmed live during development: without the AttachThreadInput
    trick and this verification, a focus-steal attempt silently failed
    and the follow-up keystrokes (meant for Spotify's search box) went
    to whatever window actually had focus instead. Callers MUST check
    the return value and abort rather than send keystrokes on a False."""
    import win32gui
    import win32process
    import win32api
    import win32con

    fg = win32gui.GetForegroundWindow()
    try:
        fg_thread, _ = win32process.GetWindowThreadProcessId(fg)
        cur_thread = win32api.GetCurrentThreadId()
        win32process.AttachThreadInput(cur_thread, fg_thread, True)
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            win32process.AttachThreadInput(cur_thread, fg_thread, False)
    except Exception as e:
        print(f"[spotify] focus-steal failed: {e}")
        return False
    return win32gui.GetForegroundWindow() == hwnd


def _spotify_session_volume():
    """The pycaw ISimpleAudioVolume for Spotify's own audio session, or
    None if Spotify isn't currently producing audio (e.g. just opened,
    hasn't played anything yet — Windows doesn't create an audio session
    until a process actually outputs sound)."""
    _ensure_com()
    from pycaw.pycaw import AudioUtilities
    for s in AudioUtilities.GetAllSessions():
        if s.Process and s.Process.name().lower() == "spotify.exe":
            return s.SimpleAudioVolume
    return None


def _ui_ensure_spotify_open(timeout: float = 12.0, on_progress=None):
    """Launches the Spotify desktop app if it isn't already running, and
    waits for its window to actually appear. Returns the hwnd or None."""
    hwnd = _find_spotify_hwnd()
    if hwnd:
        return hwnd
    if on_progress:
        on_progress("Spotify's not open — launching it now, boss.")
    from features.pc_control import open_app
    open_app("spotify")
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = _find_spotify_hwnd()
        if hwnd:
            return hwnd
        time.sleep(0.5)
    return None


_NO_WINDOW_MSG = "Couldn't open Spotify, boss — is it installed?"
_NO_FOCUS_MSG = ("Found Spotify but couldn't bring it to the front — "
                  "click on it once and ask again.")


def _find_play_button(hwnd, timeout: float = 5.0):
    """The first 'Play <song name>' button that's an actual SEARCH RESULT,
    found via Windows UI Automation (pywinauto) rather than guessed
    keyboard shortcuts. Keyboard navigation was tried first and rejected
    — confirmed live that pressing Enter (or Space) on a dropdown row
    highlighted via arrow keys just navigates to that track's ALBUM page
    instead of starting playback.

    CONFIRMED PRODUCTION BUG #1 (not caught during dev testing): Spotify's
    search dropdown overlays the Home page rather than replacing it, so
    ~100+ unrelated "Play <name>" buttons — Liked Songs, Daily Mix 1-6,
    Discover Weekly, whatever's in the sidebar — coexist in the same
    window's accessibility tree at the same time as the real results.
    The original version of this function returned the FIRST "Play "
    match found by plain text, with no way to tell a real result apart
    from Home-page clutter; in real usage it consistently grabbed "Play
    Liked Songs" instead — every "play X" request played the same song
    regardless of X. Fix: every real search-result button's immediate
    parent has a generated automation_id ending in "-play" (e.g.
    ":rj3:-play"); every Home-page button's parent has an empty one.

    CONFIRMED PRODUCTION BUG #2, found after fixing #1: search results
    stream in progressively, NOT in relevance order as they render — a
    generic playlist match can render before the actual top song match
    arrives a few hundred ms later. Scanning once and taking whatever's
    first at that instant sometimes caught a partial, still-loading
    state and locked in the wrong result even with the automation_id
    filter correctly applied. Fix: don't accept the first candidate
    until the SAME one shows up first on two consecutive scans — that's
    the actual signal that the list has stopped shuffling."""
    _ensure_com()
    from pywinauto import Application
    deadline = time.time() + timeout
    previous_name = None
    last_seen = None
    while time.time() < deadline:
        try:
            app = Application(backend='uia').connect(handle=hwnd)
            win = app.window(handle=hwnd)
            current = None
            for b in win.descendants(control_type='Button'):
                try:
                    name = b.window_text()
                except Exception:
                    continue
                if not name.startswith('Play '):
                    continue
                try:
                    parent_aid = b.parent().element_info.automation_id
                except Exception:
                    parent_aid = ''
                if parent_aid.endswith('-play'):
                    current = (name, b)
                    break
            if current:
                name, b = current
                if name == previous_name:
                    return b
                previous_name = name
                last_seen = b
        except Exception as e:
            print(f"[spotify] UIA scan error: {e}")
        time.sleep(0.4)
    # Never stabilized within the timeout — the last thing seen beats
    # nothing, but this path means something's slow/unusual and is worth
    # knowing about if it comes up.
    if last_seen:
        print(f"[spotify] search results never stabilized within {timeout}s — using last-seen match {previous_name!r}")
    return last_seen


def _read_search_box_text(hwnd) -> str:
    _ensure_com()
    from pywinauto import Application
    app = Application(backend='uia').connect(handle=hwnd)
    win = app.window(handle=hwnd)
    boxes = win.descendants(control_type='ComboBox')
    return boxes[0].window_text() if boxes else ''


def _type_into_search_box(hwnd, query: str, attempts: int = 3) -> bool:
    """Focuses Spotify's search box, clears it, and types query — then
    reads the box back to confirm the keystrokes actually landed before
    moving on. CONFIRMED PRODUCTION BUG: on repeated back-to-back
    play_song calls, Ctrl+L/Ctrl+A/typewrite would occasionally have no
    effect at all — the search box silently kept showing the PREVIOUS
    query, and _find_play_button then (correctly, per its own fix) found
    a real search-result button — just for the WRONG, stale query. Every
    "play X" after the first one played whatever the first one played.
    Retrying the whole focus+clear+type sequence when the read-back
    doesn't match catches this regardless of why Windows dropped the
    keystrokes that time (a repeated-SetForegroundWindow timing quirk,
    most likely — never fully pinned down, but reading back and retrying
    doesn't need to know why to fix it)."""
    import pyautogui

    for attempt in range(attempts):
        if attempt > 0 and not _force_foreground(hwnd):
            continue
        time.sleep(0.4)
        pyautogui.hotkey('ctrl', 'l')
        time.sleep(0.4)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.15)
        pyautogui.typewrite(query, interval=0.02)
        time.sleep(1.5)  # let search results render before reading back / hunting for the Play button
        try:
            current = _read_search_box_text(hwnd)
        except Exception as e:
            print(f"[spotify] search box read-back error: {e}")
            current = ''
        if current.strip().lower() == query.strip().lower():
            return True
        print(f"[spotify] search box still showed {current!r} after typing {query!r} (attempt {attempt + 1}/{attempts}) — retrying")
    return False


def _restore_and_hide(hwnd, original_fg) -> None:
    """Puts the window the user was actually looking at back in front, and
    tucks Spotify's window back out of view — used after the search-and-
    play automation below, which unavoidably needs Spotify focused for a
    couple of seconds to type into its search box (a real OS-level
    keystroke is the only thing that triggers Spotify's own live search;
    confirmed live that setting the search box's value directly via UI
    Automation, without real keystrokes, doesn't update results at all).
    the user doesn't want to watch that happen — this makes it a brief flash
    rather than something that sits in front of him afterward."""
    import win32con
    import win32gui
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    except Exception as e:
        print(f"[spotify] couldn't minimize after play: {e}")
    if original_fg and original_fg != hwnd:
        try:
            win32gui.SetForegroundWindow(original_fg)
        except Exception as e:
            print(f"[spotify] couldn't restore previous window: {e}")


def ui_play_song(query: str, on_progress=None) -> FeatureResult:
    """Search-and-play fallback with no API/Premium requirement — drives
    the real desktop app's own search box, then clicks the actual
    accessible Play button for the top result (see _find_play_button).
    Restores whatever the user was looking at before and minimizes Spotify
    again afterward (see _restore_and_hide) — he doesn't want to watch
    the automation happen, just get music playing in the background.
    on_progress, if given, is called with short status strings at each
    step — asked for specifically: the automation takes a few
    seconds, and he wants to know what's happening during that window
    rather than just staring at a silent "thinking" indicator."""
    import win32gui

    def _progress(msg):
        if on_progress:
            on_progress(msg)

    query = (query or "").strip()
    if not query:
        return ui_resume()

    original_fg = win32gui.GetForegroundWindow()

    hwnd = _ui_ensure_spotify_open(on_progress=on_progress)
    if not hwnd:
        return FeatureResult(ok=False, data={}, display=_NO_WINDOW_MSG, spoken=_NO_WINDOW_MSG, error="no_window")

    if not _force_foreground(hwnd):
        return FeatureResult(ok=False, data={}, display=_NO_FOCUS_MSG, spoken=_NO_FOCUS_MSG, error="no_focus")

    _progress(f"Searching Spotify for '{query}'...")
    if not _type_into_search_box(hwnd, query):
        _restore_and_hide(hwnd, original_fg)
        msg = f'Couldn\'t find "{query}" on Spotify.'
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")

    button = _find_play_button(hwnd)
    if not button:
        _restore_and_hide(hwnd, original_fg)
        msg = f'Couldn\'t find "{query}" on Spotify.'
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")

    _progress("Found it — starting playback...")
    button.click_input()
    time.sleep(1.5)
    _restore_and_hide(hwnd, original_fg)

    # Free-account ad interstitials show up as a real SMTC session with
    # artist "Spotify" — retrying briefly rather than reporting the ad
    # itself as the "now playing" result of this click.
    for attempt in range(3):
        session = _get_smtc_session()
        if not session:
            break
        try:
            props = _run_async(_smtc_properties(session))
            pb = session.get_playback_info()
            is_playing = int(pb.playback_status) == 4  # Playing
            if is_playing and props.title and (props.artist or "").strip().lower() != "spotify":
                msg = f"Playing {props.title} by {props.artist}."
                return FeatureResult(ok=True, data={"track": props.title, "artist": props.artist}, display=msg, spoken=msg)
        except Exception as e:
            print(f"[spotify] post-play verification error: {e}")
        if attempt < 2:
            time.sleep(1.5)

    msg = f"Told Spotify to play '{query}' but couldn't confirm it started."
    return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="unconfirmed")


def _ui_transport(action: str, verb: str) -> FeatureResult:
    """Shared body for pause/resume/next/previous — all identical except
    which SMTC method they call and what they say on success."""
    hwnd = _ui_ensure_spotify_open()
    if not hwnd:
        return FeatureResult(ok=False, data={}, display=_NO_WINDOW_MSG, spoken=_NO_WINDOW_MSG, error="no_window")

    session = _get_smtc_session()
    if not session:
        msg = "Spotify hasn't played anything yet this session — play a song first."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_session")

    async def _call():
        # winsdk's try_*_async methods return an IAsyncOperation, not a
        # native coroutine — asyncio.run() needs an actual coroutine, so
        # this thin async wrapper is required even though it looks like
        # a no-op. Confirmed live: calling _run_async(method()) directly
        # (without this wrapper) raised "a coroutine was expected, got
        # <IAsyncOperation ...>" for every transport action.
        return await getattr(session, action)()

    try:
        ok = _run_async(_call())
        if not ok:
            msg = f"Spotify didn't accept that — try again in a bit."
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="rejected")
        msg = f"{verb}."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    except Exception as e:
        print(f"[spotify] SMTC {action} error: {e}")
        msg = "Spotify didn't cooperate there — try again in a bit."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="spotify_error")


def ui_pause() -> FeatureResult:
    return _ui_transport("try_pause_async", "Paused")


def ui_resume() -> FeatureResult:
    return _ui_transport("try_play_async", "Resumed")


def ui_next() -> FeatureResult:
    return _ui_transport("try_skip_next_async", "Skipped")


def ui_previous() -> FeatureResult:
    return _ui_transport("try_skip_previous_async", "Back a track")


def ui_now_playing() -> FeatureResult:
    # Right after a skip/previous, SMTC can briefly report a session with
    # blank title/artist before the new track's metadata finishes loading
    # — confirmed live. A couple of short retries covers that transition
    # instead of ever saying "Playing: by .".
    for attempt in range(3):
        session = _get_smtc_session()
        if not session:
            msg = "Nothing's playing on Spotify right now."
            return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
        try:
            props = _run_async(_smtc_properties(session))
            pb = session.get_playback_info()
            is_playing = int(pb.playback_status) == 4
            if not (props.title or "").strip():
                if attempt < 2:
                    time.sleep(1.0)
                    continue
                msg = "Nothing's playing on Spotify right now."
                return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
            if (props.artist or "").strip().lower() == "spotify":
                msg = "An ad's playing on Spotify right now."
                return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
            status = "Playing" if is_playing else "Paused"
            msg = f"{status}: {props.title} by {props.artist}."
            return FeatureResult(ok=True, data={"track": props.title, "artist": props.artist, "is_playing": is_playing},
                                  display=msg, spoken=msg)
        except Exception as e:
            print(f"[spotify] now-playing error: {e}")
            msg = "Couldn't read what's playing on Spotify."
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="spotify_error")


def ui_set_volume(level: int) -> FeatureResult:
    vol = _spotify_session_volume()
    if vol is None:
        msg = "Spotify isn't playing anything yet, so there's no volume to set."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_session")
    level = max(0, min(100, level))
    vol.SetMasterVolume(level / 100.0, None)
    msg = f"Spotify volume set to {level}%."
    return FeatureResult(ok=True, data={"volume": level}, display=msg, spoken=msg)


def ui_adjust_volume(delta: int) -> FeatureResult:
    vol = _spotify_session_volume()
    if vol is None:
        msg = "Spotify isn't playing anything yet, so there's no volume to adjust."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_session")
    current = round(vol.GetMasterVolume() * 100)
    new_level = max(0, min(100, current + delta))
    vol.SetMasterVolume(new_level / 100.0, None)
    msg = f"Spotify volume {'up' if delta > 0 else 'down'} to {new_level}%."
    return FeatureResult(ok=True, data={"volume": new_level}, display=msg, spoken=msg)


# ══════════════════════════════════════════════════════════
#   PUBLIC FEATURE-RESULT API
# ══════════════════════════════════════════════════════════

def play_song(query: str, on_progress=None) -> FeatureResult:
    """'play <song>' / 'play <song> by <artist>' — searches Spotify and
    starts playback on whatever device is open."""
    sp, err = _require_client()
    if err:
        return ui_play_song(query, on_progress=on_progress)

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
        return ui_resume()

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
        return ui_pause()

    try:
        sp.pause_playback()
        msg = "Paused."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    except Exception as e:
        return _handle_spotify_error(e)


def next_track() -> FeatureResult:
    sp, err = _require_client()
    if err:
        return ui_next()

    try:
        sp.next_track()
        msg = "Skipped."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    except Exception as e:
        return _handle_spotify_error(e)


def previous_track() -> FeatureResult:
    sp, err = _require_client()
    if err:
        return ui_previous()

    try:
        sp.previous_track()
        msg = "Back a track."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    except Exception as e:
        return _handle_spotify_error(e)


def set_volume(level: int) -> FeatureResult:
    sp, err = _require_client()
    if err:
        return ui_set_volume(level)

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
        return ui_adjust_volume(delta)

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
        return ui_now_playing()

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


def open_and_play(query: str = "", on_progress=None) -> FeatureResult:
    """
    'open spotify and play' / 'open spotify and play <song>' — tries the
    desktop app first (so there's actually a device for the Web API to
    target a moment later), then falls through to the same search+play
    or resume logic.
    """
    from features.pc_control import open_app
    import time as _time

    if on_progress and not _find_spotify_hwnd():
        on_progress("Opening Spotify...")
    open_app("spotify")
    _time.sleep(3)  # give the desktop app a moment to register as a device

    if query.strip():
        return play_song(query, on_progress=on_progress)
    return resume_playback()
