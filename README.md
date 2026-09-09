# publish-context

A Claude Code skill that reads what your AI assistants have written down about
your work, keeps only what no other system already knows, and hands it to the
Arionix context graph — with your explicit consent and nothing you didn't see.

**It publishes nothing on its own.** Report-only is the default, and today it is
the only path that completes: the publication endpoint is not built yet. What
works now is reading a machine, classifying what's there, and telling you what
it found.

---

## What it does to your machine

Worth reading before you install it, because it reads your notes.

**It reads** markdown instruction and memory files belonging to GenAI CLIs —
`~/.claude/projects/*/memory/`, a repo's `CLAUDE.md`, `AGENTS.md`, and the
equivalents for six other tools. Locations come from `reference/manifest.json`;
nothing is discovered by sweeping your home directory.

**It writes** to `~/.arionix/` only: `candidates.json` (what it read),
`project-map.json` (your confirmed project names), `publish-state.json`. Your
memory files are never modified — the one exception is writing an `arionix_id`
into frontmatter *after* a successful publication, which cannot happen yet.

**It sends nothing anywhere.** No network call is made in any of the four
working stages. `submit.py` is the only script that would, and it has no
endpoint to reach.

**Credentials are quarantined before they are copied.** If a memory file
contains something shaped like an API key, the record still appears in the
report — you should know your notes hold a key — but its body, statement and
rationale are dropped, so the secret's only home stays the original file. You
get the filename and a line number, never the value:

```
Quarantined — credential detected
  ! keycloak-jwt-key-sync.md
      assigned_secret, jwt  · line 14, 31

  This is a floor, not a guarantee: prefixed tokens, PEM headers, JWTs and
  assigned literals. A bare high-entropy string with no marker will pass.
```

**Anything about a colleague is excluded, not published.** The rubric drops
anything evaluative about a named person other than you, and reports the count
without the content.

---

## Install

```bash
git clone https://github.com/okayojas/publish-context.git ~/.claude/skills/publish-context
```

That's it — user-scope skill, available in every project on the machine. No
dependencies. Python 3.8 or newer; PyYAML is used if present and a built-in
fallback parser handles the frontmatter subset these files actually use if not.

## Look before it reads anything

```bash
cd ~/.claude/skills/publish-context
python3 scripts/collect.py --dry-run
```

Resolves every store and lists the exact files it *would* open, without opening
one. If a tool you use reports `not installed`, its root has probably been
relocated — check the environment variable or settings key for it in
`reference/manifest.json`. That's a data fix, not a code change.

## Run it

```bash
python3 scripts/collect.py --all
python3 scripts/resolve-projects.py     # hold enter; see below
python3 scripts/collect.py --all        # second pass reads your project files
python3 scripts/report.py
```

The second `collect` is not redundant. `resolve-projects` is what turns an
encoded project directory into a real project name, and that name is what lets
the collector find your repos' `CLAUDE.md` files — the only human-authored
memory it ever reaches.

### The confirmation step

Some tools name their memory directory after the filesystem path, flattened:

```
C:\Users\Ojas\Downloads\arionix-weight-poc
  ->  c--Users-Ojas-Downloads-arionix-weight-poc
```

That can't be decoded — separators and real hyphens are the same character — but
it can be *matched*, by re-encoding candidate directories and comparing. Where
matching fails, usually because the folder was moved or deleted, it asks you:

```
  1.  arionix-weight-poc             7 record(s)   relative to Downloads/
  2.  CSE-112                        3 record(s)   relative to Documents/

Which would you like to confirm?  enter = all  ·  1,3-5  ·  none

  1. arionix-weight-poc  (7 record(s))
     name  [enter to accept · s to skip] >
     found /Users/ojas/code/arionix-weight-poc  (CLAUDE.md)
     use it?  [enter = yes · n = no] >
     ok arionix-weight-poc  + will read its instruction files
```

**Holding enter is the intended path.** Blank confirms everything and accepts
each suggested name and found path — every value is shown before it's taken, and
a wrong name yields an unresolvable reference rather than a wrong one. You never
type a path: directories that actually hold a `CLAUDE.md` are indexed and matched
by name. Where two match, it lists both and takes neither unless you pick, because
guessing between them is the one thing worth refusing.

Then re-run `collect.py --all` and the report ends with what each project will
contribute.

## What you get

