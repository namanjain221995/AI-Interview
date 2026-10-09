// Injected before any page script (page.addInitScript). Replaces getUserMedia
// with a controllable microphone (AudioContext -> MediaStreamDestination) and a
// synthetic camera (canvas), so end-to-end tests drive the REAL voice pipeline:
// browser AudioWorklet -> WebSocket -> server VAD -> Whisper -> engine.
(() => {
  const state = { ctx: null, dest: null, canvas: null, videoStream: null, speaking: 0 };
  function ensureAudio() {
    if (state.ctx) return;
    state.ctx = new AudioContext({ sampleRate: 48000 });
    state.dest = state.ctx.createMediaStreamDestination();
    // Very low noise floor so the stream is never digitally silent.
    const noise = state.ctx.createBufferSource();
    const buf = state.ctx.createBuffer(1, 48000, 48000);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * 0.0008;
    noise.buffer = buf;
    noise.loop = true;
    noise.connect(state.dest);
    noise.start();
  }
  function ensureVideo() {
    if (state.videoStream) return;
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 480;
    const g = canvas.getContext("2d");
    let t = 0;
    const draw = () => {
      t += 1;
      g.fillStyle = "#6b7280";
      g.fillRect(0, 0, 640, 480);
      g.fillStyle = "#f1c27d";
      g.beginPath();
      g.ellipse(320, 210 + Math.sin(t / 20) * 3, 95, 120, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#1f2937";
      g.fillRect(205, 330, 230, 150);
      g.fillStyle = "#111827";
      g.beginPath();
      g.arc(285, 190, 10, 0, Math.PI * 2);
      g.arc(355, 190, 10, 0, Math.PI * 2);
      g.fill();
      requestAnimationFrame(draw);
    };
    draw();
    state.canvas = canvas;
    state.videoStream = canvas.captureStream(15);
  }
  window.__e2eMedia = {
    async say(url) {
      ensureAudio();
      if (state.ctx.state !== "running") await state.ctx.resume();
      const arr = await (await fetch(url)).arrayBuffer();
      const audio = await state.ctx.decodeAudioData(arr);
      const src = state.ctx.createBufferSource();
      src.buffer = audio;
      src.connect(state.dest);
      state.speaking += 1;
      src.start();
      await new Promise((resolve) => (src.onended = resolve));
      state.speaking -= 1;
      return audio.duration;
    },
    isSpeaking() {
      return state.speaking > 0;
    },
  };
  const md = navigator.mediaDevices;
  if (!md) return;
  md.getUserMedia = async (constraints = {}) => {
    const tracks = [];
    if (constraints.audio) {
      ensureAudio();
      tracks.push(...state.dest.stream.getAudioTracks().map((t) => t.clone()));
    }
    if (constraints.video) {
      ensureVideo();
      tracks.push(...state.videoStream.getVideoTracks().map((t) => t.clone()));
    }
    return new MediaStream(tracks);
  };
  md.enumerateDevices = async () => [
    { kind: "audioinput", deviceId: "e2e-mic", label: "E2E Microphone", groupId: "e2e", toJSON() { return this; } },
    { kind: "videoinput", deviceId: "e2e-cam", label: "E2E Camera", groupId: "e2e", toJSON() { return this; } },
    { kind: "audiooutput", deviceId: "default", label: "Default output", groupId: "e2e", toJSON() { return this; } },
  ];
})();
