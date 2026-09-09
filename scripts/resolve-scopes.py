#!/usr/bin/env python3
"""
Give every scope-requiring claim a target — by asking, not by defaulting.

`constraint`, `rejected_alternative`, `authority` and `vocabulary` are
meaningless without a subject, and the classifier often cannot supply one: it
sees a claim and the record it came from, and those disagree more often than
not. A rule about the platform's connector layer and a rule about one repo can
be written down in the same file on the same afternoon.

So this asks. Two things make that cheap rather than tedious:

  * the options are single keystrokes, because the source record already
    supplies a candidate — the project it was written in
  * it only asks about claims that are actually blocked

There is deliberately **no default**. For project *names* a default is safe:
the suggestion is read off a path and the person can see whether it is right.
Here the choice is a judgement about what the claim means, and inheriting the
source project would be wrong for exactly the claims that matter most — the
broad ones. A wrong scope is a bad edge in the graph with a citation attached;
an absent one merely blocks until someone answers.

    python3 resolve-scopes.py [--classified PATH] [--candidates PATH]
                              [--no-interactive]
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assemble import REQUIRES_SCOPE, read_json   # one implementation, not two

UNUSABLE = ("container", "encoded_path")


def usable(scopes):
    return [s for s in scopes if s.get("quality") not in UNUSABLE]


def needs_scope(claim, src):
    """True when this claim would fail validation for want of a target."""
    if claim.get("kind") not in REQUIRES_SCOPE:
        return False
    if claim.get("scope_breadth") == "platform_wide":
        return False
    if "claimed_scope" in claim:
        scopes = claim["claimed_scope"] or []
    else:
        scopes = (src or {}).get("scope_hints") or []
    return not usable(scopes)


def candidate_project(src):
    """The project the record was written in, if the collector could name it."""
    for h in (src or {}).get("scope_hints") or []:
        if h.get("quality") == "name" and h.get("text"):
            return h["text"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classified",
                    default=str(Path.home() / ".arionix" / "classified.json"))
    ap.add_argument("--candidates",
                    default=str(Path.home() / ".arionix" / "candidates.json"))
    ap.add_argument("--no-interactive", action="store_true")
    args = ap.parse_args()

    doc = read_json(args.classified, "classified file",
                    "Classify first — see reference/tier-rubric.md.")
    inter = read_json(args.candidates, "candidates file",
                      "Run the collector first:\n    python3 scripts/collect.py --all")

    claims = doc["candidates"] if isinstance(doc, dict) else doc
    by_hash = {c["content_hash"]: c for c in inter["candidates"]}

    pending = []
    for i, cl in enumerate(claims):
        src = by_hash.get(cl.get("content_hash"))
        if needs_scope(cl, src):
            pending.append((i, cl, src))

    if not pending:
        print("Every scope-requiring claim already has a target. Nothing to do.")
        return 0

    interactive = (not args.no_interactive
                   and sys.stdin.isatty() and sys.stdout.isatty())
    if not interactive:
        print(f"{len(pending)} claim(s) need a scope, and there is no terminal to "
              f"ask in.\n", file=sys.stderr)
        for i, cl, src in pending:
            proj = candidate_project(src) or "unknown"
            print(f"  [{i}] {cl.get('kind')}  {(cl.get('statement') or '')[:60]}",
                  file=sys.stderr)
            print(f"       written in: {proj}", file=sys.stderr)
        print("\nRe-run with a terminal, or edit the file: give each a "
              "`claimed_scope`\nentry, or `scope_breadth: \"platform_wide\"` with a "
              "rationale.", file=sys.stderr)
        return 1

    print(f"\n{len(pending)} claim(s) need a target before they can publish.")
    print("\033[2mThese kinds are meaningless without one — an unscoped constraint "
          "is a\nprohibition that applies to everything. There is no default: the "
          "project a\nclaim was written in is often not what it is about.\033[0m")

    changed, skipped = 0, 0
    for n, (i, cl, src) in enumerate(pending, start=1):
        proj = candidate_project(src)
        stmt = (cl.get("statement") or "").strip()
        where = Path(src["source_path"]).name if src else "?"
        print(f"\n  \033[1m{n}/{len(pending)}\033[0m  {cl.get('kind')}")
        print(f"  {stmt[:150]}")
        print(f"  \033[2mfrom {where}\033[0m")
        print()
        if proj:
            print(f"       \033[1m1\033[0m  {proj}   \033[2m(the project it was "
                  f"written in)\033[0m")
        print(f"       \033[1mp\033[0m  platform-wide   \033[2m(no narrower "
              f"target)\033[0m")
        print(f"       \033[1mt\033[0m  type a name")
        print(f"       \033[1ms\033[0m  skip   \033[2m(stays blocked)\033[0m")

        try:
            while True:
                ans = input("     > ").strip().lower()
                if ans == "1" and proj:
                    cl["claimed_scope"] = [{"text": proj, "guess_kind": "unknown",
                                            "evidence": "source_project"}]
                    cl.pop("scope_breadth", None)
                    print(f"     \033[32mok\033[0m {proj}")
                    changed += 1
                    break
                if ans == "p":
                    rat = (cl.get("rationale") or "").strip()
                    if cl.get("tier") == 1 and not rat:
                        print("     \033[2ma tier-1 platform-wide claim needs a "
                              "rationale — it is the\n     broadest claim the "
                              "payload can carry\033[0m")
                        rat = input("     why?  > ").strip()
                        if not rat:
                            print("     \033[33mno rationale — not set\033[0m")
                            continue
                        cl["rationale"] = rat
                    cl["claimed_scope"] = []
                    cl["scope_breadth"] = "platform_wide"
                    print("     \033[32mok\033[0m platform-wide")
                    changed += 1
                    break
                if ans == "t":
                    name = input("     name  > ").strip()
                    if not name:
                        continue
                    cl["claimed_scope"] = [{"text": name, "guess_kind": "unknown",
                                            "evidence": "asked_and_confirmed"}]
                    cl.pop("scope_breadth", None)
                    print(f"     \033[32mok\033[0m {name}")
                    changed += 1
                    break
                if ans == "s":
                    print("     \033[2mskipped — stays blocked\033[0m")
                    skipped += 1
                    break
                print("     \033[2m1, p, t or s\033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\n  stopped — earlier answers kept")
            break

    if changed:
        Path(args.classified).write_text(
            json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {args.classified}  ({changed} scoped"
              + (f", {skipped} still blocked" if skipped else "") + ")")
        print("\nNext:  python3 scripts/assemble.py --classified "
              f"{args.classified} --mode report-only")
    else:
        print("\nNothing changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
