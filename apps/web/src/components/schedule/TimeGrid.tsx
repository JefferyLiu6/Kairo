import { useMemo, type CSSProperties } from "react";
import type { ScheduleEntry } from "../../types";
import {
  type TimedLayout,
  CAL_DAY_START_HOUR,
  CAL_DAY_END_HOUR,
  PX_PER_HOUR,
  isSameCalendarDay,
  layoutTimedEventsForDay,
  entryHue,
  formatTimeRange,
  shortWeekdayLabel,
  dayNum,
  isRecurringEntry,
} from "../../lib/scheduleCalendar";

/** A task being dragged from the task list onto the calendar grid. */
export type DraggableTask = {
  id: string;
  title: string;
  /** Minutes to block out when it is dropped. */
  duration: number;
};

export type DragPassProps = {
  draggingTask?: DraggableTask | null;
  draggingEntry?: ScheduleEntry | null;
  dropTarget?: { day: Date; hour: number } | null;
  onCellDragOver?: (day: Date, hour: number) => void;
  onCellDrop?: (day: Date, hour: number) => void;
  onDragLeave?: () => void;
  onEventDragStart?: (entry: ScheduleEntry) => void;
  onEventDragEnd?: () => void;
};

export function hourLabel(h: number): string {
  if (h === 0) return "12 AM";
  if (h === 12) return "12 PM";
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
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

export function EventPill({
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
      {recurring && <span className="sch-recur" aria-hidden="true">↻</span>}
      {recurring && <span className="sr-only">Repeats. </span>}
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
      draggable={Boolean(onDragStart) && !isGoogle}
      className={`sch-timed-block ${isGoogle ? "sch-event-google" : ""}`}
      style={{ top, height: h, left: `${left}%`, width: `${w}%`, "--sch-h": hue } as CSSProperties}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      onDragStart={onDragStart ? (e) => { e.stopPropagation(); e.dataTransfer.effectAllowed = "move"; onDragStart(entry); } : undefined}
      onDragEnd={onDragEnd}
      title={entry.notes || entry.title}
    >
      {recurring && <span className="sch-timed-recur" aria-hidden="true">↻</span>}
      {recurring && <span className="sr-only">Repeats. </span>}
      <span className="sch-timed-title">{entry.title || "Untitled"}</span>
      <span className="sch-timed-sub">{isGoogle ? "Google · " : ""}{formatTimeRange(entry)}</span>
    </button>
  );
}

export function TimeGridColumn({
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
              aria-label={`Add event ${shortWeekdayLabel(day)} ${dayNum(day)} at ${hourLabel(h)}`}
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
