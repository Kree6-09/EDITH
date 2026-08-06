"""Spotify Web API client: OAuth login + playback control for G.R.A.C.E.

Uses the Authorization Code flow (server-side, with the client secret kept
here and never sent to the frontend). Playback control requires Spotify
Premium and at least one active device (the Spotify app open somewhere).
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse

import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TOKEN_PATH = os.path.join(DATA_DIR, "spotify_token.json")

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing"


def _client_id() -> str:
    return os.environ.get("SPOTIFY_CLIENT_ID", "")


def _client_secret() -> str:
    return os.environ.get("SPOTIFY_CLIENT_SECRET", "")


def _redirect_uri() -> str:
    # Spotify's app dashboard only accepts HTTP for loopback redirect URIs
    # using the literal "127.0.0.1" (not "localhost"), so the one-time login
    # must happen from a browser on the same machine as the backend.
    return os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/api/spotify/callback")


def is_configured() -> bool:
    return bool(_client_id() and _client_secret())


def _load_tokens() -> dict | None:
    if not os.path.exists(TOKEN_PATH):
        return None
    with open(TOKEN_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_tokens(tokens: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TOKEN_PATH, "w", encoding="utf-8") as fh:
        json.dump(tokens, fh)


def is_connected() -> bool:
    return _load_tokens() is not None


def get_authorize_url() -> str:
    params = {
        "client_id": _client_id(),
        "response_type": "code",
        "redirect_uri": _redirect_uri(),
        "scope": SCOPES,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> None:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _redirect_uri(),
            "client_id": _client_id(),
            "client_secret": _client_secret(),
        },
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    payload["obtained_at"] = time.time()
    _save_tokens(payload)


def _refresh_access_token(tokens: dict) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": _client_id(),
            "client_secret": _client_secret(),
        },
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    payload["obtained_at"] = time.time()
    # Spotify doesn't always return a new refresh_token; keep the old one.
    payload.setdefault("refresh_token", tokens["refresh_token"])
    _save_tokens(payload)
    return payload


def _get_valid_access_token() -> str:
    tokens = _load_tokens()
    if tokens is None:
        raise RuntimeError("not_connected")
    expires_in = tokens.get("expires_in", 3600)
    if time.time() >= tokens.get("obtained_at", 0) + expires_in - 30:
        tokens = _refresh_access_token(tokens)
    return tokens["access_token"]


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_get_valid_access_token()}"}


def search_track(query: str) -> dict | None:
    resp = requests.get(
        f"{API_BASE}/search",
        headers=_auth_headers(),
        params={"q": query, "type": "track", "limit": 1},
        timeout=10,
    )
    resp.raise_for_status()
    items = resp.json().get("tracks", {}).get("items", [])
    if not items:
        return None
    track = items[0]
    return {
        "uri": track["uri"],
        "name": track["name"],
        "artists": ", ".join(a["name"] for a in track["artists"]),
    }


def get_active_device_id() -> str | None:
    resp = requests.get(f"{API_BASE}/me/player/devices", headers=_auth_headers(), timeout=10)
    resp.raise_for_status()
    devices = resp.json().get("devices", [])
    active = next((d for d in devices if d.get("is_active")), None)
    if active:
        return active["id"]
    return devices[0]["id"] if devices else None


def play(track_uri: str | None = None) -> tuple[bool, str]:
    device_id = get_active_device_id()
    if device_id is None:
        return False, "no_device"
    params = {"device_id": device_id}
    body = {"uris": [track_uri]} if track_uri else None
    resp = requests.put(f"{API_BASE}/me/player/play", headers=_auth_headers(), params=params, json=body, timeout=10)
    if resp.status_code == 403:
        return False, "premium_required"
    if resp.status_code >= 400:
        return False, f"http_{resp.status_code}"
    return True, "ok"


def pause() -> tuple[bool, str]:
    resp = requests.put(f"{API_BASE}/me/player/pause", headers=_auth_headers(), timeout=10)
    if resp.status_code == 403:
        return False, "premium_required"
    if resp.status_code >= 400 and resp.status_code != 404:
        return False, f"http_{resp.status_code}"
    return True, "ok"


def skip_next() -> tuple[bool, str]:
    resp = requests.post(f"{API_BASE}/me/player/next", headers=_auth_headers(), timeout=10)
    if resp.status_code >= 400 and resp.status_code != 204:
        return False, f"http_{resp.status_code}"
    return True, "ok"


def skip_previous() -> tuple[bool, str]:
    resp = requests.post(f"{API_BASE}/me/player/previous", headers=_auth_headers(), timeout=10)
    if resp.status_code >= 400 and resp.status_code != 204:
        return False, f"http_{resp.status_code}"
    return True, "ok"


def current_playback() -> dict | None:
    resp = requests.get(f"{API_BASE}/me/player", headers=_auth_headers(), timeout=10)
    if resp.status_code == 204 or resp.status_code >= 400:
        return None
    data = resp.json()
    item = data.get("item") or {}
    return {
        "is_playing": data.get("is_playing", False),
        "track": item.get("name"),
        "artists": ", ".join(a["name"] for a in item.get("artists", [])),
    }
