# publish-context — technical specification

What the code does, why it is shaped this way, and where each decision lives.
Written to be read instead of the source, though every section names the file
and function so you can check it.

Schema `0.3` · rubric `v11` · manifest `v1` · alias table `v1`

---

## 1. The problem

A person's AI assistants accumulate things no other system in an organization
knows: an approach that was tried and abandoned, a rule that makes generated
work wrong in ways CI cannot catch, the fact that a documented setup has never
existed in this checkout. It sits in markdown files on one laptop.

Moving that into a governed context graph is not a file-sync problem. Three
things make it hard:

- **Most of it is worthless to anyone else.** A fact the repository already
  answers becomes a staler, un-attributed duplicate of something the platform
  holds with provenance.
- **Some of it is actively dangerous.** Credentials, evaluations of colleagues,
  plans that were never approved.
- **Nothing on the machine can resolve identity.** A memory says "the weight
  path"; the graph has canonical ids. The laptop has no graph access and must
  not invent one.

The design answers all three the same way: **extract deterministically, judge
with a model only where judgement is required, and never assert what the data
cannot support.**

---

## 2. Pipeline

Five stages. Stages 1–4 need no network. Stage 5 has no endpoint yet, so
report-only is the only complete path today.

```
  ┌──────────────┐
  │ collect.py   │  resolve roots → enumerate → diff → parse → tag
  └──────┬───────┘  writes ~/.arionix/candidates.json
         │
  ┌──────┴────────────────┐
  │ resolve-projects.py   │  encoded dir names → real project names (asks)
  └──────┬────────────────┘  writes ~/.arionix/project-map.json
         │                   ── re-run collect.py to pick up the names ──
  ┌──────┴───────┐
  │ report.py    │  thirteen sections; safe to share (counts, no bodies)
  └──────┬───────┘
         │
  ┌──────┴────────────────┐
  │ the model classifies  │  reads reference/tier-rubric.md
  └──────┬────────────────┘  writes ~/.arionix/classified.json
         │
  ┌──────┴────────────────┐
  │ resolve-scopes.py     │  what does this claim apply to? (asks)
  └──────┬────────────────┘
         │
  ┌──────┴───────┐
  │ assemble.py  │  mint ids → build envelope → digest → validate
  └──────┬───────┘  writes ~/.arionix/payload.json
         │
  ┌──────┴───────┐
  │ submit.py    │  POST → state → write arionix_id back to source
  └──────────────┘  no endpoint yet; refuses report-only payloads
```

**Only one stage uses a model.** Classification is genuine judgement about what
a claim means. Everything else is deterministic, and where determinism runs out
the code asks a person rather than guessing.

### Files on disk

| Path | Written by | Contents |
|---|---|---|
| `~/.arionix/candidates.json` | `collect.py` | every record read, **including verbatim bodies** |
| `~/.arionix/project-map.json` | `resolve-projects.py` | encoded dir → `{name, path, remote, verified}` |
| `~/.arionix/scope-vocabulary.json` | `resolve-scopes.py` | scope → `{guess_kind, scope_breadth, uses, source}` |
| `~/.arionix/classified.json` | the model | classification decisions |
| `~/.arionix/payload.json` | `assemble.py` | the validated envelope |
| `~/.arionix/publish-state.json` | `submit.py` | per-file hashes, minted ids, reservations |

`candidates.json` is the sensitive one — it holds the full text of everything
read. The report is the shareable artefact.

---

## 3. `collect.py` — extraction

1089 lines, the largest component. Its job: find every store, read what changed,
and tag each record with facts that are **derived, never judged**.

### 3.1 Root resolution — `resolve_root()`

Three steps, first hit wins, and which one answered is reported:

1. An environment variable from `env_root` (e.g. `CLAUDE_CONFIG_DIR`)
2. A settings key inside a default root (e.g. `settings.json` → `autoMemoryDirectory`)
3. The first `default_roots` entry that exists

A tool whose root cannot be found is `absent`, not an error.

### 3.2 The one-parser bet

Across ~20 surveyed CLIs, instructions are **markdown with optional YAML
frontmatter, without exception.** Only two things differ: where files live, and
what the frontmatter keys are called. So there is one parser and two data files:

- `reference/manifest.json` — locations, per tool
- `reference/alias-table.json` — ten frontmatter spellings → four meanings

