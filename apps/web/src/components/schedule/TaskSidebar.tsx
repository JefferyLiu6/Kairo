import { useMemo } from "react";
import type { PmTask } from "../../api";
import { groupTasks, type DatedTask } from "../../lib/tasks";
import type { DraggableTask } from "./TimeGrid";

/** Tasks carry no length of their own, so a scheduled task blocks out an hour. */
const DEFAULT_TASK_MINUTES = 60;

type Props = {
  tasks: PmTask[];
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  /** Lower-cased titles of events already on the calendar. */
  scheduledTitles: Set<string>;
  onDragStart: (task: DraggableTask) => void;
  onDragEnd: () => void;
  /** Keyboard and touch route to the same result as dragging. */
  onSchedule: (task: PmTask) => void;
};

function GripIcon() {
  return (
    <svg className="sch-task-grip" width="8" height="14" viewBox="0 0 8 14" aria-hidden="true">
      {[2, 7, 12].map((y) => (
        <g key={y} fill="currentColor">
          <circle cx="2" cy={y} r="1.1" />
          <circle cx="6" cy={y} r="1.1" />
        </g>
      ))}
    </svg>
  );
}

function TaskCard({
  item, onCalendar, onDragStart, onDragEnd, onSchedule,
}: {
  item: DatedTask;
  onCalendar: boolean;
} & Pick<Props, "onDragStart" | "onDragEnd" | "onSchedule">) {
  const { task, bucket, dueLabel } = item;
  return (
    <li
      className={`sch-task-card sch-task-${bucket}`}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "copy";
        onDragStart({ id: task.id, title: task.title, duration: DEFAULT_TASK_MINUTES });
      }}
      onDragEnd={onDragEnd}
    >
      <GripIcon />
      <div className="sch-task-card-body">
        <div className="sch-task-card-title">{task.title}</div>
        <div className="sch-task-card-meta">
          {dueLabel && <span className="sch-task-due">{dueLabel}</span>}
          {onCalendar && <span className="sch-task-oncal">On calendar</span>}
          <button
            type="button"
            className="sch-task-card-schedule"
            onClick={() => onSchedule(task)}
            aria-label={`Schedule task: ${task.title}`}
          >
            Schedule
          </button>
        </div>
      </div>
    </li>
  );
}

export function TaskSidebar({
  tasks, loading, error, onRetry, scheduledTitles, onDragStart, onDragEnd, onSchedule,
}: Props) {
  const groups = useMemo(() => groupTasks(tasks), [tasks]);
  const ready = !loading && !error;
  const count = (bucket: string) => groups.find((g) => g.bucket === bucket)?.tasks.length ?? 0;
  const overdue = count("overdue");
  const dueToday = count("today");

  return (
    <aside className="sch-task-sidebar" aria-labelledby="sch-task-heading">
      <div className="sch-task-sidebar-top">
        <h2 className="sch-task-sidebar-heading" id="sch-task-heading">
          Tasks
          {ready && tasks.length > 0 && <span className="sch-task-count">{tasks.length} open</span>}
        </h2>
        {ready && (overdue > 0 || dueToday > 0) && (
          <p className="sch-task-summary">
            {overdue > 0 && <span className="sch-task-summary-overdue">{overdue} overdue</span>}
            {overdue > 0 && dueToday > 0 && <span aria-hidden="true"> · </span>}
            {dueToday > 0 && <span className="sch-task-summary-today">{dueToday} due today</span>}
          </p>
        )}
      </div>

      {loading ? (
        <div className="sch-task-list" role="status" aria-busy="true" aria-label="Loading tasks">
          {[0, 1, 2].map((i) => (
            <div key={i} className="sch-task-skeleton" />
          ))}
        </div>
      ) : error ? (
        <div className="sch-task-empty" role="alert">
          <p>Couldn't load your tasks.</p>
          <button type="button" className="history-retry-btn" onClick={onRetry}>
            Try again
          </button>
        </div>
      ) : tasks.length === 0 ? (
        <div className="sch-task-empty">
          <p className="sch-task-empty-title">No open tasks</p>
          <p>Ask Kairo in chat to add one, like "add a task: book the dentist".</p>
        </div>
      ) : (
        <div className="sch-task-list">
          {groups.map((group) => (
            <section key={group.bucket} className="sch-task-group" aria-labelledby={`sch-task-group-${group.bucket}`}>
              <h3 className={`sch-task-group-title sch-task-${group.bucket}`} id={`sch-task-group-${group.bucket}`}>
                {group.title}
                <span className="sch-task-group-count">{group.tasks.length}</span>
              </h3>
              <ul className="sch-task-cards" role="list">
                {group.tasks.map((item) => (
                  <TaskCard
                    key={item.task.id}
                    item={item}
                    onCalendar={scheduledTitles.has(item.task.title.trim().toLowerCase())}
                    onDragStart={onDragStart}
                    onDragEnd={onDragEnd}
                    onSchedule={onSchedule}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      <p className="sch-task-sidebar-note">
        Drag a task onto the calendar to block time for it. To add, finish or remove a task, ask Kairo in chat.
      </p>
    </aside>
  );
}
