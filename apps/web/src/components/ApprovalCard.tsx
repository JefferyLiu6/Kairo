import { useState } from "react";

const APPROVAL_RE =
  /^Approval required \[([a-f0-9]+)\]:\s*(.+?)\s*\nRisk:\s*(\w+)\./is;

export function parseApproval(content: string) {
  const m = content.match(APPROVAL_RE);
  if (!m) return null;
  return { id: m[1], summary: m[2].trim(), risk: m[3].toLowerCase() };
}

type Props = {
  id: string;
  summary: string;
  risk: string;
  onAction: (text: string) => void;
  disabled: boolean;
};

export function ApprovalCard({ id, summary, risk, onAction, disabled }: Props) {
  const [chosen, setChosen] = useState<"approve" | "reject" | null>(null);

  const handle = (action: "approve" | "reject") => {
    if (chosen || disabled) return;
    setChosen(action);
    onAction(`${action} ${id}`);
  };

  const riskLabel = risk.charAt(0).toUpperCase() + risk.slice(1);
  const [heading, ...rest] = summary.split("\n").map((line) => line.trim()).filter(Boolean);
  const items = rest.map((line) => line.replace(/^-\s*/, ""));

  return (
    <div className={`approval-card approval-risk-${risk}`}>
      <div className="approval-header">
        <span className="approval-title">Action Required</span>
        <span className={`approval-risk-badge approval-risk-${risk}`}>
          {riskLabel}
        </span>
      </div>

      {/* A batch's summary is a heading line and one "- " line for each change. */}
      <p className="approval-summary">{heading}</p>
      {items.length > 0 && (
        <ul className="approval-items">
          {items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}

      {chosen ? (
        <div className={`approval-result approval-result-${chosen}`} role="status">
          {chosen === "approve" ? "✓ Approved" : "✗ Rejected"}
        </div>
      ) : (
        <div className="approval-actions">
          <button
            type="button"
            className="approval-btn approval-btn-approve"
            onClick={() => handle("approve")}
            disabled={disabled}
          >
            ✓ {items.length > 1 ? "Approve all" : "Approve"}
          </button>
          <button
            type="button"
            className="approval-btn approval-btn-reject"
            onClick={() => handle("reject")}
            disabled={disabled}
          >
            ✗ {items.length > 1 ? "Reject all" : "Reject"}
          </button>
        </div>
      )}
    </div>
  );
}
