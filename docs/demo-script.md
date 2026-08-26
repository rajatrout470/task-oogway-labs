# Demo video — script & checklist

Target: **6–8 minutes.** Four beats: the problem, the product, local Ollama
running live, and one real technical trade-off.

---

## Before you record

```bash
ollama serve                       # separate terminal, leave running
ollama list                        # confirm qwen2.5:7b-instruct + nomic-embed-text
make up
make status                        # should show 303 episodes / 21,984 chunks
curl -s localhost:8000/api/health | python3 -m json.tool | head -5   # "ok"
```

**Warm the model before recording** — the first request after startup includes a
~27s cold load that will make the demo look broken:

```bash
curl -s http://localhost:11434/api/chat -d \
  '{"model":"qwen2.5:7b-instruct","messages":[{"role":"user","content":"hi"}],"stream":false}' \
  > /dev/null
```

Checklist:
- [ ] Fresh browser window, no unrelated tabs, notifications off
- [ ] One terminal visible showing `docker compose logs -f backend`
- [ ] Zoom the browser to ~110% so citations are legible on video
- [ ] Have a **second** terminal ready for the "Ollama down" moment
- [ ] Start a new chat so the empty state is on screen

---

## Beat 1 — The problem (45s)

> "Lenny's Podcast is about 500 hours of the best operator knowledge in tech.
> 303 episodes. And it's effectively unsearchable at the moment you actually
> need it.
>
> YouTube search finds *episodes*, not *claims*. The thing you need is one
> paragraph 47 minutes into an episode you never clicked on. And if you ask
> ChatGPT, you get a fluent, confident answer that blends the podcast with
> Reddit and invention — which is exactly the answer you can't bring to a
> leadership review, because you can't say where it came from.
>
> So I built a research instrument, not a chatbot. Everything it says is
> grounded in those transcripts, and it tells you when they don't cover your
> question."

---

## Beat 2 — Product walkthrough (3 min)

### 2a. A grounded answer

Type: **"How do you know if you have product-market fit?"**

Narrate *while it runs* — the timing is the point:

> "Retrieval finishes in about 150 milliseconds. Notice the sources appear
> **now** — under a second — before any prose has been written. That's
> deliberate: on a local 7B, first token takes about 11 seconds, and I'd rather
> fill that wait with real information than a spinner."

When it completes:

> "Six sources across five different episodes — Todd Jackson, Sean Ellis, Rahul
> Vohra. That's cross-operator synthesis, not one guest's opinion presented as
> consensus."

**Click a citation chip.** Show the panel expanding and scrolling to the source.

> "Every claim traces to a passage. And this link" — click it — "goes to the
> exact second of the source video."

*(Let YouTube load briefly, then come back.)*

### 2b. The refusal — the most important 30 seconds

Type: **"What's the best CI/CD tool for Kubernetes?"**

> "This is the behaviour I care most about. It comes back in eight seconds and
> refuses — and notice the model was never even called. Retrieval scored the
> best match at 0.57 against a calibrated floor of 0.60, so the pipeline stopped
> before generation.
>
> That's the whole design: grounding is enforced in code, not requested in a
> prompt. A 7B model told 'only answer if the context supports it' will answer
> anyway. A 7B model that's never invoked can't.
>
> And it doesn't just apologise — it tells you what the corpus *does* cover."

### 2c. The Ship 30 essay + artifact viewer

Go back to the PMF conversation. Click **Write essay**, type: **"turn this into
an essay"**.

> "This is a distinct skill, not a prompt string. The Ship 30 for 30 writing
> principles live in a versioned Markdown file the agent loads at runtime —
> headline discipline, one idea per piece, skimmable structure, a real takeaway.
> A writer can tune those without touching Python.
>
> It's building on the sources from the answer I just read, so the essay and the
> answer agree."

When the artifact appears:

> "It renders beside the chat — not a code dump, not a redirect. Note the
> typography changes to serif: the moment something becomes publishable, it
> should stop looking like an app."

