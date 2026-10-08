# Contributing

Thanks for helping. Jarvis started as one VIT Chennai student's assistant, and the goal is for it to work well for any student.

## Most wanted

- **More campuses:** VIT Vellore testing (`college.campus = "vellore"`), then VIT-AP and Bhopal. Campus hosts and semester-ID prefixes live in `backend/core/semesters.py`.
- **Languages:** better replies and recognition for Tamil, Telugu, Hindi and Malayalam speakers.
- **Linux/macOS:** port `backend/features/pc_control.py` and `backend/core/power.py`. Everything else is mostly portable.
- **Bug reports with logs:** the console output of `start.bat`, or `python backend/diagnose_voice.py`.

## Development setup

```
python setup.py              # or setup.bat for an isolated .venv
python backend/run.py        # server + voice loop
cd frontend && npm run dev   # dashboard with hot reload (http://localhost:5173)
```

`python backend/server.py` alone runs the brain without the mic. You can talk to it by typing in the dashboard, or with `curl -X POST localhost:8000/command -H "Content-Type: application/json" -d "{\"text\": \"what's next\"}"`.

## Ground rules

- **Never commit personal data.** `.env`, `profile.toml`, `backend/data/database/` and the TTS cache are git-ignored. Keep it that way, and check `git status` before every commit. No real registration numbers, names or marks in tests or examples.
- **Nothing personal in code.** Anything about the user comes from `core/profile.py`, and anything VIT-specific is gated on `VTOP_ENABLED` / `LMS_ENABLED`.
- **Be gentle with VIT's servers.** Answer from the local SQLite copy, and only contact VTOP on a schedule or an explicit “refresh”.
- **Free tier first.** A feature should work on Groq's free limits. Route every AI call through `core/llm.py` so model fallback and rate-limit handling apply.
- **Match the surrounding code.** Short docstrings that explain *why*, and no new dependencies without a reason.

## Pull requests

Keep them focused. Say what you changed, how you tested it, and on which campus if it touches VTOP.
