#!/usr/bin/env python3
"""check_citations — the prose that quotes `file.py:123` has to stay attached to the code.

The repo documents itself in one place, README.md, which cites line numbers throughout. A judge who
follows one of those citations to an unrelated function stops trusting the rest of the page. Line
numbers drift silently as code grows — this gate found stale citations on its first run — so the
check is mechanical and runs in CI.

What it catches:
  * a cited file that does not exist;
  * a cited line beyond the end of the file;
  * a cited line (or range) that covers nothing but blank lines;
  * a `test_name` (file.py:NN) citation whose NN is not that test's `def` line -- the shape
    that appears every time a test is inserted, because everything below it moves.

What it does NOT catch: a citation that lands on a real, non-blank line but describes the wrong
thing without naming a test (a helper, a route, a CSS rule). That is a human reading a claim;
the machine cannot do it. A green run here means "no citation points at empty space and no test
pointer is off by a shift", not "every claim is true".

    python scripts/check_citations.py            # report and exit 1 on any problem
    python scripts/check_citations.py -v         # also list every citation that resolved
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# `path/to/file.ext:12`, `path/to/file.ext:12-34`, optionally inside backticks.
CITE = re.compile(r"`?([\w./-]+\.(?:py|ts|tsx|js|mjs|json|svelte|astro|html|css|cedar|md))"
                  r":(\d+)(?:-(\d+))?`?")

# where the docs live, and what never holds citable source. Forward slashes on purpose:
# glob treats a backslash as an escape, so os.path.join here would silently match nothing on
# Windows and the whole docs/ subtree would go unchecked.
DOC_GLOBS = ["*.md", "docs/*.md", "docs/**/*.md"]
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv",
             ".astro", ".angular", ".hypothesis", ".pytest_cache", "data"}
# generated/bundled copies of things are not the source of truth a doc should point at
SKIP_PATHS = {os.path.join("careloop", "web"), os.path.join("preflight", "build")}


def doc_files() -> list[str]:
    out, seen = [], set()
    for pattern in DOC_GLOBS:
        for path in (_norm(p) for p in _walk_glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            out.append(path)
    return out


def _norm(p: str) -> str:
    return os.path.relpath(p, ROOT).replace("\\", "/")


def _walk_glob(pattern: str):
    """Very small glob helper: `*.md` at a dir, or `**/*.md` recursively, rooted at ROOT."""
    import fnmatch
    parts = pattern.split("/")
    cur = [ROOT]
    for part in parts:
        nxt = []
        for d in cur:
            if part == "**":
                for dirpath, dirnames, _ in os.walk(d):
                    dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
                    nxt.append(dirpath)
            elif "*" in part or "?" in part:
                try:
                    nxt += [os.path.join(d, f) for f in sorted(os.listdir(d))
                            if fnmatch.fnmatch(f, part)
                            and os.path.isfile(os.path.join(d, f))]
                except OSError:
                    pass
            else:
                cand = os.path.join(d, part)
                if os.path.exists(cand):
                    nxt.append(cand)
        cur = nxt
    return [p for p in cur if os.path.isfile(p)]


def _index_sources() -> dict[str, list[str]]:
    """basename -> candidate repo-relative paths, for the bare `store.py:93` citations."""
    index: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, ROOT).replace("\\", "/")
        if any(rel.replace("/", os.sep).startswith(sp) for sp in SKIP_PATHS):
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.endswith((".py", ".ts", ".json", ".svelte", ".astro", ".css", ".md",
                            ".html", ".mjs", ".cedar", ".tsx", ".js")):
                index.setdefault(fn, []).append(f"{rel}/{fn}" if rel != "." else fn)
    return index


def resolve(raw: str, index: dict[str, list[str]]) -> tuple[str | None, str]:
    """Return (path-or-None, why). Bare names resolve only if the basename is unique."""
    rel = raw.replace("\\", "/").lstrip("./")
    full = os.path.join(ROOT, rel.replace("/", os.sep))
    if os.path.isfile(full):
        return rel, ""
    hits = sorted(index.get(os.path.basename(rel), []))
    if len(hits) == 1:
        return hits[0], ""
    if not hits:
        return None, "no such file anywhere in the repo"
    return None, f"basename is ambiguous ({', '.join(hits[:4])})"


# `test_the_card_is_...` (test_http.py:283) -- the name and its number must agree.
TEST_REF = re.compile(r"`((?:test_|should_)\w+)`\s*\(\s*`?([\w./-]+\.py)`?\s*:((?:\s*:?\d+(?:-\d+)?,?\s*)+)\)")
NUMS = re.compile(r"\d+(?:-\d+)?")
_DEFS: dict[str, dict[str, int]] = {}


def test_defs(path: str) -> dict[str, int]:
    """test-name -> `def` line, for one source file (cached)."""
    if path not in _DEFS:
        out: dict[str, int] = {}
        try:
            lines = open(os.path.join(ROOT, path.replace("/", os.sep)),
                         encoding="utf-8", errors="ignore").read().splitlines()
        except OSError:
            lines = []
        for i, line in enumerate(lines, 1):
            m = re.match(r"def (test_\w+)\(", line)
            if m:
                out[m.group(1)] = i
        _DEFS[path] = out
    return _DEFS[path]


def name_line_problems(where: str, text: str, index) -> tuple[list[str], int]:
    """One document's `test_x` (file.py:NN) pointers, checked against test_x's own `def`.

    Only pairs we can resolve are checked: the name must be defined in the cited file and the
    names on the line must be as many as the numbers, so a citation whose siblings are named on
    another line is left alone rather than guessed at. Returns (problems, how many were checked)
    -- the count matters: a pass that silently checks nothing looks exactly like a clean one.
    """
    problems: list[str] = []
    named = 0
    for line_no, line in enumerate(text.splitlines(), 1):
        for m in TEST_REF.finditer(line):
            name, cited, block = m.group(1), m.group(2), m.group(3)
            path, _why = resolve(cited, index)
            if not path:
                continue
            defs = test_defs(path)
            if name not in defs:
                continue
            numbers = NUMS.findall(block)
            # the matched name sits at the start of the match, so line[:m.start()] alone would
            # drop it; names listed before it on the SAME line are the positional ones
            names = re.findall(r"`((?:test_|should_)\w+)`", line[:m.start()]) + [name]
            if len(names) != len(numbers):
                continue
            for n, num in zip(names, numbers):
                if n not in defs:
                    continue
                named += 1
                want = defs[n]
                if int(num.split("-")[0]) != want:
                    problems.append(f"{where}:{line_no} `{n}` cites {cited}:{num} "
                                    f"but its def is at {path}:{want}")
    return problems, named


def check_name_lines(index, problems: list[str]) -> int:
    """`test_x` (file.py:NN) has to point at test_x's own `def`.

    The blank-line pass above cannot see this class of drift: adding one test shifts every
    later `def` onto a line that is still real, non-blank code -- 11 of these were live in
    the submission docs at once, all of them mechanically checkable and none caught.
    """
    named = 0
    for doc in doc_files():
        text = open(os.path.join(ROOT, doc.replace("/", os.sep)), encoding="utf-8",
                    errors="ignore").read()
        found, count = name_line_problems(doc, text, index)
        problems.extend(found)
        named += count
    return named


def main(argv: list[str]) -> int:
    verbose = "-v" in argv or "--verbose"
    index = _index_sources()
    problems: list[str] = []
    ok = 0
    for doc in doc_files():
        text = open(os.path.join(ROOT, doc.replace("/", os.sep)), encoding="utf-8",
                    errors="ignore").read()
        for m in CITE.finditer(text):
            raw, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
            path, why = resolve(raw, index)
            if path is None:
                problems.append(f"{doc}: `{raw}:{a}` -> {why}")
                continue
            lines = open(os.path.join(ROOT, path.replace("/", os.sep)), encoding="utf-8",
                         errors="ignore").read().splitlines()
            if a > len(lines) or b > len(lines):
                problems.append(f"{doc}: `{raw}:{a}-{b if b != a else a}` -> {path} has "
                                f"{len(lines)} lines")
                continue
            if b < a:
                problems.append(f"{doc}: `{raw}:{a}-{b}` -> range runs backwards")
                continue
            if all(not lines[i].strip() for i in range(a - 1, b)):
                problems.append(f"{doc}: `{raw}:{a}"
                                f"{'-' + str(b) if b != a else ''}` -> {path} line(s) are blank "
                                f"(the code moved and the citation did not)")
                continue
            ok += 1
            if verbose:
                print(f"  ok  {doc}  {raw}:{a}-{b} -> {lines[a-1].strip()[:60]}")

    named = check_name_lines(index, problems)

    if problems:
        print(f"check_citations: {len(problems)} stale citation(s) "
              f"({ok} resolved cleanly):\n")
        for p in problems:
            print("  - " + p)
        print("\nFix the prose to point at where the code actually is. Blank-line hits almost "
              "always mean lines were added above the cited one.")
        return 1
    print(f"check_citations: {ok} citations resolve inside their files and none lands on "
          f"blank space.")
    if named:
        print(f"  {named} `test_name` (file.py:NN) citations were checked against that "
              f"test's real `def` line.")
    print("  (does not prove the line says what the prose claims - that is still a human job)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
