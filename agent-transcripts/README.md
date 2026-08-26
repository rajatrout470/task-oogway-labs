# Agent transcripts

A record of building this system with Claude Code, including the failed attempts
and how they were corrected.

Secrets are stripped throughout. No API keys were used — the entire build and
all verification ran against local Ollama and local PostgreSQL.

| File | Contents |
|---|---|
| [`build-log.md`](build-log.md) | Phase-by-phase narrative of what was built and what broke |
| [`failures-and-fixes.md`](failures-and-fixes.md) | The seven bugs found during development, with root causes |

## How this was built

Nine phases, run largely end-to-end after the Phase 0 discovery brief was
approved:

| Phase | Output |
|---|---|
| 0 | Discovery brief → `PRD.md` §1–8 |
| 1 | Repo scaffold, Compose, schema, config, logging, error contract |
| 2 | Corpus sync, three-format parser, chunker, embeddings, ingest CLI |
| 3 | Provider abstraction (Ollama + Anthropic), registry with fallback |
| 4 | Ship 30 skill with principles encoded in a versioned Markdown file |
| 5 | Artifact generation, HTML sanitiser, sandboxed viewer |
| 6 | React frontend, streaming UI, artifact panel, accessibility pass |
| 7 | 141 tests, calibration script, failure-mode handling |
| 8 | README, architecture.md, design.md, demo script, manual test plan |
| 9 | Self-check against the evaluation criteria |

## The working method that mattered

**Verify against reality before building on an assumption.** Three of the most
consequential decisions in this codebase came from measurement, not design:

1. Cloning the corpus *before* writing the PRD revealed that every line carries
   a timestamp — which is what made second-accurate citation the centrepiece of
   the product rather than a nice-to-have.

2. Running the parser across all 303 episodes revealed **three** transcript
   formats, not one. The first implementation silently dropped two episodes.

3. Measuring similarity distributions revealed that the abstention threshold
   could not work at all as originally built — the in-corpus and out-of-corpus
   score ranges *overlapped*. That led to finding a missing embedding-model
   requirement (task prefixes) that no amount of threshold tuning would have
   fixed.

Each of these would have shipped as a plausible-looking, quietly broken system
if the work had stopped at "the code runs".
