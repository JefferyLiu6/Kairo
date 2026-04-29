import type { ScheduleData } from "./types";

// Production support: set VITE_API_BASE=/api and proxy through Vercel rewrites.
const _API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

// CSRF token — fetched from /auth/csrf on mount, injected on state-changing requests.
let _csrfToken = "";


function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const url = _API_BASE ? `${_API_BASE}${path}` : path;
  const merged = new Headers(init?.headers);
  merged.set("Content-Type", merged.get("Content-Type") ?? "application/json");
  const method = (init?.method ?? "GET").toUpperCase();
  if (_csrfToken && ["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    merged.set("X-CSRF-Token", _csrfToken);
  }
  return fetch(url, { ...init, headers: merged, credentials: "include" });
}

// ── Auth API ──────────────────────────────────────────────────────────────────

export type AuthUser = {
  id: string;
  email: string;
  displayName: string;
  isDemo: boolean;
  creditsRemaining: number;
};

function _parseUser(data: {
  id: string;
  email: string;
  display_name: string;
  is_demo?: boolean;
  credits_remaining?: number;
}): AuthUser {
  return {
    id: data.id,
    email: data.email,
    displayName: data.display_name,
    isDemo: data.is_demo ?? false,
    creditsRemaining: data.credits_remaining ?? 0,
  };
}

export async function authCsrf(): Promise<void> {
  const res = await fetch(
    _API_BASE ? `${_API_BASE}/auth/csrf` : "/auth/csrf",
    { credentials: "include" },
  );
  if (res.ok) {
    const data = (await res.json()) as { csrf_token: string };
    _csrfToken = data.csrf_token;
  }
}

export async function authMe(): Promise<AuthUser> {
  const res = await apiFetch("/auth/me");
  if (!res.ok) throw new Error(`${res.status}`);
  return _parseUser(await res.json());
}

export async function authDemo(): Promise<AuthUser> {
  const res = await apiFetch("/auth/demo", { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText })) as { detail: string };
    throw new Error(body.detail ?? "Demo login failed");
  }
  return _parseUser(await res.json());
}

export async function authLogin(email: string, password: string): Promise<AuthUser> {
  const res = await apiFetch("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText })) as { detail: string };
    throw new Error(body.detail ?? "Login failed");
  }
  return _parseUser(await res.json());
}

export async function authSignup(
  email: string,
  password: string,
  displayName: string,
): Promise<void> {
  const res = await apiFetch("/auth/signup", {
    method: "POST",
    body: JSON.stringify({ email, password, display_name: displayName }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText })) as { detail: string };
    throw new Error(body.detail ?? "Signup failed");
  }
}

export async function authLogout(): Promise<void> {
  await apiFetch("/auth/logout", { method: "POST" });
  _csrfToken = "";
}

export type SessionEntry = {
  sessionId: string;
  messageCount: number;
  createdAt?: string;
  title?: string;
};

export type ServerMessage = {
  role: "user" | "assistant";
  content: string;
};

/**
 * Stream a chat message via SSE.
 *
 * Calls onToken for each token chunk (accumulate to build the full reply).
 * Retries once on network error, sending Last-Event-ID so the backend can
 * replay a completed-but-not-delivered reply without re-running the agent.
 * onRetry is called before the retry so the UI can reset the partial message.
 */
