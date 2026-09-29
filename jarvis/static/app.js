// Jarvis Web-App: Text + Sprache. Spracherkennung/-ausgabe laufen bevorzugt direkt
// im Handy-Browser (kostenlos); Fallback ist Whisper/Piper auf deinem Server.
(() => {
  const $ = (id) => document.getElementById(id);
  const log = $("log"), mic = $("mic"), hint = $("hint"), dot = $("status-dot");
  const store = {
    get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
  };

  // Token einmalig per ?token=… übernehmen und aus der URL entfernen
  const params = new URLSearchParams(location.search);
  if (params.get("token")) {
    store.set("jarvis-token", params.get("token"));
    params.delete("token");
    history.replaceState(null, "", location.pathname + (params.toString() ? "?" + params : ""));
  }
  let token = store.get("jarvis-token", "");
  let muted = store.get("jarvis-muted", "0") === "1";
  let handsfree = store.get("jarvis-handsfree", "0") === "1";
  let busy = false, listening = false;
  let serverTTS = false;

  // ------------------------------------------------------------------ UI
  function add(text, who, cls = "") {
    const el = document.createElement("div");
    el.className = `msg ${who} ${cls}`.trim();
    el.textContent = text;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }
  function setState(s) {
    dot.className = "dot " + (s === "busy" ? "busy" : s === "err" ? "err" : "ok");
    mic.classList.toggle("listening", s === "listening");
    mic.classList.toggle("thinking", s === "busy");
    hint.textContent = { listening: "Ich höre zu …", busy: "Denke nach …", speaking: "Spreche …" }[s] ||
      (handsfree ? "Gesprächsmodus aktiv – tippen zum Sprechen" : "Tippen zum Sprechen");
  }
  function refreshChips() {
    $("btn-mute").textContent = muted ? "🔇" : "🔊";
    $("btn-handsfree").textContent = "Gespräch: " + (handsfree ? "an" : "aus");
    $("btn-handsfree").classList.toggle("on", handsfree);
  }
  function showLogin(show) { $("login").hidden = !show; }

  // ----------------------------------------------------------------- API
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { ...(opts.headers || {}), Authorization: "Bearer " + token },
    });
    if (res.status === 401) { showLogin(true); throw new Error("Token ungültig"); }
    if (!res.ok) throw new Error((await res.text()) || res.statusText);
    return res;
  }

  async function send(text, voice) {
    if (!text.trim() || busy) return;
    add(text, "me");
    busy = true; setState("busy");
    const pending = add("…", "bot", "pending");
    try {
      const res = await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, voice }),
      });
      const data = await res.json();
      finish(pending, data.reply, data.error, voice);
    } catch (e) {
      finish(pending, "Fehler: " + e.message, true, false);
    }
  }

  async function sendAudio(blob) {
    busy = true; setState("busy");
    const pending = add("…", "bot", "pending");
    try {
      const fd = new FormData();
      fd.append("audio", blob, "aufnahme." + (blob.type.includes("mp4") ? "m4a" : "webm"));
      const data = await (await api("/api/voice", { method: "POST", body: fd })).json();
      if (data.transcript) log.insertBefore(Object.assign(document.createElement("div"),
        { className: "msg me", textContent: data.transcript }), pending);
      finish(pending, data.reply, data.error, true);
    } catch (e) {
      finish(pending, "Fehler: " + e.message, true, false);
    }
  }

  function finish(el, text, isErr, speakIt) {
    el.textContent = text || "(keine Antwort)";
    el.classList.remove("pending");
    if (isErr) el.classList.add("err");
    busy = false;
    setState(isErr ? "err" : "idle");
    if (speakIt && !muted && text) speak(text);
    else if (handsfree && speakIt) setTimeout(listen, 400);
  }

  // ------------------------------------------------------------- Sprache
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognizer = null, recorder = null;

  function pickVoice() {
    const voices = speechSynthesis.getVoices();
    return voices.find((v) => v.lang?.startsWith("de") && /google|natural|neural/i.test(v.name)) ||
      voices.find((v) => v.lang?.startsWith("de"));
  }

  async function speak(text) {
    setState("speaking");
    const done = () => { setState("idle"); if (handsfree) setTimeout(listen, 300); };
    if (serverTTS) {
      try {
        const res = await api("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }) });
        const audio = new Audio(URL.createObjectURL(await res.blob()));
        audio.onended = done; audio.onerror = done;
        await audio.play();
        return;
      } catch { /* Fallback auf Browser-Stimme */ }
    }
    if (!("speechSynthesis" in window)) return done();
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "de-DE";
    const v = pickVoice(); if (v) u.voice = v;
    u.rate = 1.05;
    u.onend = done; u.onerror = done;
    speechSynthesis.speak(u);
  }

  function listen() {
    if (busy || listening) return;
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    if (SR) return listenBrowser();
    return listenRecorder();
  }

  function listenBrowser() {
    recognizer = new SR();
    recognizer.lang = "de-DE";
    recognizer.interimResults = true;
    recognizer.maxAlternatives = 1;
    let finalText = "";
    const live = add("…", "me", "pending");
    recognizer.onresult = (e) => {
      let interim = "";
      for (const r of e.results) (r.isFinal ? (finalText = r[0].transcript) : (interim += r[0].transcript));
      live.textContent = finalText || interim || "…";
    };
    recognizer.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        hint.textContent = "Kein Mikrofon-Zugriff – nutze Server-Aufnahme";
        window.__noBrowserSR = true;
      }
    };
    recognizer.onend = () => {
      listening = false;
      live.remove();
      if (finalText.trim()) send(finalText.trim(), true);
      else { setState("idle"); if (window.__noBrowserSR) listenRecorder(); }
    };
    listening = true; setState("listening");
    recognizer.start();
  }

  async function listenRecorder() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks = [];
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        listening = false;
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        if (blob.size > 2000) sendAudio(blob); else setState("idle");
      };
      recorder.start();
      listening = true; setState("listening");
      hint.textContent = "Aufnahme läuft – nochmal tippen zum Senden";
      stopOnSilence(stream, () => recorder?.state === "recording" && recorder.stop());
    } catch (e) {
      hint.textContent = "Mikrofon nicht verfügbar: " + e.message;
    }
  }

  // Beendet die Aufnahme automatisch nach ~1,5 s Stille
  function stopOnSilence(stream, stop) {
    try {
      const ctx = new AudioContext();
      const an = ctx.createAnalyser(); an.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.fftSize);
      let heard = false, quietSince = performance.now();
      const started = performance.now();
      (function tick() {
        if (!listening) return ctx.close();
        an.getByteTimeDomainData(buf);
        const level = Math.max(...buf.map((b) => Math.abs(b - 128)));
        const now = performance.now();
        if (level > 12) { heard = true; quietSince = now; }
        if ((heard && now - quietSince > 1500) || now - started > 60000) { ctx.close(); return stop(); }
        requestAnimationFrame(tick);
      })();
    } catch { /* ohne AudioContext: manuell stoppen */ }
  }

  function stopListening() {
    if (recognizer && listening) recognizer.stop();
    if (recorder && recorder.state === "recording") recorder.stop();
  }

  // ------------------------------------------------------------- Events
  mic.addEventListener("click", () => (listening ? stopListening() : listen()));
  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    const t = $("text").value; $("text").value = "";
    send(t, false);
  });
  $("btn-mute").onclick = () => { muted = !muted; store.set("jarvis-muted", muted ? "1" : "0"); if (muted) window.speechSynthesis?.cancel(); refreshChips(); };
  $("btn-handsfree").onclick = () => { handsfree = !handsfree; store.set("jarvis-handsfree", handsfree ? "1" : "0"); refreshChips(); setState("idle"); };
  $("btn-reset").onclick = async () => {
    await api("/api/reset", { method: "POST" }).catch(() => {});
    log.innerHTML = ""; add("Neues Gespräch. Wie kann ich helfen?", "bot");
  };
  $("token-save").onclick = () => {
    token = $("token-input").value.trim(); store.set("jarvis-token", token);
    showLogin(false); init();
  };

  async function init() {
    refreshChips();
    if (!token) return showLogin(true);
    try {
      const h = await (await fetch("/api/health")).json();
      serverTTS = !!h.tts && store.get("jarvis-server-tts", "0") === "1";
      setState("idle");
    } catch { setState("err"); }
    if (!log.children.length) add("Hallo! Tippe auf das Mikrofon oder schreib mir eine Aufgabe.", "bot");
    if (params.get("voice") === "1") listen();
  }

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  window.speechSynthesis?.getVoices();
  init();
})();
