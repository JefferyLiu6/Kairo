import { MODES, type Mode } from "../types";

type Props = {
  mode: Mode;
  onMode: (m: Mode) => void;
  onClear: () => void;
  onHistory: () => void;
};

export function Sidebar({ mode, onMode, onClear, onHistory }: Props) {
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="sidebar-logo-icon">✦</div>
        Assistant
      </div>

      <div className="sidebar-section-label">Mode</div>

      {MODES.map((m) => (
        <button
          key={m.id}
          className={`mode-btn ${mode === m.id ? "active" : ""}`}
          onClick={() => onMode(m.id)}
          title={m.description}
        >
          <span className="mode-icon">{m.icon}</span>
          {m.label}
        </button>
      ))}

      <div className="sidebar-bottom">
        <button className="clear-btn" onClick={onHistory} title="Browse past sessions">
          <span className="mode-icon">⊟</span>
          Chat history
        </button>
        <button className="clear-btn" onClick={onClear} title="Start a new conversation">
          <span className="mode-icon">↺</span>
          New chat
        </button>
      </div>
    </aside>
  );
}
