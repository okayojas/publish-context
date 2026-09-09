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
plus an optional sibling key "excluded": [{"reason","count","unit"}], where
unit is "fragment" (claims discarded while decomposing) or "record" (whole
candidates dropped). Count in fragments — that is the only unit at which
container decomposition is visible.
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
RUBRIC_VERSION = 10

KINDS = ["rejected_alternative", "constraint", "authority", "preference",
         "playbook", "vocabulary", "external_reference"]

# Kinds that are meaningless without a subject. A constraint with no scope is a
# prohibition that applies to everything, which is worse than dropping the
# record; "ask X before touching Y" needs Y; a vocabulary entry's referent *is*
# the claim. The rest legitimately have no external scope — a method preference
# defaults to the publisher, and pinning it to one repository narrows it
# wrongly rather than sharpening it.
REQUIRES_SCOPE = {"rejected_alternative", "constraint", "authority", "vocabulary"}
TARGETS = ["decision", "document", "preference_rule", "alias_proposal", "person_property"]
SHARING = ["personal", "team", "org"]
STILL = ["yes", "no", "unknown"]
ORIGIN = ["user_stated", "model_inferred", "derived_from_correction", "unknown"]
PAM = ["factual", "procedural", "identity"]
# Narrowest to broadest. Most real claims sit in the middle two — a rule about
# a construction path is neither one repository nor the whole company.
BREADTHS = ["application", "application_group", "portfolio", "enterprise"]
# Why a record never entered the system. Counts and reasons only, never content.
EXCL = ["derivable", "session_local", "person_sensitive", "unclassifiable",
        # A credential was detected at collect time. Distinct from
        # 'unclassifiable' on purpose: that reads as "we couldn't tell", and a
        # key in a memory file is something we could tell exactly.
        "secret_bearing",
        # Written down before it was agreed — a proposal, a draft ticket, an
        # architecture position marked "awaiting decision". Publishing one tells
        # every agent in the org it is settled. Not a staleness problem, which
        # is what `still_true` covers.
        "provisional",
        # True, durable, not derivable from any repo — and about one laptop.
        # Ports, container names, local stack layout. `session_local` means
        # "meaningless in three months"; these stay true and stay unshareable.
        "machine_local"]


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


