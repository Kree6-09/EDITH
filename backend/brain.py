"""E.D.I.T.H. conversational brain: local command handling + optional LLM fallback (Claude or Groq)."""
from __future__ import annotations

import os
import re
from datetime import datetime

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

    def respond(self, text: str) -> str:
        local = self._local_answer(text)
        if local is not None:
            return local

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
