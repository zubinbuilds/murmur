"""Wake parsing against the punctuation and casing Deepgram's smart_format writes."""

import unittest

from server.config import Config
from server.wake import WakeGate


def gate():
    return WakeGate(Config().wake_aliases, clock=lambda: 0.0)


class WakeFormTests(unittest.TestCase):
    def test_punctuated_and_cased_wake_phrases_carry_the_command(self):
        for text, command in [
            ("Hey Murmur, text Sam I'm running late.", "text Sam I'm running late."),
            ("Hey, Murmur. Text Sam I'm running late.", "Text Sam I'm running late."),
            ("HEY MURMUR! call mom", "call mom"),
            ("Hey Murmur's text Sam", "text Sam"),
            ("Hey Murmur’s, text Sam", "text Sam"),
            ("hey murmer text sam", "text sam"),
        ]:
            self.assertEqual(gate().feed(text), command, text)

    def test_the_wake_phrase_alone_with_punctuation_arms_the_gate(self):
        for text in ("Hey, Murmur.", "Hey Murmur,", "hey murmur?"):
            g = gate()
            self.assertIsNone(g.feed(text))
            self.assertTrue(g.armed, text)

    def test_names_that_are_not_aliases_do_not_wake_it(self):
        for text in ("Hey, merma, text Sam", "a murmur text sam", "Hey Murmurous text"):
            g = gate()
            self.assertIsNone(g.feed(text), text)
            self.assertFalse(g.armed, text)


if __name__ == "__main__":
    unittest.main()
