"use client";
import { Button } from "@/components/ui/button";

import { Textarea } from "@/components/ui/textarea";
import { ArrowUpRight, Mic, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export function CommandInput({
  text,
  onChange,
  onSubmit,
  onAudio,
  disabled,
  loading,
}: {
  text: string;
  onChange: (text: string) => void;
  onSubmit: () => void;
  onAudio: (audio: Blob) => void;
  disabled: boolean;
  loading: boolean;
}) {
  const [recording, setRecording] = useState(false);
  const [micError, setMicError] = useState("");
  const [acquiring, setAcquiring] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const generation = useRef(0);
  const audioCallback = useRef(onAudio);
  useEffect(() => {
    audioCallback.current = onAudio;
  }, [onAudio]);
  useEffect(
    () => () => {
      generation.current++;
      if (recorder.current?.state === "recording") {
        recorder.current.onstop = null;
        recorder.current.stop();
      }
      stream.current?.getTracks().forEach((track) => track.stop());
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  async function toggleMic() {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    setMicError("");
    setAcquiring(true);
    const current = ++generation.current;
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined")
        throw new Error("Recording is unavailable here. Please type your request.");
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (current !== generation.current) {
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      stream.current = media;
      const chunks: Blob[] = [];
      const active = new MediaRecorder(media);
      recorder.current = active;
      active.ondataavailable = (e) => {
        if (e.data.size) chunks.push(e.data);
      };
      active.onstop = () => {
        media.getTracks().forEach((track) => track.stop());
        if (timer.current) clearTimeout(timer.current);
        if (current !== generation.current) return;
        setRecording(false);
        const audio = new Blob(chunks, { type: active.mimeType });
        if (audio.size) audioCallback.current(audio);
      };
      active.start();
      setRecording(true);
      setAcquiring(false);
      timer.current = setTimeout(() => {
        if (active.state === "recording") active.stop();
      }, 30000);
    } catch (e) {
      stream.current?.getTracks().forEach((track) => track.stop());
      if (current === generation.current) {
        setAcquiring(false);
        setRecording(false);
        setMicError(e instanceof Error ? e.message : "Microphone access failed. Please type your request.");
      }
    }
  }
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim() && !disabled && !recording) onSubmit();
      }}
    >
      <label className="field-label" htmlFor="command">
        What would you like to do?
      </label>
      <div className="command-box">
        <Textarea
          id="command"
          value={text}
          onChange={(e) => onChange(e.target.value)}
          maxLength={500}
          rows={2}
          placeholder="Send fifty to John…"
          disabled={disabled || recording || acquiring}
        />
        <div className="command-toolbar">
          <Button
            type="button"
            variant="ghost"
            className={`mic-button ${recording ? "recording" : ""}`}
            onClick={toggleMic}
            disabled={disabled || acquiring}
            aria-pressed={recording}
          >
            {recording ? <Square size={16} aria-hidden="true" /> : <Mic size={16} aria-hidden="true" />}
            {recording ? "Stop recording" : acquiring ? "Opening microphone..." : "Use your voice"}
          </Button>
          <Button
            type="submit"
            loading={loading}
            className="primary-button"
            disabled={!text.trim() || disabled || recording || acquiring}
          >
            {loading ? "Working…" : "Build my task"}
            <ArrowUpRight size={18} aria-hidden="true" />
          </Button>
        </div>
      </div>
      <div className="input-note">
        <span>
          {recording
            ? "Listening. Tap stop when you’re done."
            : "Speak naturally, or type. You’ll review every detail."}
        </span>
        <span>{text.length}/500</span>
      </div>
      {micError && (
        <p role="alert" className="error-text">
          {micError}
        </p>
      )}
    </form>
  );
}
