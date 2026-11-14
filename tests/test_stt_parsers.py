import unittest

from server.stt.deepgram import parse_message as deepgram
from server.stt.elevenlabs import parse_message as elevenlabs


class ElevenLabsParserTests(unittest.TestCase):
    def test_session_and_partial(self):
        self.assertEqual(elevenlabs({"message_type": "session_started"}), [{"type": "ready"}])
        ev = elevenlabs({"message_type": "partial_transcript", "text": "hello wor",
                         "words": [{"text": "hello", "type": "word", "speaker_id": "speaker_0"},
                                   {"text": " ", "type": "spacing", "speaker_id": None},
                                   {"text": "wor", "type": "word", "speaker_id": None}]})
        self.assertEqual(ev, [{"type": "partial", "text": "hello wor", "words": [
            {"text": "hello", "speaker": "speaker_0"}, {"text": "wor", "speaker": None}]}])

    def test_empty_keepalive_partial_is_ignored(self):
        self.assertEqual(elevenlabs({"message_type": "partial_transcript", "text": ""}), [])

    def test_with_diarization_the_final_comes_from_diarized_transcript(self):
        self.assertEqual(elevenlabs({"message_type": "committed_transcript", "text": "Hello.", "segment_index": 0}), [])
        ev = elevenlabs({"message_type": "diarized_transcript", "segment_index": 0, "text": "Hello.",
                         "words": [{"text": "Hello.", "speaker_id": "speaker_0"}]})
        self.assertEqual(ev, [{"type": "final", "text": "Hello.", "words": [{"text": "Hello.", "speaker": "speaker_0"}]}])

    def test_without_diarization_the_committed_transcript_is_final(self):
        ev = elevenlabs({"message_type": "committed_transcript", "text": "Hello."}, diarize=False)
        self.assertEqual(ev, [{"type": "final", "text": "Hello.", "words": []}])

    def test_merge_and_errors(self):
        self.assertEqual(elevenlabs({"message_type": "speakers_merged", "from_speaker_id": "speaker_1",
                                     "to_speaker_id": "speaker_0"}),
                         [{"type": "merge", "from": "speaker_1", "to": "speaker_0"}])
        ev = elevenlabs({"message_type": "auth_error", "error": "bad key"})
        self.assertEqual(ev[0]["type"], "error")
        self.assertIn("bad key", ev[0]["message"])


class DeepgramParserTests(unittest.TestCase):
    RESULT = {"type": "Results", "is_final": True, "channel": {"alternatives": [{
        "transcript": "hey vocal send it",
        "words": [{"word": "hey", "punctuated_word": "Hey", "speaker": 0},
                  {"word": "vocal", "punctuated_word": "Vocal,", "speaker": 0},
                  {"word": "send", "speaker": 1}]}]}}

    def test_final_result_with_speakers(self):
        ev = deepgram(self.RESULT)
        self.assertEqual(ev[0]["type"], "final")
        self.assertEqual([w["speaker"] for w in ev[0]["words"]], ["speaker_0", "speaker_0", "speaker_1"])
        self.assertEqual(ev[0]["words"][1]["text"], "Vocal,")

    def test_interim_result_is_partial(self):
        self.assertEqual(deepgram({**self.RESULT, "is_final": False})[0]["type"], "partial")

    def test_empty_transcript_and_metadata_are_ignored(self):
        empty = {"type": "Results", "is_final": True, "channel": {"alternatives": [{"transcript": ""}]}}
        self.assertEqual(deepgram(empty), [])
        self.assertEqual(deepgram({"type": "Metadata"}), [])

    def test_error(self):
        self.assertEqual(deepgram({"type": "Error", "description": "bad"})[0]["type"], "error")


if __name__ == "__main__":
    unittest.main()
