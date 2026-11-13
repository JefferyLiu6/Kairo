import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Message } from "./Message";
import type { Message as MessageType, Mode } from "../types";

const SUGGESTIONS = [
  "What's on my schedule today?",
  "Add event: team standup tomorrow at 9am",
  "Add a task: follow up with Alex tomorrow",
  "What do I have tomorrow?",
];

/** How close to the bottom (px) still counts as "following" the conversation. */
const STICK_TO_BOTTOM_PX = 80;

function KairoEmptyState({ onAction }: { onAction?: (t: string) => void }) {
  return (
    <div className="empty-state">
      <div className="empty-logo-mark">
        <img className="empty-logo-img" src="/kairo-logo.svg" alt="" aria-hidden="true" />
      </div>
      <h1 className="empty-title">Ask Kairo to plan, update, or review</h1>
      {onAction && (
        <div className="empty-suggestions" role="group" aria-label="Suggested messages">
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
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const lastCountRef = useRef(0);
  const streamingIdRef = useRef<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  // Follow new content only while the reader is at the bottom, so scrolling
  // up to re-read isn't yanked back down by each streamed token.
  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_TO_BOTTOM_PX;
  }

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const grew = messages.length > lastCountRef.current;
    lastCountRef.current = messages.length;
    // A newly sent message always brings the view back to the bottom.
    if (grew) followRef.current = true;
    if (followRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, agentActivity, progressSteps]);

  // Announce a reply to screen readers once, when it finishes streaming,
  // instead of making the whole transcript a noisy live region.
  useEffect(() => {
    const streaming = messages.find((m) => m.role === "assistant" && m.streaming);
    if (streaming) {
      streamingIdRef.current = streaming.id;
      return;
    }
    const finishedId = streamingIdRef.current;
    if (!finishedId) return;
    streamingIdRef.current = null;
    const finished = messages.find((m) => m.id === finishedId);
    if (finished?.content) setAnnouncement(`Kairo replied: ${finished.content}`);
  }, [messages]);

  const steps = progressSteps ?? [];

  return (
    <div className="chat-window" ref={scrollRef} onScroll={handleScroll}>
      <div className="sr-only" aria-live="polite">{announcement}</div>
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
                    <span className={`agent-activity-pulse${i === steps.length - 1 ? "" : " done"}`} aria-hidden="true" />
                    <span className="agent-activity-text">{step}</span>
                  </div>
                ))}
              </div>
              {onStopAgent && (
                <button
                  type="button"
                  className="agent-activity-stop"
                  onClick={onStopAgent}
                >
                  Stop
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