Show the **Markdown tab**, the **word count**, and the **grounded-in-N-transcripts** disclosure.

### 2d. HTML artifact and the sandbox *(optional if time is tight)*

> "HTML artifacts get three independent layers of defence: the prompt asks for
> no scripts, the server sanitises against an allowlist, and it renders in a
> sandboxed iframe with no `allow-scripts` and a restrictive CSP set as a
> response header. 26 XSS payloads are test-enforced."

---

## Beat 3 — Local Ollama, live (1.5 min)

Show the sidebar indicator: green dot, `qwen2.5:7b-instruct`, "Running locally".

In the terminal:

```bash
ollama ps
```

> "That's the model, resident in memory on this laptop. No API key anywhere in
> this demo. Nothing — not my question, not the transcripts — leaves this
> machine."

Show `.env`:

> "Switching models is configuration only. No application code names a model —
> the only place model identity exists is this file."

**Now kill it live:**

```bash
pkill ollama
```

Ask another question. Show the error:

> "Structured error, and — importantly — it tells you the fix. Most failures in
> a system like this are operational, not programming errors. A bare 503 makes
> you guess."

```bash
ollama serve &
```

Refresh; the indicator returns to green.

---

## Beat 4 — One real technical trade-off (1.5 min)

Pick **one**. The routing asymmetry is the strongest.

> "The brief asked for two things that pull in opposite directions: build on the
> Claude Agent SDK, and make a local 7B the default demo path.
>
> Claude picks tools reliably — give it four well-described skills and it
> chooses correctly, and it can chain them. A 7B doesn't. Asked to choose among
> four skills it errs often enough to matter, and the failure is severe: you ask
> a question and get a 1,250-word essay.
>
> So I didn't force one strategy. Each provider *declares* whether it does
> reliable tool selection. Claude gets model-driven routing through the Agent
> SDK, with our skills exposed as in-process MCP tools. Ollama gets
> deterministic rule-based routing.
>
> That's not a workaround — it's capability-appropriate design. Use the model's
> judgement where it's reliable and code where it isn't. Forcing one strategy
> means either crippling the cloud path or shipping a local path that
> misroutes."

### Alternative trade-off: the metric I got wrong

> "In my PRD I set a 2-second time-to-first-token target before I'd measured
> anything. Once it was running I profiled it: a 50-token prompt gives first
> token in 0.57 seconds, but our actual 3,500-token evidence prompt takes 18.
> Time-to-first-token on a 7B is dominated by *prompt evaluation*, and a
> grounded answer requires a big evidence prompt. Those constraints are in
> direct conflict — no engineering removes that.
>
> So I changed the metric rather than the claim. I trimmed passages and dropped
> top-k from 8 to 6, which got 18 seconds down to 11. But the real fix was
> recognising the user doesn't need *prose* in two seconds — they need to know
> it's working and what it found. Sources now render at 0.8 seconds. The metric
> that mattered was time-to-first-*evidence*, and I'd set the wrong one."

---

## Closing (20s)

> "One command to start, one to ingest. Runs fully local with no API key.
> 141 tests. And the thing I'd point at is the refusal path — an assistant
> that's willing to say 'I don't have evidence for this' is the only kind you
> can actually build on."

---

## If something breaks on camera

| Symptom | Say this, then |
|---|---|
| First answer very slow | "That's the cold model load — one-time." Keep talking; warm it next time. |
| Assistant refuses a question you expected it to answer | Own it: "That's the abstention threshold being conservative — that's the trade-off I chose." Move on. |
| Docker not started | Have `make up` output pre-verified; don't debug on camera. |
| Artifact panel doesn't open | Reload the page; the most recent artifact reopens automatically. |

---

## Don't forget

- [ ] Say the numbers out loud: 303 episodes, 21,984 passages, 141 tests
- [ ] Show at least one **YouTube deep link** actually opening
- [ ] Show the **refusal** — it's the differentiator
- [ ] Show `.env` to prove model switching is config-only
- [ ] Name one thing you'd do next (auth, or a golden-set eval in CI)
