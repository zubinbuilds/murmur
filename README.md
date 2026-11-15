# Murmur

Whisper commands to your computer from a microphone in your mouth.

You whisper, and the mic on a tiny band board (Seeed XIAO nRF52840 Sense, running Vexo's
firmware) sits in your mouth and hears it. A page on your laptop shows a live transcript of
only your whispers. People talking out loud next to you are dropped before any speech service
hears them. Each whisper is a command: Murmur can type it into the app in front, hand it to
your own program (for computer use), text it to [Instinct](https://instinct.com) or have
Gemini answer it.

```
 band mic ──USB or BLE──▶ Python server ──▶ whisper gate ──▶ speech engine ──────────▶ turn
 (16 kHz)                 (Vexo kit)        drops pitched    Deepgram Nova-3 through     │
                                            voices           Pipecat, or ElevenLabs      │
                                                             Scribe                      ▼
             localhost page ◀── transcript, replies ◀───────────────────────────── command
                                                                                         │
                                                       agent: type, your program, ◀──────┘
                                                       Instinct or Gemini
```

## What works today

Run live:

- The real band over its USB cable, transcribed by Deepgram Nova-3 through Pipecat.
- All three speech engines and both voices (ElevenLabs, Deepgram), on recorded files. The
  voices played into a silent output.

Not run live yet: the band over Bluetooth, Gemini (no key), texting Instinct, typing into
another app, the command agent and spoken replies inside the app.

Measured on one 40 s recording from the band in one room, the whisper gate was open for 99% of
the owner's whisper and 0% of the room noise, the owner's normal voice and the person talking
next to them. 194 automated tests pass, with no network and no audio devices.

## Quickstart

You need macOS and Python 3.11+.

```bash
./scripts/setup.sh          # clones the Vexo kit, makes .venv, installs dependencies, creates .env
open .env                   # paste DEEPGRAM_API_KEY (and ELEVENLABS_API_KEY if you use it)
```

No hardware, no keys:

```bash
.venv/bin/python -m server --sim --engine mock
```

Open <http://127.0.0.1:8000> and press **Start**. The pretend band replays a recording and the
mock engine shows a placeholder transcript. To try a command without speaking, open **Settings**
and type `email Sam I'm late` into the test box: it lands in the transcript the way a whisper
would.

The band on its USB cable (find the port name with `ls /dev/cu.usbmodem*`; it differs per Mac):

```bash
.venv/bin/python -m server --usb /dev/cu.usbmodem101 --engine pipecat
```

The band over Bluetooth, for example in a retainer on a Mac that has never run Murmur:

1. Set `MURMUR_BAND` in `.env` to the number on the band's bag (82 is `Vexo-HACK-82`).
2. Stop every other program using the band: the Vexo dashboard, a Murmur server on another
   laptop, anything on its USB cable. Over Bluetooth the band takes one connection and cannot be
   found while it has one. A program on the cable shares the band, and each stream goes to
   whichever program started it last.
3. Double-tap the band. Its light blinks blue and it can be found for 60 seconds.
4. Start the server. Bluetooth is the default (`MURMUR_TRANSPORT=ble`):

   ```bash
   .venv/bin/python -m server --engine pipecat
   ```

   If macOS asks whether Terminal may use Bluetooth, say yes (or switch it on under System
   Settings, Privacy & Security, Bluetooth, then restart Terminal).

Once connected, a double-tap toggles listening. If the link drops, the server reconnects by
itself; for 2 minutes after a drop the band can be found without a double-tap.

Before a demo, run the 40 s guided check with the band in your mouth. It tells you when to stay
quiet, whisper, talk and let someone else talk, then prints what the gate let through. Give it
the USB port, or `ble`:

```bash
.venv/bin/python scripts/guided_recording.py ble
```

Without the band, a mic plugged into the Mac works too: `--mic DEVICE`.

## How it works

**Whisper mode** (`server/gate.py`, the default). A whisper has no pitch and nearby talk does.
The gate checks each 20 ms frame for pitch between 70 and 400 Hz. A turn opens after 200 ms of
unpitched sound 5 dB over the room, and 100 ms of pitch in a row is silenced. Gated audio is
replaced with silence, so the speech service never hears it but the stream keeps its timing.
Output is delayed 300 ms so the first syllable gets through. Anyone whispering into the band
counts as you.

**Turns.** A turn ends after 600 ms of quiet (`MURMUR_TURN_SILENCE_MS`). Murmur then tells the
speech service to commit and acts 0.3 s after the last words arrive (`MURMUR_SETTLE_S`). That is
900 ms of Murmur's own waiting after your last sound; the speech service's time comes on top.
600 ms is the shortest quiet that kept the owner's recorded whisper as one turn; 500 ms split
it in two.

**Commands** (`server/wake.py`). Every turn is a command, and a "Hey Murmur" at the start is
dropped. With `MURMUR_WAKE_WORD=1` a turn must start with "Hey Murmur" and nothing else is
sent. Then saying only "Hey Murmur" arms the next turn for 8 s, and after a command the next
turns count too until 20 s pass quietly (`MURMUR_FOLLOW_SECONDS`).

**Speech engines** (`server/stt/`). The real ones stream 16 kHz PCM and return word-level
speaker labels.

| Engine | Model | Turn end |
|---|---|---|
| `pipecat` | Deepgram Nova-3 through Pipecat | `Finalize` message |
| `deepgram` | Deepgram Nova-3 | `Finalize` message |
| `elevenlabs` | Scribe v3 Live (`scribe_v3_live`) | manual commit |
| `mock` | none, for testing | |

**Vocabulary** (`server/vocab.py`, Deepgram engines). Words to recognise more readily come from
`MURMUR_KEYTERMS`, your own list (`MURMUR_VOCAB_FILE`, one term per line), the bundled
`server/vocab.txt`, and with `MURMUR_VOCAB_APPS=1` the names of the apps in `/Applications`.
At most 50 terms.

**Agents** (`MURMUR_AGENT`, `server/agents.py`).

| Value | What happens to a command |
|---|---|
| `instinct` (default) | With `MURMUR_DRY_RUN=1` (default) the page shows what would be sent and nothing leaves the Mac. With `0` and `INSTINCT_IMESSAGE_TO` set, it is texted to Instinct over iMessage and replies are read from `~/Library/Messages/chat.db`. |
| `type` | Typed into the app in front (needs Accessibility permission). Saying "type ..." does the same with any agent. |
| `command` | Runs `MURMUR_AGENT_CMD` once, with the command as its last argument and no shell. What it prints is the reply. |
| `gemini` | Gemini (`GEMINI_API_KEY`) answers on the Mac. |

With a Gemini key, each command is also tidied by Gemini first (`MURMUR_CLEANUP`). Replies can
be spoken on an output device (`MURMUR_VOICE_DEVICE`) by ElevenLabs or Deepgram through Pipecat.

**Voice mode** (`MURMUR_WHISPER_ONLY=0`, `--sim`, or the page's Settings). A level gate passes
anything 14 dB over the room; calibrate it in the page (3 s of room, 3 s of whisper). The
speech service labels speakers, and only the first one (or the one **Enroll me** picks) reaches
the transcript.

## Settings

All in `.env` (see `.env.example`).

| Variable | Default | |
|---|---|---|
| `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY` | empty | speech service keys |
| `MURMUR_STT` | `elevenlabs` | `pipecat`, `deepgram`, `elevenlabs` or `mock`; `--engine` overrides it |
| `MURMUR_BAND` | `82` | the number on the band's bag |
| `MURMUR_TRANSPORT` | `ble` | `ble`, `serial` (`--usb`), `sim` (`--sim`) or `mic` (`--mic`) |
| `MURMUR_SERIAL_PORT` | empty | USB port; empty lets the kit find the band |
| `MURMUR_WHISPER_ONLY` | `1` | `0` for voice mode |
| `MURMUR_WAKE_WORD` | `0` | `1` to require "Hey Murmur" |
| `MURMUR_TURN_SILENCE_MS` | `600` | quiet that ends a turn |
| `MURMUR_SETTLE_S` | `0.3` | wait after the last words before acting |
| `MURMUR_AGENT` | `instinct` | `instinct`, `type`, `command` or `gemini` |
| `MURMUR_AGENT_CMD` | empty | the program for `command` |
| `MURMUR_DRY_RUN` | `1` | `0` lets the Instinct agent send |
| `INSTINCT_IMESSAGE_TO` | empty | the number or Apple ID you text Instinct at |
| `MURMUR_KEYTERMS` | `Murmur` | comma-separated words to recognise |
| `MURMUR_VOCAB`, `MURMUR_VOCAB_FILE`, `MURMUR_VOCAB_APPS` | `1`, empty, `0` | the word list sources |
| `MURMUR_TOGGLE_GESTURE` | `double-tap` | `tap`, `double-tap` or `none` |
| `MURMUR_RECORD` | `0` | `1` saves the raw band audio to `recordings/` (or `--record`) |
| `MURMUR_VOICE_DEVICE`, `MURMUR_TTS` | empty, `elevenlabs` | speak replies on this output |
| `GEMINI_API_KEY` | empty | Gemini cleanup and the `gemini` agent |

## Privacy and safety

- Audio leaves your laptop for Deepgram or ElevenLabs while you listen. Only the gated stream is
  sent, but the mic can still pick up people near you. Word lists go to the speech service too.
- With a Gemini key, command text goes to Google; spoken replies send their text to ElevenLabs
  or Deepgram.
- Keys live in `.env`, which is git-ignored.
- The page only accepts WebSocket connections from its own origin, so another website open in
  your browser cannot make Murmur act.
- The band lights its LED blue while the mic is on, and the kit cannot turn that off. Tell
  people near you that you are recording.
- Hardware in your mouth: test it unsealed first. Never charge the battery while it is in your
  mouth, keep a floss tether outside your lips, keep sessions short, and take it out at once if
  it feels warm or swollen.

## Limits

- Whisper mode is measured on one 40 s recording in one room. Crowds, music and someone else
  whispering nearby are untested.
- In voice mode, level calibration could not separate that room: its loudest moments (p95
  -19.3 dB) were louder than most of the whisper (p70 -20.8 dB).
- Bluetooth has not worked yet. Our one try said "Could not see Vexo-HACK-82", and we did not
  find out why.
- Gemini, typing, the command agent, spoken replies and iMessage are tested against fakes only.
- Whether a double-tap registers with the board in a retainer is untested
  (`MURMUR_TAP_THRESHOLD` sets the sensitivity).
- Transcript pieces are grouped by a time window (`MURMUR_SETTLE_S`), not by turn.
- There is no installer; `scripts/setup.sh` is the whole setup.

## Layout

```
server/                 Python app (aiohttp); __main__.py is the command line
  pipeline.py           audio in, through gate, engine and turns, to the agent
  gate.py, wake.py      whisper gate and turns; the command check
  owner.py, vocab.py    voice mode's speaker filter; the word list (vocab.txt)
  agents.py             Instinct, Gemini, command and dry-run agents
  imessage.py, gemini.py, typer.py, voice.py   what the agents use
  stt/                  Pipecat, Deepgram, ElevenLabs and mock engines
  band_source.py        the band through the Vexo kit (USB, Bluetooth, pretend band)
  mic_source.py, replay_source.py   a Mac input or a recording instead of the band
  bodygate.py           accelerometer own-voice check (MURMUR_OWN_VOICE, off)
  app.py, hub.py        the page's server and its WebSocket events
web/                    the localhost page (plain HTML, CSS, JS)
scripts/                setup.sh, guided_recording.py and analyze_recording.py, check_keys.py
tests/                  unittest suite
vexo-open-kit/          created by setup.sh at a pinned commit; git-ignored
```

Run the tests with `.venv/bin/python -m unittest discover -s tests -t .`.

## Built with

- [Vexo Open kit](https://github.com/VexoAI-Org/vexo-open-kit) (MIT): the band's Python library,
  fetched by `setup.sh` at a pinned commit.
- [Deepgram](https://deepgram.com) Nova-3 for speech to text, through [Pipecat](https://github.com/pipecat-ai/pipecat).
- [ElevenLabs](https://elevenlabs.io) Scribe v3 Live for speech to text, and its voices for spoken replies.
