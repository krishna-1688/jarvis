<div align="center">

# J.A.R.V.I.S

**A voice assistant for VIT Chennai students that runs on your own laptop.**

Say *“Hey Jarvis, can I bunk DAA tomorrow?”* and get a real answer from your own VTOP attendance.

Free to run · your data stays on your laptop · Windows 10/11

<!-- Add a 20–30 s screen recording here: docs/demo.gif -->

</div>

---

## Why this exists

VTOP answers every question in seven clicks and a captcha. Jarvis answers it in one sentence:

| You say | Jarvis |
|---|---|
| “What's my attendance?” | Overall and per-course %, and which course is closest to 75% |
| “Can I skip DAA tomorrow?” | Whether one more absence keeps you above 75%, and how many you can afford |
| “How many CN classes do I need to attend?” | The exact number of classes to get back above 75% |
| “When's my next CAT?” | The next exam, date, time and venue, with days left |
| “What do I need in the FAT to get an A in OS lab?” | The mark you need, from your internal marks so far (lab courses; theory courses are graded on a curve, and Jarvis says so rather than guess) |
| “What will my CGPA be if I get an S in DBMS?” | Your projected CGPA |
| “What's my next class?” / “timetable tomorrow” | From your VTOP timetable, with free slots |
| “Any assignments due?” | Pending LMS (Moodle) assignments, soonest first |

And the everyday stuff, for anyone (VIT or not):

- **Tasks and reminders:** “remind me to pay hostel fees Friday”, “what's due today”, “mark the lab record as done”
- **Plan your day:** “block 8 to 10 for DSA”, “when am I free tomorrow”, “what's next”
- **Focus mode:** “start focus on DAA for 25 minutes” mutes distractions and logs your study time
- **Money:** “spent 120 on lunch”, “how much did I spend on Swiggy this month”. It can also log UPI payments from bank messages on WhatsApp.
- **Your PC:** volume, brightness, screenshots, open or close apps, battery and CPU, “what's on my screen?” (it reads and explains it)
- **Web and music:** “open YouTube”, “search Amazon for earphones”, “play lo-fi on Spotify”
- **Memory:** “remember that my lab is in AB2”. Jarvis links what you tell it into a memory map you can explore.
- **Just talk:** explanations, CS doubts, advice, motivation, in the tone you pick

## Make it yours

Jarvis isn't one fixed personality. During setup, or later from the **You** panel on the dashboard (Alt+9), you choose:

- **What it calls you:** “boss”, “sir”, your name, or nothing
- **Tone:** witty, like Iron Man's Jarvis · friendly · formal · tough-love (calls out procrastination) · calm
- **Reply length and language:** “English with some Tamil” or Hinglish work
- **Voice:** British, Indian or American, male or female
- **Your dream, ambitions and what drives you:** advice and motivation get tied to these
- **Your routine, habits and favourite things**

Changes apply from the next reply, with no restart.

## Quick start (about 10 minutes)

**You need:** Windows 10/11, about 1.5 GB of free disk space, a microphone, [Python 3.12](https://www.python.org/downloads/release/python-31210/) or 3.11 (tick *Add python.exe to PATH*; 3.13 and newer don't work yet), [Node.js LTS](https://nodejs.org/) for the dashboard, and a free [Groq API key](https://console.groq.com/keys). No card needed.

1. **Download:** click **Code → Download ZIP** above and unzip it. Or:
   ```
   git clone https://github.com/krishna-1688/jarvis.git
   ```
2. **Set up:** double-click **`setup.bat`**. It installs everything, then asks for:
   - your Groq key (it checks the key right away)
   - optionally, your VTOP username and password (your reg number or the custom username you log in with), and your LMS login
   - your name, tone, voice, goals and routine
   - a quick “Hey Jarvis” microphone test
3. **Run:** double-click **`start.bat`**. When the console shows *“Standby — say 'Hey Jarvis'”*, say it.

Press **Ctrl+J** any time for the dashboard. You can also type there instead of speaking.

Need to change something later? Run `python setup.py college`, `profile`, `keys` or `mic` to redo just that part.

<details>
<summary><b>Not from VIT Chennai?</b></summary>

- **Other VIT students:** VIT Vellore is supported but **experimental**. Pick it during setup and please [open an issue](https://github.com/krishna-1688/jarvis/issues) with what works. VIT-AP and Bhopal use different portals and aren't supported yet. PRs welcome.
- **Not a VIT student:** answer “no” to the college question. The VIT features stay hidden and everything else works the same.

</details>

## Your data stays on your laptop

- Your VTOP and LMS passwords are stored only in `backend/.env` on your laptop, and are sent only to VIT's own servers.
- Your profile, memories, tasks, expenses and synced college data live in `backend/profile.toml` and a local SQLite database. All of these are git-ignored.
- **What does leave your laptop:** the text of what you say is sent to Groq's AI (and Google Gemini, if you add a backup key) to understand and answer it. The VTOP captcha image is read by the same AI. Spoken replies are made with Microsoft Edge's online text-to-speech voice. There are no analytics, accounts or servers of mine.
- Jarvis syncs VTOP a few times a day, no more than a student opening it themselves.

## How it works

```
 "Hey Jarvis" ─► wake word (on-device) ─► speech-to-text (Groq Whisper)
                                                    │
                    ┌───────────────────────────────┘
                    ▼
      understand the request (AI classifier ─► keyword fallback when offline)
                    │
      ┌─────────────┼──────────────┬──────────────┬─────────────┐
      ▼             ▼              ▼              ▼             ▼
   VTOP / LMS    tasks,        PC control,    memory       chat (AI with
   (local sync)  schedule,     web, Spotify   graph        your profile)
                 focus, money
                    │
                    ▼
      reply ─► spoken (Edge neural voice) + shown on the dashboard
```

Everything runs as three local processes: a FastAPI server, the voice loop, and an Electron dashboard. A supervisor restarts any of them if it crashes. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

## Troubleshooting

| Problem | Fix |
|---|---|
| It doesn't wake up | Run `python setup.py mic` and watch the score while you say “Hey Jarvis”. If it stays low, add `JARVIS_WAKE_THRESHOLD=0.2` to `backend/.env`. |
| “Give me a sec…” / slow answers | Groq's free tier has per-minute limits. Add a free Gemini key (`python setup.py keys`) as a backup. |
| VTOP login fails | Check your password on vtopcc.vit.ac.in, then run `python setup.py college`. VTOP is sometimes down for maintenance, especially at night. |
| Dashboard doesn't open | Install Node.js LTS, then run `python setup.py install`. |
| Anything else | `python backend/diagnose_voice.py` checks each part of the voice pipeline. [Open an issue](https://github.com/krishna-1688/jarvis/issues) with its output. |

## Contributing

Ideas and PRs are welcome, especially support for more campuses, more languages, and Linux/macOS. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

- VTOP client adapted from [VTOP_API](https://github.com/Sarath191181208/VTOP_API) by Srinivasa Sarath Chandra Vangipuram (MIT). Its licence is kept in `backend/features/vtop_handler/LICENCE`.
- Wake word by [openWakeWord](https://github.com/dscripka/openWakeWord). Speech by [edge-tts](https://github.com/rany2/edge-tts). AI by [Groq](https://groq.com) and Google Gemini.

## Disclaimer

This is an independent student project. It is **not affiliated with or endorsed by VIT**. You use your own credentials, at your own risk.

## License

[MIT](LICENSE) © 2026 Krishna
