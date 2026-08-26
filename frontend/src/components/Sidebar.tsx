/**
 * Sidebar — session list and system status.
 *
 * Sessions are independent context scopes, so switching is a real navigation
 * action, not a filter. The active one is marked with aria-current so screen
 * readers convey the same state the highlight conveys visually.
 */

import type { CorpusStatus, ModelsStatus, Session } from "../lib/types";

interface Props {
  sessions: Session[];
  activeId: string | null;
  models: ModelsStatus | null;
  corpus: CorpusStatus | null;
  open: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

export default function Sidebar({
  sessions,
  activeId,
  models,
  corpus,
  open,
  onSelect,
  onNew,
  onDelete,
}: Props) {
  const activeProvider = models?.providers.find(
    (p) => p.name === models.effective_provider,
  );

  const providerState = !models
    ? "down"
    : models.degraded
      ? "degraded"
      : activeProvider?.healthy
        ? "ok"
        : "down";

  return (
    <nav className={`sidebar ${open ? "open" : ""}`} aria-label="Conversations">
      <div className="sidebar-header">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">L</div>
          <div className="brand-name">
            Lenny Growth
            <br />
            Assistant
          </div>
        </div>

        <button className="btn-new-chat" onClick={onNew}>
          <span aria-hidden="true">＋</span> New chat
        </button>
      </div>

      <div className="session-list">
        {sessions.length > 0 && <div className="session-list-label">Recent</div>}

        {sessions.length === 0 && (
          <p style={{ padding: "10px", fontSize: 13, color: "var(--text-subtle)" }}>
            No conversations yet.
          </p>
        )}

        {sessions.map((session) => (
          <div
            key={session.id}
            className="session-item"
            aria-current={session.id === activeId}
            role="button"
            tabIndex={0}
            onClick={() => onSelect(session.id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect(session.id);
              }
            }}
          >
            <span className="session-item-title">
              {session.title ?? "Untitled chat"}
            </span>
            <button
              className="session-delete"
              aria-label={`Delete ${session.title ?? "chat"}`}
              onClick={(event) => {
                // Without this the click also selects the session we just deleted.
                event.stopPropagation();
                onDelete(session.id);
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>

      <div className="sidebar-footer">
        <div className="status-row">
          <span className={`status-dot ${providerState}`} aria-hidden="true" />
          <span className="status-model" title={activeProvider?.reason}>
            {activeProvider ? `${activeProvider.model}` : "connecting…"}
          </span>
        </div>

        <div className="status-row">
          <span
            className={`status-dot ${corpus?.ready ? "ok" : "down"}`}
            aria-hidden="true"
          />
          <span className="status-model">
            {corpus?.ready
              ? `${corpus.episodes} episodes · ${corpus.chunks.toLocaleString()} passages`
              : "corpus not ingested"}
          </span>
        </div>

        {models && (
          <div style={{ marginTop: 6, fontSize: 10.5, color: "var(--text-subtle)" }}>
            {models.effective_provider === "ollama" ? "Running locally" : "Cloud"}
            {models.degraded && " · fallback active"}
          </div>
        )}
      </div>
    </nav>
  );
}
