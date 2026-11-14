import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.agents import DryRunAgent, InstinctAgent, make_agent
from server.config import Config
from server.imessage import IMessageBridge, text_from_attributed_body


def attributed(text: str) -> bytes:
    raw = text.encode()
    assert len(raw) < 0x80
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92" \
           b"\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + bytes([len(raw)]) + raw + b"\x86\x84\x02iI"


class AttributedBodyTests(unittest.TestCase):
    def test_extracts_short_text(self):
        self.assertEqual(text_from_attributed_body(attributed("Done: email sent.")), "Done: email sent.")

    def test_no_marker_or_empty(self):
        self.assertIsNone(text_from_attributed_body(b"nothing here"))
        self.assertIsNone(text_from_attributed_body(None))


class SendTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_passes_handle_and_text_as_arguments_not_in_the_script(self):
        calls = []

        async def runner(*argv):
            calls.append(argv)
            return 0, ""

        bridge = IMessageBridge("+15551234567", runner=runner)
        await bridge.send('say "hi" & `rm -rf /`')
        argv = calls[0]
        self.assertEqual(argv[0], "osascript")
        self.assertEqual(argv[-2:], ("+15551234567", 'say "hi" & `rm -rf /`'))
        self.assertNotIn("rm -rf", argv[2])  # the script itself never contains the spoken text

    async def test_send_failure_raises(self):
        async def runner(*argv):
            return 1, "not authorised"

        with self.assertRaises(RuntimeError):
            await IMessageBridge("+1555", runner=runner).send("hi")

    async def test_agent_choice_defaults_to_dry_run(self):
        self.assertIsInstance(make_agent(Config()), DryRunAgent)
        self.assertIsInstance(make_agent(Config(dry_run=False)), DryRunAgent)  # no number set
        self.assertIsInstance(make_agent(Config(dry_run=False, instinct_to="+1555")), InstinctAgent)
        result = await DryRunAgent().send("anything")
        self.assertEqual(result.state, "dry-run")


class ReplyReadingTests(unittest.TestCase):
    def test_reads_only_new_incoming_messages_from_the_handle(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "chat.db"
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
                CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB,
                                      is_from_me INTEGER, handle_id INTEGER);
                INSERT INTO handle VALUES (1, '+15551234567'), (2, '+15559999999');
                INSERT INTO message VALUES (1, 'old reply', NULL, 0, 1);
            """)
            db.execute("INSERT INTO message VALUES (2, 'sent by me', NULL, 1, 1)")
            db.execute("INSERT INTO message VALUES (3, 'someone else', NULL, 0, 2)")
            db.execute("INSERT INTO message VALUES (4, NULL, ?, 0, 1)", (attributed("Booked it."),))
            db.commit()
            db.close()

            bridge = IMessageBridge("+15551234567", db_path=path)
            self.assertEqual(bridge._last_rowid(), 4)
            rows = bridge._fetch_after(1)
            self.assertEqual([r[0] for r in rows], [4])
            self.assertEqual(text_from_attributed_body(rows[0][2]), "Booked it.")


if __name__ == "__main__":
    unittest.main()
