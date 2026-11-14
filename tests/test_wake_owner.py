import unittest

from server.owner import OwnerFilter
from server.wake import WakeGate


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class WakeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.wake = WakeGate(("vocal", "vocals"), arm_seconds=8, clock=self.clock)

    def test_prefix_with_command(self):
        self.assertEqual(self.wake.feed("Hey Vocal, send an email to Sam."), "send an email to Sam.")
        self.assertEqual(self.wake.feed("hey, vocal: what's on my calendar"), "what's on my calendar")

    def test_no_prefix_means_no_action(self):
        self.assertIsNone(self.wake.feed("send an email to Sam"))
        self.assertIsNone(self.wake.feed("I told them hey vocal is the wake phrase"))
        self.assertIsNone(self.wake.feed("hey vocalize this"))
        self.assertIsNone(self.wake.feed(""))

    def test_wake_phrase_alone_arms_the_next_turn(self):
        self.assertIsNone(self.wake.feed("Hey Vocal."))
        self.assertTrue(self.wake.armed)
        self.assertEqual(self.wake.feed("book a table for two"), "book a table for two")
        self.assertFalse(self.wake.armed)
        self.assertIsNone(self.wake.feed("and something else"))

    def test_armed_state_times_out(self):
        self.wake.feed("hey vocal")
        self.clock.t += 9
        self.assertFalse(self.wake.armed)
        self.assertIsNone(self.wake.feed("book a table"))

    def test_aliases(self):
        wake = WakeGate(("vocal", "local"), clock=self.clock)
        self.assertEqual(wake.feed("hey local, play something"), "play something")


def words(text, speaker):
    return [{"text": t, "speaker": speaker} for t in text.split()]


class OwnerTests(unittest.TestCase):
    def test_first_speaker_is_the_owner_and_others_are_dropped(self):
        f = OwnerFilter()
        f.observe(words("hello there", "speaker_0"))
        runs = f.split(words("hello there", "speaker_0") + words("buy now", "speaker_1"))
        self.assertEqual([(r["speaker"], r["mine"], r["text"]) for r in runs],
                         [("speaker_0", True, "hello there"), ("speaker_1", False, "buy now")])

    def test_enrollment_picks_the_speaker_with_most_words(self):
        f = OwnerFilter()
        f.observe(words("noise", "speaker_1"))  # auto-owner would be speaker_1
        f.start_enrollment()
        done = f.observe(words("one two three four five six", "speaker_2") + words("x", "speaker_1")
                         + words("seven eight", "speaker_2"))
        self.assertTrue(done)
        self.assertEqual(f.owner, "speaker_2")
        self.assertFalse(f.is_owner("speaker_1"))

    def test_merge_follows_chains_and_moves_the_owner(self):
        f = OwnerFilter()
        f.observe(words("hi", "speaker_1"))
        f.apply_merge("speaker_1", "speaker_0")
        self.assertEqual(f.owner, "speaker_0")
        self.assertTrue(f.is_owner("speaker_1"))

    def test_unlabelled_words_count_as_the_owner(self):
        f = OwnerFilter()
        f.observe(words("hi", "speaker_0"))
        self.assertTrue(f.is_owner(None))
        runs = f.split([], "plain text")
        self.assertTrue(runs[0]["mine"])


if __name__ == "__main__":
    unittest.main()
