import { useMemo, type CSSProperties } from "react";
import type { ScheduleEntry } from "../../types";
import {
  formatYMD,
  isSameCalendarDay,
  entryMatchesDay,
  entryHue,
  longDateLabel,
  dayNum,
  buildMonthCells,
  isUndatedEntry,
} from "../../lib/scheduleCalendar";

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

export function MonthCalendarView({
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
            // The whole cell is a mouse target; the day-number button is the
            // keyboard and screen-reader route to the same action.
            <div
              key={formatYMD(d)}
              className={`sch-month-cell ${!inMonth ? "other-month" : ""} ${isToday ? "today" : ""}`}
              onClick={() => onPickDay(d)}
            >
              <button
                type="button"
                className="sch-month-num"
                aria-label={`${longDateLabel(d)}${list.length ? `, ${list.length} ${list.length === 1 ? "event" : "events"}` : ""}. Open day`}
                aria-current={isToday ? "date" : undefined}
                onClick={(ev) => {
                  ev.stopPropagation();
                  onPickDay(d);
                }}
              >
                {dayNum(d)}
              </button>
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
