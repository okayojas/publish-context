# Classification rubric

Rubric version **9**. Bump `generator.version` in the payload when this file changes, so a batch of bad classifications is attributable to a rubric version rather than untraceable across installs.

You are classifying **one candidate at a time** from `candidates.json`. Each already carries its authorship, activation, timestamp and refs — those are extracted, not judged. Your job is three fields: `kind`, `tier`, `target`.

---

## Step 0 — Quarantined records are already decided

A record with `"secret_detected": true` has **no `body`, `statement` or
`rationale`** — the collector detected a credential and refused to copy them.
There is nothing to classify.

Count it under `excluded` as **`secret_bearing`** and move on. Do not reconstruct
a statement from the filename, and do not classify it from what you can infer:
the assembler rejects the whole batch if you try, because a classifier reaching
into a quarantined file is a rubric failure worth surfacing rather than
absorbing.

Tell the person the filename and the finding kinds. Never quote a value, and
don't ask them to paste the line.

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

### The `committed` field answers this directly

A record from a project instruction file (`authority_signal: "project_root"` — a
CLAUDE.md, AGENTS.md or equivalent read from the working tree) carries
`committed`:

| `committed` | What it means | Default |
|---|---|---|
| `true` | The file is tracked in the repository, so the code connector already holds it with provenance | Lean **`derivable`** |
| `false` | Untracked or gitignored — invisible to every other system in the organization | **Keep.** The highest-value content the collector reaches |
| `null` | Not a git checkout, or git couldn't answer | Judge on content as usual |

`committed: false` is the case this stage exists for. A gitignored CLAUDE.local.md
is hand-written, never reviewed, never shared, and holds exactly the local
knowledge nothing else records.

`committed: true` is not an automatic exclusion — a *committed* file may still
carry reasoning the connector stores as prose but nothing extracts as a claim.
But start from derivable and require a reason to keep it.

These are also the only records with `authority: "asserted"`. That is a
stronger epistemic footing than anything else in the store, so an overstated
claim here costs more. Prefer the file's own wording.

---

## Step 2 — Four more exclusions, all mechanical

**`session_local`** — scaffolding with no durable value. "The user wants me to refactor this file." "Working on the auth bug today." If it would be meaningless in three months, exclude it.

**`provisional`** — written down before it was agreed. A draft ticket, a proposal, an architecture position marked "awaiting decision", a plan with competing alternatives still open.

**The collector does the looking.** A record with a non-empty `provisional_signals` array said so in its own words, and the array carries the kind and line number:

| Signal | What matched |
|---|---|
| `status_unsettled` | a `status:` / `state:` line reading draft, proposed, pending, awaiting, open, TBD |
| `awaiting` | "awaiting a decision / approval / sign-off / review" |
| `blocked_on` | "blocked on …" |
| `not_settled` | "not yet decided / agreed / approved / signed" |
| `draft_marker` | a line beginning DRAFT / PROPOSAL / TBD / WIP |
| `pending_ref` | "pending ADR-…", "pending sign-off" |
| `self_dated` | "as of YYYY-MM-DD" — a snapshot, not a standing fact |

This is a **signal, not a verdict**: plenty of records mention a pending ticket while asserting something perfectly settled. Read the line it points at and decide. But a signal you were handed and ignored is a different failure from one you never saw, and **nothing carrying `status_unsettled`, `awaiting` or `draft_marker` should reach tier 1**.

Two real stores, both missed:

> A third competing architecture position, explicitly `status: awaiting decision` with ADRs still pending, whose kernel classified cleanly as a `constraint`. Publishing it would have told every agent in the organization that this was the direction.

> A plan whose own status line read "awaiting boss sign-off as of 2026-07-14", in a file 54 days old. Its four claims published as tier-1 constraints with no expiry. Two signals fired on it and neither was consulted.

`still_true` does not cover either case — that field is about a claim going stale, not about one that was never settled.

Pair the signal with **age**. `asserted_at` and `timestamp_source` are on every record, and the report prints days-old beside each unsettled one. Old *plus* unsettled is the combination that matters; either alone is usually fine.

**`machine_local`** — true, durable, not derivable from any repository, and about **one machine**. Local ports, container names, compose-project layout, which folder holds which checkout, this laptop's network quirks.

The distinction from `session_local` is duration: these stay true. The distinction from `derivable` is that no shared system of record holds them — the laptop is the only record, which is exactly why they are worthless to anyone else.

