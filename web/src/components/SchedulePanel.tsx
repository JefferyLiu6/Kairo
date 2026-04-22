import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  autoSyncGoogleCalendar,
  createGoogleCalendarEvent,
  deleteGoogleCalendarEvent,
  disconnectGoogleCalendarAccount,
  fetchGoogleCalendarAccounts,
  fetchGoogleCalendarEvents,
  fetchPmSchedule,
  fetchUpcomingEvents,
  savePmSchedule,
  syncGoogleCalendar,
  updateGoogleCalendarEvent,
  type GoogleCalendarAccount,
  type GoogleCalendarEvent,
  type UpcomingEvent,
} from "../api";
import type { ScheduleEntry } from "../types";
import {
  type CalendarView,
  type TimedLayout,
  CAL_DAY_START_HOUR,
  CAL_DAY_END_HOUR,
  PX_PER_HOUR,
  formatYMD,
  addDays,
  addMonths,
  startOfWeekSunday,
  isSameCalendarDay,
  entryMatchesDay,
  layoutTimedEventsForDay,
  allDayEntriesForDay,
  entryHue,
  formatTimeRange,
  longDateLabel,
  monthYearLabel,
  shortWeekdayLabel,
  dayNum,
  buildMonthCells,
  isUndatedEntry,
  isRecurringEntry,
} from "../lib/scheduleCalendar";

