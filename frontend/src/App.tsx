/**
 * Application shell.
 *
 * Owns session state, the streaming turn lifecycle, and artifact selection.
 * Kept as one component with hooks rather than a state library: the state is
 * genuinely local to this screen, and a store would add indirection without
 * removing any real complexity at this size.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import ArtifactViewer from "./components/ArtifactViewer";
import Composer from "./components/Composer";
import Message from "./components/Message";
import Sidebar from "./components/Sidebar";
import { ApiRequestError, api, getUserId, sendMessage } from "./lib/api";
import type {
  ApiError,
  Artifact,
  Citation,
  CorpusStatus,
  Message as MessageType,
  ModelsStatus,
  Session,
  StreamEvent,
} from "./lib/types";

const SUGGESTIONS = [
  {
    text: "How do you know if you've found product-market fit?",
    hint: "Grounded answer with citations",
  },
  {
    text: "What separates great product managers from good ones?",
    hint: "Synthesises across multiple operators",
  },
  {
    text: "Write an essay about early-stage growth channels",
    hint: "Ship 30 for 30 style, ~1,250 words",
  },
  {
    text: "Make a one-page checklist for running user interviews",
    hint: "Generates a document artifact",
  },
];

/** The in-flight assistant turn, before it becomes a persisted Message. */
interface PendingTurn {
  content: string;
  citations: Citation[];
  status: string | null;
  skill: string | null;
  provider: string | null;
  model: string | null;
  error: ApiError | null;
  insufficientEvidence: boolean;
}

const emptyTurn = (): PendingTurn => ({
  content: "",
  citations: [],
  status: null,
  skill: null,
  provider: null,
  model: null,
  error: null,
  insufficientEvidence: false,
});