Careful here, because machine-local records often *wrap* something shareable. "Use `docker start`, never `docker compose up`, because the orchestrator loses its queue offsets" is about the team's stack, not one laptop — keep it. "The stack runs on ports 8000–8010 on this machine" is not. When a record mixes both, that is Step 2.5's job, not this one.

**`person_sensitive`** — anything evaluative about a named colleague. Performance, competence, reliability, working style *of someone other than the publisher*. This is **not a judgement call**: if a person other than the publisher is named and the content characterizes them, exclude it. Report the count, never the content.

> This exclusion is the one that decides whether this feature survives contact with an engineering organization. When uncertain, exclude.

Facts *about* a colleague that are not evaluative are fine: "Priya owns the auth-gateway deploy rota" is an `authority` record, not a personnel assessment.

---

## Step 2.5 — Is this a claim, or a container?

Some records are not one claim. They are a **summary** — project status, an
architecture overview, a digest of several decisions — and they need decomposing
before Step 3 can apply. The seven kinds all have a normative shape ("do this",
"don't do that", "this applies to that"); a container has a descriptive one.

The `project` category, where a tool emits one, is nearly always a container.

**A container is a derivable wrapper around a non-derivable kernel.** Take it
apart along that seam:

> "`weight-config-api` is a FastAPI + MongoDB service that stores and serves
> weight-engine scoring parameters **so coefficients change without a code
> deploy**. Reads: any authenticated caller. Writes: `TECH_LEAD` only, audited.
> Architecture: `app/routers/configs.py`…"

| Fragment | Verdict |
|---|---|
| "FastAPI + MongoDB service" | derivable — the code says so |
| "`app/routers/configs.py`" | derivable — the tree says so |
| "Writes: `TECH_LEAD` only" | derivable — the decorator says so |
| **"so coefficients change without a code deploy"** | **keep** — design intent, written nowhere else |

Which yields one claim, not one record:

    constraint — "Weight-engine scoring parameters are served at runtime rather
    than compiled in, so coefficients can change without a deploy."

### How to emit it

A container produces **zero, one, or several** entries. Give each a
`claim_index` starting at 0, all carrying the container's `content_hash`:

```json
{ "content_hash": "<the container's, unchanged>", "claim_index": 0,
  "kind": "constraint", "tier": 1, "target": "decision",
  "statement": "…", "rationale": "…" }
```

They share the container's body and hash — that is the evidence a reviewer
reads, and it is why several claims can point at one file. Their ids differ.

A container with no kernel yields **nothing**: count it under `excluded` as
`derivable` and move on. That is not a failure — but it is also not the common
outcome, and assuming it was cost a real run half its yield.

> A pass over 17 records produced 36 claims and excluded none. Re-run with Step 1
> enforced, the same records produced **50** claims. The failure was two-sided:
> the pass under-excluded *and* under-mined, both in this step, because it
> treated dense containers as yielding one or two claims each. One plan went
> 4 → 11. Mine the container properly and the exclusions appear on their own —
> they are the fragments you rejected on the way.

### Count exclusions in fragments, and say so

`excluded` entries carry a **`unit`**, and it must be the same throughout:

| `unit` | What `count` counts |
|---|---|
| `fragment` | individual claims discarded while decomposing. **Use this.** |
| `record` | whole candidates dropped before Step 2.5 ever opened them |

Fragment level is the only unit at which this step's work is visible. A record
that yields one kernel out of nine fragments is not an excluded record — count
it at record level and it reads as zero exclusions, which is what happened:

> That same run reported 0% excluded. At record level the figure was honest,
> since every record held at least one keepable kernel. The number was
> uninterpretable rather than wrong, and the report then diagnosed a skipped
> filter that had in fact run. A count with no unit is not a count.

Emit a fragment-level entry **even when it is zero** — an empty `excluded` list
carries no unit, so it cannot distinguish a skipped Step 1 from a store where
everything yielded something.

The optional `note` records **how the count was taken**, never what was
excluded. It ships in the payload, so a quoted excerpt there publishes exactly
what the exclusion withheld; the assembler rejects a note that quotes a long
passage, and caps it at 240 characters.

### A gate is not a fragment

Decomposition assumes the fragments are independent. **A precondition is not.**
If a container carries a gate — an approval that hasn't landed, a freeze, a
"don't act on this until X" — that gate qualifies *every* kernel underneath it.
Excluding it and keeping them inverts the record's own meaning.

> A real plan was headed "Locked decisions (CEO/user-approved)" and closed with
> "awaiting boss sign-off as of 2026-07-14 · NO code changes to any repo until
> user confirms." The pass excluded the closing line as `session_local` and
> published eleven tier-1 constraints from the body. The freeze was the one
> sentence that told a reader what the other eleven were worth.

