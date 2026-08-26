# Failures and fixes

Seven bugs found during development. Each is recorded with what broke, how it
was found, the root cause, and the fix — including the two that were my *tests*
being wrong rather than the code.

The pattern worth noting: **five of the seven were silent**. They produced
plausible-looking output while being wrong, which is the failure class that
matters most for a product whose entire value is trustworthiness.

---

## 1. Two episodes silently vanished from the knowledge base

**Severity:** high — silent data loss in a grounding product.

**Symptom.** Running the chunker across all 303 real transcripts, 301 parsed and
2 produced *zero turns*. The ingest would have reported success.

**How it was found.** Deliberately running the parser over the entire real
corpus and printing a per-episode summary, rather than testing against a
handful of files.

**Root cause.** The corpus is not homogeneous. It contains three distinct
transcript layouts:

| Layout | Count | Shape |
|---|---|---|
| Header timestamp | 301 | `Speaker Name (HH:MM:SS):` on its own line |
| Inline timestamp | 1 | `[HH:MM:SS] Speaker: text` on one line |
| Speaker only | 1 | `Speaker Name:` alone, **no timestamps anywhere** |

The parser handled only the first and returned an empty list for the others.
Nothing raised.

**Why it mattered.** The assistant would have confidently claimed the corpus
didn't cover a guest it actually did. For a product whose promise is "grounded
in these transcripts", quietly holding fewer transcripts than claimed is close
to the worst possible bug.

**Fix.**
- Per-file layout detection, trying parsers most-specific first.
- `parse_transcript()` now **raises `ParseError`** when no layout yields turns.
- `transcript_format` recorded per episode, so a future upstream change is
  diagnosable.
- The timestamp-less layout propagates `start_seconds = None` all the way to the
  citation, which then omits its `&t=` deep link rather than inventing `0:00`.
  This required making `chunks.start_seconds` nullable.

**Test:** `test_chunker.py::test_parse_transcript_raises_on_unknown_layout`,
plus one test per format.

---

## 2. The abstention gate could not work at all

**Severity:** critical — this is the product's central guarantee.

**Symptom.** An out-of-corpus question ("best CI/CD tool for Kubernetes?")
returned `sufficient=True` and got a confident answer.

**How it was found.** Not by the bug report above — by *measuring the
distributions*. Running 6 in-corpus and 6 out-of-corpus questions and comparing
top-1 similarity:

```
in-corpus      0.504 – 0.722
out-of-corpus  0.429 – 0.519
                     ^^^^^ overlaps the in-corpus minimum
```

The distributions **overlapped**. No threshold could separate them. Tuning the
number — the obvious response — would have been futile.

**Root cause.** `nomic-embed-text` is an *asymmetric* embedding model. It is
trained with task prefixes and expects `search_document: ` on indexed passages
and `search_query: ` on queries. Without them, every pair of English texts lands
in a narrow high-similarity band and the model's discriminative power collapses.

**Fix.** Task prefixes keyed by model family, with `kind="document"` /
`kind="query"` made **explicit** at both call sites so the asymmetry is visible
rather than inferred. Then a full re-embed of all 21,984 chunks.

Result:

```
              before          after
in-corpus     0.504–0.722     0.605–0.824
out-of-corpus 0.429–0.519     0.525–0.586
gap           −0.015 (overlap) +0.019 (clean)
```

**Follow-through.** Since the discovery process was more valuable than the fix,
it was made repeatable: `scripts/calibrate_threshold.py` (`make calibrate`)
measures the real distributions and reports whether a usable threshold exists.
It explicitly refuses to suggest a number when the distributions overlap, and
instead lists the upstream causes to check — because a threshold is not the
problem in that case.

The threshold was then set to 0.60 from measurement, and the config comment
records the measured ranges so the next person doesn't treat it as arbitrary.

---

## 3. pgvector codec conflict — two places, found separately

**Severity:** medium — loud failure, but it hit twice.

**Symptom.** Every episode failed to ingest:
`invalid input for query argument $8 ... (expected list or ndarray)`.
Then, after fixing that, the identical error on the *query* path.

**Root cause.** `pool.py` registers pgvector's asyncpg codec on every
connection, so asyncpg encodes Python lists into the `vector` wire format
itself. Passing a JSON *string* plus an explicit `::vector` cast fights that
codec and fails at bind time.

**Fix.** Pass plain `list[float]` and drop the casts, in both the ingest INSERT
and the `vector_search` query. The comment at each site now explains *why*
there's no cast, since the natural instinct is to add one back.

**Note.** The ingest reported all 12 failures loudly and refused to claim
success — the failure-isolation design worked exactly as intended.

---

## 4. Every custom-validated endpoint returned 500 instead of 422

**Severity:** high — a real production bug on the public API.

**Symptom.** `POST /api/sessions/{id}/messages` with a whitespace-only message
returned **500 Internal Server Error** instead of a 422 explaining the problem.

**How it was found.** An API contract test. It was not visible in manual use,
because the frontend disables the send button on empty input — so only a direct
API caller would ever hit it.

**Root cause.** My `validation_error_handler` passed `exc.errors()` straight to
`JSONResponse`. Pydantic v2 embeds the originating exception **object** in each
error's `ctx` (e.g. `{"error": ValueError(...)}`), which `json.dumps` cannot
encode. So the error handler *itself* raised `TypeError`, which fell through to
the catch-all handler and became a 500.

