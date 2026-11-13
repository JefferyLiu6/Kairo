import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import {
  autoSyncGoogleCalendar,
  createGoogleCalendarEvent,
  deleteGoogleCalendarEvent,
  disconnectGoogleCalendarAccount,
  fetchGoogleCalendarAccounts,
  fetchGoogleCalendarEvents,
  fetchPmSchedule,
  fetchPmTasks,
  fetchUpcomingEvents,
  savePmSchedule,
  syncGoogleCalendar,
  updateGoogleCalendarEvent,
  type GoogleCalendarAccount,
  type GoogleCalendarEvent,
  type PmTask,
  type UpcomingEvent,
} from "../api";
import type { ScheduleEntry } from "../types";
import { EventEditorModal, GoogleEventModal } from "./schedule/EventModals";
import { DayCalendarView, WeekCalendarView } from "./schedule/DayWeekViews";
import { InsightTips } from "./schedule/InsightTips";
import { MonthCalendarView } from "./schedule/MonthView";
import { TaskSidebar } from "./schedule/TaskSidebar";
import type { DraggableTask } from "./schedule/TimeGrid";
import {
  type CalendarView,
  formatYMD,
  addDays,
  addMonths,
  startOfWeekSunday,
  dayTitleLabel,
  weekRangeLabel,
  monthYearLabel,
  buildMonthCells,
  entryHue,
  formatTimeLabel,
} from "../lib/scheduleCalendar";

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

