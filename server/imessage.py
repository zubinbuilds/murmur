"""iMessage bridge: send a text through the Messages app and read replies from its database.

Sending uses AppleScript (macOS asks once to let your terminal control Messages). Reading
replies opens ~/Library/Messages/chat.db read-only, which needs Full Disk Access for the
terminal or editor you start the server from.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Awaitable, Callable, Optional

SEND_SCRIPT = """on run argv
  set theHandle to item 1 of argv
  set theText to item 2 of argv
  tell application "Messages"
    set theService to 1st account whose service type = iMessage
    set theBuddy to participant theHandle of theService
    send theText to theBuddy
  end tell
end run"""

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"


def text_from_attributed_body(blob: Optional[bytes]) -> Optional[str]:
    """Newer macOS keeps message text in a binary blob and leaves `text` empty.

    This is the commonly used heuristic: the string follows the "NSString" marker, after five
    header bytes and a length (one byte, or 0x81 and two bytes for long messages).
    """
    if not blob:
        return None
    start = blob.find(b"NSString")
    if start < 0:
        return None
    rest = blob[start + len(b"NSString") + 5:]
    if not rest:
        return None
    length = rest[0]
    offset = 1
    if length == 0x81 and len(rest) >= 3:
        length = int.from_bytes(rest[1:3], "little")
        offset = 3
    return rest[offset:offset + length].decode("utf-8", "ignore") or None


async def _run(*argv: str) -> tuple:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    return proc.returncode, err.decode("utf-8", "ignore").strip()


class IMessageBridge:
    def __init__(self, handle: str, runner: Callable[..., Awaitable[tuple]] = _run,
                 db_path: Path = CHAT_DB, poll_seconds: float = 2.0) -> None:
        self.handle = handle  # phone number in +15551234567 form, or an Apple ID email
        self._run = runner
        self.db_path = Path(db_path)
        self.poll_seconds = poll_seconds

    async def send(self, text: str) -> None:
        code, err = await self._run("osascript", "-e", SEND_SCRIPT, self.handle, text)
        if code != 0:
            raise RuntimeError(f"Messages refused the send: {err or 'unknown error'}")

    def _fetch_after(self, rowid: int) -> list:
        uri = f"file:{self.db_path}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            return db.execute(
                "SELECT m.ROWID, m.text, m.attributedBody FROM message m "
                "JOIN handle h ON h.ROWID = m.handle_id "
                "WHERE h.id = ? AND m.is_from_me = 0 AND m.ROWID > ? ORDER BY m.ROWID",
                (self.handle, rowid)).fetchall()

    def _last_rowid(self) -> int:
        uri = f"file:{self.db_path}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            return db.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0]

    async def watch(self, on_reply: Callable[[str], None], on_error: Callable[[str], None]) -> None:
        """Call on_reply(text) for each new message from the handle, forever."""
        try:
            last = await asyncio.to_thread(self._last_rowid)
        except (sqlite3.Error, OSError) as e:
            on_error(f"Cannot read replies ({e}). Give your terminal Full Disk Access to see them here.")
            return
        while True:
            await asyncio.sleep(self.poll_seconds)
            try:
                rows = await asyncio.to_thread(self._fetch_after, last)
            except (sqlite3.Error, OSError) as e:
                on_error(f"Cannot read replies ({e}).")
                return
            for rowid, text, blob in rows:
                last = max(last, rowid)
                text = text or text_from_attributed_body(blob)
                if text:
                    on_reply(text)