Two contradictory claims *inside one record* is the signal. When you find one,
the record does not decompose — resolve the contradiction first.

**The rule: the gate travels with the kernels, or nothing ships.** In practice:

1. If the gate can be checked, check it. Ask whether the sign-off happened.
2. If it has landed, publish with `still_true: "yes"` and say who confirmed.
3. If it hasn't, or nobody knows, **all** the kernels are `provisional` — not
   just the gate.

Never publish the kernels and drop the gate. That is the one decomposition that
produces claims the source does not support, and it produces them at tier 1.

### The one risk, and how to hold it

Everywhere else you *pick* a statement from the source's own words. Here you
**author** one, which can drift from what the record actually said. Two rules:

- **Never assert more than the source does.** If the record says "we moved to
  advisory locks", do not write "advisory locks are required" — that is a
  stronger claim than the evidence carries.
- **Prefer the source's own phrasing** for the operative clause, even when it is
  clumsier than what you would write.

If a fragment is only *arguably* a kernel, drop it. A container that produces
nothing costs a count; one that produces an overstated claim costs a reviewer's
trust in every other record.

---

## Step 3 — Assign the kind

Seven values. Pick the one that fits; if two fit, prefer the higher row.

| Kind | Tier | Target | Scope required |
|---|---|---|---|
| `rejected_alternative` | 1 | `decision` | **yes** |
| `constraint` | 1 | `decision` | **yes** |
| `authority` | 1 | `decision` | **yes** |
| `preference` | 1 | `preference_rule` | no |
| `playbook` | 2 | `document` | no |
| `vocabulary` | 2 | `alias_proposal` | **yes** |
| `external_reference` | 2 | `document` | no |

**Scope required** is enforced by the assembler, not left to judgement. A
constraint with no subject is a prohibition that applies to everything, which is
worse than dropping the record; "ask X before touching Y" needs Y; a vocabulary
entry's referent *is* the claim.

The other four legitimately have no external scope. A method preference or
playbook defaults to the publisher, and pinning it to one repository **narrows it
wrongly** rather than sharpening it — "search the whole subsystem before claiming
absence" applies to all code, not to whichever repo happened to be mentioned in
the body. Don't attach a scope to those kinds just because a reference was
available.

A hint graded `container` does not count. It names the folder someone keeps their
code in, which is not a weaker scope — it is no scope.

### When the claim really is platform-wide

Some constraints have no narrower target. "One connector per external system,
never one per bug or event type" is a rule about the construction path, not about
any one service, and attaching it to whichever service the body mentioned would
narrow it wrongly.

For those, set **`scope_breadth: "platform_wide"`** and leave `claimed_scope`
empty. That satisfies the scope requirement, because the point of the
requirement is to stop *absence* from meaning two things — "applies to
everything" and "we don't know" need opposite handling, so the broad case has to
be stated rather than left as a gap.

Two guards, and they exist because this is otherwise the cheapest possible
escape from the scope rule:

- It **cannot** coexist with a narrow scope. One or the other.
- At tier 1 it **requires a rationale**. It is the broadest claim the payload can
  carry, so it does not get to be the one without a reason attached.

Use it only when the claim's own text asserts that breadth. If you are reaching
for it because you could not find a scope, the honest move is still to ask.

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

**`claimed_scope`** — take the extracted `scope_hints` and `refs` and record what this applies to, but only for the kinds that require it (Step 3). Every entry keeps `resolution: "unresolved"` and `canonical_id: null`. **Never invent an identifier.** An unresolved reference is honest; a wrong one is a bad edge in the graph with a citation on it.

Grade the evidence you're working from — the collector already did most of it:

| Source | Strength |
|---|---|
| `refs` with `zone: "statement"` | strongest — the author named the subject *while making the claim* |
| `refs` with `zone: "rationale"` | strong |
| `scope_hints` with `quality: "name"` | good — a real project name, verified or unencoded |
| `refs` with `zone: "body"` | weak — a mention, not a subject. Fine as corroboration, thin on its own |
| `scope_hints` with `quality: "encoded_path"` | unusable until `resolve-projects.py` upgrades it |
| `scope_hints` with `quality: "container"` | **not a scope.** Never attach it |

A ticket or ADR id is the most *resolvable* thing available, because it is already canonical in a connected system. Prefer it when present.

If a required scope has no usable evidence, **ask** — batch the question per the last section. Don't fall back to a body mention and don't fall back to the path.

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
