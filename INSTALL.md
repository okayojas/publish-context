# Installing publish-context on another machine

## 1. Copy it

Move `publish-context-skill.tar.gz` to the target machine, then:

    mkdir -p ~/.claude/skills
    tar -xzf publish-context-skill.tar.gz -C ~/.claude/skills

That's the whole install. It's a user-scope skill, so it's available in every
project on that machine.

## 2. Look before it reads

    python3 ~/.claude/skills/publish-context/scripts/collect.py --dry-run

Resolves every store and lists what it *would* read without opening a single
file. Check this first on a machine with real content — if a tool you use shows
`not installed`, its root has probably been relocated and the manifest entry
needs the right environment variable or settings key.

## 3. Run the report

In Claude Code:

    /publish-context

Or directly:

    python3 ~/.claude/skills/publish-context/scripts/collect.py --all
    python3 ~/.claude/skills/publish-context/scripts/report.py

Report-only is the default. Nothing is submitted, and no network is used.

## Requirements

Python 3.8+. PyYAML is used if present and a built-in fallback parser handles
the frontmatter subset these files actually use if it isn't.

## What to send back

`~/.arionix/candidates.json` holds the extraction result. Before sharing it,
note that it contains the **verbatim body** of every record it read. The report
output alone is usually enough — it carries the counts and distributions
without the content.
