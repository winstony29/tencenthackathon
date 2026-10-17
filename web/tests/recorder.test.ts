import assert from "node:assert/strict";
import { test } from "node:test";
import { downsample, encodeWav, rms } from "../src/lib/recorder";

test("the WAV encoder writes a 16 kHz mono 16-bit PCM header and clamps samples (D-006)", () => {
  const input = new Float32Array(48000); // one second at 48 kHz
  input.set([1, 1, 1, -1, -1, -1, 2, 2, 2]); // three 3-sample windows: full, negative full, clipped
  const samples = downsample(input, 48000, 16000);
  assert.equal(samples.length, 16000);
  const wav = Buffer.from(encodeWav(samples));
  assert.equal(wav.length, 44 + 32000);
  assert.equal(wav.toString("latin1", 0, 4), "RIFF");
  assert.equal(wav.readUInt32LE(4), 36 + 32000);
  assert.equal(wav.toString("latin1", 8, 12), "WAVE");
  assert.equal(wav.toString("latin1", 12, 16), "fmt ");
  assert.equal(wav.readUInt32LE(16), 16);
  assert.equal(wav.readUInt16LE(20), 1); // PCM
  assert.equal(wav.readUInt16LE(22), 1); // mono
  assert.equal(wav.readUInt32LE(24), 16000);
  assert.equal(wav.readUInt32LE(28), 32000);
  assert.equal(wav.readUInt16LE(32), 2);
  assert.equal(wav.readUInt16LE(34), 16);
  assert.equal(wav.toString("latin1", 36, 40), "data");
  assert.equal(wav.readUInt32LE(40), 32000);
  assert.equal(wav.readInt16LE(44), 32767);
  assert.equal(wav.readInt16LE(46), -32768);
  assert.equal(wav.readInt16LE(48), 32767);
  assert.equal(wav.readInt16LE(50), 0);
});

test("rms and same-rate downsampling", () => {
  assert.equal(rms(new Float32Array([0.5, -0.5])), 0.5);
  assert.equal(rms(new Float32Array()), 0);
  const same = new Float32Array([0.1, 0.2]);
  assert.equal(downsample(same, 16000, 16000), same);
  assert.equal(downsample(new Float32Array(44100), 44100, 16000).length, 16000);
});