function useIsMobile() {
  const [mobile, setMobile] = useState(() => typeof window !== "undefined" && window.innerWidth <= 760);
  useEffect(() => {
    const mql = window.matchMedia("(max-width: 760px)");
    const handler = (e: MediaQueryListEvent) => setMobile(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);
  return mobile;
}

const WEEKDAYS = [
  { v: "", label: "—" },
  { v: "0", label: "Sun" },
  { v: "1", label: "Mon" },
  { v: "2", label: "Tue" },
  { v: "3", label: "Wed" },
  { v: "4", label: "Thu" },
  { v: "5", label: "Fri" },
  { v: "6", label: "Sat" },
];

function emptyRow(overrides: Partial<ScheduleEntry> = {}): ScheduleEntry {
  return {
    id: crypto.randomUUID(),
    title: "",
    date: "",
    weekday: null,
    start: "",
    end: "",
    notes: "",
    ...overrides,
  };
}

function splitGoogleDateTime(value: string): { date: string; time: string } {
  if (!value) return { date: "", time: "" };
  if (!value.includes("T")) return { date: value.slice(0, 10), time: "" };
  const [date, rest] = value.split("T", 2);
  return { date: date.slice(0, 10), time: rest.slice(0, 5) };
}

function googleEventToScheduleEntry(event: GoogleCalendarEvent): ScheduleEntry {
  const start = splitGoogleDateTime(event.startAt);
  const end = splitGoogleDateTime(event.endAt);
  return {
    id: `google-${event.id}`,
    title: event.title || "Busy",
    date: start.date,
    weekday: null,
    start: start.time,
    end: end.time,
    notes: event.notes,
    source: "google",
    readOnly: true,
    providerEventId: event.providerEventId,
    location: event.location,
    timezone: event.timezone,
  };
}

function gridTotalPx(): number {
  return (CAL_DAY_END_HOUR + 1 - CAL_DAY_START_HOUR) * PX_PER_HOUR;
}

function minutesToY(min: number): number {
  const topMin = CAL_DAY_START_HOUR * 60;
  return ((min - topMin) / 60) * PX_PER_HOUR;
}

function NowLine({ day }: { day: Date }) {
  const now = new Date();
  if (!isSameCalendarDay(day, now)) return null;
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const topMin = CAL_DAY_START_HOUR * 60;
  const bottomMin = (CAL_DAY_END_HOUR + 1) * 60;
  if (nowMin < topMin || nowMin > bottomMin) return null;
  const y = minutesToY(nowMin);
  return (
    <div className="sch-now-line" style={{ top: y }}>
      <span className="sch-now-badge">
        {now.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}
      </span>
    </div>
  );
}

function EventPill({
  entry,
  onClick,
  compact,
}: {
  entry: ScheduleEntry;
  onClick: () => void;
  compact?: boolean;
}) {
  const hue = entryHue(entry.id);
  const recurring = isRecurringEntry(entry);
  const isGoogle = entry.source === "google";
  return (
    <button
      type="button"
      className={`sch-event-pill ${compact ? "sch-event-pill-compact" : ""} ${isGoogle ? "sch-event-google" : ""}`}
      style={{ "--sch-h": hue } as CSSProperties}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={entry.notes || entry.title}
    >
      {recurring && <span className="sch-recur" aria-hidden>↻</span>}
      <span className="sch-event-pill-title">{entry.title || "Untitled"}</span>
      {isGoogle && <span className="sch-event-source">Google</span>}
      {!compact && formatTimeRange(entry) && (
        <span className="sch-event-pill-time">{formatTimeRange(entry)}</span>
      )}
    </button>
  );
}

function TimedEventBlock({
  layout,
  onClick,
  onDragStart,
  onDragEnd,
}: {
  layout: TimedLayout;
  onClick: () => void;
  onDragStart?: (entry: ScheduleEntry) => void;
  onDragEnd?: () => void;
}) {
  const { entry, startMin, endMin, lane, laneCount } = layout;
  const hue = entryHue(entry.id);
  const top = minutesToY(startMin);
  const h = Math.max(minutesToY(endMin) - top, 20);
  const w = 100 / laneCount;
  const left = lane * w;
  const recurring = isRecurringEntry(entry);
  const isGoogle = entry.source === "google";
  return (
    <button
      type="button"
      draggable={!isGoogle && !!onDragStart}
      className={`sch-timed-block ${isGoogle ? "sch-event-google" : ""}`}
      style={{ top, height: h, left: `${left}%`, width: `${w}%`, "--sch-h": hue } as CSSProperties}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      onDragStart={onDragStart ? (e) => { e.stopPropagation(); e.dataTransfer.effectAllowed = "move"; onDragStart(entry); } : undefined}
      onDragEnd={onDragEnd}
      title={entry.notes || entry.title}
    >
      {recurring && <span className="sch-timed-recur">↻</span>}
      <span className="sch-timed-title">{entry.title || "Untitled"}</span>
      <span className="sch-timed-sub">{isGoogle ? "Google · " : ""}{formatTimeRange(entry)}</span>
    </button>
  );
}

function TimeGridColumn({
  day, entries, onBackgroundClick, onEventClick,
  draggingTask, draggingEntry, dropTarget,
  onCellDragOver, onCellDrop, onDragLeave,
  onEventDragStart, onEventDragEnd,
}: {
  day: Date;
  entries: ScheduleEntry[];
  onBackgroundClick: (hour: number) => void;
  onEventClick: (e: ScheduleEntry) => void;
} & DragPassProps) {
  const layouts = useMemo(() => layoutTimedEventsForDay(day, entries), [day, entries]);
  const hours = useMemo(() => {
    const list: number[] = [];
    for (let h = CAL_DAY_START_HOUR; h <= CAL_DAY_END_HOUR; h++) list.push(h);
    return list;
  }, []);

  const getHourFromY = (e: React.DragEvent) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const y = e.clientY - rect.top;
    return Math.max(CAL_DAY_START_HOUR, Math.min(CAL_DAY_END_HOUR, Math.floor(y / PX_PER_HOUR) + CAL_DAY_START_HOUR));
  };

  return (
    <div className="sch-time-col">
      <div
        className="sch-time-col-inner"
        style={{ height: gridTotalPx() }}
        onDragOver={onCellDragOver ? (e) => { e.preventDefault(); onCellDragOver(day, getHourFromY(e)); } : undefined}
        onDrop={onCellDrop ? (e) => { e.preventDefault(); onCellDrop(day, getHourFromY(e)); } : undefined}
        onDragLeave={onDragLeave}
      >
        {hours.map((h) => {
          const isDropTarget = (draggingTask || draggingEntry) && dropTarget &&
            isSameCalendarDay(dropTarget.day, day) && dropTarget.hour === h;
          return (
            <button
              key={h}
              type="button"
              className={`sch-hour-slot${isDropTarget ? " sch-hour-drop-target" : ""}`}
              style={{ height: PX_PER_HOUR }}
              aria-label={`Add event at ${h}:00`}
              onClick={() => onBackgroundClick(h)}
            >
              {isDropTarget && (draggingTask || draggingEntry) && (
                <div className="sch-drop-ghost">
                  <span>{draggingTask?.title ?? draggingEntry?.title}</span>
                </div>
              )}
            </button>
          );
        })}
        <div className="sch-timed-layer" style={{ height: gridTotalPx(), pointerEvents: (draggingTask || draggingEntry) ? "none" : undefined }}>
          {layouts.map((L) => (
            <TimedEventBlock key={L.entry.id} layout={L} onClick={() => onEventClick(L.entry)}
              onDragStart={onEventDragStart} onDragEnd={onEventDragEnd} />
          ))}
          <NowLine day={day} />
        </div>
      </div>
    </div>
  );
}

function EventEditorModal({
  entry,
  onClose,
  onSave,
  onDelete,
}: {
  entry: ScheduleEntry;
  onClose: () => void;
  onSave: (e: ScheduleEntry) => void;
  onDelete: (id: string) => void;
}) {
  const [draft, setDraft] = useState<ScheduleEntry>({ ...entry });

  return (
    <div className="sch-modal-overlay" onClick={onClose} role="presentation">
      <div className="sch-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-labelledby="sch-edit-title">
        <h2 id="sch-edit-title" className="sch-modal-title">
          {entry.title ? "Edit event" : "New event"}
        </h2>
        <label className="sch-field">
          <span>Title</span>
          <input
            className="sch-input"
            value={draft.title}
            onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
            placeholder="What is it?"
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
              <option key={w.v || "x"} value={w.v}>
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
            />
          </label>
        </div>
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
        <div className="sch-modal-actions">
          <button type="button" className="sch-btn sch-btn-danger-text" onClick={() => onDelete(entry.id)}>
            Delete
          </button>
          <button type="button" className="sch-btn sch-btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="sch-btn sch-btn-primary" onClick={() => onSave(draft)}>
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}

function GoogleEventModal({
  entry,
  onClose,
}: {
  entry: ScheduleEntry;
  onClose: () => void;
}) {
  return (
    <div className="sch-modal-overlay" onClick={onClose} role="presentation">
      <div className="sch-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-labelledby="sch-google-title">
        <h2 id="sch-google-title" className="sch-modal-title">
          {entry.title || "Google Calendar event"}
        </h2>
        <div className="sch-google-detail-grid">
          <span>Source</span>
          <strong>Google Calendar</strong>
          <span>Date</span>
          <strong>{entry.date || "All day"}</strong>
          <span>Time</span>
          <strong>{formatTimeRange(entry) || "All day"}</strong>
          {entry.location && (
            <>
              <span>Location</span>
              <strong>{entry.location}</strong>
            </>
          )}
        </div>
        {entry.notes && <p className="sch-google-notes">{entry.notes}</p>}
        <p className="sch-modal-hint">
          This is a read-only mirrored event. Edits still need to happen in Google Calendar.
        </p>
        <div className="sch-modal-actions">
          <button type="button" className="sch-btn sch-btn-primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

type DraggableTask = {
  id: string;
  title: string;
  duration: number;
  list: string;
  priority: "high" | "med" | "low";
};

const PRIORITY_COLOR: Record<string, string> = {
  high: "oklch(0.50 0.18 25)",
  med:  "oklch(0.58 0.14 55)",
  low:  "oklch(0.58 0.14 80)",
};

const LIST_META: Record<string, { color: string; hours: number; max: number }> = {
  "Work":             { color: "oklch(0.58 0.14 220)", hours: 12.5, max: 20 },
  "Family & Friends": { color: "oklch(0.58 0.14 55)",  hours: 5,    max: 10 },
  "Personal":         { color: "oklch(0.58 0.14 168)", hours: 4,    max: 10 },
  "Health":           { color: "oklch(0.58 0.14 145)", hours: 3,    max: 5  },
  "Learning":         { color: "oklch(0.58 0.14 275)", hours: 2,    max: 8  },
};

const SAMPLE_TASKS: DraggableTask[] = [
  { id: "t1", title: "Review Q2 roadmap",      duration: 60,  list: "Personal", priority: "high" },
  { id: "t2", title: "Write standup notes",     duration: 30,  list: "Personal", priority: "high" },
  { id: "t3", title: "Prepare investor deck",   duration: 120, list: "Personal", priority: "med"  },
  { id: "t4", title: "Code review PR #42",      duration: 45,  list: "Work",     priority: "med"  },
  { id: "t5", title: "Update documentation",    duration: 60,  list: "Work",     priority: "low"  },
  { id: "t6", title: "Sync with design team",   duration: 30,  list: "Work",     priority: "low"  },
];

type Props = {
  sessionId: string;
  onClose: () => void;
  /** Increment after a Kairo reply so the panel reloads if the agent edited the calendar. */
  refreshNonce?: number;
  /** When true, renders without an overlay wrapper — used as an inline sidebar. */
  sidebar?: boolean;
  /** When true, renders full-screen with task sidebar and drag-and-drop. */
  fullscreen?: boolean;
};

const AI_INSIGHTS = [
  "📅 Check your week ahead — I can help move conflicts.",
  "🎯 Block deep work now before meetings fill the day.",
  "⚡ Ask me to reschedule anything that doesn't fit.",
  "🔋 Connect Google to see your real calendar here.",
];

export function SchedulePanel({ sessionId, onClose, refreshNonce = 0, sidebar = false, fullscreen = false }: Props) {
  const isMobile = useIsMobile();
  const [insightIdx, setInsightIdx] = useState(0);
  const insightTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [tasks, setTasks] = useState<DraggableTask[]>(SAMPLE_TASKS);
  const [draggingTask, setDraggingTask] = useState<DraggableTask | null>(null);
  const [draggingEntry, setDraggingEntry] = useState<ScheduleEntry | null>(null);
  const [dropTarget, setDropTarget] = useState<{ day: Date; hour: number } | null>(null);

  useEffect(() => {
    insightTimer.current = setInterval(() => setInsightIdx((i) => (i + 1) % AI_INSIGHTS.length), 5000);
    return () => { if (insightTimer.current) clearInterval(insightTimer.current); };
  }, []);
  const [entries, setEntries] = useState<ScheduleEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [view, setView] = useState<CalendarView>("day");
  const [cursor, setCursor] = useState(() => new Date());
  const [editing, setEditing] = useState<ScheduleEntry | null>(null);
  const [viewingGoogle, setViewingGoogle] = useState<ScheduleEntry | null>(null);
  const [upcoming, setUpcoming] = useState<UpcomingEvent[]>([]);
  const [googleAccounts, setGoogleAccounts] = useState<GoogleCalendarAccount[]>([]);
  const [googleEntries, setGoogleEntries] = useState<ScheduleEntry[]>([]);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [googleSyncing, setGoogleSyncing] = useState(false);
  const [googleDisconnecting, setGoogleDisconnecting] = useState(false);
  const [googleError, setGoogleError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPmSchedule(sessionId)
      .then((d) => {
        if (!cancelled) {
          setEntries(d.entries.length ? d.entries : []);
          setDirty(false);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    fetchUpcomingEvents(sessionId, 1)
      .then((evs) => { if (!cancelled) setUpcoming(evs); })
      .catch(() => { /* silently ignore */ });
    return () => {
      cancelled = true;
    };
  }, [sessionId, refreshNonce]);

  const weekStart = useMemo(() => startOfWeekSunday(cursor), [cursor]);
  const weekDays = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)), [weekStart]);
  const googleRange = useMemo(() => {
    if (view === "day") return { start: formatYMD(cursor), end: formatYMD(addDays(cursor, 1)) };
    if (view === "week") return { start: formatYMD(weekStart), end: formatYMD(addDays(weekStart, 7)) };
    const cells = buildMonthCells(cursor);
    return { start: formatYMD(cells[0]), end: formatYMD(addDays(cells[cells.length - 1], 1)) };
  }, [cursor, view, weekStart]);
  const googleConnected = googleAccounts.length > 0;
  const visibleEntries = useMemo(
    () => (googleConnected ? googleEntries : entries),
    [entries, googleConnected, googleEntries],
  );
  const googleWritable = useMemo(
    () => googleAccounts.some((account) => account.scopes.includes("https://www.googleapis.com/auth/calendar.events")),
    [googleAccounts],
  );

  const goToday = useCallback(() => setCursor(new Date()), []);

  const loadGoogleCalendar = useCallback(async () => {
    setGoogleLoading(true);
    setGoogleError(null);
    try {
      let autoSyncError: unknown = null;
      try {
        await autoSyncGoogleCalendar(sessionId);
      } catch (e) {
        autoSyncError = e;
      }
      const [accounts, events] = await Promise.all([
        fetchGoogleCalendarAccounts(sessionId),
        fetchGoogleCalendarEvents(sessionId, googleRange.start, googleRange.end),
      ]);
      setGoogleAccounts(accounts);
      setGoogleEntries(events.map(googleEventToScheduleEntry));
      const failedAccount = accounts.find((account) => account.syncStatus === "error" && account.lastSyncError);
      if (failedAccount) {
        setGoogleError(`Google auto-sync failed: ${failedAccount.lastSyncError}`);
      } else if (autoSyncError && accounts.length > 0) {
        setGoogleError(autoSyncError instanceof Error ? autoSyncError.message : String(autoSyncError));
      }
    } catch (e) {
      setGoogleError(e instanceof Error ? e.message : String(e));
    } finally {
      setGoogleLoading(false);
    }
  }, [sessionId, googleRange.start, googleRange.end]);

  useEffect(() => {
    void loadGoogleCalendar();
  }, [loadGoogleCalendar, refreshNonce]);

  useEffect(() => {
    const reloadOnFocus = () => {
      void loadGoogleCalendar();
    };
    window.addEventListener("focus", reloadOnFocus);
    return () => window.removeEventListener("focus", reloadOnFocus);
  }, [loadGoogleCalendar]);

  const connectGoogle = useCallback(() => {
    window.open(
      `/personal-manager/google-calendar/connect?sessionId=${encodeURIComponent(sessionId)}`,
      "_blank",
      "noopener,noreferrer",
    );
  }, [sessionId]);

  const syncGoogle = useCallback(async () => {
    setGoogleSyncing(true);
    setGoogleError(null);
    try {
      await syncGoogleCalendar(sessionId);
      await loadGoogleCalendar();
    } catch (e) {
      setGoogleError(e instanceof Error ? e.message : String(e));
    } finally {
      setGoogleSyncing(false);
    }
  }, [sessionId, loadGoogleCalendar]);

  const disconnectGoogle = useCallback(async () => {
    setGoogleDisconnecting(true);
    setGoogleError(null);
    try {
      await Promise.all(
        googleAccounts.map((account) => disconnectGoogleCalendarAccount(sessionId, account.id)),
      );
      setGoogleAccounts([]);
      setGoogleEntries([]);
    } catch (e) {
      setGoogleError(e instanceof Error ? e.message : String(e));
    } finally {
      setGoogleDisconnecting(false);
    }
  }, [sessionId, googleAccounts]);

  const openEntry = useCallback((entry: ScheduleEntry) => {
    if (entry.source === "google") {
      setEditing({ ...entry });
      return;
    }
    setEditing({ ...entry });
  }, []);

  const navPrev = useCallback(() => {
    if (view === "day") setCursor((c) => addDays(c, -1));
    else if (view === "week") setCursor((c) => addDays(c, -7));
    else setCursor((c) => addMonths(c, -1));
  }, [view]);

  const navNext = useCallback(() => {
    if (view === "day") setCursor((c) => addDays(c, 1));
    else if (view === "week") setCursor((c) => addDays(c, 7));
    else setCursor((c) => addMonths(c, 1));
  }, [view]);

  const save = useCallback(async () => {
    if (googleConnected) {
      await syncGoogle();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await savePmSchedule(sessionId, { version: 1, entries });
      setDirty(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [sessionId, entries, googleConnected, syncGoogle]);

  const openNew = useCallback(
    (defaults: Partial<ScheduleEntry> = {}) => {
      setEditing(emptyRow({ date: formatYMD(cursor), start: "09:00", end: "10:00", ...defaults }));
    },
    [cursor],
  );

  const openSlot = useCallback(
    (day: Date, hour: number) => {
      const sh = String(hour).padStart(2, "0");
      const eh = String(Math.min(hour + 1, 23)).padStart(2, "0");
      setEditing(
        emptyRow({
          date: formatYMD(day),
          start: `${sh}:00`,
          end: `${eh}:00`,
        }),
      );
    },
    [],
  );

  const applyEdit = useCallback((e: ScheduleEntry) => {
    const existingLocal = entries.some((entry) => entry.id === e.id);
    const shouldWriteGoogle = e.source === "google" || (!existingLocal && googleWritable && e.date && e.start && e.end);
    if (shouldWriteGoogle) {
      setSaving(true);
      setGoogleError(null);
      void (async () => {
        try {
          const payload = {
            title: e.title || "Scheduled block",
            date: e.date,
            start: e.start,
            end: e.end,
            notes: e.notes,
            location: e.location || "",
          };
          if (e.providerEventId) {
            await updateGoogleCalendarEvent(sessionId, e.providerEventId, payload);
          } else {
            await createGoogleCalendarEvent(sessionId, payload);
          }
          await loadGoogleCalendar();
          setEditing(null);
          setViewingGoogle(null);
        } catch (err) {
          setGoogleError(err instanceof Error ? err.message : String(err));
        } finally {
          setSaving(false);
        }
      })();
      return;
    }
    setEntries((prev) => {
      const i = prev.findIndex((x) => x.id === e.id);
      if (i >= 0) {
        const next = [...prev];
        next[i] = e;
        return next;
      }
      return [...prev, e];
    });
    setDirty(true);
    setEditing(null);
  }, [entries, googleWritable, loadGoogleCalendar, sessionId]);

  const deleteEntry = useCallback((id: string) => {
    const target = googleEntries.find((entry) => entry.id === id);
    if (target?.providerEventId) {
      setSaving(true);
      setGoogleError(null);
      void (async () => {
        try {
          await deleteGoogleCalendarEvent(sessionId, target.providerEventId || "");
          await loadGoogleCalendar();
          setEditing(null);
          setViewingGoogle(null);
        } catch (err) {
          setGoogleError(err instanceof Error ? err.message : String(err));
        } finally {
          setSaving(false);
        }
      })();
      return;
    }
    setEntries((prev) => prev.filter((x) => x.id !== id));
    setDirty(true);
    setEditing(null);
  }, [googleEntries, loadGoogleCalendar, sessionId]);

  const handleCellDrop = useCallback((day: Date, hour: number) => {
    let nextEntries: ScheduleEntry[] | null = null;

    if (draggingTask) {
      const sh = String(hour).padStart(2, "0");
      const totalMins = draggingTask.duration;
      const endHour = Math.min(hour + Math.floor(totalMins / 60), 23);
      const endMin = totalMins % 60;
      const newEntry: ScheduleEntry = {
        id: crypto.randomUUID(),
        title: draggingTask.title,
        date: formatYMD(day),
        weekday: null,
        start: `${sh}:00`,
        end: `${String(endHour).padStart(2, "0")}:${String(endMin).padStart(2, "0")}`,
        notes: draggingTask.list,
      };
      nextEntries = [...entries, newEntry];
      setEntries(nextEntries);
      setTasks((prev) => prev.filter((t) => t.id !== draggingTask.id));
      setDraggingTask(null);
    } else if (draggingEntry) {
      const startMins = draggingEntry.start
        ? (() => { const [h, m] = draggingEntry.start.split(":").map(Number); return h * 60 + (m || 0); })()
        : 0;
      const endMins = draggingEntry.end
        ? (() => { const [h, m] = draggingEntry.end.split(":").map(Number); return h * 60 + (m || 0); })()
        : startMins + 60;
      const duration = Math.max(endMins - startMins, 30);
      const newStartMins = hour * 60;
      const newEndMins = Math.min(newStartMins + duration, 23 * 60 + 59);
      const newStart = `${String(Math.floor(newStartMins / 60)).padStart(2, "0")}:${String(newStartMins % 60).padStart(2, "0")}`;
      const newEnd = `${String(Math.floor(newEndMins / 60)).padStart(2, "0")}:${String(newEndMins % 60).padStart(2, "0")}`;
      nextEntries = entries.map((e) =>
        e.id === draggingEntry.id ? { ...e, date: formatYMD(day), start: newStart, end: newEnd } : e
      );
      setEntries(nextEntries);
      setDraggingEntry(null);
    }
    setDropTarget(null);

    // Auto-save immediately so changes survive view switches
    if (nextEntries && !googleConnected) {
      void savePmSchedule(sessionId, { version: 1, entries: nextEntries });
    }
  }, [draggingTask, draggingEntry, entries, googleConnected, sessionId]);

  const headerSubtitle = useMemo(() => {
    if (view === "month") return monthYearLabel(cursor);
    if (view === "week") {
      const end = addDays(weekStart, 6);
      return `${monthYearLabel(weekStart)} · ${shortWeekdayLabel(weekStart)} ${dayNum(weekStart)} – ${shortWeekdayLabel(end)} ${dayNum(end)}`;
    }
    return longDateLabel(cursor);
  }, [view, cursor, weekStart]);

  const taskSidebar = (
    <aside className="sch-task-sidebar">
      <div className="sch-task-sidebar-top">
        <div className="sch-task-sidebar-heading">Tasks</div>
        <div className="sch-task-nav-item">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><polyline points="20 6 9 17 4 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
          <span>All tasks</span>
          {tasks.length > 0 && <span className="sch-task-count">{tasks.length}</span>}
        </div>
        <div className="sch-task-lists-label">Lists · time this week</div>
        {Object.entries(LIST_META).map(([name, meta]) => {
          const taskCount = tasks.filter((t) => t.list === name).length;
          return (
            <div key={name} className="sch-task-list-row">
              <div className="sch-task-list-row-top">
                <span className="sch-task-list-dot" style={{ background: meta.color, boxShadow: `0 0 6px ${meta.color}88` }} />
                <span className="sch-task-list-name">{name}</span>
                <span className="sch-task-list-hours">{meta.hours}h</span>
                {taskCount > 0 && <span className="sch-task-count">{taskCount}</span>}
              </div>
              <div className="sch-task-list-bar">
                <div className="sch-task-list-bar-fill" style={{ width: `${Math.min((meta.hours / meta.max) * 100, 100)}%`, background: meta.color }} />
              </div>
            </div>
          );
        })}
        <div className="sch-drag-hint">
          <div className="sch-drag-hint-title">Drag to schedule</div>
          <div className="sch-drag-hint-sub">Drop any task on the calendar grid</div>
        </div>
      </div>
      <div className="sch-task-cards">
        {tasks.map((task) => (
          <div
            key={task.id}
            className="sch-task-card"
            draggable={!isMobile}
            onDragStart={!isMobile ? (e) => {
              setDraggingTask(task);
              e.dataTransfer.effectAllowed = "copy";
            } : undefined}
            onDragEnd={!isMobile ? () => { setDraggingTask(null); setDropTarget(null); } : undefined}
            style={{ borderLeftColor: PRIORITY_COLOR[task.priority] }}
          >
            <button
              className="sch-task-card-delete"
              onClick={(e) => { e.stopPropagation(); setTasks((prev) => prev.filter((t) => t.id !== task.id)); }}
              title="Delete task"
            >✕</button>
            <div className="sch-task-card-title">{task.title}</div>
            <div className="sch-task-card-meta">
              <span className="sch-task-card-dur">{task.duration}m</span>
              <span className="sch-task-card-list">{task.list}</span>
            </div>
          </div>
        ))}
        {tasks.length === 0 && (
          <div className="sch-task-empty">
            <div style={{ fontSize: 22, marginBottom: 6 }}>🎉</div>
            <div>All tasks scheduled!</div>
          </div>
        )}
      </div>
      <div className="sch-task-sidebar-footer">
        <button className="sch-task-new-btn" onClick={() => openNew()}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg>
          New task
        </button>
      </div>
    </aside>
  );

  const calendarSection = (
    <div className="sch-cal-main">
      <header className="ws-header sch-cal-header">
        <div className="sch-cal-header-main">
          <div className="ws-header-left">
            {!fullscreen && <span className="ws-title" id="sch-title">Schedule</span>}
            <span className={fullscreen ? "sch-month-title" : "sch-subtitle"}>{headerSubtitle}</span>
          </div>
          <div className="sch-view-tabs" role="tablist" aria-label="Calendar view">
            {(["day", "week", "month"] as const).map((v) => (
              <button key={v} type="button" role="tab" aria-selected={view === v}
                className={`sch-view-tab ${view === v ? "active" : ""}`} onClick={() => setView(v)}>
                {v === "day" ? "Day" : v === "week" ? "Week" : "Month"}
              </button>
            ))}
          </div>
        </div>
        <div className="sch-cal-toolbar">
          <div className="sch-nav-cluster">
            <button type="button" className="sch-icon-btn" onClick={navPrev} aria-label="Previous">‹</button>
            <button type="button" className="sch-btn sch-btn-secondary sch-today-btn" onClick={goToday}>Today</button>
            <button type="button" className="sch-icon-btn" onClick={navNext} aria-label="Next">›</button>
          </div>
          <div className="sch-header-actions">
            {!fullscreen && (
              <button type="button" className="sch-btn sch-btn-secondary" onClick={() => openNew()}>+ Event</button>
            )}
            {!fullscreen && (
              <button type="button" className="sch-btn sch-btn-primary" onClick={() => void save()} disabled={saving || loading}>
                {googleConnected ? (googleSyncing ? "Syncing..." : "Synced") : saving ? "Saving…" : dirty ? "Save" : "Saved"}
              </button>
            )}
            {fullscreen ? (
              <button type="button" className="sch-fullscreen-add-btn" onClick={() => openNew()}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="white" strokeWidth="2" strokeLinecap="round"/></svg>
              </button>
            ) : (
              <button type="button" className="sch-btn sch-btn-close" onClick={onClose} title="Close">✕</button>
            )}
          </div>
        </div>
        {/* AI insight banner + Google Calendar connect */}
        <div className="sch-insight-banner">
          <div className="sch-insight-left">
            <div className="sch-insight-icon">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none">
                <path d="M12 2L13.5 8.5L20 10L13.5 11.5L12 18L10.5 11.5L4 10L10.5 8.5Z" stroke="white" strokeWidth="1.5" fill="none" />
              </svg>
            </div>
            <span className="sch-insight-text">{AI_INSIGHTS[insightIdx]}</span>
            <div className="sch-insight-dots">
              {AI_INSIGHTS.map((_, i) => (
                <button key={i} type="button" className={`sch-insight-dot${i === insightIdx ? " active" : ""}`}
                  onClick={() => setInsightIdx(i)} aria-label={`Insight ${i + 1}`} />
              ))}
            </div>
          </div>
          <div className="sch-insight-gcal">
            <div className="sch-gcal-identity">
              <div className="sch-gcal-dots">
                {["#4285F4", "#EA4335", "#FBBC05", "#34A853"].map((c, i) => (
                  <span key={i} style={{ background: c }} className="sch-gcal-dot-colored" />
                ))}
              </div>
              <span className="sch-gcal-label">Google Calendar</span>
              <span className="sch-gcal-status">
                {googleConnected ? `· ${googleEntries.length} synced${googleWritable ? "" : " · read-only"}` : "· Not connected"}
              </span>
            </div>
            <div className="sch-gcal-actions">
              {googleConnected ? (
                <>
                  <button type="button" className="sch-gcal-btn" onClick={() => void syncGoogle()} disabled={googleSyncing || googleLoading || googleDisconnecting}>
                    {googleSyncing ? "Syncing…" : "Sync"}
                  </button>
                  <button type="button" className="sch-gcal-btn sch-gcal-btn-muted" onClick={() => void disconnectGoogle()} disabled={googleDisconnecting || googleSyncing}>
                    {googleDisconnecting ? "…" : "Disconnect"}
                  </button>
                </>
              ) : (
                <button type="button" className="sch-gcal-btn" onClick={connectGoogle}>Connect Google</button>
              )}
            </div>
          </div>
        </div>
      </header>

      {error && <div className="sch-error">{error}</div>}
      {googleError && <div className="sch-error">{googleError}</div>}

      {!googleConnected && upcoming.length > 0 && (
        <div className="sch-agenda-strip">
          <span className="sch-agenda-label">Today</span>
          <div className="sch-agenda-items">
            {upcoming.map((ev) => (
              <span key={ev.id} className="sch-agenda-chip" style={{ "--sch-h": (ev.id.charCodeAt(0) * 37) % 360 } as CSSProperties}>
                {ev.start ? <span className="sch-agenda-time">{ev.start}</span> : null}
                {ev.title}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="sch-body sch-cal-body">
        {loading ? (
          <p className="ws-empty">Loading…</p>
        ) : view === "day" ? (
          <DayCalendarView day={cursor} entries={visibleEntries}
            onSlotClick={(h) => openSlot(cursor, h)} onEventClick={openEntry}
            onAddUndated={() => openNew({ date: "", weekday: null, start: "", end: "" })}
            draggingTask={isMobile ? null : draggingTask} draggingEntry={isMobile ? null : draggingEntry} dropTarget={isMobile ? null : dropTarget}
            onCellDragOver={isMobile ? undefined : (d, h) => setDropTarget({ day: d, hour: h })}
            onCellDrop={isMobile ? undefined : handleCellDrop}
            onDragLeave={isMobile ? undefined : () => setDropTarget(null)}
            onEventDragStart={isMobile ? undefined : (e) => { setDraggingEntry(e); setDraggingTask(null); }}
            onEventDragEnd={isMobile ? undefined : () => { setDraggingEntry(null); setDropTarget(null); }} />
        ) : view === "week" ? (
          <WeekCalendarView days={weekDays} entries={visibleEntries}
            onSlotClick={openSlot} onEventClick={openEntry}
            draggingTask={isMobile ? null : draggingTask} draggingEntry={isMobile ? null : draggingEntry} dropTarget={isMobile ? null : dropTarget}
            onCellDragOver={isMobile ? undefined : (d, h) => setDropTarget({ day: d, hour: h })}
            onCellDrop={isMobile ? undefined : handleCellDrop}
            onDragLeave={isMobile ? undefined : () => setDropTarget(null)}
            onEventDragStart={isMobile ? undefined : (e) => { setDraggingEntry(e); setDraggingTask(null); }}
            onEventDragEnd={isMobile ? undefined : () => { setDraggingEntry(null); setDropTarget(null); }} />
        ) : (
          <MonthCalendarView anchorMonth={cursor} entries={visibleEntries}
            onPickDay={(d) => { setCursor(d); setView("day"); }} onEventClick={openEntry} />
        )}
      </div>

      {visibleEntries.length === 0 && !loading && (
        <p className="sch-empty-hint">
          {googleConnected
            ? "No synced Google events in this view. Try Sync Google, Week, or Month."
            : "No events yet. Click + Event, connect Google, or use an hour row."}
        </p>
      )}

      {editing && (
        <EventEditorModal entry={editing} onClose={() => setEditing(null)} onSave={applyEdit} onDelete={deleteEntry} />
      )}
      {viewingGoogle && (
        <GoogleEventModal entry={viewingGoogle} onClose={() => setViewingGoogle(null)} />
      )}
    </div>
  );

  if (fullscreen) {
    return (
      <div className="sch-fullscreen-layout">
        {taskSidebar}
        {calendarSection}
      </div>
    );
  }

  const panelContent = (
    <div
      className={`ws-panel sch-panel-cal${sidebar ? " sch-panel-sidebar" : ""}`}
      onClick={(e) => e.stopPropagation()}
      role="dialog"
      aria-labelledby="sch-title"
    >
      {calendarSection}
    </div>
  );

  if (sidebar) return panelContent;

  return (
    <div className="ws-overlay" onClick={onClose} role="presentation">
      {panelContent}
    </div>
  );
}

type DragPassProps = {
  draggingTask?: DraggableTask | null;
  draggingEntry?: ScheduleEntry | null;
  dropTarget?: { day: Date; hour: number } | null;
  onCellDragOver?: (day: Date, hour: number) => void;
  onCellDrop?: (day: Date, hour: number) => void;
  onDragLeave?: () => void;
  onEventDragStart?: (entry: ScheduleEntry) => void;
  onEventDragEnd?: () => void;
};

function DayCalendarView({
  day, entries, onSlotClick, onEventClick, onAddUndated,
  draggingTask, draggingEntry, dropTarget, onCellDragOver, onCellDrop, onDragLeave, onEventDragStart, onEventDragEnd,
}: {
  day: Date;
  entries: ScheduleEntry[];
  onSlotClick: (hour: number) => void;
  onEventClick: (e: ScheduleEntry) => void;
  onAddUndated: () => void;
} & DragPassProps) {
  const allDay = useMemo(() => allDayEntriesForDay(day, entries), [day, entries]);
  const hours = useMemo(() => {
    const list: number[] = [];
    for (let h = CAL_DAY_START_HOUR; h <= CAL_DAY_END_HOUR; h++) list.push(h);
    return list;
  }, []);

  return (
    <div className="sch-cal-scroll">
      <div className="sch-allday sch-allday-day">
        <span className="sch-allday-label">All day</span>
        <div className="sch-allday-chips">
          {allDay.map((e) => (
            <EventPill key={e.id} entry={e} onClick={() => onEventClick(e)} />
          ))}
          <button type="button" className="sch-allday-add" onClick={onAddUndated}>
            + Add
          </button>
        </div>
      </div>
      <div className="sch-day-grid">
        <div className="sch-time-gutter">
          {hours.map((h) => (
            <div key={h} className="sch-time-label" style={{ height: PX_PER_HOUR }}>
              {h === 12 ? "12 PM" : h < 12 ? `${h} AM` : `${h - 12} PM`}
            </div>
          ))}
        </div>
        <TimeGridColumn day={day} entries={entries} onBackgroundClick={onSlotClick} onEventClick={onEventClick}
          draggingTask={draggingTask} draggingEntry={draggingEntry} dropTarget={dropTarget}
          onCellDragOver={onCellDragOver} onCellDrop={onCellDrop} onDragLeave={onDragLeave}
          onEventDragStart={onEventDragStart} onEventDragEnd={onEventDragEnd} />
      </div>
    </div>
  );
}

function WeekCalendarView({
  days, entries, onSlotClick, onEventClick,
  draggingTask, draggingEntry, dropTarget, onCellDragOver, onCellDrop, onDragLeave, onEventDragStart, onEventDragEnd,
}: {
  days: Date[];
  entries: ScheduleEntry[];
  onSlotClick: (day: Date, hour: number) => void;
  onEventClick: (e: ScheduleEntry) => void;
} & DragPassProps) {
  const hours = useMemo(() => {
    const list: number[] = [];
    for (let h = CAL_DAY_START_HOUR; h <= CAL_DAY_END_HOUR; h++) list.push(h);
    return list;
  }, []);
  const today = new Date();

  return (
    <div className="sch-cal-scroll">
      <div className="sch-week-allday">
        {days.map((d) => (
          <div key={formatYMD(d)} className={`sch-week-head ${isSameCalendarDay(d, today) ? "today" : ""}`}>
            <span className="sch-week-dow">{shortWeekdayLabel(d)}</span>
            <span className="sch-week-dom">{dayNum(d)}</span>
          </div>
        ))}
      </div>
      <div className="sch-week-allday-row">
        {days.map((d) => (
          <div key={formatYMD(d)} className="sch-week-allday-cell">
            {allDayEntriesForDay(d, entries).map((e) => (
              <EventPill key={e.id} entry={e} onClick={() => onEventClick(e)} compact />
            ))}
          </div>
        ))}
      </div>
      <div className="sch-week-grid">
        <div className="sch-time-gutter">
          {hours.map((h) => (
            <div key={h} className="sch-time-label" style={{ height: PX_PER_HOUR }}>
              {h === 12 ? "12" : h < 13 ? `${h}` : `${h - 12}`}
            </div>
          ))}
        </div>
        <div className="sch-week-cols">
          {days.map((d) => (
            <TimeGridColumn
              key={formatYMD(d)}
              day={d}
              entries={entries}
              onBackgroundClick={(hour) => onSlotClick(d, hour)}
              onEventClick={onEventClick}
              draggingTask={draggingTask}
              draggingEntry={draggingEntry}
              dropTarget={dropTarget}
              onCellDragOver={onCellDragOver}
              onCellDrop={onCellDrop}
              onDragLeave={onDragLeave}
              onEventDragStart={onEventDragStart}
              onEventDragEnd={onEventDragEnd}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function entriesForMonthCell(d: Date, entries: ScheduleEntry[]): ScheduleEntry[] {
  const matched = entries.filter((e) => entryMatchesDay(e, d));
  const today = new Date();
  if (isSameCalendarDay(d, today)) {
    const undated = entries.filter((e) => isUndatedEntry(e));
    const ids = new Set(matched.map((e) => e.id));
    return [...matched, ...undated.filter((e) => !ids.has(e.id))];
  }
  return matched;
}

function MonthCalendarView({
  anchorMonth,
  entries,
  onPickDay,
  onEventClick,
}: {
  anchorMonth: Date;
  entries: ScheduleEntry[];
  onPickDay: (d: Date) => void;
  onEventClick: (e: ScheduleEntry) => void;
}) {
  const cells = useMemo(() => buildMonthCells(anchorMonth), [anchorMonth]);
  const today = new Date();
  const monthIndex = anchorMonth.getMonth();

  return (
    <div className="sch-month-wrap">
      <div className="sch-month-dow-row">
        {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => (
          <div key={d} className="sch-month-dow">
            {d}
          </div>
        ))}
      </div>
      <div className="sch-month-grid">
        {cells.map((d) => {
          const inMonth = d.getMonth() === monthIndex;
          const isToday = isSameCalendarDay(d, today);
          const list = entriesForMonthCell(d, entries);
          const show = list.slice(0, 3);

          return (
            <div
              key={formatYMD(d)}
              className={`sch-month-cell ${!inMonth ? "other-month" : ""} ${isToday ? "today" : ""}`}
              role="button"
              tabIndex={0}
              onClick={() => onPickDay(d)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  onPickDay(d);
                }
              }}
            >
              <span className="sch-month-num">{dayNum(d)}</span>
              <div className="sch-month-events">
                {show.map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    className={`sch-month-event-line ${e.source === "google" ? "sch-event-google" : ""}`}
                    style={{ "--sch-h": entryHue(e.id) } as CSSProperties}
                    onClick={(ev) => {
                      ev.stopPropagation();
                      onEventClick(e);
                    }}
                  >
                    {e.source === "google" ? "Google · " : ""}{e.title || "Untitled"}
                  </button>
                ))}
                {list.length > 3 && <span className="sch-month-more">+{list.length - 3} more</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
