import unittest

from server.config import Config
from server.hub import Hub
from server.owner import OwnerFilter
from server.pipeline import Pipeline


def words(text, speaker):
    return [{"text": t, "speaker": speaker} for t in text.split()]


class StrictFinalsTests(unittest.TestCase):
    def test_once_there_is_an_owner_an_unlabelled_final_word_is_not_theirs(self):
        f = OwnerFilter()
        f.observe(words("hello", "speaker_0"))
        self.assertFalse(f.is_owner(None, strict=True))
        self.assertTrue(f.is_owner(None))  # partials, display only
        self.assertEqual([r["mine"] for r in f.split([], "who said this", strict=True)], [False])

    def test_before_any_owner_everything_counts_and_the_first_labelled_speaker_wins(self):
        f = OwnerFilter()
        self.assertTrue(f.is_owner(None, strict=True))
        self.assertEqual([r["mine"] for r in f.split([], "no labels yet", strict=True)], [True])
        f.observe(words("hi", "speaker_1") + words("there", "speaker_0"))
        self.assertEqual(f.owner, "speaker_1")


class OwnerStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = Hub()
        self.pipeline = Pipeline(Config(settle_s=60, whisper_only=False), self.hub, engine_factory=lambda n: None)

    async def asyncTearDown(self):
        await self.pipeline.shutdown()

    def last_status(self):
        return [e for e in self.hub.events if e["type"] == "status"][-1]

    async def test_status_names_the_owner_even_when_the_first_final_has_two_speakers(self):
        self.pipeline._on_final({"text": "", "words": words("hey there", "speaker_0") + words("buy now", "speaker_1")})
        self.assertEqual(self.last_status()["owner"], "speaker_0")
        self.assertEqual([e["text"] for e in self.hub.events if e["type"] == "transcript"], ["hey there"])

    async def test_a_merge_that_renames_the_owner_reaches_the_status(self):
        self.pipeline._on_final({"text": "", "words": words("hello", "speaker_2")})
        self.pipeline.engine = engine = object()
        await self.pipeline._on_stt_event({"type": "merge", "from": "speaker_2", "to": "speaker_0"}, engine)
        self.assertEqual(self.last_status()["owner"], "speaker_0")

    async def test_unlabelled_final_after_an_owner_is_ignored_not_transcribed(self):
        self.pipeline._on_final({"text": "", "words": words("hello", "speaker_0")})
        self.pipeline._on_final({"text": "from the room", "words": []})
        self.assertEqual([e["text"] for e in self.hub.events if e["type"] == "transcript"], ["hello"])
        self.assertEqual(self.hub.events[-1]["type"], "ignored")


class SimTurnsWhisperOnlyOffTests(unittest.TestCase):
    def test_status_reads_the_gate_that_sim_switched_off(self):
        cfg = Config()
        cfg.whisper_only = False  # what __main__ does for --sim
        hub = Hub()
        Pipeline(cfg, hub, engine_factory=lambda n: None).status()
        self.assertFalse(hub.events[-1]["whisper_only"])


if __name__ == "__main__":
    unittest.main()
