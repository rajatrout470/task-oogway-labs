# Design — The Lenny Growth Assistant

UI/UX decisions and the reasoning behind them.

---

## 1. The design problem

Most AI chat interfaces optimise for one feeling: *fluency*. Text appears fast,
reads well, and looks authoritative. That is exactly wrong for this product.

Our user is a PM or founder who needs an answer they can **bring to a leadership
review**. Fluency without provenance is worse than useless to them — it is a
liability, because they cannot say where it came from. The interface therefore
has to make a different promise:

> **Every claim here is traceable, and when it isn't, we'll say so.**

Three principles follow.

### Principle 1 — Evidence is the hero, not a footnote

Citations are not superscripts hidden at the end. They are inline, clickable,
visually distinct chips that jump to a real excerpt with a link to the exact
second of the source video. The source count is visible **before** the panel is
expanded, because "6 sources from 5 episodes" is itself the trust signal.

### Principle 2 — Refusal is a designed state, not an error

An assistant that says "I don't know" is doing its most valuable work. So the
insufficient-evidence state gets real design attention: a distinct visual
treatment (warning-toned, not error-red), an explanation of what was searched,
and — most importantly — **the topics the corpus does cover**, so a dead end
becomes navigation.

### Principle 3 — Latency must be legible

A local 7B takes ~11 seconds to produce a first token. Hiding that behind a
generic spinner makes the product feel broken. Instead the wait is filled with
real information: a retrieval status line, then the actual sources at ~0.8s —
seven seconds before any prose. The user spends the wait reading which
operators are about to be quoted.

---

## 2. Information architecture

```
┌──────────────┬────────────────────────────┬──────────────────────────┐
│              │  ┌──────────────────────┐  │  ┌────────────────────┐  │
│  SIDEBAR     │  │  Session title    ⇥  │  │  │ Title  ⧉ ↓ Save ✕ │  │
│              │  └──────────────────────┘  │  ├────────────────────┤  │
│  ＋ New chat │                            │  │ Preview │ Markdown │  │
│              │   [degraded banner]        │  ├────────────────────┤  │
│  Recent      │                            │  │                    │  │
│  · Session A │   You                      │  │  Serif long-form   │  │
│  · Session B │   How do you know if…      │  │  reading column    │  │
│  · Session C │                            │  │                    │  │
│              │   Assistant · qwen · 11s   │  │                    │  │
│              │   To determine PMF… [E1]   │  │                    │  │
│              │   …spectrum [E3, E4]       │  │                    │  │
│              │                            │  │                    │  │
│              │   ┌──────────────────────┐ │  ├────────────────────┤  │
│              │   │ 6 sources · 5 eps  › │ │  │ 🔒 sandboxed       │  │
│              │   └──────────────────────┘ │  └────────────────────┘  │
│  ───────────  │                            │                          │
│  ● qwen2.5   │  ┌──────────────────────┐  │                          │
│  ● 303 eps   │  │ [Write essay] [Doc]  │  │                          │
│  Running     │  │ Ask about product…  ↑│  │                          │
│  locally     │  └──────────────────────┘  │                          │
└──────────────┴────────────────────────────┴──────────────────────────┘
   navigation          conversation                 artifact
   + system state      (the work)                   (the output)
```

Three zones, each answering a distinct question:

| Zone | Question it answers | Why it's placed there |
|---|---|---|
| **Sidebar** | "What was I working on, and is the system healthy?" | Persistent, low-attention. System status lives at the bottom — reassuring when green, findable when not. |
| **Chat** | "What's the answer, and can I trust it?" | Centre, widest attention. |
| **Artifact** | "What am I going to publish?" | Right, appears only when there is one. |

The artifact panel is **not a modal**. An essay is generated *from* a
conversation and often revised through it ("make it shorter"), so both must be
visible and usable simultaneously. A modal would force a context switch on every
revision.

### Reading measures

The chat column caps at **760px** and the artifact prose at **720px**. Both are
around 65–75 characters per line — the range where the eye reliably finds the
next line. On a wide monitor, full-width text is genuinely harder to read.

---

## 3. Typography

