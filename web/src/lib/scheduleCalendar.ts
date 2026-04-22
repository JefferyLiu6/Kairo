import type { ScheduleEntry } from "../types";

export type CalendarView = "day" | "week" | "month";

export const CAL_DAY_START_HOUR = 6;
export const CAL_DAY_END_HOUR = 22; // last labeled hour (grid extends to endHour + 1)
export const PX_PER_HOUR = 52;

export function formatYMD(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function parseYMD(s: string): Date | null {
  const m = s.trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  const y = parseInt(m[1], 10);
  const mo = parseInt(m[2], 10) - 1;
  const d = parseInt(m[3], 10);
  const dt = new Date(y, mo, d);
  if (dt.getFullYear() !== y || dt.getMonth() !== mo || dt.getDate() !== d) return null;
  return dt;
}

export function startOfWeekSunday(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - x.getDay());
  return x;
}

export function addDays(d: Date, n: number): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() + n);
  return x;
}

export function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}

export function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

export function isSameCalendarDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

const BY_DAY_TO_JS_DAY: Record<string, number> = {
  SU: 0,
  MO: 1,
  TU: 2,
  WE: 3,
  TH: 4,
  FR: 5,
  SA: 6,
};

function daysBetween(a: Date, b: Date): number {
  const utcA = Date.UTC(a.getFullYear(), a.getMonth(), a.getDate());
  const utcB = Date.UTC(b.getFullYear(), b.getMonth(), b.getDate());
  return Math.floor((utcB - utcA) / 86_400_000);
}

function weeksBetween(a: Date, b: Date): number {
  return Math.floor(daysBetween(startOfWeekSunday(a), startOfWeekSunday(b)) / 7);
}

export function isRecurringEntry(entry: ScheduleEntry): boolean {
  return Boolean(entry.recurrence || (entry.weekday !== null && !entry.date));
}

/** One-off on `date`, recurring via `recurrence`, or legacy weekly on `weekday`. */
export function entryMatchesDay(entry: ScheduleEntry, day: Date): boolean {
  const ymd = formatYMD(day);
  const recurrence = entry.recurrence;
  if (recurrence) {
    const anchor = entry.date ? parseYMD(entry.date) : null;
    if (anchor && day < anchor) return false;

    const until = recurrence.until ? parseYMD(recurrence.until) : null;
    if (until && day > until) return false;
    if (entry.exceptions?.includes(ymd)) return false;
    if (entry.overrides?.some((override) => override.original_date === ymd && override.cancelled)) return false;

    const interval = Math.max(1, Number(recurrence.interval || 1));
    const freq = String(recurrence.freq || "weekly").toLowerCase();
    const byDay = new Set((recurrence.by_day || []).map((d) => BY_DAY_TO_JS_DAY[d]).filter((d) => d !== undefined));
    const allDays = byDay.size === 7;

    if (freq === "daily" || allDays) {
      return !anchor || daysBetween(anchor, day) % interval === 0;
    }

    if (freq === "monthly") {
      if (!anchor || day.getDate() !== anchor.getDate()) return false;
      const monthDelta = (day.getFullYear() - anchor.getFullYear()) * 12 + day.getMonth() - anchor.getMonth();
      return monthDelta >= 0 && monthDelta % interval === 0;
    }

    if (byDay.size > 0 && !byDay.has(day.getDay())) return false;
    if (byDay.size === 0) {
      if (!anchor || day.getDay() !== anchor.getDay()) return false;
    }
    if (!anchor) return true;
    return weeksBetween(anchor, day) % interval === 0;
  }

  if (entry.date) {
    return entry.date === ymd;
  }
  if (entry.weekday !== null) {
    return entry.weekday === day.getDay();
  }
  return false;
}

export function isUndatedEntry(entry: ScheduleEntry): boolean {
  return !entry.date && entry.weekday === null && !entry.recurrence;
}

export function parseTimeToMinutes(hhmm: string): number | null {
  const m = hhmm.trim().match(/^(\d{1,2}):(\d{2})$/);
  if (!m || m[1] === undefined || m[2] === undefined) return null;
  const h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  if (h > 23 || min > 59) return null;
  return h * 60 + min;
}

