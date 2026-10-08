# Security

Jarvis runs on your own laptop and can read your college records, see your screen, control your PC and send WhatsApp messages. It is built so that only you can make it do those things.

## How it is protected

- **Local only.** The backend (`127.0.0.1:8000`) and the WhatsApp service (`127.0.0.1:4500`) listen on the loopback address, so nothing on your Wi-Fi network can reach them.
- **Web pages are locked out.** Any website open in your browser could otherwise send requests to `127.0.0.1`. The backend rejects every request made from a browser (identified by its `Origin` / `Sec-Fetch-Site` headers, which pages cannot remove) unless it carries this install's random API key. Only the Electron dashboard receives that key, from its main process. The live WebSocket is covered the same way. The WhatsApp service accepts no browser requests at all.
- **DNS rebinding is blocked.** Requests addressed to any host name other than `127.0.0.1` or `localhost` are rejected.
- **The dashboard can't be hijacked.** Its window never navigates away from Jarvis's own page; links open in your normal browser.
- **No arbitrary programs.** "Open …" launches only known apps or programs on your `PATH`, never a path or command line taken from speech. Shutdown and restart always ask for confirmation.
- **Secrets stay out of git.** `backend/.env` (API keys, VTOP/LMS passwords), `backend/profile.toml`, the local databases and the API key file are git-ignored.

Implementation: `backend/core/security.py`, `frontend/electron/main.js`, `whatsapp_service/server.js`.

## What leaves your laptop

The text of what you say goes to Groq (and Google Gemini, if you add a backup key) to be understood and answered; "what's on my screen" sends one screenshot to the same AI; the VTOP captcha image is read by it too; replies are voiced by Microsoft Edge's online text-to-speech. Your VTOP and LMS passwords go only to VIT's own servers.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's **Report a vulnerability** button under this repository's **Security** tab, with steps to reproduce. You'll get a reply within a few days.
