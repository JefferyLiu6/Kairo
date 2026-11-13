import { useMemo } from "react";
import type { ScheduleEntry } from "../../types";
import {
  CAL_DAY_START_HOUR,
  CAL_DAY_END_HOUR,
  PX_PER_HOUR,
  formatYMD,
  isSameCalendarDay,
  allDayEntriesForDay,
  shortWeekdayLabel,
  dayNum,
} from "../../lib/scheduleCalendar";
import { EventPill, TimeGridColumn, type DragPassProps } from "./TimeGrid";

export function DayCalendarView({
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

export function WeekCalendarView({
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
          <div
            key={formatYMD(d)}
            className={`sch-week-head ${isSameCalendarDay(d, today) ? "today" : ""}`}
            aria-current={isSameCalendarDay(d, today) ? "date" : undefined}
          >
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
