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
    wanted = {}
    for c in d["candidates"]:
        for h in c.get("scope_hints") or []:
            if h.get("quality") == "encoded_path":
                wanted.setdefault(h["text"], 0)
                wanted[h["text"]] += 1

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

    matched, unmatched = [], []
    for enc, n in sorted(wanted.items(), key=lambda kv: -kv[1]):
        if mapping.get(enc):
            matched.append((enc, mapping[enc], n, "already mapped"))
            continue
        hit = index.get(enc.lower())
        if not hit:
            unmatched.append((enc, n))
            continue
        remote = git_remote(hit)
        name = remote or hit.name
        mapping[enc] = name
        matched.append((enc, name, n, "matched " + str(hit)))

    for enc, name, n, how in matched:
        print(f"  ✓ {name}")
        print(f"      {n} record(s) · {how}")
    for enc, n in unmatched:
        print(f"  ? {enc}")
        print(f"      {n} record(s) · no checkout found — add it by hand below")

    mp = Path(args.map)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {mp}  ({len(mapping)} mapped)")

    if unmatched:
        print("\nFor anything unmatched, add the real repository or service name:")
        print(json.dumps({e: "owner/repo-or-service" for e, _ in unmatched}, indent=2))
        print("\nNothing is guessed. An unmapped project keeps its encoded hint and")
        print("stays flagged as unresolvable, which is the honest outcome.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
