"""
core/spotify_intent.py — Groq-powered song-query cleanup for Spotify's
"play <song>" action.

core/router.py's detect_spotify_intent is a cheap, fast regex pre-filter
that ALSO extracts a candidate query — necessary at that layer just to
tell "this is a Spotify request" apart from PC control ("open spotify
and play X" contains "open", a PC_KEYWORD) without a Groq round-trip on
every single utterance. But that regex extraction is naive: it strips
only a couple of fixed prefixes/suffixes ("play ", "on spotify") and
keeps everything else verbatim, so "play X song in spotify" or "put on
some X" or "can you play X for me" all leak filler words straight into
the Spotify search query, returning the wrong track. Stage 2's Groq
classifier (when the regex misses a phrasing entirely) has the same
"copy it as said" instruction and doesn't reliably strip filler either.

This module is the one place both paths funnel through before the query
ever reaches features/spotify.py's search: a single small, fast Groq
call that understands arbitrary phrasing and returns just the song
(and artist, if named), not the surrounding sentence.
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import complete

EXTRACTION_PROMPT = """Someone asked a voice assistant to play a song on Spotify. Extract ONLY the song name (and artist, if one was named), stripping filler words like "song", "track", "some", "please", "for me", "can you", "in/on spotify", "in the background", etc.

Respond with ONLY the cleaned song query — no quotes, no explanation, no extra words. If literally nothing meaningful remains after stripping filler, respond with the original text unchanged.

Examples:
Input: "aaluma doluma song in spotify"
Output: aaluma doluma

Input: "some kannazhaga"
Output: kannazhaga

Input: "believer by imagine dragons"
Output: believer by imagine dragons

Input: "vaathi coming for me please"
Output: vaathi coming

Input: "can you play rowdy baby"
Output: rowdy baby

Now clean this:
Input: "{raw_query}"
Output:"""


def clean_song_query(raw_query: str) -> str:
    """Returns a cleaned song/artist query, or the original text unchanged
    if cleanup fails for any reason — never raises, never returns empty
    when the input wasn't empty."""
    raw_query = (raw_query or "").strip()
    if not raw_query:
        return raw_query

    try:
        prompt = EXTRACTION_PROMPT.replace("{raw_query}", raw_query)
        cleaned = complete([{"role": "user", "content": prompt}], role="classifier",
                           max_tokens=40, temperature=0)
        cleaned = cleaned.splitlines()[0].strip().strip('"').strip("'")
        return cleaned if cleaned else raw_query
    except Exception as e:
        print(f"[spotify_intent] query cleanup error: {e}")
        return raw_query