```
Authorship
  asserted    3  ███···················  human-written
  inferred   17  ███████████████████···  agent-written

  3 from project instruction files (read from the working tree)
      tracked       0  the repo has these — lean derivable
      untracked     3  invisible to every other system

Unsettled — may be `provisional`
  ? prod-integration-plan.md  · 56d old
      awaiting
  ? b2c-launch.md  · 43d old
      blocked_on
```

Eight measures in all: which stores resolved and how, the authorship split, how
much arrives pre-labelled by its tool, activation coverage, scope evidence,
records that say their own subject is still open, timestamp provenance, and any
frontmatter key the alias table doesn't recognize.

The report is safe to share — counts and distributions, no record bodies.
`~/.arionix/candidates.json` is **not**: it holds the verbatim text of everything
read. If a teammate asks how extraction went, send the report.

---

## How it's built

**One parser, not a reader per tool.** Across ~20 surveyed CLIs, instructions are
markdown with optional YAML frontmatter — no exceptions. Only the *locations* and
the frontmatter *key names* differ, so every tool-specific fact lives in data:

| File | Contents |
|---|---|
| `reference/manifest.json` | Where each tool keeps things. A new tool is an entry, not code. |
| `reference/alias-table.json` | Ten frontmatter spellings mapped to four meanings |
| `reference/tier-rubric.md` | Classification rules, with worked examples from real stores |
| `reference/payload.schema.json` | The publication contract |

| Script | Stage |
|---|---|
| `collect.py` | Resolve → enumerate → diff → parse → tag |
| `resolve-projects.py` | Match encoded project paths to real directories, by re-encoding |
| `report.py` | Report-only rendering |
| `assemble.py` | Envelope, digest, local validation |
| `submit.py` | Submit, then state and id write-back *(no endpoint yet)* |

Adding a tool is roughly six lines of JSON. If the skill meets a CLI it doesn't
recognize, it proposes the manifest entry and waits for you to confirm it.

## Four rules it won't break

1. **Authorship is never guessed.** `authority` comes from a file's location via
   one of four manifest rules. A model judging that text "reads as model-written"
   is exactly the inference this design refuses. Unknown source ⇒ `inferred`.
2. **Identifiers are never invented.** References leave your machine
   *unresolved*. An unresolved reference is honest; a wrong one is a bad edge in
   a graph with a citation attached to it.
3. **Exclusion is one-directional.** Anything dropped locally never existed in
   the system. When uncertain, exclude.
4. **You see everything before it leaves.** No silent submission, ever.

---

## Status, honestly

Schema `0.3`, rubric `v9`, held at `0.x` on purpose.

**Exercised against four real stores** — 95 records across three people, macOS
and Windows. Nearly every bug this thing has had was found by someone running it
on their own machine rather than by a fixture, which is why the report leads with
what it *couldn't* determine.

**Only Claude Code has met a real store.** The manifest entries for Gemini CLI,
Codex, Copilot, Windsurf, OpenCode and the shared `AGENTS.md` convention are
written from first-party documentation and have never been exercised against an
actual install. Cursor is unreachable by design — its memory is server-side.

If you use any of those, your run is the first test of that entry — and the
report tells you which way it went. A store that resolves but reads nothing
prints what markdown *is* there that the globs missed:

```
  ● OpenAI Codex CLI            0 files,   0 changed  [default_root]
      ↳ 2 markdown file(s) here that the globs did not match:
          notes.md
          prompts/review.md
      that is a manifest gap, not an empty store — worth reporting
```

No such line means the store is genuinely empty and nothing is wrong. If you do
see one, the fix is usually a glob in `reference/manifest.json` — open an issue
with those paths and it is a six-line data change.

**Publication is not built.** `submit.py` will refuse a report-only payload and
exit if `ARIONIX_ENDPOINT` is unset, which it is for everyone. Stages 1–4 are
complete and useful on their own; stage 5 is waiting on the ingress.

**Two of the seven `kind` values have never been emitted.** `vocabulary` and
`external_reference` produced nothing across 95 records. They may not be real
categories, and the taxonomy is a live question rather than a settled one — which
is what report-only mode exists to answer.

**Secret detection is a floor.** Prefixed tokens, PEM headers, JWT shape and
assigned literals. Entropy scanning is deliberately omitted: it is noisy enough
to train people to ignore the flag, which is worse than not flagging.

## License

Not yet chosen. Ask before redistributing outside the organization.
