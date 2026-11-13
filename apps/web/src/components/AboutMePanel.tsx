import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchPmMemory, type PmMemory } from "../api";
import { useModalDialog } from "../hooks/useModalDialog";

type Props = {
  onClose: () => void;
  /** Increment after a Kairo reply so the panel reloads if something new was remembered. */
  refreshNonce?: number;
};

function dayLabel(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** What Kairo remembers about the user, checked against what they asked it to remember. */
export function AboutMePanel({ onClose, refreshNonce }: Props) {
  const [memory, setMemory] = useState<PmMemory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const panelRef = useModalDialog<HTMLDivElement>(onClose);

  const load = useCallback(() => {
    let cancelled = false;
    setError(false);
    fetchPmMemory()
      .then((m) => { if (!cancelled) setMemory(m); })
      .catch(() => { if (!cancelled) setError(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => load(), [load, refreshNonce, retry]);

  // When each remembered fact was asked for, and the asks the profile no longer holds word for word.
  const { toldOn, lost, keptCount } = useMemo(() => {
    const asked = memory?.asked ?? [];
    const told = new Map<string, string>();
    for (const ask of asked) {
      const key = ask.text.toLowerCase();
      if (ask.kept && !told.has(key)) told.set(key, ask.at);   // newest first: keep the latest
    }
    return { toldOn: told, lost: asked.filter((a) => !a.kept), keptCount: asked.filter((a) => a.kept).length };
  }, [memory]);

  const asked = memory?.asked.length ?? 0;

  return (
    <div className="history-overlay" onClick={onClose}>
      <div
        ref={panelRef}
        className="history-panel about-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="about-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="history-header">
          <div>
            <h2 className="history-title" id="about-title">About me</h2>
            <p className="about-subtitle">What Kairo remembers about you</p>
          </div>
          <button type="button" className="history-close" onClick={onClose} aria-label="Close about me">
            <span aria-hidden="true">✕</span>
          </button>
        </div>

        {loading ? (
          <div className="history-empty" role="status">Loading what I remember…</div>
        ) : error || !memory ? (
          <div className="history-empty" role="alert">
            <p>Couldn't load what Kairo remembers.</p>
            <button type="button" className="history-retry-btn" onClick={() => { setLoading(true); setRetry((n) => n + 1); }}>
              Try again
            </button>
          </div>
        ) : (
          <div className="about-body">
            {asked > 0 && (
              <p className={`about-score${lost.length ? " about-score-warn" : ""}`} role="status">
                <strong>{keptCount} of {asked}</strong>{" "}
                {asked === 1 ? "thing" : "things"} you asked me to remember {keptCount === 1 ? "is" : "are"} stored
                exactly as you said {keptCount === 1 ? "it" : "them"}.
              </p>
            )}

            {lost.length > 0 && (
              <section aria-labelledby="about-lost-title">
                <h3 className="about-section-title about-section-warn" id="about-lost-title">
                  Asked, but not stored in your words
                  <span className="about-section-count">{lost.length}</span>
                </h3>
                <ul className="about-list" role="list">
                  {lost.map((ask) => (
                    <li key={`${ask.at}-${ask.text}`} className="about-fact about-fact-lost">
                      <span className="about-fact-text">{ask.text}</span>
                      <span className="about-fact-tag">Asked {dayLabel(ask.at)}</span>
                    </li>
                  ))}
                </ul>
                <p className="about-note">
                  These were saved when you asked, and have since been reworded or dropped. Compare them with the list below.
                </p>
              </section>
            )}

            <section aria-labelledby="about-facts-title">
              <h3 className="about-section-title" id="about-facts-title">
                What I remember
                <span className="about-section-count">{memory.facts.length}</span>
              </h3>
              {memory.facts.length === 0 ? (
                <p className="about-empty">
                  Nothing yet. Tell Kairo in chat, like "remember that I prefer meetings after 10am".
                </p>
              ) : (
                <ul className="about-list" role="list">
                  {memory.facts.map((fact, i) => {
                    const told = toldOn.get(fact.toLowerCase());
                    return (
                      <li key={`${i}-${fact}`} className="about-fact">
                        <span className="about-fact-text">{fact}</span>
                        {told && <span className="about-fact-tag about-fact-tag-kept">You told me · {dayLabel(told)}</span>}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            {memory.privateNotes > 0 && (
              <p className="about-private">
                Plus {memory.privateNotes} private {memory.privateNotes === 1 ? "note" : "notes"}. Sensitive facts are
                kept there, and are never shown here or to the model.
              </p>
            )}
          </div>
        )}

        <p className="about-footer">
          To add something, say "remember that …" in chat. A remembered fact can't be removed yet.
        </p>
      </div>
    </div>
  );
}
