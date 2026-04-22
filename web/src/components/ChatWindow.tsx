import { useEffect, useRef } from "react";
import { Message } from "./Message";
import type { Message as MessageType, Mode } from "../types";

const SUGGESTIONS = [
  "What's on my schedule today?",
  "Add event: team standup tomorrow at 9am",
  "Add a task: follow up with Alex tomorrow",
  "What do I have tomorrow?",
];

function KairoEmptyState({ onAction }: { onAction?: (t: string) => void }) {
  return (
    <div className="empty-state">
      <div className="empty-logo-mark">
        <img className="empty-logo-img" src="/kairo-logo.svg" alt="" aria-hidden="true" />
      </div>
      <div className="empty-title">Ask Kairo to plan, update, or review</div>
      {onAction && (
        <div className="empty-suggestions">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              className="suggestion-chip"
              onClick={() => onAction(s)}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

type Props = {
  messages: MessageType[];
  mode: Mode;
  agentActivity?: string | null;
  progressSteps?: string[];
  onStopAgent?: () => void;
  onAction?: (text: string) => void;
  onSpeak?: (text: string) => void;
};

export function ChatWindow({ messages, agentActivity, progressSteps, onStopAgent, onAction, onSpeak }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, agentActivity, progressSteps]);

  const steps = progressSteps ?? [];

  return (
    <div className="chat-window">
      {messages.length === 0 ? (
        <KairoEmptyState onAction={onAction} />
      ) : (
        <div className="messages-list">
          {messages.map((msg) => (
            <Message key={msg.id} message={msg} onAction={onAction} onSpeak={onSpeak} />
          ))}
          {agentActivity && (
            <div className="agent-activity" role="status" aria-live="polite">
              <div className="agent-activity-steps">
                {steps.map((step, i) => (
                  <div
                    key={i}
                    className={`agent-activity-step${i === steps.length - 1 ? " active" : " done"}`}
                  >
                    <span className={`agent-activity-pulse${i === steps.length - 1 ? "" : " done"}`} aria-hidden />
                    <span className="agent-activity-text">{step}</span>
                  </div>
                ))}
              </div>
              {onStopAgent && (
                <button
                  type="button"
                  className="agent-activity-stop"
                  onClick={onStopAgent}
                  aria-label="Stop"
                >
                  Stop
                </button>
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}
