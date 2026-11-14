"""The whole path from microphone frames to an action on your behalf.

    band PCM -> whisper gate (+turn detection) -> speech engine (diarized)
             -> owner filter (only you) -> turn assembly -> "Hey Vocal" check -> agent
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from .agents import make_agent
from .gate import GateSettings, WhisperGate, suggest_threshold
from .hub import Hub
from .owner import OwnerFilter
from .stt import STTError, make_engine
from .wake import WakeGate

CALIBRATION_FRAMES = 150  # 3 seconds


class Pipeline:
    def __init__(self, cfg, hub: Hub, engine_factory: Optional[Callable] = None, agent=None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.hub = hub
        self.clock = clock
        self.engine_name = cfg.stt
        self._engine_factory = engine_factory or (lambda name: make_engine(name, cfg))
        self.gate = WhisperGate(GateSettings(
            threshold_db=cfg.gate_threshold_db, margin_db=cfg.gate_margin_db, hangover_ms=cfg.turn_silence_ms))
        self.owner = OwnerFilter()
        self.wake = WakeGate(cfg.wake_aliases, clock=clock)
        self.agent = agent or make_agent(cfg)
        self.engine = None
        self.listening = False
        self.band_state = "starting"
        self.band_detail = ""
        self._pending: list = []
        self._flush_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._calibration: Optional[dict] = None
        self._noise_dbs: list = []
        self._last_level = 0.0
        self._ignored = 0

    # ---- lifecycle ----

    async def start(self) -> None:
        await self.agent.start(self._on_reply, self._on_agent_error)
        self.status()

    async def shutdown(self) -> None:
        self.listening = False
        for task in (self._flush_task, self._reconnect_task):
            if task:
                task.cancel()
        await self._stop_engine()
        await self.agent.stop()

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
            "wake_aliases": list(self.cfg.wake_aliases),
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
        out = self.gate.process(frame)
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
        for attempt in range(1, 6):
            await asyncio.sleep(1.5 * attempt)
            if not self.listening:
                return
            if await self._start_engine():
                self.note("Speech connection restored.")
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
        elif kind == "error":
            self.error("stt", ev.get("message", "speech error"))
        elif kind == "closed":
            self.error("stt", "The speech service closed the connection; reconnecting.")
            self._begin_reconnect(engine)

    def _on_final(self, ev: dict) -> None:
        words = ev.get("words") or []
        finished = self.owner.observe(words)
        for run in self.owner.split(words, ev.get("text", "")):
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
        elif self.owner.owner and self._ignored == 0:
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
            await self.run_command(command)

    async def run_command(self, command: str) -> None:
        self.hub.emit({"type": "command", "text": command, "state": "sending", "dry_run": self.cfg.dry_run})
        try:
            result = await self.agent.send(command)
        except Exception as e:
            self.hub.emit({"type": "command", "text": command, "state": "error", "detail": str(e)})
            return
        self.hub.emit({"type": "command", "text": command, "state": result.state, "detail": result.detail})

    def _on_reply(self, text: str) -> None:
        self.hub.emit({"type": "reply", "text": text})

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

    def enroll(self) -> None:
        self.owner.start_enrollment()
        self.note("Enrolling: whisper a sentence or two.")
        self.status()