export function SchedulePanel({ sessionId, onClose, refreshNonce = 0, sidebar = false, fullscreen = false }: Props) {
  const [tasks, setTasks] = useState<PmTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasksError, setTasksError] = useState(false);
  const [tasksRetry, setTasksRetry] = useState(0);
  const [dragDropEnabled, setDragDropEnabled] = useState(() =>
    typeof window === "undefined" ? true : !window.matchMedia("(max-width: 760px)").matches
  );
  const [draggingTask, setDraggingTask] = useState<DraggableTask | null>(null);
  const [draggingEntry, setDraggingEntry] = useState<ScheduleEntry | null>(null);
  const [dropTarget, setDropTarget] = useState<{ day: Date; hour: number } | null>(null);

  // Open tasks for the full-screen task list; reloaded after each Kairo reply.
  useEffect(() => {
    if (!fullscreen) return;
    let cancelled = false;
    setTasksError(false);
    fetchPmTasks()
      .then((list) => { if (!cancelled) setTasks(list); })
      .catch(() => { if (!cancelled) setTasksError(true); })
      .finally(() => { if (!cancelled) setTasksLoading(false); });
    return () => { cancelled = true; };
  }, [fullscreen, refreshNonce, tasksRetry]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const query = window.matchMedia("(max-width: 760px)");
    const syncDragMode = () => {
      const enabled = !query.matches;
      setDragDropEnabled(enabled);
      if (!enabled) {
        setDraggingTask(null);
        setDraggingEntry(null);
        setDropTarget(null);
      }
    };
    syncDragMode();
    query.addEventListener("change", syncDragMode);
    return () => query.removeEventListener("change", syncDragMode);
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
  /** Failure from a save/delete made inside the event editor; shown in the dialog. */
  const [editError, setEditError] = useState<string | null>(null);
  const editingId = editing?.id;
  useEffect(() => setEditError(null), [editingId]);

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
    // Without write access a Google event can only be viewed, not edited.
    if (entry.source === "google" && !googleWritable) {
      setViewingGoogle({ ...entry });
      return;
    }
    setEditing({ ...entry });
  }, [googleWritable]);

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

  /** Write the local schedule to the server. On failure the change stays on
   *  screen, marked unsaved, with a retry in the error banner. */
  const persist = useCallback(async (nextEntries: ScheduleEntry[]) => {
    setSaving(true);
    setError(null);
    try {
      await savePmSchedule(sessionId, { version: 1, entries: nextEntries });
      setDirty(false);
    } catch {
      setDirty(true);
      setError("Couldn't save your calendar change. It's still shown here but isn't saved yet.");
    } finally {
      setSaving(false);
    }
  }, [sessionId]);

  const save = useCallback(async () => {
    if (googleConnected) {
      await syncGoogle();
      return;
    }
    await persist(entries);
  }, [entries, googleConnected, syncGoogle, persist]);

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
      setEditError(null);
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
          setEditError(err instanceof Error ? err.message : String(err));
        } finally {
          setSaving(false);
        }
      })();
      return;
    }
    const next = existingLocal ? entries.map((x) => (x.id === e.id ? e : x)) : [...entries, e];
    setEntries(next);
    setEditing(null);
    // Save straight away: the full-screen calendar has no Save button, so an
    // unsaved edit there would be lost on the next reload.
    if (googleConnected) setDirty(true);
    else void persist(next);
  }, [entries, googleConnected, googleWritable, loadGoogleCalendar, persist, sessionId]);

  const deleteEntry = useCallback((id: string) => {
    const target = googleEntries.find((entry) => entry.id === id);
    if (target?.providerEventId) {
      setSaving(true);
      setEditError(null);
      void (async () => {
        try {
          await deleteGoogleCalendarEvent(sessionId, target.providerEventId || "");
          await loadGoogleCalendar();
          setEditing(null);
          setViewingGoogle(null);
        } catch (err) {
          setEditError(err instanceof Error ? err.message : String(err));
        } finally {
          setSaving(false);
        }
      })();
      return;
    }
    const next = entries.filter((x) => x.id !== id);
    setEntries(next);
    setEditing(null);
    if (googleConnected) setDirty(true);
    else void persist(next);
  }, [entries, googleConnected, googleEntries, loadGoogleCalendar, persist, sessionId]);

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
        notes: "",
      };
      nextEntries = [...entries, newEntry];
      setEntries(nextEntries);
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
    if (nextEntries && !googleConnected) void persist(nextEntries);
  }, [draggingTask, draggingEntry, entries, googleConnected, persist]);

  // What the calendar is showing. It is the title in both the narrow and the full-screen calendar.
  const title = useMemo(() => {
    if (view === "month") return monthYearLabel(cursor);
    if (view === "week") return weekRangeLabel(weekStart);
    return dayTitleLabel(cursor);
  }, [view, cursor, weekStart]);

  const dragDropViewProps = dragDropEnabled
    ? {
        draggingTask,
        draggingEntry,
        dropTarget,
        onCellDragOver: (d: Date, h: number) => setDropTarget({ day: d, hour: h }),
        onCellDrop: handleCellDrop,
        onDragLeave: () => setDropTarget(null),
        onEventDragStart: (e: ScheduleEntry) => { setDraggingEntry(e); setDraggingTask(null); },
        onEventDragEnd: () => { setDraggingEntry(null); setDropTarget(null); },
      }
    : {};

  const scheduledTitles = useMemo(
    () => new Set(visibleEntries.map((e) => e.title.trim().toLowerCase())),
    [visibleEntries],
  );

  const taskSidebar = (
    <TaskSidebar
      tasks={tasks}
      loading={tasksLoading}
      error={tasksError}
      onRetry={() => { setTasksLoading(true); setTasksRetry((n) => n + 1); }}
      scheduledTitles={scheduledTitles}
      onDragStart={(task) => { setDraggingTask(task); setDraggingEntry(null); }}
      onDragEnd={() => { setDraggingTask(null); setDropTarget(null); }}
      onSchedule={(task) => openNew({ title: task.title })}
    />
  );

  const calendarSection = (
    <div className="sch-cal-main">
      <header className="ws-header sch-cal-header">
        <div className="sch-cal-header-main">
          <h2 className="sch-title" aria-live="polite">{title}</h2>
          <div className="sch-view-tabs" role="group" aria-label="Calendar view">
            {(["day", "week", "month"] as const).map((v) => (
              <button key={v} type="button" aria-pressed={view === v}
                className={`sch-view-tab ${view === v ? "active" : ""}`} onClick={() => setView(v)}>
                {v === "day" ? "Day" : v === "week" ? "Week" : "Month"}
              </button>
            ))}
          </div>
        </div>
        <div className="sch-cal-toolbar">
          <div className="sch-nav-cluster">
            <button type="button" className="sch-icon-btn" onClick={navPrev} aria-label={`Previous ${view}`}>
              <span aria-hidden="true">‹</span>
            </button>
            <button type="button" className="sch-btn sch-btn-secondary sch-today-btn" onClick={goToday}>Today</button>
            <button type="button" className="sch-icon-btn" onClick={navNext} aria-label={`Next ${view}`}>
              <span aria-hidden="true">›</span>
            </button>
          </div>
          <div className="sch-header-actions">
            {/* Changes save themselves. This only appears while one is in flight, or when one is waiting to be saved. */}
            {saving ? (
              <span className="sch-save-state" role="status">Saving…</span>
            ) : dirty && !googleConnected ? (
              <button type="button" className="sch-btn sch-btn-secondary" onClick={() => void save()} disabled={loading}>
                Save changes
              </button>
            ) : null}
            <button type="button" className="sch-btn sch-btn-primary sch-add-btn" onClick={() => openNew()}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
              </svg>
              Event
            </button>
            {fullscreen ? (
              <button type="button" className="sch-btn sch-btn-secondary sch-back-chat-btn" onClick={onClose}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M15 18 9 12l6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                Chat
              </button>
            ) : (
              <button type="button" className="sch-icon-btn" onClick={onClose} aria-label="Close calendar" title="Close calendar">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M6 6l12 12M18 6 6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </button>
            )}
          </div>
        </div>
        {/* AI insight banner + Google Calendar connect */}
        <div className="sch-insight-banner">
          <InsightTips />
          <div className="sch-insight-gcal">
            <div className="sch-gcal-identity">
              <div className="sch-gcal-dots" aria-hidden="true">
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

      {error && (
        <div className="sch-error" role="alert">
          <span>{error}</span>
          {dirty && !googleConnected && (
            <button type="button" className="sch-error-retry" onClick={() => void save()} disabled={saving}>
              {saving ? "Saving…" : "Try again"}
            </button>
          )}
        </div>
      )}
      {googleError && <div className="sch-error" role="alert">{googleError}</div>}

      {!googleConnected && upcoming.length > 0 && (
        <div className="sch-agenda-strip">
          <span className="sch-agenda-label">Today</span>
          <div className="sch-agenda-items">
            {/* An occurrence's id is "<entry id>:<date>". It takes the colour of the entry it belongs to. */}
            {upcoming.map((ev) => (
              <span key={ev.id} className="sch-agenda-chip" style={{ "--sch-h": entryHue(ev.id.split(":")[0]) } as CSSProperties}>
                {ev.start ? <span className="sch-agenda-time">{formatTimeLabel(ev.start)}</span> : null}
                {ev.title}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="sch-body sch-cal-body">
        {loading ? (
          <p className="ws-empty" role="status">Loading calendar…</p>
        ) : view === "day" ? (
          <DayCalendarView day={cursor} entries={visibleEntries}
            onSlotClick={(h) => openSlot(cursor, h)} onEventClick={openEntry}
            onAddUndated={() => openNew({ date: "", weekday: null, start: "", end: "" })}
            {...dragDropViewProps} />
        ) : view === "week" ? (
          <WeekCalendarView days={weekDays} entries={visibleEntries}
            onSlotClick={openSlot} onEventClick={openEntry}
            {...dragDropViewProps} />
        ) : (
          <MonthCalendarView anchorMonth={cursor} entries={visibleEntries}
            onPickDay={(d) => { setCursor(d); setView("day"); }} onEventClick={openEntry} />
        )}
      </div>

      {visibleEntries.length === 0 && !loading && (
        <p className="sch-empty-hint">
          {googleConnected
            ? "No synced Google events in this view. Try Sync Google, Week, or Month."
            : "No events yet. Add one with the + button, or click a time slot."}
        </p>
      )}

      {editing && (
        <EventEditorModal
          entry={editing}
          isNew={!entries.some((x) => x.id === editing.id) && !googleEntries.some((x) => x.id === editing.id)}
          busy={saving}
          error={editError}
          onClose={() => setEditing(null)}
          onSave={applyEdit}
          onDelete={deleteEntry}
        />
      )}
      {viewingGoogle && (
        <GoogleEventModal entry={viewingGoogle} onClose={() => setViewingGoogle(null)} />
      )}
    </div>
  );

  if (fullscreen) {
    return (
      <div className="sch-fullscreen-layout">
        {dragDropEnabled && taskSidebar}
        {calendarSection}
      </div>
    );
  }

  const panelContent = (
    <div
      className={`ws-panel sch-panel-cal${sidebar ? " sch-panel-sidebar" : ""}`}
      onClick={(e) => e.stopPropagation()}
      role={sidebar ? "region" : "dialog"}
      aria-modal={sidebar ? undefined : true}
      aria-label="Calendar"
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