Adding a tool is ~6 lines of JSON. Eight tools are described; `cursor` is listed
under `unreachable` because its memory is server-side.

**Manifest keys** (per tool): `tool`, `display`, `env_root`, `default_roots`,
`settings`, `globs.human`, `globs.agent`, `project_globs`, `exclude`,
`authorship`, `scope_from_path`, `timestamp_key`, `section_heading`,
`human_filenames`, `patch_globs`, `documented`.

**Alias buckets**: `activation_pattern` (7 spellings), `activation_inclusion`
(4), `statement` (3), `native_type` (4), plus `timestamp`, `identity`, `ignore`.
Nesting is handled by `_flatten()`, which matches on both dotted path and leaf
name — Claude Code puts `type` under `metadata:`.

### 3.3 Authorship — `authorship_for()`

`authority` is `asserted` (human-written) or `inferred` (agent-written), and it
comes from **file location only**, via one of four manifest rules: `path`,
`filename`, `section`, or the fail-safe default.

> A model judging that text "reads as model-written" is exactly the inference
> this design refuses. Unknown source ⇒ `inferred`.

This matters downstream: `asserted` content can reach canonical in the graph,
`inferred` content is recalibrated.

**Empirically, `asserted` was 0 across 75 records on three machines** — not
because nobody writes instructions, but because `globs.human` is relative to a
tool root while a repository's `CLAUDE.md` lives in the working tree. Hence
`project_globs` (§3.7).

### 3.4 Change detection

`source_hash` over the file, `content_hash` over each chunk. `collect.py --all`
ignores state and re-reads everything; the bare form skips unchanged files.
Landing dedups on `content_hash`, so a stable hash is load-bearing.

### 3.5 Extraction that grades itself

Every derived field carries how good it is. This is the spine of the design.

**References** — `extract_refs(statement, rationale, body)` returns
`{text, kind, zone}` where `kind ∈ {ticket, url, path}` and `zone ∈ {statement,
rationale, body}`, strongest zone winning on duplicates. A ticket id in the
statement is the author naming the subject *while making the claim*; the same
string buried in a 27,000-character body is a mention.

URLs are rejected unless the host is routable — `_routable()` screens loopback,
RFC 1918 ranges, the RFC 6761/6762 reserved TLDs (`.test .local .localhost
.invalid .example`) and single-label hosts. *One store counted
`http://localhost:8080` and `http://agent-writeback-api:8007` as resolvable
references, inflating its attachment metric to 19/25.*

**Scope hints** — `scope_from_path` pulls a project directory out of the store
path, then it is graded three ways:

| Grade | Meaning | Fixable? |
|---|---|---|
| `name` | a real project name | already usable |
| `encoded_path` | a flattened filesystem path | yes — `resolve-projects.py` |
| `container` | a folder holding several checkouts | **no** — there is no right answer |

*One store had 32 of 33 records hinting `-Users-<name>-Developer`: the folder
holding every checkout. Not unresolvable — resolvable to something meaningless,
which is worse, because a person will try to fix it.*

**Subjects** — `detect_subjects()` finds `@handles` and emails, then drops
reserved TLDs and RFC 2606 example domains. `subjects` drives per-person
encryption and erasure-index registration downstream, so a false positive
creates a data-subject obligation for someone who is not a party. *Two stores
failed differently: one reported an open-source maintainer merely cited, the
other four `.test` fixtures.*

**Timestamps** — `_norm_ts()` canonicalises to UTC ISO because PyYAML resolves
`2026-08-02T14:31:00Z` to a `datetime` while the fallback parser returns a
string. Same instant, two spellings, and `asserted_at` feeds validity.

### 3.6 Two detectors that run before judgement

Both exist because asking a model to notice something buried in several thousand
characters is the judgement call the rubric refuses everywhere else.

**`scan_secrets()`** — 15 patterns: prefixed tokens (`sk-`, `ghp_`,
`github_pat_`, `AKIA`, `xox[baprs]-`, `glpat-`, `AIza`, `hf_`, `npm_`,
`dop_v1_`, `SG.`, `pypi-`), PEM private-key headers, JWT shape, and assigned
literals next to secret-ish names with a placeholder filter.

On a hit the record **still appears** — the person is entitled to know their
notes hold a key — but `body`, `statement` and `rationale` are dropped. Findings
carry `{kind, line}`, never a value, so the report stays safe to paste.

