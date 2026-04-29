type Props = {
  mode?: string;
  onMode?: unknown;
  onHistory: () => void;
  onSchedule?: () => void;
  onTrace?: () => void;
  onStopAgent?: () => void;
  agentBusy?: boolean;
  onNewChat: () => void;
  user?: { displayName: string; email: string };
  onLogout?: () => void;
};

export function TopNav({
  onHistory,
  onSchedule,
  onTrace,
  onStopAgent,
  agentBusy,
  onNewChat,
  user,
  onLogout,
}: Props) {
  return (
    <nav className="topnav">
      {/* Kairo logo */}
      <div className="topnav-logo">
        <div className="topnav-logo-mark">
          <img className="topnav-logo-img" src="/kairo-logo.svg" alt="" aria-hidden="true" />
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
        <button className="topnav-action-btn" onClick={onNewChat} title="New chat">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
            <path d="M23 4v6h-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M1 20v-6h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
        {user && (
          <div className="topnav-user">
            <span className="topnav-user-name" title={user.email}>{user.displayName}</span>
            {onLogout && (
              <button className="topnav-action-btn topnav-logout-btn" onClick={onLogout} title="Sign out">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  <polyline points="16 17 21 12 16 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  <line x1="21" y1="12" x2="9" y2="12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            )}
          </div>
        )}
      </div>
    </nav>
  );
}
