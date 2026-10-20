#!/usr/bin/env python3
"""Uniform test harness for this repository.

Usage: python3 validate.py   (run from the repo root)
Deterministic, offline. Exits 0 with `HARNESS GREEN` only if every stage
passes; otherwise exits non-zero with `HARNESS RED: <failing stages>`.

Stages:
  1. import-compile  Compile every .py file and import all packages found in
                     pyproject.toml (src layout), including every subpackage.
  2. pytest          Run the FULL existing test suite via a pytest subprocess
                     over the whole repo (--import-mode=importlib handles the
                     per-system tests/test_smoke.py duplicate basenames;
                     all tests run, none skipped or weakened). Failures
                     propagate.
  3. fis-scan        Scan the repo tree with the forbidden-reference scanner
                     (same patterns as ~/workspace/github-fis-watch/fis_scan.py).
                     Candidates are judged in context:
                       * the harness's own pattern definitions (this file) are
                         benign meta-references (a scanner must name patterns;
                         same reason the upstream scanner lives outside scanned
                         trees),
                       * literals inside forbidden-name guard tests
                         (test_no_forbidden_names / BANNED lists) are benign
                         meta-references -- they exist precisely to assert the
                         terms are absent elsewhere; removing them would weaken
                         tests,
                       * anything else matching is a REAL reference and fails
                         the stage.  It is never auto-removed: user-decision
                         blockers (identity/branding) must be resolved by the
                         owner, not by this harness.
  4. consistency     Existence checks only: markdown links in every README.md
                     resolve; results/ and experiments/ files named by READMEs
                     exist; every system directory ships its entry point
                     (demo.py or experiments/run_experiments.py),
                     tests/test_smoke.py, and results/results.md.
"""

from __future__ import annotations

import compileall
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
FIS_SCAN_PATH = Path.home() / "workspace" / "github-fis-watch" / "fis_scan.py"

# ---------------------------------------------------------------- patterns
# Embedded copy of the fis_scan.py patterns, used only if the external
# scanner is unavailable. Word-boundary discipline matches upstream.
EMBEDDED_PATTERNS = [
    ("fisglobal", re.compile(r"fisglobal", re.I)),
    ("fis-global", re.compile(r"fis[-\s]?global", re.I)),
    ("FIS-word", re.compile(r"(?<![A-Za-z])FIS(?![A-Za-z])")),
    ("fisglobal-domain", re.compile(r"[\w.-]*fisglobal\.com", re.I)),
    ("Zenith-codename", re.compile(r"zenith", re.I)),
    ("GOS-word", re.compile(r"(?<![A-Za-z])GOS(?![A-Za-z])")),
    ("fis-path-segment", re.compile(r"(?<![\w])fis[/_\-.](?![a-z]*?(?:h\b))", re.I)),
]
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
              ".zip", ".gz", ".woff", ".woff2", ".mp4", ".mp3", ".pyc", ".npz"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}

RESULTS = {}


def stage(name):
    print(f"\n=== STAGE: {name} ===")


def verdict(name, ok, detail=""):
    RESULTS[name] = ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))


def find_py_files():
    """Every .py file that belongs to the repo (excludes .git etc.)."""
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        if Path(dirpath).name.endswith(".egg-info"):
            continue
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                files.append(Path(dirpath) / fn)
    return files


def stage1_import_compile():
    stage("1 import/compile")
    ok = True
    try:
        proj = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["name"]
    except Exception as e:  # noqa: BLE001
        verdict("import-compile", False, f"cannot parse pyproject.toml: {e}")
        return
    print(f"project: {proj}")

    py_files = find_py_files()
    for f in py_files:
        if not compileall.compile_file(str(f), quiet=2, force=False):
            print(f"  compile FAILED: {f.relative_to(ROOT)}")
            ok = False
    print(f"  compiled {len(py_files)} .py files")

    src = SRC
    toplevel = sorted(p.name for p in src.iterdir()
                      if (p / "__init__.py").is_file())
    if not toplevel:
        verdict("import-compile", False, f"no packages under {src}")
        return
    print(f"  packages: {toplevel}")
    import_code = (
        "import importlib, pkgutil\n"
        "mods = []\n"
        f"for _top in {toplevel!r}:\n"
        "    _m = importlib.import_module(_top)\n"
        "    mods.append(_top)\n"
        "    for _i in pkgutil.walk_packages(_m.__path__, prefix=_top + '.'):\n"
        "        importlib.import_module(_i.name)\n"
        "        mods.append(_i.name)\n"
        "print('IMPORTED', len(mods))\n"
        "for _x in sorted(mods): print('  ', _x)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-c", import_code],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    print(r.stdout, end="")
    if r.returncode != 0:
        print(r.stderr, end="")
        ok = False
    verdict("import-compile", ok)


