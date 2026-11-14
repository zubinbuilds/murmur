"""The whole path from microphone frames to an action on your behalf.

    band PCM -> whisper gate (+turn detection) -> speech engine (diarized)
             -> owner filter (only you) -> turn assembly -> "Hey Murmur" check -> agent
"""

from __future__ import annotations

import asyncio
import csv
import re
import time
import wave
from difflib import SequenceMatcher
from typing import Callable, Optional

from . import gemini, typer
from .agents import make_agent
from .config import RECORDINGS_DIR
from .gate import FRAME_MS, SAMPLE_RATE, GateSettings, WhisperGate, suggest_threshold
from .hub import Hub
from .owner import OwnerFilter
from .stt import STTError, make_engine
from .voice import make_voice
from .wake import WakeGate

CALIBRATION_FRAMES = 150  # 3 seconds
GATE_STATS_FRAMES = 50  # one "gate" event a second
# arrival_s: our clock since the WAV's first frame; wav_s: audio recorded by then; t, n: the kit's own
MOTION_COLUMNS = ("arrival_s", "wav_s", "t", "n", "ax", "ay", "az")
QUICK_CLOSE_S = 5.0  # a speech connection that closes sooner than this after opening is refused, not dropped
CLEANUP_PROMPT = ("A speech recognizer transcribed this whispered command and may have misheard words. "
                  "Fix misheard words, spelling and punctuation. Do not answer it, explain it or add anything. "
                  "Reply with the corrected command only.\n\nCommand: ")
TYPE_PREFIX = re.compile(r"type\b[\s,:]*(\S.*)", re.IGNORECASE | re.DOTALL)


