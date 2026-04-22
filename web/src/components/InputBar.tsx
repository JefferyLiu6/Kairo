import { useRef, useEffect, type KeyboardEvent } from "react";
import { MODES, type Mode } from "../types";

type Props = {
  mode: Mode;
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  onStop?: () => void;
  isListening?: boolean;
  isSpeaking?: boolean;
  voiceEnabled?: boolean;
  onMicClick?: () => void;
  onVoiceToggle?: () => void;
  speechSupported?: boolean;
};

export function InputBar({
  mode, value, onChange, onSubmit, disabled, onStop,
  isListening, isSpeaking, voiceEnabled, onMicClick, onVoiceToggle, speechSupported,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const modeConfig = MODES.find((m) => m.id === mode)!;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [value]);

  useEffect(() => {
    ref.current?.focus();
  }, [mode]);

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && value.trim()) onSubmit();
    }
  }

  return (
    <div className="input-area">
      <div className="input-bar">
        <textarea
          ref={ref}
          className="input-textarea"
          placeholder={isListening ? "Listening…" : disabled && !onStop ? "No credits left — refresh for a new session" : modeConfig.placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKey}
          disabled={disabled}
          rows={1}
        />

        {/* Mic button */}
        {speechSupported && onMicClick && (
          <button
            type="button"
            className={`mic-btn${isListening ? " listening" : ""}`}
            onClick={onMicClick}
            disabled={disabled}
            aria-label={isListening ? "Stop listening" : "Voice input"}
            title={isListening ? "Stop" : "Speak"}
          >
            {isListening ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <rect x="9" y="2" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.8" />
                <path d="M5 10a7 7 0 0 0 14 0" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                <line x1="12" y1="19" x2="12" y2="22" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            )}
          </button>
        )}

        {/* Voice output toggle */}
        {speechSupported && onVoiceToggle && (
          <button
            type="button"
            className={`voice-toggle-btn${voiceEnabled ? " active" : ""}${isSpeaking ? " speaking" : ""}`}
            onClick={isSpeaking ? undefined : onVoiceToggle}
            aria-label={voiceEnabled ? "Mute voice" : "Enable voice"}
            title={voiceEnabled ? "Voice on (click to mute)" : "Voice off"}
          >
            {voiceEnabled ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <path d="M11 5L6 9H2v6h4l5 4V5z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <path d="M11 5L6 9H2v6h4l5 4V5z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                <line x1="23" y1="9" x2="17" y2="15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                <line x1="17" y1="9" x2="23" y2="15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            )}
          </button>
        )}

        {disabled && onStop && (
          <button type="button" className="stop-btn" onClick={onStop} aria-label="Stop">
            Stop
          </button>
        )}
        <button
          className="send-btn"
          onClick={() => onSubmit()}
          disabled={disabled || !value.trim()}
          aria-label="Send"
        >
          {disabled ? (
            <span className="send-spinner" />
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M22 2L11 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M22 2L15 22L11 13L2 9L22 2Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </button>
      </div>
    </div>
  );
}
