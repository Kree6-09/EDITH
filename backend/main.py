"""G.R.A.C.E. backend: FastAPI server for voice/camera-driven AI assistant."""
from __future__ import annotations

import base64
import os
import time

import cv2
import numpy as np
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel

import spotify_client
from brain import GraceBrain
from logbook import log_sighting, recent_sightings
from vision import FaceEngine

load_dotenv(override=True)  # .env should win over stray OS-level env vars

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

app = FastAPI(title="G.R.A.C.E.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = FaceEngine()
brain = GraceBrain()

# Tracks the most recent recognition snapshot so voice commands like
# "who is that" can answer without a fresh frame round-trip.
last_seen: list[dict] = []
last_seen_ts = 0.0
_sighting_cooldown: dict[str, float] = {}
COOLDOWN_SECONDS = 30.0


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """No-op unless GRACE_API_KEY is set — LAN-only setups stay frictionless.
    Once you expose the backend to the internet (tunnel/port-forward), set
    GRACE_API_KEY so random visitors can't enroll faces, burn your LLM
    credits, or control your Spotify."""
    expected = os.environ.get("GRACE_API_KEY")
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def _check_ws_api_key(api_key: str | None) -> bool:
    expected = os.environ.get("GRACE_API_KEY")
    return not expected or api_key == expected


def _decode_frame(data_url: str) -> np.ndarray:
    header, _, b64data = data_url.partition(",")
    raw = base64.b64decode(b64data or header)
    arr = np.frombuffer(raw, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Could not decode frame")
    return frame


@app.websocket("/ws/vision")
async def vision_ws(websocket: WebSocket, api_key: str | None = None) -> None:
    global last_seen, last_seen_ts
    if not _check_ws_api_key(api_key):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while True:
            data_url = await websocket.receive_text()
            try:
                frame = _decode_frame(data_url)
            except ValueError:
                continue
            sightings = engine.recognize(frame)
            last_seen = [
                {"box": s.box, "name": s.name, "confidence": s.confidence, "known": s.known}
                for s in sightings
            ]
            last_seen_ts = time.time()

            for s in sightings:
                key = s.name
                now = time.time()
                if now - _sighting_cooldown.get(key, 0) > COOLDOWN_SECONDS:
                    _sighting_cooldown[key] = now
                    log_sighting(s.name, s.confidence, s.known)

            await websocket.send_json({"faces": last_seen})
    except WebSocketDisconnect:
        pass


class ChatRequest(BaseModel):
    text: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
def chat(req: ChatRequest) -> ChatResponse:
    text = req.text.strip()
    lowered = text.lower()

    identify_triggers = ("who is that", "who is this", "who do you see", "who am i looking at", "identify")
    if any(trigger in lowered for trigger in identify_triggers):
        if time.time() - last_seen_ts > 5:
            return ChatResponse(reply="I don't have a current camera reading. Point the camera at the subject.")
        known = [f for f in last_seen if f["known"]]
        if not known:
            if last_seen:
                return ChatResponse(reply=f"I see {len(last_seen)} face(s) in frame, but none match anyone enrolled.")
            return ChatResponse(reply="No faces currently in view.")
        names = ", ".join(sorted({f["name"] for f in known}))
        return ChatResponse(reply=f"I recognize {names} in frame.")

    return ChatResponse(reply=brain.respond(text))


class EnrollRequest(BaseModel):
    name: str
    image: str  # data URL


class EnrollResponse(BaseModel):
    success: bool
    message: str


@app.post("/api/faces/enroll", response_model=EnrollResponse, dependencies=[Depends(require_api_key)])
def enroll(req: EnrollRequest) -> EnrollResponse:
    name = req.name.strip()
    if not name:
        return EnrollResponse(success=False, message="Name is required.")
    try:
        frame = _decode_frame(req.image)
    except ValueError:
        return EnrollResponse(success=False, message="Invalid image data.")
    success, message = engine.enroll(name, frame)
    return EnrollResponse(success=success, message=message)


@app.get("/api/faces", dependencies=[Depends(require_api_key)])
def list_faces() -> dict:
    return {"names": engine.known_names()}


@app.delete("/api/faces/{name}", dependencies=[Depends(require_api_key)])
def delete_face(name: str) -> dict:
    removed = engine.remove_person(name)
    return {"removed": removed}


@app.get("/api/log", dependencies=[Depends(require_api_key)])
def get_log(limit: int = 20) -> dict:
    return {"sightings": recent_sightings(limit)}


@app.get("/api/status")
def status() -> dict:
    return {
        "online": True,
        "known_faces": len(engine.known_names()),
        "brain_connected": bool(
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GROQ_API_KEY")
        ),
        "spotify_configured": spotify_client.is_configured(),
        "spotify_connected": spotify_client.is_connected(),
    }


@app.get("/api/spotify/login")
def spotify_login(key: str | None = None) -> Response:
    expected = os.environ.get("GRACE_API_KEY")
    if expected and key != expected:
        return HTMLResponse("Falta o es invalido el parametro ?key=", status_code=401)
    if not spotify_client.is_configured():
        return HTMLResponse(
            "Spotify no esta configurado: falta SPOTIFY_CLIENT_ID / "
            "SPOTIFY_CLIENT_SECRET en el .env del servidor.",
            status_code=400,
        )
    return RedirectResponse(spotify_client.get_authorize_url())


@app.get("/api/spotify/callback")
def spotify_callback(code: str | None = None, error: str | None = None) -> HTMLResponse:
    if error:
        return HTMLResponse(f"Spotify devolvio un error: {error}", status_code=400)
    if not code:
        return HTMLResponse("Falta el parametro 'code' en la respuesta de Spotify.", status_code=400)
    try:
        spotify_client.exchange_code(code)
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(f"No se pudo conectar con Spotify: {exc}", status_code=500)
    return HTMLResponse(
        "<h2>G.R.A.C.E. ya esta conectada a Spotify.</h2>"
        "<p>Podes cerrar esta pestana y volver a la app.</p>"
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