class Pipeline:
    def __init__(self, cfg, hub: Hub, engine_factory: Optional[Callable] = None, agent=None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.hub = hub
        self.clock = clock
        self.engine_name = cfg.stt
        self._engine_factory = engine_factory or (lambda name: make_engine(name, cfg))
        self.gate = WhisperGate(GateSettings(
            threshold_db=cfg.gate_threshold_db, margin_db=cfg.gate_margin_db, hangover_ms=cfg.turn_silence_ms,
            whisper_only=cfg.whisper_only))
        self.owner = OwnerFilter()
        self.wake = WakeGate(cfg.wake_aliases, clock=clock)
        self.agent = agent or make_agent(cfg)
        self.voice = make_voice(cfg, hub)
        self.engine = None
        self.listening = False
        self.band_state = "starting"
        self.band_detail = ""
        self._pending: list = []
        self._flush_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._engine_started = 0.0
        self._calibration: Optional[dict] = None
        self._noise_dbs: list = []
        self._last_level = 0.0
        self._ignored = 0
        self._commands: set = set()  # running commands, held so a shielded task is not garbage-collected
        self._recording: Optional[wave.Wave_write] = None
        self._motion_file = None  # the .motion.csv beside the recording
        self._motion_csv = None
        self._recorded_frames = 0
        self._record_t0: Optional[float] = None  # clock() at the WAV's first frame

    # ---- lifecycle ----

    async def start(self) -> None:
        await self.agent.start(self._on_reply, self._on_agent_error)
        if self.cfg.record:
            RECORDINGS_DIR.mkdir(exist_ok=True)
            path = RECORDINGS_DIR / time.strftime("murmur-%Y%m%d-%H%M%S.wav")
            self._recording = wave.open(str(path), "wb")
            self._recording.setnchannels(1)
            self._recording.setsampwidth(2)
            self._recording.setframerate(SAMPLE_RATE)
            self._motion_file = open(path.with_suffix(".motion.csv"), "w", newline="")
            self._motion_csv = csv.writer(self._motion_file)
            self._motion_csv.writerow(MOTION_COLUMNS)
            print(f"Murmur: recording the raw band audio to {path}")
            self.note(f"Recording the raw band audio to {path}")
        self.status()

    async def shutdown(self) -> None:
        self.listening = False
        for task in (self._flush_task, self._reconnect_task, *self._commands):
            if task:
                task.cancel()
        await self._stop_engine()
        await self.agent.stop()
        if self.voice:
            await self.voice.close()
        if self._recording:
            self._recording.close()  # writes the WAV header's final length
            self._recording = None
            self._motion_file.close()
            self._motion_file = self._motion_csv = None

    def status(self) -> None:
        self.hub.emit({
            "type": "status",
            "listening": self.listening,
            "engine": self.engine_name,
            "band": self.band_state,
            "band_detail": self.band_detail,
            "dry_run": self.cfg.dry_run,
            "armed": self.wake.armed,
            "owner": self.owner.owner,
            "enrolling": self.owner.enrolling,
            "threshold": round(self.gate.threshold, 1),
            "calibrated": self.gate.s.calibrated,
            "whisper_only": self.gate.s.whisper_only,
            "wake_aliases": list(self.cfg.wake_aliases),
            "source": self.cfg.transport,
            "agent": self.cfg.agent,
            "voice": self.cfg.voice_device if self.voice else "",
        })

    def set_band_status(self, state: str, detail: str = "") -> None:
        self.band_state, self.band_detail = state, detail
        self.status()

    def note(self, text: str) -> None:
        self.hub.emit({"type": "note", "text": text})

    def error(self, where: str, message: str) -> None:
        self.hub.emit({"type": "error", "where": where, "message": message})

    # ---- audio in ----

    async def on_pcm(self, frame: bytes) -> None:
        if self._recording:
            if self._record_t0 is None:
                self._record_t0 = self.clock()
            self._recording.writeframes(frame)
            self._recorded_frames += 1
        out = self.gate.process(frame)
        stats = self.gate.stats
        if stats["frames"] >= GATE_STATS_FRAMES:
            self.hub.emit({"type": "gate", "frames": stats["frames"], "above": stats["above"],
                           "voiced": stats["voiced"], "passed": stats["passed"],
                           "floor": round(self.gate.floor_db, 1), "threshold": round(self.gate.threshold, 1)})
            stats.clear()
        if self._calibration is not None:
            self._collect(out.db)
        now = self.clock()
        if now - self._last_level >= 0.1:
            self._last_level = now
            self.hub.emit({"type": "level", "db": round(out.db, 1), "open": out.open,
                           "threshold": round(self.gate.threshold, 1)})
        engine = self.engine
        if engine is None or not self.listening:
            return
        try:
            await engine.send_audio(out.frame)
            if out.turn_end:
                await engine.end_turn()
        except Exception as e:
            self.error("stt", f"Lost the speech connection: {e}")
            self._begin_reconnect(engine)

    def on_motion(self, samples) -> None:
        """Accelerometer events from the band (one kit Message or a list): recorded beside the audio, else unused."""
        if self._motion_csv is None or self._record_t0 is None:
            return  # nothing to line them up with before the first audio frame
        arrival = round(self.clock() - self._record_t0, 4)
        wav_s = round(self._recorded_frames * FRAME_MS / 1000, 3)
        for s in [samples] if isinstance(samples, dict) else samples:
            self._motion_csv.writerow([arrival, wav_s, s.get("t"), s.get("n"), s.get("ax"), s.get("ay"), s.get("az")])

    # ---- listening on/off, engine choice ----

    async def toggle_listening(self) -> None:
        await self.set_listening(not self.listening)

    async def set_listening(self, on: bool) -> None:
        if on == self.listening:
            return
        if on:
            if not await self._start_engine():
                return
            self.listening = True
        else:
            self.listening = False
            self.wake.disarm()
            if self._flush_task:
                self._flush_task.cancel()
            self._pending.clear()  # whatever was half-heard when you switched off is not acted on
            await self._stop_engine()
        self.status()

    async def set_engine(self, name: str) -> None:
        self.engine_name = name
        if self.listening:
            await self._stop_engine()
            if not await self._start_engine():
                self.listening = False
        self.status()

    async def _start_engine(self) -> bool:
        try:
            engine = self._engine_factory(self.engine_name)
            self.engine = engine  # set first: the engine may report "ready" before start() returns
            self.owner.reset()  # speaker ids restart with every speech session
            await engine.start(lambda ev, e=engine: self._on_stt_event(ev, e))
            self._engine_started = self.clock()
            return True
        except STTError as e:
            self.engine = None
            self.error("stt", str(e))
            return False
        except Exception as e:
            self.engine = None
            self.error("stt", f"Could not start {self.engine_name}: {e}")
            return False

    async def _stop_engine(self) -> None:
        engine, self.engine = self.engine, None
        if engine is not None:
            try:
                await engine.stop()
            except Exception:
                pass

    def _begin_reconnect(self, dead) -> None:
        if dead is not self.engine or not self.listening:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect(dead))

    async def _reconnect(self, dead) -> None:
        self.engine = None
        try:
            await dead.stop()
        except Exception:
            pass
        if self.clock() - self._engine_started < QUICK_CLOSE_S:  # refused as it opened: a bad key, not a dropped line
            self.listening = False
            self.error("stt", "The speech service closed the connection as soon as it opened "
                              "(see the error above). Listening is off.")
            self.status()
            return
        for attempt in range(1, 6):
            await asyncio.sleep(1.5 * attempt)
            if not self.listening:
                return
            if await self._start_engine():
                self.note("Speech connection restored.")
                self.status()  # the restart cleared the owner
                return
        self.listening = False
        self.error("stt", "Could not reconnect to the speech service. Listening is off.")
        self.status()

    # ---- speech results in ----

    async def _on_stt_event(self, ev: dict, engine) -> None:
        if engine is not self.engine:
            return  # a stale engine we already replaced
        kind = ev["type"]
        if kind == "partial":
            runs = [r for r in self.owner.split(ev.get("words") or [], ev.get("text", "")) if r["mine"]]
            text = " ".join(r["text"] for r in runs)
            if text:
                self.hub.emit({"type": "partial", "text": text})
        elif kind == "final":
            self._on_final(ev)
        elif kind == "merge":
            self.owner.apply_merge(ev.get("from"), ev.get("to"))
            self.status()  # a merge can rename the owner
        elif kind == "error":
            self.error("stt", ev.get("message", "speech error"))
        elif kind == "closed":
            self.error("stt", "The speech service closed the connection; reconnecting.")
            self._begin_reconnect(engine)

    def _on_final(self, ev: dict) -> None:
        words = ev.get("words") or []
        owner_before = self.owner.owner
        finished = self.owner.observe(words)
        for run in self.owner.split(words, ev.get("text", ""), strict=True):
            if run["mine"]:
                self.hub.emit({"type": "transcript", "speaker": "you", "speaker_id": run["speaker"],
                               "text": run["text"]})
                self._pending.append(run["text"])
            else:
                self._ignored += len(run["text"].split())
                self.hub.emit({"type": "ignored", "speaker_id": run["speaker"],
                               "words": len(run["text"].split())})
        if finished:
            self.note(f"Enrolled: you are {self.owner.owner}.")
            self.status()
        elif self.owner.owner != owner_before:
            self.status()
        if self._pending:
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
        self._flush_task = asyncio.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        await asyncio.sleep(self.cfg.settle_s)
        waited = 0.0
        while self.gate.is_open and waited < 5.0:  # you are still talking
            await asyncio.sleep(0.2)
            waited += 0.2
        await self.flush_turn()

    async def flush_turn(self) -> None:
        text = " ".join(self._pending).strip()
        self._pending.clear()
        if not text:
            return
        self.hub.emit({"type": "turn", "text": text})
        command = self.wake.feed(text)
        self.status()
        if command:
            # a new final cancels _flush_task; the command it already took must still run to the end
            task = asyncio.ensure_future(self.run_command(command))
            self._commands.add(task)
            task.add_done_callback(self._commands.discard)
            await asyncio.shield(task)

    async def run_command(self, command: str) -> None:
        typed = TYPE_PREFIX.match(command)
        if typed:
            await self._type(typed.group(1))  # dictation is typed as heard, not rewritten by cleanup
            return
        if self.cfg.cleanup and self.cfg.gemini_key:
            command = await self._clean(command)
        self.hub.emit({"type": "command", "text": command, "state": "sending", "dry_run": self.cfg.dry_run})
        try:
            result = await self.agent.send(command)
        except Exception as e:
            self.hub.emit({"type": "command", "text": command, "state": "error", "detail": str(e)})
            return
        self.hub.emit({"type": "command", "text": command, "state": result.state, "detail": result.detail})

    async def _type(self, text: str) -> None:
        try:
            await asyncio.to_thread(typer.type_text, text)
        except Exception as e:
            self.hub.emit({"type": "typed", "text": text, "state": "error", "detail": str(e)})
            return
        self.hub.emit({"type": "typed", "text": text, "state": "typed", "detail": "Typed into the app in front."})

    async def _clean(self, raw: str) -> str:
        """raw with misheard words and punctuation fixed by Gemini, or raw itself if Gemini fails or adds text."""
        event = {"type": "cleanup", "raw": raw, "text": raw}
        try:
            text = (await gemini.ask(CLEANUP_PROMPT + raw, self.cfg.gemini_key, self.cfg.gemini_model)).strip()
            if not text:
                event["error"] = "Gemini sent an empty reply; kept what you said."
            elif len(text) > 2 * len(raw) + 20:
                event["error"] = "Gemini's reply was much longer than the command; kept what you said."
            elif SequenceMatcher(None, raw.lower(), text.lower()).ratio() < 0.6:  # an answer, not a correction
                event["error"] = "Gemini's reply was not a correction of the command; kept what you said."
            else:
                event["text"] = text
        except Exception as e:
            event["error"] = f"Cleanup failed, kept what you said: {e}"
        self.hub.emit(event)
        return event["text"]

    def _on_reply(self, text: str, source: str = "instinct") -> None:
        self.hub.emit({"type": "reply", "text": text, "from": source})
        if self.voice:
            self.voice.say(text)

    def _on_agent_error(self, message: str) -> None:
        self.error("agent", message)

    async def inject(self, text: str, speaker: str = "speaker_0") -> None:
        """Feed in one complete turn as if the speech service had returned it, and act on it now.

        For testing without speech. It does not wait for the settle timer and does not merge
        with transcript pieces that are still pending from real audio.
        """
        if self._flush_task:
            self._flush_task.cancel()
        self._pending.clear()
        words = [{"text": t, "speaker": speaker} for t in text.split()]
        self._on_final({"text": text, "words": words})
        if self._flush_task:
            self._flush_task.cancel()
        await self.flush_turn()

    # ---- calibration and enrollment ----

    def calibrate(self, phase: str) -> None:
        if self._calibration is not None:
            return
        if phase not in ("noise", "whisper"):
            self.error("calibration", "Calibration has two steps: noise, then whisper.")
            return
        self._calibration = {"phase": phase, "dbs": []}
        self.hub.emit({"type": "calibration", "phase": phase, "state": "recording", "seconds": 3})

    def _collect(self, db: float) -> None:
        c = self._calibration
        assert c is not None
        c["dbs"].append(db)
        if len(c["dbs"]) < CALIBRATION_FRAMES:
            return
        self._calibration = None
        if c["phase"] == "noise":
            self._noise_dbs = c["dbs"]
            self.hub.emit({"type": "calibration", "phase": "noise", "state": "done"})
            return
        if not self._noise_dbs:
            self.hub.emit({"type": "calibration", "phase": "whisper", "state": "failed",
                           "message": "Record the room noise first."})
            return
        threshold = suggest_threshold(self._noise_dbs, c["dbs"])
        if threshold is None:
            self.hub.emit({"type": "calibration", "phase": "whisper", "state": "failed",
                           "message": "Your whisper is too close to the room noise for a level gate. "
                                      "Move the band closer to your mouth, or turn the music down."})
            return
        self.gate.set_threshold(threshold)
        self.hub.emit({"type": "calibration", "phase": "whisper", "state": "done", "threshold": threshold})
        self.status()

    def set_threshold(self, db: float) -> None:
        self.gate.set_threshold(db)
        self.status()

    def set_whisper_only(self, on: bool) -> None:
        self.gate.s.whisper_only = bool(on)
        self.status()

    def enroll(self) -> None:
        self.owner.start_enrollment()
        self.note("Enrolling: whisper a sentence or two.")
        self.status()
