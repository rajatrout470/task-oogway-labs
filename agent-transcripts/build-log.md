# Build log

Phase-by-phase narrative. Verification commands and their real output are
included where they changed a decision.

---

## Phase 0 — Discovery

**Approach: measure before designing.** The corpus was cloned and inspected
before any product thinking was written down.

```
episodes: 303
total words: 4,641,791          (~6.2M tokens)
per-file: min 9KB  p50 82KB  p95 ~140KB  max 155KB
frontmatter: guest, title, youtube_url, video_id, publish_date,
             description, duration_seconds, duration, view_count,
             channel, keywords[]
body: "Speaker Name (HH:MM:SS):" blocks
index/: 89 curated topic files
```

**The finding that shaped the product.** Every line is timestamped and every
episode has a `youtube_url`. A citation could therefore be a deep link to the
*exact second* of the source video. Verifiability was handed to us by the data,
so the entire product was designed around exploiting it.

**The RAG-vs-long-context question answered itself.** ~6.2M tokens is roughly 6×
the largest context window available — full-corpus long-context isn't expensive,
it's impossible. And the mandated demo path is a 7–8B local model with a small
usable context. RAG was forced by arithmetic, not chosen by preference. One
episode (~20k tokens) *does* fit, so a per-episode escalation path was designed
in as a second stage.

**Environment reality check.** Docker daemon down, local Python 3.14 (too new for
reliable `asyncpg`/`pgvector` wheels), Ollama present with only `llama3.2:3b`.
Consequences: containers pin Python 3.12; a `uv`-managed 3.12 native path was
provided; local PostgreSQL 16 with pgvector 0.8.2 turned out to be already
installed, which allowed full end-to-end verification without waiting on Docker.

---

## Phase 1 — Scaffold

Schema, config, logging, error contract, Compose, Dockerfiles.

Two decisions that paid off later:

- **Config is the only place a model is named.** Everything above the provider
  registry asks for "the active provider". This is what makes "switch models
  without touching application code" structurally true rather than a claim.
- **Per-stage loggers** (`api | model | retrieval | db | ingest | agent |
  artifact`) with a request-id bound via contextvars. The brief asked to debug
  these *separately*; that word drove the design.

---

## Phase 2 — Knowledge base

Parser written, then immediately run against all 303 real transcripts rather
than a sample. That surfaced [failure #1](failures-and-fixes.md#1) — two
episodes silently producing zero turns, because the corpus contains **three**
distinct transcript layouts.

After the fix:

```
formats     : {'header_timestamp': 301, 'speaker_only': 1, 'inline_timestamp': 1}
total turns : 63,008
total chunks: 21,984
parse failures: []
chunk tokens: min 54  p50 422  p90 515  max 1340
```

First ingest attempt failed on all 12 episodes with a pgvector codec error
([failure #3](failures-and-fixes.md#3)). After the fix:

```
12 episodes ingested, 871 chunks, 0 failed, 47.7s
```

Then the full corpus:

```
291 ingested, 12 unchanged, 0 failed, 21,113 chunks, 1356s
```

The `12 unchanged` line confirms incremental re-ingest works — content hashing
skipped the already-loaded episodes.

---

## Phase 3 — Agent layer

**The engagement's central tension**, resolved here: the brief mandates both the
Claude Agent SDK *and* a local 7B default. Those want opposite routing
strategies. Rather than force one, providers **declare** capability
(`supports_native_tools`), and routing follows: model-driven tool selection for
Claude, deterministic rules for Ollama. Reasoning is in
[architecture.md §7](../architecture.md#7-agent-layer-and-routing).

Grounding was made structural, not prompted:
1. Retrieval decides sufficiency *before* the model is invoked.
2. Evidence arrives pre-numbered and delimited as untrusted data.
3. Citations are mechanically verified after generation; fabrications are
   stripped from the text.

---

## Phase 4 — Ship 30 skill

The published guide was read first, and the methodology encoded into a
**versioned Markdown file** (`ship30_principles.md`) that the skill loads at
runtime — so the craft rules are reviewable as a diff and tunable without
touching Python.

**A tension worth surfacing:** Ship 30's signature format is the ~250-word
*atomic essay*; the brief asks for ~1,250 words. Rather than silently pick one,
the resolution is documented at the top of the principles file — apply the
principles at long-form length by treating the piece as a spine of 4–6
self-contained units, so length comes from stacking complete thoughts rather
than inflating one.

---

## Phase 5 — Artifacts and sanitisation

The sanitiser was written and then immediately attacked with a payload suite,
which caught two real bypasses before they shipped (`@import` surviving CSS
filtering; `<meta>` retaining an attacker URL). 17/19 → **27/27**.

Design choice worth noting: HTML artifacts render from a **real URL**, not
`srcdoc`. `srcdoc` inherits the parent origin; a URL lets the *server* set CSP
as a response header, which model-generated markup cannot omit.

---

## Phase 6 — Frontend

Built around one idea from [design.md](../design.md): **evidence is the hero**.
Sources stream to the UI *before* prose, which is what makes ~11s local
generation feel informative rather than broken.

---

## Phase 7 — Tests and calibration

141 tests. First full run: **7 failures**, of which

- 3 were real code bugs (validation handler 500s, diversification semantics,
  pgvector on the query path),
- 2 were test-infrastructure (event-loop scope),
- 2 were **my tests being wrong** (a vacuous duplicate check, and two
  contradictory diversification assertions).

All documented in [failures-and-fixes.md](failures-and-fixes.md). Final: 141
passed.

The most consequential work in this phase was not a test but the
**calibration script**. Measuring in-corpus vs out-of-corpus similarity
distributions revealed they *overlapped* — meaning no abstention threshold could
work, and the cause was upstream (missing embedding task prefixes), not a tuning
problem. See [failure #2](failures-and-fixes.md#2).

---

## Phase 8 — Documentation

README, architecture.md, design.md, demo script, manual test plan, these
transcripts. Written to the standard of "a fresh engineer can clone, run, test
and extend using only the docs".

---

## Verification performed

Everything below was actually run, not assumed:

| Check | Result |
|---|---|
| Parser across all 303 real transcripts | 303/303, 3 formats, 0 failures |
| Full corpus ingest | 303 episodes, 21,984 chunks, 0 failures |
| Incremental re-ingest | Unchanged episodes skipped by content hash |
| Retrieval end-to-end | 6 sources, 5 distinct episodes, deep links correct |
| Abstention on out-of-corpus questions | Refuses; model never invoked |
| Threshold calibration | Clean separation confirmed after the prefix fix |
| XSS payload suite | 27/27 neutralised; legitimate documents intact |
| Test suite | 141 passed |
| Backend lint (ruff) | Clean |
| Frontend typecheck + build | Clean |
| Live grounded answer (qwen2.5:7b) | TTFT 10.9s, total 20.9s, evidence at 0.80s |

**Not verified:** the Anthropic cloud path against a live API (no key available
in this environment). It is implemented, its fallback and SDK-degradation
behaviour are unit-tested with a mocked transport, but it has not been exercised
against the real service. Stated plainly rather than implied.
