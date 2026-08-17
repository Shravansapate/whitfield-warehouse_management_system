/**
 * Voice assistant panel — connects to the WMS WebSocket voice endpoint,
 * records microphone audio, streams to Deepgram (server-side) and displays
 * the Gemini AI agent replies.
 *
 * Works in two modes:
 *  1. Audio mode — browser mic → base64 chunks → server → Deepgram STT → Gemini
 *  2. Text mode  — typed input → server → Gemini (fallback when no mic/Deepgram)
 */

import { Mic, MicOff, Send, Volume2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import "./VoiceAssistant.css";

interface VoiceMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  pending?: boolean;
  committed?: boolean;
}

interface VoiceAssistantProps {
  accessToken: string;
  onClose: () => void;
  warehouseName: string;
}

type ConnectionState = "connecting" | "ready" | "error" | "closed";

function buildWsUrl(accessToken: string): string {
  const configuredBaseUrl = (import.meta.env.VITE_WMS_API_BASE_URL as string | undefined) ?? "";
  if (configuredBaseUrl && configuredBaseUrl.startsWith("http")) {
    const wsBase = configuredBaseUrl.replace(/^http/, "ws").replace(/\/$/, "");
    return `${wsBase}/voice/ws?token=${encodeURIComponent(accessToken)}`;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const targetHost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
    ? `${window.location.hostname}:8000`
    : window.location.host;
  return `${protocol}//${targetHost}/api/v1/voice/ws?token=${encodeURIComponent(accessToken)}`;
}

export function VoiceAssistant({ accessToken, onClose, warehouseName }: VoiceAssistantProps) {
  const [messages, setMessages] = useState<VoiceMessage[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [recording, setRecording] = useState(false);
  const [textInput, setTextInput] = useState("");
  const [hasPending, setHasPending] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const addMessage = useCallback((role: VoiceMessage["role"], text: string, extra?: Partial<VoiceMessage>) => {
    setMessages((prev) => [
      ...prev,
      { id: `${Date.now()}-${Math.random()}`, role, text, ...extra },
    ]);
  }, []);

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Connect WebSocket
  useEffect(() => {
    const url = buildWsUrl(accessToken);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setConnection("connecting");

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data as string) as Record<string, string>;
        const type = data.type;

        if (type === "ready") {
          setConnection("ready");
          addMessage("system", data.message ?? "Voice assistant connected.");
        } else if (type === "transcript") {
          addMessage("user", data.text ?? "");
        } else if (type === "response") {
          const reply = data.text ?? "";
          addMessage("assistant", reply);
          setHasPending(false);
          _speak(reply);
        } else if (type === "pending_confirmation") {
          const reply = data.text ?? "";
          addMessage("assistant", reply, { pending: true });
          setHasPending(true);
          _speak(reply);
        } else if (type === "committed") {
          const reply = data.text ?? "";
          addMessage("assistant", reply, { committed: true });
          setHasPending(false);
          _speak(reply);
        } else if (type === "error") {
          addMessage("system", `Error: ${data.message ?? "Unknown error"}`);
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onerror = () => setConnection("error");
    ws.onclose = () => setConnection("closed");

    return () => {
      ws.close();
    };
  }, [accessToken, addMessage]);

  // ----- Microphone recording -----
  const startRecording = useCallback(async () => {
    if (recording || !wsRef.current) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      audioChunksRef.current = [];
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        const arrayBuf = await blob.arrayBuffer();
        const b64 = btoa(String.fromCharCode(...new Uint8Array(arrayBuf)));
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: "audio", data: b64 }));
        }
      };

      recorder.start();
      setRecording(true);
    } catch {
      addMessage("system", "Microphone access denied. Use text input instead.");
    }
  }, [recording, addMessage]);

  const stopRecording = useCallback(() => {
    mediaRecorderRef.current?.stop();
    setRecording(false);
  }, []);

  const sendText = useCallback(() => {
    const text = textInput.trim();
    if (!text || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "text", text }));
    setTextInput("");
  }, [textInput]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendText();
    }
  };

  const confirmAction = () => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "text", text: "confirm" }));
  };

  const cancelAction = () => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "text", text: "cancel" }));
    setHasPending(false);
  };

  const isConnected = connection === "ready";

  return (
    <div className="voicePanel" role="dialog" aria-label="Voice assistant" aria-modal="true">
      {/* Header */}
      <div className="voicePanelHeader">
        <div className="voicePanelTitle">
          <Volume2 size={18} aria-hidden="true" />
          <span>Voice Assistant</span>
          <span className="voiceWarehouseBadge">{warehouseName}</span>
        </div>
        <div className="voicePanelStatus">
          <span className={`voiceStatusDot voiceStatusDot--${connection}`} aria-label={`Status: ${connection}`} />
          <span>{connection === "ready" ? "Live" : connection === "connecting" ? "Connecting…" : connection}</span>
        </div>
        <button className="voicePanelClose" onClick={onClose} aria-label="Close voice assistant" type="button">
          <X size={16} />
        </button>
      </div>

      {/* Messages */}
      <div className="voicePanelMessages" role="log" aria-live="polite" aria-label="Voice conversation">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`voiceMsg voiceMsg--${msg.role}${msg.pending ? " voiceMsg--pending" : ""}${msg.committed ? " voiceMsg--committed" : ""}`}
          >
            <p>{msg.text}</p>
            {msg.pending && (
              <div className="voiceConfirmActions">
                <button className="voiceConfirmBtn voiceConfirmBtn--yes" onClick={confirmAction} type="button">
                  ✓ Confirm — save item
                </button>
                <button className="voiceConfirmBtn voiceConfirmBtn--no" onClick={cancelAction} type="button">
                  ✗ Cancel
                </button>
              </div>
            )}
            {msg.committed && <span className="voiceCommittedBadge">✓ Saved to receipt</span>}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Confirmation banner */}
      {hasPending && (
        <div className="voicePendingBanner" role="alert">
          Waiting for your confirmation — say or type <strong>"confirm"</strong> or <strong>"cancel"</strong>
        </div>
      )}

      {/* Controls */}
      <div className="voicePanelControls">
        <button
          className={`voiceMicBtn${recording ? " voiceMicBtn--active" : ""}`}
          onPointerDown={startRecording}
          onPointerUp={stopRecording}
          disabled={!isConnected}
          aria-label={recording ? "Release to send audio" : "Hold to record"}
          type="button"
        >
          {recording ? <MicOff size={20} /> : <Mic size={20} />}
          <span>{recording ? "Release to send" : "Hold to speak"}</span>
        </button>

        <div className="voiceTextRow">
          <input
            aria-label="Type a voice command"
            className="voiceTextInput"
            disabled={!isConnected}
            id="voice-text-input"
            onChange={(e) => setTextInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Or type a command…"
            type="text"
            value={textInput}
          />
          <button
            aria-label="Send text command"
            className="voiceSendBtn"
            disabled={!isConnected || !textInput.trim()}
            onClick={sendText}
            type="button"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Text-to-speech helper (browser Web Speech API)
// ---------------------------------------------------------------------------
function _speak(text: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  
  // Clean markdown and special symbols for natural audio speech
  const cleanedText = text
    .replace(/[*_#`[\]()]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  
  if (!cleanedText) return;

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(cleanedText);
  utterance.rate = 1.0;
  utterance.pitch = 1.0;

  // Pick best available English voice
  const voices = window.speechSynthesis.getVoices();
  const preferredVoice =
    voices.find((v) => v.lang.startsWith("en") && (v.name.includes("Natural") || v.name.includes("Google") || v.name.includes("Samantha") || v.name.includes("David") || v.name.includes("Jenny"))) ||
    voices.find((v) => v.lang.startsWith("en"));

  if (preferredVoice) {
    utterance.voice = preferredVoice;
  }

  window.speechSynthesis.speak(utterance);
}
