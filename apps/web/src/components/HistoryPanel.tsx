import { useEffect, useState } from "react";
import {
  fetchSessions,
  deleteSession,
  type SessionEntry,
} from "../api";
import type { Mode } from "../types";

type Props = {
  mode: Mode;
  currentSessionId: string;
  onLoad: (sessionId: string, mode: Mode) => void;
  onNew: () => void;
  onClose: () => void;
  refreshNonce?: number;
};

function formatChatId(id: string): string {
  if (id.startsWith("tg-")) return `Telegram · ${id.slice(3)}`;
  if (/^[0-9a-f-]{36}$/.test(id)) return `Web · ${id.slice(0, 8)}`;
  return id;
}

export function HistoryPanel({ mode: _mode, currentSessionId, onLoad, onNew, onClose, refreshNonce }: Props) {
  const [chatSessions, setChatSessions] = useState<SessionEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchSessions().then((s) => { setChatSessions(s.filter((x) => x.messageCount > 0)); }).finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshNonce]);

  const handleDelete = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm("Delete this session? This cannot be undone.")) return;
    setDeleting(sessionId);
    try {
      await deleteSession(sessionId);
      setChatSessions((prev) => prev.filter((s) => s.sessionId !== sessionId));
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="history-overlay" onClick={onClose}>
      <div className="history-panel" onClick={(e) => e.stopPropagation()}>
        <div className="history-header">
          <span className="history-title">Chat History</span>
          <div className="history-header-actions">
            <button className="history-new-btn" onClick={() => { onNew(); onClose(); }} title="Start a new session">
              + New
            </button>
            <button className="history-close" onClick={onClose} aria-label="Close">✕</button>
          </div>
        </div>

        {loading ? (
          <div className="history-empty">Loading…</div>
        ) : chatSessions.length === 0 ? (
          <div className="history-empty">No saved sessions yet.</div>
        ) : (
          <div className="history-list">
            {[...chatSessions]
              .sort((a, b) =>
                a.createdAt && b.createdAt
                  ? b.createdAt.localeCompare(a.createdAt)
                  : b.messageCount - a.messageCount,
              )
              .map((s) => (
                <div
                  key={s.sessionId}
                  className={`history-item ${s.sessionId === currentSessionId ? "active" : ""}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => { onLoad(s.sessionId, "personal-manager"); onClose(); }}
                  onKeyDown={(e) => { if (e.key === "Enter") { onLoad(s.sessionId, "personal-manager"); onClose(); } }}
                >
                  <div className="history-item-id">{s.title || formatChatId(s.sessionId)}</div>
                  <div className="history-item-count">{s.createdAt ? new Date(s.createdAt).toLocaleDateString() : ""}</div>
                  <button
                    className="history-item-delete"
                    onClick={(e) => handleDelete(s.sessionId, e)}
                    disabled={deleting === s.sessionId}
                    aria-label="Delete session"
                    title="Delete session"
                  >
                    {deleting === s.sessionId ? "…" : "✕"}
                  </button>
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
