import { useEffect, useId, useState } from "react";
import {
  fetchPmDecisions,
  fetchPmAuditEvents,
  type TurnDecision,
  type AuditEvent,
} from "../api";
import { useModalDialog } from "../hooks/useModalDialog";

type Props = {
  sessionId: string;
  onClose: () => void;
  /** Increment to trigger a fresh fetch (e.g. after a PM reply completes). */
  refreshNonce?: number;
};

type Tab = "decisions" | "audit";

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const today = new Date();
  const sameDay =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return hm;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${hm}`;
}

function modeBadgeClass(mode: string): string {
  const m = mode.toLowerCase();
  if (m === "awaiting_approval") return "trace-badge-block";
  if (m.includes("approve")) return "trace-badge-approve";
  if (m.includes("reject")) return "trace-badge-reject";
  if (m === "executed") return "trace-badge-executed";
  if (m === "fallback") return "trace-badge-fallback";
  if (m === "clarification" || m === "field_choices" || m === "time_slot") return "trace-badge-block";
  if (m === "lookup_error" || m === "error") return "trace-badge-reject";
  return "trace-badge-default";
}

function confidenceBand(c: number): string {
  if (c >= 0.8) return "trace-conf-high";
  if (c >= 0.5) return "trace-conf-mid";
  return "trace-conf-low";
}

function DecisionCard({ d }: { d: TurnDecision }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const primary = d.intentTrace.tasks[0];
  const intentLabel = primary ? primary.intent : "—";

  return (
    <div className={`trace-card ${open ? "open" : ""}`}>
      <button
        type="button"
        className="trace-card-header"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={open ? bodyId : undefined}
      >
        <div className="trace-card-header-main">
          <div className="trace-card-preview">{d.messagePreview || "(empty turn)"}</div>
          <div className="trace-card-meta">
            <span className="trace-intent">{intentLabel}</span>
            <span className={`trace-badge ${modeBadgeClass(d.routing.mode)}`}>
              {d.routing.mode}
            </span>
            <span
              className={`trace-conf ${confidenceBand(d.intentTrace.planConfidence)}`}
              title="Plan confidence"
            >
              {Math.round(d.intentTrace.planConfidence * 100)}%
            </span>
            <span className="trace-time">{formatWhen(d.createdAt)}</span>
            <span className="trace-duration" title="Turn duration">
              {d.durationMs} ms
            </span>
          </div>
        </div>
        <span className="trace-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="trace-card-body" id={bodyId}>
          <Section title="Extraction">
            <div className="trace-kv">
              <span className="trace-k">source</span>
              <span className="trace-v">{d.intentTrace.extractionSource || "—"}</span>
            </div>
            <div className="trace-kv">
              <span className="trace-k">plan confidence</span>
              <span className="trace-v">
                {(d.intentTrace.planConfidence * 100).toFixed(0)}%
              </span>
            </div>
            {d.intentTrace.tasks.length === 0 ? (
              <div className="trace-empty">No tasks extracted.</div>
            ) : (
              <ul className="trace-tasks">
                {d.intentTrace.tasks.map((t, i) => (
                  <li key={i}>
                    <span className="trace-task-intent">{t.intent}</span>
                    <span className={`trace-conf small ${confidenceBand(t.confidence)}`}>
                      {(t.confidence * 100).toFixed(0)}%
                    </span>
                    <span className="trace-task-source">{t.source}</span>
                    {t.missing.length > 0 && (
                      <span className="trace-missing">
                        missing: {t.missing.join(", ")}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Working memory">
            <div className="trace-kv">
              <span className="trace-k">mode</span>
              <span className="trace-v">{d.workingMemory.mode ?? "—"}</span>
            </div>
            <div className="trace-kv">
              <span className="trace-k">source</span>
              <span className="trace-v">{d.workingMemory.source ?? "—"}</span>
            </div>
            <div className="trace-kv">
              <span className="trace-k">stale</span>
              <span className="trace-v">{d.workingMemory.stale ? "yes" : "no"}</span>
            </div>
            <div className="trace-kv">
              <span className="trace-k">outcome</span>
              <span className="trace-v">{d.workingMemory.outcome}</span>
            </div>
            <div className="trace-kv">
              <span className="trace-k">after turn</span>
              <span className="trace-v">{d.wmAfter}</span>
            </div>
          </Section>

          <Section title="Routing / policy">
            <div className="trace-kv">
              <span className="trace-k">mode</span>
              <span className="trace-v">
                <span className={`trace-badge ${modeBadgeClass(d.routing.mode)}`}>
                  {d.routing.mode}
                </span>
              </span>
            </div>
            <div className="trace-kv">
              <span className="trace-k">reason</span>
              <span className="trace-v trace-v-wrap">{d.routing.reason || "—"}</span>
            </div>
            {d.blocker.type && (
              <div className="trace-kv">
                <span className="trace-k">blocker</span>
                <span className="trace-v">{d.blocker.type}</span>
              </div>
            )}
            {d.blocker.missing.length > 0 && (
              <div className="trace-kv">
                <span className="trace-k">missing</span>
                <span className="trace-v">{d.blocker.missing.join(", ")}</span>
              </div>
            )}
          </Section>

          {d.blocker.fcCandidates.length > 0 && (
            <Section title="Field-completion candidates">
              <ul className="trace-candidates">
                {d.blocker.fcCandidates.map((c, i) => (
                  <li key={i}>
                    <span className="trace-cand-label">{c.label ?? c.id ?? "—"}</span>
                    {typeof c.score === "number" && (
                      <span className="trace-cand-score">score {c.score.toFixed(2)}</span>
                    )}
                    {c.source && <span className="trace-cand-source">{c.source}</span>}
                    {c.reason && <span className="trace-cand-reason">{c.reason}</span>}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <Section title="Memory I/O">
            <div className="trace-kv">
              <span className="trace-k">read</span>
              <span className="trace-v">
                {d.memoryIO.read.length ? d.memoryIO.read.join(", ") : "—"}
              </span>
            </div>
            <div className="trace-kv">
              <span className="trace-k">written</span>
              <span className="trace-v">
                {d.memoryIO.written.length ? d.memoryIO.written.join(", ") : "—"}
              </span>
            </div>
          </Section>

          <Section title="Reply preview">
            <div className="trace-reply">{d.replyPreview || "—"}</div>
          </Section>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="trace-section">
      <div className="trace-section-title">{title}</div>
      <div className="trace-section-body">{children}</div>
    </div>
  );
}

function AuditRow({ e }: { e: AuditEvent }) {
  return (
    <div className="trace-audit-row">
      <div className="trace-audit-head">
        <span className="trace-audit-type">{e.eventType}</span>
        {e.actionType && <span className="trace-audit-action">{e.actionType}</span>}
        {e.intent && <span className="trace-audit-intent">{e.intent}</span>}
        <span className="trace-time">{formatWhen(e.createdAt)}</span>
      </div>
      {e.payloadSummary && (
        <div className="trace-audit-summary">{e.payloadSummary}</div>
      )}
      {e.resultSummary && (
        <div className="trace-audit-result">→ {e.resultSummary}</div>
      )}
      {e.approvalId && (
        <div className="trace-audit-approval">approval {e.approvalId}</div>
      )}
    </div>
  );
}

export function DecisionTracePanel({ sessionId, onClose, refreshNonce }: Props) {
  const [tab, setTab] = useState<Tab>("decisions");
  const [decisions, setDecisions] = useState<TurnDecision[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);
  const panelRef = useModalDialog<HTMLDivElement>(onClose);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    Promise.all([
      fetchPmDecisions(sessionId, 50),
      fetchPmAuditEvents(sessionId, 100),
    ])
      .then(([d, a]) => {
        if (cancelled) return;
        setDecisions(d);
        setAudit(a);
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
  }, [sessionId, refreshNonce, retryNonce]);

  // Arrow keys move between tabs, per the WAI-ARIA tabs pattern.
  function handleTabKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const next: Tab = tab === "decisions" ? "audit" : "decisions";
    setTab(next);
    document.getElementById(`trace-tab-${next}`)?.focus();
  }

  return (
    <div className="history-overlay" onClick={onClose}>
      <div
        ref={panelRef}
        className="history-panel trace-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="trace-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="history-header">
          <h2 className="history-title" id="trace-title">Decision trace</h2>
          <div className="history-header-actions">
            <div className="trace-tabs" role="tablist" aria-label="Trace view" onKeyDown={handleTabKeyDown}>
              <button
                type="button"
                role="tab"
                id="trace-tab-decisions"
                aria-selected={tab === "decisions"}
                aria-controls="trace-tabpanel"
                tabIndex={tab === "decisions" ? 0 : -1}
                className={`trace-tab ${tab === "decisions" ? "active" : ""}`}
                onClick={() => setTab("decisions")}
              >
                Turns <span className="trace-tab-count">{decisions.length}</span>
              </button>
              <button
                type="button"
                role="tab"
                id="trace-tab-audit"
                aria-selected={tab === "audit"}
                aria-controls="trace-tabpanel"
                tabIndex={tab === "audit" ? 0 : -1}
                className={`trace-tab ${tab === "audit" ? "active" : ""}`}
                onClick={() => setTab("audit")}
              >
                Audit <span className="trace-tab-count">{audit.length}</span>
              </button>
            </div>
            <button type="button" className="history-close" onClick={onClose} aria-label="Close decision trace">
              <span aria-hidden="true">✕</span>
            </button>
          </div>
        </div>

        <div
          className="trace-tabpanel"
          role="tabpanel"
          id="trace-tabpanel"
          aria-labelledby={`trace-tab-${tab}`}
        >
          {loading ? (
            <div className="history-empty" role="status">Loading trace…</div>
          ) : loadError ? (
            <div className="history-empty" role="alert">
              <p>Couldn't load the decision trace.</p>
              <button
                type="button"
                className="history-retry-btn"
                onClick={() => setRetryNonce((n) => n + 1)}
              >
                Try again
              </button>
            </div>
          ) : tab === "decisions" ? (
            decisions.length === 0 ? (
              <div className="history-empty">
                No turns recorded yet. Send Kairo a message to generate a trace.
              </div>
            ) : (
              <div className="trace-list">
                {decisions.map((d) => (
                  <DecisionCard key={d.id} d={d} />
                ))}
              </div>
            )
          ) : audit.length === 0 ? (
            <div className="history-empty">No audit events for this session.</div>
          ) : (
            <div className="trace-audit-list">
              {audit.map((e) => (
                <AuditRow key={e.id} e={e} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
