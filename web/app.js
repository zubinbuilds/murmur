// Everything that arrives here is shown with textContent, never innerHTML:
// transcripts and agent replies are untrusted text.
const $ = (id) => document.getElementById(id);
let ws;
let state = {};
let ignoredWords = 0;
let recalibrating = false; // the Settings link reopens the strip after a calibration
let showingResult = false; // keeps the strip up briefly so the new gate level can be read
const commandCards = new Map(); // command text -> entry
const typedCards = new Map(); // typed text -> entry
const replyCards = new Map(); // trimmed reply text -> entry
const cleanups = new Map(); // raw or tidied text -> cleanup event that arrived before its command

const SOURCES = { ble: "Band", sim: "Pretend band", mic: "Mac mic" };
const ENGINES = { elevenlabs: "ElevenLabs", deepgram: "Deepgram", pipecat: "Deepgram via Pipecat", mock: "Mock" };
const AGENTS = { instinct: "Instinct", gemini: "Gemini" };
const STATES = {
  sending: ["sending", "busy"], sent: ["sent", "ok"], typed: ["typed", "ok"],
  "dry-run": ["", ""], error: ["error", "bad"], // no agent is connected today, so a dry run is just the heard command
};

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

const pct = (db) => Math.max(0, Math.min(100, ((db + 90) / 90) * 100));

let retryMs = 1000;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => {
    retryMs = 1000;
    setConn("", "ok", "Connected");
  };
  ws.onclose = () => {
    setConn("Offline", "bad", "Reconnecting");
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 10000);
  };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}

// hello replays the server's history, so drop what it will redraw; error lines are not replayed and stay
function clearReplayed() {
  document.querySelectorAll("#transcript .line:not(.err)").forEach((n) => n.remove());
  $("log").replaceChildren();
  [commandCards, typedCards, replyCards, cleanups].forEach((m) => m.clear());
  $("empty").classList.toggle("hidden", !!document.querySelector("#transcript .line"));
}

function setConn(text, kind, title) {
  $("conn").className = "conn " + kind;
  $("conn").title = title;
  $("conn-text").textContent = text;
}

// ---- status ----

function applyState(s) {
  state = { ...state, ...s };
  const band = state.band || "starting";
  const source = state.source || "ble";
  const healthy = source === "mic" || band === "connected" || band === "simulated";
  const input = (SOURCES[source] || source) + (healthy ? "" : ` (${band})`);
  $("meta").textContent = [input, ENGINES[state.engine] || state.engine]
    .filter(Boolean).join(" · ");
  $("meta").title = [state.band_detail, state.voice && `Voice: ${state.voice}`].filter(Boolean).join("\n");
  $("band-detail").textContent = state.band_detail || "";
  $("listen").textContent = state.listening ? "Stop" : "Start";
  $("listen").classList.toggle("on", !!state.listening);
  $("armed").classList.toggle("hidden", !state.armed);
  if (state.engine) $("engine").value = state.engine;
  $("whisper-only").checked = !!state.whisper_only;
  $("owner").textContent = state.enrolling ? "You: whisper now..." : `You: ${state.owner || "not set"}`;
  renderCalib();
  if (typeof state.threshold === "number") setGate(state.threshold);
}

function renderCalib() {
  // Whisper mode separates the owner by voicing, not level, so it never needs the level calibration.
  const settled = !!state.calibrated || !!state.whisper_only;
  $("calib").classList.toggle("hidden", settled && !recalibrating && !showingResult);
}

function onCalibration(ev) {
  if (ev.state === "recording") {
    $("cal-msg").textContent = ev.phase === "noise" ? "Stay quiet..." : "Whisper...";
  } else if (ev.state === "done" && ev.phase === "noise") {
    $("cal-msg").textContent = "Room recorded. Now step 2.";
  } else if (ev.state === "done") {
    $("cal-msg").textContent = `Done: gate at ${ev.threshold} dB.`;
    recalibrating = false;
    showingResult = true;
    setTimeout(() => { showingResult = false; renderCalib(); }, 4000);
  } else {
    $("cal-msg").textContent = ev.message || "Calibration failed.";
  }
  renderCalib();
}

