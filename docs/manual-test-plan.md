# Manual UI test plan

Automated tests cover the API, retrieval, routing, sanitisation and persistence
(`make test-all`). This plan covers what they can't: rendering, interaction,
streaming behaviour, and how failures actually *look*.

**Setup:** `make up && make ingest`, then open http://localhost:5173.
Estimated run time: ~25 minutes.

---

## 1. First run and empty state

| # | Step | Expected |
|---|---|---|
| 1.1 | Open the app in a fresh browser profile | Empty state with "What are you working through?" and 4 suggestion cards |
| 1.2 | Check the subtitle | Shows the **live** episode count from the API (303), not a hardcoded number |
| 1.3 | Check the sidebar footer | Green dot, model name (`qwen2.5:7b-instruct`), green corpus dot with episode/passage counts, "Running locally" |
| 1.4 | Open DevTools → Application → Local Storage | A `lenny.user_id` UUID exists |
| 1.5 | Reload | Same `user_id`; no duplicate sessions created |

---

## 2. Grounded answer — the core loop

| # | Step | Expected |
|---|---|---|
| 2.1 | Click the PMF suggestion card | A session is created; the question appears immediately as a user message |
| 2.2 | Watch the first second | Status line "Searching 303 episode transcripts…" with a spinner |
| 2.3 | Watch for the source panel | **Sources appear before any prose** (~1s), showing "N sources from M episodes" |
| 2.4 | Watch generation | Tokens stream progressively; a blinking caret sits at the streaming edge |
| 2.5 | On completion | Answer contains inline citation chips (`E1`, `E2`…) |
| 2.6 | Check the message header | Shows model name and elapsed seconds |
| 2.7 | Expand the source panel | Each source shows label, guest, episode title, a short excerpt, and a "Watch at H:MM:SS" link |
| 2.8 | Click a citation chip | Panel expands, scrolls to the matching source, flashes it briefly |
| 2.9 | Press Tab to a chip, press Enter | Same behaviour as clicking |
| 2.10 | Click a "Watch at…" link | Opens YouTube **at the cited timestamp**, in a new tab |
| 2.11 | Verify the claim | Spot-check that what the answer attributes to a guest matches the excerpt |
| 2.12 | Count distinct episodes | Sources span **multiple** episodes, not one dominant guest |

---

## 3. Insufficient evidence — the differentiator

| # | Step | Expected |
|---|---|---|
| 3.1 | Ask "What's the best CI/CD tool for Kubernetes?" | Responds in <10s |
| 3.2 | Check the styling | Amber left border and tinted background — visibly **not** an error, and not a normal answer |
| 3.3 | Read the content | States it has no grounded evidence, explains what was searched, and lists topics the corpus **does** cover |
| 3.4 | Check the source panel | **No sources** shown |
| 3.5 | Check backend logs | `insufficient_evidence` event with a `reason` and `best_similarity` |
| 3.6 | Try 2 more off-corpus questions (e.g. sourdough, timing belt) | All refuse |
| 3.7 | Try 3 on-corpus questions (hiring a first PM, onboarding, pricing) | All answer with citations |

> **Pass criterion:** ≥90% correct on both directions. If in-corpus questions are
> being refused, run `make calibrate`.

---

## 4. Follow-ups and session context

| # | Step | Expected |
|---|---|---|
| 4.1 | After the PMF answer, ask "what about the second one?" | Resolves against prior context; doesn't retrieve something unrelated |
| 4.2 | Ask a new unrelated question in the same session | Retrieves fresh sources for the new topic |
| 4.3 | Click **New chat** | Empty state; previous conversation not visible |
| 4.4 | Ask something that references the prior chat | Assistant has **no** knowledge of it (sessions are isolated) |
| 4.5 | Switch back via the sidebar | Full history restored, with citations intact |
| 4.6 | `make restart`, then reload | Sessions and messages survive |

---

## 5. Ship 30 essay

| # | Step | Expected |
|---|---|---|
| 5.1 | In the PMF conversation, click **Write essay**, send "turn this into an essay" | Button shows pressed state; status "Drafting a ~1,250 word essay…" |
| 5.2 | Wait for completion (may take ~60s locally) | Artifact panel opens automatically |
| 5.3 | Check layout | Three columns on a wide screen: sidebar, chat, artifact |
| 5.4 | Check typography | Artifact prose is **serif**, visibly distinct from the chat |
| 5.5 | Check structure | H1 headline, `##` sections with meaningful subheads, bolded key sentences, bullets |
| 5.6 | Check word count in the header | Roughly 900–1,600 |
| 5.7 | Check "Grounded in N transcripts" disclosure | Expands to a list of named guests with links |
| 5.8 | Check the prose | **No** inline `[E1]` markers (stripped for essays) |
| 5.9 | Verify a factual claim against a source | Should be supported, not invented |
| 5.10 | Click the **Markdown** tab | Raw Markdown source, editable |
| 5.11 | Edit the text | A **Save** button appears |
| 5.12 | Click Save, then reload the page | Edit persisted |
| 5.13 | Click the copy icon | Icon changes to ✓; clipboard contains the Markdown |
| 5.14 | Click the download icon | Downloads a `.md` file with a sanitised filename |

