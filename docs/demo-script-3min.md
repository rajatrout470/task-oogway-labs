# Demo video — 2–3 minute script (camera on)

The submission asks for **2–3 minutes**, camera enabled, covering: the problem,
the product, local Ollama, and one technical trade-off.

That is ~400 words of speech. Everything below is timed and word-for-word.
The longer 6–8 minute version is in [demo-script.md](demo-script.md).

**Total: ~2:50.**

---

## ⚠️ Pre-flight — do these or the video will not work

The single biggest risk is **dead air**. A cited answer takes ~11s to first
token; an essay takes ~60s. You cannot wait 60 seconds on camera.

```bash
# 1. Servers up
curl -s localhost:8000/api/health | python3 -m json.tool | head -3   # "ok"

# 2. Warm the model — a cold load is 27s of nothing
curl -s http://localhost:11434/api/chat \
  -d '{"model":"qwen2.5:7b-instruct","messages":[{"role":"user","content":"hi"}],"stream":false,"keep_alive":"30m"}' \
  > /dev/null

# 3. Confirm it is resident
ollama ps
```

- [ ] **Pre-generate the essay before recording.** In a session, ask the PMF
      question, then click *Write essay*. Leave that session ready so you only
      have to *open* it on camera — never generate one live.
- [ ] Have a **second session** already containing the PMF answer, so beat 2
      can show a finished cited answer immediately if the live one is slow.
- [ ] Terminal open with `ollama ps` already typed, not yet run.
- [ ] Browser at ~110% zoom so citations are legible on video.
- [ ] Notifications off. One clean browser window.

---

## Plain-English version — easiest to read aloud

Short words, short sentences. ~420 words, about 2 minutes 50 seconds.
Stage directions are in brackets; everything else is spoken.

**[Camera on you.]**

Lenny's Podcast has 303 episodes. That is about 500 hours of advice from people
who have actually built products. But it is very hard to search. YouTube only
looks at episode titles, not at what people really said. The part you need might
be one small section, deep inside an episode you never opened. And if you ask
ChatGPT, the answer sounds good, but you cannot check where it came from. So you
cannot use it in a real meeting. That is why I built this.

**[Switch to screen. Ask the product-market-fit question.]**

Every answer comes only from these transcripts. Watch the sources here. They
show up in less than one second, before any text is written. The model runs on
my laptop, so the first words take about eleven seconds. I would rather show you
something real while you wait. Here is the answer. It uses six pieces of
evidence from five different episodes. Every point has a source. If I click one,
I can read the exact quote. And this link opens the video at the exact second.

**[Come back from YouTube. Ask: "What's the best CI/CD tool for Kubernetes?"]**

Now this part matters most. I ask about a tool the podcast never covers. It says
no. And the model never even ran. The search did not find anything close enough,
so the app stopped before writing a single word. This is the main idea. I do not
just ask the model to be careful. I check it in code. A small model will answer
anyway if you only ask it nicely. A model that never runs cannot make things up.

**[Open the pre-generated essay, then switch to the terminal and run `ollama ps`.]**

It can also turn an answer into a full essay, shown next to the chat. And all of
this runs on my laptop. Here is the model, loaded in memory. There is no API key
in this demo. My questions, the transcripts, the essays — none of it leaves this
computer. Even the search runs locally.

**[Camera on you.]**

Here is the trade-off. The task asked for two things that do not fit well
together. Use the Claude Agent SDK. And use a small local model as the default.
These two need different approaches. Claude is good at picking the right tool. A
small model is not. If it picks wrong, you ask a simple question and get a very
long essay. So I let each model do what it is good at. Claude picks its own
tools. The small model follows simple rules I wrote. I trust the model where I
can, and I use code where I cannot.

One command to start. 141 tests. It all runs offline. The part I like most is
that it can say "I don't know." An assistant that admits that is one you can
actually trust.

---

## Continuous version — read straight through

For anyone who'd rather read one flowing script than hit timing marks. ~440
words, roughly 2 minutes 55 seconds at a natural pace. Stage directions are in
brackets; everything else is spoken.

**[Camera on you.]**

Lenny's Podcast is about five hundred hours of the best operator knowledge in
tech — three hundred and three episodes. And it's effectively unsearchable at
the exact moment you need it. YouTube search finds episodes, not claims. The
thing you're looking for is one paragraph forty minutes into an episode you
never clicked on. And if you ask ChatGPT instead, you get a fluent, confident
answer that you can't take into a leadership review, because you can't say where
it came from. So I built a research instrument rather than a chatbot.

**[Switch to screen. Ask the product-market-fit question.]**

Everything it says is grounded in those transcripts. Watch the sources here —
they appear in under a second, before a single word of the answer is written.
On a local seven-billion-parameter model, the first token takes about eleven
seconds, and I'd much rather fill that wait with real information than a
spinner. And there's the answer: six passages drawn from five different
episodes, every claim cited. If I click one of these citations, it opens the
actual excerpt — and this link goes to the exact second of the source video.

**[Come back from YouTube. Ask: "What's the best CI/CD tool for Kubernetes?"]**

But this is the behaviour I care most about. It refuses — and the model was
never even called. Retrieval scored the best match below a calibrated threshold,
so the pipeline stopped before generation ever started. Grounding here is
enforced in code, not requested in a prompt. A small model told "only answer if
the context supports it" will cheerfully answer anyway. A small model that's
never invoked can't.

**[Open the pre-generated essay, then switch to the terminal and run `ollama ps`.]**

