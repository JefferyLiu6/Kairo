type Props = {
  mode?: string;
  onMode?: unknown;
  onHistory: () => void;
  onSchedule?: () => void;
  onTrace?: () => void;
  onStopAgent?: () => void;
  agentBusy?: boolean;
  onClear: () => void;
  callCount?: number;
  callLimit?: number;
};

export function TopNav({
  onHistory,
  onSchedule,
  onTrace,
  onStopAgent,
  agentBusy,
  onClear,
  callCount,
  callLimit,
}: Props) {
  return (
    <nav className="topnav">
      {/* Kairo logo */}
      <div className="topnav-logo">
        <div className="topnav-logo-mark">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
            <path
              d="M20 2H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h3l2.5 3L12 19h8a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"
              fill="white"
              fillOpacity="0.95"
            />
            <circle cx="8" cy="11" r="1.3" fill="#14b8a6" />
            <circle cx="12" cy="11" r="1.3" fill="#14b8a6" />
            <circle cx="16" cy="11" r="1.3" fill="#14b8a6" />
          </svg>
        </div>
        <div className="topnav-logo-wordmark">
          <span className="topnav-logo-name">Kairo</span>
          <span className="topnav-logo-sub">Personal AI command center</span>
        </div>
      </div>

      {/* Breadcrumb + PM pill */}
      <div className="topnav-pm-pill">
        <div className="topnav-pm-breadcrumb">
          <span>Agents</span>
          <span className="topnav-pm-breadcrumb-sep">/</span>
        </div>
        <div className="topnav-pm-badge">
          <div className="topnav-pm-badge-icon">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
              <rect x="4" y="6" width="16" height="12" rx="3" stroke="white" strokeWidth="2" />
              <circle cx="9" cy="12" r="1.5" fill="white" />
              <circle cx="15" cy="12" r="1.5" fill="white" />
            </svg>
          </div>
          <span className="topnav-pm-badge-label">PM</span>
          <div className="topnav-pm-status">
            <span className={`topnav-pm-status-dot ${agentBusy ? "working" : "ready"}`} />
            <span className="topnav-pm-status-label">{agentBusy ? "working" : "ready"}</span>
          </div>
        </div>
      </div>

      {/* Controls */}
      <div className="topnav-actions">
        {callLimit != null && (
          <div className={`topnav-credits${(callCount ?? 0) >= callLimit ? " exhausted" : ""}`} title="Demo credits remaining">
            <span className="topnav-credits-count">{Math.max(0, callLimit - (callCount ?? 0))}</span>
            <span className="topnav-credits-label">credits</span>
          </div>
        )}
        {agentBusy && onStopAgent && (
          <button
            type="button"
            className="topnav-action-btn topnav-stop-btn"
            onClick={onStopAgent}
            title="Stop agent"
          >
            <svg width="12" height="12" viewBox="0 0 24 24">
              <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
            </svg>
          </button>
        )}
        {onSchedule && (
          <button className="topnav-action-btn" onClick={onSchedule} title="Toggle calendar">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <rect x="3" y="4" width="18" height="18" rx="3" stroke="currentColor" strokeWidth="1.5" />
              <path d="M16 2v4M8 2v4M3 10h18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        )}
        {onTrace && (
          <button className="topnav-action-btn" onClick={onTrace} title="Decision trace">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path d="M18 20V10M12 20V4M6 20v-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        )}
        <button className="topnav-action-btn" onClick={onHistory} title="History">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke="currentColor" strokeWidth="1.5" />
          </svg>
        </button>
        <button className="topnav-action-btn" onClick={onClear} title="New chat">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
            <path d="M23 4v6h-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M1 20v-6h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </div>
    </nav>
  );
}
