import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message as MessageType } from "../types";
import { parseApproval, ApprovalCard } from "./ApprovalCard";

type Props = {
  message: MessageType;
  onAction?: (text: string) => void;
  onSpeak?: (text: string) => void;
};

function PmIcon() {
  return (
    <div className="message-label-icon">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none">
        <path
          d="M12 2L13.5 8.5L20 10L13.5 11.5L12 18L10.5 11.5L4 10L10.5 8.5Z"
          stroke="var(--teal)"
          strokeWidth="1.5"
          fill="none"
        />
      </svg>
    </div>
  );
}

export function Message({ message, onAction, onSpeak }: Props) {
  const isUser = message.role === "user";

  const approval = !isUser && !message.streaming
    ? parseApproval(message.content)
    : null;

  return (
    <div className={`message ${message.role}`}>
      {!isUser && (
        <div className="message-label">
          <PmIcon />
          <span>PM</span>
          {message.streaming && !message.content && (
            <span className="message-mode-badge">thinking</span>
          )}
        </div>
      )}
      <div className={approval ? "message-bubble message-bubble-approval" : "message-bubble"}>
        {isUser ? (
          <span style={{ whiteSpace: "pre-wrap" }}>
            {message.content.replace(/^(approve|reject)\s+[a-f0-9]{6,12}$/i, (_match, action) =>
              action.charAt(0).toUpperCase() + action.slice(1)
            )}
          </span>
        ) : message.streaming && message.content === "" ? (
          <div className="thinking">
            <div className="thinking-dots">
              <span /><span /><span />
            </div>
          </div>
        ) : approval ? (
          <ApprovalCard
            id={approval.id}
            summary={approval.summary}
            risk={approval.risk}
            onAction={onAction ?? (() => {})}
            disabled={!onAction}
          />
        ) : (
          <>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content}
            </ReactMarkdown>
            {message.streaming && <span className="streaming-cursor" />}
            {!message.streaming && onSpeak && (
              <button
                type="button"
                className="msg-speak-btn"
                onClick={() => onSpeak(message.content)}
                aria-label="Read aloud"
                title="Read aloud"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
                  <path d="M11 5L6 9H2v6h4l5 4V5z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/>
                  <path d="M15.54 8.46a5 5 0 0 1 0 7.07" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
                </svg>
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
