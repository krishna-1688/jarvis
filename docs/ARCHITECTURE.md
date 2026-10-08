# Architecture

Jarvis is a single-user assistant that runs entirely on one Windows laptop. Only the AI calls, speech synthesis and VIT's own portals go over the network. This doc covers how the pieces fit and why they're built the way they are. Numbers come from measurements recorded in the module docstrings. Where a number came from one laptop, it says so.

## Processes

```
run.py  (supervisor: restarts crashed children with backoff, single-instance lock,
   │     keeps the laptop awake only while plugged in, Ctrl+J opens the dashboard)
   ├── server.py   FastAPI on 127.0.0.1:8000: the one "brain"
   │                 POST /command · GET/PUT /profile · data endpoints · WS /stream
   ├── jarvis.py   voice loop: mic → wake word → record → transcribe → POST /command → speak
   └── dashboard   Electron + React (frontend/), opened on demand, talks to :8000
whatsapp_service/  optional Node service (whatsapp-web.js): morning brief, reminders,
                   UPI payment messages → expenses
```

The voice loop and the dashboard are both just clients of `server.py`, so a typed command and a spoken one take exactly the same path.

## A request, end to end

1. **Mic** (`core/mic.py`): one 16 kHz stream stays open for the life of the process, shared by wake detection and recording. It's reopened with backoff if a headset is unplugged or the laptop resumes from sleep. Opening and closing the mic on every turn was the most common way the mic silently died over a long day.
2. **Wake word** (`core/wakeword.py`): openWakeWord's `hey_jarvis` model runs **only while webrtcvad hears speech**. The model costs about 8% of a core while it runs; the VAD check costs microseconds. About 1 s of pre-roll is replayed so the model always hears the whole phrase. The threshold is 0.3, not the default 0.5, because Indian-English “Hey, Jarvis!” with a pause scored 0.30–0.48. Benchmark: 63 clips in 8 voices, 28 near-miss negatives, 4 loudness levels. Detection was 93–100%, even at 3% loudness.
3. **Instant acknowledgement:** the wake reply (“Yes boss?”) is synthesised once and replayed from a disk cache. Uncached, edge-tts took 1.4–2.8 s to return it.
4. **Speech to text:** Groq Whisper, with a vocabulary hint built from the profile (your name, plus VIT terms like CAT1, FAT and DAA only if VTOP is connected). Filters drop common hallucinations and non-English noise.
5. **Routing** (`core/router.py`), meaning first and keywords last:
   - *Reflex stage:* phrases that leave nothing to interpret (“what's my attendance”, “open YouTube”, Spotify transport, safety rules) answer instantly without a model call.
   - *Classifier stage:* everything else goes to an AI classifier. It returns the intent and the fields the feature needs (task title and due date, course, merchant) under a **5 s budget**. Intents for integrations you haven't connected are hidden from it.
   - *Offline fallback:* the old keyword rules. They're used only when every model is unreachable, and tightened so that a wrong guess falls to plain chat rather than pulling the wrong data.
   - Why: on an 81-request eval plus 20 held-out paraphrases, keyword-first routing answered “mark my words” with exam marks and “add buy milk to my to-do list” with a task list.
6. **Feature handlers** (`backend/features/`) return a `FeatureResult` with `display` text, a shorter `spoken` text and structured `data` for the dashboard.
7. **Reply:** `core/profile.personalize()` swaps the “boss” address for whatever the user chose, only where it's used as a form of address. The reply is then spoken with edge-tts, falling back to the built-in Windows voice when offline, and pushed to the dashboard over the `/stream` WebSocket.

## AI calls (`core/llm.py`)

Every text, vision and transcription call goes through one gateway that walks an ordered **model chain**. Groq models come first; `gemini:` entries go to Google's separate free quota.

- A rate-limited model is skipped until its `retry-after` passes.
- A retired or unknown model is skipped for an hour.
- Callers get text back or `LLMUnavailable`, never a half-parsed error.

This matters on the free tier: each Groq model has its own per-minute and per-day token limits, so a busy day moves across models instead of failing. Chains can be overridden in `.env` (`GROQ_CHAT_MODELS`, …).

## Personalisation (`core/profile.py`)

Everything about the user lives in `backend/profile.toml`. It's written by `setup.py` or the dashboard's **You** panel, and it's git-ignored. The file is re-read when its modification time changes, so edits apply on the next reply. From it:

- `persona_prompt()` builds the personal part of the system prompt: who they are, their tone and reply-length rules, language, their dream and ambitions, their routine and favourite things.
- `wake_reply()` and `personalize()` handle how Jarvis addresses them out loud.
- `save()` whitelists sections and keys, so a client can't write arbitrary data into the file.

The VIT-specific prompt rules are appended only when VTOP is connected.

## VIT integration

- **VTOP** (`features/vtop_handler/`, adapted from [VTOP_API](https://github.com/Sarath191181208/VTOP_API), MIT):
  - aiohttp login; the captcha is read by the vision model chain
  - HTML parsers for attendance, timetable, marks, grade history and exam schedule
  - scheduled syncs into SQLite: timetable 5 AM, full sync 6 AM, attendance 7 AM and 6 PM, grades Sundays
  - questions are answered from the local copy; VTOP is contacted only on a schedule or when you say “refresh my marks”
- **Semesters** (`core/semesters.py`): semester IDs are computed instead of hardcoded, as campus prefix + academic year + term (Fall runs Jul–Dec, Winter Jan–Jun). The admission year comes from the registration number, and the current semester rolls over automatically. `college.current_sem` in the profile can pin it.
- **Courses** (`core/course_resolver.py`): fuzzy matching turns “daa” or “the compiler lab” into the real course code. It matches against aliases learned from every sync.
- **LMS** (`features/lms_handler/`): Moodle login and the upcoming-events calendar. It checks hourly for deadline reminders.
- **Optional by design:** with no VTOP or LMS credentials, those workers never start, those intents never reach the classifier, and a direct request gets setup instructions instead of an error.

## Memory (`core/graph.py`)

Associative memory with no AI calls of its own:

- Each conversation turn becomes an *event* linked to the *entities* it mentions: courses, exams, people, tasks, topics, facts you asked it to remember.
- Entities that come up together get a link that strengthens with each co-occurrence (w += rate · (1 − w)) and decays with a half-life, computed at read time.
- Recall spreads activation two hops out through the strongest fresh links.
- Writes happen on one background thread, into its own SQLite file. Any failure degrades to “no extra context”, never to a broken reply.

## Data on disk (all git-ignored)

| Path | What |
|---|---|
| `backend/.env` | API keys, VTOP/LMS credentials |
| `backend/profile.toml` | The user's profile |
| `backend/data/database/` | SQLite: synced VTOP/LMS data, tasks, schedule, focus sessions, expenses, memory graph |
| `backend/data/tts_cache/` | Cached audio for fixed phrases |

## Dashboard (`frontend/`)

React 19 + Vite, packaged with Electron. `orbit/store.js` is a small polling cache: each data source has its own refresh interval, and the server can push a refresh over `/stream`. Lenses show attendance, exams, assignments, tasks, focus, money, memory and the **You** profile editor (Alt+1…9).

## Platform

Windows 10/11 only for now. PC control uses pywin32, pywinauto and pycaw, power management uses Win32 APIs, and the offline voice is SAPI. The rest (server, routing, VTOP, memory) is portable. A Linux/macOS port mostly means swapping `features/pc_control.py` and `core/power.py`.
