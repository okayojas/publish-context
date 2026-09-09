#!/usr/bin/env python3
"""
Report-only rendering.

The first release reads, classifies and reports — and submits nothing. This
prints what a machine actually holds, plus the eight measures that tell us
whether the taxonomy fits and whether the feature is earning its keep.

    python3 report.py [--candidates PATH] [--classified PATH]

`--classified` is optional; without it the report covers extraction only
(sources, authorship, activation, references), which is the useful half before
any rubric has been applied.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    # Single source of truth. Duplicating these here would drift the moment one
    # file is edited and the other isn't — which has already happened once
    # between the live skill directory and the repository.
    from assemble import REQUIRES_SCOPE, read_json
except Exception:                                    # pragma: no cover
    REQUIRES_SCOPE = {"rejected_alternative", "constraint", "authority",
                      "vocabulary"}

    def read_json(path, what, hint=""):
        return json.loads(Path(path).read_text(encoding="utf-8"))


def _utf8_console():
    """A stock Windows console encodes stdout as cp1252, which cannot represent
    the box-drawing and separator characters this prints — so output died with an
    encode error while file I/O was already fine. Rebind the streams; fall back
    to plain ASCII markers if even that is unavailable."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")          # 3.7+
        except Exception:
            pass


_utf8_console()


def _safe(*chars):
    """Return the first glyph the console can actually encode."""
    enc = (getattr(sys.stdout, "encoding", None) or "ascii")
    for c in chars:
        try:
            c.encode(enc)
            return c
        except Exception:
            continue
    return chars[-1]


FULL, EMPTY, DASH = _safe("█", "#"), _safe("·", "."), _safe("─", "-")


def bar(n, total, width=22):
    if not total:
        return ""
    filled = round(width * n / total)
    return FULL * filled + EMPTY * (width - filled)


