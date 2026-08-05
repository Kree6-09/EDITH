import { Capacitor } from "@capacitor/core";
import { Camera } from "@capacitor/camera";
import { Preferences } from "@capacitor/preferences";
import { SpeechRecognition } from "@capacitor-community/speech-recognition";
import { TextToSpeech } from "@capacitor-community/text-to-speech";

const IS_NATIVE = Capacitor.isNativePlatform();
const WAKE_WORD = "edith";
const FRAME_INTERVAL_MS = 1200;
const BACKEND_URL_KEY = "edith_backend_url";
const API_KEY_PREF_KEY = "edith_api_key";

let API_BASE = ""; // same-origin in browser; resolved from Preferences on native
let WS_BASE = "";
let API_KEY = ""; // optional shared secret, only needed if the backend sets EDITH_API_KEY

function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  return fetch(`${API_BASE}${path}`, { ...options, headers });
}

function wsUrlWithKey(path) {
  const url = `${WS_BASE}${path}`;
  return API_KEY ? `${url}?api_key=${encodeURIComponent(API_KEY)}` : url;
}

// ---- DOM refs (shared with the web frontend markup) ----
const video = document.getElementById("video");
const overlay = document.getElementById("overlay");
const ctx = overlay.getContext("2d");
const cameraOffMsg = document.getElementById("cameraOffMsg");

const startBtn = document.getElementById("startBtn");
const micBtn = document.getElementById("micBtn");
const switchCameraBtn = document.getElementById("switchCameraBtn");
const enrollBtn = document.getElementById("enrollBtn");
const enrollName = document.getElementById("enrollName");

const transcript = document.getElementById("transcript");
const textForm = document.getElementById("textForm");
const textInput = document.getElementById("textInput");

const statusVision = document.getElementById("statusVision");
const statusVoice = document.getElementById("statusVoice");
const statusBrain = document.getElementById("statusBrain");
const clockEl = document.getElementById("clock");

const logList = document.getElementById("logList");
const faceList = document.getElementById("faceList");

const settingsBtn = document.getElementById("settingsBtn");
const settingsModal = document.getElementById("settingsModal");
const settingsInput = document.getElementById("settingsInput");
const settingsApiKeyInput = document.getElementById("settingsApiKeyInput");
const settingsSave = document.getElementById("settingsSave");
const settingsCancel = document.getElementById("settingsCancel");

let ws = null;
let micOn = false;
let listeningLoopActive = false;
let latestFaces = [];
let systemStarted = false;
// "environment" = rear camera (better for scanning surroundings/people),
// "user" = front camera (better for self-enrollment). "ideal" (not "exact")
// so devices with only one camera don't throw OverconstrainedError.
let currentFacingMode = "environment";

function tickClock() {
  clockEl.textContent = new Date().toLocaleTimeString([], { hour12: false });
}
setInterval(tickClock, 1000);
tickClock();

function setPill(el, state) {
  el.classList.remove("on", "error");
  if (state === "on") el.classList.add("on");
  if (state === "error") el.classList.add("error");
}

function appendLine(kind, text) {
  const div = document.createElement("div");
  div.className = `line ${kind}`;
  div.textContent = text;
  transcript.appendChild(div);
  transcript.scrollTop = transcript.scrollHeight;
}

// ---- Backend URL resolution (native only) ----

async function resolveBackendUrl() {
  if (!IS_NATIVE) {
    API_BASE = "";
    WS_BASE = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
    return true;
  }

  const storedKey = await Preferences.get({ key: API_KEY_PREF_KEY });
  API_KEY = storedKey.value || "";

  const stored = await Preferences.get({ key: BACKEND_URL_KEY });
  if (stored.value) {
    setBackendUrl(stored.value);
    return true;
  }
  openSettings(true);
  return false;
}

function setBackendUrl(url) {
  const clean = url.replace(/\/+$/, "");
  API_BASE = clean;
  WS_BASE = clean.replace(/^http/, "ws");
}

function openSettings(forced = false) {
  settingsModal.classList.add("open");
  settingsInput.value = API_BASE || "http://";
  if (settingsApiKeyInput) settingsApiKeyInput.value = API_KEY || "";
  settingsCancel.style.display = forced ? "none" : "inline-block";
}

function closeSettings() {
  settingsModal.classList.remove("open");
}