export async function streamChat(
  message: string,
  sessionId: string,
  onToken: (token: string) => void,
  signal?: AbortSignal,
  onProgress?: (status: string) => void,
  /** Generic chat vs Kairo streaming (same SSE + tool progress). */
  streamMode: "chat" | "personal-manager" = "chat",
  /** Called before a retry attempt so the UI can clear any partial content. */
  onRetry?: () => void,
): Promise<string> {
  const MAX_RETRIES = 1;
  let lastEventId = "";

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");

    if (attempt > 0) {
      onRetry?.();
      await new Promise((r) => setTimeout(r, 800));
    }

    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (attempt > 0 && lastEventId) {
      headers["Last-Event-ID"] = lastEventId;
    }

    let caughtError: unknown;
    try {
      const endpoint = streamMode === "personal-manager" ? "/orchestrator/stream" : "/chat/stream";
      const res = await apiFetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify({ message, session_id: sessionId, mode: streamMode }),
        signal,
      });

      if (!res.ok) {
        const body = await res.text().catch(() => res.statusText);
        let msg = `${res.status}: ${body}`;
        if (res.status === 402 || res.status === 429) {
          try { msg = (JSON.parse(body) as { detail: string }).detail; } catch { /* keep raw */ }
        }
        throw new Error(msg);
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let full = "";
      let buf = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });

          const lines = buf.split("\n");
          buf = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith("id: ")) {
              lastEventId = line.slice(4).trim();
              continue;
            }
            if (!line.startsWith("data: ")) continue;
            const data = line.slice(6);
            if (data === "[DONE]") return full;
            try {
              const parsed = JSON.parse(data) as { token?: string; progress?: string };
              if (typeof parsed.progress === "string" && onProgress) {
                onProgress(parsed.progress);
              }
              if (typeof parsed.token === "string") {
                onToken(parsed.token);
                full += parsed.token;
              }
            } catch {
              // skip malformed chunks
            }
          }
        }
        // Stream closed without [DONE] — eligible for retry
        caughtError = new Error("Stream ended without [DONE]");
      } finally {
        try { reader.releaseLock(); } catch { /* already released */ }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") throw err;
      caughtError = err;
    }

    if (attempt >= MAX_RETRIES) throw caughtError;
  }

  return ""; // unreachable
}

export type PetState = { xp: number; energy: number; bond: number; focusStreak: number };

/** Fetch the persisted Kairo pet state for the current user. */
export async function fetchPmPet(): Promise<PetState | null> {
  try {
    const res = await apiFetch("/personal-manager/pet");
    if (!res.ok) return null;
    return res.json() as Promise<PetState>;
  } catch {
    return null;
  }
}

/** Persist the Kairo pet state for the current user. */
export async function savePmPet(state: PetState): Promise<void> {
  await apiFetch("/personal-manager/pet", {
    method: "PUT",
    body: JSON.stringify(state),
  });
}

/** Delete a PM session (thread) by ID. */
export async function deleteSession(sessionId: string): Promise<boolean> {
  const res = await apiFetch(`/personal-manager/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  return res.ok;
}

/** Fetch list of all PM sessions (threads). */
export async function fetchSessions(): Promise<SessionEntry[]> {
  const res = await apiFetch("/personal-manager/sessions");
  if (!res.ok) return [];
  const data = (await res.json()) as { sessions: Array<{ sessionId: string; title: string | null; lastActiveAt: string }> };
  return (data.sessions ?? []).map((s) => ({
    sessionId: s.sessionId,
    // PM threads don't count messages; use 1 so HistoryPanel's messageCount>0 filter passes.
    messageCount: 1,
    createdAt: s.lastActiveAt,
    title: s.title ?? undefined,
  }));
}

/** Fetch the human/assistant message transcript for a PM thread from its LangGraph checkpoint. */
export async function fetchSession(sessionId: string): Promise<ServerMessage[]> {
  try {
    const res = await apiFetch(`/personal-manager/sessions/${sessionId}/messages`);
    if (!res.ok) return [];
    const data = (await res.json()) as { messages: ServerMessage[] };
    return data.messages ?? [];
  } catch {
    return [];
  }
}

/** Load the Kairo schedule for the chat session (server applies `pm-` prefix). */
export async function fetchPmSchedule(sessionId: string): Promise<ScheduleData> {
  const res = await apiFetch(
    `/personal-manager/schedule/${encodeURIComponent(sessionId)}`,
  );
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${err}`);
  }
  const data = (await res.json()) as ScheduleData;
  if (data && Array.isArray(data.entries) && typeof data.version === "number") {
    return data;
  }
  return { version: 1, entries: [] };
}

