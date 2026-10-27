// "Talk to Sam": browser side of the voice demo.
// Audio goes up the /voice/ws relay as pcm16; the avatar's video and voice come back over WebRTC.
// If the avatar can't connect, Sam's voice is played from the audio events instead.

const $ = (id) => document.getElementById(id);
const stage = $("stage");
const callButton = $("call");
const statusLine = $("status");
const caption = $("caption");
const timerLabel = $("timer");
const video = $("avatar");
const avatarAudio = $("avatar-audio");
const placeholder = $("placeholder");

const GREETING_WAIT_MS = 1500;
const AVATAR_TIMEOUT_MS = 12000;
const SESSION_SETTINGS_WAIT_MS = 3000;

let call = null;

callButton.addEventListener("click", () => (call ? call.hangUp() : startCall()));

async function startCall() {
  setState("connecting", "Connecting to Sam…");
  callButton.disabled = true;
  try {
    call = new Call();
    await call.start();
  } catch (err) {
    console.error(err);
    const message = err && err.name === "NotAllowedError"
      ? "Microphone access was blocked. Allow it in your browser and try again."
      : (err && err.message) || "Couldn't start the call.";
    if (call) call.cleanup();
    call = null;
    setState("idle", message);
  } finally {
    callButton.disabled = false;
  }
}

function setState(state, message) {
  stage.dataset.state = state;
  callButton.textContent = state === "idle" ? "Talk to Sam" : "End call";
  if (message !== undefined) statusLine.textContent = message;
  if (state === "idle") {
    timerLabel.textContent = "";
    caption.textContent = "";
    video.classList.remove("on");
    placeholder.classList.remove("hidden");
  }
}

class Call {
  constructor() {
    this.ws = null;
    this.mic = null;
    this.micContext = null;
    this.player = null;
    this.pc = null;
    this.avatarLive = false;
    this.wantAvatar = "RTCPeerConnection" in window;
    this.sawResponse = false;
    this.inputChannels = 1;
    this.ended = false;
    this.timer = null;
  }