if (settingsBtn) {
  if (IS_NATIVE) settingsBtn.style.display = "inline-block";
  settingsBtn.addEventListener("click", () => openSettings(false));
}
if (settingsCancel) settingsCancel.addEventListener("click", closeSettings);
if (settingsSave) {
  settingsSave.addEventListener("click", async () => {
    const url = settingsInput.value.trim();
    if (!/^https?:\/\/.+/.test(url)) {
      appendLine("system", "URL invalida. Debe empezar con http:// o https://");
      return;
    }
    setBackendUrl(url);
    await Preferences.set({ key: BACKEND_URL_KEY, value: url });

    API_KEY = (settingsApiKeyInput && settingsApiKeyInput.value.trim()) || "";
    await Preferences.set({ key: API_KEY_PREF_KEY, value: API_KEY });

    closeSettings();
    appendLine("system", `Servidor configurado: ${url}`);
    checkStatus();
  });
}

// ---- Speech output ----

async function speak(text) {
  if (IS_NATIVE) {
    try {
      await TextToSpeech.speak({ text, lang: "es-ES", rate: 1.0, pitch: 0.95 });
    } catch (err) {
      console.error("TTS error", err);
    }
    return;
  }
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.rate = 1.02;
  utter.pitch = 0.95;
  window.speechSynthesis.speak(utter);
}

async function sendCommand(text) {
  if (!text.trim()) return;
  appendLine("user", text);
  try {
    const res = await apiFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    appendLine("edith", data.reply);
    speak(data.reply);
  } catch (err) {
    appendLine("system", `Error de conexion: ${err}`);
  }
}

textForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = textInput.value;
  textInput.value = "";
  sendCommand(text);
});

// ---- Camera + vision websocket ----

function resizeOverlay() {
  overlay.width = video.videoWidth || overlay.clientWidth;
  overlay.height = video.videoHeight || overlay.clientHeight;
}

function drawOverlay() {
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  for (const face of latestFaces) {
    const [x, y, w, h] = face.box;
    ctx.strokeStyle = face.known ? "#4df3ff" : "#ff4d4d";
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);
    ctx.fillStyle = face.known ? "#4df3ff" : "#ff4d4d";
    ctx.font = "14px monospace";
    ctx.fillText(face.known ? face.name : "UNKNOWN", x, Math.max(14, y - 6));
  }
  requestAnimationFrame(drawOverlay);
}

function captureFrameDataUrl(quality = 0.6) {
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const c = canvas.getContext("2d");
  c.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", quality);
}

function startVisionLoop() {
  ws = new WebSocket(wsUrlWithKey("/ws/vision"));
  ws.onopen = () => setPill(statusVision, "on");
  ws.onclose = () => setPill(statusVision, "error");
  ws.onerror = () => setPill(statusVision, "error");
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    latestFaces = data.faces || [];
  };

  setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN && video.videoWidth) {
      ws.send(captureFrameDataUrl());
    }
  }, FRAME_INTERVAL_MS);
}

async function ensureCameraPermission() {
  if (!IS_NATIVE) return true;
  const status = await Camera.checkPermissions();
  if (status.camera === "granted") return true;
  const req = await Camera.requestPermissions({ permissions: ["camera"] });
  return req.camera === "granted";
}

function acquireStream(facingMode) {
  // Constrain resolution: phone cameras default to much higher resolutions than
  // a webcam, which turns the enroll snapshot into a multi-MB data URL and makes
  // that upload fail while smaller requests (chat, status) keep working fine.
  return navigator.mediaDevices.getUserMedia({
    video: { width: { ideal: 960 }, height: { ideal: 720 }, facingMode: { ideal: facingMode } },
    audio: false,
  });
}