---

## 6. Artifact viewer and HTML sandboxing

| # | Step | Expected |
|---|---|---|
| 6.1 | Ask "Build an HTML page summarising the PMF advice" | Artifact renders as a **styled page**, not raw markup |
| 6.2 | Check the footer note | "Rendered in a sandboxed frame — scripts blocked, no network access" |
| 6.3 | DevTools → Elements, inspect the iframe | Has `sandbox` **without** `allow-scripts` and without `allow-same-origin` |
| 6.4 | DevTools → Network, find the `/render` request | Response has `Content-Security-Policy` with `default-src 'none'` and no `script-src` |
| 6.5 | Click the **HTML** tab, paste `<script>alert('xss')</script>`, Save | **No alert fires**; script is stripped on save |
| 6.6 | Paste `<img src=x onerror="alert(1)">`, Save | No alert; handler removed |
| 6.7 | Reload the page | Artifact still clean — sanitisation was persisted, not just visual |
| 6.8 | Press **Escape** with the artifact focused | Panel closes |
| 6.9 | Click ⇥ in the chat header | Panel hides; chat expands to full width |

---

## 7. Failure modes

| # | Step | Expected |
|---|---|---|
| 7.1 | `pkill ollama`, then ask a question | Structured error naming Ollama **and** the fix (`ollama serve`) |
| 7.2 | Check the sidebar | Provider dot turns red within ~20s (polling) |
| 7.3 | `ollama serve &`, wait ~20s | Dot returns to green with no reload |
| 7.4 | Set `LLM_PROVIDER=anthropic` with no key, `LLM_FALLBACK_PROVIDER=ollama`, restart | Amber **degraded banner**: configured provider unavailable, running on fallback |
| 7.5 | Ask a question in that state | Answers normally; message labels the model actually used |
| 7.6 | Set `LLM_FALLBACK_PROVIDER=none`, restart, ask | Clear error; no silent failure |
| 7.7 | `docker compose stop db`, reload the page | App still loads; error explains the database is unavailable with remediation |
| 7.8 | `docker compose start db`, reload | Recovers |
| 7.9 | Point at an empty database (or before ingest) | Banner: "Knowledge base is empty. Run `make ingest`" |
| 7.10 | Send a very long message (>4000 chars) | Rejected client-side or with a clean 422 — never a 500 |
| 7.11 | Start a long answer, click **Stop** | Generation halts; UI returns to an input-ready state |

---

## 8. Responsive and accessibility

| # | Step | Expected |
|---|---|---|
| 8.1 | Resize to ~1000px with an artifact open | Artifact **replaces** the chat; sidebar remains |
| 8.2 | Resize to ~700px | Single column; hamburger appears |
| 8.3 | Tap the hamburger | Sidebar slides in over a scrim |
| 8.4 | Tap the scrim | Sidebar closes |
| 8.5 | Check any wide table | Scrolls **inside its container**; the page never scrolls horizontally |
| 8.6 | Tab through the entire app | Every control reachable; focus ring always visible |
| 8.7 | Tab to a session row's delete button | Becomes visible on focus (not hover-only) |
| 8.8 | Switch the OS to dark mode | Entire UI switches; no unreadable or unstyled areas |
| 8.9 | Enable OS "Reduce motion" | Citation flash, spinner animation and smooth scroll are suppressed |
| 8.10 | Run Lighthouse → Accessibility | No critical violations |
| 8.11 | With VoiceOver/NVDA, focus the active session | Announced as current |

---

## 9. Session management

| # | Step | Expected |
|---|---|---|
| 9.1 | Check a session title in the sidebar | Derived from the first message, truncated sensibly |
| 9.2 | Create 3 sessions and use them | Ordered by most recent activity |
| 9.3 | Hover a session row | Delete (×) appears |
| 9.4 | Delete the **active** session | Removed from the list; view returns to the empty state |
| 9.5 | Delete a non-active session | Removed; current conversation unaffected |
| 9.6 | Open the app, don't send anything, reload | **No** empty session was created (lazy creation) |

---

## Regression checklist (run before any release)

- [ ] 2.3 sources appear before prose
- [ ] 2.10 YouTube deep link opens at the right timestamp
- [ ] 3.1–3.3 out-of-corpus question refuses correctly
- [ ] 4.4 sessions are isolated
- [ ] 5.8 essays contain no inline citation markers
- [ ] 6.5 script injection into an artifact is inert
- [ ] 7.1 Ollama down produces an actionable error
- [ ] 8.8 dark mode is fully styled
