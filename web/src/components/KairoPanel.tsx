import { useEffect, useMemo, useRef, useState } from "react";

type PetMood = "idle" | "happy" | "focused" | "working" | "celebrating" | "sleepy";

type PetState = {
  xp: number;
  energy: number;
  bond: number;
  focusStreak: number;
};


type Props = {
  busy: boolean;
  agentActivity?: string | null;
  rewardNonce: number;
  scheduleOpen: boolean;
  calendarFull?: boolean;
  traceOpen?: boolean;
  onToggleSchedule: () => void;
  onCalendarFull?: (v: boolean) => void;
  onToggleTrace?: () => void;
};

const STORAGE_KEY = "pm-pixel-pet-state-v1";

const INITIAL_STATE: PetState = {
  xp: 0,
  energy: 72,
  bond: 18,
  focusStreak: 0,
};


function clamp(value: number, min = 0, max = 100) {
  return Math.min(max, Math.max(min, value));
}

function normalizeState(raw: Partial<PetState>): PetState {
  return {
    xp: Math.max(0, Math.floor(raw.xp ?? INITIAL_STATE.xp)),
    energy: clamp(Math.floor(raw.energy ?? INITIAL_STATE.energy)),
    bond: clamp(Math.floor(raw.bond ?? INITIAL_STATE.bond)),
    focusStreak: Math.max(0, Math.floor(raw.focusStreak ?? INITIAL_STATE.focusStreak)),
  };
}

function loadPetState(): PetState {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return INITIAL_STATE;
    return normalizeState(JSON.parse(stored) as Partial<PetState>);
  } catch {
    return INITIAL_STATE;
  }
}

function levelFromXp(totalXp: number) {
  let level = 1;
  let remaining = totalXp;
  let needed = 100;

  while (remaining >= needed) {
    remaining -= needed;
    level += 1;
    needed = 100 + (level - 1) * 50;
  }

  return {
    level,
    current: remaining,
    needed,
    percent: Math.round((remaining / needed) * 100),
  };
}

export function KairoPanel({
  busy,
  agentActivity,
  rewardNonce,
  scheduleOpen,
  calendarFull,
  traceOpen,
  onToggleSchedule,
  onCalendarFull,
  onToggleTrace,
}: Props) {
  const [pet, setPet] = useState<PetState>(() => loadPetState());
  const [mood, setMood] = useState<PetMood>("idle");
  const lastRewardNonce = useRef(0);

  const level = useMemo(() => levelFromXp(pet.xp), [pet.xp]);
  const activeMood: PetMood = busy ? "working" : pet.energy < 18 ? "sleepy" : mood;

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(pet));
    } catch {
      // Browser storage can be unavailable in private contexts.
    }
  }, [pet]);

  useEffect(() => {
    if (rewardNonce <= 0 || rewardNonce === lastRewardNonce.current) return;
    lastRewardNonce.current = rewardNonce;
    setPet((current) =>
      normalizeState({
        ...current,
        xp: current.xp + 32,
        energy: current.energy + 4,
        bond: current.bond + 3,
      }),
    );
    setMood("celebrating");
  }, [rewardNonce]);

  useEffect(() => {
    if (mood === "idle" || busy) return;
    const timeout = window.setTimeout(() => setMood("idle"), 2600);
    return () => window.clearTimeout(timeout);
  }, [busy, mood]);

  function updatePet(delta: Partial<PetState>, nextMood: PetMood) {
    setPet((current) =>
      normalizeState({
        ...current,
        xp: current.xp + (delta.xp ?? 0),
        energy: current.energy + (delta.energy ?? 0),
        bond: current.bond + (delta.bond ?? 0),
        focusStreak: current.focusStreak + (delta.focusStreak ?? 0),
      }),
    );
    setMood(nextMood);
  }

const speech = useMemo(() => {
    if (busy) return agentActivity || "Kairo is sorting the queue.";
    if (activeMood === "celebrating") return "Done. XP banked.";
    if (activeMood === "sleepy") return "Low energy. Pick a small win or let me rest.";
    if (activeMood === "focused") return "Focus streak armed. Send me into the next task.";
    if (activeMood === "happy") return "Bond up. I am ready for the next useful nudge.";
    return "Send me plans, calendar fixes, and tiny wins.";
  }, [activeMood, agentActivity, busy]);

  return (
    <section className={`pm-pet-panel pm-pet-panel-${activeMood}`} aria-label="Kairo agent card">
      <div className="pm-pet-compact">
        {/* Robot avatar */}
        <button
          type="button"
          className="pm-pet-avatar-button"
          onClick={() => updatePet({ xp: 6, energy: 3, bond: 5 }, "happy")}
          disabled={busy}
          aria-label="Interact with Kairo"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
            <rect x="4" y="6" width="16" height="12" rx="3" stroke="var(--teal)" strokeWidth="1.5" />
            <circle cx="9" cy="12" r="1.5" fill="var(--teal)" />
            <circle cx="15" cy="12" r="1.5" fill="var(--teal)" />
            <path d="M8 19v2M16 19v2" stroke="var(--teal)" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>

        <div className="pm-pet-compact-copy">
          <div className="pm-pet-compact-head">
            <span className="pm-pet-compact-title">Kairo</span>
            <span className="pm-pet-compact-level">LVL {level.level}</span>
            <span className={`pm-pet-compact-status${busy ? " working" : ""}`}>
              <span className={`pm-pet-status-dot${busy ? " working" : ""}`} />
              {busy ? "working" : activeMood}
            </span>
          </div>
          <div className="pm-pet-compact-xp">
            <div className="pm-pet-xp-track" aria-label={`XP progress ${level.percent}%`}>
              <span className="pm-pet-xp-fill" style={{ width: `${level.percent}%` }} />
            </div>
            <strong>{level.current}/{level.needed} XP</strong>
          </div>
          <p className="pm-pet-speech">{speech}</p>
        </div>

        <div className="pm-pet-compact-actions">
          {/* Chat tab */}
          <button
            type="button"
            className={`pm-pet-tab${!calendarFull && !scheduleOpen ? " pm-pet-tab-active" : ""}`}
            onClick={() => { onCalendarFull?.(false); if (scheduleOpen) onToggleSchedule(); if (traceOpen && onToggleTrace) onToggleTrace(); }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
            </svg>
            Chat
          </button>
          {/* Calendar tab */}
          <button
            type="button"
            className={`pm-pet-tab${calendarFull ? " pm-pet-tab-active" : ""}`}
            onClick={() => onCalendarFull?.(!calendarFull)}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
              <rect x="3" y="4" width="18" height="18" rx="3" stroke="currentColor" strokeWidth="1.5" />
              <path d="M16 2v4M8 2v4M3 10h18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            Calendar
          </button>
        </div>
      </div>
    </section>
  );
}
