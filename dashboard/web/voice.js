(function (root) {
  "use strict";
  const SESSION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const UTTERANCE_ID = /^[A-Za-z0-9_.:-]{1,128}$/;
  function sessionGrant(value) {
    if (!value || !SESSION_ID.test(value.id || "") || value.sample_rate !== 16000 || typeof value.url !== "string") return null;
    try {
      const url = new URL(value.url);
      if (url.protocol !== "wss:" || url.username || url.password || url.port || url.hash || !(url.hostname === "gladia.io" || url.hostname.endsWith(".gladia.io"))) return null;
      return {id: value.id, url: value.url, sample_rate: 16000};
    } catch { return null; }
  }
  class Session {
    constructor(options = {}) {
      this.env = options.env || root;
      this.requestSession = options.requestSession;
      this.onState = options.onState || (() => {});
      this.onPartial = options.onPartial || (() => {});
      this.onFinal = options.onFinal || (() => {});
      this.onError = options.onError || (() => {});
      this.state = "idle";
      this.generation = 0;
      this.timers = new Set();
      this.seen = new Set();
      this.active = false;
      this.uploadAllowed = false;
      this.sessionId = null;
    }
    supported() {
      return Boolean(this.env.navigator?.mediaDevices?.getUserMedia && (this.env.AudioContext || this.env.webkitAudioContext) && this.env.AudioWorkletNode && this.env.WebSocket);
    }
    setState(state) { this.state = state; this.onState(state); }
    later(callback, delay) {
      const timer = this.env.setTimeout(() => {this.timers.delete(timer); callback();}, delay);
      this.timers.add(timer);
      return timer;
    }
    async start() {
      if (this.active) return;
      if (!this.supported()) {this.fail("Microphone streaming is unavailable in this browser. Use the text field instead."); return;}
      this.active = true;
      this.uploadAllowed = false;
      this.sessionId = null;
      this.seen.clear();
      const generation = ++this.generation;
      this.abort = new this.env.AbortController();
      this.setState("permission");
      try {
        const Audio = this.env.AudioContext || this.env.webkitAudioContext;
        // Resume in the original click gesture, before awaiting permissions/network.
        this.audio = new Audio({sampleRate: 16000});
        const resumed = this.audio.resume();
        resumed.catch(() => {}); // Permission prompts may outlive an audio-context failure.
        const stream = await this.env.navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}, video: false});
        if (!this.active || generation !== this.generation) {stream.getTracks().forEach((track) => track.stop()); return;}
        this.stream = stream;
        stream.getTracks().forEach((track) => {track.onended = () => {if (this.active && this.state !== "finishing") this.stop();};});
        await resumed;
        if (!this.active || generation !== this.generation) return;
        this.setState("connecting");
        this.later(() => this.fail("The speech connection timed out. The microphone has been stopped."), 20000);
        await this.audio.audioWorklet.addModule("/assets/voice-worklet.js");
        if (!this.active || generation !== this.generation) return;
        const grant = sessionGrant(await this.requestSession(this.abort.signal));
        if (!this.active || generation !== this.generation) return;
        if (!grant) throw new Error("invalid_session");
        this.sessionId = grant.id;
        this.processor = new this.env.AudioWorkletNode(this.audio, "holospeed-pcm", {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1]});
        this.source = this.audio.createMediaStreamSource(stream);
        this.mute = this.audio.createGain(); this.mute.gain.value = 0;
        this.source.connect(this.processor); this.processor.connect(this.mute); this.mute.connect(this.audio.destination);
        this.processor.port.onmessage = ({data}) => this.sendAudio(data);
        const socket = this.socket = new this.env.WebSocket(grant.url);
        socket.binaryType = "arraybuffer";
        socket.onmessage = (event) => {if (generation === this.generation) this.receive(event.data);};
        socket.onerror = () => {if (generation === this.generation && this.active) this.fail("The speech connection failed. Use text or start a new recording.");};
        socket.onclose = () => {
          if (generation !== this.generation || !this.active) return;
          if (this.state === "finishing") this.cleanup("idle");
          else this.fail("The speech connection closed. The microphone has been stopped.");
        };
      } catch (error) {
        if (generation !== this.generation || !this.active) return;
        this.fail(error.name === "NotAllowedError" ? "Microphone permission was denied. Use the text field or allow access in your browser." : error.name === "NotFoundError" ? "No microphone was found. Use the text field instead." : error.voiceMessage || "Voice could not start. The microphone has been stopped; try the text field.");
      }
    }
    sendAudio(data) {
      if (!this.active || !this.uploadAllowed || this.state !== "listening" || this.socket?.readyState !== 1 || !(data instanceof ArrayBuffer)) return;
      if (this.socket.bufferedAmount + data.byteLength > 262144) {this.fail("The speech connection is falling behind. Recording stopped; use text or try again."); return;}
      try {this.socket.send(data);} catch {this.fail("Audio upload stopped. The microphone is off.");}
    }
    receive(raw) {
      if (!this.active || typeof raw !== "string" || raw.length > 100000) return;
      let message;
      try {message = JSON.parse(raw);} catch {return;}
      // Gladia v2 currently acknowledges with start_recording; start_session is
      // also documented. Neither needs a data object, but an explicit denial wins.
      if (["start_recording", "start_session"].includes(message.type) && this.state === "connecting") {
        if (message.data?.audio_upload_allowed === false) {this.fail("The speech provider did not allow audio upload. The microphone has been stopped."); return;}
        this.timers.forEach((timer) => this.env.clearTimeout(timer)); this.timers.clear();
        this.uploadAllowed = true;
        this.setState("listening");
        this.later(() => this.stop(), 120000);
      } else if (message.type === "transcript") {
        if (!["listening", "finishing"].includes(this.state)) return;
        const data = message.data;
        const text = typeof data?.utterance?.text === "string" ? data.utterance.text.trim() : "";
        if (!text) return;
        if (data.is_final !== true) {this.onPartial(text.slice(0, 1000)); return;}
        if (!UTTERANCE_ID.test(data.id || "") || this.seen.has(data.id)) return;
        this.seen.add(data.id);
        if (text.length > 1000) {this.onError("That utterance was too long. Please use an instruction under 1,000 characters."); return;}
        this.onPartial("");
        this.onFinal({text, session_id: this.sessionId, utterance_id: data.id});
      } else if (message.type === "end_session") this.cleanup("idle");
      else if (message.type === "error") this.fail("Gladia reported a speech error. The microphone has been stopped.");
    }
    stop() {
      if (!this.active || this.state === "finishing") return;
      if (this.state !== "listening" || this.socket?.readyState !== 1) {this.cleanup("idle"); return;}
      this.setState("finishing");
      this.uploadAllowed = false;
      this.stopAudio();
      this.timers.forEach((timer) => this.env.clearTimeout(timer)); this.timers.clear();
      try {this.socket.send(JSON.stringify({type: "stop_recording"}));} catch {this.cleanup("idle"); return;}
      this.later(() => this.cleanup("idle"), 3000);
    }
    stopAudio() {
      if (this.stream) this.stream.getTracks().forEach((track) => {track.onended = null; track.stop();});
      this.stream = null;
      if (this.processor) this.processor.port.onmessage = null;
      for (const node of [this.source, this.processor, this.mute]) {try {node?.disconnect();} catch { /* Already disconnected. */ }}
      this.source = this.processor = this.mute = null;
      try {this.audio?.close()?.catch?.(() => {});} catch { /* Already closed. */ }
      this.audio = null;
    }
    cancel() {this.cleanup("idle");}
    fail(message) {this.cleanup("error"); this.onError(message);}
    cleanup(state) {
      this.active = false;
      this.uploadAllowed = false;
      this.generation++;
      this.abort?.abort();
      this.abort = null;
      this.stopAudio();
      this.timers.forEach((timer) => this.env.clearTimeout(timer)); this.timers.clear();
      const socket = this.socket; this.socket = null;
      if (socket) {socket.onmessage = socket.onerror = socket.onclose = null; try {socket.close();} catch { /* Socket may not have connected. */ }}
      this.onPartial("");
      this.setState(state);
    }
  }
  const api = {Session, sessionGrant};
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HoloVoice = api;
})(globalThis);
