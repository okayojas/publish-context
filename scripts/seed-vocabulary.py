#!/usr/bin/env python3
"""
Optionally pre-fill the scope vocabulary from a GitHub organization.

Entirely optional, and not a source of truth. Most people running this skill
will not have an organization worth seeding from, or will work across several,
or will name scopes that are not repositories at all — so the vocabulary stays
a convenience for the common case and never becomes a gate.

What it is good for: where a scope *is* a repository, picking the name beats
typing it, and the near-miss list catches a transposition. What it must not be
read as: evidence about names it does not contain. A real scope is missing from
a seeded vocabulary for many ordinary reasons — a package inside a monorepo, a
service that is not its own repository, a repo since deleted or renamed, an
application group, a portfolio. `gh repo list` answers "no repository has that
name", which is a much narrower claim than "no such thing exists", and the
resolver treats it that way.

The two middle rungs cannot come from here at all. Nothing in GitHub says which
application group a repository belongs to, or what portfolios exist; that is org
structure and it stays human-supplied.

    python3 seed-vocabulary.py --org ORG [--vocabulary PATH] [--dry-run]

Requires the `gh` CLI, authenticated, with the org authorized if it enforces
SAML. Existing entries are never overwritten — a rung you assigned by hand wins
over anything inferred here.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Nothing is filtered. An earlier version skipped repositories whose names
# looked like infrastructure — and the list was one organization's actual repo
# names, which is precisely the assumption this script must not carry. Deciding
# what counts as an application from a repository name is a guess, and a wrong
# one silently removes a real scope from the list a person picks from. The
# resolver already shortlists by what a claim actually names, so a larger
# vocabulary costs nothing.


def gh_repos(org):
    """[(name, description, visibility)] for every repo the caller can see."""
    try:
        r = subprocess.run(
            ["gh", "repo", "list", org, "--limit", "500", "--json",
             "name,description,visibility"],
            capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        print("The `gh` CLI is not installed. Install it, or add scopes by hand "
              "as you go.", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.TimeoutExpired:
        print("`gh repo list` timed out.", file=sys.stderr)
        raise SystemExit(1)

    if r.returncode != 0:
        err = (r.stderr or "").strip()
        print(f"`gh repo list {org}` failed:\n  {err}", file=sys.stderr)
        if "SAML" in err or "403" in err:
            print("\nThe organization enforces SAML and this token is not "
                  "authorized for it.\nRun:  gh auth login --scopes "
                  "\"repo,read:org\" --web\nand grant the organization when "
                  "prompted.", file=sys.stderr)
        raise SystemExit(1)

    try:
        return [(d["name"], d.get("description") or "", d.get("visibility", ""))
                for d in json.loads(r.stdout)]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"could not read `gh repo list` output: {e}", file=sys.stderr)
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--vocabulary",
                    default=str(Path.home() / ".arionix" / "scope-vocabulary.json"))
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be added, write nothing")
    args = ap.parse_args()

    path = Path(args.vocabulary)
    vocab = {}
    if path.is_file():
        try:
            vocab = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            vocab = {}

    repos = gh_repos(args.org)
    added, kept = [], []
    for name, desc, vis in sorted(repos):
        if name in vocab:
            # Never overwrite. A rung someone assigned by hand carries more
            # information than the assumption that a repository is one
            # application, and this script must not quietly demote it.
            kept.append(name)
            continue
        vocab[name] = {
            "guess_kind": "repository",
            "scope_breadth": "application",
            "uses": 0,
            # `source` says where the name came from, which is all that is
            # known. An earlier version wrote `verified: true`, which read as a
            # claim about the scope rather than about the lookup.
            "source": f"github:{args.org}",
            **({"description": desc[:120]} if desc else {}),
        }
        added.append(name)

    print(f"{args.org}: {len(repos)} repositor{'y' if len(repos) == 1 else 'ies'} "
          f"visible")
    print(f"  {len(added)} added   {len(kept)} already known")
    if added:
        print()
        for n in added[:40]:
            print(f"    + {n}")
        if len(added) > 40:
            print(f"    … and {len(added) - 40} more")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(vocab, indent=2, sort_keys=True,
                               ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {path}  ({len(vocab)} scope(s))")
    print("\n\033[2mEvery repository is seeded at `application` level, which is a "
          "convention,\nnot a fact — correct any of them when it comes up and the "
          "correction sticks.\nApplication groups and portfolios are org structure "
          "GitHub does not carry, so\nname those as you go. A scope missing from "
          "this list is not a wrong scope.\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