// Shares of each one-second gate report, picked from --sim runs: with "Whispers only" off,
// passed was 24-50 of 50 frames; with it on, voiced was 16-38 of 50 and passed 0.
const HEARING_SHARE = 0.2;
const VOICED_SHARE = 0.2;
const QUIET_PASS_SHARE = 0.1;
const FRAME_S = 0.02;
let ignoredVoiceFrames = 0;

function onGate(ev) {
  const frames = ev.frames || 1;
  const passed = (ev.passed || 0) / frames;
  const voiced = (ev.voiced || 0) / frames;
  const word = passed >= HEARING_SHARE ? "Hearing you"
    : voiced >= VOICED_SHARE && passed < QUIET_PASS_SHARE ? "Ignoring nearby voice" : "";
  $("gate-word").textContent = word;
  $("gate-word").className = "gate-word" + (word === "Hearing you" ? " hearing" : word ? " ignoring" : "");
  ignoredVoiceFrames += ev.voiced || 0;
  const secs = Math.round(ignoredVoiceFrames * FRAME_S);
  $("ignored-secs").textContent = secs > 0 ? `Ignored ${secs} s of nearby voice` : "";
}

function setGate(db) {
  $("meter-gate").style.left = pct(db) + "%";
}

function setLevel(ev) {
  $("meter-fill").style.transform = `scaleX(${pct(ev.db) / 100})`;
  $("meter").classList.toggle("open", !!ev.open);
  $("meter").setAttribute("aria-valuenow", String(Math.round(ev.db)));
  if (typeof ev.threshold === "number") setGate(ev.threshold);
}

// ---- transcript ----