| Context | Family | Reasoning |
|---|---|---|
| Interface | System UI stack | Zero load cost, native feel, excellent hinting. |
| Chat body | System UI, 15px/1.6 | Conversational register. |
| **Artifact prose** | **Serif, 16.5px/1.72** | A deliberate signal. The moment content becomes something you'd *publish*, it should stop looking like an app and start looking like a document. The switch to serif does more work than any label could. |
| Citations, timestamps, model names | Monospace, small | Machine-generated facts, visually separated from prose. |

---

## 4. Colour

A warm off-white ground (`#fbfaf9`) rather than pure white — this is a reading
tool, and the reduced contrast against text is easier over long sessions.

The accent is a **deep teal** (`#0f6b62`), chosen so it cannot be confused with
either status colour:

| Role | Colour | Used for |
|---|---|---|
| Accent | teal | Citations, links, primary actions |
| Success | green | Provider healthy, corpus ready |
| Warning | amber | **Degraded** — working, but not as configured |
| Danger | red | Errors, destructive actions |

The amber "degraded" state is deliberately distinct from red. Running on a
fallback provider is not an error — the app works — but it is not what the
operator configured, and collapsing that into either "fine" or "broken" would
hide the most common real-world condition.

Every colour is a CSS custom property defined once on `:root`, with a dark
variant under `prefers-color-scheme`. Dark mode is a token swap, not a parallel
stylesheet, so the two cannot drift.

---

## 5. Interaction states

Every state below is implemented, not aspirational.

### Empty (new chat)

Not a blank page. A direct question — *"What are you working through?"* — a
one-line explanation of the grounding promise with the **live episode count**
(read from the API, not hardcoded), and four suggestion cards.

The suggestions are chosen to demonstrate the product's range, each with a hint
describing what will happen:

| Suggestion | Hint | Demonstrates |
|---|---|---|
| "How do you know if you've found PMF?" | Grounded answer with citations | Core loop |
| "What separates great PMs from good ones?" | Synthesises across operators | Cross-episode synthesis |
| "Write an essay about early-stage growth channels" | Ship 30 style, ~1,250 words | Essay skill |
| "Make a one-page checklist for user interviews" | Generates a document artifact | Artifact skill |

### Loading and streaming

Three distinct phases, because they mean different things:

1. **Retrieving** (~0.15s) — spinner + "Searching 303 episode transcripts…"
2. **Sources arrive** (~0.8s) — the source panel appears with real citations,
   *before any prose*. This is the phase that makes local latency tolerable.
3. **Generating** (~11s to first token) — tokens stream with a blinking caret at
   the edge.

The caret is a deliberate choice over a second spinner: it sits at the point of
change, so the eye is already where new text appears.

### Insufficient evidence

Amber left border and tinted background — visually distinct from both a normal
answer and an error. Content structure:

1. A direct statement: **"I don't have grounded evidence for this one."**
2. What was searched and why it fell short.
3. Why we're refusing (the corpus-bounded promise, stated plainly).
4. **The topics the corpus does cover**, as a concrete next step.

Point 4 is what turns a refusal into navigation rather than a dead end.

### Errors

Every error renders its `message` **and** its `remediation` — the API is built
to always supply one, because these failures are usually operational (Ollama not
running, model not pulled, corpus not ingested). Commands render as inline code.

The composer disables on a fatal boot error rather than letting the user type
into something that cannot respond.

### Degraded

A banner, not a toast — it persists because the condition persists. Two cases:

- **Fallback active:** names the configured provider, the effective one, and
  warns that quality may differ.
- **Corpus empty:** names the exact command (`make ingest`) and explains that
  the assistant has nothing to ground answers in.

A per-message model label also appears on every assistant turn, so an answer
produced during a fallback stays identifiable in scrollback.

### Citation interaction

Clicking a chip expands the source panel, scrolls the matching source into view,
and flashes it. Chips are keyboard-accessible (`role="button"`, `tabIndex=0`,
Enter/Space), and the flash animation is disabled under
`prefers-reduced-motion`.

Chips are rendered by string replacement **after** sanitisation, from a
*validated* label — no model-produced text is ever interpolated into HTML.
Clicks are handled by event delegation on the container, since React does not
own those injected nodes.

---

## 6. Responsive behaviour

| Breakpoint | Layout |
|---|---|
| **> 1100px** | Three columns: sidebar, chat, artifact side by side. |
| **760–1100px** | Sidebar + one main column. **The artifact replaces the chat** rather than splitting it. |
| **< 760px** | Single column. Sidebar becomes an overlay drawer with a scrim. |

