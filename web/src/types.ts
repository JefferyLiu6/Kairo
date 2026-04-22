export type Mode = "personal-manager";

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  mode: Mode;
  streaming?: boolean;
};

/** Kairo calendar row (mirrors server `schedule.json`). */
export type RecurrenceRule = {
  freq?: "daily" | "weekly" | "monthly" | string;
  interval?: number;
  by_day?: string[];
  until?: string | null;
};

export type ScheduleOverride = {
  original_date: string;
  cancelled?: boolean;
  title?: string | null;
  start?: string | null;
  end?: string | null;
  notes?: string | null;
};

export type ScheduleEntry = {
  id: string;
  title: string;
  date: string;
  weekday: number | null;
  start: string;
  end: string;
  notes: string;
  series_id?: string | null;
  recurrence?: RecurrenceRule | null;
  exceptions?: string[];
  overrides?: ScheduleOverride[];
  source?: "local" | "google";
  readOnly?: boolean;
  providerEventId?: string;
  location?: string;
  timezone?: string;
};

export type ScheduleData = {
  version: 1;
  entries: ScheduleEntry[];
};

export type ModeConfig = {
  id: Mode;
  label: string;
  shortLabel: string;
  icon: string;
  description: string;
  placeholder: string;
};

export const MODES: ModeConfig[] = [
  {
    id: "personal-manager",
    label: "Kairo",
    shortLabel: "Kairo",
    icon: "▣",
    description: "Kairo for plans, habits, and priorities",
    placeholder: "Ask Kairo to plan, update, or review...",
  },
];
