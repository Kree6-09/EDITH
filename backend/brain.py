"""E.D.I.T.H. conversational brain: local command handling + optional LLM fallback (Claude or Groq)."""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime

import spotify_client

SYSTEM_PROMPT = (
    "You are E.D.I.T.H. (Even Dead, I'm The Hero), a calm, precise AI assistant "
    "inspired by Tony Stark's system from Spider-Man: Far From Home. You have "
    "access to a live camera feed with face recognition and a microphone with "
    "voice command input. Respond concisely, like a sharp tactical assistant — "
    "no filler, no long paragraphs unless asked. Address the user directly. "
    "You do not control any weapons or drones; you are an information, "
    "security-monitoring, and conversation assistant only."
)

GROQ_MODEL = "llama-3.3-70b-versatile"

# Music commands are matched on an accent-stripped, lowercased copy of the
# text so "poné", "pone", and "pon" (with or without voice-recognition
# accents) all match the same pattern.
_PLAY_RE = re.compile(r"^(?:pon(?:e|é)?|reproduc\w*|toc\w*)\s+(?:musica\s+de\s+|la\s+cancion\s+|cancion\s+de\s+|de\s+)?(.+)$")
_PAUSE_RE = re.compile(r"^(?:pausa\w*|para\w*|deten\w*)(?:\s+(?:la\s+)?(?:musica|cancion))?$")
_NEXT_RE = re.compile(r"^(?:siguiente|proxima|salta\w*)(?:\s+(?:cancion|tema))?$")
_PREV_RE = re.compile(r"^(?:cancion\s+anterior|anterior|volve\w*\s+cancion)$")


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")

_client = None
_provider = None
_client_checked = False


def _get_client():
    """Lazily construct an LLM client: prefers Anthropic, falls back to Groq."""
    global _client, _provider, _client_checked
    if _client_checked:
        return _provider, _client
    _client_checked = True

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            _client = anthropic.Anthropic()
            _provider = "anthropic"
        except Exception:
            _client = None
    elif os.environ.get("GROQ_API_KEY"):
        try:
            import groq
            _client = groq.Groq()
            _provider = "groq"
        except Exception:
            _client = None
    return _provider, _client


class EdithBrain:
    def __init__(self) -> None:
        self.history: list[dict] = []

    def _local_answer(self, text: str) -> str | None:
        lowered = text.lower().strip()

        if re.search(r"\b(time)\b", lowered):
            return f"The current time is {datetime.now().strftime('%I:%M %p')}."
        if re.search(r"\b(date|today)\b", lowered):
            return f"Today is {datetime.now().strftime('%A, %B %d, %Y')}."
        if re.search(r"\b(status|status report|system check|are you online)\b", lowered):
            return "All systems nominal. Camera feed active, voice link stable."
        if re.search(r"\b(hello|hi|hey)\b", lowered):
            return "Online and listening. What do you need?"
        if re.search(r"\bwho (made|created|built) you\b", lowered):
            return "I was built as a personal AI assistant project, in the spirit of Tony Stark's E.D.I.T.H."
        if re.search(r"\b(thank you|thanks)\b", lowered):
            return "Anytime."
        return None

    def _spotify_setup_issue(self) -> str | None:
        if not spotify_client.is_configured():
            return (
                "Spotify no esta configurado en el servidor. Crea una app en "
                "developer.spotify.com/dashboard y agrega SPOTIFY_CLIENT_ID y "
                "SPOTIFY_CLIENT_SECRET al .env del backend."
            )
        if not spotify_client.is_connected():
            return (
                "Spotify no esta conectado todavia. Abri "
                "http://127.0.0.1:8000/api/spotify/login en el navegador de la "
                "PC donde corre el servidor, una sola vez, para autorizar el acceso."
            )
        return None

    def _music_answer(self, text: str) -> str | None:
        plain = _strip_accents(text.lower().strip()).rstrip(".!?")
        if not plain:
            return None

        if _PAUSE_RE.match(plain):
            return self._spotify_action(spotify_client.pause, "Musica en pausa.")
        if _NEXT_RE.match(plain):
            return self._spotify_action(spotify_client.skip_next, "Siguiente cancion.")
        if _PREV_RE.match(plain):
            return self._spotify_action(spotify_client.skip_previous, "Volviendo a la cancion anterior.")

        match = _PLAY_RE.match(plain)
        if match and match.group(1).strip():
            return self._spotify_play(match.group(1).strip())
        return None

    def _spotify_action(self, action, success_message: str) -> str:
        issue = self._spotify_setup_issue()
        if issue:
            return issue
        try:
            ok, reason = action()
        except Exception as exc:  # noqa: BLE001
            return f"Error hablando con Spotify: {exc}"
        if not ok:
            if reason == "premium_required":
                return "Esa accion requiere Spotify Premium."
            return f"No pude completar la accion en Spotify ({reason})."
        return success_message

    def _spotify_play(self, query: str) -> str:
        issue = self._spotify_setup_issue()
        if issue:
            return issue
        try:
            track = spotify_client.search_track(query)
        except Exception as exc:  # noqa: BLE001
            return f"No pude buscar en Spotify: {exc}"
        if track is None:
            return f'No encontre nada en Spotify para "{query}".'
        try:
            ok, reason = spotify_client.play(track["uri"])
        except Exception as exc:  # noqa: BLE001
            return f"Encontre \"{track['name']}\" pero no pude reproducirla: {exc}"
        if not ok:
            if reason == "no_device":
                return (
                    f"Encontre \"{track['name']}\" de {track['artists']}, pero no hay "
                    "ningun dispositivo de Spotify activo. Abri Spotify en el telefono "
                    "o la PC y proba de nuevo."
                )
            if reason == "premium_required":
                return "Reproducir musica requiere Spotify Premium."
            return f"No pude iniciar la reproduccion ({reason})."
        return f"Reproduciendo \"{track['name']}\" de {track['artists']}."

    def respond(self, text: str) -> str:
        local = self._local_answer(text)
        if local is not None:
            return local

        music = self._music_answer(text)
        if music is not None:
            return music

        provider, client = _get_client()
        if client is None:
            return (
                "My reasoning core isn't connected — set ANTHROPIC_API_KEY or "
                "GROQ_API_KEY on the server to enable full conversation. I can "
                "still handle time, date, status checks, and face identification."
            )

        self.history.append({"role": "user", "content": text})
        self.history = self.history[-20:]
        try:
            if provider == "anthropic":
                response = client.messages.create(
                    model="claude-opus-5",
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    messages=self.history,
                )
                reply = next((b.text for b in response.content if b.type == "text"), "")
            else:
                response = client.chat.completions.create(
                    model=GROQ_MODEL,
                    max_tokens=1024,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT}, *self.history],
                )
                reply = response.choices[0].message.content or ""
            self.history.append({"role": "assistant", "content": reply})
            return reply or "I didn't catch a usable response — try again."
        except Exception as exc:  # noqa: BLE001
            return f"Reasoning core error: {exc}"