/** Persist schedule for this Kairo session. */
export async function savePmSchedule(sessionId: string, schedule: ScheduleData): Promise<void> {
  const res = await apiFetch(`/personal-manager/schedule/${encodeURIComponent(sessionId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(schedule),
  });
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${err}`);
  }
}

export type UpcomingEvent = {
  id: string;
  title: string;
  date: string;
  start: string;
  end: string;
  notes: string;
};

/** Fetch events in the next `days` days for this session. */
export async function fetchUpcomingEvents(
  sessionId: string,
  days = 1,
): Promise<UpcomingEvent[]> {
  const res = await apiFetch(
    `/personal-manager/upcoming?sessionId=${encodeURIComponent(sessionId)}&days=${days}`,
  );
  if (!res.ok) return [];
  const data = (await res.json()) as { events: UpcomingEvent[] };
  return data.events ?? [];
}

export type GoogleCalendarAccount = {
  id: string;
  sessionId: string;
  provider: "google";
  accountEmail: string;
  calendarId: string;
  scopes: string[];
  syncTokenPresent: boolean;
  status: string;
  lastSyncAt?: string | null;
  nextSyncAfter?: string | null;
  syncStatus?: string;
  lastSyncError?: string;
  lastSyncErrorAt?: string | null;
  createdAt: string;
  updatedAt: string;
};

export type GoogleCalendarEvent = {
  id: string;
  accountId: string;
  provider: "google";
  providerEventId: string;
  title: string;
  startAt: string;
  endAt: string;
  timezone: string;
  status: string;
  notes: string;
  location: string;
  updatedAt: string;
};

export type GoogleCalendarSyncResult = {
  accountId: string;
  provider: "google";
  synced: number;
  fullSync: boolean;
};

export async function fetchGoogleCalendarAccounts(
  sessionId: string,
): Promise<GoogleCalendarAccount[]> {
  const res = await apiFetch(
    `/personal-manager/google-calendar/accounts?sessionId=${encodeURIComponent(sessionId)}`,
  );
  if (!res.ok) return [];
  const data = (await res.json()) as { accounts: GoogleCalendarAccount[] };
  return data.accounts ?? [];
}

export async function fetchGoogleCalendarEvents(
  sessionId: string,
  start?: string,
  end?: string,
): Promise<GoogleCalendarEvent[]> {
  const params = new URLSearchParams({ sessionId, limit: "500" });
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  const res = await apiFetch(`/personal-manager/google-calendar/events?${params.toString()}`);
  if (!res.ok) return [];
  const data = (await res.json()) as { events: GoogleCalendarEvent[] };
  return data.events ?? [];
}

export async function syncGoogleCalendar(
  sessionId: string,
): Promise<GoogleCalendarSyncResult[]> {
  const res = await apiFetch(
    `/personal-manager/google-calendar/sync?sessionId=${encodeURIComponent(sessionId)}`,
    { method: "POST" },
  );
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${err}`);
  }
  const data = (await res.json()) as { sync: GoogleCalendarSyncResult[] };
  return data.sync ?? [];
}

export async function autoSyncGoogleCalendar(
  sessionId: string,
  staleSeconds = 60,
): Promise<GoogleCalendarSyncResult[]> {
  const params = new URLSearchParams({
    sessionId,
    staleSeconds: String(staleSeconds),
  });
  const res = await apiFetch(`/personal-manager/google-calendar/auto-sync?${params.toString()}`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${err}`);
  }
  const data = (await res.json()) as { sync: GoogleCalendarSyncResult[] };
  return data.sync ?? [];
}

export type GoogleCalendarEventWrite = {
  title: string;
  date: string;
  start: string;
  end: string;
  notes?: string;
  location?: string;
};