function stopStream(stream) {
  if (stream) stream.getTracks().forEach((track) => track.stop());
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Releasing the camera on Android isn't instantaneous even after track.stop():
// the hardware session takes a moment to actually free up, so an immediate
// getUserMedia() for the other camera can still race and fail with
// "Could not start video source". Retry with a short backoff instead of
// failing on the first race.
async function acquireStreamWithRetry(facingMode, attempts = 4, delayMs = 350) {
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    if (i > 0) await wait(delayMs);
    try {
      return await acquireStream(facingMode);
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr;
}

async function startCamera() {
  const granted = await ensureCameraPermission();
  if (!granted) throw new Error("Permiso de camara denegado");

  const stream = await acquireStream(currentFacingMode);
  video.srcObject = stream;
  await video.play();
  resizeOverlay();
  cameraOffMsg.style.display = "none";
  requestAnimationFrame(drawOverlay);
  startVisionLoop();
}

async function switchCamera() {
  // Camera hardware is exclusive on most Android devices: the previous stream
  // must be released before a new one can be opened, otherwise getUserMedia
  // fails with "Could not start video source".
  const previousFacingMode = currentFacingMode;
  stopStream(video.srcObject);
  video.srcObject = null;
  await wait(300);

  const nextFacingMode = previousFacingMode === "environment" ? "user" : "environment";
  try {
    const stream = await acquireStreamWithRetry(nextFacingMode);
    currentFacingMode = nextFacingMode;
    video.srcObject = stream;
    await video.play();
    resizeOverlay();
    appendLine("system", `Camara ${currentFacingMode === "environment" ? "trasera" : "frontal"} activada.`);
  } catch (err) {
    appendLine("system", `No se pudo cambiar de camara: ${err.message || err}`);
    try {
      const fallbackStream = await acquireStreamWithRetry(previousFacingMode);
      video.srcObject = fallbackStream;
      await video.play();
      resizeOverlay();
    } catch (fallbackErr) {
      appendLine("system", "No se pudo restaurar la camara anterior.");
    }
  }
}

if (switchCameraBtn) {
  switchCameraBtn.addEventListener("click", switchCamera);
}

// ---- Voice: native SpeechRecognition (Android) vs Web Speech API (browser) ----

let webRecognition = null;

function handleUtterance(said) {
  const lowered = said.toLowerCase();
  const wakeIdx = lowered.indexOf(WAKE_WORD);
  if (wakeIdx === -1) return;
  const command = said.slice(wakeIdx + WAKE_WORD.length).replace(/^[\s,.:]+/, "");
  if (command) {
    sendCommand(command);
  } else {
    appendLine("edith", "Si? Te escucho.");
    speak("Si?");
  }
}

async function startNativeListeningLoop() {
  const available = await SpeechRecognition.available();
  if (!available.available) {
    appendLine("system", "Reconocimiento de voz no disponible en este dispositivo.");
    return false;
  }
  const perm = await SpeechRecognition.requestPermissions();
  if (perm.speechRecognition !== "granted") {
    appendLine("system", "Permiso de microfono denegado.");
    return false;
  }

  listeningLoopActive = true;
  (async function loop() {
    while (listeningLoopActive) {
      try {
        const result = await SpeechRecognition.start({
          language: "es-ES",
          popup: false,
          partialResults: false,
          maxResults: 1,
        });
        const said = (result.matches && result.matches[0]) || "";
        if (said) handleUtterance(said);
      } catch (err) {
        // timeout / no match / stopped externally - just keep looping
      }
    }
  })();
  return true;
}

function stopNativeListeningLoop() {
  listeningLoopActive = false;
  SpeechRecognition.stop().catch(() => {});
}

function setupWebRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    appendLine("system", "El reconocimiento de voz no esta soportado en este navegador. Usa Chrome o escribe los comandos.");
    return null;
  }
  const rec = new SR();
  rec.continuous = true;
  rec.interimResults = false;
  rec.lang = "en-US";

  rec.onresult = (event) => {
    const result = event.results[event.results.length - 1];
    if (!result.isFinal) return;
    handleUtterance(result[0].transcript.trim());
  };

  rec.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      setPill(statusVoice, "error");
    }
  };

  rec.onend = () => {
    if (micOn) {
      try {
        rec.start();
      } catch (_) {
        /* already running */
      }
    }
  };

  return rec;
}

async function toggleMic() {
  micOn = !micOn;
  if (micOn) {
    micBtn.textContent = "MIC: ON";
    micBtn.classList.add("active");
    setPill(statusVoice, "on");

    if (IS_NATIVE) {
      const ok = await startNativeListeningLoop();
      if (!ok) {
        micOn = false;
        micBtn.textContent = "MIC: OFF";
        micBtn.classList.remove("active");
        setPill(statusVoice, "error");
      }
    } else {
      if (!webRecognition) webRecognition = setupWebRecognition();
      if (!webRecognition) {
        micOn = false;
        return;
      }
      webRecognition.start();
    }
  } else {
    micBtn.textContent = "MIC: OFF";
    micBtn.classList.remove("active");
    setPill(statusVoice, null);
    if (IS_NATIVE) {
      stopNativeListeningLoop();
    } else if (webRecognition) {
      webRecognition.stop();
    }
  }
}

