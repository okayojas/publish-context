#!/usr/bin/env python3
"""
Seed the scope vocabulary from a source of truth, so scopes are picked not typed.

The vocabulary started as a memory of what a person had typed before, which
removed the friction of re-answering but not the risk of answering wrongly. Two
scopes supplied by hand for a real batch — `arionix-weight-core` and
`weight-engine-service` — turned out not to exist anywhere in the organization.
They were read off memory-record prose, which names modules and intentions as
readily as it names repositories. Publishing them would have created two
unresolvable entities in the graph, each with a citation attached.

A GitHub organization is a registry of real applications, and reading it is a
read-only call any member can make. So: seed from there, and the common rung —
`application` — becomes something you pick from things that exist.

The two middle rungs cannot come from this. Nothing in GitHub says which
application group `weight-config-api` belongs to, or what portfolios exist; that
is org structure and it stays human-supplied. This fixes the rung where most
claims land, not all of them.

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

# Repositories that are plainly not applications. Named rather than guessed at,
# because a wrong exclusion here silently removes a real scope from the list a
# person picks from.
NOT_APPLICATIONS = {
    "iac-terraform", "helm-charts", "opa-policies", "github-runner-image",
    "env-fixture", "demo-repository", "test-bed", "architecture",
}


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
    ap.add_argument("--include-infra", action="store_true",
                    help="also add repositories that look like infrastructure")
    args = ap.parse_args()

    path = Path(args.vocabulary)
    vocab = {}
    if path.is_file():
        try:
            vocab = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            vocab = {}

    repos = gh_repos(args.org)
    added, kept, skipped = [], [], []
    for name, desc, vis in sorted(repos):
        if not args.include_infra and name.lower() in NOT_APPLICATIONS:
            skipped.append(name)
            continue
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
            "source": f"github:{args.org}",
            "verified": True,
            **({"description": desc[:120]} if desc else {}),
        }
        added.append(name)

    print(f"{args.org}: {len(repos)} repositor{'y' if len(repos) == 1 else 'ies'} "
          f"visible")
    print(f"  {len(added)} added   {len(kept)} already known   "
          f"{len(skipped)} skipped as infrastructure")
    if added:
        print()
        for n in added[:40]:
            print(f"    + {n}")
        if len(added) > 40:
            print(f"    … and {len(added) - 40} more")
    if skipped:
        print(f"\n  \033[2mskipped: {', '.join(skipped)}"
              f"\033[0m\n  \033[2m--include-infra adds them anyway\033[0m")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(vocab, indent=2, sort_keys=True,
                               ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {path}  ({len(vocab)} scope(s))")
    print("\n\033[2mEvery repository is seeded at `application` level. Application "
          "groups and\nportfolios are org structure that GitHub does not carry — "
          "name those as they\ncome up and they will be remembered alongside "
          "these.\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
