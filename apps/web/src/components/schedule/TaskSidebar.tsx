import type { PmTask } from "../../api";
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

function dueLabel(due: string): { text: string; overdue: boolean } {
  const date = new Date(`${due}T00:00`);
  if (Number.isNaN(date.getTime())) return { text: `Due ${due}`, overdue: false };
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const day = date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return date < today ? { text: `Overdue · ${day}`, overdue: true } : { text: `Due ${day}`, overdue: false };
}

export function TaskSidebar({
  tasks, loading, error, onRetry, scheduledTitles, onDragStart, onDragEnd, onSchedule,
}: Props) {
  return (
    <aside className="sch-task-sidebar" aria-labelledby="sch-task-heading">
      <div className="sch-task-sidebar-top">
        <h2 className="sch-task-sidebar-heading" id="sch-task-heading">
          Tasks
          {!loading && !error && tasks.length > 0 && (
            <span className="sch-task-count">
              {tasks.length}
              <span className="sr-only"> open</span>
            </span>
          )}
        </h2>
        <div className="sch-drag-hint">
          <div className="sch-drag-hint-title">Drag to schedule</div>
          <div className="sch-drag-hint-sub">Drop a task on the calendar, or use its Schedule button.</div>
        </div>
      </div>

      {loading ? (
        <div className="sch-task-cards" role="status" aria-busy="true" aria-label="Loading tasks">
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
        <ul className="sch-task-cards" role="list">
          {tasks.map((task) => {
            const due = task.due ? dueLabel(task.due) : null;
            const onCalendar = scheduledTitles.has(task.title.trim().toLowerCase());
            return (
              <li
                key={task.id}
                className="sch-task-card"
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.effectAllowed = "copy";
                  onDragStart({ id: task.id, title: task.title, duration: DEFAULT_TASK_MINUTES });
                }}
                onDragEnd={onDragEnd}
              >
                <div className="sch-task-card-title">{task.title}</div>
                <div className="sch-task-card-meta">
                  {due && (
                    <span className={`sch-task-card-dur${due.overdue ? " overdue" : ""}`}>{due.text}</span>
                  )}
                  {onCalendar && <span className="sch-task-card-list">On calendar</span>}
                  <button
                    type="button"
                    className="sch-task-card-schedule"
                    onClick={() => onSchedule(task)}
                    aria-label={`Schedule task: ${task.title}`}
                  >
                    Schedule
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <p className="sch-task-sidebar-note">
        Tasks are added, completed and removed by asking Kairo in chat.
      </p>
    </aside>
  );
}
