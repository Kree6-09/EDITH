# E.D.I.T.H.

*Even Dead, I'm The Hero* — a personal AI assistant inspired by Tony Stark's
system from *Spider-Man: Far From Home*. Runs in your browser with a live
camera feed, face recognition, voice commands, text-to-speech replies, and an
optional connection to a real LLM for open-ended conversation.

## Features

- **Voice recognition** — say the wake word "Edith" followed by a command
  (uses the browser's Web Speech API). Text input is always available as a
  fallback.
- **Camera / vision** — live webcam feed streamed to the backend for face
  detection and recognition (OpenCV, no GPU required).
- **Person recognition** — enroll people by name from a live camera frame;
  E.D.I.T.H. recognizes and labels them in the feed and can answer "who is
  that?" style questions.
- **Sighting log** — a running log of who's been seen and when, shown in the
  HUD.
- **Conversational core** — local rule-based answers for time/date/status,
  and (if you provide an `ANTHROPIC_API_KEY`) full LLM-backed conversation
  via the Claude API.
- **Text-to-speech** — spoken replies via the browser's `speechSynthesis` API.
- **HUD-style interface** — dark sci-fi console with live optical feed,
  console/transcript panel, and status indicators.

## Architecture

```
frontend/        Browser HUD (camera capture, mic, TTS, chat UI)
  index.html
  style.css
  app.js
backend/         FastAPI server
  main.py        REST + WebSocket endpoints, serves the frontend
  vision.py      Face detection/recognition (OpenCV Haar cascade + LBPH)
  brain.py       Command router + optional Claude API conversation
  logbook.py     Append-only sighting log
mobile/          Android app (Capacitor) — see mobile/README.md
data/
  known_faces/   Per-person face crops (created as you enroll people)
  logs/          Sighting log (JSONL)
```

The camera and microphone are accessed by the browser (required for real
hardware access); the backend does face recognition on frames sent over a
WebSocket and handles the conversational logic.

## Setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp ../.env.example ../.env   # then edit .env and add ANTHROPIC_API_KEY (optional)
```

## Run

```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in **Chrome** (the Web Speech API for voice
recognition is Chrome-only; camera/face-ID and text chat work in any modern
browser). Click **START SYSTEM** to grant camera access, then **MIC: ON** to
enable voice commands.

## Usage

- **Enroll a person**: type their name in the "Subject name" box and click
  **ENROLL FACE** while they're in frame. Repeat a few times from different
  angles for better recognition.
- **Voice command**: say "Edith, what time is it?" or "Edith, who do you
  see?".
- **Text command**: type into the console input and press SEND.
- **Full conversation**: set `ANTHROPIC_API_KEY` in `.env` to let E.D.I.T.H.
  answer open-ended questions via Claude instead of just local commands.

## Android app

There's also a native Android app in `mobile/` (Capacitor) that reuses this
HUD with native voice recognition and text-to-speech instead of the
browser's Web Speech API, so voice commands work outside Chrome too. It
connects to this same backend over the network — see `mobile/README.md` for
how to get a build.

## Notes

- No API key? E.D.I.T.H. still works — time, date, system status, and face
  identification are all handled locally.
- Face recognition uses OpenCV's LBPH recognizer, which is lightweight but
  less accurate than deep-learning face embeddings. Enroll multiple samples
  per person for best results.
- Nothing here controls physical hardware, drones, or weapons — this is an
  information/monitoring/conversation assistant only.
