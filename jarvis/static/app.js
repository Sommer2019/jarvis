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

  // In der nativen Android-App gibt es window.JarvisAndroid (Sprache, Kontakte, Aktionen)
  const native = window.JarvisAndroid || null;
  const done = new Set();

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
      headers: {
        ...(opts.headers || {}),
        Authorization: "Bearer " + token,
        ...(native ? { "X-Jarvis-App": native.version() } : {}),
      },
    });
    if (res.status === 401) { showLogin(true); throw new Error("Token ungültig"); }
    if (!res.ok) throw new Error((await res.text()) || res.statusText);
    return res;
  }

  // Standort aus der App (nur wenn in den App-Einstellungen freigegeben), max. 2,5 s warten
  function getLocation() {
    if (!native || !native.locationEnabled || !native.locationEnabled()) return Promise.resolve(null);
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve(null), 2500);
      window.JarvisNative.onLocation = (json) => {
        clearTimeout(timer);
        try { resolve(json ? JSON.parse(json) : null); } catch { resolve(null); }
      };
      native.requestLocation();
    });
  }

  async function send(text, voice) {
    if (!text.trim() || busy) return;
    add(text, "me");
    busy = true; setState("busy");
    const pending = add("…", "bot", "pending");
    try {
      const location = await getLocation();
      const res = await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, voice, ...(location ? { location } : {}) }),
      });
      const data = await res.json();
      finish(pending, data.reply, data.error, voice);
      handleActions(data.actions);
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
      handleActions(data.actions);
    } catch (e) {
      finish(pending, "Fehler: " + e.message, true, false);
    }
  }

  // ------------------------------------------------------ Handy-Aktionen
  const ACTION_LABELS = {
    call: (p) => `📞 ${p.number} anrufen`,
    sms: (p) => `💬 SMS an ${p.number}`,
    whatsapp: (p) => `🟢 WhatsApp an ${p.number}`,
    navigate: (p) => `🧭 Navigation: ${p.destination}`,
    alarm: (p) => `⏰ Wecker ${String(p.hour).padStart(2, "0")}:${String(p.minute).padStart(2, "0")}`,
    timer: (p) => `⏱️ Timer ${Math.round(p.seconds / 60)} min`,
    open_url: (p) => `🔗 ${p.url}`,
  };

  // Browser-Fallback: Links, die Android/iOS selbst öffnen können
  function actionLink(a) {
    const p = a.params || {};
    const digits = (n) => String(n || "").replace(/[^\d+]/g, "");
    switch (a.type) {
      case "call": return "tel:" + digits(p.number);
      case "sms": return `sms:${digits(p.number)}?body=${encodeURIComponent(p.text || "")}`;
      case "whatsapp": return `https://wa.me/${digits(p.number).replace("+", "")}?text=${encodeURIComponent(p.text || "")}`;
      case "navigate": return "https://www.google.com/maps/dir/?api=1&destination=" + encodeURIComponent(p.destination || "");
      case "open_url": return p.url;
      default: return null; // Wecker/Timer gehen nur in der App
    }
  }

  async function ack(a) {
    done.add(a.id);
    await api(`/api/phone/actions/${a.id}/done`, { method: "POST" }).catch(() => {});
  }

  function handleActions(actions) {
    for (const a of actions || []) {
      if (done.has(a.id)) continue;
      if (native) {
        if (native.runAction(JSON.stringify(a))) { ack(a); add("✔ " + (ACTION_LABELS[a.type]?.(a.params) || a.type), "bot", "action"); }
        continue;
      }
      const href = actionLink(a);
      if (!href) { ack(a); add("Diese Aktion geht nur in der Jarvis-Android-App: " + a.type, "bot", "err"); continue; }
      const el = document.createElement("a");
      el.className = "msg bot action-btn";
      el.href = href; el.target = "_blank"; el.rel = "noopener";
      el.textContent = ACTION_LABELS[a.type]?.(a.params) || a.type;
      el.onclick = () => ack(a);
      log.appendChild(el);
      done.add(a.id);
      log.scrollTop = log.scrollHeight;
    }
  }

  async function pollActions() {
    try { handleActions((await (await api("/api/phone/actions")).json()).actions); } catch {}
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
    if (native) {
      window.JarvisNative.onSpeakDone = done;
      native.speak(text);
      return;
    }
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
    if (native) return listenNative();
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    if (SR) return listenBrowser();
    return listenRecorder();
  }

  // Android-Spracherkennung über die App-Brücke
  window.JarvisNative = window.JarvisNative || {};
  function listenNative() {
    native.stopSpeaking();
    const live = add("…", "me", "pending");
    const end = () => { listening = false; live.remove(); };
    Object.assign(window.JarvisNative, {
      onPartial: (t) => { live.textContent = t || "…"; },
      onResult: (t) => { end(); if (t && t.trim()) send(t.trim(), true); else setState("idle"); },
      onError: (msg) => { end(); setState("idle"); hint.textContent = msg; },
    });
    listening = true; setState("listening");
    native.startListening();
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
    if (native && listening) return native.stopListening();
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
  $("btn-mute").onclick = () => {
    muted = !muted; store.set("jarvis-muted", muted ? "1" : "0");
    if (muted) { native ? native.stopSpeaking() : window.speechSynthesis?.cancel(); }
    refreshChips();
  };
  $("btn-handsfree").onclick = () => { handsfree = !handsfree; store.set("jarvis-handsfree", handsfree ? "1" : "0"); refreshChips(); setState("idle"); };
  $("btn-reset").onclick = async () => {
    await api("/api/reset", { method: "POST" }).catch(() => {});
    log.innerHTML = ""; add("Neues Gespräch. Wie kann ich helfen?", "bot");
  };
  $("token-save").onclick = () => {
    token = $("token-input").value.trim(); store.set("jarvis-token", token);
    showLogin(false); init();
  };

  if (native) {
    const b = $("btn-settings");
    b.hidden = false;
    b.onclick = () => native.openSettings();
    window.JarvisNative.onContactsSynced = (n) => add(`📇 ${n} Handy-Kontakte mit Jarvis synchronisiert.`, "bot", "action");
  }

  async function init() {
    window.__jarvisReady = true; // Signal an die Android-App: Oberfläche läuft
    refreshChips();
    if (!token) return showLogin(true);
    try {
      const h = await (await fetch("/api/health")).json();
      serverTTS = !!h.tts && store.get("jarvis-server-tts", "0") === "1";
      setState("idle");
    } catch { setState("err"); }
    if (!log.children.length) add("Hallo! Tippe auf das Mikrofon oder schreib mir eine Aufgabe.", "bot");
    pollActions();
    if (params.get("voice") === "1") listen();
  }

  // Aktionen aus Telegram/WhatsApp abholen, sobald die App wieder sichtbar ist
  document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && pollActions());
  window.JarvisNative.listen = listen; // für den Launcher-Shortcut "Sprechen"

  if ("serviceWorker" in navigator && !native) navigator.serviceWorker.register("/sw.js").catch(() => {});
  window.speechSynthesis?.getVoices();
  init();
})();
