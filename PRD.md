# PRD — The Lenny Growth Assistant

**Status:** Phase 0 (Discovery) — sections 1–8 drafted for review. Sections 9–10 (detailed
implementation plan, final acceptance sign-off) are finalized in Phase 8.
**Author:** Rajat (with Claude Code as engineering partner)
**Last updated:** 2026-08-23

---

## 0. Corpus reconnaissance (what we actually verified, not assumed)

Everything below is grounded in a real clone of
[`ChatPRD/lennys-podcast-transcripts`](https://github.com/ChatPRD/lennys-podcast-transcripts),
inspected before a line of product thinking was written.

| Fact | Value | Why it matters |
|---|---|---|
| Episodes | 303 | Small enough to index exhaustively; too many to browse |
| Corpus size | ~25 MB, ~4.64M words ≈ **~6.2M tokens** | ~6× larger than the largest available context window |
| Transcript size | median ~82 KB ≈ **~20k tokens**; max ~155 KB | One episode fits in context comfortably; the corpus does not |
| Frontmatter | `guest, title, youtube_url, video_id, publish_date, description, duration_seconds, duration, view_count, channel, keywords[]` | Rich, clean, machine-readable metadata for free |
| Body format | `Speaker Name (HH:MM:SS):` blocks, with bare `(HH:MM:SS):` continuations | **Every passage carries a timestamp** |
| Topic index | 89 curated topic files under `index/` | A pre-built human taxonomy usable for hybrid retrieval |

**The single most important finding:** every line of every transcript is timestamped, and every
episode has a `youtube_url`. That means a citation is not a vague "Sean Ellis said something like
this" — it can be a **deep link to the exact second in the source video**
(`{youtube_url}&t={seconds}s`). Verifiability is therefore not a nice-to-have we bolt on; it is a
property the data hands us, and the whole product is designed around exploiting it.

---

## 1. Primary user & the problem

### Who
**The operator-writer.** A PM, growth lead, or early-stage founder — 2–10 years in — who is
simultaneously (a) accountable for product decisions they can't fully justify from first
principles, and (b) under quiet pressure to publish and build a public reputation in their field.

They already listen to Lenny's Podcast. That's the point: they are not looking for a new
information source, they are looking for **access** to one they already trust.

### The problem
Lenny's Podcast is ~500 hours of the best operator tacit knowledge in tech, and it is
**functionally unsearchable at the moment of decision**.

- YouTube search is title-level. It finds *episodes*, not *claims*.
- The knowledge our user needs is one paragraph 47 minutes into an episode they never clicked on,
  because the title was about something else.
- Asking ChatGPT gets a confident, fluent, unattributable answer that blends the podcast with
  Reddit, blogspam, and invention — which is exactly the answer you cannot bring to a leadership
  review, because you cannot say where it came from.

So the user does the expensive thing: they either spend an afternoon scrubbing transcripts, or
they wing it and cite "best practice."

### Why the essay skill belongs in the same product (and isn't a bolt-on)
Research and publishing draw on the *same corpus* and differ only in output shape. The operator
who just built a defensible point of view on activation metrics is precisely the person who should
publish it — and the reason they don't is that the blank page is expensive and generic AI drafts
are embarrassing to sign your name to. Putting the essay skill next to the answer converts
*"I now understand this"* into *"I have a POV I can put my name on"* in one move, with the
citations already attached. That's a single continuous job, not two features.

### Anti-goal (stated deliberately)
This is **not a general-purpose PM chatbot**. It is a corpus-bounded research instrument.
**Declining to answer is a feature, not a failure.** An assistant that answers "what's the best CI
tool?" from parametric knowledge has destroyed the only thing that makes it trustworthy. Every
design decision below follows from that.

---

## 2. Jobs to be done

**JTBD-1 (primary).** *When I'm facing a live product or growth decision, help me get to what
operators who have actually done it said — with receipts I can verify and quote — in two minutes
instead of an afternoon.*

**JTBD-2 (secondary, same corpus).** *When I want to publish a credible point of view, help me
turn what I just learned into an essay that sounds like me and cites real practitioners, rather
than generic AI filler.*

**JTBD-3 (enabling).** *When the corpus genuinely doesn't cover my question, tell me that plainly
and immediately, so I stop trusting the tool exactly when I should.*

---

## 3. Success metrics

### North Star — **Cited Answer Accuracy (CAA) ≥ 95%**
The share of factual claims in an answer that are actually supported by the transcript passage
they cite.

*How it's measured:* a fixed 30-question golden set spanning the corpus. Each answer is decomposed
into claims; each claim is checked against its cited chunk by an LLM judge, with a ~20% human spot
check to keep the judge honest. Run in CI against the local Ollama path (the mandated demo path),
and separately against the cloud path.

*Why this metric:* it is the one number that, if it drops, the product is worthless regardless of
how good everything else is. Latency, UI polish, and essay quality are all recoverable failures.
A confident wrong citation is not.

### Guardrail metrics

| Metric | Target | Rationale |
|---|---|---|
| **Abstention correctness** | ≥ 90% on a 15-question out-of-corpus set | Directly measures the anti-goal. Questions like "best CI/CD tool?" must return explicit insufficient-evidence, not an answer. |
| **Time to first *evidence*** (local Ollama) | < 2.0s p50 | **Measured: 0.80s.** The responsiveness metric that actually matters — see the revision note below. |
| **Time to first token** (local Ollama) | < 12s p50 | **Measured: 10.9s.** Revised from an original 2s target that was not physically achievable — see below. |
| **Full grounded answer** (local Ollama) | < 25s p50 | **Measured: 20.9s.** |
| **Retrieval recall@k** | ≥ 90% on the golden set | If the right passage never reaches the model, no amount of prompting saves the answer. |

#### Revision note: the original 2s TTFT target was wrong

Phase 0 set "time to first token < 2s" before anything had been measured. Once
the stack was running, profiling showed why that is unreachable on the mandated
local path:

| Prompt size | TTFT (warm model) |
|---|---|
| ~50 tokens | 0.57s |
| ~3,500 tokens (our evidence set) | 18.1s |
| ~7,000 tokens | 21.0s |

Time to first token on a 7B model is dominated by **prompt evaluation** — the
model must read the whole evidence block before emitting one word, at roughly
190 tokens/sec prefill on an M-series laptop. A grounded answer *requires* a
multi-thousand-token evidence prompt. The two constraints are in direct
conflict, and no amount of engineering removes that: 2s TTFT and real grounding
cannot coexist on this hardware.

What we did instead, in order of impact:

1. **Changed the metric that matters.** The user does not need *prose* in 2
   seconds; they need to know the system is working and what it found.
   Retrieved sources now stream to the UI at **0.80s**, well before generation
   begins. Perceived responsiveness is solved by showing evidence early, not by
   pretending prefill is free.
2. **Cut the prompt.** Evidence passages trimmed to ~300 tokens each on clean
   sentence boundaries, and `RETRIEVAL_TOP_K` reduced 8 → 6. TTFT 18.1s → 10.9s
   with no observed loss of citation quality.
3. **Kept the model resident** (`keep_alive: 30m`). A cold load costs 27s before
   any token; Ollama's 5-minute default would make every post-reading-pause
   question pay it again.

The honest summary for the demo: **a cited answer takes ~11 seconds to start
and ~21 to finish on a local 7B, and sources appear in under a second.** The
cloud path is substantially faster. This is the real trade-off of the
local-first requirement, and it is better stated plainly than hidden.

### Deliberately *not* a metric
Session length, message count, or "engagement." A user who gets a cited answer in one turn and
leaves is a **success**. Optimizing for time-on-app would corrupt the product.

---

## 4. Assumptions (the brief is incomplete — these are the gaps we filled)

Each assumption states the call, the reasoning, and what would change if it's wrong.

| # | Assumption | Reasoning | If wrong |
|---|---|---|---|
| A1 | **RAG, not full-corpus long-context.** | The corpus is ~6.2M tokens — roughly 6× the largest context window that exists, so full-corpus long-context is not expensive, it is *impossible*. Decisive second reason: the mandated demo path is a 7–8B local model whose usable context is a small fraction of one episode. RAG is not a preference here; it is the only design that satisfies the brief's own constraints. See §7 for the hybrid refinement. | n/a — this one is forced by arithmetic. |
| A2 | **Hybrid retrieval, with per-episode long-context as a second stage.** | One episode (~20k tokens) *does* fit in a cloud model's context. So: retrieve passages to find the right episodes, then optionally pull a full episode when the question needs within-episode reasoning ("what was X's whole argument?"). Best of both, and it degrades cleanly to pure RAG on the local path. | Drop stage two; pure RAG still meets CAA. |
| A3 | **No authentication; anonymous durable identity.** | Auth is table stakes and not what this engagement is graded on. A browser-issued `user_id` (UUID in localStorage) is persisted with every session, satisfying "user metadata" and giving us the exact seam where real auth plugs in later — documented in `architecture.md`. | Add an auth provider behind the existing `user_id` column; no schema change. |
| A4 | **The corpus is a pinned snapshot, refreshed on demand.** | Ingestion pins the source repo's commit SHA and records it on every chunk. Refresh is an idempotent re-ingest (new/changed episodes only), runnable by CLI or cron. Real-time sync is out of scope for a weekly-cadence podcast. | Add a scheduled job; the ingest is already idempotent. |
| A5 | **Transcripts are fetched at build/ingest time, never vendored into this repo.** | The transcripts are third-party copyrighted material. We clone the public source repo during ingestion rather than committing a copy, the UI surfaces short attributed excerpts with a deep link back to the original video, and we never expose a full transcript for download. This is both the legally careful posture and the *better product* — the citation drives traffic back to the source. | If redistribution were ever licensed, vendoring becomes a caching optimization, not a requirement. |
| A6 | **Demo hardware = Apple Silicon laptop, 16 GB RAM.** Default local model in the 7–8B class, quantized. | The brief mandates "runs comfortably on a normal dev laptop." Verified available: Ollama 0.32.14, currently only `llama3.2:3b` pulled. Recommending `qwen2.5:7b-instruct` (strong instruction-following and refusal behavior) with `llama3.1:8b` as the documented alternative and `llama3.2:3b` as the low-RAM fallback. | Config-only change — model identity never appears in application code (§6). |
| A7 | **Embeddings run locally too** (`nomic-embed-text` via Ollama), stored in **pgvector**. | A demo that requires a cloud embedding key to *ingest* isn't really a local demo. Keeping vectors in Postgres means one datastore, one backup story, one connection pool — and metadata filtering and vector search in a single SQL query. | Swap the embedder behind the same interface; a cloud embedder is a config flip and a re-ingest. |
| A8 | **"Ship 30 for 30–style" = the publicly documented methodology**, encoded as principles. | We encode the writing *principles* from the public guide (hook discipline, one idea per essay, skimmable structure, specific takeaway) as an inspectable, testable skill — not the branded curriculum, and not a vibes-based prompt string. | Principles are versioned in one file; tune without touching the agent. |
| A9 | **Single-digit concurrent users.** | It's a demo and a take-home. No queue tier, no worker fleet — but no architecture that *forbids* one either. | Add a task queue; the agent layer is already async. |
| A10 | **English-only, text-only.** | The corpus is English; audio playback is the source platform's job. | Out of scope, stated in §5. |
| A11 | **Artifacts are session-scoped and persisted, not published.** | An artifact belongs to the conversation that made it; export is copy/download, not a public URL. Avoids inventing a publishing surface nobody asked for. | Add a share table + route. |

### Two open items for you to confirm (not blocking — defaults chosen)
1. **Is an `ANTHROPIC_API_KEY` available for the demo?** None is set in this environment. Default:
   we build and test both paths, but the recorded demo runs **fully local on Ollama** (which the
   brief mandates anyway), and the cloud path is exercised in tests via a recorded/mocked
   transport so it's verifiably wired even without a key.
2. **Model pull.** `qwen2.5:7b-instruct` is a ~4.7 GB download. Say the word if you'd rather stay
   on the already-present `llama3.2:3b` — it works, it's just measurably weaker at abstention,
   which is our #2 metric.

---

## 5. Scope

### In scope
- Grounded conversational Q&A over all 303 transcripts, with per-claim citations that deep-link to
  the exact timestamp in the source video.
- Explicit, well-designed **insufficient-evidence** behavior.
- Multi-session chat with independent context per session, persisted in Postgres.
- The **Ship 30 for 30 essay skill** as a first-class, inspectable agent skill.
- **Markdown + HTML/CSS artifact generation**, rendered in an in-app Artifact Viewer with a real
  sandboxing strategy.
- **Provider/model abstraction** — cloud (Anthropic) and local (Ollama) — switchable by config
  alone, surfaced in the UI, with documented and implemented fallback.
- Idempotent ingestion pipeline with source tracing and corpus-version stamping.
- One-command Docker Compose startup, structured logging, graceful degradation on every external
  dependency.
- Automated tests (API, retrieval, routing, persistence, sanitization) + a manual UI test plan.
- Full doc set: README, PRD, design.md, architecture.md, agent transcripts, demo script.

### Out of scope (and why)
| Excluded | Why |
|---|---|
| Auth, accounts, RBAC, multi-tenancy | Table stakes, well-understood, not what's being evaluated. The seam is documented. |
| Audio/video playback in-app | The deep link hands off to YouTube, which does it better and keeps attribution intact. |
| Corpus editing / user-uploaded transcripts | Changes the product from "trusted corpus" to "document tool" — a different thesis. |
| A dedicated vector database | pgvector at 303-episode scale is comfortably sufficient. Adding Pinecone/Weaviate would be resume-driven architecture. |
| Fine-tuning or continued pre-training | Grounding is a retrieval problem here, not a weights problem. |
| Cross-session agent memory / user profiles | Real privacy surface, real scope, no stated need. |
| Native mobile | Responsive web covers the demo and the realistic usage. |
| Per-user cost metering & rate limits | Needed at multi-tenant scale; the hooks live in the logging layer. |
| i18n | Corpus is English. |

---

## 6. Key flows

**F1 — Grounded question (the core loop).**
New session → user asks → retrieval over pgvector + metadata filters → evidence set assembled with
stable IDs → agent synthesizes *only* over that evidence → citation validator strips any
unsupported reference → answer streams in with inline citation chips → clicking a chip opens the
passage and a deep link to that second on YouTube.

**F2 — Follow-up.** Session context carries; the follow-up is resolved against conversation history
*and* re-retrieved, so "what did she say about the second one?" works without hallucinating.

**F3 — Insufficient evidence.** Retrieval returns nothing above the relevance floor → the agent
never sees an empty-evidence prompt it might paper over. The app returns a designed empty state:
what was searched, why it fell short, and the nearest adjacent topics that *are* covered.

**F4 — Essay generation.** "Turn this into an essay" → the Ship 30 skill runs over the existing
grounded answer plus its evidence set → ~1,250-word essay streams into the Artifact Viewer → the
user edits, copies, or downloads it. Claims stay traceable to transcripts.

**F5 — Artifact rendering.** Generated Markdown or HTML/CSS renders in the side panel — Markdown
through a safe renderer, HTML inside a sandboxed iframe with a restrictive CSP and no script
execution. Documented allow/block list in `architecture.md`.

**F6 — Model switch.** The active provider/model is always visible. Changing it is a config
concern; if the cloud key is missing or the provider errors, the app falls back to Ollama and
**says so in the UI** rather than failing silently or pretending nothing changed.

---

## 7. Retrieval design (the justification the brief asked for)

**Decision: hybrid retrieval over pgvector, with optional per-episode long-context escalation.**

The reasoning is arithmetic, not taste:

1. **Full-corpus long-context is impossible.** ~6.2M tokens vs. a ~1M-token ceiling at the very
   best. Not "costly" — impossible.
2. **The mandated demo path settles it.** A 7–8B local model has a small usable context. Any design
   that depends on stuffing lots of tokens in fails on exactly the path the brief calls mandatory.
3. **But one episode does fit** (~20k tokens median). So questions of the form "walk me through
   X's whole argument" get a second-stage tool that loads a full episode — available on the cloud
   path, degrading cleanly to top-k passages locally.

**Chunking follows the data's own structure.** The transcripts are already segmented into
timestamped speaker turns, so we chunk on turn boundaries with overlap rather than blind
fixed-width splits — every chunk therefore inherits a real speaker and a real timestamp, which is
what makes second-accurate citation possible at all. Metadata (guest, episode, date, topic
keywords, source commit SHA) rides on every chunk, enabling filtered search ("only 2024+", "only
Sean Ellis") and full source tracing.

**Grounding is enforced in code, not requested in a prompt.** Evidence arrives at the model
pre-numbered; a post-generation validator checks every citation marker against the real evidence
IDs and rejects invented ones. This matters most on the local path: it means the 7B model's job is
narrowed to synthesis over a small pre-filtered set, and its most likely failure mode —
fabricating a source — is caught by deterministic code rather than trusted to the model.

---

## 8. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Hallucination / fabricated citations** | Critical | Evidence-only synthesis; deterministic citation validator; abstention path with a relevance floor; CAA measured in CI. |
| **Local-model quality** — a 7B is meaningfully worse at synthesis *and at refusing* | High | Narrow its job (retrieval/ranking/validation are code, not model); structured output; strict evidence framing; abstention measured separately per provider so we never ship a local path that quietly hallucinates. |
| **Agent SDK ↔ Ollama impedance mismatch** — the Claude Agent SDK is built around Anthropic models; Ollama speaks OpenAI-compatible | High | Two viable designs: (a) an Anthropic-compatible shim in front of Ollama so one agent layer serves both, or (b) one provider interface with two transports. Feasibility is verified in Phase 3 before committing; this is the engagement's headline trade-off and the one to explain on the demo video. |
| **Latency** on local inference | Medium | Stream everything; cap evidence-set size; TTFT budget as a guardrail metric; visible streaming states so waiting is legible. |
| **Cost** on the cloud path | Medium | RAG keeps per-query tokens ~2 orders of magnitude below long-context; cloud is opt-in and not the demo default. |
| **Data leakage** — transcripts and user questions leaving the machine | Medium | The default path is fully local: no text leaves the laptop. Cloud usage is explicit, visible in the UI, and documented. |
| **Prompt injection from corpus content** — a transcript could contain instruction-shaped text | Medium | Evidence is delimited and labeled as untrusted data; the agent's instructions are structurally separated from retrieved text; artifact HTML is sandboxed regardless of origin. |
| **Unsafe artifact rendering** | High | Untrusted-by-default: sandboxed iframe, restrictive CSP, no script execution, sanitization before render. Allow/block list documented and unit-tested with real XSS payloads. |
| **Corpus licensing** | Medium | Never vendored, never redistributed whole; short attributed excerpts with deep links back to the source (A5). |
| **Toolchain sharpness** — local Python is 3.14, very new; some deps lack wheels | Low | Containers pin a stable Python (3.12); the local path is documented but not the supported one. |
| **Scope risk** — 9 phases, one engagement | Medium | Phase gates with review; grounding quality and operability are protected first if anything gets cut. |

---

## 9. Acceptance criteria

- [ ] `docker compose up` brings the entire stack up on a clean machine with **no cloud API key**.
- [ ] Ingestion loads 303 episodes, records the source commit SHA, and is safely re-runnable.
- [ ] Every grounded answer identifies its source episode(s) and deep-links to the timestamp.
- [ ] An out-of-corpus question produces an explicit insufficient-evidence response, not an answer.
- [ ] Sessions are independent and survive a full stack restart.
- [ ] The essay skill produces a ~1,250-word Ship 30–style essay whose claims trace to transcripts.
- [ ] The Artifact Viewer renders Markdown and HTML side-by-side with chat; a script-injection
      payload in generated HTML is provably inert (test-enforced).
- [ ] Switching provider/model requires **zero application-code edits** and is visible in the UI.
- [ ] Missing key, Ollama down, model timeout, empty retrieval, and DB down each degrade gracefully
      with a useful message — each covered by a test.
- [ ] A fresh engineer can clone, run, test, and extend using only the docs.

## 10. Implementation plan
Phases 1–9 per the engagement plan; expanded with sequencing and estimates in Phase 8.