Entropy scanning is **deliberately absent**: noisy enough to train people to
ignore the flag, which is worse than not flagging. The docs say this is a floor.

**`detect_provisional()`** — seven signals with line numbers:
`status_unsettled`, `awaiting`, `blocked_on`, `not_settled`, `draft_marker`,
`pending_ref`, `self_dated`. A signal, never a verdict.

> A plan whose own status line read *"awaiting boss sign-off as of 2026-07-14"*,
> in a file 54 days old, published four tier-1 constraints with no expiry.

### 3.7 Project instruction files

`project_globs` reads `CLAUDE.md`, `AGENTS.md` and equivalents **from the
working tree** — but only from directories a verified match supplied, never by
sweeping. Each record carries `committed`, from `git ls-files --error-unmatch`:

- `true` — tracked, so the code connector already holds it → lean `derivable`
- `false` — untracked or gitignored → **invisible to every other system**, the
  case this stage exists for
- `null` — not a checkout, or git could not say

These are the only `asserted` records the pipeline produces.

### 3.8 Resolved-but-empty

When a store resolves and reads nothing, `unmatched_markdown()` counts markdown
under that root outside the globs. Silence means genuinely empty; a list means a
manifest gap with the evidence attached. **Six of eight manifest entries have
never met a real install**, so this is how someone else's run tests them.

---

## 4. `resolve-projects.py` — encoded directory names

Some tools name a memory directory after the filesystem path, flattened:

```
C:\Users\Ojas\Downloads\arionix-weight-poc
  ->  c--Users-Ojas-Downloads-arionix-weight-poc
```

That **cannot be decoded** — separators and real hyphens are the same character,
so segmentation is genuinely ambiguous. But the transform is one substitution,
so candidate directories can be *encoded and compared*. A match is verification,
not inference.

Three refinements, each from a real failure:

- **`holds_checkouts()`** refuses to map a directory containing two or more
  other checkouts. Without it, `~/Developer` would have mapped to a scope named
  "Developer" and stamped 31 records — arriving as `evidence: project_map`,
  reading as *verified*.
- **`lookup_dirs()`** tries the full name then progressively shorter suffixes,
  because a nested source path yields the whole flattened tail while the
  directory is named after the last segment only.
- **`clean_path()`** strips quotes, whitespace and trailing separators. Windows
  Explorer's "Copy as path" wraps in double quotes, and a real report was a
  valid path rejected as "not a directory" for exactly that reason.

Two directories sharing a git remote are merged to one scope. Where remotes
cannot settle it, the merge is *proposed* as `CONFIRM-MERGE:` and treated as
unset until a person strips the prefix.

**Interaction design**: blank means *all*, paths are offered rather than typed
(directories holding an instruction file are indexed and matched by name), and
where several match it lists them and takes none. Holding enter is the intended
path.

---

## 5. Classification — the model's stage

Governed by `reference/tier-rubric.md`. Steps run in order:

| Step | Test |
|---|---|
| 0 | **Quarantined?** `secret_detected` → `secret_bearing`, nothing to classify |
| 1 | **Derivable?** could the graph, repo or tracker already answer this? Highest-volume filter |
| 2 | `session_local`, `provisional`, `machine_local`, `person_sensitive` |
| 2.5 | **Claim or container?** decompose, and a *gate travels with its kernels* |
| 3 | Assign `kind`, `tier`, `target`, `scope_breadth` |
| 4 | Fill `statement`, `rationale`, `pam_component`, `claimed_scope`, `sharing` |

### 5.1 The seven kinds

| Kind | Tier | Target | Scope required |
|---|---|---|---|
| `rejected_alternative` | 1 | `decision` | **yes** |
| `constraint` | 1 | `decision` | **yes** |
| `authority` | 1 | `decision` | **yes** |
| `preference` | 1 | `preference_rule` | no |
| `playbook` | 2 | `document` | no |
| `vocabulary` | 2 | `alias_proposal` | **yes** |
| `external_reference` | 2 | `document` | no |

Scope is required only where its absence breaks meaning. An unscoped constraint
is a prohibition applying to everything; a method preference pinned to whichever
repo the body mentioned is *narrowed wrongly*. Same field, opposite failure.

**`vocabulary` and `external_reference` have never been emitted** across 95
records from four people. They may not be real categories.

### 5.2 Containers

