// Murmur chat page. Shows what you whispered and Murmur's replies, and reads replies aloud.
// All text is set with textContent, never innerHTML.
"use strict";

const $ = (id) => document.getElementById(id);
const messages = $("messages");
const chat = $("chat");
const liveRow = $("live-row");
const live = $("live");
const typingRow = $("typing-row");

let ws = null;
let state = {};
let heard = [];          // transcript pieces of the turn in progress
let lastStampAt = 0;
let voiceOn = localGet("murmur.voice", "1") === "1";
const audioQueue = [];
let playing = false;

function localGet(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

// ---- clock ----
function tick() {
  const now = new Date();
  $("day").textContent = now.toLocaleDateString(undefined, { weekday: "short" });
  $("date").textContent = now.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  $("time").textContent = now.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  $("day-sep").textContent = now.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}
tick();
setInterval(tick, 10_000);

// ---- chat rendering ----
function nearBottom() { return chat.scrollHeight - chat.scrollTop - chat.clientHeight < 160; }
function scrollDown(force) { if (force || nearBottom()) requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight; }); }

function wakePattern() {
  const names = (state.wake_aliases || ["murmur", "murmurs", "murmer"])
    .map((a) => a.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  return new RegExp(`^(\\W*hey\\W+(?:${names})\\b\\W*)`, "i");
}

function fillWithWake(el, text) {
  el.textContent = "";
  const m = text.match(wakePattern());
  if (m) {
    const span = document.createElement("span");
    span.className = "wake";
    span.textContent = m[1].trimEnd().replace(/[,.]$/, "");
    el.append(span, document.createTextNode(" " + text.slice(m[1].length)));
  } else {
    el.textContent = text;
  }
}

function stampFor(at) {
  if (at - lastStampAt < 5 * 60 * 1000) return null;
  lastStampAt = at;
  const d = new Date(at);
  const el = document.createElement("div");
  el.className = "stamp";
  el.append(d.toLocaleDateString(undefined, { month: "short", day: "numeric" }), document.createElement("br"),
            d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }));
  return el;
}

function addBubble(kind, text, opts = {}) {
  clearEmpty();
  const row = document.createElement("div");
  row.className = "row" + (kind === "mine" ? " mine" : "");
  const bubble = document.createElement("div");
  bubble.className = "bubble" + (kind === "mine" ? " mine" : "") + (opts.quiet ? " quiet" : "");
  if (kind === "mine") fillWithWake(bubble, text); else bubble.textContent = text;
  const stamp = stampFor(opts.at || Date.now());
  if (stamp) row.append(stamp);
  row.append(bubble);
  messages.append(row);
  scrollDown(true);
}

function showEmpty() {
  if (messages.children.length) return;
  const el = document.createElement("div");
  el.className = "empty";
  el.id = "empty";
  const b = document.createElement("b");
  b.textContent = "“Hey Murmur, …”";
  el.append("Whisper ", b, document.createElement("br"), "Only your voice is transcribed. Murmur acts when you say its name.");
  messages.append(el);
}
function clearEmpty() { const e = $("empty"); if (e) e.remove(); }

function setLive(text) {
  liveRow.hidden = !text;
  if (text) { fillWithWake(live, text); scrollDown(); }
}

function setTyping(on) {
  typingRow.hidden = !on;
  if (on) { chat.append(typingRow); scrollDown(true); }
}

// ---- status ----
function applyStatus(s) {
  state = { ...state, ...s };
  const listening = !!state.listening;
  $("listen-label").textContent = listening ? "Listening" : "Start listening";
  $("listen-dot").className = "dot" + (listening ? " on" : "");
  $("mic").classList.toggle("on", listening);

  const band = state.band || "";
  const connected = band === "connected" || band === "simulated";
  $("band-dot").className = "dot" + (connected ? " on" : band === "off" ? "" : " warn");
  $("band-label").textContent = {
    connected: "Mouthpiece connected",
    simulated: "Pretend mouthpiece (sample audio)",
    error: "Mouthpiece not found",
    off: "No mouthpiece (typing only)",
  }[band] || "Connecting to mouthpiece";

  const ignored = state.ignored_words || 0;
  $("isolation").textContent = ignored
    ? `Only your voice · ${ignored} word${ignored === 1 ? "" : "s"} from others ignored`
    : "Only your voice is heard";
  $("voice").hidden = state.voice === false;
}

// ---- voice ----
function updateVoiceButton() { $("voice").textContent = voiceOn ? "Voice on" : "Voice off"; }
updateVoiceButton();

function enqueue(clip) {
  if (!voiceOn) return;
  audioQueue.push(`/voice/${encodeURIComponent(clip)}`);
  if (!playing) playNext();
}

function playNext() {
  const url = audioQueue.shift();
  if (!url) { playing = false; return; }
  playing = true;
  const audio = new Audio(url);
  audio.onended = audio.onerror = () => playNext();
  audio.play().catch(() => {
    // The browser wants a click before it plays sound. Keep this clip for after the click.
    audioQueue.unshift(url);
    playing = false;
    $("unlock").hidden = false;
  });
}

$("unlock").addEventListener("click", () => { $("unlock").hidden = true; playNext(); });
$("voice").addEventListener("click", () => {
  voiceOn = !voiceOn;
  localSet("murmur.voice", voiceOn ? "1" : "0");
  if (!voiceOn) audioQueue.length = 0;
  updateVoiceButton();
});

// ---- mic level ring ----
let ringTimer = null;
function showLevel(ev) {
  const ring = $("ring");
  const over = Math.max(0, Math.min(1, (ev.db - ev.threshold + 12) / 24));
  ring.style.opacity = ev.open ? 1 : over * 0.5;
  ring.style.transform = `scale(${1 + over * 0.12})`;
  clearTimeout(ringTimer);
  ringTimer = setTimeout(() => { ring.style.opacity = 0; }, 600);
}

// ---- events ----
function handle(ev) {
  switch (ev.type) {
    case "hello":
      applyStatus(ev.state || {});
      messages.textContent = "";
      lastStampAt = 0;
      for (const h of ev.history || []) handle({ ...h, replay: true });
      showEmpty();
      break;
    case "status": applyStatus(ev); break;
    case "partial": setLive([...heard, ev.text].join(" ")); break;
    case "transcript": heard.push(ev.text); setLive(heard.join(" ")); break;
    case "turn":
      heard = [];
      setLive("");
      addBubble("mine", ev.text, { quiet: !ev.wake });
      break;
    case "thinking": setTyping(!!ev.on && !ev.replay); break;
    case "reply":
      setTyping(false);
      addBubble("murmur", ev.text);
      break;
    case "speak": enqueue(ev.clip); break;
    case "level": showLevel(ev); break;
    case "error": toast(ev.where === "stt" ? ev.message : "Something went wrong. Details are in the console."); console.warn(ev); break;
    default: break; // command, ignored, note: not shown on this page
  }
}

let toastTimer = null;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
}

function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  ws.onclose = () => setTimeout(connect, 1000);
}
connect();

// ---- controls ----
const toggleListening = () => send({ cmd: "listen", on: !state.listening });
$("listen").addEventListener("click", toggleListening);
$("mic").addEventListener("click", toggleListening);

$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  let text = $("say").value.trim();
  if (!text) return;
  if (!wakePattern().test(text)) text = `Hey Murmur, ${text}`; // typing here is always addressed to Murmur
  send({ cmd: "say", text });
  $("say").value = "";
});