def read_json(path, what, hint=""):
    """Read a JSON input, or explain what to do about it.

    A traceback is an acceptable failure for a script one author runs and a bad
    one for a skill other people install: "FileNotFoundError" does not tell
    someone they ran the stages out of order.
    """
    p = Path(path)
    if not p.is_file():
        print(f"No {what} at {p}", file=sys.stderr)
        if hint:
            print(f"\n{hint}", file=sys.stderr)
        raise SystemExit(1)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"{what} at {p} is not valid JSON — line {e.lineno}, column "
              f"{e.colno}:\n  {e.msg}", file=sys.stderr)
        print("\nIf a model wrote this file, the usual causes are a trailing "
              "comma or an\nunquoted key. Fix that line and re-run.",
              file=sys.stderr)
        raise SystemExit(1)
    except OSError as e:
        print(f"could not read {what} at {p}: {e}", file=sys.stderr)
        raise SystemExit(1)


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
        # The statement, not the minted id. An id and an index mean nothing to
        # someone who has to go find the entry in a 50-item file, and this
        # validator is now run by people who did not write it.
        _s = (c.get("statement") or "").strip()
        _label = (_s[:64] + "…") if len(_s) > 64 else (_s or "no statement")
        w = f"candidate[{i}] {_label!r}"
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

        scopes = c.get("claimed_scope") or []
        for s in scopes:
            if s.get("resolution") != "unresolved" or s.get("canonical_id") is not None:
                errs.append(f"{w}: claimed_scope must leave the machine unresolved "
                            f"with canonical_id null — the platform resolves it")

        # Neither low grade can satisfy a scope requirement, and for the same
        # reason: an `encoded_path` is "readable by a person, never resolvable by
        # the resolver" by its own definition, and a `container` names the folder
        # someone keeps code in. They differ only in whether anything can be
        # done about it — the resolver can upgrade the first and nothing can fix
        # the second — so they get different advice, not different verdicts.
        UNUSABLE = ("container", "encoded_path")
        usable_scopes = [s for s in scopes if s.get("quality") not in UNUSABLE]

        # An explicit breadth assertion satisfies the requirement. Absence of a
        # scope is ambiguous between "applies to everything" and "we don't
        # know", and those need opposite handling — so the broad case has to be
        # stated rather than left as a gap. Guarded two ways: it cannot coexist
        # with a narrow scope, and at tier 1 it needs a rationale, because
        # otherwise it is the cheapest possible escape from Step 3's scope rule.
        # A level and a target are two halves of one statement. Only
        # 'enterprise' means everything and so names nothing; every narrower
        # level has to say what the thing at that level is, or it asserts a
        # breadth without a subject.
        breadth = c.get("scope_breadth")
        if breadth is not None and breadth not in BREADTHS:
            errs.append(f"{w}: scope_breadth {breadth!r} invalid — one of "
                        f"{BREADTHS}")
        elif breadth == "enterprise":
            if usable_scopes:
                errs.append(f"{w}: scope_breadth 'enterprise' with a named target "
                            f"({usable_scopes[0].get('text')!r}) — enterprise means "
                            f"everything, so it names nothing. Use a narrower level.")
        elif breadth in ("portfolio", "application_group", "application"):
            if not usable_scopes:
                errs.append(f"{w}: scope_breadth {breadth!r} needs `claimed_scope` "
                            f"to name the {breadth.replace('_', ' ')} it applies to")

        if (c.get("tier") == 1 and breadth in ("portfolio", "enterprise")
                and not (c.get("rationale") or "").strip()):
            errs.append(f"{w}: a tier-1 claim at {breadth} level needs a rationale "
                        f"— it is among the broadest the payload can carry")

        if (c.get("kind") in REQUIRES_SCOPE and not usable_scopes
                and breadth != "enterprise"):
            grades = {s.get("quality") for s in scopes}
            if "encoded_path" in grades:
                why = ("Its only scope is an encoded local path. Run "
                       "resolve-projects.py, then\n      re-collect.")
            elif "container" in grades:
                why = ("Its only scope names a code parent, not a project. Ask "
                       "what it applies\n      to.")
            else:
                why = ("Run  python3 scripts/resolve-scopes.py  to answer this "
                       "and any others\n      in one pass. It offers what the batch "
                       "already names — a sibling claim's\n      scope, a referenced "
                       "ticket, the source project — then asks which rung:\n      "
                       "application, application group, portfolio or enterprise.")
            errs.append(f"{w}:\n      kind {c['kind']!r} requires a scope and has "
                        f"none.\n      {why}")

        act = c.get("activation") or {}
        if act.get("inclusion") not in ("always", "fileMatch", "manual"):
            errs.append(f"{w}: activation.inclusion invalid")

    units = set()
    for e in payload.get("excluded", []):
        if e.get("reason") not in EXCL:
            errs.append(f"excluded: reason {e.get('reason')!r} invalid")
        if "content" in e or "body" in e or "statement" in e:
            errs.append("excluded: must carry counts and reasons only, never content")

        # A count with no unit is not interpretable, and the ambiguity was not
        # harmless: one run reported 0% excluded because it counted records,
        # where every record yielded at least one kernel, and the report
        # diagnosed a skipped filter that had in fact run.
        u = e.get("unit")
        if u not in ("record", "fragment"):
            errs.append(f"excluded[{e.get('reason')}]: needs `unit` — 'record' for "
                        f"whole candidates dropped before Step 2.5, 'fragment' for "
                        f"claims discarded while decomposing a container")
        else:
            units.add(u)

        note = e.get("note")
        if isinstance(note, str):
            if len(note) > 240:
                errs.append(f"excluded[{e.get('reason')}]: note is {len(note)} chars, "
                            f"cap is 240.\n      A note records how the count was "
                            f"taken, not what was excluded — e.g.\n      "
                            f"\"counted while decomposing containers\".")
            # A note ships in the payload, so a quoted excerpt inside it
            # publishes exactly what the exclusion was for.
            if re.search(r"[\"'“‘][^\"'”’]{40,}", note):
                errs.append(f"excluded[{e.get('reason')}]: note quotes a long passage "
                            f"— that publishes the content the exclusion withheld")

    if len(units) > 1:
        errs.append(f"excluded: entries mix units {sorted(units)} — the total is then "
                    f"meaningless. Count everything in one unit.")

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

    inter = read_json(args.candidates, "candidates file",
                      "Run the collector first:\n    python3 scripts/collect.py --all")

    # The collector always emits these; a candidates file that lacks them was
    # hand-made or truncated, and crashing on a KeyError three hundred lines
    # later tells the person nothing about which.
    _need = ("content_hash", "tool", "source_path", "authority",
             "authority_signal", "asserted_at")
    for _i, _c in enumerate(inter.get("candidates") or []):
        _missing = [f for f in _need if f not in _c]
        if _missing:
            print(f"candidates[{_i}] is missing {', '.join(_missing)} — this file "
                  f"was not written by collect.py, or was truncated.\n"
                  f"Re-run:  python3 scripts/collect.py --all", file=sys.stderr)
            raise SystemExit(1)
    by_hash = {c["content_hash"]: c for c in inter["candidates"]}

    # A candidates.json written before refs were position-graded holds bare
    # strings. Coerce rather than fail: 'body' is the honest reading of "this
    # run doesn't know where the reference appeared", and it keeps a stale
    # intermediate from emitting a payload that violates the schema.
    stale_refs = 0
    for c in inter["candidates"]:
        rs = c.get("refs") or []
        if rs and isinstance(rs[0], str):
            c["refs"] = [{"text": r, "kind": "path" if "/" in r else "ticket",
                          "zone": "body"} for r in rs]
            stale_refs += 1
    if stale_refs:
        print(f"note: {stale_refs} record(s) had ungraded refs from an older "
              f"collect.py — treated as zone 'body'. Re-run collect.py to grade "
              f"them properly.", file=sys.stderr)

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

    def id_key(src, claim_index=0):
        ci = src.get("chunk_index")
        base = src["source_path"] if ci is None else f"{src['source_path']}#{ci}"
        return base if claim_index == 0 else f"{base}@{claim_index}"

    def stable_id(src, claim_index=0):
        """Published id > id reserved by an earlier assemble > mint and reserve."""
        if src.get("memory_id") and claim_index == 0:
            return src["memory_id"]
        k = id_key(src, claim_index)
        if k in pending:
            return pending[k]
        pending[k] = mint_id()
        return pending[k]

    raw = read_json(args.classified, "classified file",
                    "This is the classification you write in step 3 — see\nreference/tier-rubric.md and the example in SKILL.md.")
    decisions = raw if isinstance(raw, list) else raw.get("candidates", [])
    excluded = [] if isinstance(raw, list) else raw.get("excluded", [])
    retired = [] if isinstance(raw, list) else raw.get("retired", [])

    # Check the classifier's OWN output before we rebuild it. The assembler
    # sanitizes these fields on the way through, so without this pass a model
    # that invented a canonical_id or attached a confidence would be silently
    # corrected — safe, but it hides rubric drift from the person.
    input_errs = []
    _seen_claims = set()
    for i, d in enumerate(decisions):
        w = f"classified[{i}]"
        if "confidence" in d:
            input_errs.append(f"{w}: attached a `confidence` — forbidden; asserted "
                              f"content is authoritative, inferred is recalibrated in code")
        if "authority" in d:
            input_errs.append(f"{w}: set `authority` — that comes from the file's "
                              f"location, never from the classifier")
        if "claim_index" in d and not (isinstance(d["claim_index"], int) and d["claim_index"] >= 0):
            input_errs.append(f"{w}: claim_index must be a non-negative integer")

        # Two claims from one container that both omit claim_index both default
        # to 0, so both mint the same memory_id — and landing dedups on it, which
        # would drop the second silently. Decomposing containers into several
        # claims is the normal path now (one store went 36 -> 50 claims by doing
        # more of it), so this collision is live rather than theoretical.
        _ck = (d.get("content_hash"), d.get("claim_index", 0))
        if _ck in _seen_claims:
            input_errs.append(f"{w}: another claim already uses claim_index "
                              f"{_ck[1]} for this content_hash — they would mint the "
                              f"same memory_id and landing would keep only one. "
                              f"Number the claims from a container 0, 1, 2, …")
        _seen_claims.add(_ck)
        if d.get("tier") not in (1, 2):
            input_errs.append(f"{w}: tier {d.get('tier')!r} — tier 3 is excluded "
                              f"locally and reported as a count, never classified")
        for s_ in d.get("claimed_scope") or []:
            if s_.get("canonical_id") or s_.get("resolution") not in (None, "unresolved"):
                input_errs.append(f"{w}: claimed_scope entry {s_.get('text')!r} carries a "
                                  f"resolved id — the machine has no graph access and must "
                                  f"never invent one")

        # A quarantined record has no body, statement or rationale to classify,
        # so classifying one means the model authored content the collector
        # deliberately refused to copy. Refuse the whole batch rather than drop
        # the record: a classifier reaching for a secret-bearing file is a rubric
        # failure worth surfacing, not something to silently absorb.
        # A statement is one normative sentence, so a fragment is a defect the
        # validator can see. Two real cases shipped as tier-1 constraints:
        # "CI and TM data" and "Peripheralize metadata/ontology edges rather th".
        # Terminal punctuation is NOT the test — plenty of good statements end
        # without a period ("Its Keycloak runs on 8083, not 8080").
        stmt = (d.get("statement") or "").strip()
        if stmt:
            last = re.split(r"[\s]+", stmt)[-1].strip(".,;:!?)\"'`]}")
            # Two characters, not three. The first version allowed a list of
            # known short words and rejected the rest, which cannot work: there
            # are hundreds of three-letter words and acronyms, and it duly
            # rejected valid statements ending in 'are', 'one' and 'RAG'. That
            # is the same mistake this codebase keeps making — asserting
            # something the data cannot support.
            #
            # So: conservative on purpose. A fragment of three or more
            # characters gets through, and that is the right trade. Missing a
            # truncation costs one bad statement a reviewer will notice; a false
            # positive blocks an entire batch and teaches people to distrust the
            # validator.
            _SHORT_WORDS = {"a", "an", "as", "at", "be", "by", "do", "go", "if",
                            "in", "is", "it", "me", "my", "no", "of", "on", "or",
                            "so", "to", "up", "us", "we", "ok"}
            if (len(last) <= 2 and last.isalpha() and last.islower()
                    and last not in _SHORT_WORDS):
                input_errs.append(f"{w}: statement ends mid-word ({last!r}) — it was "
                                  f"truncated somewhere upstream; re-read the source "
                                  f"and write the whole sentence")
            if len(stmt) < 20 or len(stmt.split()) < 3:
                input_errs.append(f"{w}: statement is {len(stmt)} chars "
                                  f"({len(stmt.split())} words) — too short to be a "
                                  f"normative sentence: {stmt!r}")

        src_ = by_hash.get(d.get("content_hash"))
        if src_ and src_.get("secret_detected"):
            kinds_ = ", ".join(sorted({f['kind'] for f in src_.get('secret_findings', [])}))
            input_errs.append(f"{w}: {Path(src_['source_path']).name} was quarantined at "
                              f"collect time ({kinds_}) and must be excluded as "
                              f"`secret_bearing` — it has no body to publish")
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

        # An explicit empty claimed_scope means the classifier looked and found
        # nothing nameable — a real answer for a globally-applicable rule. Only
        # fall back to collected hints when the key is absent entirely; `or`
        # treated [] as "unset" and silently stamped the record with a
        # low-quality path hint.
        if "claimed_scope" in d:
            hints = d["claimed_scope"] or []
        else:
            hints = src.get("scope_hints") or []

        # The collector's own grading of every hint it produced for this record.
        # A classifier writing a scope by hand gets `name` by default — correct
        # when it named a service from the body, wrong if it copied a hint the
        # collector had already graded unusable. Defence in depth: the rubric
        # says never attach a container hint, and this makes saying it useless.
        graded = {h.get("text"): h.get("quality")
                  for h in (src.get("scope_hints") or []) if h.get("quality")}

        scope = []
        for h in hints:
            text = h.get("text", "")
            scope.append({
                "text": text,
                "guess_kind": h.get("guess_kind", "unknown"),
                "evidence": h.get("evidence", "body_reference"),
                # carried through, not dropped: without it the platform cannot
                # tell an encoded local path from a real name
                "quality": h.get("quality") or graded.get(text) or "name",
                "resolution": "unresolved",
                "canonical_id": None,
            })

        candidates.append({
            "memory_id": stable_id(src, d.get("claim_index", 0)),
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
            # Carried only when set — an absent key means "has a target",
            # which is the ordinary case and needs no marker.
            **({"scope_breadth": d["scope_breadth"]}
               if d.get("scope_breadth") else {}),
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
                # `in`, not truthiness: an explicit claim_index 0 means "claim
                # one of several", and dropping it made that indistinguishable
                # from a single-claim record.
                **({"claim_index": d["claim_index"]} if "claim_index" in d else {}),
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
