import type { PmTask } from "../api";
import { formatTimeLabel } from "./scheduleCalendar";

/** Where a task sits relative to today. The list is grouped and coloured by it. */
export type TaskBucket = "overdue" | "today" | "upcoming" | "someday";

export type DatedTask = {
  task: PmTask;
  bucket: TaskBucket;
  /** "Today · 5pm", "Tomorrow", "Fri, Nov 20", "Overdue · Wed, Nov 11". Empty when there is no due date. */
  dueLabel: string;
  /** Sort key: local time in ms, or Infinity for a task with no due date. */
  dueAt: number;
};

export type TaskGroup = { bucket: TaskBucket; title: string; tasks: DatedTask[] };

const GROUP_TITLES: Record<TaskBucket, string> = {
  overdue: "Overdue",
  today: "Today",
  upcoming: "Upcoming",
  someday: "No due date",
};

const DAY_MS = 24 * 60 * 60 * 1000;

/** A due value is "YYYY-MM-DD", optionally followed by "THH:MM". Read as local time. */
function parseDue(due: string): { day: Date; time: string } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(due.trim());
  if (!m) return null;
  const day = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(day.getTime()) ? null : { day, time: m[4] ?? "" };
}

function dayLabel(day: Date, today: Date): string {
  return day.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    ...(day.getFullYear() === today.getFullYear() ? {} : { year: "numeric" }),
  });
}

export function describeTask(task: PmTask, now: Date = new Date()): DatedTask {
  if (!task.due) return { task, bucket: "someday", dueLabel: "", dueAt: Infinity };
  const parsed = parseDue(task.due);
  // A value we cannot read is shown as it is stored instead of being hidden.
  if (!parsed) return { task, bucket: "upcoming", dueLabel: `Due ${task.due}`, dueAt: Infinity };

  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const daysAway = Math.round((parsed.day.getTime() - today.getTime()) / DAY_MS);
  const time = parsed.time ? ` · ${formatTimeLabel(parsed.time)}` : "";
  const [hours, minutes] = parsed.time ? parsed.time.split(":").map(Number) : [0, 0];
  const dueAt = parsed.day.getTime() + (hours * 60 + minutes) * 60 * 1000;

  if (daysAway < 0) return { task, bucket: "overdue", dueLabel: `Overdue · ${dayLabel(parsed.day, today)}`, dueAt };
  if (daysAway === 0) return { task, bucket: "today", dueLabel: `Today${time}`, dueAt };
  const day = daysAway === 1 ? "Tomorrow" : dayLabel(parsed.day, today);
  return { task, bucket: "upcoming", dueLabel: `${day}${time}`, dueAt };
}

/** Tasks in the order they need attention: overdue, today, upcoming by date, then undated. Empty groups are left out. */
export function groupTasks(tasks: PmTask[], now: Date = new Date()): TaskGroup[] {
  const dated = tasks.map((task) => describeTask(task, now));
  return (Object.keys(GROUP_TITLES) as TaskBucket[])
    .map((bucket) => ({
      bucket,
      title: GROUP_TITLES[bucket],
      // Array.prototype.sort is stable, so undated tasks keep the order they were added in.
      tasks: dated.filter((t) => t.bucket === bucket).sort((a, b) => (a.dueAt === b.dueAt ? 0 : a.dueAt - b.dueAt)),
    }))
    .filter((group) => group.tasks.length > 0);
}
