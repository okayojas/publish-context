---
name: publish-context
description: Read memory and instructions out of every GenAI CLI on this machine, classify what's worth sharing, and publish it to the Arionix context graph. Use when the user says "publish my context", "share what I've learned", "run the context report", or after a significant decision they want the team's agents to know about.
allowed-tools: Read, Bash
---

# Publish context to the Arionix graph

Read what a person's assistants have accumulated across every tool they use, keep
only what no system of record already holds, and hand it over — with their
explicit consent and nothing they didn't see.

**Default to report-only.** Unless the user clearly asks to publish, run the
report and stop. It is useful on its own and it is how the taxonomy gets
validated before anything is submitted.

**The interpreter differs by platform.** Commands below say `python3`; on Windows
it is usually `python`. Check once with `python3 --version` and use whichever
answers — do not report a missing interpreter as a broken skill.

**Run from the skill directory.** Every path below is relative to it:
`cd ~/.claude/skills/publish-context` first, or use absolute paths.

## The four rules this skill runs on

1. **Never guess authorship.** `authority` comes from the file's location via one
   of four manifest rules. A model judging that text "reads as model-written" is
   exactly the inference this design refuses. Unknown source ⇒ `inferred`.
2. **Never invent an identifier.** Scope references leave this machine
   *unresolved*. An unresolved reference is honest; a wrong canonical id is a bad
   edge in the graph with a citation attached.
3. **Exclusion is one-directional.** Once content is submitted it can only be
   removed through the erasure workflow. Here, "excluded" means it never existed
   in the system. When uncertain, exclude.
4. **The person sees everything before it leaves.** No silent submission, ever.

---

## Step 1 — Collect

```bash
python3 scripts/collect.py --all          # report-only: read everything
python3 scripts/collect.py                # publish: only what changed since last time
```

Resolves each tool's root (environment variable → settings key → default roots),
enumerates against the manifest globs, skips unchanged files, and parses
everything into `~/.arionix/candidates.json`.

Two things happen here that are worth knowing about:

**Credentials are quarantined before anything is copied.** A record that matches
still appears — the person is entitled to know their memory holds a key — but its
body, statement and rationale are dropped, so the secret's only home stays the
original file. The report names the file and the finding kinds, never a value.
Classification is already decided for these: rubric Step 0, `secret_bearing`.

**Project instruction files are read from the working tree**, but only from
project directories that `resolve-projects.py` has verified — never by sweeping.
These are the only `asserted` records the collector finds, since a repo's
CLAUDE.md or AGENTS.md sits nowhere near a tool root. Each carries `committed`:
tracked means the code connector already has it, untracked means nothing else in
the organization does. Run step 2.5 first or this stage finds nothing.

Read the output summary. If a source you expected shows `absent`, check whether
the tool relocates its root — the resolution step is reported per source.

## Step 2 — Report

```bash
python3 scripts/report.py
python3 scripts/report.py --sample 3    # also print 3 full records as JSON
```

`--sample N` prints the structured records verbatim, which is what to reach for
when someone asks what the output actually looks like rather than how much of it
there is.

Show the user. If they only asked for a report, **stop here.** Mention that
publishing is available but don't do it uninvited.

## Step 2.5 — Resolve encoded project paths (only if the report flags them)

If **Scope evidence** shows records carrying only an `encoded_path` hint, those
are unpublishable as-is — the resolver can't use them and the skill won't invent
an id.

Records graded `container` are a different matter: the hint names the folder the
person keeps all their code in, so there is no correct answer and running the
resolver won't produce one. Don't ask them to fix it. Scope for those has to come
from the record's own text, or from asking what it applies to.

```bash
python3 scripts/resolve-projects.py
```

It matches by **re-encoding** candidate checkouts and comparing, then reads each
match's git remote. That is verification, not decoding — the encoded name can't
be decoded, because separators and real hyphens are the same character.

Anything it can't match it **asks you about in the terminal** — numbered, with
a suggested name to accept or correct and an optional path:

