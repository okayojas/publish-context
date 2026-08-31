# publish-context

A Claude Code skill that reads memory and instructions out of every GenAI CLI on
a machine, classifies what is worth sharing, and publishes it to the Arionix
context graph.

**Report-only by default.** It submits nothing unless explicitly asked.

## Install

Clone straight into the skills directory:

    git clone <this-repo> ~/.claude/skills/publish-context

Then, in Claude Code:

    /publish-context

## Look before it reads

On a machine with real content, resolve and list what *would* be read without
opening a single file:

    python3 ~/.claude/skills/publish-context/scripts/collect.py --dry-run

If a tool you use reports `not installed`, its root has probably been relocated
— check the environment variable or settings key in `reference/manifest.json`.
That is a data fix, not a code change.

## How it is put together

One parser, not a reader per tool. Across ~20 surveyed CLIs, instructions are
markdown with optional YAML frontmatter without exception; only the *locations*
and the frontmatter *key names* differ. So all tool-specific knowledge lives in
two data files:

| File | Contents |
|---|---|
| `reference/manifest.json` | Where each tool keeps things — a new tool is an entry, not code |
| `reference/alias-table.json` | Ten frontmatter spellings mapped to four meanings |
| `reference/tier-rubric.md` | Classification rules, with worked examples |
| `reference/payload.schema.json` | The publication contract (schema 0.3) |

| Script | Stage |
|---|---|
| `scripts/collect.py` | Resolve → enumerate → diff → parse → tag |
| `scripts/report.py` | Report-only rendering |
| `scripts/assemble.py` | Envelope, digest, validation |
| `scripts/submit.py` | Submit, then state and id write-back |

## Four rules it will not break

1. **Authorship is never guessed.** `authority` comes from a file's location via
   one of four manifest rules. A model judging that text "reads as model-written"
   is exactly the inference this design refuses. Unknown source ⇒ `inferred`.
2. **Identifiers are never invented.** Scope references leave the machine
   unresolved; the platform resolves them.
3. **Exclusion is one-directional.** Anything dropped locally never existed in
   the system. When uncertain, exclude.
4. **Nothing is submitted without confirmation.**

## Requirements

Python 3.8+. PyYAML is used if present; a built-in fallback parser handles the
frontmatter subset these files use if it is not.

## Status

Schema `0.3`, held at `0.x` deliberately. The seven-value `kind` taxonomy is a
hypothesis derived from reasoning, not from data — report-only mode exists to
produce the corpus that settles it.