The irony: the handler written to produce clean, structured errors was the thing
producing opaque ones.

**Diagnosis note.** My first hypothesis — that the logging middleware was
re-raising past the exception handler — was wrong. A minimal four-way
reproduction (middleware × catch-all handler) returned 422 in every combination,
which ruled it out and forced a proper look at the real traceback. Worth
recording: the plausible theory cost more time than reading the stack would
have.

**Fix.** `_serializable_errors()` extracts only JSON-safe fields (type, field
path, message) and truncates the echoed input to 200 characters so a large body
isn't reflected back.

**Test:** `test_api.py::test_empty_message_is_rejected`.

---

## 5. Test-suite event-loop teardown

**Severity:** low — test infrastructure only.

**Symptom.** Three integration tests passed individually but failed together
with `ConnectionDoesNotExistError: connection was closed in the middle of
operation`.

**Root cause.** The asyncpg pool is a module-level singleton bound to the event
loop that created it. pytest-asyncio gave each test a fresh loop, so the second
test inherited a pool whose connections belonged to an already-closed loop. (In
production there is one loop for the process lifetime, so this is genuinely a
test-only concern.)

**Fix.** `asyncio_default_test_loop_scope = "session"` in `pyproject.toml`, and
removed a custom `event_loop` fixture that pytest-asyncio 1.x deprecates.

---

## 6. Diversification had contradictory semantics

**Severity:** medium — found by a test failure that exposed a real design gap.

**Symptom.** Two tests failed with opposite expectations. Investigating showed
they contradicted each other: one asserted no episode may supply more than 2 of
6 passages; the other asserted 6 passages are returned even when only one
episode is relevant. Both behaviours are desirable; the code and the tests had
never resolved which wins.

**Root cause.** `max_episodes` and the per-episode cap were both treated as soft,
and the backfill path ignored both — so an answer could silently draw from far
more episodes than configured.

**Fix.** Made the contract explicit and asymmetric:

- **`max_episodes` is hard** — an answer never draws on more episodes than this
  (citation legibility). Backfill draws only from episodes already selected.
- **The per-episode cap is soft** — backfill may exceed it to reach `top_k`,
  because starving the model of context to protect a diversity target the
  corpus cannot satisfy is the wrong trade.

Separately, the first-pass cap was tightened. It had allowed 4 of 8 passages
from one episode, and in a real query 4 of 8 came from a single guest — one
person's view presented as consensus, which is precisely what the product
promises not to do. Now 2 of 6, and a live query returns 5 distinct episodes
across 6 passages.

---

## 7. Two tests that were themselves wrong

Worth recording separately, because "the test failed" is not the same as "the
code is broken".

**7a. A vacuous duplicate-detection test.** `test_no_duplicate_trailing_chunk`
built synthetic turns whose text was *identical filler*. Overlapping chunks
therefore shared text simply because every turn was the same string — the test
could never pass and was measuring nothing. Fixed by seeding each turn with its
index.

**7b. The contradictory diversification test** described in §6 — fixed by
supplying multiple episodes, which is the scenario the assertion actually
describes.

---

## Two sanitiser bugs, caught before they shipped

Both found by running a payload suite against the sanitiser immediately after
writing it, rather than trusting the allowlist design to be correct.

**`@import` survived.** The CSS filter split declarations on `}`, but an at-rule
is terminated by `;`. So `@import url("//evil.com");p{color:blue}` partitioned on
the first `{`, leaving the `@import` inside what the filter treated as the next
rule's *selector*. Fixed by stripping at-rules by their own terminator **before**
declaration filtering, and by dropping any rule whose selector is unsafe.

**`<meta http-equiv="refresh">` leaked a URL.** `http-equiv` was correctly
stripped, but `content` was allowlisted — leaving
`<meta content="0;url=//evil.com">` in the output. Harmless without `http-equiv`,
but sloppy, and one allowlist edit away from being exploitable. Fixed by
allowing **only** `charset` on `<meta>`.

The payload suite went from 17/19 to 27/27, and is now `test_sanitize.py` with
26 parameterised cases plus a test that legitimate documents survive intact.

---

## The metric I set wrong

Not a bug, but the most instructive correction.

The Phase 0 PRD committed to **time to first token < 2s** before anything had
been measured. Profiling once the stack ran:

| Prompt size | TTFT (warm) |
|---|---|
| ~50 tokens | 0.57s |
| ~3,500 tokens (our real evidence prompt) | 18.1s |
| ~7,000 tokens | 21.0s |

TTFT on a 7B is dominated by **prompt evaluation** (~190 tok/s prefill here),
and a grounded answer requires a large evidence prompt. The two constraints are
in direct conflict; no engineering removes that.

What was done, in order of value:

1. **Changed the metric.** The user doesn't need *prose* in 2 seconds — they
   need to know it's working and what it found. Sources now stream at **0.80s**,
   ~10 seconds before prose. Time-to-first-*evidence* was the metric that
   mattered; the original one measured the wrong thing.
2. **Cut the prompt.** Passages trimmed to ~300 tokens on clean sentence
   boundaries (never mid-sentence — a truncated passage invites the model to
   finish the thought itself), `top_k` 8 → 6. TTFT 18.1s → 10.9s.
3. **Kept the model resident** (`keep_alive: 30m`), since a cold load costs 27s
   and Ollama's 5-minute default would charge that after every reading pause.

The PRD now carries the revision and the reasoning rather than the original
number.
