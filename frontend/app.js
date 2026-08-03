(() => {
  const video = document.getElementById("video");
  const overlay = document.getElementById("overlay");
  const ctx = overlay.getContext("2d");
  const cameraOffMsg = document.getElementById("cameraOffMsg");

  const startBtn = document.getElementById("startBtn");
  const micBtn = document.getElementById("micBtn");
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

  const WAKE_WORD = "edith";
  const FRAME_INTERVAL_MS = 1200;

  let ws = null;
  let recognition = null;
  let micOn = false;
  let latestFaces = [];
  let systemStarted = false;

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

  function speak(text) {
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
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      appendLine("edith", data.reply);
      speak(data.reply);
    } catch (err) {
      appendLine("system", `Connection error: ${err}`);
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
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/vision`);
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

  async function startCamera() {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    video.srcObject = stream;
    await video.play();
    resizeOverlay();
    cameraOffMsg.style.display = "none";
    requestAnimationFrame(drawOverlay);
    startVisionLoop();
  }

  // ---- Speech recognition (wake word + command) ----

  function setupRecognition() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      appendLine("system", "Speech recognition is not supported in this browser. Use Chrome, or type commands below.");
      return null;
    }
    const rec = new SR();
    rec.continuous = true;
    rec.interimResults = false;
    rec.lang = "en-US";

    rec.onresult = (event) => {
      const result = event.results[event.results.length - 1];
      if (!result.isFinal) return;
      const said = result[0].transcript.trim();
      const lowered = said.toLowerCase();
      const wakeIdx = lowered.indexOf(WAKE_WORD);
      if (wakeIdx === -1) return;
      const command = said.slice(wakeIdx + WAKE_WORD.length).replace(/^[\s,.:]+/, "");
      if (command) {
        sendCommand(command);
      } else {
        appendLine("edith", "Yes? I'm listening.");
        speak("Yes?");
      }
    };

    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        setPill(statusVoice, "error");
      }
    };

    rec.onend = () => {
      if (micOn) {
        try { rec.start(); } catch (_) { /* already running */ }
      }
    };

    return rec;
  }

  function toggleMic() {
    if (!recognition) {
      recognition = setupRecognition();
      if (!recognition) return;
    }
    micOn = !micOn;
    if (micOn) {
      recognition.start();
      micBtn.textContent = "MIC: ON";
      micBtn.classList.add("active");
      setPill(statusVoice, "on");
    } else {
      recognition.stop();
      micBtn.textContent = "MIC: OFF";
      micBtn.classList.remove("active");
      setPill(statusVoice, null);
    }
  }

  micBtn.addEventListener("click", toggleMic);

  enrollBtn.addEventListener("click", async () => {
    const name = enrollName.value.trim();
    if (!name) {
      appendLine("system", "Enter a subject name before enrolling.");
      return;
    }
    if (!video.videoWidth) {
      appendLine("system", "Camera is not active.");
      return;
    }
    const image = captureFrameDataUrl(0.85);
    try {
      const res = await fetch("/api/faces/enroll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, image }),
      });
      const data = await res.json();
      appendLine("system", data.message);
      if (data.success) refreshFaces();
    } catch (err) {
      appendLine("system", `Enroll failed: ${err}`);
    }
  });

  startBtn.addEventListener("click", async () => {
    if (systemStarted) return;
    systemStarted = true;
    startBtn.disabled = true;
    startBtn.textContent = "INITIALIZING...";
    try {
      await startCamera();
      startBtn.textContent = "SYSTEM ONLINE";
      micBtn.disabled = false;
      enrollBtn.disabled = false;
      appendLine("system", "E.D.I.T.H. online. Camera and reasoning core connected.");
      speak("Systems online.");
      checkStatus();
    } catch (err) {
      startBtn.disabled = false;
      startBtn.textContent = "START SYSTEM";
      systemStarted = false;
      appendLine("system", `Camera access failed: ${err.message || err}`);
      setPill(statusVision, "error");
    }
  });

  async function checkStatus() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      setPill(statusBrain, data.brain_connected ? "on" : "error");
      if (!data.brain_connected) {
        appendLine("system", "Reasoning core offline (no ANTHROPIC_API_KEY set). Local commands still work.");
      }
    } catch (_) {
      setPill(statusBrain, "error");
    }
  }

  async function refreshLog() {
    try {
      const res = await fetch("/api/log?limit=15");
      const data = await res.json();
      logList.innerHTML = "";
      for (const entry of data.sightings) {
        const li = document.createElement("li");
        const time = new Date(entry.timestamp).toLocaleTimeString([], { hour12: false });
        li.innerHTML = `<span class="name ${entry.known ? "known" : "unknown"}">${entry.name}</span><span>${time}</span>`;
        logList.appendChild(li);
      }
    } catch (_) { /* ignore */ }
  }

  async function refreshFaces() {
    try {
      const res = await fetch("/api/faces");
      const data = await res.json();
      faceList.innerHTML = "";
      if (data.names.length === 0) {
        const li = document.createElement("li");
        li.textContent = "No subjects enrolled yet.";
        faceList.appendChild(li);
        return;
      }
      for (const name of data.names) {
        const li = document.createElement("li");
        li.textContent = name;
        const btn = document.createElement("button");
        btn.textContent = "remove";
        btn.addEventListener("click", async () => {
          await fetch(`/api/faces/${encodeURIComponent(name)}`, { method: "DELETE" });
          refreshFaces();
        });
        li.appendChild(btn);
        faceList.appendChild(li);
      }
    } catch (_) { /* ignore */ }
  }

  setInterval(refreshLog, 4000);
  setInterval(refreshFaces, 6000);
  refreshLog();
  refreshFaces();
  checkStatus();
})();
