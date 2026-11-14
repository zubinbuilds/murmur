"""scripts/check_keys.py with fake HTTP: no network, no real keys."""

import json
import unittest

from scripts import check_keys
from server.config import Config

KEY = "sk-secret-123"


def fake(replies):
    """An http(url, headers) stand-in: replies maps a URL prefix to (status, body); records calls."""
    calls = []

    def http(url, headers):
        calls.append((url, headers))
        for prefix, reply in replies.items():
            if url.startswith(prefix):
                return reply
        raise AssertionError(f"unexpected URL {url}")

    http.calls = calls
    return http


class CheckKeysTests(unittest.TestCase):
    def run_main(self, cfg, http):
        lines = []
        code = check_keys.main(cfg, http=http, out=lines.append)
        return code, lines

    def test_all_ok_and_the_model_is_found(self):
        http = fake({
            check_keys.DEEPGRAM_URL: (200, json.dumps({"projects": [{"project_id": "p"}]})),
            check_keys.ELEVENLABS_URL: (200, json.dumps({"subscription": {"tier": "creator", "character_count": 5,
                                                                         "character_limit": 100}})),
            check_keys.GEMINI_URL: (200, json.dumps({"models": [{"name": "models/gemini-4-hydrogen"},
                                                                {"name": "models/gemini-3-pro"}]})),
        })
        cfg = Config(deepgram_key=KEY, elevenlabs_key=KEY, gemini_key=KEY, gemini_model="gemini-4-hydrogen")
        code, lines = self.run_main(cfg, http)
        self.assertEqual(code, 0)
        self.assertIn("OK (200): 1 project(s)", lines[0])
        self.assertIn("tier creator, 5 of 100", lines[1])
        self.assertIn("'gemini-4-hydrogen' is available", lines[2])
        self.assertIn("gemini-4 models: gemini-4-hydrogen", lines[2])
        self.assertNotIn("gemini-3-pro", lines[2])
        headers = {url.split("?")[0]: h for url, h in http.calls}
        self.assertEqual(headers[check_keys.DEEPGRAM_URL], {"Authorization": f"Token {KEY}"})
        self.assertEqual(headers[check_keys.ELEVENLABS_URL], {"xi-api-key": KEY})

    def test_missing_keys_say_not_set_and_nothing_is_called(self):
        http = fake({})
        code, lines = self.run_main(Config(), http)
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 3)
        self.assertTrue(all("not set" in line for line in lines))
        self.assertEqual(http.calls, [])

    def test_a_rejected_key_fails_with_its_status_and_the_key_is_scrubbed(self):
        http = fake({check_keys.DEEPGRAM_URL: (401, f'{{"err_msg": "Invalid credentials {KEY}"}}')})
        code, lines = self.run_main(Config(deepgram_key=KEY), http)
        self.assertEqual(code, 1)
        self.assertIn("FAIL (401)", lines[0])
        self.assertNotIn(KEY, "\n".join(lines))
        self.assertIn("***", lines[0])

    def test_a_model_not_in_the_list_is_a_failure(self):
        http = fake({check_keys.GEMINI_URL: (200, json.dumps({"models": [{"name": "models/gemini-4-flash"}]}))})
        code, lines = self.run_main(Config(gemini_key=KEY, gemini_model="gemini-4-hydrogen"), http)
        self.assertEqual(code, 1)
        self.assertIn("NOT in the list", lines[2])
        self.assertIn("gemini-4-flash", lines[2])

    def test_no_connection_and_a_bad_body_are_failures_not_crashes(self):
        http = fake({check_keys.DEEPGRAM_URL: (None, "no connection: timed out"),
                     check_keys.ELEVENLABS_URL: (200, "<html>not json</html>")})
        code, lines = self.run_main(Config(deepgram_key=KEY, elevenlabs_key=KEY), http)
        self.assertEqual(code, 1)
        self.assertIn("FAIL (no HTTP status): no connection", lines[0])
        self.assertIn("FAIL (200): unexpected reply", lines[1])


if __name__ == "__main__":
    unittest.main()
