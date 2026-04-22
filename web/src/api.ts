import type { Mode, ScheduleData } from "./types";

// Production support: set VITE_API_BASE to the backend origin (e.g. https://api.example.com)
// and VITE_GATEWAY_TOKEN to the bearer token if auth is enabled.
const _API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
const _API_TOKEN = import.meta.env.VITE_GATEWAY_TOKEN ?? "";

export function apiWebSocketUrl(path: string, params: Record<string, string> = {}): string {
  const base = _API_BASE || window.location.origin;
  const url = new URL(path, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.set(key, value);
  }
  if (_API_TOKEN) url.searchParams.set("token", _API_TOKEN);
  return url.toString();
}

function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const url = _API_BASE ? `${_API_BASE}${path}` : path;
  const merged = new Headers(init?.headers);
  merged.set("Content-Type", merged.get("Content-Type") ?? "application/json");
  if (_API_TOKEN) merged.set("Authorization", `Bearer ${_API_TOKEN}`);
  return fetch(url, { ...init, headers: merged });
}

export type SessionEntry = {
  sessionId: string;
  messageCount: number;
  createdAt?: string;  // ISO timestamp; present on sessions fetched from the updated backend
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
        if (res.status === 429) {
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

/** Send a non-streaming request to the backend chat API. */
export async function sendChat(
  message: string,
  sessionId: string,
  mode: Mode,
  signal?: AbortSignal,
): Promise<string> {
  const res = await apiFetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, sessionId, mode }),
    signal,
  });

  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${err}`);
  }

  const data = (await res.json()) as { reply: string };
  return data.reply;
}

/** Workspace file tree node. */
export type WorkspaceFile = {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number;
  modified?: number;
  children?: WorkspaceFile[];
};

/** Fetch the workspace file tree. */
export async function fetchWorkspace(): Promise<{ root: string | null; files: WorkspaceFile[] }> {
  const res = await apiFetch("/workspace");
  if (!res.ok) return { root: null, files: [] };
  return res.json() as Promise<{ root: string | null; files: WorkspaceFile[] }>;
}

/** Fetch the content of a workspace file. */
export async function fetchWorkspaceFile(path: string): Promise<string | null> {
  const res = await apiFetch(`/workspace/file?path=${encodeURIComponent(path)}`);
  if (!res.ok) return null;
  const data = (await res.json()) as { content: string };
  return data.content;
}

export type RunResult = {
  stdout: string;
  stderr: string;
  exitCode: number | null;
  durationMs: number;
  image: string;
  timedOut: boolean;
};

/** Save (create or overwrite) a workspace file. */
export async function saveWorkspaceFile(path: string, content: string): Promise<boolean> {
  const res = await apiFetch("/workspace/file", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content }),
  });
  return res.ok;
}

/** Create a workspace directory (and any missing parents). */
export async function createWorkspaceDir(path: string): Promise<boolean> {
  const res = await apiFetch("/workspace/mkdir", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return res.ok;
}

/** Delete a workspace file or directory (recursive). */
export async function deleteWorkspaceEntry(path: string): Promise<boolean> {
  const res = await apiFetch(`/workspace/entry?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
  return res.ok;
}

/** Run a workspace file in a Docker container. */
export async function runWorkspaceFile(
  path: string,
  language?: string,
  signal?: AbortSignal,
): Promise<RunResult> {
  const res = await apiFetch("/workspace/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, language }),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText })) as { error: string };
    throw new Error(err.error);
  }
  return res.json() as Promise<RunResult>;
}

/** Delete a master (chat) session by ID. */
export async function deleteSession(sessionId: string): Promise<boolean> {
  const res = await apiFetch(`/master/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  return res.ok;
}

/** Fetch list of all sessions. */
export async function fetchSessions(): Promise<SessionEntry[]> {
  const res = await apiFetch("/sessions");
  if (!res.ok) return [];
  const data = (await res.json()) as { sessions: SessionEntry[] };
  return data.sessions ?? [];
}

/** Fetch full message history for a master (chat) session. */
export async function fetchSession(sessionId: string): Promise<ServerMessage[]> {
  const res = await apiFetch(`/sessions/${encodeURIComponent(sessionId)}`);
  if (!res.ok) return [];
  const data = (await res.json()) as { messages?: ServerMessage[] };
  return data.messages ?? [];
}

export type CodingSessionEntry = {
  sessionId: string;
  messageCount: number;
  task: string;
  createdAt: string;
};

/** Fetch list of all coding agent sessions. */
export async function fetchCodingSessions(): Promise<CodingSessionEntry[]> {
  const res = await apiFetch("/sessions/coding");
  if (!res.ok) return [];
  const data = (await res.json()) as { sessions: CodingSessionEntry[] };
  return data.sessions ?? [];
}

/** Fetch user/assistant messages from a coding agent session. */
export async function fetchCodingSession(sessionId: string): Promise<ServerMessage[]> {
  const res = await apiFetch(`/sessions/coding/${encodeURIComponent(sessionId)}`);
  if (!res.ok) return [];
  const data = (await res.json()) as { messages: ServerMessage[] };
  return data.messages ?? [];
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
