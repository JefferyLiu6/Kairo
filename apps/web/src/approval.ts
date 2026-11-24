// Finds the approval block the backend appends to a reply:
//
//   Approval required [<id>]: <summary, or a heading and "- " lines for a batch>
//   Risk: <level>.
//   Reply `approve <id>` to go ahead, or `reject <id>` to cancel.
//
// A turn that also did something safe right away puts that first ("Added task: ...")
// and the block after it, so the block can sit anywhere in the reply, not only at the start.
const APPROVAL_RE =
  /(^|\s)Approval required \[([a-f0-9]+)\]:\s*(.+?)\s*\n[ \t]*Risk:\s*(\w+)\.[ \t]*(?:\n?[ \t]*Reply `approve \2` to go ahead, or `reject \2` to cancel\.)?/is;

export type ParsedApproval = {
  id: string;
  summary: string;
  risk: string;
  /** What the reply says before the block, such as changes already made. */
  before: string;
  /** Anything the reply says after the block. */
  after: string;
};

export function parseApproval(content: string): ParsedApproval | null {
  const m = APPROVAL_RE.exec(content);
  if (!m) return null;
  const start = m.index + m[1].length;
  return {
    id: m[2],
    summary: m[3].trim(),
    risk: m[4].toLowerCase(),
    before: content.slice(0, start).trim(),
    after: content.slice(m.index + m[0].length).trim(),
  };
}
