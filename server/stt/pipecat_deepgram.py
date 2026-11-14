"""Deepgram live transcription through a Pipecat pipeline (Pipecat 1.16, DeepgramSTTService).

The same service as deepgram.py, reached through Pipecat instead of a hand-written websocket.
Pipecat sends Deepgram "Finalize" when it sees VADUserStoppedSpeakingFrame, so end_turn queues one.
With diarize on, Pipecat splits a final into one frame per speaker run, each carrying the whole
Deepgram result, so the collector reads each result object once and parses it like deepgram.py does.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from .base import OnEvent, STTEngine, STTError
from .deepgram import parse_message

RATE = 16000


class PipecatDeepgramSTT(STTEngine):
    name = "pipecat"

    def __init__(self, api_key: str, make_service: Optional[Callable] = None) -> None:
        self.api_key = api_key
        self._make_service = make_service or _deepgram_service  # tests pass a fake processor
        self._worker = None
        self._task: Optional[asyncio.Task] = None
        self._on_event: Optional[OnEvent] = None
        self._stopping = False

    async def start(self, on_event: OnEvent) -> None:
        if not self.api_key:
            raise STTError("DEEPGRAM_API_KEY is not set. Put it in .env.")
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
        from pipecat.workers.runner import WorkerRunner

        self._on_event = on_event
        self._stopping = False
        # dictation can sit silent for minutes; the default idle timeout would end the pipeline
        self._worker = PipelineWorker(
            Pipeline([self._make_service(self.api_key), _collector(on_event)]),
            params=PipelineParams(audio_in_sample_rate=RATE),
            idle_timeout_secs=None,
            processor_unusable_policy=ProcessorUnusablePolicy.END,  # a rejected key ends the run, so we report "closed"
        )

        @self._worker.event_handler("on_pipeline_error")
        async def _on_error(worker, frame):
            message = f"Deepgram (Pipecat): {frame.error}"
            if "(status 401)" in message or "(status 403)" in message:  # the SDK's text is a dump of request headers
                message = "Deepgram refused DEEPGRAM_API_KEY. Check it with: .venv/bin/python scripts/check_keys.py"
            await on_event({"type": "error", "message": message})

        runner = WorkerRunner(handle_sigint=False)  # the server owns Ctrl+C
        await runner.add_workers(self._worker)
        self._task = asyncio.create_task(self._run(runner))
        await on_event({"type": "ready"})

    async def _run(self, runner) -> None:
        try:
            await runner.run()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._on_event({"type": "error", "message": f"Deepgram (Pipecat): {e}"})
        if not self._stopping:
            await self._on_event({"type": "closed"})

    async def send_audio(self, pcm: bytes) -> None:
        from pipecat.frames.frames import InputAudioRawFrame

        if self._worker is not None and not self._stopping:
            await self._worker.queue_frame(InputAudioRawFrame(audio=pcm, sample_rate=RATE, num_channels=1))

    async def end_turn(self) -> None:
        # A Finalize that Deepgram answers with nothing pushes no frame, so nothing waits on it.
        from pipecat.frames.frames import VADUserStoppedSpeakingFrame

        if self._worker is not None and not self._stopping:
            await self._worker.queue_frame(VADUserStoppedSpeakingFrame())

    async def stop(self) -> None:
        from pipecat.frames.frames import EndFrame

        self._stopping = True
        if self._worker is not None and self._task is not None and not self._task.done():
            await self._worker.queue_frame(EndFrame())
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except Exception:
                self._task.cancel()


def _deepgram_service(api_key: str):
    from pipecat.services.deepgram.stt import DeepgramSTTService

    return DeepgramSTTService(api_key=api_key, settings=DeepgramSTTService.Settings(diarize=True))


def _collector(on_event: OnEvent):
    from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class Collector(FrameProcessor):
        """Turns each Deepgram result into engine events, once per result, and passes every frame on."""

        def __init__(self) -> None:
            super().__init__()
            self._last = None

        async def process_frame(self, frame, direction) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
                result = frame.result
                if result is not None and result is not self._last:
                    self._last = result
                    for event in parse_message(result.model_dump()):
                        await on_event(event)
            await self.push_frame(frame, direction)

    return Collector()