```
  1.  arionix-weight-poc      7 record(s)   relative to Downloads/
  2.  CSE-112                 3 record(s)   relative to Documents/

Which would you like to confirm?  enter = all  ·  1,3-5  ·  none
  >
  1. arionix-weight-poc  (7 record(s))
     name  [enter to accept · s to skip] >
     found /Users/ojas/code/arionix-weight-poc  (CLAUDE.md)
     use it?  [enter = yes · n = no · or paste a different path] >
     ok arionix-weight-poc  + will read its instruction files
```

**Holding enter is the intended path.** Blank confirms everything, accepts each
suggested name, and accepts a found path — every value is shown before it is
taken, and a wrong name yields an unresolvable scope rather than a wrong edge.

The path is never typed. The resolver indexes directories that actually hold a
CLAUDE.md / AGENTS.md and matches by name, folding case and separators, so
`CSE_112` matches `CSE-112`. One match is offered for a yes; several are listed
to pick from and enter takes none, because guessing between them is the one
thing worth refusing. Skipping keeps the encoded hint and leaves it flagged
unresolvable, which is a real answer.

With no terminal (a subagent, a pipe, cron) it writes `CONFIRM:` entries into the
map instead, for hand editing. `--no-interactive` forces that path.

Matches are written to `~/.arionix/project-map.json` with the verified path, and
picked up by every later `collect.py` run, which upgrades those hints to real
names and unlocks the project-instruction-file stage above.

Two directories sharing a git remote are **the same project**: both hints map to
one name, so both emit one scope string and the platform resolves them to one id.
Where remotes can't settle it, the merge is only proposed — written with a
`CONFIRM-MERGE:` prefix and treated as unset until a person strips it. Anything unmatched is
printed as a stub for the person to complete by hand — one line each, once.

Re-run `collect.py` afterwards so the upgraded hints land in the candidates.

## Step 3 — Classify

Two reasons to run this, and they stop at different points:

- **Classify only** — validate the taxonomy against a real store and stop. No
  submission, no file is modified. This is the right default while the schema
  sits at `0.x`, because it produces the two numbers the report cannot: kind
  distribution and exclusion mix.
- **Classify to publish** — the same pass, then steps 4 and 5.

Read `reference/tier-rubric.md`, then work through `~/.arionix/candidates.json`
one candidate at a time.

The derivability test runs **first** — if the graph or the repository could
already answer it, exclude it and move on. That filter removes more than
anything else.

For each survivor decide `kind`, `tier`, `target`, and fill `statement`,
`rationale`, `pam_component`, `claimed_scope`, `sharing`. Leave `authority`
alone — it is already set.

**Scope is required for `constraint`, `rejected_alternative`, `authority` and
`vocabulary`, and optional for the rest** — the assembler enforces it. For the
optional kinds, an empty `claimed_scope` is a real answer, not a gap: a method
preference applies to the publisher's work generally, and attaching whichever
repo happened to be mentioned in the body narrows it wrongly. Write `[]`
deliberately rather than omitting the key, which falls back to collected hints.

Scope has two halves: **`scope_breadth`** names the level — `application`,
`application_group`, `portfolio`, `enterprise` — and `claimed_scope` names the
thing on it. Only `enterprise` names nothing. Most claims sit in the middle two
rungs; forcing a choice between one repo and the whole company makes them wrong
either way.

Don't hand-resolve a missing scope. **Run `scripts/resolve-scopes.py`** (step
3.5) — it gathers what the batch already names, including the scope a sibling
claim from the same record chose, which is usually the answer.

Watch for **containers** (rubric Step 2.5): a status or architecture summary is
not one claim. It yields zero, one, or several, each with a `claim_index`, all
sharing the container's `content_hash`. Most yield nothing and count as
`derivable`.

Write your decisions to `~/.arionix/classified.json`:

