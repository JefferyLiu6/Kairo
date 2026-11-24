import { useEffect, useState } from "react";
import { fetchApprovalStatus, type ApprovalStatus } from "../api";

type Props = {
  id: string;
  summary: string;
  risk: string;
  onAction: (text: string) => void;
  disabled: boolean;
};

type Outcome = "approve" | "reject" | "failed" | "expired";

/** What a stored status means for the card, or null while it can still be decided on. */
function outcomeOf(status: ApprovalStatus["status"]): Outcome | null {
  if (status === "pending") return null;
  if (status === "rejected") return "reject";
  if (status === "failed") return "failed";
  if (status === "expired") return "expired";
  return "approve"; // executing, executed, approved
}

const OUTCOME_TEXT: Record<Outcome, string> = {
  approve: "✓ Approved",
  reject: "✗ Rejected",
  failed: "Approved, but it couldn't be completed",
  expired: "Expired. Ask again if you still want this.",
};

function formatDeadline(iso: string): string {
  const at = new Date(iso);
  const time = at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const sameDay = at.toDateString() === new Date().toDateString();
  return sameDay ? time : `${at.toLocaleDateString([], { weekday: "short" })} ${time}`;
}

export function ApprovalCard({ id, summary, risk, onAction, disabled }: Props) {
  const [chosen, setChosen] = useState<Outcome | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);

  // The card may be from an earlier visit: ask where the approval stands now.
  useEffect(() => {
    if (disabled) return;
    let live = true;
    fetchApprovalStatus(id).then((found) => {
      if (!live || !found) return;
      setExpiresAt(found.expiresAt);
      const outcome = outcomeOf(found.status);
      if (outcome) setChosen((current) => current ?? outcome);
    });
    return () => {
      live = false;
    };
  }, [id, disabled]);

  // A card left open past its deadline turns into an expired one.
  useEffect(() => {
    if (!expiresAt || chosen) return;
    const left = new Date(expiresAt).getTime() - Date.now();
    if (left > 2 ** 31 - 1) return;
    const timer = window.setTimeout(() => setChosen((current) => current ?? "expired"), Math.max(0, left));
    return () => window.clearTimeout(timer);
  }, [expiresAt, chosen]);

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
          {OUTCOME_TEXT[chosen]}
        </div>
      ) : (
        <>
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
          {expiresAt && <p className="approval-deadline">Expires {formatDeadline(expiresAt)} if not decided</p>}
        </>
      )}
    </div>
  );
}