It also turns a grounded answer into a publishable essay, rendered right beside
the chat. And all of this is running on my laptop — that's the model sitting
resident on my GPU. There's no API key anywhere in this demo. My questions, the
transcripts, the essays, none of it leaves this machine. Even the embeddings are
local, so the search is offline too.

**[Camera on you.]**

The trade-off I'd highlight is this. The brief asked for two things that pull in
opposite directions: build on the Claude Agent SDK, and make a local
seven-billion model the default. Those want opposite routing strategies. Claude
picks tools reliably; a small local model doesn't, and the failure is bad — you
ask a question and get a twelve-hundred-word essay. So rather than force one
strategy, each provider declares whether it does reliable tool selection. Claude
gets model-driven routing through the Agent SDK. The local model gets
deterministic rule-based routing. That's not a workaround — it's using the
model's judgement where it's reliable, and code where it isn't.

One command to start, a hundred and forty-one tests, runs entirely offline. The
piece I'd point at is the refusal. An assistant willing to say "I don't have
evidence for this" is the only kind you can actually build on.

---

## Beat 1 — Problem · camera on you · 0:00–0:25

> "Lenny's Podcast is about 500 hours of the best operator knowledge in tech —
> 303 episodes. And it's effectively unsearchable at the moment you actually
> need it.
>
> YouTube search finds episodes, not claims. And if you ask ChatGPT, you get a
> fluent, confident answer that you can't bring to a leadership review — because
> you can't say where it came from.
>
> So I built a research instrument, not a chatbot."

*(Switch to screen share.)*

---

## Beat 2 — Product · screen · 0:25–1:35

**Type the PMF question** (or open the pre-loaded session).

While it runs:

> "Everything is grounded in those transcripts. Watch the sources — they appear
> in under a second, before any text is written. On a local 7B, first token
> takes about eleven seconds, and I'd rather fill that with real information
> than a spinner."

When the answer lands:

> "Six sources across five different episodes. Every claim is cited."

**Click a citation chip**, then **click the "Watch at…" link**.

> "And that goes to the exact second of the source video."

*(Come straight back — don't let YouTube play.)*

**Now the important part. Type: "What's the best CI/CD tool for Kubernetes?"**

> "This is the behaviour I care most about. It refuses — and the model was never
> even called. Retrieval scored below a calibrated threshold, so the pipeline
> stopped before generation.
>
> Grounding is enforced in code, not asked for in a prompt. A 7B told 'only
> answer if the context supports it' will answer anyway. A 7B that's never
> invoked can't."

**Open the pre-generated essay artifact.**

> "And it turns a grounded answer into a publishable Ship 30 essay, rendered
> right beside the chat."

---

## Beat 3 — Local Ollama · screen · 1:35–2:05

**Point at the sidebar indicator**, then run `ollama ps`.

> "This is running entirely on my laptop. That's a 7-billion-parameter model
> resident on my GPU. There's no API key anywhere in this demo — my questions,
> the transcripts, the generated essays, none of it leaves this machine.
>
> Embeddings are local too, so even the search is offline."

**Show `.env` briefly.**

> "Switching to a cloud model is one line here. No application code names a
> model — that's the whole point of the config layer."

---

## Beat 4 — Trade-off · camera on you · 2:05–2:40

> "The trade-off I'd highlight: the brief asked for two things that pull in
> opposite directions — build on the Claude Agent SDK, and make a local 7B the
> default.
>
> Those want opposite routing strategies. Claude picks tools reliably. A 7B
> doesn't — and the failure is bad: you ask a question and get a
> 1,250-word essay.
>
> So I didn't force one strategy. Each provider *declares* whether it does
> reliable tool selection. Claude gets model-driven routing through the Agent
> SDK. Ollama gets deterministic rule-based routing.
>
> That's not a workaround — it's using the model's judgement where it's
> reliable, and code where it isn't."

---

## Close · camera · 2:40–2:50

> "One command to start, 141 tests, runs fully local. The piece I'd point at is
> the refusal — an assistant willing to say 'I don't have evidence for this' is
> the only kind you can actually build on."

---

## Alternative trade-off (swap into Beat 4 if you prefer)

Shows intellectual honesty rather than architecture judgement. Same length.

> "In my PRD I committed to a two-second time-to-first-token before I'd measured
> anything. Once it ran, I profiled it: a small prompt gives first token in half
> a second, but our real evidence prompt takes eighteen.
>
> Time-to-first-token on a 7B is dominated by prompt *evaluation*, and a grounded
> answer needs a big evidence prompt. Those two things are in direct conflict —
> no engineering removes it.
>
> I got it down to eleven seconds by trimming passages. But the real fix was
> admitting I'd set the wrong metric. The user doesn't need prose in two seconds
> — they need to know it's working and what it found. Sources now render in
> 0.8 seconds. I changed the metric, not the claim."

---

## If something breaks on camera

| Symptom | Do this |
|---|---|
| Answer is slow | Keep narrating the source panel — it's on screen and it's the point |
| It refuses a question you expected it to answer | Own it: "that's the threshold being conservative — a deliberate trade-off." Move on |
| Anything hangs | Cut. Re-record the beat. Don't debug on camera |

---

## Numbers worth saying out loud

- **303** episodes · **21,984** indexed passages
- Sources visible in **0.8 seconds**
- **141** tests
- **Zero** API keys

## Upload settings

- **Title:** The Lenny Growth Assistant — grounded PM research on a local 7B
- **Visibility:** Unlisted (shareable by link, not public)
- Confirm the link opens in a private window before submitting
