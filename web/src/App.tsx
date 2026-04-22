import { useState, useCallback, useRef, useEffect } from "react";
import { TopNav } from "./components/TopNav";
import { ChatWindow } from "./components/ChatWindow";
import { InputBar } from "./components/InputBar";
import { HistoryPanel } from "./components/HistoryPanel";
import { SchedulePanel } from "./components/SchedulePanel";
import { DecisionTracePanel } from "./components/DecisionTracePanel";
import { KairoPanel } from "./components/KairoPanel";
import { streamChat, fetchSession, seedDemoSession } from "./api";
import { useSpeech } from "./hooks/useSpeech";
import type { Message, Mode } from "./types";

function makeId() {
  return crypto.randomUUID();
}

const _PM_SESSION_KEY = "pm-session-id";
function getOrCreateSessionId(): string {
  const id = "demo-" + crypto.randomUUID();
  try { localStorage.setItem(_PM_SESSION_KEY, id); } catch {}
  return id;
}

export default function App() {
  const [mode, setMode] = useState<Mode>("personal-manager");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [calendarFull, setCalendarFull] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const [scheduleRefreshNonce, setScheduleRefreshNonce] = useState(0);
  const [pmPetRewardNonce, setPmPetRewardNonce] = useState(0);
  const [agentActivity, setAgentActivity] = useState<string | null>(null);
  const [progressSteps, setProgressSteps] = useState<string[]>([]);
  const [callCount, setCallCount] = useState(0);
  const CALL_LIMIT = 6;
  const sessionIdRef = useRef<string>(getOrCreateSessionId());
  const abortRef = useRef<AbortController | null>(null);
  const speech = useSpeech();
  const speakRef = useRef(speech.speak);
  speakRef.current = speech.speak;
  const voiceEnabledRef = useRef(speech.voiceEnabled);
  voiceEnabledRef.current = speech.voiceEnabled;

  const prevMessagesRef = useRef<Message[]>([]);
  useEffect(() => {
    const prev = prevMessagesRef.current;
    prevMessagesRef.current = messages;
    if (!voiceEnabledRef.current) return;
    for (const msg of messages) {
      if (msg.role === "assistant" && !msg.streaming && msg.content) {
        const wasPending = prev.find((m) => m.id === msg.id);
        if (!wasPending || wasPending.streaming) {
          speakRef.current(msg.content);
          break;
        }
      }
    }
  }, [messages]);

  // Seed the current demo session on mount, then refresh schedule
  useEffect(() => {
    seedDemoSession(sessionIdRef.current).then(() =>
      setScheduleRefreshNonce((n) => n + 1)
    );
  }, []);

  const handleMode = useCallback((m: Mode) => {
    setMode(m);
    if (m !== "personal-manager") { setScheduleOpen(false); setCalendarFull(false); }
  }, []);

  const handleClear = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setInput("");
    setBusy(false);
    setAgentActivity(null);
    setCalendarFull(false);
  }, []);

  const handleNewPmSession = useCallback(() => {
    abortRef.current?.abort();
    const id = "demo-" + crypto.randomUUID();
    try { localStorage.setItem(_PM_SESSION_KEY, id); } catch {}
    sessionIdRef.current = id;
    setMessages([]);
    setInput("");
    setBusy(false);
    setAgentActivity(null);
    setProgressSteps([]);
    setCallCount(0);
    seedDemoSession(id).then(() => setScheduleRefreshNonce((n) => n + 1));
  }, []);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const handleLoadSession = useCallback(async (sessionId: string, sessionMode: Mode = "personal-manager") => {
    const serverMessages = await fetchSession(sessionId);
    sessionIdRef.current = sessionId;
    setMode(sessionMode);
    setMessages(
      serverMessages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({
          id: makeId(),
          role: m.role as "user" | "assistant",
          content: m.content,
          mode: sessionMode,
        })),
    );
  }, []);

  const handleSubmit = useCallback(async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || busy) return;
    if (callCount >= CALL_LIMIT) return;

    const sessionId = sessionIdRef.current;

    const userMsg: Message = { id: makeId(), role: "user", content: text, mode };
    const assistantId = makeId();
    const assistantMsg: Message = { id: assistantId, role: "assistant", content: "", mode, streaming: true };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    if (!overrideText) setInput("");
    setBusy(true);
    setAgentActivity("starting…");
    setProgressSteps(["starting…"]);
    setCallCount((n) => n + 1);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const resetAssistantMsg = () =>
        setMessages((prev) =>
          prev.map((m) => m.id === assistantId ? { ...m, content: "" } : m),
        );

      if (mode === "personal-manager") {
        await streamChat(
          text,
          sessionId,
          (token) => {
            setMessages((prev) =>
              prev.map((m) => m.id === assistantId ? { ...m, content: m.content + token } : m),
            );
          },
          ctrl.signal,
          (status) => {
            setAgentActivity(status);
            setProgressSteps((prev) => [...prev, status]);
          },
          "personal-manager",
          resetAssistantMsg,
        );
        setScheduleRefreshNonce((n) => n + 1);
        setPmPetRewardNonce((n) => n + 1);
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: m.content.trim()
                    ? `${m.content.trim()}\n\n_Stopped._`
                    : "_Stopped._",
                  streaming: false,
                }
              : m,
          ),
        );
        return;
      }
      const errMsg = err instanceof Error ? err.message : "Something went wrong.";
      const isRateLimit = errMsg.includes("429") || errMsg.toLowerCase().includes("limit reached") || errMsg.toLowerCase().includes("demo limit");
      const displayMsg = isRateLimit
        ? "You've used all your demo credits. Refresh the page to start a new session."
        : `_${errMsg}_`;
      setMessages((prev) =>
        prev.map((m) => m.id === assistantId ? { ...m, content: displayMsg } : m),
      );
    } finally {
      setAgentActivity(null);
      setProgressSteps([]);
      setMessages((prev) =>
        prev.map((m) => m.id === assistantId ? { ...m, streaming: false } : m),
      );
      setBusy(false);
      abortRef.current = null;
    }
  }, [input, busy, mode, callCount]);

  const handleAction = useCallback((text: string) => {
    handleSubmit(text);
  }, [handleSubmit]);

  const isPmMode = mode === "personal-manager";

  return (
    <div className={`app${isPmMode ? " app-pm" : ""}`}>
      {/* Ambient gradient blobs */}
      <div className="blob blob-1" />
      <div className="blob blob-2" />
      <div className="blob blob-3" />

      <TopNav
        mode={mode}
        onMode={handleMode}
        onHistory={() => setHistoryOpen(true)}
        onSchedule={
          mode === "personal-manager"
            ? () => setScheduleOpen((v) => !v)
            : undefined
        }
        onTrace={
          mode === "personal-manager" ? () => setTraceOpen((v) => !v) : undefined
        }
        agentBusy={busy}
        onStopAgent={handleStop}
        onClear={handleClear}
        callCount={callCount}
        callLimit={CALL_LIMIT}
      />

      <div className="main">
        {/* Full-screen calendar mode */}
        {isPmMode && calendarFull ? (
          <div className="cal-full">
            {isPmMode && (
              <KairoPanel
                busy={busy}
                agentActivity={agentActivity}
                rewardNonce={pmPetRewardNonce}
                scheduleOpen={scheduleOpen}
                calendarFull={calendarFull}
                traceOpen={traceOpen}
                onToggleSchedule={() => setScheduleOpen((v) => !v)}
                onCalendarFull={(v) => { setCalendarFull(v); if (v) setScheduleOpen(false); }}
                onToggleTrace={() => setTraceOpen((v) => !v)}
              />
            )}
            <SchedulePanel
              sessionId={sessionIdRef.current}
              refreshNonce={scheduleRefreshNonce}
              onClose={() => setCalendarFull(false)}
              fullscreen
            />
          </div>
        ) : (
          <>
            {/* Chat column */}
            <div className="chat-col">
              {isPmMode && (
                <KairoPanel
                  busy={busy}
                  agentActivity={agentActivity}
                  rewardNonce={pmPetRewardNonce}
                  scheduleOpen={scheduleOpen}
                  calendarFull={calendarFull}
                  traceOpen={traceOpen}
                  onToggleSchedule={() => setScheduleOpen((v) => !v)}
                  onCalendarFull={(v) => { setCalendarFull(v); if (v) setScheduleOpen(false); }}
                  onToggleTrace={() => setTraceOpen((v) => !v)}
                />
              )}
              <ChatWindow
                messages={messages}
                mode={mode}
                agentActivity={agentActivity}
                progressSteps={progressSteps}
                onStopAgent={busy ? handleStop : undefined}
                onAction={busy ? undefined : handleAction}
                onSpeak={speech.speakDirect}
              />
              <InputBar
                mode={mode}
                value={input}
                onChange={setInput}
                onSubmit={handleSubmit}
                disabled={busy || callCount >= CALL_LIMIT}
                onStop={busy ? handleStop : undefined}
                speechSupported={speech.supported}
                isListening={speech.isListening}
                isSpeaking={speech.isSpeaking}
                voiceEnabled={speech.voiceEnabled}
                onMicClick={() => {
                  if (speech.isListening) {
                    speech.stopListening();
                  } else {
                    speech.startListening((transcript) => {
                      setInput(transcript);
                    });
                  }
                }}
                onVoiceToggle={speech.toggleVoice}
              />
            </div>

            {/* Calendar sidebar */}
            {isPmMode && scheduleOpen && (
              <div className="cal-sidebar">
                <SchedulePanel
                  sessionId={sessionIdRef.current}
                  refreshNonce={scheduleRefreshNonce}
                  onClose={() => setScheduleOpen(false)}
                  sidebar
                />
              </div>
            )}
          </>
        )}
      </div>

      {/* Modal calendar for non-PM modes */}
      {!isPmMode && scheduleOpen && (
        <SchedulePanel
          sessionId={sessionIdRef.current}
          refreshNonce={scheduleRefreshNonce}
          onClose={() => setScheduleOpen(false)}
        />
      )}

      {historyOpen && (
        <HistoryPanel
          mode={mode}
          currentSessionId={sessionIdRef.current}
          onLoad={handleLoadSession}
          onNew={mode === "personal-manager" ? handleNewPmSession : handleClear}
          onClose={() => setHistoryOpen(false)}
          refreshNonce={scheduleRefreshNonce}
        />
      )}


      {traceOpen && (
        <DecisionTracePanel
          sessionId={sessionIdRef.current}
          refreshNonce={scheduleRefreshNonce}
          onClose={() => setTraceOpen(false)}
        />
      )}

    </div>
  );
}
