#!/usr/bin/env python3
"""
Stage 4b: submit, then record state and write ids back.

Runs only after the person has seen the list and said yes. Nothing here decides
what gets published.

    python3 submit.py [--payload PATH] [--state PATH] [--dry-run]

Config, by environment:
    ARIONIX_ENDPOINT   publication endpoint (user-authenticated)
    ARIONIX_TOKEN      bearer token for that endpoint

Two things happen only on a 2xx: the state file records each source file's hash
so the next run skips it, and `arionix_id` is written back into the source
frontmatter so a later rename cannot fork the record.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def write_back_id(path, memory_id):
    """Persist the minted id in the source file's frontmatter.

    Only for files that ALREADY have frontmatter — we never introduce a block to
    a file that has none. That mirrors how the tools themselves behave, and it
    keeps us from reformatting a person's hand-written notes. Files without
    frontmatter keep their id in the state file instead.
    """
    try:
        text = path.read_text()
    except Exception as e:
        return False, f"unreadable: {e}"

    if not text.startswith("---"):
        return False, "no frontmatter — id kept in state only"

    lines = text.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return False, "unterminated frontmatter"

    for i in range(1, end):
        if lines[i].strip().startswith("arionix_id:"):
            return False, "already present"

    lines.insert(end, f"arionix_id: {memory_id}")
    try:
        path.write_text("\n".join(lines))
        return True, "written"
    except Exception as e:
        return False, f"write failed: {e}"


def post(endpoint, token, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Arionix-Schema", payload.get("schema_version", ""))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode()[:400]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default=str(Path.home() / ".arionix" / "payload.json"))
    ap.add_argument("--candidates", default=str(Path.home() / ".arionix" / "candidates.json"))
    ap.add_argument("--state", default=str(Path.home() / ".arionix" / "publish-state.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    payload = json.loads(Path(args.payload).read_text())
    inter = json.loads(Path(args.candidates).read_text())
    by_hash = {"sha256:" + c["content_hash"]: c for c in inter["candidates"]}

    n = len(payload.get("candidates", []))
    if payload.get("generator", {}).get("mode") == "report-only":
        print("payload is marked report-only — refusing to submit", file=sys.stderr)
        return 1

    endpoint = os.environ.get("ARIONIX_ENDPOINT", "")
    token = os.environ.get("ARIONIX_TOKEN", "")

    if args.dry_run or not endpoint:
        why = "dry run" if args.dry_run else "ARIONIX_ENDPOINT not set"
        print(f"[{why}] would POST {n} candidate(s), digest "
              f"{payload.get('envelope_digest','')[:23]}…")
        if not args.dry_run:
            return 2
        status = 200
    else:
        try:
            status, resp = post(endpoint, token, payload)
        except urllib.error.HTTPError as e:
            print(f"submission rejected: HTTP {e.code} — {e.read().decode()[:300]}",
                  file=sys.stderr)
            return 1
        except Exception as e:
            print(f"submission failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        if not 200 <= status < 300:
            print(f"submission rejected: HTTP {status} — {resp}", file=sys.stderr)
            return 1
        print(f"accepted · HTTP {status} · {n} candidate(s)")

    # -- state and id write-back, only past a successful submission --
    state_path = Path(args.state)
    state = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            state = {}
    state.setdefault("files", {})

    wrote, skipped = 0, []
    for c in payload["candidates"]:
        src = by_hash.get(c["content_hash"])
        if not src:
            continue
        p = Path(src["source_path"])
        state["files"][str(p)] = {
            "source_hash": src["source_hash"],
            "memory_id": c["memory_id"],
            "published_at": payload["exported_at"],
        }
        ok, why = write_back_id(p, c["memory_id"])
        if ok:
            wrote += 1
        elif why != "already present":
            skipped.append((p.name, why))

    # Reservations are now published — drop them so state has one home for ids.
    pending = state.get("pending_ids") or {}
    published = {c["memory_id"] for c in payload["candidates"]}
    state["pending_ids"] = {k: v for k, v in pending.items() if v not in published}

    state["last_export_id"] = payload["export_id"]
    state["last_exported_at"] = payload["exported_at"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"state updated · {len(payload['candidates'])} file(s) recorded")
    print(f"ids written back into {wrote} source file(s)")
    for name, why in skipped[:5]:
        print(f"    {name}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
