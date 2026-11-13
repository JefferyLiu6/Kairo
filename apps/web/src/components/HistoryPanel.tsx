import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchSessions,
  deleteSession,
  type SessionEntry,
} from "../api";
import { useModalDialog } from "../hooks/useModalDialog";
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
  // Untitled web chats come back as a bare UUID, or one with the server's "pm-" prefix.
  const uuid = id.replace(/^pm-/, "");
  if (/^[0-9a-f-]{36}$/.test(uuid)) return `Web chat · ${uuid.slice(0, 8)}`;
  return id;
}

export function HistoryPanel({ currentSessionId, onLoad, onNew, onClose, refreshNonce }: Props) {
  const [chatSessions, setChatSessions] = useState<SessionEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const panelRef = useModalDialog<HTMLDivElement>(onClose);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    fetchSessions()
      .then((s) => {
        if (!cancelled) setChatSessions(s.filter((x) => x.messageCount > 0));
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => load(), [load, refreshNonce]);

  const sorted = useMemo(
    () =>
      [...chatSessions].sort((a, b) =>
        a.createdAt && b.createdAt
          ? b.createdAt.localeCompare(a.createdAt)
          : b.messageCount - a.messageCount,
      ),
    [chatSessions],
  );

  const handleDelete = async (sessionId: string) => {
    if (!window.confirm("Delete this session? This cannot be undone.")) return;
    setDeleting(sessionId);
    setDeleteError(null);
    try {
      const ok = await deleteSession(sessionId);
      if (ok === false) throw new Error("delete failed");
      setChatSessions((prev) => prev.filter((s) => s.sessionId !== sessionId));
    } catch {
      setDeleteError("Couldn't delete that session. Try again.");
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="history-overlay" onClick={onClose}>
      <div
        ref={panelRef}
        className="history-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="history-header">
          <h2 className="history-title" id="history-title">Chat history</h2>
          <div className="history-header-actions">
            <button type="button" className="history-new-btn" onClick={() => { onNew(); onClose(); }}>
              + New chat
            </button>
            <button type="button" className="history-close" onClick={onClose} aria-label="Close chat history">
              <span aria-hidden="true">✕</span>
            </button>
          </div>
        </div>

        {deleteError && (
          <div className="history-notice" role="alert">{deleteError}</div>
        )}

        {loading ? (
          <div className="history-empty" role="status">Loading sessions…</div>
        ) : loadError ? (
          <div className="history-empty" role="alert">
            <p>Couldn't load your chat history.</p>
            <button type="button" className="history-retry-btn" onClick={load}>
              Try again
            </button>
          </div>
        ) : sorted.length === 0 ? (
          <div className="history-empty">
            <p>No saved chats yet.</p>
            <p className="history-empty-hint">Conversations appear here after your first message.</p>
          </div>
        ) : (
          <ul className="history-list" role="list">
            {sorted.map((s) => {
              const label = s.title || formatChatId(s.sessionId);
              const isCurrent = s.sessionId === currentSessionId;
              return (
                <li key={s.sessionId} className={`history-item${isCurrent ? " active" : ""}`}>
                  <button
                    type="button"
                    className="history-item-open"
                    aria-current={isCurrent ? "true" : undefined}
                    onClick={() => { onLoad(s.sessionId, "personal-manager"); onClose(); }}
                  >
                    <span className="history-item-id">{label}</span>
                    <span className="history-item-count">
                      {s.createdAt ? new Date(s.createdAt).toLocaleDateString() : ""}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="history-item-delete"
                    onClick={() => handleDelete(s.sessionId)}
                    disabled={deleting === s.sessionId}
                    aria-label={`Delete chat: ${label}`}
                    title="Delete chat"
                  >
                    <span aria-hidden="true">{deleting === s.sessionId ? "…" : "✕"}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