micBtn.addEventListener("click", toggleMic);

enrollBtn.addEventListener("click", async () => {
  const name = enrollName.value.trim();
  if (!name) {
    appendLine("system", "Escribe el nombre del sujeto antes de enrolarlo.");
    return;
  }
  if (!video.videoWidth) {
    appendLine("system", "La camara no esta activa.");
    return;
  }
  const image = captureFrameDataUrl(0.85);
  try {
    const res = await apiFetch("/api/faces/enroll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, image }),
    });
    if (!res.ok) {
      appendLine("system", `El servidor respondio ${res.status} al enrolar. Intenta con una foto mas chica o revisa el servidor.`);
      return;
    }
    const data = await res.json();
    appendLine("system", data.message);
    if (data.success) refreshFaces();
  } catch (err) {
    appendLine("system", `Fallo el enrolamiento: ${err}`);
  }
});

startBtn.addEventListener("click", async () => {
  if (systemStarted) return;

  if (IS_NATIVE && !API_BASE) {
    const ok = await resolveBackendUrl();
    if (!ok) return; // settings modal is open, waiting on the user
  }

  systemStarted = true;
  startBtn.disabled = true;
  startBtn.textContent = "INICIALIZANDO...";
  try {
    await startCamera();
    startBtn.textContent = "SISTEMA ACTIVO";
    micBtn.disabled = false;
    enrollBtn.disabled = false;
    if (switchCameraBtn) switchCameraBtn.disabled = false;
    appendLine("system", "E.D.I.T.H. en linea. Camara y nucleo de razonamiento conectados.");
    speak("Sistemas en linea.");
    checkStatus();
  } catch (err) {
    startBtn.disabled = false;
    startBtn.textContent = "START SYSTEM";
    systemStarted = false;
    appendLine("system", `Fallo el acceso a la camara: ${err.message || err}`);
    setPill(statusVision, "error");
  }
});

async function checkStatus() {
  try {
    const res = await apiFetch("/api/status");
    const data = await res.json();
    setPill(statusBrain, data.brain_connected ? "on" : "error");
    if (!data.brain_connected) {
      appendLine("system", "Nucleo de razonamiento desconectado (falta ANTHROPIC_API_KEY en el servidor). Los comandos locales siguen funcionando.");
    }
  } catch (_) {
    setPill(statusBrain, "error");
  }
}

async function refreshLog() {
  if (!API_BASE && IS_NATIVE) return;
  try {
    const res = await apiFetch("/api/log?limit=15");
    const data = await res.json();
    logList.innerHTML = "";
    for (const entry of data.sightings) {
      const li = document.createElement("li");
      const time = new Date(entry.timestamp).toLocaleTimeString([], { hour12: false });
      li.innerHTML = `<span class="name ${entry.known ? "known" : "unknown"}">${entry.name}</span><span>${time}</span>`;
      logList.appendChild(li);
    }
  } catch (_) {
    /* ignore */
  }
}

async function refreshFaces() {
  if (!API_BASE && IS_NATIVE) return;
  try {
    const res = await apiFetch("/api/faces");
    const data = await res.json();
    faceList.innerHTML = "";
    if (data.names.length === 0) {
      const li = document.createElement("li");
      li.textContent = "No hay sujetos enrolados todavia.";
      faceList.appendChild(li);
      return;
    }
    for (const name of data.names) {
      const li = document.createElement("li");
      li.textContent = name;
      const btn = document.createElement("button");
      btn.textContent = "eliminar";
      btn.addEventListener("click", async () => {
        await apiFetch(`/api/faces/${encodeURIComponent(name)}`, { method: "DELETE" });
        refreshFaces();
      });
      li.appendChild(btn);
      faceList.appendChild(li);
    }
  } catch (_) {
    /* ignore */
  }
}

(async function init() {
  await resolveBackendUrl();
  setInterval(refreshLog, 4000);
  setInterval(refreshFaces, 6000);
  refreshLog();
  refreshFaces();
  if (API_BASE || !IS_NATIVE) checkStatus();
})();
