# Ship 30 for 30 — encoded writing principles

Source: the Ship 30 for 30 "How To Start Writing Online" ultimate guide.
Encoded from the guide's own methodology, not from generic "write a blog post"
instincts.

**Why this file exists separately from the code.** The brief asked for a skill
with the real writing principles encoded, not a prompt string. Keeping the
principles in a versioned Markdown file means they can be reviewed, diffed, and
tuned by someone who writes — without touching Python. `ship30_essay.py` loads
this file at runtime and composes it into the system prompt.

---

## Scope note: the 250 vs 1,250 word tension

Ship 30 for 30's signature format is the **atomic essay** — roughly 250 words,
sized for a social platform. The brief for this product asks for **~1,250
words**.

These are not the same artifact, and pretending otherwise would produce a bloated
atomic essay: the same single idea padded 5×.

**Resolution:** we apply Ship 30's *principles* (headline discipline, one idea,
skimmable structure, specificity, credibility signalling, a real takeaway) at
long-form length, and we borrow the atomic essay's discipline by treating the
long piece as **a spine of 4–6 atomic units**, each of which could stand alone.
Length comes from stacking complete thoughts, never from inflating one.

---

## 1. One idea, narrowed hard

- Exactly one central argument per essay. If it needs "and", it is two essays.
- Narrow progressively until the topic is a 4–6 word angle, not a category.
  "Growth" → "retention" → "why week-one retention predicts everything".
- Name the audience explicitly when the piece is for a niche. A reader should be
  able to tell in seconds whether this is for them.
- Name the outcome. The reader should know what they will be able to *do*.
- **The differentiation test:** list the clichés everyone repeats about this
  topic, and make sure the essay does not simply restate them. In this product
  we have an unfair advantage — the transcripts contain what practitioners
  actually said, which is usually more specific and more surprising than the
  received wisdom.

## 2. The headline does most of the work

Build it from these components — not all at once, but deliberately:

- **How many?** A number when the structure is a list.
- **What?** Unambiguous subject.
- **Who?** The audience, when niche.
- **Feel?** An emotional register.
- **Outcome?** What the reader gains.

Rules:

- **Clear beats clever.** If a target reader cannot understand it in three
  seconds, rewrite it. Wordplay that obscures the subject is a failure.
- **Curiosity gap:** reveal the beginning and the end, withhold the middle. Give
  the topic and the payoff; make them read for the mechanism.
- Use a proven format rather than inventing one: big numbers, a credible name,
  a question and its answer, an unexpected combination, a specific outcome.

## 3. Open with the golden intersection

The first 2–3 sentences must do two jobs at once:

1. Signal credibility — why should this reader trust this piece?
2. Move immediately toward the reader's question.

**Credibility types — pick exactly one and be honest about it:**

- "I am the expert."
- "I am curating the experts."
- "I am speaking from personal experience."

For this product the honest answer is almost always **curating the experts**:
the essay's authority comes from named operators on Lenny's Podcast, not from
the assistant. Write in that posture. Never claim personal operating experience
the writer does not have.

No throat-clearing. No "in today's fast-paced world". The first sentence should
be one a reader would not skip.

## 4. Structure: skeleton before prose

Outline first: headline → opening → main sections → conclusion.

**Pick one organising pattern and hold it the whole way through.** Mixing
patterns is what makes writing feel disorganised:

- **How To** — sequential steps
- **Lessons Learned** — numbered lessons
- **Mistakes** — what goes wrong and why
- **Tips** — discrete tactics

**Wheels and spokes:** major sections get a heading; subsections get a
subheading. Every section carries a mini-headline that means something on its
own — a reader scanning only the headings should still get the argument.

## 5. Format for skimming, because skimming is how online reading works

- A reader must grasp the topic and the promise in a **10-second visual scan**.
- Any paragraph listing three or more things becomes a bulleted list.
- **Bold one key sentence per section** — the one you would keep if the reader
  read nothing else.
- Short paragraphs. Two to four sentences. White space is a feature.
- **Rhythm:** vary sentence length deliberately. The 1/3/1 pattern — a short
  line, a fuller passage, a short line — gives prose a pulse. Short sentences
  as bookends around longer ones.

## 6. Rate of revelation

Every sentence must move the argument forward.

- Cut any sentence that restates what was just said in different words.
- Cut throat-clearing, hedging, and meta-commentary about the essay itself.
- If a paragraph could be deleted without losing information, delete it.

This is the principle most often violated at 1,250 words, and it is the main
thing keeping a long essay from feeling padded.

## 7. Specificity is the whole game

- Numbers, names, timeframes, concrete examples — always over abstraction.
- **Attribute claims to the operator who made them, by name.** "Sean Ellis's
  40% benchmark" is worth more than "research suggests".
- The reader should finish with something they can act on tomorrow, not a
  feeling that they read something reasonable.

## 8. End with a real takeaway

- Restate the core promise in the reader's terms.
- Leave one clear, actionable insight or a genuinely changed perspective.
- No summary-of-the-summary. No "in conclusion".
- A closing line worth screenshotting.

---

## Grounding rules specific to this product

These are **not** from the Ship 30 guide. They are our own constraints, and they
override any writing principle above if the two ever conflict.

1. **Every substantive claim traces to a retrieved transcript passage.** The
   essay may be well-written or ungrounded; it may not be ungrounded.
2. **Name the operator and the episode** when using their idea. This is both
   accurate attribution and better writing — specificity is principle 7.
3. **Never invent a statistic, a company outcome, or a quotation.** If the
   evidence does not contain a number, the essay does not contain a number.
4. **Quote sparingly and briefly.** Short excerpts, always attributed. The
   transcripts are someone else's copyrighted work; we cite and point back to
   the source, we do not reproduce it.
5. **If the evidence supports only a narrower essay, write the narrower essay.**
   Scope down to what is actually supported rather than padding to reach a word
   count. A tight 900-word essay that is fully grounded beats a 1,250-word essay
   with invented filler.
