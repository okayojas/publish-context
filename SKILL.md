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

## Step 3 — Classify

Only when publishing. Read `reference/tier-rubric.md`, then work through
`~/.arionix/candidates.json` one candidate at a time.

The derivability test runs **first** — if the graph or the repository could
already answer it, exclude it and move on. That filter removes more than
anything else.

For each survivor decide `kind`, `tier`, `target`, and fill `statement`,
`rationale`, `pam_component`, `claimed_scope`, `sharing`. Leave `authority`
alone — it is already set.

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
    { "reason": "derivable", "count": 9 },
    { "reason": "person_sensitive", "count": 2 }
  ]
}
```

Excluded entries carry **counts and reasons only** — never the content.

If several high-value records can't be scoped, batch the questions and ask once
rather than dropping them or guessing.

## Step 4 — Assemble and validate

```bash
python3 scripts/assemble.py --classified ~/.arionix/classified.json \
                            --publisher "$ARIONIX_PRINCIPAL"
```

Mints ids, builds the envelope, computes the digest, and validates locally.
**Do not proceed on a validation failure** — fix the classification and re-run.
The validator is strict on purpose: it rejects a `confidence` field, a tier-3
record, or a pre-resolved scope reference.

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
| `scripts/report.py` | Report-only rendering. |
| `scripts/assemble.py` | Envelope, digest, local validation. |
| `scripts/submit.py` | Submit, then state and id write-back. |

Environment: `ARIONIX_ENDPOINT`, `ARIONIX_TOKEN`, `ARIONIX_PRINCIPAL`.
No network access is needed for steps 1–4.

## Defaults when unsure

Exclude rather than include · `sharing: personal` · `origin: unknown` ·
`still_true: unknown` · omit an ambiguous scope entry rather than guessing it.