  async start() {
    const config = await fetch("/voice/config").then((r) => r.json());
    if (!config.enabled) throw new Error("The voice demo isn't switched on yet.");

    this.mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });

    const scheme = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${scheme}://${location.host}/voice/ws?avatar=${this.wantAvatar ? 1 : 0}`);
    this.ws.onmessage = (e) => this.onEvent(JSON.parse(e.data));
    this.ws.onclose = () => this.finish();
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = () => reject(new Error("Couldn't reach the voice server."));
    });
  }

  send(event) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(event));
  }

  onEvent(event) {
    if (!event.type.endsWith(".delta")) console.log("[voice]", event.type, JSON.stringify(event).slice(0, 4000));
    switch (event.type) {
      case "demo.ready":
        this.startTimer(event.max_session_seconds);
        if (!event.avatar) this.goLive();
        break;
      case "demo.ended":
        this.finish(event.message);
        break;
      case "session.created":
      case "session.updated": {
        // session.created carries service defaults; the agent's own settings (avatar included)
        // follow in session.updated, so give that a moment before falling back to voice only.
        this.trackInputChannels(event.session);
        const ice = event.session && event.session.avatar && event.session.avatar.ice_servers;
        if (this.wantAvatar && ice && !this.pc) this.connectAvatar(ice);
        else if (!this.wantAvatar || event.type === "session.updated") {
          if (!this.pc) this.goLive();
        } else {
          setTimeout(() => { if (!this.pc) this.goLive(); }, SESSION_SETTINGS_WAIT_MS);
        }
        break;
      }
      case "session.avatar.connecting":
        this.acceptAvatar(event.server_sdp);
        break;
      case "response.created":
        this.sawResponse = true;
        caption.textContent = "";
        break;
      case "response.audio.delta":
      case "response.output_audio.delta":
        if (!this.avatarLive) this.play(event.delta);
        break;
      case "response.audio_transcript.delta":
      case "response.output_audio_transcript.delta":
        caption.textContent += event.delta || "";
        break;
      case "input_audio_buffer.speech_started":
        if (this.player) this.player.stop();
        caption.textContent = "Listening…";
        break;
      case "response.done": {
        const r = event.response || {};
        if (r.status && r.status !== "completed" && r.status !== "cancelled") {
          this.showProblem(`Sam couldn't answer (${r.status})`, r.status_details);
        }
        break;
      }
      case "error":
        this.showProblem("Voice service error", event.error);
        break;
    }
  }

  // Server echo cancellation with a client reference expects interleaved channels (microphone
  // first). The browser already removes Sam's voice from the mic, so the reference stays silent.
  trackInputChannels(session) {
    const input = session && session.audio && session.audio.input;
    if (!input || !("echo_cancellation" in input)) return;
    const echo = input.echo_cancellation;
    this.inputChannels = echo && echo.reference_source === "client" ? (echo.channels || 2) : 1;
  }

  showProblem(title, details) {
    console.warn(title, details);
    const d = details || {};
    const reason = d.message || (d.error && d.error.message) || d.reason || d.type || "";
    statusLine.textContent = reason ? `${title}: ${reason}` : `${title}.`;
    caption.textContent = "";
  }

  // --- avatar (WebRTC) ---

  async connectAvatar(iceServers) {
    this.pc = new RTCPeerConnection({
      iceServers: iceServers.map((s) => ({ urls: s.urls, username: s.username, credential: s.credential })),
    });
    this.pc.addTransceiver("video", { direction: "recvonly" });
    this.pc.addTransceiver("audio", { direction: "recvonly" });
    this.pc.ontrack = (e) => {
      if (e.track.kind === "video") {
        video.srcObject = e.streams[0];
        video.classList.add("on");
        placeholder.classList.add("hidden");
      } else {
        avatarAudio.srcObject = e.streams[0];
      }
    };
    this.pc.onconnectionstatechange = () => {
      if (this.pc && this.pc.connectionState === "connected" && !this.avatarLive) {
        this.avatarLive = true;
        this.goLive();
      }
    };
    setTimeout(() => {
      if (!this.avatarLive && !this.ended) {
        console.warn("Avatar didn't connect; continuing with voice only.");
        this.goLive();
      }
    }, AVATAR_TIMEOUT_MS);

    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    await waitForIceGathering(this.pc, 3000);
    const local = this.pc.localDescription;
    // Azure avatar services exchange SDP as base64-encoded JSON {type, sdp}.
    this.send({ type: "session.avatar.connect", client_sdp: btoa(JSON.stringify({ type: local.type, sdp: local.sdp })) });
  }

  async acceptAvatar(serverSdp) {
    if (!this.pc || !serverSdp) return;
    let answer;
    try {
      answer = JSON.parse(atob(serverSdp));
    } catch {
      answer = { type: "answer", sdp: serverSdp };
    }
    await this.pc.setRemoteDescription({ type: answer.type || "answer", sdp: answer.sdp });
  }

  // --- microphone up, Sam's voice down ---

  async goLive() {
    if (this.micContext || this.ended) return;
    setState("live", this.avatarLive ? "You're live with Sam. Just start talking." : "You're live with Sam (voice only). Just start talking.");
    this.micContext = new AudioContext();
    await this.micContext.audioWorklet.addModule("/static/mic-worklet.js");
    const source = this.micContext.createMediaStreamSource(this.mic);
    const node = new AudioWorkletNode(this.micContext, "mic-processor");
    node.port.onmessage = (e) => {
      const audio = this.inputChannels === 1 ? e.data : withSilentReference(e.data, this.inputChannels);
      this.send({ type: "input_audio_buffer.append", audio: toBase64(audio) });
    };
    source.connect(node);
    if (!this.avatarLive) this.player = new Player();

    // Sam's greeting is normally sent by the agent; nudge it if nothing has started.
    setTimeout(() => { if (!this.sawResponse) this.send({ type: "response.create" }); }, GREETING_WAIT_MS);
  }

  play(base64) {
    if (!this.player) this.player = new Player();
    this.player.enqueue(base64);
  }

  startTimer(seconds) {
    const endsAt = Date.now() + seconds * 1000;
    const tick = () => {
      const left = Math.max(0, Math.round((endsAt - Date.now()) / 1000));
      timerLabel.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} left`;
    };
    tick();
    this.timer = setInterval(tick, 1000);
  }

  hangUp() {
    this.finish("Call ended. Thanks for talking with Sam!");
  }

  finish(message) {
    if (this.ended) return;
    this.ended = true;
    this.cleanup();
    call = null;
    setState("idle", message || "Call ended.");
  }

  cleanup() {
    clearInterval(this.timer);
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) this.ws.close();
    if (this.mic) this.mic.getTracks().forEach((t) => t.stop());
    if (this.micContext) this.micContext.close();
    if (this.player) this.player.close();
    if (this.pc) this.pc.close();
    video.srcObject = null;
    avatarAudio.srcObject = null;
  }
}

// Plays 24 kHz pcm16 chunks back to back; stop() drops queued audio when the caller interrupts.
class Player {
  constructor() {
    this.context = new AudioContext({ sampleRate: 24000 });
    this.nextTime = 0;
    this.sources = new Set();
  }

  enqueue(base64) {
    const pcm = new Int16Array(fromBase64(base64));
    const buffer = this.context.createBuffer(1, pcm.length, 24000);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 0x8000;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    this.nextTime = Math.max(this.nextTime, this.context.currentTime + 0.05);
    source.start(this.nextTime);
    this.nextTime += buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  stop() {
    this.sources.forEach((s) => { try { s.stop(); } catch { /* already stopped */ } });
    this.sources.clear();
    this.nextTime = 0;
  }

  close() {
    this.stop();
    this.context.close();
  }
}

function withSilentReference(monoBuffer, channels) {
  const mono = new Int16Array(monoBuffer);
  const out = new Int16Array(mono.length * channels);
  for (let i = 0; i < mono.length; i++) out[i * channels] = mono[i];
  return out.buffer;
}

function waitForIceGathering(pc, timeoutMs) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { pc.removeEventListener("icegatheringstatechange", check); resolve(); };
    const check = () => { if (pc.iceGatheringState === "complete") done(); };
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(done, timeoutMs);
  });
}

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

function fromBase64(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
