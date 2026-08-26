# Architecture — The Lenny Growth Assistant

How the system is built, and why each significant decision went the way it did.

---

## Contents

1. [System topology](#1-system-topology)
2. [Database schema](#2-database-schema)
3. [API surface](#3-api-surface)
4. [Component boundaries](#4-component-boundaries)
5. [Ingestion flow](#5-ingestion-flow)
6. [Retrieval flow](#6-retrieval-flow)
7. [Agent layer and routing](#7-agent-layer-and-routing)
8. [Model configuration](#8-model-configuration)
9. [Security](#9-security)
10. [Observability](#10-observability)
11. [Failure modes](#11-failure-modes)
12. [Deployment](#12-deployment)
13. [Decision log](#13-decision-log)

---

## 1. System topology

```
                            ┌───────────────────────┐
                            │       Browser         │
                            │   React + Vite SPA    │
                            └───────────┬───────────┘
                                        │
                       REST (JSON) + SSE (chat streaming)
                                        │
                            ┌───────────▼───────────┐
                            │       FastAPI         │
                            │                       │
                            │  middleware:          │
                            │   request-id + logs   │
                            │   CORS                │
                            │   error contract      │
                            └───────────┬───────────┘
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        │                               │                               │
┌───────▼────────┐           ┌──────────▼─────────┐          ┌──────────▼─────────┐
│  Orchestrator  │           │     Retriever      │          │  Provider Registry │
│                │           │                    │          │                    │
│  provider      │──────────►│  embed query       │          │  health + fallback │
│  history       │           │  vector + keyword  │          │                    │
│  route         │           │  RRF fusion        │          │  ┌──────────────┐  │
│  run skill     │           │  diversify         │          │  │ OllamaProv.  │  │
│  persist       │           │  ABSTENTION GATE   │          │  │ AnthropicPr. │  │
└───────┬────────┘           └──────────┬─────────┘          │  └──────────────┘  │
        │                               │                    └──────────┬─────────┘
        │                               │                               │
        │                    ┌──────────▼─────────┐                     │
        │                    │  PostgreSQL 16     │           ┌─────────▼────────┐
        └───────────────────►│  + pgvector        │           │  Ollama (host)   │
                             │                    │           │  Anthropic (opt) │
                             │  knowledge  ───────┼──────────►│                  │
                             │  conversation      │  embed    └──────────────────┘
                             └────────────────────┘
```

**Three processes** in Compose: `frontend` (nginx serving static files), `backend`
(uvicorn), `db` (pgvector/pg16). Ollama runs on the **host** — a containerised
Ollama on macOS has no GPU access and is roughly 10× slower.

---

## 2. Database schema

Two logical halves, deliberately decoupled.

### Knowledge (rebuildable)

Dropping this loses nothing permanent — it is reconstructible from the source
repository at any time.

```
episodes                                chunks
─────────────────────────────           ─────────────────────────────
id              UUID PK                 id              UUID PK
slug            TEXT UNIQUE  ◄──────────┤ episode_id    UUID FK
guest           TEXT                    │ ord           INTEGER
title           TEXT                    │ speaker       TEXT
youtube_url     TEXT                    │ start_seconds INTEGER (NULLABLE)
video_id        TEXT                    │ end_seconds   INTEGER
publish_date    DATE                    │ text          TEXT
description     TEXT                    │ token_estimate INTEGER
duration_seconds INTEGER                │ embedding     VECTOR(768)
view_count      BIGINT                  │ source_commit TEXT
channel         TEXT                    │ tsv           TSVECTOR (GENERATED)
keywords        TEXT[]                  │ created_at    TIMESTAMPTZ
transcript_format TEXT                  │
has_timestamps  BOOLEAN                 └ UNIQUE (episode_id, ord)
source_commit   TEXT
content_hash    TEXT                    ingest_runs
source_path     TEXT                    ─────────────────────────────
ingested_at     TIMESTAMPTZ             id, started_at, finished_at,
updated_at      TIMESTAMPTZ             source_commit, source_ref,
                                        embedding_model, episodes_*,
                                        chunks_written, status, error
```

Notable columns and why they exist:

- **`start_seconds` is NULLABLE.** One source layout carries no timestamps.
  NULL means "genuinely unknown", never "zero" — and the citation then omits its
  `&t=` deep link rather than pointing at a fabricated position.
- **`content_hash`** (SHA-256 of the raw file) makes re-ingest idempotent and
  cheap: unchanged episodes are skipped without re-embedding.
- **`source_commit`** on both tables makes every answer traceable to an exact
  corpus state.
- **`transcript_format`** records which of the three parsers handled the file,
  so a future upstream format change is diagnosable rather than mysterious.
- **`tsv` is `GENERATED ALWAYS`** rather than trigger-maintained, so it cannot
  drift from `text`.

**Indexes:** GIN on `tsv` (full-text) and `keywords`; trigram GIN on `guest` (so
"sean elis" still finds Sean Ellis); IVFFlat on `embedding` with `lists = 100`,
**rebuilt after ingest** — an IVFFlat index built on an empty table has useless
centroids and silently degrades to a sequential scan.

### Conversation (genuinely stateful)

```
users ──┬──► sessions ──┬──► messages ──┬──► citations ──► chunks (SET NULL)
        │               │               │
        │               └──► artifacts ─┘
```

| Table | Purpose | Notable design |
|---|---|---|
| `users` | Anonymous durable identity | Client mints a UUID into localStorage. This is the seam where real auth plugs in. |
| `sessions` | One chat | Records provider/model at creation, so the UI can show what a past conversation was produced with even after config changes. |
| `messages` | Turns | `seq` is assigned atomically inside the INSERT — read-then-write would collide under concurrency. `insufficient_evidence` is a **column**, not inferred from text, so the abstention metric is queryable in SQL. |
| `citations` | The join between answer and evidence | Stores an **immutable snapshot** (quote, episode, timestamp, URL) beside the chunk FK. `ON DELETE SET NULL`, not CASCADE: a corpus rebuild must not delete the record of what was claimed. |
| `artifacts` | Generated documents | `version` auto-increments per (session, title). Regenerating keeps the previous draft — losing a 1,250-word essay to a retry would be a bad experience. |

### Migrations

Plain SQL applied in filename order, tracked in `schema_migrations` with a
checksum, run at container start. Not Alembic: the schema is small, every
statement is `IF NOT EXISTS`, and autogenerate reliably misreads the pgvector
column type. Editing an already-applied migration logs a checksum warning
instead of silently diverging.

---

## 3. API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health/live` | Liveness. **No dependencies** — an orchestrator that restarts the container because Postgres is slow makes an outage worse. |
| `GET` | `/api/health/ready` | Readiness: DB + corpus + provider. 503 when not ready. |
| `GET` | `/api/health` | Full detail with remediation hints. |
| `GET` | `/api/models` | Provider health, effective model, skill catalogue. Backs the UI indicator. |
| `GET` | `/api/corpus` | Episodes, chunks, source commit, last ingest. |
| `POST` | `/api/sessions` | Create a chat. |
| `GET` | `/api/sessions?user_id=` | List sessions. |
| `GET` | `/api/sessions/{id}` | Full conversation + citations + artifacts. |
| `DELETE` | `/api/sessions/{id}` | Delete (cascades). |
| `POST` | `/api/sessions/{id}/messages` | **Send a message — SSE stream.** |
| `GET` | `/api/artifacts/{id}` | Artifact with content. |
| `GET` | `/api/artifacts/by-session/{id}` | Summaries only (no bodies). |
| `PATCH` | `/api/artifacts/{id}` | Save an edit (re-sanitised). |
| `GET` | `/api/artifacts/{id}/render` | **HTML for the sandboxed iframe, under a strict CSP.** |
| `GET` | `/api/artifacts/{id}/download` | Raw source as an attachment. |

There is deliberately **no** artifact-creation endpoint: artifacts are produced
by the grounding pipeline, and a side door would bypass what makes them
trustworthy. There is also no endpoint to *change* the model — that is a
configuration concern, and a runtime mutation would create a second source of
truth that no config file records.

### Error contract

Every failure has one shape:

```json
{ "error": {
    "code": "provider_unavailable",
    "message": "Ollama is not reachable at http://localhost:11434.",
    "detail": { "model": "qwen2.5:7b-instruct" },
    "remediation": "Start it with `ollama serve`, then `ollama pull ...`"
}}
```

`remediation` exists because the common failures here are **operational**, not
programming errors. A bare 503 makes a developer guess; the API should say
"run `ollama pull qwen2.5:7b-instruct`".

FastAPI's validation errors are reshaped into the same envelope, so clients
never face two error formats.

### Streaming protocol (SSE)

SSE rather than WebSockets: the flow is strictly one-directional for a turn, and
SSE gives that over plain HTTP with no upgrade handshake or connection state.

```
data: {"type":"provider","provider":"ollama","model":"qwen2.5:7b-instruct","was_fallback":false}
data: {"type":"skill","skill":"answer_from_transcripts","reason":"default_grounded_qa"}
data: {"type":"status","stage":"retrieving","message":"Searching 303 episode transcripts…"}
data: {"type":"evidence","evidence":[...]}          ← ~0.8s, BEFORE generation
data: {"type":"token","text":"To determine"}
...
data: {"type":"correction","text":"..."}            ← only if a citation was stripped
data: {"type":"done","message_id":"...","citations":[...],"persisted":true}
event: close
```

`evidence` is emitted **before** the first token deliberately: the user sees
which transcripts are being drawn on within a second, which makes a 10-second
local generation informative rather than dead air.

Validation and the user-message write happen **before** the stream opens, so bad
requests get a normal JSON error with a proper status code — once a
`StreamingResponse` starts, the status line is already sent.

---

## 4. Component boundaries

| Layer | Owns | Must not |
|---|---|---|
| `api/` | HTTP contracts, validation, SSE framing | Contain business logic |
| `agent/` | Orchestration, routing, citation validation | Know about HTTP or SQL |
| `agent/skills/` | One capability each: prompt, retrieval strategy, post-processing | Know which provider is active |
| `retrieval/` | Embedding, hybrid search, fusion, **the abstention decision** | Know about models or prompts |
| `providers/` | Vendor SDKs, streaming, health, fallback | Know about skills or retrieval |
| `db/` | Pool, migrations, queries | Contain business rules |
| `core/` | Config, logging, errors, sanitisation | Import from any layer above |
| `ingest/` | Corpus sync, parsing, chunking, embedding | Be reachable over HTTP |

The two boundaries that carry real weight:

**No application code names a model.** Only `core/config.py` does. That is what
makes "switch models without touching application code" structurally true
rather than merely claimed.

**The abstention decision lives in `retrieval/`, not in a prompt.** It runs
before any model is invoked, so a weak match cannot be talked into becoming an
answer.

---

## 5. Ingestion flow

```
sync_corpus()                git clone --depth 1  (or fetch + checkout)
       │                     never vendored: third-party copyrighted material
       ▼
resolve commit SHA           stamped onto every episode and chunk
       │
       ▼
discover_episodes()          episodes/<slug>/transcript.md   → 303 found
       │
       ▼
for each episode:
   ├─ content_hash unchanged? ──── yes ──► SKIP (idempotent refresh)
   │                                        no
   ▼                                        ▼
parse_transcript()           detect layout among three known formats
   │                         raise ParseError if none match (never silent)
   ▼
chunk_turns()                pack whole speaker turns to ~350 tokens,
   │                         1 turn overlap; never split a turn
   ▼
embed(kind="document")       nomic-embed-text, "search_document: " prefix
   │                         batched 16 at a time
   ▼
UPSERT episode               ON CONFLICT (slug) DO UPDATE
DELETE + INSERT chunks       replace-all: ordinals aren't stable across
   │                         upstream corrections
   ▼
REINDEX ivfflat + ANALYZE    centroids need real data to be useful
   ▼
record ingest_run            audit trail
```

**Why chunk on turn boundaries.** The transcripts are already segmented into
timestamped speaker turns. Fixed-width splitting would destroy that and with it
the ability to say who said something and when. Turn-based chunking is what
makes second-accurate citation possible at all.

**Three formats.** Parsing all 303 episodes revealed three layouts: a header
timestamp form (301 episodes), an inline `[HH:MM:SS] Speaker:` form, and a
speaker-only form with no timestamps. The first implementation handled only the
dominant one and produced zero turns for the others — the episodes silently
vanished while the ingest reported success. The parser now detects per file and
**raises** on an unknown layout.

**Failure isolation.** One bad episode is counted and reported, never aborting a
300-episode run and never silently dropped.

---

## 6. Retrieval flow

```
query
  │
  ├─ corpus empty? ──► KnowledgeBaseEmptyError (a setup problem, distinct
  │                    from "no results", which is a valid answer)
  ▼
embed(kind="query")          "search_query: " prefix
  │
  ├──────────────────────┬──────────────────────┐
  ▼                      ▼                      │
vector_search(40)    keyword_search(40)         │  both honour the same
cosine similarity    websearch_to_tsquery       │  metadata filters
  │                      │                      │
  └──────────┬───────────┘                      │
             ▼                                  │
    Reciprocal Rank Fusion (k=60)               │
    combines RANKS, not scores — no calibration │
    needed between two incomparable scales      │
             ▼                                  │
    diversify(top_k=6, max_episodes=5)          │
    hard cap on episodes, soft cap per episode  │
             ▼                                  │
    ┌────────────────────────────────┐          │
    │      ABSTENTION GATE           │          │
    │  best_similarity < 0.60?  ─────┼──► INSUFFICIENT EVIDENCE
    │  fewer than 2 strong AND       │     (model never invoked)
    │  best < 0.75?             ─────┼──► INSUFFICIENT EVIDENCE
    └────────────────┬───────────────┘
                     ▼
        Evidence[] labelled E1..En
```

**Why hybrid.** Dense retrieval handles paraphrase; sparse handles proper nouns,
product names, acronyms and numbers — exactly what embeddings are worst at. A
query for "ICE framework" must not be defeated by semantic drift.

**Why RRF.** It fuses *ranks*, so cosine similarity and `ts_rank` — two
quantities on incomparable scales — never have to be calibrated against each
other. A chunk found by both retrievers accumulates both contributions.

**Why diversify.** Without it, one long episode that returns to a topic
repeatedly supplies every evidence slot, and one guest's opinion is presented as
consensus. `max_episodes` is a **hard** cap (citation legibility);
per-episode is **soft** (backfill may exceed it to reach `top_k`, but only from
episodes already selected).

**Why the threshold is 0.60.** Measured, not guessed — see the README. The
margin between in-corpus and out-of-corpus questions is ~0.02, which is why the
corroboration rule exists as a second gate rather than trusting one number.

**Task prefixes matter enormously.** `nomic-embed-text` is asymmetric and
expects `search_document:` / `search_query:`. Without them the two distributions
*overlapped* and no threshold could separate them — abstention was impossible.
This is the single highest-impact correctness fix in the retrieval stack.

---

## 7. Agent layer and routing

### One turn

```
Orchestrator.run_turn()
  1. get_provider()            health-checked, with fallback
  2. load history + prior evidence
  3. router.route()            which skill
  4. skill.run()               stream events
  5. validate citations        strip fabrications
  6. persist                   message + citations + artifact
```

Persistence happens **after** streaming, from the terminal event. A database
failure then degrades to "answer shown but not saved" — logged, and reported to
the client as `persisted: false` — rather than discarding work the user has
already read.

### Skills

| Skill | Retrieval strategy | Post-processing |
|---|---|---|
| `answer_from_transcripts` | `top_k=6`, follow-ups anchored with the previous question | Validate citations, keep inline `[E1]` markers |
| `write_ship30_essay` | `top_k=12` — essays need breadth across operators | Validate, then **strip** markers (editorial noise in prose); sources rendered as a list |
| `create_artifact` | `top_k=10` | Validate, strip markers, strip code fences, **sanitise HTML** |

The Ship 30 skill loads its writing principles from a **versioned Markdown
file** (`skills/ship30_principles.md`), not a prompt string. A writer can tune
the craft rules without touching Python, and changes are reviewable as a diff.

> **Scope note.** Ship 30's signature format is the ~250-word *atomic essay*;
> the brief asks for ~1,250 words. We apply their principles at long-form length
> by treating the piece as a spine of 4–6 self-contained units. Length comes
> from stacking complete thoughts, never from inflating one. This tension is
> documented at the top of the principles file.

### Routing: the central trade-off

The brief mandates **both** the Claude Agent SDK **and** a local 7–8B model as
the demo default. Those want opposite strategies:

- **Claude selects tools reliably.** Given well-described skills it picks
  correctly and can chain them in ways a fixed pipeline cannot.
- **A 7B does not.** Choosing among four skills, it errs often enough to matter,
  and a wrong choice is severe: the user asks a question and receives a
  1,250-word essay.

**Resolution:** route by declared provider capability
(`BaseProvider.supports_native_tools`).

| Provider | Strategy |
|---|---|
| Anthropic (`True`) | Model-driven tool selection via the Agent SDK. Skills are exposed as in-process MCP tools through `create_sdk_mcp_server`. |
| Ollama (`False`) | Deterministic intent classification (`agent/router.py`). |

This is capability-appropriate design, not a workaround: use the model's
judgement where it is reliable and code where it is not. One strategy for both
would mean either crippling the cloud path or shipping a local path that
misroutes.

The classifier is rule-based rather than a second LLM call — rules are
inspectable, instant, free and unit-testable, whereas a classification call
would add a full local-inference round trip to every turn.

Explicit UI actions ("Write essay") always override inference.

### Citation validation

Prompts are requests, not guarantees. Model output is treated as untrusted:

1. Extract every `[E*]` label emitted.
2. Any label with no corresponding retrieved passage is **removed from the
   text** — a dangling citation is worse than none because it looks verified.
3. Report what was stripped, so hallucination rate is measurable rather than
   anecdotal.
4. An answer left with zero valid citations is flagged ungrounded.

A fabricated citation cannot reach the user regardless of which model produced
it.

### Agent SDK vs. direct transport

The Claude Agent SDK is a Python wrapper that drives the Claude Code CLI as a
subprocess. That is a real deployment constraint — the CLI must be installed in
the image. `ANTHROPIC_AGENT_RUNTIME` selects:

- `sdk` — full agent loop, tool orchestration, permission model.
- `messages` — direct Messages API; fewer moving parts, no Node dependency.

If `sdk` is requested but the SDK or CLI is absent, the provider degrades to
`messages` **at construction** and logs it — discovering this at request time
would turn a deployment gap into a user-facing 500.

When the SDK runs, the agent is given **only** our knowledge-base tools — no
filesystem, no shell. Retrieved transcript text is untrusted input, and a
prompt-injection payload inside a transcript must have nothing dangerous to
reach for.

---

## 8. Model configuration

```
.env  ──►  core/config.py (Settings)  ──►  providers/registry.py
                                                    │
                                     ┌──────────────┴──────────────┐
                                     ▼                             ▼
                            OllamaProvider              AnthropicProvider
                                     └──────────────┬──────────────┘
                                                    ▼
                                            BaseProvider interface
                                          (complete / stream / health)
                                                    │
                                    everything above sees only this
```

Selection and fallback:

```
try LLM_PROVIDER
  └─ healthy? ──yes──► use it
        no
        ▼
  LLM_FALLBACK_PROVIDER set and different?
        ├─ no ──► ProviderUnavailableError (names the failure + fix)
        └─ yes ─► healthy? ──yes──► use it, flagged was_fallback
                        no ──► error naming BOTH failures
```

Health results are cached for 30 seconds: probing before every message would add
latency to every turn, but a stale cache would keep routing to a dead provider.

A fallback is **never silent** — logged at WARNING, flagged on the message,
shown as a UI banner. A local 7B and a frontier model produce visibly different
output; hiding the switch would make that look like random unreliability.

---

## 9. Security

### Untrusted-content threat model

Generated HTML is influenced by retrieved transcript text we do not control.
That is a genuine injection path, so **three independent layers** apply, and no
single one is trusted:

| Layer | Mechanism | Defends against |
|---|---|---|
| 1. Prompt | "no JavaScript" instruction | Nothing — a preference, not a control. Reduces noise only. |
| 2. Server sanitiser | Allowlist parser over stdlib `HTMLParser`, run **before storage and again on read** | Anything the model emits; makes API responses safe for *any* consumer |
| 3. Client sandbox | `<iframe>` on a real URL + response-header CSP + `sandbox` attribute | A failure of layers 1 and 2 |

**Allowlist, not blocklist.** Blocklists lose to obfuscation
(`<scr<script>ipt>`, entity-encoded `javascript:`, novel event handlers). An
allowlist fails closed — anything not explicitly permitted is dropped, including
constructs invented after this code was written.

| Allowed | Blocked |
|---|---|
| Structural/text/table tags, `<style>`, `<a>` | `script`, `iframe`, `object`, `embed`, `form`, `link`, `base`, `svg`, `canvas`, media |
| `class`, `id`, `style`, `title`, `lang`, `dir`, `colspan`, `rowspan`, `href`, `target`, `rel` | Every `on*` handler (matched by **prefix**), all other attributes |
| `http:`, `https:`, `mailto:`, relative, anchor | `javascript:`, `data:`, `vbscript:`, `file:` |
| Inline CSS declarations | `expression()`, `@import`, `url()`, `-moz-binding`, `behavior:`, `position:fixed` |
| `<meta charset>` | `<meta http-equiv>` (refresh redirects) |

Notes on specific decisions:

- `url()` is blocked outright, which also removes remote background images —
  consistent with "no remote resources".
- At-rules are stripped **before** declaration filtering, because `@import` is
  terminated by `;` rather than `}`. An earlier version split on `}` and let
  `@import` survive inside the next rule's selector.
- `<meta>` allows **only** `charset`. Stripping `http-equiv` while keeping
  `content` left an attacker-supplied URL in the output.
- `target="_blank"` gets `rel="noopener noreferrer"` injected unconditionally.
- Comments are dropped entirely (conditional comments were historically an
  execution vector).

**Why a real URL, not `srcdoc`.** `srcdoc` content inherits the parent origin, so
a sandbox escape lands in our origin with access to localStorage. A URL lets the
*server* set CSP as a response header — a `<meta>` CSP inside model-generated
markup is part of the content being defended against.

```
Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline';
  img-src data:; font-src data:; form-action 'none'; base-uri 'none';
  frame-ancestors 'self'; sandbox allow-popups
```

Plus `sandbox="allow-popups allow-popups-to-escape-sandbox"` on the iframe —
**no** `allow-scripts`, **no** `allow-same-origin`, so it is an opaque origin
that cannot execute JavaScript even if everything else failed.

Markdown gets its own two layers: `marked` escapes raw HTML, then DOMPurify
applies an allowlist.

26 XSS payloads are test-enforced (`tests/test_sanitize.py`), alongside proof
that legitimate documents survive intact.

### Other controls

- **Prompt injection:** evidence is delimited in `<evidence>` blocks and labelled
  as untrusted data; the system prompt instructs that instruction-shaped text
  inside a transcript is quoted speech.
- **SQL injection:** every query is parameterised; no string interpolation of
  user input.
- **Secrets:** never logged (a redaction processor strips known key names
  regardless of caller), never returned by the API, never committed.
- **Input limits:** messages capped at 4,000 characters, artifacts at 200,000.
- **Downloads:** served as `text/plain` with `Content-Disposition: attachment`
  and a filename stripped of path separators — a download must never become an
  execution path.

### Deliberately out of scope

No authentication, authorisation, rate limiting, or multi-tenancy. `users.id` is
the documented seam where auth plugs in without a schema change. This is stated
plainly rather than implied — see PRD assumption A3.

---

## 10. Observability

Structured logs (structlog), JSON or console. Each pipeline stage has its own
named logger so stages can be debugged **separately**:

```
api | model | retrieval | db | ingest | agent | artifact
```

```bash
docker compose logs backend | grep '"stage": "retrieval"'
```

A `request_id` is bound once per request and propagates through every stage via
contextvars, so one answer's full lifecycle can be reassembled from interleaved
output. It is returned as `X-Request-ID` and honoured if the client supplies one.

Key events: `retrieval_complete` (candidates, best similarity, threshold,
sufficiency, reason), `stream_first_token` (TTFT, a named guardrail metric),
`citations_hallucinated` (labels and rate), `provider_fallback_engaged`,
`ingest_progress`, `artifact_html_sanitized`.

Health probes are excluded from access logs — they fire constantly and would
drown the signal.

---

## 11. Failure modes

| Failure | Behaviour |
|---|---|
| **Missing API key** | Detected at health check. Falls back to Ollama if configured; otherwise a 503 naming the fix. Never a 500. |
| **Ollama down** | Distinguished from "model not pulled" — different fixes. UI shows a red indicator; error names the command. |
| **Model not pulled** | Ollama's 404 is treated as a setup error and **not retried** (retrying wastes time). Error gives the exact `ollama pull`. |
| **Model timeout** | 504 with remediation: raise the timeout, lower `RETRIEVAL_TOP_K`, or use a smaller model. |
| **Empty retrieval** | Not an error — a designed insufficient-evidence response listing what the corpus *does* cover. |
| **Corpus not ingested** | Distinct error (`knowledge_base_empty`) from "no results", with the ingest command. |
| **DB down at startup** | App **starts anyway** and serves `/api/health` to explain. A process that exits can't report anything. |
| **DB down mid-turn** | Answer still streams; persistence fails, is logged, and returns `persisted: false`. |
| **Embedding dim mismatch** | Fails at **boot** with an actionable message, not at the first insert with an opaque pgvector error. |
| **Unparseable transcript** | Raises, counted, reported. Never silently dropped. |
| **Malformed tsquery** | Keyword search returns empty and logs; vector results still answer. |
| **Agent SDK/CLI absent** | Degrades to the Messages API at construction, logged. |
| **Client disconnects** | `CancelledError` handled as normal (logged at info, not error). |

---

## 12. Deployment

```
docker compose up --build -d
```

| Service | Image | Port | Notes |
|---|---|---|---|
| `db` | `pgvector/pgvector:pg16` | 5433→5432 | Named volume; healthcheck gates the backend |
| `backend` | Python 3.12-slim | 8000 | Migrations at start; non-root user |
| `frontend` | nginx:alpine | 5173→80 | Multi-stage; runtime has no Node or source |
| `ingest` | same as backend | — | `--profile ingest`, run on demand |

Startup ordering: `db` healthcheck → backend `entrypoint.sh` waits for the port
→ migrations → uvicorn. Ingestion is deliberately **not** in the startup path —
it is a minutes-long batch job, and blocking `up` on it would make the stack
look hung.

Volumes: `pgdata` (database), `corpus` (the clone, so re-ingest after a rebuild
doesn't re-download).

**Production gaps**, stated honestly: no TLS termination, no auth, no rate
limiting, no backup automation, no horizontal scaling (the health cache and
connection pool are per-process; both are fine to replicate, but session
affinity is not required since all state is in Postgres).

---

## 13. Decision log

| Decision | Alternative | Why |
|---|---|---|
| RAG | Full-corpus long-context | 6.2M tokens is ~6× the largest context window. Not costly — impossible. And a 7B's usable context settles it. |
| pgvector | Pinecone/Weaviate | 22k chunks is comfortably within Postgres. One datastore, one backup story, metadata filter + vector search in one query. |
| Turn-boundary chunks | Fixed-width | Preserves speaker and timestamp; the precondition for second-accurate citation. |
| Hybrid + RRF | Dense only | Embeddings are weak on proper nouns and numbers. RRF avoids calibrating incomparable scales. |
| Abstention in code | Prompt instruction | A 7B told "only answer if supported" answers anyway. A 7B never invoked cannot. |
| Deterministic routing (local) | Model tool-choice | Measured unreliability at 7B; a misroute is severe and slow. |
| Agent SDK (cloud) | Messages only | The brief mandates it, and Claude genuinely routes better than our rules. |
| SSE | WebSockets | One-directional flow; no upgrade handshake or connection state. |
| asyncpg + raw SQL | SQLAlchemy ORM | Small fixed model; the queries that matter are hand-written fusion SQL an ORM wouldn't improve. |
| Allowlist sanitiser | DOMPurify server-side / bleach | Fails closed, no extra dependency, explicitly documented and testable. |
| iframe URL + header CSP | `srcdoc` | `srcdoc` inherits the parent origin; a header CSP can't be omitted by generated markup. |
| Plain SQL migrations | Alembic | Small schema, idempotent statements, and autogenerate misreads pgvector types. |
| Anonymous localStorage id | Full auth | Auth is table stakes and not what's being evaluated. The seam is documented. |
| Ollama on host | Containerised | No GPU access on macOS in a container; ~10× slower. |