A `project`-typed record is usually a **derivable wrapper around a non-derivable
kernel**. It yields zero, one or several claims, each with a `claim_index`, all
sharing the container's `content_hash`.

> A pass over 17 records produced 36 claims and excluded none. Re-run with Step 1
> enforced, the same records produced **50**. The failure was two-sided: it
> under-excluded *and* under-mined. One plan went 4 → 11.

**A gate is not a fragment.** If a container carries a precondition — an
approval that has not landed, a freeze — that gate qualifies every kernel under
it. Publishing the kernels and dropping the gate inverts the record's meaning.

### 5.3 Scope: a level and a target

`scope_breadth` names the rung, `claimed_scope` names the thing on it:

| Level | `claimed_scope` |
|---|---|
| `application` | the app, service or repository |
| `application_group` | the group |
| `portfolio` | the portfolio |
| `enterprise` | **empty** — it means everything |

Only `enterprise` names nothing. Every narrower rung must say what the thing is,
or it asserts a breadth with no subject. A tier-1 claim at `portfolio` or
`enterprise` requires a rationale.

The ladder exists because most claims are neither one application nor the whole
company, and forcing that choice makes them wrong either way.

### 5.4 Where a scope comes from

Evidence order, strongest first:

1. A name in this claim's **own statement** that the batch also uses
2. A scope a **sibling claim from the same record** chose
3. A **ticket or path** the collector extracted
4. The **source project** — a candidate, never a default

Four is a candidate rather than a default because inheriting is right and wrong
for reasons the data does not carry: *"Do not extend Slice 3"* written in
`project-vantage` **is** about `project-vantage`; *"one connector per external
system"* written the same afternoon is about the platform.

**Resolve this during classification.** The rubric previously said "omit, or ask"
while the validator required a target, so a classifier followed instructions and
eight claims failed. `resolve-scopes.py` is the fallback for the residue.

---

## 6. `resolve-scopes.py` — the fallback

Finds claims that would fail validation for want of a target, gathers candidates
(§5.4 plus the remembered vocabulary), and asks: pick a target, pick a rung.

`--auto` attaches only the one tier that is verification rather than inference —
a target the claim's own statement names, which another claim in the batch
already uses, inheriting that claim's rung.

**The vocabulary remembers.** Whether `arionix-platform` is a portfolio or an
application group is org structure no laptop can derive, so it is asked once and
written to `scope-vocabulary.json`. A person answering the same question across
three runs will eventually answer it differently.

`seed-vocabulary.py --org ORG` optionally pre-fills from a GitHub organization.
**Absence from the vocabulary is not evidence a name is wrong** — a real scope
is missing for many ordinary reasons: a package inside a monorepo, a service
that is not its own repository, a renamed repo, an application group. `gh repo
list` answers "no repository has that name", a much narrower claim.

---

## 7. `assemble.py` — the contract

Merges the classification with the collector's intermediate, mints ids, builds
the envelope, computes a digest, and validates. **Never submits.**

### 7.1 Stable ids

```
published id  >  id reserved by an earlier assemble  >  mint and reserve
```

Keyed on `(source_path[#chunk][@claim_index])`, reservations held in
`state["pending_ids"]`. Without this, assembling twice produced different ids
for the same record — and since landing dedups on `memory_id`, a submission that
failed *after* landing would republish as a second object.

Duplicate `(content_hash, claim_index)` is rejected: two claims from one
container both defaulting to 0 would mint the same id, and landing would keep
one silently.

### 7.2 Two validation passes

**Input boundary** (9 checks) runs against the classifier's *own* output before
anything is rebuilt — a model that invented a `canonical_id` or attached a
`confidence` would otherwise be silently corrected, hiding rubric drift.

**Envelope validation** (36 checks) runs against the built payload. Notable:

- `confidence` is **forbidden** — asserted content is authoritative, inferred is
  recalibrated in code
- `tier` must be 1 or 2; tier 3 is excluded locally and never sent
- every `claimed_scope` entry must carry `resolution: "unresolved"` and
  `canonical_id: null`
- a scope-requiring kind with no usable target is rejected; `container` and
  `encoded_path` grades do not count
- `excluded` entries need a `unit` (`record` or `fragment`), uniform across
  entries, and `note` is capped at 240 chars and rejected if it quotes a long
  passage — a note ships in the payload, so an excerpt publishes what the
  exclusion withheld

