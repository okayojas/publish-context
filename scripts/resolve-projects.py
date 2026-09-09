#!/usr/bin/env python3
"""
Turn encoded project-dir hints into real repository names — by matching, not guessing.

Some tools name a memory directory after the local filesystem path, flattened:

    C:\\Users\\Ojas\\Downloads\\arionix-weight-poc
      ->  c--Users-Ojas-Downloads-arionix-weight-poc

That cannot be decoded: separators and real hyphens are both '-', so the
segmentation is genuinely ambiguous. But the transform is a single substitution,
so any candidate directory can be *encoded* and compared. A match is a
verification, not an inference — and once matched, the directory's git remote
gives a scope the resolver can actually use.

Writes ~/.arionix/project-map.json, which collect.py reads on later runs.

Anything it cannot match is put to the person in the terminal: numbered, with a
suggested name to accept or correct, and an optional path. Supplying the path
also lets the collector read that project's CLAUDE.md / AGENTS.md. Without a
terminal it falls back to writing CONFIRM: entries into the map for hand
editing.

    python3 resolve-projects.py [--candidates PATH] [--map PATH]
                               [--root DIR ...] [--depth N] [--no-interactive]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Directory names never worth descending into.
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".tox",
        "dist", "build", "target", ".next", ".cache", "Library", "AppData",
        ".gradle", ".m2", ".cargo", ".rustup", "site-packages", ".terraform"}

# Where a checkout plausibly lives. Bounded on purpose: this walks a person's
# machine, and an unbounded sweep is a privacy problem before a technical one.
DEFAULT_ROOTS = ["~", "~/Downloads", "~/Documents", "~/Desktop", "~/Projects",
                 "~/projects", "~/src", "~/code", "~/repos", "~/dev", "~/work"]


def encode_path(p):
    """The flattening a tool applies to build its project-directory name."""
    return re.sub(r"[:\\/ ]", "-", str(p))


# Folder names that reliably mark where a person's own paths begin. Everything
# after the last one is the path relative to it — which for a single segment is
# the directory name itself.
# Deliberately conservative. A false anchor truncates a real name — 'project'
# turned 'project-vantage' into 'vantage' — whereas a missing anchor merely
# yields no suggestion. Tokens that plausibly appear *inside* a directory name
# (project, src, code, dev, work, git, repo) are excluded for that reason.
ANCHORS = {"downloads", "documents", "desktop", "projects", "repos",
           "workspace", "dropbox", "onedrive", "developer", "sites"}


def suggest_name(encoded):
    """A prefill for the person to confirm — never a value that enters the graph.

    Matching fails outright when the directory has been moved or deleted, which
    is the common case for finished work. The encoded name still carries the
    tail, so offer it rather than asking for a blank line.
    """
    parts = [t for t in encoded.split("-") if t]
    last = None
    for i, tok in enumerate(parts):
        if tok.lower() in ANCHORS:
            last = i
    if last is None or last == len(parts) - 1:
        return None, None
    tail = "-".join(parts[last + 1:])
    # No certainty label: whether 'CSE-112' is one directory or two is not
    # decidable from the encoding, and a confident-sounding guess would be worse
    # than naming the anchor and letting the person read it.
    return tail, parts[last]


def _is_pending(v):
    """A value the person has not accepted yet.

    Both prefixes mean the same thing to every reader: CONFIRM: is a suggested
    name for an unmatched directory, CONFIRM-MERGE: is a proposed same-project
    merge. Neither is a mapping until the prefix is gone.
    """
    name = v.get("name") if isinstance(v, dict) else v
    return isinstance(name, str) and name.startswith("CONFIRM")


def parse_selection(text, n):
    """'1,3-5' / 'all' / 'none' -> a set of 1-based indices. None if unparseable."""
    text = (text or "").strip().lower()
    if text in ("all", "a", "*"):
        return set(range(1, n + 1))
    if text in ("none", "n", "q", "quit", "skip", ""):
        return set()
    out = set()
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not m:
            return None
        lo = int(m.group(1))
        hi = int(m.group(2) or lo)
        if lo < 1 or hi > n or lo > hi:
            return None
        out.update(range(lo, hi + 1))
    return out


def confirm_interactively(unmatched, mapping):
    """Walk the person through the pending entries in the terminal.

    Editing project-map.json by hand was the original flow and it was the wrong
    one twice over: the entries were printed but not written, so the file a
    person was told to open did not contain them — and even once written, asking
    someone to hand-edit JSON to answer "is this the right folder name" is a
    detour out of the terminal for a yes.

    Returns the number confirmed. Falls back to the file flow on anything
    unexpected, because a half-finished interactive session must not leave the
    map in a state nobody chose.
    """
    print(f"\n{len(unmatched)} project(s) need a name. No checkout matched them, "
          f"which usually\nmeans the directory was moved or deleted — the tool keeps "
          f"its project\nfolder after the working directory is gone.\n")
    rows = []
    for i, (enc, n) in enumerate(unmatched, start=1):
        name, anchor = suggest_name(enc)
        rows.append((enc, n, name, anchor))
        where = f"relative to {anchor}/" if anchor else "no name in the path"
        print(f"  {i}.  {name or '(unknown)':<28} {n:>3} record(s)   "
              f"\033[2m{where}\033[0m")
        print(f"      \033[2m{enc}\033[0m")

    print("\nWhich would you like to confirm?  e.g. \033[1m1,3-5\033[0m  ·  "
          "\033[1mall\033[0m  ·  \033[1mnone\033[0m")
    print("\033[2mAnything you skip keeps its encoded hint and stays flagged "
          "unresolvable,\nwhich is a real answer — a wrong scope is worse than an "
          "absent one.\033[0m")

    try:
        sel = parse_selection(input("\n  > "), len(rows))
        while sel is None:
            sel = parse_selection(
                input("  didn't parse that — numbers, ranges, 'all' or 'none' > "),
                len(rows))
    except (EOFError, KeyboardInterrupt):
        print("\n  stopped — nothing confirmed")
        return 0

    if not sel:
        print("  nothing confirmed")
        return 0

    done = 0
    for i in sorted(sel):
        enc, n, name, _anchor = rows[i - 1]
        print(f"\n  {i}. \033[1m{name or '(no suggestion)'}\033[0m  "
              f"\033[2m({n} record(s))\033[0m")
        try:
            typed = input(f"     name  [enter to accept{'' if name else ' — required'}] > ").strip()
            final = typed or name
            if not final:
                print("     skipped — no name given")
                continue
            path = input("     path  [enter to skip · a path also reads this "
                         "project's CLAUDE.md] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  stopped — earlier answers kept")
            break

        path = os.path.expanduser(path) if path else None
        if path and not Path(path).is_dir():
            print(f"     \033[33mnot a directory: {path} — name kept, path "
                  f"dropped\033[0m")
            path = None

        mapping[enc] = {"name": final, "path": path, "remote": None,
                        "verified": False}
        extra = "  + will read its instruction files" if path else ""
        print(f"     \033[32mok\033[0m {final}{extra}")
        done += 1
    return done


def holds_checkouts(d, limit=2):
    """How many *other* checkouts live under this directory, up to `limit`.

    A directory that holds several repositories is where someone keeps their
    code, not a project. Matching one and taking its name would map every record
    in the store to a scope like "Developer" — and it would arrive graded `name`
    with `evidence: project_map`, reading as *verified*. A confidently wrong
    scope is worse than an honest useless one, and this function guards the only
    place in the pipeline that can manufacture one.

    Two, not one: a repository with a single vendored dependency is ordinary.
    """
    found = 0
    stack = [(d, 0)]
    while stack and found < limit:
        cur, depth = stack.pop()
        if depth >= 2:
            continue
        try:
            for child in cur.iterdir():
                if not child.is_dir() or child.is_symlink():
                    continue
                if child.name in SKIP or child.name.startswith("."):
                    continue
                if (child / ".git").exists():
                    found += 1
                    if found >= limit:
                        break
                else:
                    stack.append((child, depth + 1))
        except (PermissionError, OSError):
            continue
    return found


def git_remote(d):
    try:
        r = subprocess.run(["git", "-C", str(d), "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", r.stdout.strip())
        return m.group(1) if m else None
    except Exception:
        return None


def find_repos(roots, max_depth):
    """Directories containing .git, within the bounded roots."""
    seen, out = set(), []
    for root in roots:
        base = Path(os.path.expanduser(root))
        if not base.is_dir():
            continue
        if base in seen:
            continue
        seen.add(base)
        stack = [(base, 0)]
        while stack:
            d, depth = stack.pop()
            try:
                if (d / ".git").exists():
                    out.append(d)
                    continue          # do not descend into a repo
                if depth >= max_depth:
                    continue
                for child in d.iterdir():
                    if child.is_dir() and not child.is_symlink() \
                       and child.name not in SKIP and not child.name.startswith("."):
                        stack.append((child, depth + 1))
            except (PermissionError, OSError):
                continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(Path.home() / ".arionix" / "candidates.json"))
    ap.add_argument("--map", default=str(Path.home() / ".arionix" / "project-map.json"))
    ap.add_argument("--root", action="append", default=None,
                    help="where to look for checkouts (repeatable)")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--no-interactive", action="store_true",
                    help="never prompt; write CONFIRM: entries to the map "
                         "instead (automatic when there is no terminal)")
    args = ap.parse_args()

    cpath = Path(args.candidates)
    if not cpath.is_file():
        print(f"No candidates file at {cpath} — run collect.py first", file=sys.stderr)
        return 1

    d = json.loads(cpath.read_text(encoding="utf-8"))
    wanted, container_hints = {}, {}
    for c in d["candidates"]:
        for h in c.get("scope_hints") or []:
            q = h.get("quality")
            if q == "encoded_path":
                wanted[h["text"]] = wanted.get(h["text"], 0) + 1
            elif q == "container":
                container_hints[h["text"]] = container_hints.get(h["text"], 0) + 1

    # Graded unfixable by the collector, so they are not work — but say so.
    # Silently omitting them would read as "nothing to do here".
    for enc, n in sorted(container_hints.items(), key=lambda kv: -kv[1]):
        print(f"  - {enc}")
        print(f"      {n} record(s) · names a code parent, not a project — "
              f"nothing to resolve")
    if container_hints:
        print("      scope for these has to come from the record text or from "
              "asking\n")

    if not wanted:
        print("No encoded-path hints to resolve.")
        return 0

    print(f"{len(wanted)} encoded project path(s) to resolve\n")

    roots = args.root or DEFAULT_ROOTS
    repos = find_repos(roots, args.depth)
    print(f"scanned {len(roots)} root(s) to depth {args.depth} — found {len(repos)} checkout(s)\n")

    # encoded form -> directory. Case-insensitive: at least one tool lowercases
    # the drive letter on the way in.
    index = {}
    for r in repos:
        # Both the literal path and its symlink-resolved form: a tool encodes
        # whatever it was launched with, and on macOS /tmp resolves to
        # /private/tmp, which encodes to a different string entirely.
        for variant in {r, r.resolve()}:
            index.setdefault(encode_path(variant).lower(), r)

    mapping = {}
    if Path(args.map).is_file():
        try:
            mapping = json.loads(Path(args.map).read_text(encoding="utf-8"))
        except Exception:
            mapping = {}

    matched, unmatched, containers = [], [], []
    for enc, n in sorted(wanted.items(), key=lambda kv: -kv[1]):
        existing = mapping.get(enc)
        if isinstance(existing, dict):
            existing = existing.get("name")
        if isinstance(existing, str) and existing.startswith("CONFIRM:"):
            existing = None          # a suggestion the person has not accepted
        if existing:
            matched.append((enc, existing, n, "already mapped"))
            continue
        hit = index.get(enc.lower())
        if not hit:
            unmatched.append((enc, n))
            continue
        # A match is not automatically an answer. `find_repos` stops descending
        # at the first `.git`, so this only fires when the code parent is itself
        # a checkout — which is precisely when the old code mapped it happily.
        held = holds_checkouts(hit)
        if held >= 2:
            containers.append((enc, n, hit, held))
            continue
        remote = git_remote(hit)
        name = remote or hit.name
        # The path is recorded alongside the name, not instead of it: collect.py
        # needs the directory to read the project's own instruction files
        # (CLAUDE.md, AGENTS.md), which live in the working tree where no tool
        # root can see them. Verified match only — never a guess, never a sweep.
        mapping[enc] = {"name": name, "path": str(hit),
                        "remote": remote, "verified": True}
        matched.append((enc, name, n, "matched " + str(hit)))

    for enc, name, n, how in matched:
        print(f"  ✓ {name}")
        print(f"      {n} record(s) · {how}")
    for enc, n, hit, held in containers:
        print(f"  ! {enc}")
        print(f"      {n} record(s) · matched {hit}, but it holds "
              f"{held}+ checkouts")
        print(f"      that names where code is kept, not a project — left "
              f"unmapped on purpose")
    for enc, n in unmatched:
        name, certainty = suggest_name(enc)
        print(f"  ? {enc}")
        if name:
            print(f"      {n} record(s) · no checkout found · relative to "
                  f"{certainty}/ this is {name!r}")
        else:
            print(f"      {n} record(s) · no checkout found")

    # -- same project, two directories --
    #
    # One store had 13 records under `…-Workspace-Arionix-Inc` and 6 under
    # `…-Workspace-arionix-Arionix-Inc`: one project, moved, so two scope
    # strings the resolver has no way to relate.
    #
    # A shared git remote settles it — that is verification, so both hints map
    # to the same name and the platform resolves one string to one id. Where
    # remotes are absent or differ, a merge is only *proposed*: written with a
    # CONFIRM-MERGE: prefix, treated as unset until a person strips it, which is
    # the same idiom already used for unmatched names.
    by_remote = {}
    for enc, v in mapping.items():
        if isinstance(v, dict) and v.get("remote"):
            by_remote.setdefault(v["remote"], []).append(enc)

    merged = [(r, encs) for r, encs in by_remote.items() if len(encs) > 1]
    for remote, encs in merged:
        for enc in encs:
            mapping[enc]["name"] = remote
            mapping[enc]["merged_with"] = sorted(e for e in encs if e != enc)
            mapping[enc]["merge_basis"] = "same_git_remote"

    # Proposals: unmatched hints whose tail matches a name already mapped.
    tails = {}
    for enc, v in mapping.items():
        nm = v.get("name") if isinstance(v, dict) else v
        if isinstance(nm, str) and not nm.startswith("CONFIRM"):
            tails[nm.split("/")[-1].lower()] = nm
    proposals = []
    for enc, n in unmatched:
        tail, _ = suggest_name(enc)
        hit_name = tails.get((tail or "").lower())
        if hit_name:
            mapping[enc] = {"name": f"CONFIRM-MERGE:{hit_name}", "path": None,
                            "remote": None, "verified": False,
                            "merge_basis": "same_tail_name"}
            proposals.append((enc, hit_name, n))

    if merged or proposals:
        print()
        for remote, encs in merged:
            print(f"  = {remote}")
            print(f"      {len(encs)} directories, same git remote — merged, "
                  f"one scope")
        for enc, name, n in proposals:
            print(f"  ? {enc}")
            print(f"      {n} record(s) · same directory name as {name!r} — "
                  f"merge proposed, awaiting approval")
        if proposals:
            print("\n      A CONFIRM-MERGE: value is treated as unset. Strip the "
                  "prefix to accept\n      the merge; delete the line to keep the "
                  "scopes separate.")

    # Pending entries go into the map so the person edits one file in place.
    # Safe to write: every reader treats a CONFIRM-prefixed name as unset.
    for enc, _ in unmatched:
        if enc in mapping:
            continue
        nm, _anchor = suggest_name(enc)
        mapping[enc] = {"name": f"CONFIRM:{nm}" if nm else "CONFIRM:owner/repo",
                        "path": None, "remote": None, "verified": False}

    # Interactive by default when there is a terminal. The file flow remains
    # the fallback so this still works from a subagent, a pipe or cron — but it
    # is the fallback, not the primary path: nobody should have to hand-edit
    # JSON to answer "is this the right folder name".
    interactive = (unmatched and not args.no_interactive
                   and sys.stdin.isatty() and sys.stdout.isatty())
    confirmed = 0
    if interactive:
        confirmed = confirm_interactively(unmatched, mapping)

    still_pending = [(e, n) for e, n in unmatched if _is_pending(mapping.get(e, ""))
                     or e not in mapping]
    for enc, _ in still_pending:
        if enc in mapping:
            continue
        nm, _a = suggest_name(enc)
        mapping[enc] = {"name": f"CONFIRM:{nm}" if nm else "CONFIRM:owner/repo",
                        "path": None, "remote": None, "verified": False}

    mp = Path(args.map)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")

    n_mapped = sum(1 for v in mapping.values() if not _is_pending(v))
    n_pending = sum(1 for v in mapping.values() if _is_pending(v))
    print(f"\nwrote {mp}")
    print(f"  {n_mapped} mapped" + (f"  ·  {n_pending} still pending"
                                    if n_pending else ""))

    if confirmed:
        print(f"  {confirmed} confirmed just now")

    if n_pending:
        if interactive:
            print(f"\n\033[2m{n_pending} left as CONFIRM: entries. Re-run this "
                  f"script to be asked again,\nor edit {mp} directly — strip the "
                  f"CONFIRM: prefix to accept a name.\033[0m")
        else:
            print(f"""
No terminal, so nothing was asked. Pending entries are in the file as
CONFIRM: values — strip the prefix to accept a name, or add a `path` to also
read that project's CLAUDE.md. Re-run with a terminal to be prompted instead.""")

    if confirmed or n_pending == 0:
        print("\nNext:  python3 scripts/collect.py --all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
