import { useEffect, useState } from "react";

const TIPS = [
  "📅 Check your week ahead — I can help move conflicts.",
  "🎯 Block deep work now before meetings fill the day.",
  "⚡ Ask me to reschedule anything that doesn't fit.",
  "🔋 Connect Google to see your real calendar here.",
];

const ROTATE_MS = 5000;

/** Rotating one-line tips shown above the calendar. */
export function InsightTips() {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);

  // Rotation stops while hovered or focused, and never starts for people who
  // ask for reduced motion (the dots still switch tips by hand).
  useEffect(() => {
    if (paused) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const timer = setInterval(() => setIndex((i) => (i + 1) % TIPS.length), ROTATE_MS);
    return () => clearInterval(timer);
  }, [paused]);

  return (
    <div
      className="sch-insight-left"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <div className="sch-insight-icon">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M12 2L13.5 8.5L20 10L13.5 11.5L12 18L10.5 11.5L4 10L10.5 8.5Z" stroke="white" strokeWidth="1.5" fill="none" />
        </svg>
      </div>
      <span className="sch-insight-text">{TIPS[index]}</span>
      <div className="sch-insight-dots">
        {TIPS.map((_, i) => (
          <button
            key={i}
            type="button"
            className={`sch-insight-dot${i === index ? " active" : ""}`}
            onClick={() => setIndex(i)}
            aria-label={`Tip ${i + 1} of ${TIPS.length}`}
            aria-current={i === index ? "true" : undefined}
          />
        ))}
      </div>
    </div>
  );
}
