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
    if claim.get("scope_breadth") == "enterprise":
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


def _kind_for(level):
    """A level implies what the named thing is, when nothing better is known."""
    return {"application": "application", "application_group": "application_group",
            "portfolio": "portfolio", "enterprise": "unknown"}.get(level, "unknown")


def gather_candidates(claim, src, all_claims, by_hash):
    """Everything in the batch that could plausibly be this claim's target.

    The first version of this offered one option — the project the record was
    written in — which is why the scopes for a real store had to be supplied by
    hand instead. The information was there; the script just wasn't looking.

    Four sources, and the order is the evidence ordering the rubric already
    uses. A sibling claim naming a service is how a human reads it too: the
    container said "the ladder is a pure function in arionix-weight-core" three
    claims earlier, so that is what "the weight path" means here.
    """
    out, seen = [], set()

    def add(text, kind, why):
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        out.append({"text": text, "guess_kind": kind, "why": why})

    # 1. named in this claim's own statement, matched against names already in
    #    play elsewhere in the batch — the strongest evidence available
    stmt = (claim.get("statement") or "").lower()
    vocabulary = {}
    for other in all_claims:
        for s in other.get("claimed_scope") or []:
            if s.get("text"):
                vocabulary.setdefault(s["text"], other.get("guess_kind", "unknown"))
    for name in sorted(vocabulary, key=len, reverse=True):
        if name.lower() in stmt:
            add(name, "unknown", "named in this claim, and used elsewhere in the batch")

    # 2. scopes that sibling claims from the SAME record chose
    same = [o for o in all_claims
            if o is not claim and o.get("content_hash") == claim.get("content_hash")]
    for o in same:
        for s in o.get("claimed_scope") or []:
            add(s.get("text"), s.get("guess_kind", "unknown"),
                "another claim from this same record uses it")

    # 3. references the collector extracted from this record, strongest zone first
    for zone in ("statement", "rationale", "body"):
        for r in (src or {}).get("refs") or []:
            if isinstance(r, dict) and r.get("zone") == zone and r.get("kind") != "url":
                add(r["text"], "ticket" if r.get("kind") == "ticket" else "repository",
                    f"referenced in the {zone}")

    # 4. the project the record was written in — a candidate, never a default
    proj = candidate_project(src)
    add(proj, "unknown", "the project this record was written in")

    # 5. anything else already used in this batch, so a person can reuse a name
    #    rather than retyping it and creating a near-duplicate
    for name, kind in sorted(vocabulary.items()):
        add(name, kind, "used elsewhere in this batch")

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classified",
                    default=str(Path.home() / ".arionix" / "classified.json"))
    ap.add_argument("--candidates",
                    default=str(Path.home() / ".arionix" / "candidates.json"))
    ap.add_argument("--no-interactive", action="store_true")
    ap.add_argument("--auto", action="store_true",
                    help="attach only the one tier that is not a guess — a target "
                         "the claim's own statement names and another claim in the "
                         "batch already uses — then report the rest")
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

    # The strongest evidence tier the rubric recognises: the author named the
    # target while making the claim, and another claim in the batch confirms the
    # spelling and the rung. That is verification, not inference, so it is the
    # only thing safe to attach without asking. Everything else asks.
    if args.auto:
        did = []
        for i, cl, src in list(pending):
            stmt = (cl.get("statement") or "").lower()
            best = None
            for other in claims:
                if other is cl:
                    continue
                for s in other.get("claimed_scope") or []:
                    name = s.get("text") or ""
                    if len(name) >= 4 and name.lower() in stmt:
                        if best is None or len(name) > len(best[0]):
                            best = (name, s.get("guess_kind", "unknown"),
                                    other.get("scope_breadth"))
            if not best:
                continue
            name, kind, rung = best
            cl["claimed_scope"] = [{"text": name, "guess_kind": kind,
                                    "evidence": "named_in_statement"}]
            if rung:
                cl["scope_breadth"] = rung
            did.append((i, name, rung))
            pending.remove((i, cl, src))

        if did:
            print(f"attached {len(did)} scope(s) the statements name themselves:")
            for i, name, rung in did:
                print(f"  [{i}] {name}" + (f"  ({rung})" if rung else ""))
            sys.stdout.flush()          # the listing below goes to stderr
            Path(args.classified).write_text(
                json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            print("nothing could be attached without asking.")
            sys.stdout.flush()
        if not pending:
            print("\nAll scope-requiring claims now have a target.")
            return 0
        print(f"\n{len(pending)} still need a decision.\n")
        sys.stdout.flush()

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
        print("\nRe-run with a terminal to be asked, or edit the file: give "
              "each a\n`claimed_scope` entry naming the target plus a "
              "`scope_breadth` of application,\napplication_group or "
              "portfolio — or `scope_breadth: \"enterprise\"` with a "
              "rationale and no target.", file=sys.stderr)
        return 1

    print(f"\n{len(pending)} claim(s) need a target before they can publish.")
    print("\033[2mThese kinds are meaningless without one — an unscoped constraint "
          "is a\nprohibition that applies to everything. There is no default: the "
          "project a\nclaim was written in is often not what it is about.\033[0m")

    changed, skipped = 0, 0
    LEVELS = [("a", "application"), ("g", "application_group"),
              ("f", "portfolio"), ("e", "enterprise")]

    for n, (i, cl, src) in enumerate(pending, start=1):
        stmt = (cl.get("statement") or "").strip()
        where = Path(src["source_path"]).name if src else "?"
        cands = gather_candidates(cl, src, claims, by_hash)

        print(f"\n  \033[1m{n}/{len(pending)}\033[0m  {cl.get('kind')}"
              f"  \033[2m· tier {cl.get('tier')} · from {where}\033[0m")
        print(f"  {stmt[:160]}")
        print()
        for j, c in enumerate(cands[:6], start=1):
            print(f"       \033[1m{j}\033[0m  {c['text']:<30} \033[2m{c['why']}"
                  f"\033[0m")
        if not cands:
            print("       \033[2mnothing in this batch names a plausible target"
                  "\033[0m")
        print(f"       \033[1me\033[0m  enterprise                     "
              f"\033[2meverything; names no target\033[0m")
        print(f"       \033[1mt\033[0m  type a name")
        print(f"       \033[1ms\033[0m  skip                           "
              f"\033[2mstays blocked\033[0m")

        def ask_level(target):
            """Which rung the named thing sits on. The point of the ladder is
            that most claims are neither one app nor the whole company."""
            print(f"       \033[2mwhat is {target!r}?\033[0m")
            for k, name in LEVELS[:3]:
                print(f"         \033[1m{k}\033[0m  {name.replace('_', ' ')}")
            while True:
                lv = input("       > ").strip().lower()
                for k, name in LEVELS[:3]:
                    if lv == k:
                        return name
                print("       \033[2ma, g or f\033[0m")

        def need_rationale(level):
            if cl.get("tier") != 1 or level not in ("portfolio", "enterprise"):
                return True
            if (cl.get("rationale") or "").strip():
                return True
            print(f"       \033[2ma tier-1 claim at {level} level needs a "
                  f"rationale\033[0m")
            rat = input("       why?  > ").strip()
            if not rat:
                print("       \033[33mno rationale — not set\033[0m")
                return False
            cl["rationale"] = rat
            return True

        try:
            while True:
                ans = input("     > ").strip().lower()

                if ans == "s":
                    print("     \033[2mskipped — stays blocked\033[0m")
                    skipped += 1
                    break

                if ans == "e":
                    if not need_rationale("enterprise"):
                        continue
                    cl["claimed_scope"] = []
                    cl["scope_breadth"] = "enterprise"
                    print("     \033[32mok\033[0m enterprise")
                    changed += 1
                    break

                target, kind, evidence = None, "unknown", "asked_and_confirmed"
                if ans == "t":
                    target = input("     name  > ").strip()
                    if not target:
                        continue
                elif ans.isdigit() and 1 <= int(ans) <= min(6, len(cands)):
                    c = cands[int(ans) - 1]
                    target, kind = c["text"], c["guess_kind"]
                    evidence = ("source_project"
                                if c["why"].startswith("the project") else
                                "batch_reference")
                else:
                    print("     \033[2ma number, e, t or s\033[0m")
                    continue

                level = ask_level(target)
                if not need_rationale(level):
                    continue
                cl["claimed_scope"] = [{"text": target,
                                        "guess_kind": kind if kind != "unknown"
                                        else _kind_for(level),
                                        "evidence": evidence}]
                cl["scope_breadth"] = level
                print(f"     \033[32mok\033[0m {target}  \033[2m({level})\033[0m")
                changed += 1
                break
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