export function hasTimedPortion(entry: ScheduleEntry): boolean {
  return Boolean(entry.start?.trim() || entry.end?.trim());
}

export type TimedLayout = {
  entry: ScheduleEntry;
  startMin: number;
  endMin: number;
  lane: number;
  laneCount: number;
};

function dayBoundsMinutes(): { start: number; end: number } {
  return {
    start: CAL_DAY_START_HOUR * 60,
    end: (CAL_DAY_END_HOUR + 1) * 60,
  };
}

/** Overlap packing for timed events on one day. */
export function layoutTimedEventsForDay(day: Date, entries: ScheduleEntry[]): TimedLayout[] {
  const { start: dayStart, end: dayEnd } = dayBoundsMinutes();
  const raw: { entry: ScheduleEntry; startMin: number; endMin: number }[] = [];

  for (const e of entries) {
    if (!entryMatchesDay(e, day) || !hasTimedPortion(e)) continue;
    const s = parseTimeToMinutes(e.start) ?? parseTimeToMinutes(e.end);
    if (s === null) continue;
    let endM = parseTimeToMinutes(e.end) ?? s + 60;
    if (endM <= s) endM = s + 60;
    endM = Math.min(endM, dayEnd);
    const startClamped = Math.max(s, dayStart);
    if (startClamped >= dayEnd) continue;
    raw.push({ entry: e, startMin: startClamped, endMin: Math.max(endM, startClamped + 15) });
  }

  raw.sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin);

  const lanesEnd: number[] = [];
  const out: TimedLayout[] = [];

  for (const item of raw) {
    let lane = 0;
    while (lanesEnd[lane] !== undefined && lanesEnd[lane]! > item.startMin) {
      lane++;
    }
    lanesEnd[lane] = item.endMin;
    out.push({ ...item, lane, laneCount: 1 });
  }

  const maxLane = out.reduce((m, x) => Math.max(m, x.lane), -1);
  const laneCount = maxLane + 1;
  if (laneCount <= 0) return [];
  return out.map((x) => ({ ...x, laneCount }));
}

export function allDayEntriesForDay(day: Date, entries: ScheduleEntry[]): ScheduleEntry[] {
  const undated = entries.filter(isUndatedEntry);
  const forDay = entries.filter((e) => entryMatchesDay(e, day) && !hasTimedPortion(e));
  const seen = new Set<string>();
  const merged: ScheduleEntry[] = [];
  for (const e of [...undated, ...forDay]) {
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    merged.push(e);
  }
  return merged;
}

/** Month grid: 6 rows × 7 cols, Date objects (may be outside anchor month). */
export function buildMonthCells(anchorMonth: Date): Date[] {
  const first = startOfMonth(anchorMonth);
  const start = startOfWeekSunday(first);
  const cells: Date[] = [];
  for (let i = 0; i < 42; i++) {
    cells.push(addDays(start, i));
  }
  return cells;
}

export function entryHue(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) {
    h = (h * 31 + id.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % 360;
}

export function formatTimeRange(entry: ScheduleEntry): string {
  if (!entry.start && !entry.end) return "";
  if (entry.start && entry.end) return `${formatTimeLabel(entry.start)}–${formatTimeLabel(entry.end)}`;
  return formatTimeLabel(entry.start || entry.end);
}

function formatTimeLabel(hhmm: string): string {
  const m = parseTimeToMinutes(hhmm);
  if (m === null) return hhmm;
  const h24 = Math.floor(m / 60);
  const min = m % 60;
  const am = h24 < 12;
  let h12 = h24 % 12;
  if (h12 === 0) h12 = 12;
  const mm = min === 0 ? "" : `:${String(min).padStart(2, "0")}`;
  return `${h12}${mm}${am ? "am" : "pm"}`;
}

export function longDateLabel(d: Date): string {
  return d.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

export function monthYearLabel(d: Date): string {
  return d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

export function shortWeekdayLabel(d: Date): string {
  return d.toLocaleDateString(undefined, { weekday: "short" });
}

export function dayNum(d: Date): number {
  return d.getDate();
}
