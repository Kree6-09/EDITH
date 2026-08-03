"""Sighting/event log for E.D.I.T.H. — a simple append-only JSON log."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "logs")
LOG_PATH = os.path.join(DATA_DIR, "sightings.jsonl")

os.makedirs(DATA_DIR, exist_ok=True)

_lock = threading.Lock()


def log_sighting(name: str, confidence: float, known: bool) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "confidence": confidence,
        "known": known,
    }
    with _lock:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def recent_sightings(limit: int = 20) -> list[dict]:
    if not os.path.exists(LOG_PATH):
        return []
    with _lock:
        with open(LOG_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    return [json.loads(line) for line in lines[-limit:]][::-1]