def stage2_pytest():
    stage("2 pytest (full suite)")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    # --import-mode=importlib is the root-cause fix for same-basename
    # per-system tests/test_smoke.py collection conflicts; every test
    # file is collected and run, none skipped or weakened.
    # -p no:cacheprovider keeps the tree clean of .pytest_cache.
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--import-mode=importlib",
         "-p", "no:cacheprovider", "."],
        cwd=ROOT, env=env, capture_output=True, text=True)
    tail = r.stdout.strip().splitlines()[-25:]
    for line in tail:
        print(f"  {line}")
    if r.returncode != 0:
        print(f"  --- stderr tail ---")
        for line in r.stderr.strip().splitlines()[-10:]:
            print(f"  {line}")
    verdict("pytest", r.returncode == 0,
            "exit 0" if r.returncode == 0 else f"exit {r.returncode}")


def collect_candidates():
    """Return list of dicts {file, line, pattern, text}."""
    if FIS_SCAN_PATH.is_file():
        r = subprocess.run([sys.executable, str(FIS_SCAN_PATH), str(ROOT)],
                           capture_output=True, text=True)
        out = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                hit = json.loads(line)
                f = Path(hit["file"])
                if f.is_absolute():
                    try:
                        hit["file"] = str(f.relative_to(ROOT))
                    except ValueError:
                        pass
                out.append(hit)
        return out
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in BINARY_EXT:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = str(p.relative_to(ROOT))
            for i, line in enumerate(text.splitlines(), 1):
                for name, rx in EMBEDDED_PATTERNS:
                    if rx.search(line):
                        out.append({"file": rel, "line": i,
                                    "pattern": name, "text": line.strip()[:300]})
                        break
    return out


def in_forbidden_guard(path: Path, lineno: int) -> bool:
    """True iff the candidate sits inside a forbidden-name guard test."""
    if not ("/tests/" in str(path).replace(os.sep, "/")
            and path.name.startswith("test_")):
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
    if re.search(r"\bBANNED\s*=", line):
        return True  # the banned-term list definition itself
    enclosing = None
    for prev in reversed(lines[:lineno - 1]):
        m = re.match(r"\s*def\s+([A-Za-z0-9_]+)\s*\(", prev)
        if m:
            enclosing = m.group(1)
            break
    return enclosing is not None and (
        "forbidden" in enclosing.lower() or "banned" in enclosing.lower())


def stage3_fis():
    stage("3 forbidden-reference scan")
    candidates = collect_candidates()
    print(f"  candidates: {len(candidates)}")
    real_hits = []
    for c in candidates:
        fpath = ROOT / c["file"]
        print(f"  candidate [{c['pattern']}] {c['file']}:{c['line']}: {c['text']}")
        if c["file"] == "validate.py":
            print("    -> judged BENIGN (scanner's own pattern definitions; "
                  "a scanner must name its patterns)")
            continue
        if in_forbidden_guard(fpath, c["line"] or 0):
            print("    -> judged BENIGN (banned-term guard test literal: asserts "
                  "the terms are absent elsewhere; removal would weaken tests)")
            continue
        print("    -> judged REAL")
        real_hits.append(c)
    verdict("fis-scan", not real_hits,
            f"{len(candidates)} candidates, {len(real_hits)} REAL" if candidates
            else "no candidates")


def stage4_consistency():
    stage("4 results/docs consistency")
    problems = []
    readmes = sorted(p for p in ROOT.rglob("README.md")
                     if not any(part in SKIP_DIRS for part in p.parts))
    systems = sorted(d for d in ROOT.iterdir()
                     if d.is_dir() and re.match(r"^\d\d-", d.name))
    print(f"  READMEs: {len(readmes)}, systems: {[d.name for d in systems]}")

    for md in readmes:
        text = md.read_text()
        links = re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)
        for t in links:
            t = t.split("#")[0].strip()
            if not t or t.startswith(("http://", "https://", "mailto:")):
                continue
            target = (md.parent / t).resolve()
            try:
                target.relative_to(ROOT.resolve())
            except ValueError:
                problems.append(f"{md.relative_to(ROOT)}: link {t} escapes repo")
                continue
            if not target.exists():
                problems.append(f"{md.relative_to(ROOT)}: link target missing: {t}")
        for t in sorted(set(re.findall(r"`((?:results|experiments)/[^`\s]+)`", text))):
            tt = t.rstrip(".,;:")
            bases = [md.parent]
            if md.parent == ROOT:
                bases += [s for s in systems]
            if not any((b / tt).exists() for b in bases):
                problems.append(
                    f"{md.relative_to(ROOT)}: referenced file missing: {tt}")

    for s in systems:
        if not (s / "demo.py").exists() and not (s / "experiments" / "run_experiments.py").exists():
            problems.append(f"{s.name}: no entry point (demo.py nor experiments/run_experiments.py)")
        if not (s / "tests" / "test_smoke.py").exists():
            problems.append(f"{s.name}: tests/test_smoke.py missing")
        if not (s / "results" / "results.md").exists():
            problems.append(f"{s.name}: results/results.md missing")

    for p in problems:
        print(f"  MISSING: {p}")
    verdict("consistency", not problems,
            f"{len(problems)} problem(s)" if problems else "all references resolve")


def main():
    stage1_import_compile()
    stage2_pytest()
    stage3_fis()
    stage4_consistency()
    failed = [k for k, ok in RESULTS.items() if not ok]
    print()
    if failed:
        print(f"HARNESS RED: {failed}")
        return 1
    print("HARNESS GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
