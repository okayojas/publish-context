# Classification rubric

Rubric version **3**. Bump `generator.version` in the payload when this file changes, so a batch of bad classifications is attributable to a rubric version rather than untraceable across installs.

You are classifying **one candidate at a time** from `candidates.json`. Each already carries its authorship, activation, timestamp and refs — those are extracted, not judged. Your job is three fields: `kind`, `tier`, `target`.

---

## Step 1 — The derivability test, first and always

**Ask: could the graph, the repository, or the ticket tracker already answer this?**

If yes → `excluded`, reason `derivable`. Stop. Do not classify further.

This is the highest-volume filter and it runs before everything else. Ingesting derivable content produces staler, un-attributed duplicates of facts the platform already holds with provenance.

| Derivable — exclude | Not derivable — keep going |
|---|---|
| "The API uses FastAPI" — `pyproject.toml` says so | "We chose FastAPI over Flask because the async story mattered for the webhook path" |
| "The auth service lives in `src/auth/`" — the tree says so | "Don't put new endpoints in `src/auth/` — it's being split next quarter" |
| "PROJ-812 is closed" — Jira says so | "PROJ-812's fix was reverted twice; the root cause is still open" |
| "We use pytest" — the config says so | "Run pytest with `-p no:randomly` locally or the fixtures collide" |

The pattern: **a fact about current state is derivable; the reasoning behind it is not.**

---

## Step 2 — Two more exclusions, both mechanical

**`session_local`** — scaffolding with no durable value. "The user wants me to refactor this file." "Working on the auth bug today." If it would be meaningless in three months, exclude it.

**`person_sensitive`** — anything evaluative about a named colleague. Performance, competence, reliability, working style *of someone other than the publisher*. This is **not a judgement call**: if a person other than the publisher is named and the content characterizes them, exclude it. Report the count, never the content.

> This exclusion is the one that decides whether this feature survives contact with an engineering organization. When uncertain, exclude.

Facts *about* a colleague that are not evaluative are fine: "Priya owns the auth-gateway deploy rota" is an `authority` record, not a personnel assessment.

---

## Step 3 — Assign the kind

Seven values. Pick the one that fits; if two fit, prefer the higher row.

### `rejected_alternative` · tier 1 · target `decision`
An approach tried or considered and deliberately abandoned, **with the reason**. The single highest-value kind — nothing else in the organization records what was *not* done, and re-proposing a killed approach is the most expensive failure mode of a coding agent.

- ✅ "Don't use Redis for the SSO refresh lock — it dropped locks under reconnect storms; we moved to Postgres advisory locks."
- ❌ "We use Postgres advisory locks." → `derivable`, the code says so.

**Requires a rationale.** Without one it's just a prohibition — classify as `constraint` instead.

### `constraint` · tier 1 · target `decision`
An undocumented rule about how this system must be worked on. Makes generated work wrong in ways a build-and-test gate cannot catch.

- ✅ "Payments changes need a migration ticket linked before review."
- ✅ "Don't run the backfill during business hours."
- ❌ "Tests must pass before merge." → `derivable`, that's branch protection.

### `authority` · tier 1 · target `decision`
Who actually knows or decides something, where that differs from what the ownership files say.

- ✅ "Ask Priya before touching auth-gateway, whatever CODEOWNERS says."
- ❌ "The platform team owns auth-gateway." → `derivable`, CODEOWNERS says so.

### `preference` · tier 1 · target `preference_rule`
How work should be produced — style, sequencing, tooling. Shapes a draft rather than blocking it.

- ✅ "Write the failing test first, then the fix."
- ✅ "Keep PRs under ~400 lines; split rather than stack."
- ❌ "Use 2-space indentation." → `derivable`, the formatter config says so.

Preferences about *the publisher themselves* rather than about work — "I prefer terse explanations" — are `target: person_property`, still `kind: preference`.

### `playbook` · tier 2 · target `document`
A reusable procedure for a recurring situation. **Highest-risk kind**: it governs how work gets done rather than informing it, so it carries the strictest review downstream. Set `pam_component: procedural`.

- ✅ "When the SSO tests fail, check token clock skew before anything else."
- ❌ "How to run the test suite." → `derivable`, the README says so.

### `vocabulary` · tier 2 · target `alias_proposal`
A name people use that no system uses. Valuable on the query side, but a name is an identity claim, so it is strictly a proposal.

- ✅ "'The checkout thing' means the storefront-api repo."
- ✅ "We call the nightly job 'the reaper'."

### `external_reference` · tier 2 · target `document`
Where something authoritative lives, outside the connected systems. Doubles as a signal about which system to integrate next.

- ✅ "The real payments design doc is in Notion, not Confluence."
- ❌ A bare URL with no explanation of what it is. → `session_local`.

---

## Step 4 — Fill the remaining fields

**`statement`** — one normative sentence. If the source has a `description` frontmatter key, start from it and sharpen it into an imperative. Don't invent detail the body doesn't support.

**`rationale`** — the *why*, verbatim where the source has a `**Why:**` line. Never paraphrase into something stronger than the original claims. `null` is an honest answer.

**`body`** — the original text, unedited. Do not clean it up; it is the evidence a reviewer reads.

**`pam_component`** — `factual` for constraints and vocabulary, `procedural` for playbooks, `identity` for preferences and person properties.

**`claimed_scope`** — take the extracted `scope_hints` and `refs` and record what this applies to. Every entry keeps `resolution: "unresolved"` and `canonical_id: null`. **Never invent an identifier.** An unresolved reference is honest; a wrong one is a bad edge in the graph with a citation on it.

**`sharing`** — default `personal`. Propose `team` only when the content is plainly about shared work and the person confirms it. Never default to `org`.

**`still_true`** — `unknown` unless the source says otherwise. The platform runs its own contradiction check against graph state.

---

## Defaults when unsure

| Field | Default |
|---|---|
| Include or exclude? | **Exclude.** |
| `tier` | 2 |
| `sharing` | `personal` |
| `origin` | `unknown` |
| `still_true` | `unknown` |
| `claimed_scope` when ambiguous | omit the entry, or ask — never guess |

**Never touch `authority`.** It is set by the collector from the file's location using one of four rules. A model judging that text "reads as model-written" is exactly the inference this design refuses. If a record's authority looks wrong, the manifest entry is wrong — fix the data, not the record.

---

## Batching questions

When several high-value records can't be scoped, don't drop them and don't guess. Collect the questions and ask once:

> 3 items look valuable but I couldn't tell what they apply to:
> 1. "Don't run the backfill during business hours" — which service?
> 2. …

One clarification costs a sentence. A wrongly-scoped decision pollutes the context of every run on the wrong service.
