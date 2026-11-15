# Murmur

Whisper to your computer. Murmur hears your whisper and ignores the people talking around you.

A tiny, retainer-sized microphone sits in your mouth. You whisper, and your words appear on your
Mac as text, live. Each whisper can also be a command: Murmur can type it into the app you are
using, or hand it to your own AI agent.

## How it works

```
┌───────────────────────────────┐
│  Microphone in your mouth     │
└───────────────┬───────────────┘
                │  Bluetooth or USB
                ▼
┌───────────────────────────────┐
│  Murmur on your Mac           │
│  keeps your whisper,          │
│  drops other voices           │
└───────────────┬───────────────┘
                │
                ▼
┌───────────────────────────────┐
│  Speech to text               │
│  Deepgram or ElevenLabs       │
└───────────────┬───────────────┘
                │
                ▼
┌───────────────────────────────┐      ┌───────────────────────────────┐
│                               │      │  Command                      │
│  Your words on the screen     │ ───▶ │  typed for you, or handed     │
│                               │      │  to your AI agent             │
└───────────────────────────────┘      └───────────────────────────────┘
```

Murmur tells a whisper from normal talking by pitch. A whisper has none and a normal voice does,
so only sound without pitch gets through. The people next to you never reach the speech service.

## Try it

You need a Mac with Python 3.11 or newer. The microphone is on a small Vexo board that we call
the band.

```bash
./scripts/setup.sh     # installs everything and creates .env
open .env              # paste your Deepgram key
```

Then start it one of three ways:

```bash
.venv/bin/python -m server --sim --engine mock       # no hardware and no key: a pretend band
.venv/bin/python -m server --engine pipecat          # the band over Bluetooth (double-tap it first)
.venv/bin/python -m server --usb --engine pipecat    # the band on its USB cable
```

Open <http://127.0.0.1:8000> and press **Start**.

The band talks to one computer at a time, so close anything else that is using it. Every setting
is explained in `.env.example`.

## What works today

Whispering into the band over USB and reading the text live works. On our 40 second test
recording, 99% of the wearer's whisper got through, and none of the room noise, the wearer's
normal voice or the person talking next to them. 194 automated tests pass.

Bluetooth is built, but our one try could not find the band. Typing and handing commands to an
agent are built and tested, but have not been run on real apps yet.

## Good to know

Your whispers are sent to Deepgram or ElevenLabs to be turned into text. Your keys stay in `.env`,
a file that is kept out of GitHub.

This is a prototype that goes in your mouth. Never charge it there, keep sessions short, and take
it out if it feels warm.

## Built with

- [Vexo Open kit](https://github.com/VexoAI-Org/vexo-open-kit): the band and its software
- [Deepgram](https://deepgram.com) through [Pipecat](https://github.com/pipecat-ai/pipecat): speech to text
- [ElevenLabs](https://elevenlabs.io): speech to text, and a voice for spoken replies
