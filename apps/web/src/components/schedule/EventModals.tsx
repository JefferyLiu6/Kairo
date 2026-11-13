import { useState, type FormEvent } from "react";
import { useModalDialog } from "../../hooks/useModalDialog";
import { formatTimeRange } from "../../lib/scheduleCalendar";
import type { ScheduleEntry } from "../../types";

const WEEKDAYS = [
  { v: "", label: "Doesn't repeat" },
  { v: "0", label: "Sunday" },
  { v: "1", label: "Monday" },
  { v: "2", label: "Tuesday" },
  { v: "3", label: "Wednesday" },
  { v: "4", label: "Thursday" },
  { v: "5", label: "Friday" },
  { v: "6", label: "Saturday" },
];

type EditorProps = {
  entry: ScheduleEntry;
  /** True when the entry hasn't been saved anywhere yet. */
  isNew: boolean;
  /** A save or delete is in flight (Google writes are async). */
  busy?: boolean;
  /** Failure from the last save or delete, shown inside the dialog. */
  error?: string | null;
  onClose: () => void;
  onSave: (e: ScheduleEntry) => void;
  onDelete: (id: string) => void;
};

export function EventEditorModal({ entry, isNew, busy = false, error, onClose, onSave, onDelete }: EditorProps) {
  const [draft, setDraft] = useState<ScheduleEntry>({ ...entry });
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  // Set once the delete step has been opened, so backing out of it returns
  // focus to the Delete button rather than dropping it on the page.
  const [visitedConfirm, setVisitedConfirm] = useState(false);
  const panelRef = useModalDialog<HTMLFormElement>(onClose);

  // "HH:MM" strings compare correctly as text.
  const timeError =
    draft.start && draft.end && draft.end <= draft.start ? "End time must be after the start time." : null;

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (busy || timeError) return;
    onSave(draft);
  }

  return (
    <div className="sch-modal-overlay" onClick={onClose} role="presentation">
      <form
        ref={panelRef}
        className="sch-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sch-edit-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
        noValidate
      >
        <h2 id="sch-edit-title" className="sch-modal-title">
          {isNew ? "New event" : "Edit event"}
        </h2>
        <label className="sch-field">
          <span>Title</span>
          <input
            className="sch-input"
            value={draft.title}
            onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
            placeholder="What is it?"
            data-autofocus
          />
        </label>
        <label className="sch-field">
          <span>Date (one-off)</span>
          <input
            type="date"
            className="sch-input sch-input-date"
            value={draft.date}
            onChange={(e) => setDraft((d) => ({ ...d, date: e.target.value }))}
          />
        </label>
        <label className="sch-field">
          <span>Repeat weekly</span>
          <select
            className="sch-select"
            value={draft.weekday === null ? "" : String(draft.weekday)}
            onChange={(e) => {
              const v = e.target.value;
              setDraft((d) => ({ ...d, weekday: v === "" ? null : parseInt(v, 10) }));
            }}
          >
            {WEEKDAYS.map((w) => (
              <option key={w.v || "none"} value={w.v}>
                {w.label}
              </option>
            ))}
          </select>
        </label>
        <div className="sch-field-row">
          <label className="sch-field sch-field-half">
            <span>Start</span>
            <input
              type="time"
              className="sch-input sch-input-time"
              value={draft.start}
              onChange={(e) => setDraft((d) => ({ ...d, start: e.target.value }))}
            />
          </label>
          <label className="sch-field sch-field-half">
            <span>End</span>
            <input
              type="time"
              className="sch-input sch-input-time"
              value={draft.end}
              onChange={(e) => setDraft((d) => ({ ...d, end: e.target.value }))}
              aria-invalid={timeError ? true : undefined}
              aria-describedby={timeError ? "sch-edit-time-error" : undefined}
            />
          </label>
        </div>
        {timeError && (
          <p className="sch-field-error" id="sch-edit-time-error" role="alert">{timeError}</p>
        )}
        <label className="sch-field">
          <span>Notes</span>
          <input
            className="sch-input"
            value={draft.notes}
            onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))}
            placeholder="Optional"
          />
        </label>
        <p className="sch-modal-hint">
          Leave times empty for an all-day item. Use either a specific date or a weekly repeat, or neither for a floating
          reminder (shown in the top strip).
        </p>

        {error && <p className="sch-field-error" role="alert">{error}</p>}

        {confirmingDelete ? (
          // Distinct keys so React mounts fresh buttons (and autoFocus runs)
          // instead of recycling the ones from the other branch.
          <div key="confirm" className="sch-modal-actions" role="group" aria-label="Confirm delete">
            <span className="sch-modal-confirm-text">
              Delete this event?{entry.source === "google" ? " It will be removed from Google Calendar." : ""}
            </span>
            <button
              type="button"
              className="sch-btn sch-btn-secondary"
              onClick={() => setConfirmingDelete(false)}
              disabled={busy}
              // The button that opened this step just unmounted; land on the safe choice.
              autoFocus
            >
              Keep
            </button>
            <button
              type="button"
              className="sch-btn sch-btn-danger"
              onClick={() => onDelete(entry.id)}
              disabled={busy}
            >
              {busy ? "Deleting…" : "Delete"}
            </button>
          </div>
        ) : (
          <div key="actions" className="sch-modal-actions">
            {!isNew && (
              <button
                type="button"
                className="sch-btn sch-btn-danger-text"
                onClick={() => {
                  setConfirmingDelete(true);
                  setVisitedConfirm(true);
                }}
                disabled={busy}
                autoFocus={visitedConfirm}
              >
                Delete
              </button>
            )}
            <button type="button" className="sch-btn sch-btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="sch-btn sch-btn-primary" disabled={busy || Boolean(timeError)}>
              {busy ? "Saving…" : isNew ? "Add event" : "Save changes"}
            </button>
          </div>
        )}
      </form>
    </div>
  );
}

export function GoogleEventModal({ entry, onClose }: { entry: ScheduleEntry; onClose: () => void }) {
  const panelRef = useModalDialog<HTMLDivElement>(onClose);

  return (
    <div className="sch-modal-overlay" onClick={onClose} role="presentation">
      <div
        ref={panelRef}
        className="sch-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sch-google-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="sch-google-title" className="sch-modal-title">
          {entry.title || "Google Calendar event"}
        </h2>
        <dl className="sch-google-detail-grid">
          <dt>Source</dt>
          <dd>Google Calendar</dd>
          <dt>Date</dt>
          <dd>{entry.date || "All day"}</dd>
          <dt>Time</dt>
          <dd>{formatTimeRange(entry) || "All day"}</dd>
          {entry.location && (
            <>
              <dt>Location</dt>
              <dd>{entry.location}</dd>
            </>
          )}
        </dl>
        {entry.notes && <p className="sch-google-notes">{entry.notes}</p>}
        <p className="sch-modal-hint">
          This calendar is connected read-only, so this event can only be changed in Google Calendar.
        </p>
        <div className="sch-modal-actions">
          <button type="button" className="sch-btn sch-btn-primary" onClick={onClose} data-autofocus>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