Errors are one line with a bracketed tag; each tag's remedy prints once as a
footnote. *Eight blocked claims previously produced eight copies of the same
four-line remedy.*

### 7.3 The envelope

```json
{
  "schema_version": "0.3",
  "export_id": "<uuid>",
  "store_instance": "<user>.<host>",
  "exported_at": "<iso8601>",
  "generator": { "skill": "publish-context", "version": 11, "mode": "report-only" },
  "envelope_digest": "sha256:…",
  "candidates": [ … ],
  "retired": [ … ],
  "excluded": [ { "reason": "derivable", "count": 41, "unit": "fragment" } ]
}
```

`store_instance` namespaces two people's stores. The digest covers the canonical
serialization — sorted keys, no incidental whitespace.

---

## 8. `report.py` — the deliverable

Thirteen sections, driven by what the data contains: **Stores**, **Quarantined**,
**Authorship**, **Source labelling**, **Activation**, **Scope evidence**,
**Unsettled**, **Timestamps**, **Unrecognized frontmatter keys**, and with
`--classified`: **Taxonomy fit**, **Attachment ceiling**, **Exclusion mix**,
**Set aside**.

Two design rules:

**The report states what it could not determine.** A store that resolves and
reads nothing says whether that is an empty store or a manifest gap. Zero
project instruction files says whether no path was supplied or no file exists.

**No number is printed that the unit cannot support.** The exclusion percentage
appears only at fragment level, because dividing claims by records is
meaningless. With no entries at all it names the ambiguity rather than resolving
it — *a previous version diagnosed a skipped filter from a number that could not
support that conclusion.*

---

## 9. `submit.py` — not yet reachable

POSTs the payload, then on 2xx only: records each source hash in state, and
writes `arionix_id` into the frontmatter of files that **already have** a
frontmatter block — never introducing one to hand-written notes.

Refuses a payload marked `report-only`. Exits 2 if `ARIONIX_ENDPOINT` is unset,
which it is for everyone: **the ingress does not exist.** `POST /context/publish`,
the `context-router` consumer for preference rules, and the additive ontology
publish extending `DECIDES` target types are all unbuilt.

---

## 10. Four invariants

1. **Authorship is never guessed.** From file location, via one of four manifest
   rules. Unknown ⇒ `inferred`.
2. **Identifiers are never invented.** References leave the machine unresolved.
   An unresolved reference is honest; a wrong one is a bad edge in the graph with
   a citation attached.
3. **Exclusion is one-directional.** Anything dropped locally never existed in
   the system. When uncertain, exclude.
4. **The person sees everything before it leaves.** No silent submission.

---

## 11. Two recurring bug classes

Worth knowing before changing anything, because both have recurred repeatedly.

**Code asserting what the data cannot support.** Nine instances: a cwd git
remote used as scope, an attachment ceiling counting every hint, a truncation
check rejecting `RAG` as a word fragment, a zero-exclusion count read as a
skipped filter, `verified: true` on a name that had only been looked up. One
occurred *inside a guard written to prevent it.*

**Absence is not a value.** Three instances: empty vs absent `claimed_scope` (a
global rule stamped with whichever folder it lived in), empty vs record-zero
`excluded`, unscoped-because-universal vs unscoped-because-unknown. Each needed
an explicit marker rather than a smarter default.

---

## 12. Status

**Exercised against four real stores** — 95 records, three people, macOS and
Windows. Nearly every bug was found by someone running it on their own machine
rather than by a fixture.

**Only Claude Code has met a real store.** Gemini CLI, Codex, Copilot, Windsurf,
OpenCode and the shared `AGENTS.md` convention are written from first-party
documentation and untested. Cursor is unreachable by design.

**Publication is not built.** Stages 1–4 complete and useful; stage 5 waits on
the ingress.

**Python 3.8+, no dependencies.** PyYAML is used when present; a built-in
fallback parser handles the frontmatter subset these files use when it is not.

### Where to change what

| To change | Edit |
|---|---|
| Support a new CLI | `reference/manifest.json` — an entry, not code |
| A frontmatter key is unrecognized | `reference/alias-table.json` — a row |
| Classification rules | `reference/tier-rubric.md`, then bump `RUBRIC_VERSION` |
| The contract | `reference/payload.schema.json` **and** `validate()` — the schema documents, the validator enforces |
| What the skill does | `SKILL.md` |
