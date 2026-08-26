-- =============================================================================
-- The Lenny Growth Assistant — initial schema
--
-- Two logical halves that are deliberately decoupled:
--   1. KNOWLEDGE  (episodes, chunks, ingest_runs) — rebuildable from source at
--      any time. Dropping it loses nothing permanent.
--   2. CONVERSATION (users, sessions, messages, citations, artifacts) — the
--      only genuinely stateful data. Survives any corpus rebuild.
--
-- Citations are the join between the two halves, which is what lets an answer
-- from six months ago still resolve to the passage it was built on.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- =============================================================================
-- KNOWLEDGE
-- =============================================================================

-- One row per podcast episode. Mirrors the YAML frontmatter of each
-- transcript.md, plus provenance columns for source tracing.
CREATE TABLE IF NOT EXISTS episodes (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Directory name in the source repo, e.g. "sean-ellis". Stable natural key.
    slug              TEXT        NOT NULL UNIQUE,

    guest             TEXT        NOT NULL,
    title             TEXT        NOT NULL,
    youtube_url       TEXT,
    video_id          TEXT,
    publish_date      DATE,
    description       TEXT,
    duration_seconds  INTEGER,
    view_count        BIGINT,
    channel           TEXT,

    -- Curated topic tags from the source repo's frontmatter. Used as a cheap
    -- pre-filter and to power the "adjacent topics" empty state.
    keywords          TEXT[]      NOT NULL DEFAULT '{}',

    -- Which of the three source layouts this transcript used:
    -- header_timestamp | inline_timestamp | speaker_only
    -- Recorded so a future corpus refresh that changes format is diagnosable.
    transcript_format TEXT        NOT NULL DEFAULT 'header_timestamp',

    -- False when the source carries no timestamps (speaker_only layout), in
    -- which case citations link to the episode without a &t= deep link rather
    -- than inventing a plausible-looking one.
    has_timestamps    BOOLEAN     NOT NULL DEFAULT TRUE,

    -- ---- Provenance: how we trace any answer back to an exact source state --
    -- Commit SHA of the transcripts repo this episode was ingested from.
    source_commit     TEXT        NOT NULL,
    -- SHA-256 of the raw transcript file. Lets re-ingest skip unchanged
    -- episodes cheaply, which is what makes refresh idempotent.
    content_hash      TEXT        NOT NULL,
    source_path       TEXT        NOT NULL,

    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_episodes_guest        ON episodes (guest);
CREATE INDEX IF NOT EXISTS idx_episodes_publish_date ON episodes (publish_date DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_keywords     ON episodes USING GIN (keywords);
-- Trigram index so "sean elis" still finds Sean Ellis.
CREATE INDEX IF NOT EXISTS idx_episodes_guest_trgm   ON episodes USING GIN (guest gin_trgm_ops);


-- One row per retrievable passage. Chunks follow speaker-turn boundaries, so
-- every chunk inherits a real speaker and a real timestamp — that is what makes
-- second-accurate citation possible at all.
CREATE TABLE IF NOT EXISTS chunks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    episode_id      UUID        NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,

    -- Position within the episode. (episode_id, ord) is the stable address.
    ord             INTEGER     NOT NULL,

    speaker         TEXT,
    -- NULLABLE on purpose: one source layout has no timestamps at all. NULL
    -- means "genuinely unknown", never "zero".
    start_seconds   INTEGER,
    end_seconds     INTEGER,

    text            TEXT        NOT NULL,
    token_estimate  INTEGER     NOT NULL DEFAULT 0,

    -- Dimension must match EMBEDDING_DIMENSIONS (nomic-embed-text = 768).
    -- Changing the embedding model requires a re-ingest; see README.
    embedding       VECTOR(768),

    -- Denormalised for provenance without a join, and so a chunk written by an
    -- older ingest is still identifiable after a partial refresh.
    source_commit   TEXT        NOT NULL,

    -- Generated full-text vector powering the keyword half of hybrid search.
    -- Generated (not trigger-maintained) so it can never drift from `text`.
    tsv             TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_chunk_position UNIQUE (episode_id, ord)
);

CREATE INDEX IF NOT EXISTS idx_chunks_episode ON chunks (episode_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv     ON chunks USING GIN (tsv);

-- IVFFlat over cosine distance. `lists` is tuned for ~50-80k chunks (303
-- episodes); the rule of thumb is rows/1000. Must be built AFTER data load to
-- be effective, so the ingest pipeline reindexes at the end — see
-- ingest/pipeline.py::_finalize_index.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);


-- Audit trail for ingestion. Makes "which corpus version produced this answer?"
-- and "did last night's refresh actually work?" answerable.
CREATE TABLE IF NOT EXISTS ingest_runs (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    source_commit     TEXT        NOT NULL,
    source_ref        TEXT        NOT NULL,
    embedding_model   TEXT        NOT NULL,
    episodes_total    INTEGER     NOT NULL DEFAULT 0,
    episodes_ingested INTEGER     NOT NULL DEFAULT 0,
    episodes_skipped  INTEGER     NOT NULL DEFAULT 0,
    chunks_written    INTEGER     NOT NULL DEFAULT 0,
    status            TEXT        NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'succeeded', 'failed')),
    error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_started ON ingest_runs (started_at DESC);


