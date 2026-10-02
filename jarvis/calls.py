"""Jarvis telefoniert selbst – über die Zweit-SIM im Handy, Ton am PC.

Ablauf:
1. Jarvis (Tool `phone_agent_call`) legt einen Anruf an → data/calls.json und
   schickt der Handy-App die Aktion „agent_call“.
2. Die App wählt über die gewählte SIM und meldet „dialing“ bzw. später „ended“.
   Der Gesprächston läuft per Bluetooth/Kabel zum PC (CALL_AUDIO_IN/OUT).
3. Der CallAgent hier im Server hört zu (Pausen-Erkennung + Whisper), fragt
   Claude im schnellen Modus (ohne Tools), spricht mit Piper („Thorsten“) und
   schickt dem Nutzer am Ende das Ergebnis.

Am Gesprächsanfang stellt sich Jarvis IMMER als KI-Assistent vor (fest im Code).
Es wird kein Ton aufgezeichnet, nur ein Text-Protokoll.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import queue
import random
import re
import time
import uuid
import wave
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

log = logging.getLogger("jarvis.calls")

RATE = 16000          # Whisper-Abtastrate
FRAME = 480           # 30 ms bei 16 kHz
ACTIVE = ("queued", "dialing", "active")
DAILY_LIMIT = 10

# Notrufe, Sonder- und teure Mehrwertnummern ruft Jarvis nie an
BLOCKED = re.compile(r"^(\+?49)?0?(110|112|115|116\d*|118\d*|19\d{3}|0900|0137|0138|0180|0190|0191|0192|0193|0194|0199)")


def normalize_number(number: str) -> str:
    n = re.sub(r"[^\d+]", "", number or "")
    if n.startswith("00"):
        n = "+" + n[2:]
    return n


def check_number(number: str) -> str:
    n = normalize_number(number)
    digits = n.lstrip("+")
    if len(digits) < 6:
        raise ValueError("Ungültige oder zu kurze Nummer – Notrufe und Kurzwahlen sind gesperrt")
    local = re.sub(r"^\+49", "0", n)
    if BLOCKED.match(n) or BLOCKED.match(local):
        raise ValueError("Diese Nummer (Notruf/Sonder-/Mehrwertnummer) ruft Jarvis nicht an")
    return n


# ------------------------------------------------------------------ Speicher
class CallStore:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "calls.json"

    @contextmanager
    def _calls(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = open(self.path.with_suffix(".lock"), "w")
        try:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                calls = json.loads(self.path.read_text()) if self.path.exists() else []
            except json.JSONDecodeError:
                calls = []
            yield calls
            calls[:] = calls[-50:]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(calls, ensure_ascii=False, indent=1))
            os.replace(tmp, self.path)
        finally:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def create(self, number: str, goal: str, name: str = "", max_minutes: int = 6) -> dict:
        number = check_number(number)
        if not goal.strip():
            raise ValueError("goal fehlt: Was soll Jarvis im Gespräch erreichen?")
        now = time.time()
        call = {"id": uuid.uuid4().hex[:8], "number": number, "name": name[:80], "goal": goal[:1500],
                "max_minutes": max(1, min(int(max_minutes or 6), 15)), "status": "queued",
                "created": now, "started": None, "ended": None, "transcript": [], "result": "", "error": ""}
        with self._calls() as calls:
            if any(c["status"] in ACTIVE for c in calls):
                raise RuntimeError("Es läuft schon ein Anruf – erst danach den nächsten starten")
            if sum(1 for c in calls if now - c["created"] < 86400) >= DAILY_LIMIT:
                raise RuntimeError(f"Maximal {DAILY_LIMIT} Anrufe pro Tag")
            calls.append(call)
        return call

    def get(self, call_id: str) -> dict | None:
        with self._calls() as calls:
            return next((dict(c) for c in calls if c["id"] == call_id), None)

    def latest(self) -> dict | None:
        with self._calls() as calls:
            return dict(calls[-1]) if calls else None

    def all(self) -> list[dict]:
        with self._calls() as calls:
            return [dict(c) for c in calls]

    def update(self, call_id: str, **fields) -> dict | None:
        with self._calls() as calls:
            for c in calls:
                if c["id"] == call_id:
                    c.update(fields)
                    return dict(c)
        return None

    def add_turn(self, call_id: str, who: str, text: str) -> None:
        with self._calls() as calls:
            for c in calls:
                if c["id"] == call_id:
                    c["transcript"].append({"who": who, "text": text, "t": round(time.time())})

    def report(self, call_id: str, state: str, detail: str = "") -> dict | None:
        """Statusmeldung der Handy-App (dialing / ended / failed)."""
        call = self.get(call_id)
        if not call:
            return None
        if state == "dialing" and call["status"] == "queued":
            return self.update(call_id, status="dialing", started=time.time())
        if state in ("ended", "failed") and call["status"] in ACTIVE:
            return self.update(call_id, status="ended" if state == "ended" else "failed",
                               ended=time.time(), error=detail if state == "failed" else call["error"],
                               hangup_by=detail or "gegenüber/handy")
        return call


# --------------------------------------------------------- Pausen-Erkennung
class Segmenter:
    """Schneidet aus dem Ton einzelne Äußerungen: Start bei Lautstärke über dem
    Grundrauschen, Ende nach `end_silence` Sekunden Ruhe."""

    def __init__(self, end_silence: float = 0.8, min_speech: float = 0.3, max_len: float = 20.0,
                 threshold: float = 0.012):
        self.end_frames = int(end_silence * RATE / FRAME)
        self.min_frames = int(min_speech * RATE / FRAME)
        self.max_frames = int(max_len * RATE / FRAME)
        self.threshold = threshold
        self.floor = threshold / 3
        self.reset()

    def reset(self) -> None:
        self.frames: list = []
        self.loud = 0
        self.quiet = 0
        self.speaking = False
        self.voiced = 0

    def feed(self, frame):
        """Nimmt einen 30-ms-Block (float32) – gibt eine fertige Äußerung zurück oder None."""
        import numpy as np

        rms = float(np.sqrt(np.mean(np.square(frame)))) if len(frame) else 0.0
        if not self.speaking:
            self.floor = 0.95 * self.floor + 0.05 * rms  # Grundrauschen nachführen
        level = max(self.threshold, self.floor * 3)
        if not self.speaking:
            self.frames = (self.frames + [frame])[-10:]  # etwas Vorlauf behalten
            self.loud = self.loud + 1 if rms > level else 0
            if self.loud >= 3:
                self.speaking, self.quiet, self.voiced = True, 0, self.loud
            return None
        self.frames.append(frame)
        if rms > level:
            self.quiet = 0
            self.voiced += 1
        else:
            self.quiet += 1
        if self.quiet >= self.end_frames or len(self.frames) >= self.max_frames:
            frames, self.frames = self.frames, []
            speech = self.voiced
            self.reset()
            if speech >= self.min_frames:
                return np.concatenate(frames)
        return None


def resample(x, src: int, dst: int):
    import numpy as np

    if src == dst or len(x) == 0:
        return x.astype(np.float32)
    n = int(round(len(x) * dst / src))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def wav_to_float(data: bytes):
    import numpy as np

    with wave.open(io.BytesIO(data)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("Nur 16-Bit-WAV")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, rate


# ------------------------------------------------------------------ Audio
def find_device(spec: str, kind: str) -> int:
    import sounddevice as sd

    devices = sd.query_devices()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    if spec.strip().isdigit():
        return int(spec)
    for i, d in enumerate(devices):
        if spec.lower() in d["name"].lower() and d[key] > 0:
            return i
    raise RuntimeError(f"Audiogerät '{spec}' ({kind}) nicht gefunden – `jarvis call-setup` zeigt alle")


class SoundDeviceAudio:
    """Hört auf CALL_AUDIO_IN (Ton vom Gegenüber) und spricht auf CALL_AUDIO_OUT (ins Gespräch)."""

    def __init__(self, in_spec: str, out_spec: str):
        import sounddevice as sd

        self.sd = sd
        self.in_dev = find_device(in_spec, "input")
        self.out_dev = find_device(out_spec, "output")
        self.in_rate = int(sd.query_devices(self.in_dev)["default_samplerate"])
        self.out_rate = int(sd.query_devices(self.out_dev)["default_samplerate"])
        self.blocks: queue.Queue = queue.Queue()
        self.muted = False
        self.stream = sd.InputStream(device=self.in_dev, channels=1, samplerate=self.in_rate,
                                     dtype="float32", callback=self._on_audio)
        self.stream.start()

    def _on_audio(self, data, frames, t, status):
        if not self.muted:
            self.blocks.put(data[:, 0].copy())

    def frames(self, timeout: float):
        """Liefert 30-ms-Blöcke (16 kHz) bis `timeout` Sekunden ohne Daten vergehen."""
        import numpy as np

        buf = np.zeros(0, dtype=np.float32)
        while True:
            try:
                block = self.blocks.get(timeout=timeout)
            except queue.Empty:
                return
            buf = np.concatenate([buf, resample(block, self.in_rate, RATE)])
            while len(buf) >= FRAME:
                yield buf[:FRAME]
                buf = buf[FRAME:]

    def play(self, samples, rate: int) -> None:
        self.muted = True  # eigene Stimme nicht als Gegenüber erkennen
        try:
            self.sd.play(resample(samples, rate, self.out_rate), self.out_rate, device=self.out_dev, blocking=True)
            time.sleep(0.15)
        finally:
            with self.blocks.mutex:
                self.blocks.queue.clear()
            self.muted = False

    def close(self) -> None:
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:  # noqa: BLE001
            pass


def listen(audio, timeout: float, should_stop=lambda: False, segmenter: Segmenter | None = None):
    """Wartet auf die nächste Äußerung des Gegenübers (blockierend; im Thread aufrufen).
    None = nichts gesagt innerhalb von `timeout` Sekunden oder abgebrochen."""
    seg = segmenter or Segmenter()
    end = time.time() + timeout
    for frame in audio.frames(timeout=1.0):
        utt = seg.feed(frame)
        if utt is not None:
            return utt
        if should_stop() or (not seg.speaking and time.time() > end):
            return None
    return None


# ----------------------------------------------------------------- Gespräch
HANGUP = re.compile(r"\[\s*AUFLEGEN\s*\]", re.I)
RESULT = re.compile(r"\[\s*ERGEBNIS\s*:\s*(.*?)\]", re.I | re.S)
FILLERS = ["Mhm, einen kleinen Moment.", "Ja, Moment bitte.", "Einen Augenblick."]


def parse_reply(text: str) -> tuple[str, bool, str]:
    """Antwort von Claude → (zu sprechender Text, auflegen?, Ergebnis für den Nutzer)."""
    result = RESULT.search(text or "")
    spoken = RESULT.sub("", text or "")
    hangup = bool(HANGUP.search(spoken))
    spoken = HANGUP.sub("", spoken)
    spoken = re.sub(r"[*_#`>|]+", " ", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken, hangup, (result.group(1).strip() if result else "")


def disclosure(owner: str) -> str:
    who = f"im Auftrag von {owner}" if owner else "im Auftrag meines Nutzers"
    return f"Guten Tag, hier spricht Jarvis, ein KI-Assistent, {who}."


def call_rules(call: dict, owner: str) -> str:
    owner = owner or "dem Nutzer"
    target = call.get("name") or call["number"]
    return f"""[TELEFONAT – du bist Jarvis, der KI-Assistent von {owner}, und führst gerade ein echtes Telefonat
mit {target} ({call['number']}). Deine Antworten werden vorgelesen, das Gegenüber hört dich.
Ziel des Anrufs (Auftrag von {owner}): {call['goal']}
Regeln:
- Sprich natürlich, freundlich und knapp: 1–3 kurze Sätze pro Antwort, gesprochene Sprache, keine Listen,
  kein Markdown, keine Emojis, Uhrzeiten/Daten ausgeschrieben wie man sie sagt.
- Du hast dich bereits als KI-Assistent vorgestellt. Fragt jemand, bist du ehrlich: Du bist eine KI.
- Gib nur Informationen weiter, die im Ziel stehen oder für das Ziel nötig sind. Keine Zahlungen, Verträge,
  Bankdaten, Passwörter oder Zusagen über das Ziel hinaus – sag dann, dass {owner} sich dazu meldet.
- Möchte das Gegenüber nicht mit einer KI sprechen oder bittet um Beenden: kurz entschuldigen,
  verabschieden, auflegen.
- Kannst du etwas nicht klären, sag freundlich, dass {owner} sich meldet.
- Ist das Ziel erreicht oder das Gespräch vorbei: verabschiede dich und schreib ans Ende
  [AUFLEGEN] [ERGEBNIS: kurze Zusammenfassung für {owner}: Was wurde vereinbart/erfahren, offene Punkte]
- Hörst du eine Mailbox/Ansage: hinterlasse eine sehr kurze Nachricht mit dem Anliegen, dann [AUFLEGEN] [ERGEBNIS: …].
Nachrichten vom Gegenüber kommen als „Gegenüber: …“ (automatisch transkribiert, kann Hörfehler enthalten).]"""


class CallAgent:
    def __init__(self, cfg, brain, store: CallStore | None = None, phone=None,
                 audio_factory=None, stt=None, tts=None):
        from .phone import PhoneStore

        self.cfg = cfg
        self.brain = brain
        self.store = store or CallStore(cfg.data_dir)
        self.phone = phone or PhoneStore(cfg.data_dir)
        self.audio_factory = audio_factory or (lambda: SoundDeviceAudio(cfg.call_audio_in, cfg.call_audio_out))
        self.stt = stt or self._stt
        self.tts = tts or self._tts
        self.owner = cfg.user_name
        self.poll = 1.0
        self.answer_timeout = 45   # so lange auf das erste Wort warten (klingeln)
        self.turn_timeout = 15     # Stille im Gespräch, bevor Jarvis nachfragt

    # Standard-Umsetzungen (Whisper / Piper)
    def _stt(self, samples) -> str:
        from .stt import transcribe_pcm

        return transcribe_pcm(samples, self.cfg)

    def _tts(self, text: str):
        from . import tts

        if not tts.available(self.cfg):
            raise RuntimeError("Keine Stimme eingerichtet – `jarvis voice-setup` ausführen")
        return wav_to_float(tts._synthesize_wav(text, self.cfg))

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("Anruf-Schleife")
            await asyncio.sleep(self.poll)

    async def tick(self) -> None:
        for call in self.store.all():
            if call["status"] == "queued" and time.time() - call["created"] > 120:
                self.store.update(call["id"], status="failed", ended=time.time(),
                                  error="Das Handy hat den Anruf nicht gestartet (App offen? Berechtigungen?)")
                self.notify(self.store.get(call["id"]))
            elif call["status"] == "dialing":
                await self.handle(call)

    def stopped(self, call_id: str) -> bool:
        c = self.store.get(call_id)
        return not c or c["status"] not in ACTIVE or c.get("abort")

    async def say(self, audio, text: str) -> None:
        samples, rate = await asyncio.to_thread(self.tts, text)
        await asyncio.to_thread(audio.play, samples, rate)

    async def think(self, audio, call: dict, message: str, first: bool) -> str:
        task = asyncio.create_task(self.brain.ask(
            message, f"call:{call['id']}", channel="telefonat", lite=True, model=self.cfg.call_model,
            context=call_rules(call, self.owner) if first else ""))
        done, _ = await asyncio.wait({task}, timeout=4.0)
        if not done and not first:
            await self.say(audio, random.choice(FILLERS))  # Denkpause überbrücken
        reply = await task
        if reply.is_error:
            raise RuntimeError(reply.text)
        return reply.text

    async def handle(self, call: dict) -> None:
        cid = call["id"]
        self.store.update(cid, status="active")
        log.info("Anruf %s an %s gestartet", cid, call["number"])
        audio = None
        hung_up = False
        try:
            audio = await asyncio.to_thread(self.audio_factory)
            deadline = time.time() + call["max_minutes"] * 60
            heard = await self.hear(audio, cid, self.answer_timeout)
            if heard is None:
                raise RuntimeError("Niemand hat abgenommen (oder kein Ton am PC – Bluetooth verbunden?)")
            opener = disclosure(self.owner)
            self.store.add_turn(cid, "gegenüber", heard)
            text = await self.think(audio, call, f"Gegenüber: {heard}\n(Die Vorstellung „{opener}“ wird "
                                    f"automatisch vorher gesprochen – nicht wiederholen, nicht nochmal begrüßen, sag direkt, worum es geht.)", first=True)
            spoken, hang, result = parse_reply(text)
            spoken = f"{opener} {spoken.replace(opener, '').strip()}".strip()  # Vorstellung genau einmal
            silence = 0
            while True:
                self.store.add_turn(cid, "jarvis", spoken)
                await self.say(audio, spoken)
                if result:
                    self.store.update(cid, result=result)
                if hang or self.stopped(cid):
                    break
                if time.time() > deadline:
                    await self.say(audio, "Ich muss das Gespräch leider beenden. Vielen Dank und auf Wiederhören!")
                    self.store.update(cid, error="Zeitlimit erreicht")
                    break
                heard = await self.hear(audio, cid, self.turn_timeout)
                if self.stopped(cid):
                    break
                if heard is None:
                    silence += 1
                    if silence >= 2:
                        break
                    message = "(Das Gegenüber sagt nichts. Frag kurz nach, ob man dich hört.)"
                else:
                    silence = 0
                    self.store.add_turn(cid, "gegenüber", heard)
                    message = f"Gegenüber: {heard}"
                spoken, hang, result = parse_reply(await self.think(audio, call, message, first=False))
                if not spoken:
                    spoken = "Vielen Dank, auf Wiederhören!" if hang else "Entschuldigung, können Sie das wiederholen?"
            hung_up = True
        except Exception as e:  # noqa: BLE001
            log.warning("Anruf %s: %s", cid, e)
            self.store.update(cid, error=str(e)[:300])
        finally:
            if audio is not None:
                audio.close()
            current = self.store.get(cid) or call
            if current["status"] in ACTIVE:
                self.phone.queue("agent_hangup", {"call_id": cid})
                self.store.update(cid, status="ended" if hung_up else "failed", ended=time.time())
            await self.finish(cid)

    async def hear(self, audio, cid: str, timeout: float) -> str | None:
        """Nächste verständliche Äußerung als Text (leere Transkripte, z.B. Freizeichen, überspringen)."""
        end = time.time() + timeout
        while time.time() < end and not self.stopped(cid):
            utt = await asyncio.to_thread(listen, audio, end - time.time(), lambda: self.stopped(cid))
            if utt is None:
                return None
            text = await asyncio.to_thread(self.stt, utt)
            if text:
                return text
        return None

    async def finish(self, cid: str) -> None:
        call = self.store.get(cid)
        if not call:
            return
        if not call.get("result") and len(call["transcript"]) > 1:
            # Kein [ERGEBNIS] bekommen → kurz zusammenfassen lassen
            try:
                log_text = "\n".join(f"{t['who']}: {t['text']}" for t in call["transcript"])
                reply = await self.brain.ask(
                    f"Fasse dieses Telefonat in 1–2 Sätzen für {self.owner or 'den Nutzer'} zusammen "
                    f"(Ziel war: {call['goal']}):\n{log_text}", f"call:{cid}:summary", lite=True,
                    model=self.cfg.call_model)
                if not reply.is_error:
                    self.store.update(cid, result=reply.text.strip())
            except Exception:  # noqa: BLE001
                log.exception("Zusammenfassung")
        self.save_protocol(self.store.get(cid))
        self.notify(self.store.get(cid))

    def save_protocol(self, call: dict) -> None:
        folder = self.cfg.workspace / "anrufe"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H%M", time.localtime(call["created"]))
        lines = [f"# Anruf bei {call.get('name') or call['number']} ({stamp})", "",
                 f"**Ziel:** {call['goal']}", f"**Ergebnis:** {call.get('result') or call.get('error') or '–'}", ""]
        lines += [f"- **{t['who']}:** {t['text']}" for t in call["transcript"]]
        (folder / f"{stamp}_{call['id']}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def notify(self, call: dict | None) -> None:
        if not call:
            return
        who = call.get("name") or call["number"]
        text = call.get("result") or call.get("error") or "Gespräch beendet."
        title = f"📞 Anruf bei {who}" + (" – fehlgeschlagen" if call["status"] == "failed" else "")
        try:
            self.phone.queue("notify", {"title": title, "text": text})
        except Exception:  # noqa: BLE001
            log.exception("Benachrichtigung")