function wakePrefix(text) {
  const aliases = (state.wake_aliases && state.wake_aliases.length ? state.wake_aliases : ["murmur"])
    .map((a) => a.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const m = text.match(new RegExp(`^\\s*hey[\\s,]+(?:${aliases.join("|")})\\b[\\s,.!:]*`, "i"));
  return m ? m[0].length : 0;
}

function addLine(text, kind) {
  $("empty").classList.add("hidden");
  const line = el("p", "line" + (kind ? " " + kind : ""));
  const cut = kind ? 0 : wakePrefix(text);
  if (cut) line.append(el("mark", null, text.slice(0, cut)), text.slice(cut));
  else line.textContent = text;
  $("transcript").insertBefore(line, $("partial"));
  follow();
}

// Keep the newest words in view unless the reader has scrolled up to look back.
let following = true;

function follow() {
  const box = $("transcript");
  if (following) box.scrollTop = box.scrollHeight;
  $("jump").classList.toggle("hidden", following);
}

$("transcript").addEventListener("scroll", () => {
  const box = $("transcript");
  following = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  $("jump").classList.toggle("hidden", following);
});

// ---- commands and replies ----

function entry(kind, text) {
  const li = el("li", "entry " + kind);
  li.append(el("p", "entry-text", text));
  $("log").prepend(li);
  return li;
}

function setState(li, stateName, detail) {
  let tag = li.querySelector(".tag");
  if (!tag) tag = li.appendChild(el("span"));
  const [label, tone] = STATES[stateName] || [String(stateName || ""), ""];
  tag.textContent = label;
  tag.className = "tag" + (tone ? " " + tone : "") + (label ? "" : " hidden");
  li.title = detail || "";
}

function claimCleanup(li, ev) {
  cleanups.delete(ev.raw);
  cleanups.delete(ev.text);
  if (!ev.error && ev.raw !== ev.text) li.querySelector(".entry-text").title = `Heard: ${ev.raw}`;
}

function onCommand(ev) {
  let li = commandCards.get(ev.text);
  if (!li || ev.state === "sending") {
    li = entry("command", ev.text);
    commandCards.set(ev.text, li);
    const early = cleanups.get(ev.text);
    if (early) claimCleanup(li, early);
  }
  setState(li, ev.state, ev.detail);
}

function onCleanup(ev) {
  const li = commandCards.get(ev.raw) || commandCards.get(ev.text);
  if (li) return claimCleanup(li, ev);
  if (ev.raw) cleanups.set(ev.raw, ev);
  if (ev.text) cleanups.set(ev.text, ev);
}

function onTyped(ev) {
  let li = typedCards.get(ev.text);
  if (!li) {
    li = entry("typed", ev.text);
    typedCards.set(ev.text, li);
    // "type <text>" commands emit cleanup for the whole command, then typed for the text after "type"
    const early = [...cleanups.values()].find((c) => c.text && c.text.endsWith(ev.text));
    if (early) claimCleanup(li, early);
  }
  setState(li, ev.state, ev.detail);
}

function onReply(ev) {
  const li = entry("reply", ev.text);
  li.title = AGENTS[ev.from] || ev.from || "";
  replyCards.set(ev.text.trim(), li); // the voice strips the text before it reports spoken
}

function onSpoken(ev) {
  const li = replyCards.get(ev.text.trim());
  if (!li) return;
  li.classList.toggle("speaking", ev.state === "speaking");
  if (ev.state === "error") li.title = `Could not read aloud: ${ev.detail || "error"}`;
}

// ---- events ----

function handle(ev) {
  switch (ev.type) {
    case "hello":
      clearReplayed();
      applyState(ev.state || {});
      following = true;
      (ev.history || []).forEach(handle);
      break;
    case "clear":
      document.querySelectorAll("#transcript .line").forEach((n) => n.remove());
      $("partial").textContent = "";
      ignoredWords = 0;
      $("ignored").textContent = "";
      ignoredVoiceFrames = 0;
      $("ignored-secs").textContent = "";
      clearReplayed();
      break;
    case "status":
      applyState(ev);
      break;
    case "level":
      setLevel(ev);
      break;
    case "gate":
      onGate(ev);
      break;
    case "partial":
      $("partial").textContent = ev.text;
      follow();
      break;
    case "transcript":
      $("partial").textContent = "";
      addLine(ev.text);
      break;
    case "ignored":
      ignoredWords += ev.words;
      $("ignored").textContent = `Ignored ${ignoredWords} words from other voices.`;
      break;
    case "command":
      onCommand(ev);
      break;
    case "cleanup":
      onCleanup(ev);
      break;
    case "typed":
      onTyped(ev);
      break;
    case "reply":
      onReply(ev);
      break;
    case "spoken":
      onSpoken(ev);
      break;
    case "calibration":
      onCalibration(ev);
      break;
    case "note":
      $("note").textContent = ev.text;
      break;
    case "error":
      addLine(`${ev.where}: ${ev.message}`, "err");
      if (state.engine) $("engine").value = state.engine; // a refused engine switch leaves the menu on the old one
      break;
  }
}

$("listen").onclick = () => send({ cmd: "listen", on: !state.listening });
$("engine").onchange = (e) => send({ cmd: "engine", name: e.target.value });
$("whisper-only").onchange = (e) => send({ cmd: "whisper_only", on: e.target.checked });
$("cal-noise").onclick = () => send({ cmd: "calibrate", phase: "noise" });
$("cal-whisper").onclick = () => send({ cmd: "calibrate", phase: "whisper" });
$("enroll").onclick = () => send({ cmd: "enroll" });
$("clear").onclick = () => send({ cmd: "clear" });
$("jump").onclick = () => {
  following = true;
  follow();
};
$("recal").onclick = () => {
  recalibrating = true;
  $("cal-msg").textContent = "";
  renderCalib();
};
$("say-form").onsubmit = (e) => {
  e.preventDefault();
  const text = $("say-text").value.trim();
  if (text) send({ cmd: "say", text });
  $("say-text").value = "";
};

connect();