The 1100px rule is the considered one. Splitting a 900px viewport gives two
~400px columns — below the readable measure for both. One good column beats two
cramped ones, so the artifact takes over and a header control returns to chat.

Wide content (tables, code) scrolls inside its own `overflow-x: auto` container.
The page body never scrolls horizontally.

---

## 7. Accessibility

Implemented, not aspirational:

- **Semantics:** `<nav>`, `<main>`, `<section>`, `<header>`; headings in order.
- **Keyboard:** every interactive element reachable and operable. Citation chips
  respond to Enter/Space. Escape closes the artifact panel. `Enter` sends,
  `Shift+Enter` newlines — the convention users already expect.
- **Focus:** `:focus-visible` only, so keyboard users get clear rings and mouse
  users don't get stray ones. The delete button on a session row reveals on
  focus as well as hover — otherwise it is keyboard-invisible.
- **Screen readers:** `aria-current` on the active session; `aria-expanded` on
  the source toggle; `aria-pressed` on skill toggles; `role="status"` on
  degraded banners and `role="alert"` on errors; a visually-hidden label on the
  composer; `aria-label` on icon-only buttons.
- **Motion:** `prefers-reduced-motion` disables the citation flash, the spinner
  animation, chip hover translation, and smooth scrolling.
- **Colour independence:** status is never colour alone — the dot is always
  paired with text ("Running locally", "corpus not ingested", the model name).
- **Contrast:** body text and muted text both meet WCAG AA on their backgrounds
  in light and dark themes.

### Known gaps

Stated rather than glossed: streaming text is not announced via an ARIA live
region (a token-by-token live region is unusably noisy; the right fix is
announcing the completed message, which is not yet implemented). The artifact
iframe's content is outside our styling control for high-contrast modes.

---

## 8. Component inventory

| Component | Responsibility |
|---|---|
| `App` | Session state, streaming lifecycle, artifact selection |
| `Sidebar` | Session list, new chat, provider/corpus status |
| `Message` | One turn; Markdown + citation chips; per-message model label |
| `Sources` | Collapsible evidence panel with deep links |
| `ArtifactViewer` | Preview/source tabs, sandboxed rendering, copy/download/save |
| `Composer` | Input, skill overrides, send/stop |

State lives in `App` with hooks rather than a store — it is genuinely local to
this screen, and a store would add indirection without removing complexity.

The stream reducer (`reduceEvent`) is a **pure function**, so the streaming
lifecycle is testable in isolation and the component body stays free of a long
event switch.

---

## 9. Trust-affecting details

Small decisions that exist specifically to keep the product honest:

- **A missing timestamp is shown as "(episode)"**, not a fabricated `0:00`. One
  source transcript genuinely has no timestamps; the UI degrades to an
  episode-level link and says so.
- **Only *cited* sources are listed**, not everything retrieved. Showing all six
  retrieved passages when the answer used three would overstate its grounding.
- **A stripped citation updates the rendered text mid-stream** (the `correction`
  event), so a fabricated reference never survives on screen.
- **"No sources were cited"** appears when an answer somehow arrives ungrounded —
  visible rather than quietly indistinguishable from a cited one.
- **`persisted: false`** is surfaced when the database write fails, so a user
  knows a turn won't survive reload.
- **The security note under every artifact** states plainly what is enforced —
  scripts blocked, no network access, and whether unsafe markup was removed.

---

## 10. What was deliberately not built

| Not built | Why |
|---|---|
| Dark-mode toggle | The OS preference is honoured. An in-app toggle adds state and a control for a preference the user has already expressed system-wide. |
| Message editing / regeneration | Real scope (branching history), no stated need. |
| Rich artifact WYSIWYG | A source textarea plus live preview is honest about what the artifact *is*. A WYSIWYG would imply an editing model we don't have. |
| Multi-artifact tabs | The most recent artifact opens automatically; older ones are reachable via the API. Tabs would be premature at typical session sizes. |
| Optimistic streaming into the message list | The pending turn is rendered separately so a mid-stream error can replace it cleanly instead of leaving a half-message stranded in history. |
| Toast notifications | Every condition worth reporting is tied to a specific place in the UI — an inline error, a persistent banner, a status dot. Toasts would disappear before a user finishes reading. |