```json
{
  "candidates": [
    { "content_hash": "<from candidates.json, unprefixed>",
      "kind": "rejected_alternative", "tier": 1, "target": "decision",
      "pam_component": "factual",
      "statement": "Do not use Redis for the SSO refresh lock in auth-gateway.",
      "rationale": "It dropped locks under reconnect storms; moved to Postgres advisory locks.",
      "claimed_scope": [{"text": "auth-gateway", "guess_kind": "service", "evidence": "body_reference"}],
      "sharing": "team", "still_true": "unknown", "origin": "user_stated" }
  ],
  "excluded": [
    { "reason": "derivable", "count": 41, "unit": "fragment",
      "note": "counted while decomposing containers" },
    { "reason": "person_sensitive", "count": 2, "unit": "fragment" }
  ]
}
```

Excluded entries carry **counts and reasons only** — never the content. `unit` is
required and must be the same throughout: count **fragments**, because a record
that yields one kernel out of nine is not an excluded record, and counting at
record level reads as zero exclusions. Emit a fragment entry even when it is
zero — an empty list carries no unit and so proves nothing either way.

`note` records *how* the count was taken, never what was excluded; it ships in
the payload, so a quoted excerpt there publishes what the exclusion withheld.

If several high-value records can't be scoped, batch the questions and ask once
rather than dropping them or guessing.

When the request was **classify only**, stop here and report:

```bash
python3 scripts/assemble.py --classified ~/.arionix/classified.json \
                            --mode report-only
python3 scripts/report.py --classified ~/.arionix/classified.json
```

`--mode report-only` stamps the payload so `submit.py` refuses it outright. The
run still validates the classification, so a rubric mistake surfaces here rather
than at publication time.

## Step 3.5 — Resolve scopes (the residue only)

Step 3 should have done this. You have the full bodies, every sibling claim, and
the extracted refs, so a scope-requiring claim leaving step 3 with an empty
`claimed_scope` is work deferred onto a script that knows less than you did.

This is for what genuinely survives that — an ambiguous target, or a
classification someone else wrote.

```bash
python3 scripts/resolve-scopes.py --auto     # attach what is not a guess
python3 scripts/resolve-scopes.py            # then decide the rest
```

`--auto` attaches only the strongest evidence tier: a target the claim's **own
statement names** which another claim in the batch already uses, inheriting that
claim's rung. That is verification rather than inference, so it needs no
confirmation. Everything else it leaves alone and reports.

`constraint`, `rejected_alternative`, `authority` and `vocabulary` need a
target, and the classifier frequently cannot supply one — it sees a claim and
the record it came from, and those disagree more often than not. A rule about
the platform's connector layer and a rule about one repository get written down
in the same file on the same afternoon.

It gathers every plausible target from the batch and asks, one keystroke each:

```
  1/2  constraint  · tier 1 · from prod-integration-plan.md
  Keep the kind table — do not replace it with SLM extraction in arionix-weight-core.

       1  arionix-weight-core   named in this claim, and used elsewhere in the batch
       2  AR-1195               referenced in the body
       3  arionix-weight-poc    the project this record was written in
       e  enterprise            everything; names no target
       t  type a name
       s  skip                  stays blocked
     > 1
       what is 'arionix-weight-core'?
         a  application
         g  application group
         f  portfolio
       > a
     ok arionix-weight-core  (application)
```

**There is no default on purpose.** For project names a default is safe — the
suggestion is read off a path and the person can see whether it fits. Here the
choice is a judgement about what the claim means, and inheriting the source
project would be wrong for exactly the broad claims that matter most.

`evidence` records which source answered — `named_in_statement`,
`batch_reference`, `source_project` or `asked_and_confirmed` — so review
downstream can tell an inherited scope from a stated one.

**Optionally** pre-fill the vocabulary from a GitHub organization, once per
machine:

```bash
python3 scripts/seed-vocabulary.py --org YOUR-ORG
```

Where a scope *is* a repository, picking the name beats typing it. That is the
whole benefit — it is a convenience, not a source of truth, and most people
running this skill will skip it. Many work across several organizations, or name
scopes that are not repositories at all.

