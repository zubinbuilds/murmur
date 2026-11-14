# Murmur

Whisper to your AI agent from a retainer-sized Bluetooth microphone in your mouth.

You whisper. A tiny band board (Seeed XIAO nRF52840 Sense, running Vexo's firmware) streams
the sound to your laptop. A localhost web app shows a live transcript of **only your
whispers**, and when a turn starts with **"Hey Murmur"** it texts the rest to
[Instinct](https://instinct.com), an agent with a computer that you talk to over iMessage.

```
 band mic ──BLE──▶ Python server ──▶ whisper gate ──▶ speech engine ──▶ owner filter
 (Opus, 16 kHz)    (Vexo kit)        + turn detection   ElevenLabs Scribe    only you
                                                        v3 Live or Deepgram
                                                        (both diarize)
                                                                │
        Instinct ◀──iMessage── "Hey Murmur" check ◀── turn assembly ◀┘
           │                                             │
           └──── replies read back ──▶ localhost web UI ◀┘
```

## Status

Built and tested against the Vexo kit's pretend band, with a mock speech engine: 87 automated
tests pass, and the page runs end to end. **Not yet tried with live services or hardware:**
real ElevenLabs and Deepgram connections, the real band over Bluetooth, sending an iMessage,
and whether the whisper gate separates your whisper from loud music in a real room. See
[What is unproven](#what-is-unproven).

## Quickstart

You need macOS and Python 3.11+. The page is plain HTML and works in any modern browser.

```bash
./scripts/setup.sh          # clones the Vexo kit, makes .venv, installs dependencies, creates .env
open .env                   # paste ELEVENLABS_API_KEY and/or DEEPGRAM_API_KEY
.venv/bin/python -m server --sim --engine mock    # no hardware, no keys
```

Open <http://127.0.0.1:8000>. Press **Start listening**: the pretend band replays a recording,
and the mock engine shows a placeholder transcript. Type `Hey Murmur, email Sam I'm late` into
"Test without speaking" to see the agent path (it only shows what it would send).

With the real band (number 82, so `Vexo-HACK-82`):

1. Close the Vexo dashboard tab if it is open. The band accepts one Bluetooth connection.
2. **Double-tap the band** so it can be found for 60 seconds.
3. `.venv/bin/python -m server --engine elevenlabs` (or `--engine deepgram`).
4. Calibrate the gate (below), then press **Start listening**.

The band's number is `MURMUR_BAND` in `.env`. The engine can also be switched in the page.

Backup without the band, with a mic plugged into the Mac:

```bash
.venv/bin/python -m server --mic DEVICE --engine pipecat
```

## How it works

**Only your whispers.** Three layers, because no single one is enough.

1. **The mic is on you.** The band's microphone is in your mouth, so your whisper reaches it
   far louder than a room does.
2. **A level gate** (`server/gate.py`) drops everything below a threshold, 20 ms at a time. Gated
   audio is replaced with silence, not removed, so the speech service never hears it but the
   stream keeps its timing and its connection. Calibrate it in the page: record the room
   for 3 s (with the music on if there will be music), then whisper for 3 s. It picks a
   threshold between them, and tells you if they are too close to separate.
3. **Diarization plus an owner filter** (`server/owner.py`). The service labels speakers; the
   first speaker in a session is you (or press **Enroll my voice** to pick the one who
   speaks most). Words from anyone else are dropped before the transcript and the agent see
   them.

**Turn detection** is the same state machine as the gate: a turn starts when the level stays
above the threshold for 60 ms and ends after 700 ms of quiet (`MURMUR_TURN_SILENCE_MS`). At the
end of a turn we tell the speech service to commit what it has heard. Output is delayed 200 ms
so the first syllable is not clipped.

**"Hey Murmur"** (`server/wake.py`). A turn must start with "Hey Murmur" (aliases in
`MURMUR_WAKE_ALIASES`, for recognisers that mishear it). Everything after is the command.
Saying only "Hey Murmur" arms the next turn for 8 s, so you can pause. Nothing else is ever
sent anywhere.

**Toggling listening.** A double tap on the band (its motion sensor) toggles listening. Change
the gesture with `MURMUR_TOGGLE_GESTURE`. Whether a tap on a cheek-mounted board registers is
untested; `MURMUR_TAP_THRESHOLD` sets the sensitivity.

**Speech engines** (`server/stt/`). Both stream 16 kHz PCM and return word-level speaker labels.
Add another by implementing `STTEngine` in `server/stt/base.py`.

| Engine | Model | Diarization | Turn end |
|---|---|---|---|
| ElevenLabs | Scribe v3 Live (`scribe_v3_live`) | `diarize=true` | manual commit |
| Deepgram | Nova-3 | `diarize=true` | `Finalize` message |
| Pipecat (`pipecat`) | Deepgram Nova-3 through Pipecat | `diarize=True` | `Finalize` message |

**Instinct** (`server/agents.py`, `server/imessage.py`). Instinct is texted, so the bridge sends an
iMessage with AppleScript and reads replies from `~/Library/Messages/chat.db`.

- Set `INSTINCT_IMESSAGE_TO` to the number (like `+15551234567`) or Apple ID email you text.
- **Dry run is on by default** (`MURMUR_DRY_RUN=1`): the page shows what would be sent and
  nothing leaves your machine. Set it to `0` only when you mean it.
- macOS asks once whether your terminal may control Messages. Reading replies needs Full
  Disk Access for your terminal.
- Dry run stops only the iMessage. Gemini, typing and spoken replies still run.

**Also built:**

- **Gemini 4** (`server/gemini.py`): with `GEMINI_API_KEY`, tidies each command; `MURMUR_AGENT=gemini` answers it on the Mac instead of Instinct.
- **Typing** (`server/typer.py`): "Hey Murmur, type ..." types into the app in front (needs Accessibility permission).
- **Replies aloud** (`server/voice.py`): set `MURMUR_VOICE_DEVICE`; ElevenLabs or Deepgram through Pipecat (`MURMUR_TTS`).
- **Mic backup** (`server/mic_source.py`): `--mic DEVICE` listens on a Mac input instead of the band.

## Privacy and safety

- **Audio leaves your laptop** for ElevenLabs or Deepgram whenever you are listening. Only the
  gated stream is sent, but the mic can pick up people near you.
- Keys live in `.env`, which is git-ignored. Never commit it.
- The page only accepts WebSocket connections from its own origin, so another website open in
  your browser cannot make Murmur send messages.
- The band's firmware lights its LED blue while the mic is on, and the kit cannot turn that
  off. Inside a retainer it is hidden. Tell people near you that you are recording.
- **Hardware in your mouth:** test everything unsealed first. Never charge the battery while it
  is in your mouth, keep a floss tether outside your lips, keep sessions short, and take it out
  at once if it feels warm or swollen.

## Layout

```
server/            Python app (aiohttp)
  gate.py          whisper gate + turn detection + calibration
  stt/             ElevenLabs, Deepgram, and a mock engine
  owner.py         "which speaker is me"
  wake.py          the "Hey Murmur" prefix
  pipeline.py      band audio in → agent out
  band_source.py   talks to the band through the Vexo kit
  imessage.py      send and read iMessages
  agents.py        dry-run and Instinct agents
web/               the localhost page (plain HTML, CSS, JS)
tests/             unittest suite
scripts/setup.sh   fetch the kit (pinned commit), venv, deps
vexo-open-kit/     created by setup.sh; git-ignored; do not edit
```

Run the tests with `.venv/bin/python -m unittest discover -s tests -t .`.

## What is unproven

- **Live speech services.** The ElevenLabs and Deepgram message handling is tested against
  messages copied from their docs, not against the live services. Two guesses to check first:
  that ElevenLabs accepts a commit on a short chunk of silence, and that it keeps a session
  open while we stream silence between whispers.
- **The gate in a loud room.** It assumes your whisper at the band mic is louder than the music.
  If calibration says they are too close, move the band closer or lower the music; if that
  fails, the next step is a spectral or voice-activity check on top of level.
- **Diarization on whispers.** Speaker labels on whispered, single-speaker audio may be
  unstable. The owner filter treats the first label as you, so a mid-session label change
  would drop your own words; "Enroll my voice" repairs that.
- **iMessage.** Sending and reading replies are tested against fakes. Reading uses a
  common heuristic for newer macOS message storage.
- **Taps on a cheek-mounted board**, and the mic's orientation in the mouth (facing the cheek or
  the teeth). Test these with the board unsealed, held against your back teeth.
- **Everything under "Also built"** and the Pipecat engine: tested against fakes only. With a
  Gemini key, command text goes to Google; spoken replies send their text to ElevenLabs or Deepgram.

## Not built yet

- Per-turn rather than time-window grouping of transcript pieces (`settle_s` in `config.py`).
- A packaged installer. `scripts/setup.sh` is the whole setup.

## Credits

The band's Python library and Bluetooth contract: [Vexo Open kit](https://github.com/VexoAI-Org/vexo-open-kit)
(MIT), fetched by `setup.sh` at a pinned commit.