def rule(title=""):
    print(f"\n\033[2m{DASH * 68}\033[0m")
    if title:
        print(f"\033[1m{title}\033[0m")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(Path.home() / ".arionix" / "candidates.json"))
    ap.add_argument("--classified", default=None)
    ap.add_argument("--sample", type=int, default=0, metavar="N",
                    help="also print N full records as structured JSON")
    args = ap.parse_args()

    cpath = Path(args.candidates)
    if not cpath.is_file():
        print(f"\nNo candidates file at {cpath}\n\n"
              f"Run the collector first:\n"
              f"    python3 {Path(__file__).parent / 'collect.py'} --all\n", file=sys.stderr)
        return 1

    d = read_json(cpath, "candidates file")
    cands = d["candidates"]
    total = len(cands)

    print("\n\033[1mArionix — context report\033[0m")
    print(f"\033[2m{d['generated_at']}  ·  report only, nothing submitted\033[0m")

    # ---- 1. stores found, and which resolution step answered
    rule("Stores")
    for s in d["sources"]:
        st = s["status"]
        mark = {"ok": _safe("●", "*"), "partial": _safe("◐", "~"),
                "absent": _safe("○", "-"), "unsupported": _safe("×", "x")}.get(st, "?")
        line = f"  {mark} {s.get('display', s['tool']):<26}"
        if st in ("ok", "partial"):
            c = s["counts"]
            doc = "" if s.get("documented", True) else "  \033[2m(undocumented format)\033[0m"
            line += f"{c['found']:>3} files, {c['changed']:>3} changed  \033[2m[{s['resolution']}]\033[0m{doc}"
        elif st == "absent":
            line += "\033[2mnot installed\033[0m"
        else:
            line += "\033[2munsupported — memory is server-side\033[0m"
        print(line)
        for e in s.get("errors", [])[:3]:
            print(f"      \033[33m! {Path(e['path']).name}: {e['error']}\033[0m")

        # A resolved store that read nothing is ambiguous — empty tool, or globs
        # aimed at the wrong place. Six manifest entries have never met a real
        # install, so this is how a stranger's run tells us which.
        um = s.get("unmatched_markdown") or []
        if um:
            extra = s.get("unmatched_markdown_extra", 0)
            more = f" (+{extra} more)" if extra else ""
            print(f"      \033[33m↳ {len(um) + extra} markdown file(s) here that "
                  f"the globs did not match{more}:\033[0m")
            for rel in um:
                print(f"          \033[2m{rel}\033[0m")
            print(f"      \033[2mthat is a manifest gap, not an empty store — "
                  f"worth reporting\033[0m")

    if not total:
        print("\n  No changed records. Nothing to report.\n")
        return 0

    # ---- 1b. quarantine, before anything else about content
    #
    # Reported near the top and by filename, because it is the one finding a
    # person may want to act on in the original file rather than here. Line
    # numbers, never values — this output has to stay safe to paste.
    quarantined = [c for c in cands if c.get("secret_detected")]
    if quarantined:
        rule("Quarantined — credential detected")
        print(f"  {len(quarantined)} record(s) matched at collect time. The body was "
              f"never copied,")
        print(f"  \033[2mso nothing below can be published and nothing was written to "
              f"candidates.json.\033[0m\n")
        for c in quarantined:
            kinds = ", ".join(sorted({f["kind"] for f in c.get("secret_findings", [])}))
            lines = ", ".join(str(f["line"]) for f in c.get("secret_findings", [])[:6])
            print(f"  \033[33m!\033[0m {Path(c['source_path']).name}")
            print(f"      {kinds}  \033[2m· line {lines}\033[0m")
        print(f"\n  \033[2mThis is a floor, not a guarantee: prefixed tokens, PEM "
              f"headers, JWTs and\n  assigned literals. A bare high-entropy string "
              f"with no marker will pass.\033[0m")

    # ---- 2. authorship split
    rule("Authorship")
    auth = Counter(c["authority"] for c in cands)
    sig = Counter(c["authority_signal"] for c in cands)
    for k in ("asserted", "inferred"):
        n = auth.get(k, 0)
        label = "human-written" if k == "asserted" else "agent-written"
        print(f"  {k:<9} {n:>3}  {bar(n, total)}  \033[2m{label}\033[0m")
    print(f"  \033[2msignals: {', '.join(f'{k}={v}' for k, v in sig.most_common())}\033[0m")
    if sig.get("default"):
        print(f"  \033[33m  {sig['default']} record(s) from an unrecognized source "
              f"→ defaulted to inferred\033[0m")

    # Project instruction files, split by whether the repository already has
    # them. The whole point of reaching into the working tree is the untracked
    # half, so report the split rather than a total.
    proj = [c for c in cands if c.get("authority_signal") == "project_root"]
    if proj:
        tracked = sum(1 for c in proj if c.get("committed") is True)
        untracked = sum(1 for c in proj if c.get("committed") is False)
        unknown_git = len(proj) - tracked - untracked
        print(f"\n  {len(proj)} from project instruction files "
              f"\033[2m(read from the working tree)\033[0m")
        print(f"      tracked     {tracked:>3}  \033[2mthe repo has these — "
              f"lean derivable\033[0m")
        print(f"      untracked   {untracked:>3}  \033[2minvisible to every other "
              f"system — the reason this source exists\033[0m")
        if unknown_git:
            print(f"      unknown     {unknown_git:>3}  \033[2mnot a checkout, or "
                  f"git could not say\033[0m")
    else:
        # Nothing from the working tree. Two very different reasons, and saying
        # only "0" conflates them: no project had a confirmed path, versus paths
        # were checked and those projects have no instruction file.
        roots = sum(s.get("project_roots", 0) for s in d["sources"])
        looked = [s for s in d["sources"] if s.get("project_globs_count")
                  or s.get("project_roots") is not None]
        if roots:
            print(f"\n  \033[2m0 from project instruction files — {roots} project "
                  f"path(s) were checked and\n  none has a CLAUDE.md / AGENTS.md. "
                  f"Nothing more to do here.\033[0m")
        elif looked:
            print(f"\n  \033[33m0 from project instruction files — no project has a "
                  f"confirmed path.\033[0m")
            print(f"  \033[2mRe-run resolve-projects.py and supply a path when it "
                  f"asks. That is the only\n  way this pipeline reaches "
                  f"human-authored memory, which is why `asserted` is 0.\033[0m")

    # ---- 3. pre-labelled share
    rule("Source labelling")
    labelled = sum(1 for c in cands if c.get("native_type"))
    print(f"  {labelled}/{total} arrive pre-classified by their tool  {bar(labelled, total)}")
    if labelled:
        for k, v in Counter(c["native_type"] for c in cands if c.get("native_type")).most_common():
            print(f"      {v:>3}  {k}")

    # ---- 4. activation coverage
    rule("Activation")
    withpat = sum(1 for c in cands if c["activation"].get("pattern"))
    print(f"  {withpat}/{total} carry a real trigger  {bar(withpat, total)}")
    print(f"  \033[2m{total - withpat} load unconditionally — every run pays for them\033[0m")

    # ---- 5. scope evidence available (descriptive only)
    #
    # This used to be reported as the "attachment ceiling" with `total` as the
    # denominator, which measured the wrong thing: a method preference that
    # correctly carries no external scope was counted as an attachment failure.
    # Whether a record *needs* a scope follows from its kind, so the real ceiling
    # cannot be computed until classification has run. It now lives in the
    # --classified block below; this is a plain inventory of what evidence exists.
    rule("Scope evidence")

    def strongest_ref_zone(c):
        for z in ("statement", "rationale", "body"):
            if any(r.get("zone") == z for r in c.get("refs") or []
                   if isinstance(r, dict)):
                return z
        # A pre-0.4 candidates.json has refs as bare strings.
        return "body" if c.get("refs") else None

    def hint_quality(c):
        qs = {h.get("quality", "name") for h in c.get("scope_hints") or []}
        for q in ("name", "encoded_path", "container"):
            if q in qs:
                return q
        return None

    zones = Counter(z for z in (strongest_ref_zone(c) for c in cands) if z)
    for z in ("statement", "rationale", "body"):
        if zones.get(z):
            note = {"statement": "named while making the claim — strongest",
                    "rationale": "named in the reasoning",
                    "body": "mentioned only — weak evidence"}[z]
            print(f"  ref in {z:<10} {zones[z]:>3}  \033[2m{note}\033[0m")

    hq = Counter(q for q in (hint_quality(c) for c in cands) if q)
    for q in ("name", "encoded_path", "container"):
        if hq.get(q):
            note = {"name": "real project name",
                    "encoded_path": "encoded local path — run resolve-projects.py",
                    "container": "names a code parent, not a project — unfixable"}[q]
            colour = "\033[33m" if q == "container" else "\033[2m"
            print(f"  path hint: {q:<11} {hq[q]:>3}  {colour}{note}\033[0m")

    bare = sum(1 for c in cands
               if not strongest_ref_zone(c) and not hint_quality(c))
    if bare:
        print(f"  \033[2mno scope evidence at all   {bare:>3}\033[0m")
    print("  \033[2mwhether a record needs a scope depends on its kind — "
          "see the ceiling below\033[0m")

    # ---- 5b. records that say their own subject is still open
    #
    # Paired with age, because the two together are the finding: a plan whose
    # status line reads "awaiting sign-off as of 2026-07-14" and whose file is
    # 54 days old published four tier-1 constraints with no expiry. Neither
    # number alone would have caught it.
    prov = [c for c in cands if c.get("provisional_signals")]
    if prov:
        rule("Unsettled — may be `provisional`")
        gen = d.get("generated_at", "")
        for c in sorted(prov, key=lambda x: x.get("asserted_at") or ""):
            kinds = ", ".join(sorted({s["kind"] for s in c["provisional_signals"]}))
            age = ""
            try:
                from datetime import datetime
                a = datetime.fromisoformat(c["asserted_at"])
                g = datetime.fromisoformat(gen)
                age = f"  \033[2m· {(g - a).days}d old\033[0m"
            except Exception:
                pass
            print(f"  \033[33m?\033[0m {Path(c['source_path']).name}{age}")
            print(f"      {kinds}")
        print("  \033[2ma signal, not a verdict — the rubric decides, but it is "
              "told rather than\n  expected to notice. Check these before "
              "classifying any of them tier 1.\033[0m")

    # ---- 6. timestamp provenance
    rule("Timestamps")
    ts = Counter(c.get("timestamp_source", "?") for c in cands)
    for k, v in ts.most_common():
        note = "tool-maintained" if k == "frontmatter" else "filesystem only, weaker"
        print(f"  {k:<12} {v:>3}  \033[2m{note}\033[0m")

    # ---- 7. unknown frontmatter keys (the alias table's backlog)
    unknown = Counter(k for c in cands for k in c.get("unknown_frontmatter_keys", []))
    if unknown:
        rule("Unrecognized frontmatter keys")
        for k, v in unknown.most_common(10):
            print(f"  {v:>3}×  {k}")
        print("  \033[2ma recurring key here is a missing alias-table row\033[0m")

    # ---- 8. kind distribution + exclusion mix (only with a classification pass)
    if args.classified and Path(args.classified).is_file():
        raw = read_json(args.classified, "classified file")
        kept = raw if isinstance(raw, list) else raw.get("candidates", [])
        excl = [] if isinstance(raw, list) else raw.get("excluded", [])

        rule("Taxonomy fit")
        kinds = Counter(c.get("kind") for c in kept)
        for k, v in kinds.most_common():
            print(f"  {v:>3}  {k:<22} {bar(v, len(kept) or 1, 16)}")

        # ---- the real attachment ceiling, now that kinds are known
        rule("Attachment ceiling")
        need = [c for c in kept if c.get("kind") in REQUIRES_SCOPE]
        opt = len(kept) - len(need)

        # The classifier's hand-written scopes carry no `quality`, so grade them
        # against the collector's hints the same way assemble.py does — else a
        # container hint copied by hand reads as a real scope here.
        by_ch = {c["content_hash"]: c for c in cands}

        def has_scope(c):
            src = by_ch.get((c.get("content_hash") or "").split(":")[-1], {})
            graded = {h.get("text"): h.get("quality")
                      for h in (src.get("scope_hints") or []) if h.get("quality")}
            entries = c.get("claimed_scope")
            if entries is None:
                entries = src.get("scope_hints") or []
            for s in entries:
                q = s.get("quality") or graded.get(s.get("text")) or "name"
                if q not in ("container", "encoded_path"):
                    return True
            return False

        scoped = [c for c in need if has_scope(c)]
        blocking = [c for c in need if not has_scope(c)]
        print(f"  requires scope  {len(need):>3}")
        if need:
            print(f"    scoped        {len(scoped):>3}  {bar(len(scoped), len(need), 18)}")
        if blocking:
            print(f"  \033[33m    blocking      {len(blocking):>3}  "
                  f"← publication blocked until resolved\033[0m")
            for c in blocking[:5]:
                print(f"        \033[2m{c.get('kind')}: "
                      f"{(c.get('statement') or '')[:58]}\033[0m")
        print(f"  scope optional  {opt:>3}  \033[2mpublisher- or org-scoped "
              f"by kind\033[0m")

        rule("Exclusion mix")
        ex_total = sum(e["count"] for e in excl)
        grand = len(kept) + ex_total
        for e in sorted(excl, key=lambda x: -x["count"]):
            print(f"  {e['count']:>3}  {e['reason']:<22} {bar(e['count'], grand or 1, 16)}")
        print(f"  {len(kept):>3}  \033[1mkept\033[0m  \033[2m(claims)\033[0m")

        # The unit decides whether any of this is comparable. `kept` is always
        # claims, so a percentage is only coherent when exclusions are counted
        # in claims too. Counting records against claims was the ambiguity that
        # made an earlier run read as 0% excluded.
        units = {e.get("unit") for e in excl if e.get("unit")}
        unit = units.pop() if len(units) == 1 else None
        if not excl:
            # The case that misled an earlier run: an empty list carries no unit,
            # so it cannot distinguish "Step 1 never ran" from "every record held
            # a kernel, so none was dropped whole". Say which question is open
            # rather than picking an answer.
            print("\n  \033[33mNothing is listed as excluded, and with no entries "
                  "there is no `unit` to\n  read it against — so this does not "
                  "distinguish a skipped Step 1 from a\n  store where every record "
                  "yielded at least one kernel.\033[0m")
            print("  \033[2mEmit an explicit fragment-level count, even a zero, to "
                  "make the two\n  distinguishable.\033[0m")
        elif len(units) > 1 or unit is None:
            print("\n  \033[33mExclusion entries carry no single `unit` — the total "
                  "is not\n  interpretable. Re-emit with one unit throughout.\033[0m")
        elif unit == "fragment":
            pct = round(100 * ex_total / (len(kept) + ex_total)) if (len(kept) + ex_total) else 0
            print(f"\n  \033[2m{pct}% of the claims considered were discarded "
                  f"(fragment-level).\033[0m")
            if pct >= 70:
                print("  \033[33mMostly content the graph already holds — worth "
                      "knowing before building further.\033[0m")
            if ex_total == 0:
                print("  \033[33mNo fragments discarded at all. Step 1 removes more "
                      "than any other\n  filter, so a zero here usually means it "
                      "did not run.\033[0m")
        elif unit == "record":
            print(f"\n  \033[2m{ex_total} of {total} records dropped whole; the "
                  f"other {total - ex_total} yielded\n  {len(kept)} claims between "
                  f"them.\033[0m")
            if ex_total == 0:
                print("  \033[2mNo record was dropped whole. That is normal when "
                      "every record holds at\n  least one kernel — it is not "
                      "evidence either way about Step 1, which\n  filters "
                      "fragments. Re-emit at fragment level to see that work.\033[0m")
        if excl and not any(e["reason"] == "derivable" for e in excl):
            print("  \033[33mNo `derivable` exclusions. That filter should remove "
                  "more than any\n  other; its absence usually means Step 1 was "
                  "skipped.\033[0m")

    if args.sample:
        rule(f"Structured output — first {min(args.sample, total)} record(s)")
        print("  \033[2mthis is what collect.py writes to candidates.json\033[0m")
        print("  \033[2mmemory_id is null until a record is assembled for publication\033[0m\n")
        for c in cands[:args.sample]:
            body = c.get("body") or ""
            shown = dict(c)
            if len(body) > 300:
                shown["body"] = body[:300] + f"… [{len(body)} chars total]"
            for line in json.dumps(shown, indent=2, ensure_ascii=False).splitlines():
                print("  " + line)
            print()

    if d.get("set_aside"):
        rule("Set aside")
        print(f"  {len(d['set_aside'])} unreviewed candidate patch(es) — read, not published")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
