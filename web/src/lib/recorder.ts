// Records the microphone as a 16 kHz mono 16-bit PCM WAV for /api/transcribe (D-006).
// The browser does the conversion, so the server needs no ffmpeg.

export const SPEECH_RMS = 0.02; // a frame louder than this counts as speech
export const SAMPLE_RATE = 16000;
export const SILENCE_MS = 1500; // stop this long after speech ends (CONTRACTS §9)
export const NO_SPEECH_MS = 5000; // give up when nothing is heard
export const MAX_MS = 12000;

export function rms(frame: Float32Array): number {
  let sum = 0;
  for (const s of frame) sum += s * s;
  return Math.sqrt(sum / (frame.length || 1));
}

/** Average each window of `from / to` input samples into one output sample. */
export function downsample(input: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return input;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.floor((i + 1) * ratio);
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = sum / (end - start);
  }
  return out;
}

/** A standard 44-byte RIFF/WAVE header followed by little-endian Int16 samples. */
export function encodeWav(samples: Float32Array, rate = SAMPLE_RATE): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 32768 : s * 32767, true);
  }
  return buffer;
}

/**
 * Record until the signal aborts (a second tap), 1.5 s of silence after speech, or 12 s.
 * Rejects when no speech is heard in the first 5 s, when there is no usable microphone,
 * or when the page is not HTTPS/localhost. Always releases the mic and the AudioContext.
 */
export function record({
  signal,
  onLevel,
}: {
  signal: AbortSignal;
  onLevel?: (level: number) => void;
}): Promise<Blob> {
  return new Promise<Blob>((resolve, reject) => {
    if (
      !window.isSecureContext ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof AudioContext === "undefined"
    ) {
      reject(
        new Error(
          "Voice needs a microphone on a secure page (HTTPS or localhost). Please type your request.",
        ),
      );
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
      .then((stream) => start(stream, signal, onLevel).then(resolve, reject))
      .catch((e: unknown) => {
        const name = e instanceof DOMException ? e.name : "";
        reject(
          new Error(
            name === "NotAllowedError" || name === "SecurityError"
              ? "Microphone access was refused. Please allow it or type your request."
              : name === "NotFoundError"
                ? "No microphone was found. Please type your request."
                : "The microphone couldn't start. Please type your request.",
          ),
        );
      });
  });
}

function start(stream: MediaStream, signal: AbortSignal, onLevel?: (level: number) => void): Promise<Blob> {
  const context = new AudioContext();
  const rate = context.sampleRate;
  const source = context.createMediaStreamSource(stream);
  // ponytail: ScriptProcessorNode is deprecated but needs no worklet file; move to an
  // AudioWorklet if a browser drops it.
  const processor = context.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  const started = Date.now();
  let speechAt = 0;
  let lastSpeechAt = 0;
  return new Promise<Blob>((resolve, reject) => {
    let done = false;
    const finish = (error?: Error) => {
      if (done) return;
      done = true;
      signal.removeEventListener("abort", stop);
      clearInterval(timer);
      processor.onaudioprocess = null;
      processor.disconnect();
      source.disconnect();
      stream.getTracks().forEach((track) => track.stop());
      void context.close().catch(() => {});
      if (error) {
        reject(error);
        return;
      }
      const total = chunks.reduce((n, c) => n + c.length, 0);
      const joined = new Float32Array(total);
      let offset = 0;
      for (const chunk of chunks) {
        joined.set(chunk, offset);
        offset += chunk.length;
      }
      resolve(new Blob([encodeWav(downsample(joined, rate, SAMPLE_RATE))], { type: "audio/wav" }));
    };
    const stop = () => finish();
    processor.onaudioprocess = (event) => {
      const frame = event.inputBuffer.getChannelData(0);
      chunks.push(new Float32Array(frame)); // copy: the browser reuses this buffer
      const level = rms(frame);
      onLevel?.(level);
      if (level > SPEECH_RMS) {
        speechAt ||= Date.now();
        lastSpeechAt = Date.now();
      }
    };
    const timer = setInterval(() => {
      const now = Date.now();
      if (!speechAt && now - started > NO_SPEECH_MS)
        finish(new Error("I didn't hear anything. Try again or type."));
      else if ((speechAt && now - lastSpeechAt > SILENCE_MS) || now - started > MAX_MS) finish();
    }, 100);
    if (signal.aborted) stop();
    else signal.addEventListener("abort", stop, { once: true });
    source.connect(processor);
    processor.connect(context.destination);
  });
}
