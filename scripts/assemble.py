#!/usr/bin/env python3
"""
Stage 4a: build the envelope, mint ids, compute the digest, validate locally.

Reads classified records (the model's output) plus the collector's intermediate,
merges them, and emits a payload that either validates or fails loudly. Never
submits — that is submit.py's job, after the person has seen the list.

    python3 assemble.py --classified PATH [--candidates PATH] [--out PATH]
                        [--publisher ID] [--mode report-only|publish]

`classified` is a JSON array of objects, one per kept candidate:
    { "content_hash", "kind", "tier", "target", "statement", "rationale",
      "pam_component", "claimed_scope", "sharing", "still_true", "origin" }
plus an optional sibling key "excluded": [{"reason","count"}].
"""

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REF = HERE.parent / "reference"
SCHEMA_VERSION = "0.3"
RUBRIC_VERSION = 3

KINDS = ["rejected_alternative", "constraint", "authority", "preference",
         "playbook", "vocabulary", "external_reference"]
TARGETS = ["decision", "document", "preference_rule", "alias_proposal", "person_property"]
SHARING = ["personal", "team", "org"]
STILL = ["yes", "no", "unknown"]
ORIGIN = ["user_stated", "model_inferred", "derived_from_correction", "unknown"]
PAM = ["factual", "procedural", "identity"]
EXCL = ["derivable", "session_local", "person_sensitive", "unclassifiable"]


def canonical(obj):
    """Stable serialization for hashing: sorted keys, no incidental whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(s):
    return "sha256:" + hashlib.sha256(s.encode()).hexdigest()


def mint_id():
    return "cmem_" + uuid.uuid4().hex[:16]


def store_instance():
    """Stable per-machine slug. Two people's stores are two namespaces."""
    import socket
    host = re.sub(r"[^a-z0-9]+", "-", socket.gethostname().lower()).strip("-")
    user = re.sub(r"[^a-z0-9]+", "-", (os.environ.get("USER") or "user").lower()).strip("-")
    return f"{user}.{host}"[:64]


# ------------------------------------------------------------------ validation

def validate(payload):
    """Focused validator over the known contract. Deliberately dependency-free:
    the schema file documents the contract, this enforces it."""
    errs = []

    def req(obj, field, where):
        if obj.get(field) in (None, "", []):
            errs.append(f"{where}: missing required `{field}`")

    if payload.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"envelope: schema_version must be {SCHEMA_VERSION!r}")
    for f in ("export_id", "store_instance", "exported_at", "generator"):
        req(payload, f, "envelope")

    d = payload.get("envelope_digest")
    if d and not re.fullmatch(r"sha256:[0-9a-f]{64}", d):
        errs.append("envelope: envelope_digest malformed")

    for i, c in enumerate(payload.get("candidates", [])):
        w = f"candidate[{i}] {c.get('memory_id', '?')}"
        for f in ("memory_id", "content_hash", "authority", "kind", "tier",
                  "target", "statement", "body", "asserted_at", "sharing"):
            req(c, f, w)

        if "confidence" in c:
            errs.append(f"{w}: `confidence` is forbidden — asserted content is "
                        f"authoritative and inferred content is recalibrated in code")
        if c.get("authority") not in ("asserted", "inferred"):
            errs.append(f"{w}: authority must be asserted|inferred")
        if c.get("kind") not in KINDS:
            errs.append(f"{w}: kind {c.get('kind')!r} not in taxonomy")
        if c.get("target") not in TARGETS:
            errs.append(f"{w}: target {c.get('target')!r} invalid")
        if c.get("tier") not in (1, 2):
            errs.append(f"{w}: tier must be 1 or 2 — tier 3 is excluded locally, never sent")
        if c.get("sharing") not in SHARING:
            errs.append(f"{w}: sharing must be one of {SHARING}")
        if c.get("still_true") and c["still_true"] not in STILL:
            errs.append(f"{w}: still_true invalid")
        if c.get("origin") and c["origin"] not in ORIGIN:
            errs.append(f"{w}: origin invalid")
        if c.get("pam_component") and c["pam_component"] not in PAM:
            errs.append(f"{w}: pam_component invalid")

        for s in c.get("claimed_scope") or []:
            if s.get("resolution") != "unresolved" or s.get("canonical_id") is not None:
                errs.append(f"{w}: claimed_scope must leave the machine unresolved "
                            f"with canonical_id null — the platform resolves it")

        act = c.get("activation") or {}
        if act.get("inclusion") not in ("always", "fileMatch", "manual"):
            errs.append(f"{w}: activation.inclusion invalid")

    for e in payload.get("excluded", []):
        if e.get("reason") not in EXCL:
            errs.append(f"excluded: reason {e.get('reason')!r} invalid")
        if "content" in e or "body" in e or "statement" in e:
            errs.append("excluded: must carry counts and reasons only, never content")

    for r in payload.get("retired", []):
        if r.get("reason") not in ("superseded", "wrong", "unshared"):
            errs.append(f"retired: reason {r.get('reason')!r} invalid")

    return errs


# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classified", required=True)
    ap.add_argument("--candidates", default=str(Path.home() / ".arionix" / "candidates.json"))
    ap.add_argument("--out", default=str(Path.home() / ".arionix" / "payload.json"))
    ap.add_argument("--publisher", default=os.environ.get("ARIONIX_PRINCIPAL", ""))
    ap.add_argument("--mode", choices=["report-only", "publish"], default="publish")
    ap.add_argument("--model-id", default=os.environ.get("ARIONIX_MODEL_ID", ""))
    ap.add_argument("--state", default=str(Path.home() / ".arionix" / "publish-state.json"))
    args = ap.parse_args()

    inter = json.loads(Path(args.candidates).read_text())
    by_hash = {c["content_hash"]: c for c in inter["candidates"]}

    # An id minted here must survive a re-run. Without this, assembling twice —
    # which happens whenever a classification is revised, or a submission fails
    # after the records already landed — produced a different id for the same
    # record. Downstream dedup keys on it, so the record would land twice as two
    # objects instead of deduping to one.
    state_path = Path(args.state)
    state = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    pending = dict(state.get("pending_ids") or {})

    def id_key(src):
        ci = src.get("chunk_index")
        return src["source_path"] if ci is None else f"{src['source_path']}#{ci}"

    def stable_id(src):
        """Published id > id reserved by an earlier assemble > mint and reserve."""
        if src.get("memory_id"):
            return src["memory_id"]
        k = id_key(src)
        if k in pending:
            return pending[k]
        pending[k] = mint_id()
        return pending[k]

    raw = json.loads(Path(args.classified).read_text())
    decisions = raw if isinstance(raw, list) else raw.get("candidates", [])
    excluded = [] if isinstance(raw, list) else raw.get("excluded", [])
    retired = [] if isinstance(raw, list) else raw.get("retired", [])

    # Check the classifier's OWN output before we rebuild it. The assembler
    # sanitizes these fields on the way through, so without this pass a model
    # that invented a canonical_id or attached a confidence would be silently
    # corrected — safe, but it hides rubric drift from the person.
    input_errs = []
    for i, d in enumerate(decisions):
        w = f"classified[{i}]"
        if "confidence" in d:
            input_errs.append(f"{w}: attached a `confidence` — forbidden; asserted "
                              f"content is authoritative, inferred is recalibrated in code")
        if "authority" in d:
            input_errs.append(f"{w}: set `authority` — that comes from the file's "
                              f"location, never from the classifier")
        if d.get("tier") not in (1, 2):
            input_errs.append(f"{w}: tier {d.get('tier')!r} — tier 3 is excluded "
                              f"locally and reported as a count, never classified")
        for s_ in d.get("claimed_scope") or []:
            if s_.get("canonical_id") or s_.get("resolution") not in (None, "unresolved"):
                input_errs.append(f"{w}: claimed_scope entry {s_.get('text')!r} carries a "
                                  f"resolved id — the machine has no graph access and must "
                                  f"never invent one")
    if input_errs:
        print("CLASSIFICATION REJECTED — nothing written\n", file=sys.stderr)
        for e in input_errs:
            print(f"  · {e}", file=sys.stderr)
        return 1

    candidates, unmatched = [], []
    for d in decisions:
        src = by_hash.get(d.get("content_hash"))
        if not src:
            unmatched.append(d.get("content_hash"))
            continue

        scope = []
        for s in d.get("claimed_scope") or src.get("scope_hints") or []:
            scope.append({
                "text": s.get("text", ""),
                "guess_kind": s.get("guess_kind", "unknown"),
                "evidence": s.get("evidence", "body_reference"),
                "resolution": "unresolved",
                "canonical_id": None,
            })

        candidates.append({
            "memory_id": stable_id(src),
            "content_hash": "sha256:" + src["content_hash"],
            "revision": 1,
            # authority comes from the collector, never from the classifier
            "authority": src["authority"],
            "authority_signal": src["authority_signal"],
            "origin": d.get("origin", "unknown"),
            "tier": d.get("tier"),
            "kind": d.get("kind"),
            "native_type": src.get("native_type"),
            "pam_component": d.get("pam_component"),
            "target": d.get("target"),
            "statement": d.get("statement"),
            "rationale": d.get("rationale") or src.get("rationale"),
            "body": src["body"],
            "claimed_scope": scope,
            "refs": src.get("refs", []),
            "activation": src.get("activation", {"inclusion": "always"}),
            "asserted_at": src["asserted_at"],
            "timestamp_source": src.get("timestamp_source"),
            "still_true": d.get("still_true", "unknown"),
            "publisher": args.publisher,
            "sharing": d.get("sharing", "personal"),
            "subjects": src.get("subjects_detected", []),
            "source": {
                "tool": src["tool"],
                "path": src["source_path"],
                "documented": src.get("documented_source", True),
            },
        })

    payload = {
        "schema_version": SCHEMA_VERSION,
        "export_id": str(uuid.uuid4()),
        "store_instance": store_instance(),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "generator": {
            "skill": "publish-context",
            "version": RUBRIC_VERSION,
            "mode": args.mode,
            **({"model_id": args.model_id} if args.model_id else {}),
        },
        "candidates": candidates,
        "retired": retired,
        "excluded": excluded,
    }

    # digest over everything except the digest and signature themselves
    payload["envelope_digest"] = sha256(canonical(payload))

    errs = validate(payload)
    if unmatched:
        errs.append(f"{len(unmatched)} classified record(s) matched no collected "
                    f"candidate — content_hash mismatch: {unmatched[:3]}")

    if errs:
        print("VALIDATION FAILED — nothing written\n", file=sys.stderr)
        for e in errs:
            print(f"  · {e}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # Reserve the ids so a re-assemble reuses them. submit.py promotes these to
    # `files` on a successful submission and clears the reservation.
    state["pending_ids"] = pending
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    kinds = {}
    for c in candidates:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"valid · {len(candidates)} candidates · "
          f"{sum(e['count'] for e in excluded)} excluded · digest {payload['envelope_digest'][:23]}…")
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f"    {v:>3}  {k}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