export default function App() {
  const userId = useRef(getUserId()).current;

  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageType[]>([]);
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [models, setModels] = useState<ModelsStatus | null>(null);
  const [corpus, setCorpus] = useState<CorpusStatus | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [bootError, setBootError] = useState<ApiError | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // ---- boot -------------------------------------------------------------- #

  useEffect(() => {
    (async () => {
      try {
        const [modelsStatus, corpusStatus, sessionList] = await Promise.all([
          api.getModels(),
          api.getCorpus(),
          api.listSessions(userId),
        ]);
        setModels(modelsStatus);
        setCorpus(corpusStatus);
        setSessions(sessionList);
      } catch (error) {
        if (error instanceof ApiRequestError) setBootError(error.error);
      }
    })();
  }, [userId]);

  // Poll provider health while idle. A user who starts Ollama after opening the
  // app should see the indicator go green without reloading.
  useEffect(() => {
    const timer = setInterval(async () => {
      if (pending) return;
      try {
        setModels(await api.getModels());
      } catch {
        /* transient; the next tick retries */
      }
    }, 20000);
    return () => clearInterval(timer);
  }, [pending]);

  // Keep the newest content in view as tokens stream in.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending?.content]);

  // ---- session management ------------------------------------------------ #

  const loadSession = useCallback(async (sessionId: string) => {
    setActiveId(sessionId);
    setPending(null);
    setArtifact(null);
    setSidebarOpen(false);
    try {
      const detail = await api.getSession(sessionId);
      setMessages(detail.messages);
      // Reopen the most recent artifact so returning to a chat restores the
      // full working context, not just the transcript of it.
      if (detail.artifacts.length) {
        const full = await api.getArtifact(detail.artifacts[0].id);
        setArtifact(full);
      }
    } catch (error) {
      if (error instanceof ApiRequestError) setBootError(error.error);
    }
  }, []);

  const newChat = useCallback(() => {
    setActiveId(null);
    setMessages([]);
    setPending(null);
    setArtifact(null);
    setSidebarOpen(false);
  }, []);

  const deleteSession = useCallback(
    async (sessionId: string) => {
      try {
        await api.deleteSession(sessionId);
        setSessions((current) => current.filter((s) => s.id !== sessionId));
        if (sessionId === activeId) newChat();
      } catch {
        /* the list refreshes on the next turn regardless */
      }
    },
    [activeId, newChat],
  );

  // ---- sending ----------------------------------------------------------- #

  const handleSend = useCallback(
    async (text: string, skill: string | null) => {
      let sessionId = activeId;

      // Sessions are created lazily on first message, so opening the app and
      // changing your mind doesn't litter the sidebar with empty chats.
      if (!sessionId) {
        try {
          const session = await api.createSession(userId);
          sessionId = session.id;
          setActiveId(session.id);
          setSessions((current) => [session, ...current]);
        } catch (error) {
          if (error instanceof ApiRequestError) {
            setPending({ ...emptyTurn(), error: error.error });
          }
          return;
        }
      }

      // Optimistic user message: the input should never appear to be swallowed.
      setMessages((current) => [
        ...current,
        {
          id: `local-${Date.now()}`,
          seq: current.length + 1,
          role: "user",
          content: text,
          skill: null,
          provider: null,
          model: null,
          latency_ms: null,
          insufficient_evidence: false,
          created_at: new Date().toISOString(),
          citations: [],
        },
      ]);

      const turn = emptyTurn();
      setPending(turn);

      const controller = new AbortController();
      abortRef.current = controller;

      let accumulated = emptyTurn();

      await sendMessage({
        sessionId,
        message: text,
        skill,
        signal: controller.signal,
        onEvent: (event: StreamEvent) => {
          accumulated = reduceEvent(accumulated, event);
          setPending({ ...accumulated });

          if (event.type === "artifact") {
            // Show the artifact immediately with a provisional id; the real one
            // arrives with the `done` event once it has been persisted.
            setArtifact({
              id: "pending",
              session_id: sessionId!,
              message_id: null,
              kind: event.artifact.kind,
              title: event.artifact.title,
              content: event.artifact.content,
              template: event.artifact.template,
              version: 1,
              word_count: null,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
              metadata: event.artifact.metadata,
            });
          }
        },
      });

      abortRef.current = null;

      // Reload from the server so we render exactly what was persisted —
      // including any citation the validator stripped from the streamed text.
      try {
        const detail = await api.getSession(sessionId);
        setMessages(detail.messages);
        setSessions(await api.listSessions(userId));
        if (detail.artifacts.length) {
          const full = await api.getArtifact(detail.artifacts[0].id);
          setArtifact(full);
        }
        setPending(accumulated.error ? { ...accumulated } : null);
      } catch {
        // Persistence failed but the answer is on screen. Keep showing it.
        setPending({ ...accumulated });
      }
    },
    [activeId, userId],
  );

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setPending(null);
  }, []);

  const handleSaveArtifact = useCallback(
    async (content: string) => {
      if (!artifact || artifact.id === "pending") return;
      const updated = await api.updateArtifact(artifact.id, content);
      setArtifact(updated);
    },
    [artifact],
  );

  // ---- render ------------------------------------------------------------ #

  const activeSession = sessions.find((s) => s.id === activeId);
  const streaming = pending !== null && !pending.error;
  const showEmpty = messages.length === 0 && !pending;

  return (
    <div className={`app ${artifact ? "with-artifact" : ""}`}>
      {sidebarOpen && (
        <button
          className="scrim"
          aria-label="Close menu"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <Sidebar
        sessions={sessions}
        activeId={activeId}
        models={models}
        corpus={corpus}
        open={sidebarOpen}
        onSelect={loadSession}
        onNew={newChat}
        onDelete={deleteSession}
      />

      <main className="chat">
        <header className="chat-header">
          <button
            className="icon-btn menu-btn"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open menu"
          >
            ☰
          </button>
          <span className="chat-title">
            {activeSession?.title ?? "New conversation"}
          </span>
          {artifact && (
            <button className="icon-btn" onClick={() => setArtifact(null)} title="Hide artifact">
              ⇥
            </button>
          )}
        </header>

        {models?.degraded && (
          <div className="degraded-banner" role="status">
            <span aria-hidden="true">⚠</span>
            <span>
              <strong>Running on fallback.</strong> The configured provider (
              {models.configured_provider}) is unavailable, so answers are coming
              from {models.effective_provider}. Quality may differ.
            </span>
          </div>
        )}

        {corpus && !corpus.ready && (
          <div className="degraded-banner" role="status">
            <span aria-hidden="true">⚠</span>
            <span>
              <strong>Knowledge base is empty.</strong> Run{" "}
              <code>make ingest</code> to load the transcripts — until then the
              assistant has nothing to ground answers in.
            </span>
          </div>
        )}

        <div className="chat-scroll" ref={scrollRef}>
          <div className="chat-inner">
            {bootError && (
              <div className="error-box" role="alert">
                <div className="error-title">{bootError.message}</div>
                {bootError.remediation && (
                  <div className="error-remediation">{bootError.remediation}</div>
                )}
              </div>
            )}

            {showEmpty && !bootError && (
              <div className="empty">
                <h1 className="empty-title">What are you working through?</h1>
                <p className="empty-sub">
                  Ask anything about product, growth, or building teams. Every
                  answer is grounded in {corpus?.episodes ?? 303} episodes of
                  Lenny's Podcast — with links to the exact moment it was said.
                </p>
                <div className="suggestions">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion.text}
                      className="suggestion"
                      onClick={() => handleSend(suggestion.text, null)}
                    >
                      {suggestion.text}
                      <span className="suggestion-hint">{suggestion.hint}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((message) => (
              <Message
                key={message.id}
                role={message.role}
                content={message.content}
                citations={message.citations}
                insufficientEvidence={message.insufficient_evidence}
                provider={message.provider}
                model={message.model}
                latencyMs={message.latency_ms}
              />
            ))}

            {pending && (
              <div className="message assistant">
                {pending.error ? (
                  <div className="error-box" role="alert">
                    <div className="error-title">{pending.error.message}</div>
                    {pending.error.remediation && (
                      <div className="error-remediation">
                        {pending.error.remediation}
                      </div>
                    )}
                  </div>
                ) : (
                  <>
                    <div className="message-role">
                      <span>Assistant</span>
                      {pending.model && (
                        <span
                          style={{
                            fontWeight: 400,
                            textTransform: "none",
                            letterSpacing: 0,
                          }}
                        >
                          · {pending.model}
                        </span>
                      )}
                    </div>

                    {pending.status && !pending.content && (
                      <div className="status-line">
                        <span className="spinner" aria-hidden="true" />
                        <span>{pending.status}</span>
                      </div>
                    )}

                    {pending.content && (
                      <Message
                        role="assistant"
                        content={pending.content}
                        citations={pending.citations}
                        insufficientEvidence={pending.insufficientEvidence}
                        streaming
                      />
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        <Composer
          disabled={streaming || !!bootError}
          streaming={streaming}
          hasConversation={messages.length > 0}
          onSend={handleSend}
          onStop={handleStop}
        />
      </main>

      {artifact && (
        <ArtifactViewer
          artifact={artifact}
          onClose={() => setArtifact(null)}
          onSave={handleSaveArtifact}
        />
      )}
    </div>
  );
}

/**
 * Fold one stream event into the pending turn.
 *
 * A pure reducer so the streaming lifecycle is testable in isolation and the
 * component body stays free of a long event switch.
 */
function reduceEvent(turn: PendingTurn, event: StreamEvent): PendingTurn {
  switch (event.type) {
    case "provider":
      return { ...turn, provider: event.provider, model: event.model };
    case "skill":
      return { ...turn, skill: event.skill };
    case "status":
      return { ...turn, status: event.message };
    case "evidence":
      return { ...turn, citations: event.evidence };
    case "token":
      return { ...turn, content: turn.content + event.text, status: null };
    case "correction":
      // The validator stripped a fabricated citation; replace the streamed text
      // with the cleaned version so the user never keeps a bogus reference.
      return { ...turn, content: event.text };
    case "error":
      return { ...turn, error: event.error };
    case "done":
      return {
        ...turn,
        citations: event.citations.length ? event.citations : turn.citations,
        insufficientEvidence: event.insufficient_evidence,
        status: null,
      };
    default:
      return turn;
  }
}
