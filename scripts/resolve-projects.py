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
Unmatched entries are reported for one-line manual completion.

    python3 resolve-projects.py [--candidates PATH] [--map PATH]
                               [--root DIR ...] [--depth N]
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
        mapping[enc] = name
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

    mp = Path(args.map)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {mp}  ({len(mapping)} mapped)")

    if unmatched:
        print("\nNo checkout matched these, which usually means the directory has "
              "been\nmoved or deleted — a tool keeps its project folder after the "
              "working\ndirectory is gone, so there is nothing left to match against.")
        print("\nPrefilled with the directory name read off the tail of each path. "
              "Confirm\nor correct, then re-run collect.py:\n")
        stub = {}
        for e, _ in unmatched:
            name, certainty = suggest_name(e)
            stub[e] = f"CONFIRM:{name}" if name else "owner/repo-or-service"
        print(json.dumps(stub, indent=2, ensure_ascii=False))
        print("\nA 'CONFIRM:' prefix is treated as unset — the value is a suggestion "
              "for you,\nnot a mapping. Strip the prefix to accept it. Anything left "
              "unmapped keeps\nits encoded hint and stays flagged unresolvable, which "
              "is the honest outcome.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
