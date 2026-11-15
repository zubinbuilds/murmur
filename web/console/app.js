// Everything that arrives here is shown with textContent, never innerHTML:
// transcripts and agent replies are untrusted text.
const $ = (id) => document.getElementById(id);
let ws;
let state = {};
const commandCards = new Map();

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setPill(id, text, kind) {
  const pill = $(id);
  pill.textContent = text;
  pill.className = "pill" + (kind ? " " + kind : "");
}

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => setPill("pill-conn", "Server connected", "ok");
  ws.onclose = () => {
    setPill("pill-conn", "Server disconnected", "bad");
    setTimeout(connect, 1000);
  };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}

function applyState(s) {
  state = { ...state, ...s };
  const band = state.band || "starting";
  const bandKind = band === "connected" || band === "simulated" ? "ok" : band === "error" ? "bad" : "";
  setPill("pill-band", `Band: ${band}`, bandKind);
  $("band-detail").textContent = state.band_detail || "";
  setPill("pill-listen", state.listening ? "Listening" : "Not listening", state.listening ? "ok" : "");
  $("pill-armed").classList.toggle("hidden", !state.armed);
  $("pill-armed").classList.add("pill", "warn");
  $("pill-dry").classList.toggle("hidden", !state.dry_run);
  $("listen").textContent = state.listening ? "Stop listening" : "Start listening";
  $("listen").classList.toggle("on", !!state.listening);
  if (state.engine) $("engine").value = state.engine;
  $("owner").textContent = state.enrolling ? "Whisper now..." : state.owner ? `You: ${state.owner}` : "";
  if (state.wake_aliases && state.wake_aliases.length) {
    $("wake-name").textContent = state.wake_aliases[0].replace(/^./, (c) => c.toUpperCase());
  }
  if (typeof state.threshold === "number") {
    $("meter-threshold").style.left = pct(state.threshold) + "%";
  }
}

const pct = (db) => Math.max(0, Math.min(100, ((db + 90) / 90) * 100));

function addTranscript(text, speakerLabel) {
  const line = el("div", "line");
  line.append(el("span", "chip", speakerLabel), el("span", "text", text));
  const box = $("transcript");
  box.append(line);
  box.scrollTop = box.scrollHeight;
}

function addCommand(ev) {
  const key = ev.text;
  let card = commandCards.get(key);
  if (!card || ev.state === "sending") {
    card = el("li", "cmd");
    card.append(el("div", "body", ev.text), el("div", "state"));
    $("commands").prepend(card);
    commandCards.set(key, card);
  }
  card.className = "cmd " + (ev.state === "sent" ? "sent" : ev.state === "error" ? "error" : "");
  const label = { sending: "Sending...", sent: "Sent", "dry-run": "Dry run", error: "Error" }[ev.state] || ev.state;
  card.querySelector(".state").textContent = ev.detail ? `${label}: ${ev.detail}` : label;
}

let ignoredWords = 0;

function handle(ev) {
  switch (ev.type) {
    case "hello":
      applyState(ev.state || {});
      (ev.history || []).forEach(handle);
      break;
    case "status":
      applyState(ev);
      break;
    case "level":
      $("meter-fill").style.width = pct(ev.db) + "%";
      $("meter-fill").classList.toggle("open", ev.open);
      $("meter-label").textContent = `${Math.round(ev.db)} dB`;
      $("meter-threshold").style.left = pct(ev.threshold) + "%";
      break;
    case "partial":
      $("partial").textContent = ev.text;
      break;
    case "transcript":
      $("partial").textContent = "";
      addTranscript(ev.text, "You");
      break;
    case "ignored":
      ignoredWords += ev.words;
      $("ignored").textContent = `Ignored ${ignoredWords} words from other voices.`;
      break;
    case "command":
      addCommand(ev);
      break;
    case "reply": {
      const card = el("li", "cmd reply");
      card.append(el("div", "body", ev.text), el("div", "state", "Reply from Instinct"));
      $("commands").prepend(card);
      break;
    }
    case "calibration":
      if (ev.state === "recording") {
        $("cal-msg").textContent = ev.phase === "noise" ? "Stay quiet for 3 seconds..." : "Whisper normally for 3 seconds...";
      } else if (ev.state === "done") {
        $("cal-msg").textContent = ev.phase === "noise" ? "Room recorded. Now step 2." : `Gate set to ${ev.threshold} dB.`;
      } else {
        $("cal-msg").textContent = ev.message || "Calibration failed.";
      }
      break;
    case "note":
      $("cal-msg").textContent = ev.text;
      break;
    case "error": {
      const line = el("div", "err", `${ev.where}: ${ev.message}`);
      $("transcript").append(line);
      break;
    }
  }
}

$("listen").onclick = () => send({ cmd: "listen", on: !state.listening });
$("engine").onchange = (e) => send({ cmd: "engine", name: e.target.value });
$("cal-noise").onclick = () => send({ cmd: "calibrate", phase: "noise" });
$("cal-whisper").onclick = () => send({ cmd: "calibrate", phase: "whisper" });
$("enroll").onclick = () => send({ cmd: "enroll" });
$("say-form").onsubmit = (e) => {
  e.preventDefault();
  const text = $("say-text").value.trim();
  if (text) send({ cmd: "say", text });
  $("say-text").value = "";
};

connect();
