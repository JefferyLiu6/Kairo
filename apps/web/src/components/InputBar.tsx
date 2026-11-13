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

const MAX_TEXTAREA_HEIGHT = 140;

export function InputBar({
  mode, value, onChange, onSubmit, disabled, onStop,
  isListening, isSpeaking, voiceEnabled, onMicClick, onVoiceToggle, speechSupported,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const modeConfig = MODES.find((m) => m.id === mode)!;
  // `disabled` covers two different cases: the agent is replying (a stop
  // handler is supplied) or the account has no credits left.
  const busy = disabled && Boolean(onStop);
  const outOfCredits = disabled && !onStop;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
  }, [value]);

  useEffect(() => {
    ref.current?.focus();
  }, [mode]);

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Leave Enter alone while an IME is composing (CJK input).
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!disabled && value.trim()) onSubmit();
    }
  }

  return (
    <div className="input-area">
      {outOfCredits && (
        <p className="input-notice" id="input-notice" role="status">
          You're out of credits. Sign up with a new account or try another demo later.
        </p>
      )}
      <div className="input-bar">
        {/* Stays enabled while the agent replies so focus isn't lost and the
            next message can be drafted; submitting is blocked until it's done. */}
        <textarea
          ref={ref}
          className="input-textarea"
          aria-label="Message Kairo"
          aria-describedby={outOfCredits ? "input-notice" : undefined}
          placeholder={isListening ? "Listening…" : outOfCredits ? "No credits left" : modeConfig.placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKey}
          disabled={outOfCredits}
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
            title={isListening ? "Stop listening" : "Voice input"}
          >
            {isListening ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
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
            aria-label="Read replies aloud"
            aria-pressed={Boolean(voiceEnabled)}
            title={voiceEnabled ? "Reading replies aloud (click to mute)" : "Read replies aloud"}
          >
            {voiceEnabled ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M11 5L6 9H2v6h4l5 4V5z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M11 5L6 9H2v6h4l5 4V5z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                <line x1="23" y1="9" x2="17" y2="15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                <line x1="17" y1="9" x2="23" y2="15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            )}
          </button>
        )}

        {busy && onStop && (
          <button type="button" className="stop-btn" onClick={onStop}>
            Stop
          </button>
        )}
        <button
          type="button"
          className="send-btn"
          onClick={() => onSubmit()}
          disabled={disabled || !value.trim()}
          aria-label={busy ? "Kairo is replying" : "Send message"}
        >
          {busy ? (
            <span className="send-spinner" aria-hidden="true" />
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M22 2L11 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M22 2L15 22L11 13L2 9L22 2Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </button>
      </div>
    </div>
  );
}
