"""The ready-made vocabulary: order, de-duplication, the cap, files, apps, and the refusal fallback."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server import vocab
from server.config import Config
from server.hub import Hub
from server.pipeline import Pipeline
from server.stt.base import STTError
from tests.test_e2e_hardening import ClosingEngine
from tests.test_pipeline import FakeAgent


def write(lines) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    f.write("\n".join(lines))
    f.close()
    return f.name


class LoadKeytermsTests(unittest.TestCase):
    def test_own_terms_then_the_file_then_the_bundled_list(self):
        path = write(["# a comment", "Zubin", "", "  Acme Corp  "])
        self.addCleanup(os.unlink, path)
        terms = vocab.load_keyterms(Config(keyterms=("Sam",), vocab_file=path))
        self.assertEqual(terms[:4], ("Sam", "Zubin", "Acme Corp", "Murmur"))

    def test_duplicates_are_dropped_ignoring_case_and_the_first_spelling_wins(self):
        terms = vocab.load_keyterms(Config(keyterms=("murmur", "SLACK")))
        self.assertEqual([t for t in terms if t.lower() == "murmur"], ["murmur"])
        self.assertEqual([t for t in terms if t.lower() == "slack"], ["SLACK"])

    def test_the_list_is_cut_at_the_cap(self):
        path = write([f"word{i}" for i in range(200)])
        self.addCleanup(os.unlink, path)
        terms = vocab.load_keyterms(Config(keyterms=("Sam",), vocab_file=path))
        self.assertEqual(len(terms), vocab.CAP)
        self.assertEqual(terms[0], "Sam")

    def test_a_missing_file_is_skipped(self):
        with mock.patch("sys.stderr"):
            terms = vocab.load_keyterms(Config(keyterms=("Sam",), vocab_file="/no/such/list.txt", vocab=False))
        self.assertEqual(terms, ("Sam",))

    def test_the_bundled_list_can_be_turned_off(self):
        self.assertEqual(vocab.load_keyterms(Config(keyterms=("Sam",), vocab=False)), ("Sam",))

    def test_the_bundled_list_has_something_under_the_cap(self):
        bundled = vocab._lines(vocab.BUNDLED)
        self.assertIn("Murmur", bundled)
        self.assertLessEqual(len(bundled), vocab.CAP)

    def test_app_names_only_when_asked(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        (folder / "Linear.app").mkdir()
        (folder / "notes.txt").write_text("")
        with mock.patch.object(vocab, "APPS_DIR", folder):
            self.assertNotIn("Linear", vocab.load_keyterms(Config(keyterms=(), vocab=False)))
            self.assertEqual(vocab.load_keyterms(Config(keyterms=(), vocab=False, vocab_apps=True)), ("Linear",))

    def test_from_env_keeps_the_own_terms_as_the_fallback(self):
        env = {"MURMUR_KEYTERMS": "Sam", "MURMUR_VOCAB": "1"}
        with mock.patch.dict(os.environ, env), mock.patch("server.config.load_dotenv"):
            cfg = Config.from_env()
        self.assertEqual(cfg.fallback_keyterms, ("Sam",))
        self.assertIn("Murmur", cfg.keyterms)
        with mock.patch.dict(os.environ, {**env, "MURMUR_VOCAB": "0", "MURMUR_VOCAB_FILE": ""}), \
                mock.patch("server.config.load_dotenv"):
            cfg = Config.from_env()
        self.assertEqual((cfg.keyterms, cfg.fallback_keyterms), (("Sam",), None))


class RefusingEngine(ClosingEngine):
    async def start(self, on_event):
        raise STTError("Could not connect to Deepgram: 400")  # how the raw engine reports a refused handshake


class RefusedVocabularyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ClosingEngine.starts = 0
        self.hub = Hub()
        self.seen = []

        self.refuse_list = False

        def factory(name):
            self.seen.append(self.cfg.keyterms)
            self.engine = RefusingEngine() if self.refuse_list and len(self.cfg.keyterms) > 1 else ClosingEngine()
            return self.engine

        self.cfg = Config(stt="pipecat", deepgram_key="k", keyterms=("Sam", "Murmur", "Slack"),
                          fallback_keyterms=("Sam",))
        self.pipeline = Pipeline(self.cfg, self.hub, engine_factory=factory, agent=FakeAgent(), clock=lambda: 100.0)
        await self.pipeline.start()
        await self.pipeline.set_listening(True)

    async def asyncTearDown(self):
        await self.pipeline.shutdown()

    async def test_a_refusal_retries_once_with_the_own_terms_then_gives_up(self):
        await self.engine.on_event({"type": "closed"})
        await self.pipeline._reconnect_task
        self.assertTrue(self.pipeline.listening)
        self.assertEqual(self.seen, [("Sam", "Murmur", "Slack"), ("Sam",)])
        self.assertTrue(any(e["type"] == "note" and "MURMUR_KEYTERMS only" in e["text"] for e in self.hub.events))

        await self.engine.on_event({"type": "closed"})  # refused again: a bad key, not the list
        await self.pipeline._reconnect_task
        self.assertFalse(self.pipeline.listening)
        self.assertEqual(ClosingEngine.starts, 2)

    async def test_a_refusal_inside_start_also_falls_back(self):
        await self.pipeline.set_listening(False)
        self.seen.clear()
        self.cfg.keyterms, self.cfg.fallback_keyterms = ("Sam", "Murmur"), ("Sam",)
        self.refuse_list = True
        await self.pipeline.set_listening(True)
        self.assertTrue(self.pipeline.listening)
        self.assertEqual(self.seen, [("Sam", "Murmur"), ("Sam",)])
        self.assertFalse([e for e in self.hub.events if e["type"] == "error"])

    async def test_no_word_list_note_without_a_key(self):
        self.cfg.deepgram_key = ""
        self.assertFalse(self.pipeline._drop_word_list())
        self.assertEqual(self.cfg.fallback_keyterms, ("Sam",))


if __name__ == "__main__":
    unittest.main()