**A name missing from the vocabulary is not a wrong name.** A real scope is
absent for many ordinary reasons: a package inside a monorepo, a service that is
not its own repository, a repo since renamed, an application group, a portfolio,
or simply an organization nobody seeded. `gh repo list` answers "no repository
has that name" — a much narrower claim than "no such thing" — so the resolver
offers near-misses in case of a typo and gets out of the way.

**Rungs are remembered.** Whether `arionix-platform` is a portfolio or an
application group is org structure, and nothing on a laptop can derive it — so
it is asked once and written to `~/.arionix/scope-vocabulary.json`. Every later
run offers the full vocabulary with each rung already attached, so the same
question is never asked twice and a scope named in one batch is available in the
next. When the publication endpoint exists this file becomes a cache of what the
graph already knows; until then it is the only vocabulary there is.

## Step 4 — Assemble and validate

```bash
python3 scripts/assemble.py --classified ~/.arionix/classified.json \
                            --publisher "$ARIONIX_PRINCIPAL"
```

Mints ids, builds the envelope, computes the digest, and validates locally.
**Do not proceed on a validation failure** — fix the classification and re-run.
The validator is strict on purpose: it rejects a `confidence` field, a tier-3
record, a pre-resolved scope reference, or a scope-requiring kind with nothing to
attach to.

## Step 5 — Confirm, then submit

Show the user a compact table — statement, kind, tier, scope, sharing — plus the
exclusion counts. Ask for confirmation. If they change a sharing level or drop
an item, edit `classified.json`, re-run step 4, and show them again.

```bash
python3 scripts/submit.py --dry-run     # verify first
python3 scripts/submit.py               # only after they say yes
```

On success the state file records each source hash so the next run skips it, and
`arionix_id` is written into the frontmatter of files that already have a
frontmatter block. Files without one keep their id in state instead — we never
introduce a frontmatter block to a person's hand-written notes.

---

## When a tool isn't recognized

An unknown tool is a **missing manifest entry, not a build task.** If you find
markdown files under a dotted directory in the home folder alongside a settings
file, propose an entry for `reference/manifest.json`:

```json
{ "tool": "example-cli", "display": "Example CLI",
  "env_root": ["EXAMPLE_HOME"], "default_roots": ["~/.example"],
  "settings": [], 
  "globs": { "human": ["EXAMPLE.md"], "agent": ["memory/**/*.md"] },
  "authorship": "path", "documented": false }
```

Show it to the user and let them confirm before writing it. Bounds on looking:
dotted directories **directly** under the home folder, containing both markdown
and a settings file, depth 3 at most. Do not sweep the home directory —
unbounded scanning of an engineer's machine is a privacy problem before it is a
technical one.

Unrecognized frontmatter keys are reported by `report.py`. A recurring one is a
missing row in `reference/alias-table.json` — propose the row, same confirmation.

---

## Files

| Path | What it is |
|---|---|
| `reference/manifest.json` | Where each tool keeps things. **Data** — a new tool is an entry, not code. |
| `reference/alias-table.json` | Ten frontmatter spellings → four meanings. |
| `reference/tier-rubric.md` | The classification rules. Read before step 3. |
| `reference/payload.schema.json` | The contract, documented. |
| `scripts/collect.py` | Resolve, enumerate, diff, parse, tag. |
| `scripts/resolve-projects.py` | Match encoded project paths to real directories. |
| `scripts/resolve-scopes.py` | Ask what a scope-requiring claim applies to. |
| `scripts/seed-vocabulary.py` | Seed real application names from a GitHub org. |
| `scripts/report.py` | Report-only rendering. |
| `scripts/assemble.py` | Envelope, digest, local validation. |
| `scripts/submit.py` | Submit, then state and id write-back. |

Environment: `ARIONIX_ENDPOINT`, `ARIONIX_TOKEN`, `ARIONIX_PRINCIPAL`.
No network access is needed for steps 1–4.

## Defaults when unsure

Exclude rather than include · `sharing: personal` · `origin: unknown` ·
`still_true: unknown` · for scope, work the evidence order then reach for a
broader rung — never guess a name.
