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

    d = json.loads(cpath.read_text(encoding="utf-8"))
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

    if not total:
        print("\n  No changed records. Nothing to report.\n")
        return 0

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

    # ---- 5. scope-reference density (the attachment ceiling)
    rule("Attachment ceiling")

    def usable(c):
        if c.get("refs"):
            return True
        return any(h.get("quality", "name") == "name" for h in c.get("scope_hints") or [])

    withref = sum(1 for c in cands if usable(c))
    encoded = sum(1 for c in cands
                  if not usable(c)
                  and any(h.get("quality") == "encoded_path"
                          for h in c.get("scope_hints") or []))
    print(f"  {withref}/{total} name something resolvable  {bar(withref, total)}")
    if encoded:
        print(f"  \033[33m{encoded} carry only an encoded local path — readable by a "
              f"person, not by the resolver\033[0m")
    floating = total - withref - encoded
    if floating:
        print(f"  \033[2m{floating} would land as floating sentences\033[0m")

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
        raw = json.loads(Path(args.classified).read_text(encoding="utf-8"))
        kept = raw if isinstance(raw, list) else raw.get("candidates", [])
        excl = [] if isinstance(raw, list) else raw.get("excluded", [])

        rule("Taxonomy fit")
        kinds = Counter(c.get("kind") for c in kept)
        for k, v in kinds.most_common():
            print(f"  {v:>3}  {k:<22} {bar(v, len(kept) or 1, 16)}")

        rule("Exclusion mix")
        ex_total = sum(e["count"] for e in excl)
        grand = len(kept) + ex_total
        for e in sorted(excl, key=lambda x: -x["count"]):
            print(f"  {e['count']:>3}  {e['reason']:<22} {bar(e['count'], grand or 1, 16)}")
        print(f"  {len(kept):>3}  \033[1mkept\033[0m")
        if grand:
            pct = round(100 * ex_total / grand)
            print(f"\n  \033[2m{pct}% of this store was excluded.\033[0m")
            if pct >= 70:
                print("  \033[33mMostly content the graph already holds — worth knowing "
                      "before building further.\033[0m")

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