export async function createGoogleCalendarEvent(
  sessionId: string,
  event: GoogleCalendarEventWrite,
): Promise<GoogleCalendarEvent> {
  const res = await apiFetch(
    `/personal-manager/google-calendar/events?sessionId=${encodeURIComponent(sessionId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
    },
  );
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${err}`);
  }
  const data = (await res.json()) as { event: GoogleCalendarEvent };
  return data.event;
}

export async function updateGoogleCalendarEvent(
  sessionId: string,
  providerEventId: string,
  event: Partial<GoogleCalendarEventWrite>,
): Promise<GoogleCalendarEvent> {
  const res = await apiFetch(
    `/personal-manager/google-calendar/events/${encodeURIComponent(providerEventId)}?sessionId=${encodeURIComponent(sessionId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
    },
  );
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${err}`);
  }
  const data = (await res.json()) as { event: GoogleCalendarEvent };
  return data.event;
}

export async function deleteGoogleCalendarEvent(
  sessionId: string,
  providerEventId: string,
): Promise<void> {
  const res = await apiFetch(
    `/personal-manager/google-calendar/events/${encodeURIComponent(providerEventId)}?sessionId=${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${err}`);
  }
}

export async function disconnectGoogleCalendarAccount(
  sessionId: string,
  accountId: string,
): Promise<void> {
  const res = await apiFetch(
    `/personal-manager/google-calendar/accounts/${encodeURIComponent(accountId)}?sessionId=${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${err}`);
  }
}

export type TurnIntent = {
  intent: string;
  confidence: number;
  source: string;
  missing: string[];
};

export type FieldCompletionCandidate = {
  id?: string;
  label?: string;
  score?: number;
  candidate_confidence?: number;
  source?: string;
  reason?: string;
  signals?: Record<string, unknown>;
  scope?: Record<string, unknown>;
};

export type TurnDecision = {
  id: string;
  sessionId: string;
  createdAt: string;
  durationMs: number;
  messagePreview: string;
  workingMemory: {
    mode: string | null;
    source: string | null;
    stale: boolean;
    outcome: string;
  };
  intentTrace: {
    tasks: TurnIntent[];
    planConfidence: number;
    extractionSource: string;
  };
  routing: {
    mode: string;
    reason: string;
  };
  blocker: {
    type: string | null;
    missing: string[];
    fcCandidates: FieldCompletionCandidate[];
  };
  memoryIO: {
    read: string[];
    written: string[];
  };
  replyPreview: string;
  wmAfter: string;
};

export type AuditEvent = {
  id: string;
  sessionId: string;
  eventType: string;
  intent: string | null;
  actionType: string | null;
  payloadSummary: string | null;
  resultSummary: string | null;
  approvalId: string | null;
  createdAt: string;
};

/** Fetch per-turn decision trace (intent, working memory, routing, reply) for a PM session. */
export async function fetchPmDecisions(
  sessionId: string,
  limit = 20,
): Promise<TurnDecision[]> {
  const params = new URLSearchParams({ sessionId, limit: String(limit) });
  const res = await apiFetch(`/personal-manager/decisions?${params.toString()}`);
  if (!res.ok) return [];
  const data = (await res.json()) as { decisions: TurnDecision[] };
  return data.decisions ?? [];
}

/** Seed a fresh demo session with default calendar/todos/habits data. */
export async function seedDemoSession(sessionId: string): Promise<void> {
  try {
    await apiFetch("/demo/seed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId }),
    });
  } catch {}
}

/** Fetch PM mutation/audit events for a session. */
export async function fetchPmAuditEvents(
  sessionId: string,
  limit = 50,
): Promise<AuditEvent[]> {
  const params = new URLSearchParams({ sessionId, limit: String(limit) });
  const res = await apiFetch(`/personal-manager/audit?${params.toString()}`);
  if (!res.ok) return [];
  const data = (await res.json()) as { events: AuditEvent[] };
  return data.events ?? [];
}
