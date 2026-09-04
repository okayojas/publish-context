#!/usr/bin/env python3
"""
Stage 1-2: resolve, enumerate, diff, parse, tag.

Walks the manifest, resolves each tool's root (env var -> settings key -> default
roots, first hit wins), enumerates against the globs, skips files unchanged since
the last publication, and parses each survivor into a common intermediate.

Emits unclassified candidates. Classification is the model's job, not this script's.

    python3 collect.py [--state PATH] [--out PATH] [--all]

  --all   ignore the state file and re-read everything (for report-only runs)
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
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


HERE = Path(__file__).resolve().parent
REF = HERE.parent / "reference"

# ---------------------------------------------------------------- yaml loading

def _load_yaml(text):
    """Parse a frontmatter block. Uses PyYAML when present, else a minimal parser
    covering the subset these files actually use: scalars, inline lists, block
    lists, and one level of nesting."""
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    out = {}
    stack = [(0, out)]
    pending_list_key, pending_list_indent = None, None

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()

        if line.startswith("- "):
            if pending_list_key is not None:
                target = stack[-1][1]
                target.setdefault(pending_list_key, [])
                target[pending_list_key].append(_scalar(line[2:].strip()))
            continue

        pending_list_key = None
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        target = stack[-1][1]

        if not val:
            child = {}
            target[key] = child
            stack.append((indent, child))
            pending_list_key, pending_list_indent = key, indent
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            target[key] = [_scalar(x.strip()) for x in inner.split(",")] if inner else []
        else:
            target[key] = _scalar(val)

    # a key that opened an empty dict but received list items is a list, not a dict
    def _collapse(d):
        for k, v in list(d.items()):
            if isinstance(v, dict):
                if not v:
                    d[k] = None
                else:
                    _collapse(v)
        return d

    return _collapse(out)


def _scalar(v):
    v = v.strip().strip('"').strip("'")
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False
    return v


def split_frontmatter(text):
    """Return (frontmatter_dict, body, had_frontmatter)."""
    if not text.startswith("---"):
        return {}, text, False
    parts = text.split("\n")
    if not parts[0].strip() == "---":
        return {}, text, False
    for i in range(1, len(parts)):
        if parts[i].strip() in ("---", "..."):
            fm = "\n".join(parts[1:i])
            body = "\n".join(parts[i + 1:])
            return _load_yaml(fm), body.lstrip("\n"), True
    return {}, text, False

# ------------------------------------------------------------- path resolution

def expand(p):
    return Path(os.path.expanduser(os.path.expandvars(str(p))))


def dotted(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def resolve_root(tool):
    """env var -> settings key -> default roots. First hit wins. Returns
    (root, how) so the report can show which step answered."""
    for var in tool.get("env_root", []):
        val = os.environ.get(var)
        if val and expand(val).is_dir():
            return expand(val), f"env:{var}"

    for root in tool.get("default_roots", []):
        base = expand(root)
        if not base.is_dir():
            continue
        for s in tool.get("settings", []):
            sf = base / s["file"]
            if not sf.is_file():
                continue
            try:
                cfg = json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                continue
            val = dotted(cfg, s["key"])
            if isinstance(val, str) and val.strip():
                cand = expand(val)
                if cand.is_dir():
                    return cand, f"settings:{s['file']}:{s['key']}"
        return base, "default_root"

    return None, "absent"


def settings_filenames(tool, root):
    """Some tools let the instruction filename be overridden, one of them with a
    list. Reading only the vendor default silently misses configured installs."""
    extra = []
    for s in tool.get("settings", []):
        if s.get("applies_to") != "human_filenames":
            continue
        sf = root / s["file"]
        if not sf.is_file():
            continue
        try:
            val = dotted(json.loads(sf.read_text(encoding="utf-8")), s["key"])
        except Exception:
            continue
        if isinstance(val, str):
            extra.append(val)
        elif isinstance(val, list):
            extra.extend([v for v in val if isinstance(v, str)])
    return extra

# ------------------------------------------------------------------ authorship

def authorship_for(tool, rel_path):
    """Four rules total, drawn from the manifest. Anything unrecognized defaults
    to 'inferred' — fail toward doubt. Never inferred from content."""
    rule = tool.get("authorship", "path")
    rel_path = _posix(rel_path)
    name = rel_path.rsplit("/", 1)[-1]

    if rule == "filename":
        human = tool.get("human_filenames", [])
        return ("asserted", "filename") if name in human else ("inferred", "filename")

    if rule == "section":
        return ("asserted", "section")  # per-section override happens during parse

    if rule == "path":
        for g in tool.get("globs", {}).get("agent", []):
            if _glob_match(rel_path, g):
                return ("inferred", "path")
        for g in tool.get("globs", {}).get("human", []):
            if _glob_match(rel_path, g):
                return ("asserted", "path")

    return ("inferred", "default")


_GLOB_CACHE = {}


def _glob_to_re(pattern):
    """fnmatch does not treat ** specially and chained str.replace corrupts
    already-substituted regex, so translate in one pass."""
    if pattern in _GLOB_CACHE:
        return _GLOB_CACHE[pattern]
    i, n, out = 0, len(pattern), []
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif pattern.startswith("**", i):
            out.append(".*"); i += 2
        elif pattern[i] == "*":
            out.append("[^/]*"); i += 1
        elif pattern[i] == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(re.escape(pattern[i])); i += 1
    rx = re.compile("".join(out) + r"\Z")
    _GLOB_CACHE[pattern] = rx
    return rx


def _posix(rel):
    """Manifest globs are written POSIX-style. On Windows a relative path comes
    back with backslashes, so every match silently failed and authorship fell
    through to the fail-safe default. Normalize before comparing."""
    return str(rel).replace("\\", "/")


def _glob_match(rel, pattern):
    return _glob_to_re(pattern).match(_posix(rel)) is not None

# ---------------------------------------------------------------- content bits

# Rationales wrap across lines. Capture to the next blank line, the next bold
# marker, or end of text — `(.+)$` stopped at the first newline and truncated
# the single most valuable field mid-sentence.
WHY = re.compile(
    r"^[ \t]*\*\*(?:Why|Rationale|Reason)\b[^:*]*:?\*\*:?[ \t]*(.+?)"
    r"(?=\n[ \t]*\n|\n[ \t]*\*\*|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL)
TICKET = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
# Markdown puts URLs inside backticks and at the ends of sentences, so neither
# character can be part of a reference. `[^\s)>\]]+` kept both and produced
# refs like "https://host/v1`." — unmatchable by any resolver, yet counted as
# attachment.
URL = re.compile(r"https?://[^\s)>\]`\"']+")
PATHY = re.compile(r"(?<![\w/])((?:src|lib|app|tests?|packages?)/[\w./*-]+)")
MENTION = re.compile(r"(?<![\w])@([A-Za-z][\w.-]{2,})")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")

# Trailing punctuation a reference can never legitimately end with. Applied
# after matching rather than baked into the pattern, because a URL may contain
# any of these mid-string.
_REF_TRAIL = ".,;:!?)`\"'>]}"

# Strongest wins when the same reference appears more than once.
_ZONE_RANK = {"statement": 3, "rationale": 2, "body": 1}


def extract_rationale(body):
    m = WHY.search(body)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip() or None


def _refs_in(text):
    """(text, kind) pairs found in one zone, trailing punctuation trimmed."""
    out = []
    for rx, kind in ((TICKET, "ticket"), (URL, "url"), (PATHY, "path")):
        for hit in rx.findall(text or ""):
            hit = hit.rstrip(_REF_TRAIL)
            if hit:
                out.append((hit, kind))
    return out


def extract_refs(statement, rationale, body):
    """References graded by where they appear.

    A reference in the statement is the author naming the subject *while making
    the claim* — the strongest scope evidence available. The same string buried
    in a 27k-character container body is a mention. Reading the whole record as
    one zone made those indistinguishable, so a status dump looked as
    well-attached as a one-line constraint.
    """
    best = {}
    for zone, text in (("statement", statement), ("rationale", rationale),
                       ("body", body)):
        for text_, kind in _refs_in(text):
            prior = best.get(text_)
            if prior is None or _ZONE_RANK[zone] > _ZONE_RANK[prior["zone"]]:
                best[text_] = {"text": text_, "kind": kind, "zone": zone}
    return sorted(best.values(), key=lambda r: (-_ZONE_RANK[r["zone"]], r["text"]))


def detect_subjects(text):
    """Cheap signal only — feeds the sensitivity check downstream. Not identity."""
    return sorted(set(MENTION.findall(text)) | set(EMAIL.findall(text)))


# The cwd git remote was briefly used as a scope hint. Removed: it reported the
# repository the collector was *invoked from*, not the one a memory is about, so
# running it inside any checkout stamped every record with that repo. Scope comes
# from the store's own path (`scope_from_path`) or from references in the text.
# A confidently wrong scope is worse than an absent one.

# ---------------------------------------------------------------------- parse

def _jsonable(v):
    """PyYAML resolves YAML timestamps to real date/datetime objects, while the
    fallback parser returns strings — so whether frontmatter is JSON-serializable
    depended on whether PyYAML happened to be installed. Normalize at the
    boundary rather than hoping."""
    import datetime as _dt
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


def _looks_encoded(text):
    """True for a project-dir name that is really a flattened filesystem path."""
    if len(text) > 48 and text.count("-") >= 4:
        return True
    low = text.lower()
    return (low.startswith(("c--", "d--", "-users-", "-home-"))
            or "-users-" in low or "-home-" in low or "-documents-" in low
            or "-downloads-" in low or "-desktop-" in low)


# Folder names that mark where a person's own paths begin. Kept in step with
# ANCHORS in resolve-projects.py — the two answer the same question from
# opposite ends, one grading a hint and one matching a directory.
_ANCHORS = {"downloads", "documents", "desktop", "projects", "repos",
            "workspace", "dropbox", "onedrive", "developer", "sites"}


def _looks_container(text):
    """True when an encoded hint names a code *parent*, not a project.

    One person's store had 32 of 33 records hinting `-Users-<name>-Developer`:
    the folder they keep every checkout in. That is not unresolvable — it is
    resolvable to something meaningless, which is worse, because a person
    reading "readable by a person, not by the resolver" will try to fix it and
    there is no correct answer to give.

    The test is the anchor landing last: nothing follows the known code parent,
    so the hint names the parent itself. `suggest_name()` in
    resolve-projects.py declines on exactly this shape; here it grades.
    """
    parts = [t for t in text.split("-") if t]
    if not parts:
        return False
    last = None
    for i, tok in enumerate(parts):
        if tok.lower() in _ANCHORS:
            last = i
    return last is not None and last == len(parts) - 1


def _norm_ts(v):
    """Canonicalize a timestamp string so the two parsers agree. PyYAML yields
    `...+00:00` and the fallback yields the source's `...Z` — the same instant,
    two strings. `asserted_at` feeds the validity start downstream, so two
    machines publishing one memory must not disagree about it. Unparseable
    values pass through untouched rather than being lost."""
    if not isinstance(v, str) or not v.strip():
        return v
    from datetime import datetime, timezone
    txt = v.strip()
    for cand in (txt.replace("Z", "+00:00") if txt.endswith("Z") else txt, txt):
        try:
            dt = datetime.fromisoformat(cand)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return v


def _flatten(d, prefix=""):
    """Some tools nest (Claude Code puts `type` under `metadata:`), so match on
    both the dotted path and the leaf name."""
    flat = {}
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            flat.update(_flatten(v, f"{prefix}{k}."))
        else:
            flat[f"{prefix}{k}"] = v
    return flat


def map_frontmatter(fm, aliases):
    """Ten spellings -> four meanings, plus timestamp and identity."""
    out, unknown = {}, []
    lookup = {}
    for field, spec in aliases.items():
        if field.startswith("_") or not isinstance(spec, dict):
            continue
        for k in spec.get("keys", []):
            lookup[k.lower()] = field

    for k, v in _flatten(fm).items():
        leaf = str(k).rsplit(".", 1)[-1].lower()
        field = lookup.get(str(k).lower()) or lookup.get(leaf)
        if field is None:
            unknown.append(k)
        elif field != "ignore":
            out[field] = _jsonable(v)

    activation = {}
    pat = out.get("activation_pattern")
    if pat:
        activation["pattern"] = [pat] if isinstance(pat, str) else list(pat)
        activation["inclusion"] = "fileMatch"
        activation["source"] = "frontmatter"
    else:
        inc = out.get("activation_inclusion")
        truthy = aliases.get("activation_inclusion", {}).get("truthy", [True, "true", "always"])
        activation["pattern"] = []
        activation["inclusion"] = "always" if (inc in truthy or inc is None) else str(inc)
        activation["source"] = "frontmatter" if inc is not None else "default"

    return {
        "activation": activation,
        "statement": out.get("statement"),
        "native_type": out.get("native_type"),
        "timestamp": out.get("timestamp"),
        "identity": out.get("identity"),
    }, unknown


def split_sections(body, heading):
    """Gemini-shape: agent facts appended under a fixed heading INSIDE a
    hand-written file. Split it; never tag the file whole."""
    idx = body.find(heading)
    if idx < 0:
        return [(body, "asserted")]
    human = body[:idx].strip()
    agent = body[idx + len(heading):].strip()
    parts = []
    if human:
        parts.append((human, "asserted"))
    if agent:
        parts.append((agent, "inferred"))
    return parts


def parse_file(path, tool, root, aliases, state, project_map=None):
    rel = _posix(path.relative_to(root))
    raw = path.read_text(encoding="utf-8", errors="replace")
    src_hash = hashlib.sha256(raw.encode()).hexdigest()

    prior = state.get("files", {}).get(str(path))
    reserved = state.get("pending_ids") or {}
    if prior and prior.get("source_hash") == src_hash:
        return None, "unchanged"

    fm, body, had_fm = split_frontmatter(raw)
    mapped, unknown = map_frontmatter(fm, aliases)
    base_auth, signal = authorship_for(tool, rel)

    if tool.get("authorship") == "section" and tool.get("section_heading"):
        chunks = split_sections(body, tool["section_heading"])
    else:
        chunks = [(body, base_auth)]

    ts, ts_src = _norm_ts(_jsonable(mapped.get("timestamp"))), "frontmatter"
    if isinstance(ts, str) and not ts.strip():
        ts = None
    if not ts:
        ts = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        ts_src = "mtime"

    scope_hints = []
    sp = tool.get("scope_from_path")
    if sp:
        m = re.search(sp, rel)
        if m and m.groupdict().get("scope"):
            raw = m.group("scope")
            # A verified project map (scripts/resolve-projects.py) upgrades an
            # encoded path to a real repository name. Verified by re-encoding a
            # candidate directory and comparing, never by decoding the hint.
            mapped_name = (project_map or {}).get(raw)
            # A "CONFIRM:" value is a suggestion awaiting a human, not a mapping.
            if isinstance(mapped_name, str) and mapped_name.startswith("CONFIRM:"):
                mapped_name = None
            if mapped_name:
                scope_hints.append({
                    "text": mapped_name,
                    "evidence": "project_map",
                    "quality": "name",
                    "encoded_from": raw,
                })
                sp = None
            else:
                # Graded, because some tools encode a whole filesystem path into
                # the directory name. That is readable by a person and useless to
                # a resolver, and counting it as attachment inflates the metric.
                # It cannot be decoded either: separators and real hyphens are the
                # same character, so the segmentation is genuinely ambiguous.
                #
                # 'container' is the third case and the worst: the hint names the
                # folder a person keeps all their checkouts in. No human can fix
                # it, so it is reported as unfixable rather than as pending.
                if not _looks_encoded(raw):
                    quality = "name"
                elif _looks_container(raw):
                    quality = "container"
                else:
                    quality = "encoded_path"
                scope_hints.append({
                    "text": raw,
                    "evidence": "path",
                    "quality": quality,
                })

    out = []
    for i, (chunk, auth) in enumerate(chunks):
        if not chunk.strip():
            continue
        _rationale = extract_rationale(chunk)
        out.append({
            "memory_id": (mapped.get("identity")
                          or (prior or {}).get("memory_id")
                          or reserved.get(str(path) if i == 0 and len(chunks) == 1
                                          else f"{path}#{i}")),
            "tool": tool["tool"],
            "source_path": str(path),
            "source_rel": rel,
            "source_has_frontmatter": had_fm,
            "chunk_index": i if len(chunks) > 1 else None,
            "content_hash": hashlib.sha256(chunk.encode()).hexdigest(),
            "source_hash": src_hash,
            "authority": auth,
            "authority_signal": signal if len(chunks) == 1 else "section",
            "native_type": mapped.get("native_type"),
            "statement": mapped.get("statement"),
            "rationale": _rationale,
            "body": chunk.strip(),
            "activation": mapped["activation"],
            # The chunk is passed as the body zone even though it contains the
            # rationale too — a ref found in both is graded by the stronger
            # zone, so the overlap costs nothing and needs no text surgery.
            "refs": extract_refs(mapped.get("statement"), _rationale, chunk),
            "scope_hints": scope_hints,
            "asserted_at": ts,
            "timestamp_source": ts_src,
            "subjects_detected": detect_subjects(chunk),
            "unknown_frontmatter_keys": unknown,
            "documented_source": tool.get("documented", True),
        })
    return out, "changed"

# ----------------------------------------------------------------------- main

def enumerate_files(root, patterns, excludes):
    seen = []
    for pat in patterns:
        for p in sorted(root.glob(pat)):
            if not p.is_file():
                continue
            rel = _posix(p.relative_to(root))
            if any(_glob_match(rel, e) for e in excludes):
                continue
            if p not in seen:
                seen.append(p)
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(Path.home() / ".arionix" / "publish-state.json"))
    ap.add_argument("--out", default=str(Path.home() / ".arionix" / "candidates.json"))
    ap.add_argument("--all", action="store_true", help="ignore state, re-read everything")
    ap.add_argument("--project-map",
                    default=str(Path.home() / ".arionix" / "project-map.json"))
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve and list what would be read; open nothing")
    args = ap.parse_args()

    manifest = json.loads((REF / "manifest.json").read_text(encoding="utf-8"))
    aliases = json.loads((REF / "alias-table.json").read_text(encoding="utf-8"))

    project_map = {}
    pm = Path(args.project_map)
    if pm.is_file():
        try:
            project_map = json.loads(pm.read_text(encoding="utf-8"))
        except Exception:
            project_map = {}

    state_path = Path(args.state)
    state = {}
    if state_path.is_file() and not args.all:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    sources, candidates, set_aside = [], [], []

    for tool in manifest["tools"]:
        root, how = resolve_root(tool)
        if root is None:
            sources.append({"tool": tool["tool"], "display": tool["display"],
                            "status": "absent", "resolution": how})
            continue

        pats = list(tool["globs"].get("human", [])) + list(tool["globs"].get("agent", []))
        pats += settings_filenames(tool, root)
        files = enumerate_files(root, pats, tool.get("exclude", []))

        if args.dry_run:
            sources.append({
                "tool": tool["tool"], "display": tool["display"], "status": "ok",
                "resolved_root": str(root), "resolution": how,
                "documented": tool.get("documented", True),
                "counts": {"found": len(files), "changed": 0, "unchanged": 0},
                "would_read": [_posix(f.relative_to(root)) for f in files],
                "errors": [],
            })
            continue

        changed = unchanged = 0
        errors = []
        for f in files:
            try:
                recs, status = parse_file(f, tool, root, aliases, state, project_map)
            except Exception as e:
                errors.append({"path": str(f), "error": f"{type(e).__name__}: {e}"})
                continue
            if status == "unchanged":
                unchanged += 1
                continue
            changed += 1
            for r in recs or []:
                candidates.append(r)

        for pg in tool.get("patch_globs", []):
            for p in sorted(root.glob(pg)):
                if p.is_file():
                    set_aside.append({"tool": tool["tool"], "path": str(p),
                                      "reason": "unreviewed_candidate_patch"})

        sources.append({
            "tool": tool["tool"], "display": tool["display"],
            "status": "ok" if not errors else "partial",
            "resolved_root": str(root), "resolution": how,
            "documented": tool.get("documented", True),
            "counts": {"found": len(files), "changed": changed, "unchanged": unchanged},
            "errors": errors,
        })

    for u in manifest.get("unreachable", []):
        sources.append({"tool": u["tool"], "status": "unsupported", "reason": u["reason"]})

    result = {
        "collector_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "candidates": candidates,
        "set_aside": set_aside,
    }

    if args.dry_run:
        print("DRY RUN — no file was opened, nothing written\n")
        for s_ in sources:
            if s_["status"] == "absent":
                print(f"  ○ {s_.get('display', s_['tool'])}: not installed")
            elif s_["status"] == "unsupported":
                print(f"  × {s_['tool']}: unsupported")
            else:
                print(f"  ● {s_.get('display')}  [{s_['resolution']}]  {s_['resolved_root']}")
                for rel in s_.get("would_read", []):
                    print(f"       would read  {rel}")
                if not s_.get("would_read"):
                    print("       (nothing matched)")
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str, ensure_ascii=False),
                   encoding="utf-8")

    ok = sum(1 for s in sources if s["status"] in ("ok", "partial"))
    print(f"sources: {ok} present, {sum(1 for s in sources if s['status']=='absent')} absent, "
          f"{sum(1 for s in sources if s['status']=='unsupported')} unsupported")
    print(f"candidates: {len(candidates)} changed  ·  set aside: {len(set_aside)}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
