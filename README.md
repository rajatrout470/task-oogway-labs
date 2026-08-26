# The Lenny Growth Assistant

A conversational assistant that answers product and growth questions **strictly
from 303 episodes of Lenny's Podcast** — and tells you plainly when the
transcripts don't cover your question.

Every claim carries a citation that deep-links to the exact second of the source
video. Answers can be turned into publishable Ship 30 for 30–style essays or
Markdown/HTML documents, rendered in a sandboxed in-app Artifact Viewer.

**It runs entirely on your laptop.** The default path uses a local 7B model via
Ollama — no cloud API key required, and no question or transcript leaves the
machine.

---

## Contents

- [What it does](#what-it-does)
- [Architecture at a glance](#architecture-at-a-glance)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Native development](#native-development-without-docker)
- [Configuration](#configuration)
- [Switching models](#switching-models)
- [The knowledge base](#the-knowledge-base)
- [Testing](#testing)
- [Measured performance](#measured-performance)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Documentation](#documentation)

---

## What it does

| Capability | Detail |
|---|---|
| **Grounded Q&A** | Answers synthesised *only* from retrieved transcript passages, with `[E1]`-style citations that resolve to a timestamped YouTube link. |
| **Honest refusal** | If retrieval falls below a calibrated relevance floor, the model is never invoked. You get a designed "I don't have evidence for this" plus the topics the corpus *does* cover. |
| **Ship 30 essays** | A dedicated skill encoding the real Ship 30 for 30 writing methodology, producing ~1,250-word essays whose claims trace to transcripts. |
| **Artifacts** | Markdown and HTML/CSS documents rendered beside the chat — sanitised server-side and isolated in a sandboxed iframe. |
| **Sessions** | Independent conversations with their own context, persisted in PostgreSQL. |
| **Model switching** | Local (Ollama) or cloud (Anthropic), changed by configuration alone. The active model is always visible in the UI. |

### The design principle

This is not a general-purpose PM chatbot. It is a **corpus-bounded research
instrument**, and refusing to answer is a feature. An assistant that answers
"what's the best CI tool?" from parametric knowledge has destroyed the one thing
that makes it trustworthy.

Grounding is enforced in **code**, not requested in a prompt:

1. Retrieval runs first and decides sufficiency *deterministically*. Below
   threshold, the model is never called — it cannot be talked into answering.
2. Evidence reaches the model pre-numbered and delimited as untrusted data.
3. After generation, every citation is mechanically verified against the real
   evidence IDs. Fabricated ones are **stripped from the text** before display.

---

## Architecture at a glance

```
┌──────────────┐        SSE         ┌──────────────────────────────────┐
│   React UI   │◄───────────────────│           FastAPI                │
│  chat +      │                    │                                  │
│  artifact    │──── REST ─────────►│  ┌────────────────────────────┐  │
│  viewer      │                    │  │      Orchestrator          │  │
└──────────────┘                    │  │  provider → route → skill  │  │
                                    │  └─────────┬──────────────────┘  │
                                    │            │                     │
                                    │  ┌─────────▼────────┐            │
                                    │  │  Skills          │            │
                                    │  │  grounded_qa     │            │
                                    │  │  ship30_essay    │            │
                                    │  │  create_artifact │            │
                                    │  └─────────┬────────┘            │
                                    │            │                     │
                                    │  ┌─────────▼────────┐            │
                                    │  │  Retriever       │            │
                                    │  │  hybrid + RRF    │            │
                                    │  │  abstention gate │            │
                                    │  └─────────┬────────┘            │
                                    └────────────┼─────────────────────┘
                                                 │
                   ┌─────────────────────────────┼───────────────┐
                   ▼                             ▼               ▼
          ┌─────────────────┐         ┌──────────────┐   ┌──────────────┐
          │  PostgreSQL 16  │         │    Ollama    │   │  Anthropic   │
          │  + pgvector     │         │  (local,     │   │  (optional)  │
          │  21,984 chunks  │         │   default)   │   │              │
          └─────────────────┘         └──────────────┘   └──────────────┘
```

Full detail in [architecture.md](architecture.md).

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| **Docker Desktop** | 24+ | With Compose v2. |
| **Ollama** | 0.3+ | Runs on the **host**, not in a container — see below. |
| **~8 GB free disk** | | 4.7 GB model + 274 MB embedder + ~1 GB database. |
| **~16 GB RAM** | | 8 GB works with `llama3.2:3b` instead. |

For native development you additionally need Python 3.11/3.12 (via `uv`),
Node 20+, and PostgreSQL 16 with pgvector.

### Why Ollama is not containerised

On macOS a containerised Ollama has no access to Apple Silicon GPU
acceleration and runs roughly an order of magnitude slower. Running it on the
host keeps the demo usable; containers reach it via `host.docker.internal`.

---

## Quick start

```bash
# 1. Pull the models (one time, ~5 GB)
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text

# 2. Configure
cp .env.example .env          # defaults work as-is; no API key needed

# 3. Start the stack
make up                        # or: docker compose up --build -d

# 4. Load the transcripts (~20 minutes for all 303 episodes)
make ingest                    # or `make ingest-fast` for a 15-episode smoke test
```

Then open **http://localhost:5173**.

Verify everything is healthy:

```bash
curl -s localhost:8000/api/health | python3 -m json.tool
```

`status` should be `"ok"`. `"degraded"` means the app works but something isn't
as configured — the response says exactly what.

> **First-run note.** The ingest embeds ~22,000 passages locally and genuinely
> takes ~20 minutes. It is idempotent: re-running skips unchanged episodes in
> seconds. Use `make ingest-fast` if you just want to see the thing work.

---

## Native development (without Docker)

Faster iteration; requires local PostgreSQL 16 with the pgvector extension.

```bash
make setup                     # venv + node modules + .env

createdb lenny                 # or use `make db-up` for Postgres in Docker
make migrate

# Point .env at your local services
#   POSTGRES_HOST=localhost
#   OLLAMA_BASE_URL=http://localhost:11434

make ingest-fast               # quick corpus
make dev-backend               # http://localhost:8000
make dev-frontend              # http://localhost:5173  (separate terminal)
```

> **Python version.** The containers pin Python 3.12. Several dependencies
> (`asyncpg`, `pgvector`) do not reliably publish wheels for the newest
> interpreter, and a source build in a slim image fails slowly and confusingly.
> `make setup` uses `uv` to provision 3.12 regardless of your system Python.

---

## Configuration

Everything lives in `.env`. See [.env.example](.env.example) — every variable is
documented inline and marked required or optional. The defaults boot a fully
local stack with no API keys.

The variables that matter most:

| Variable | Default | What it controls |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | Active provider: `ollama` or `anthropic`. |
| `LLM_FALLBACK_PROVIDER` | `none` | Used automatically if the primary is unhealthy. |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct` | Generation model. |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedder. **Changing it requires a re-ingest.** |
| `RETRIEVAL_TOP_K` | `6` | Evidence passages per answer. Higher = better recall, slower. |
| `RETRIEVAL_MIN_SCORE` | `0.60` | Abstention threshold. **Empirically calibrated** — see below. |
| `ANTHROPIC_API_KEY` | *(empty)* | Optional. Leave blank for the local demo. |

### No secrets are committed

`.env` is gitignored; only `.env.example` is tracked, and it contains no real
values. The API never returns a key — `/api/models` reports only whether one is
*present*.

---

## Switching models

**No application code names a model.** The only place model identity exists is
`.env`, read through `app/core/config.py`. Everything above the provider
registry asks for "the active provider" and receives an interface.

```bash
# A different local model
OLLAMA_MODEL=llama3.1:8b

# Low-RAM machine
OLLAMA_MODEL=llama3.2:3b

# Cloud
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

Then `make restart`. Confirm with `make models`, or look at the indicator in the
UI sidebar.

### Fallback behaviour

If the configured provider fails its health check and `LLM_FALLBACK_PROVIDER`
names a different one, the app switches automatically. This is **never silent**:
it is logged at WARNING, flagged on the message, and shown as a banner in the
UI. A local 7B and a frontier cloud model produce visibly different output, and
hiding that switch would make the difference look like random unreliability.

If both are unavailable, the error names *both* failures and how to fix each.

### Changing the embedding model

The `chunks.embedding` column is typed `VECTOR(768)`. A model with different
dimensionality needs a migration **and** a full re-ingest:

```bash
# 1. Add a migration altering chunks.embedding to the new dimension
# 2. Update OLLAMA_EMBED_MODEL and EMBEDDING_DIMENSIONS in .env
make reingest
make calibrate     # the abstention threshold is NOT portable across models
```

The app refuses to start if `EMBEDDING_DIMENSIONS` disagrees with the schema,
rather than failing later with an opaque pgvector error.

---

## The knowledge base

### How transcripts are loaded

Transcripts come from
[ChatPRD/lennys-podcast-transcripts](https://github.com/ChatPRD/lennys-podcast-transcripts),
**cloned at ingest time and never vendored into this repository** — they are
third-party copyrighted material. The app surfaces short attributed excerpts
that link back to the original video; it never redistributes whole transcripts.

### How they're chunked

Chunks follow **speaker-turn boundaries**, not fixed widths. The transcripts are
already segmented into timestamped turns, so every chunk inherits a real speaker
and a real timestamp — which is what makes second-accurate citation possible.

The corpus is not homogeneous: it contains **three distinct transcript layouts**,
and the ingest detects each per file. One episode has no timestamps at all; its
citations correctly degrade to episode-level links rather than inventing a
position. An unparseable transcript raises loudly rather than silently vanishing
from the index.

Result: **303 episodes → 21,984 chunks**, median ~420 tokens.

### How they're retrieved

Hybrid search, fused with Reciprocal Rank Fusion:

- **Dense** (pgvector cosine) handles paraphrase — "how do I know if we have
  PMF?" finds passages that never say "product-market fit".
- **Sparse** (Postgres full-text) handles what embeddings are worst at: proper
  nouns, product names, acronyms, numbers.

Results are then diversified so one talkative guest cannot fill every evidence
slot and have their view presented as consensus.

### How refresh works

```bash
make ingest      # incremental: skips episodes whose content hash is unchanged
make reingest    # force: re-embeds everything
make status      # what's indexed, from which commit
```

Every chunk records the **source commit SHA** it came from, so any answer traces
to an exact corpus state.

### The abstention threshold is measured, not guessed

`RETRIEVAL_MIN_SCORE` is the single most important number in the grounding
stack. It was calibrated by measuring actual score distributions:

| | top-1 similarity |
|---|---|
| In-corpus questions (8) | 0.605 – 0.824 |
| Out-of-corpus questions (8) | 0.525 – 0.586 |

0.60 sits in the gap. Re-measure any time the embedding model changes:

```bash
make calibrate
```

If that reports **OVERLAP**, no threshold can work and something upstream is
broken. (This is how we caught that `nomic-embed-text` was being used without
its required `search_query:` / `search_document:` task prefixes — without them
the distributions overlapped and abstention was impossible.)

---

## Testing

```bash
make test         # unit tests — no database, no Ollama, no keys needed
make test-all     # adds integration tests (requires PostgreSQL)
make lint         # ruff + tsc
make check        # lint + test
```

**141 tests.** Unit tests run on a clean machine with nothing else installed;
infrastructure-dependent tests skip themselves automatically rather than failing.

Coverage focuses on what can actually hurt:

| Area | What's covered |
|---|---|
| **Sanitisation** | 26 XSS payloads must be provably inert — scripts, obfuscated tags, event handlers, `javascript:`/`data:` URLs, CSS `@import` and `expression()`, meta-refresh, CDATA smuggling. Plus proof that legitimate documents survive intact. |
| **Citations** | Fabricated citations are stripped; multi-label brackets are partially cleaned; hallucination rate is measurable. |
| **Chunking** | All three transcript formats; timestamp fidelity; speaker attribution; unparseable input raises. |
| **Retrieval** | RRF fusion, diversification caps, and every branch of the abstention gate. |
| **Routing** | Question vs. essay vs. artifact intent; explicit override; revision detection. |
| **Providers** | Fallback engagement, both-down error reporting, Agent SDK degradation. |
| **Persistence** | Session isolation, cascade deletes, artifact versioning. |

A manual UI test plan is in [docs/manual-test-plan.md](docs/manual-test-plan.md).

---

## Measured performance

On an Apple Silicon laptop with `qwen2.5:7b-instruct`, warm model:

| Stage | Time |
|---|---|
| Retrieval (hybrid search over 21,984 chunks) | ~0.15s |
| **Sources visible in the UI** | **0.80s** |
| First token of prose | ~10.9s |
| Complete cited answer | ~20.9s |
| Insufficient-evidence response | ~8s |
| Cold model load (first request only) | ~27s |

**Time to first token is dominated by prompt evaluation**, not generation: a 7B
model prefills at roughly 190 tokens/sec here, and a grounded answer requires a
multi-thousand-token evidence prompt. That is the real cost of the local-first
requirement.

Mitigations actually implemented: evidence streams to the UI before generation
begins (so the wait is informative, not dead), passages are trimmed to ~300
tokens on clean sentence boundaries, `RETRIEVAL_TOP_K` is 6, and the model is
kept resident for 30 minutes so a reading pause doesn't trigger a 27s reload.

The cloud path is substantially faster. See [PRD.md](PRD.md) §3 for the full
metric revision and reasoning.

---

## Troubleshooting

<details>
<summary><strong>"Ollama is not reachable"</strong></summary>

```bash
ollama serve          # start the daemon
ollama list           # confirm the models are pulled
```

From Docker, `OLLAMA_BASE_URL` must be `http://host.docker.internal:11434`, not
`localhost` — inside a container `localhost` is the container itself.
</details>

<details>
<summary><strong>"Model is not pulled"</strong></summary>

```bash
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
```

The error message names the exact command. Health checks distinguish "daemon
down" from "model missing" because the fixes differ.
</details>

<details>
<summary><strong>"The transcript corpus has not been ingested"</strong></summary>

```bash
make ingest           # or `make ingest-fast` for a quick subset
make status           # verify
```

This is a *setup* error, deliberately distinct from "no relevant results" —
which is a correct, well-designed answer.
</details>

<details>
<summary><strong>Assistant says "I don't have grounded evidence" too often</strong></summary>

The abstention threshold may be too strict for your corpus or embedding model.

```bash
make calibrate        # measure the real distributions
```

Then set `RETRIEVAL_MIN_SCORE` inside the reported safe band. Lowering it
increases recall and hallucination risk; the trade-off is yours to make
deliberately.
</details>

<details>
<summary><strong>Answers are slow</strong></summary>

Expected: ~11s to first token locally (see [Measured performance](#measured-performance)).
To go faster:

- `RETRIEVAL_TOP_K=4` — smaller prompt, less evidence
- `OLLAMA_MODEL=llama3.2:3b` — faster, measurably weaker at refusing
- Use the cloud path

The first request after startup includes a ~27s model load. That's one-time.
</details>

<details>
<summary><strong>Database connection failures</strong></summary>

```bash
make ps                       # is `db` healthy?
docker compose logs db
```

The backend deliberately starts even when Postgres is down, so `/api/health`
can tell you what's wrong. A process that exits can't report anything.
</details>

<details>
<summary><strong>Ingestion fails partway</strong></summary>

It's idempotent — just re-run `make ingest`. Completed episodes are skipped by
content hash. Individual episode failures are counted and reported rather than
aborting the run or being silently dropped.
</details>

<details>
<summary><strong>Port already in use</strong></summary>

Override in `.env`: `BACKEND_PORT`, `FRONTEND_PORT`, `POSTGRES_HOST_PORT`.
Postgres already maps to host **5433** to avoid colliding with a local install.
</details>

---

## Project layout

```
├── backend/
│   ├── app/
│   │   ├── agent/            orchestrator, router, citation validator
│   │   │   └── skills/       grounded_qa, ship30_essay, artifact
│   │   │       └── ship30_principles.md   ← encoded writing methodology
│   │   ├── api/              routes + Pydantic contracts
│   │   ├── core/             config, logging, errors, HTML sanitiser
│   │   ├── db/               pool, migrations runner, repositories
│   │   ├── ingest/           corpus sync, chunker, pipeline, CLI
│   │   ├── providers/        base interface, ollama, anthropic, registry
│   │   └── retrieval/        embeddings, hybrid store, retriever
│   ├── migrations/           plain SQL, applied idempotently at startup
│   ├── scripts/              threshold calibration
│   └── tests/
├── frontend/src/
│   ├── components/           Sidebar, Message, Sources, ArtifactViewer, Composer
│   └── lib/                  api client + SSE, sanitising markdown renderer
├── docs/                     manual test plan, demo script
└── agent-transcripts/        build log, including failures and fixes
```

---

## Documentation

| Document | Contents |
|---|---|
| [PRD.md](PRD.md) | User, problem, success metrics, assumptions, scope, risks, acceptance criteria |
| [architecture.md](architecture.md) | Schema, endpoints, component boundaries, agent routing, security model, deployment |
| [design.md](design.md) | UI/UX principles, information architecture, interaction states, accessibility |
| [docs/demo-script.md](docs/demo-script.md) | Recording checklist and narrative |
| [docs/manual-test-plan.md](docs/manual-test-plan.md) | UI test cases |
| [agent-transcripts/](agent-transcripts/) | How this was built, including what went wrong |

---

## License & attribution

Podcast transcripts are the property of their respective owners and are **not
redistributed by this project** — they are fetched from the upstream repository
at ingest time. The assistant surfaces short attributed excerpts and links back
to the original episodes.