-- =============================================================================
-- CONVERSATION
-- =============================================================================

-- Anonymous durable identity (PRD assumption A3). The client mints a UUID and
-- keeps it in localStorage. This is the exact seam where real auth plugs in:
-- add an auth provider that supplies `id`, and nothing downstream changes.
CREATE TABLE IF NOT EXISTS users (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- User agent, locale, etc. Free-form so adding metadata needs no migration.
    metadata     JSONB       NOT NULL DEFAULT '{}'::jsonb
);


-- One chat. Sessions are fully independent context scopes.
CREATE TABLE IF NOT EXISTS sessions (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Auto-derived from the first user message; nullable until then.
    title      TEXT,

    -- Provider/model captured at session start so the UI can show what a past
    -- conversation was actually produced with, even after the config changes.
    provider   TEXT,
    model      TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata   JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions (updated_at DESC);


CREATE TABLE IF NOT EXISTS messages (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id  UUID        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,

    -- Monotonic within a session. Ordering by timestamp alone is unsafe when a
    -- user turn and assistant turn land in the same millisecond.
    seq         INTEGER     NOT NULL,

    role        TEXT        NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content     TEXT        NOT NULL,

    -- Which skill produced this turn: grounded_qa | ship30_essay | artifact | null
    skill       TEXT,

    -- Recorded per-message, not just per-session: a fallback mid-conversation
    -- means different turns genuinely came from different models.
    provider    TEXT,
    model       TEXT,

    latency_ms  INTEGER,
    token_usage JSONB,

    -- True when the assistant declined for lack of supporting evidence. Stored
    -- as a column rather than inferred from text so the abstention-correctness
    -- metric is queryable in SQL.
    insufficient_evidence BOOLEAN NOT NULL DEFAULT FALSE,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_message_seq UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, seq);


-- The join between an answer and the evidence it was built on. This table is
-- what makes Cited Answer Accuracy auditable after the fact.
CREATE TABLE IF NOT EXISTS citations (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id     UUID        NOT NULL REFERENCES messages(id) ON DELETE CASCADE,

    -- SET NULL rather than CASCADE: if the corpus is rebuilt and this chunk no
    -- longer exists, we must keep the historical citation and its snapshot
    -- rather than silently deleting evidence of what was claimed.
    chunk_id       UUID        REFERENCES chunks(id) ON DELETE SET NULL,

    -- The label the model used inline, e.g. "E3".
    label          TEXT        NOT NULL,
    rank           INTEGER     NOT NULL DEFAULT 0,
    score          REAL,

    -- Immutable snapshot of what was actually shown to the model. Survives
    -- corpus rebuilds and is the ground truth for accuracy audits.
    quote          TEXT,
    episode_slug   TEXT,
    episode_title  TEXT,
    guest          TEXT,
    start_seconds  INTEGER,
    -- Deep link to the exact second in the source video.
    source_url     TEXT,

    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_citations_message ON citations (message_id, rank);
CREATE INDEX IF NOT EXISTS idx_citations_chunk   ON citations (chunk_id);


-- Generated Markdown/HTML rendered in the Artifact Viewer. Versioned so
-- "regenerate" keeps history instead of destroying the previous draft.
CREATE TABLE IF NOT EXISTS artifacts (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id  UUID        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_id  UUID        REFERENCES messages(id) ON DELETE SET NULL,

    kind        TEXT        NOT NULL CHECK (kind IN ('markdown', 'html')),
    title       TEXT        NOT NULL,
    content     TEXT        NOT NULL,

    -- essay | document | custom — drives viewer affordances (word count etc.)
    template    TEXT,

    version     INTEGER     NOT NULL DEFAULT 1,
    word_count  INTEGER,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_message ON artifacts (message_id);


-- =============================================================================
-- Triggers: keep updated_at honest without application-code discipline.
-- =============================================================================

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sessions_touch ON sessions;
CREATE TRIGGER trg_sessions_touch BEFORE UPDATE ON sessions
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

DROP TRIGGER IF EXISTS trg_artifacts_touch ON artifacts;
CREATE TRIGGER trg_artifacts_touch BEFORE UPDATE ON artifacts
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

DROP TRIGGER IF EXISTS trg_episodes_touch ON episodes;
CREATE TRIGGER trg_episodes_touch BEFORE UPDATE ON episodes
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
