"""The committed contract layer (PRD Part O engineering standard): `artifacts/contracts/*.schema.json`,
`artifacts/contracts/index.json` and `frontend/client/src/contracts.ts` are a generated PROJECTION of
TOOL_DEFS in careloop/tools.py — never a hand-written description of it.

So the only test that matters here is the drift test: regenerate in memory from the live
defs and compare bytes with what is committed. A hand-edited schema, an un-regenerated
contract after a tools.py change, or a stale extra file must break CI, and the failure has
to name the exact file and the exact command to run.
"""
import hashlib
import importlib.util
import json
import os
import re

from careloop import tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATE_LIKE = re.compile(r"\b20\d{2}-\d{2}-\d{2}([T ]\d{2}:\d{2})?\b")


def load_generator():
    """scripts/ is not a package, so the generator is loaded by path (same trick CI uses)."""
    spec = importlib.util.spec_from_file_location("gen_contracts",
                                                  os.path.join(ROOT, "scripts", "gen_contracts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = load_generator()


def repo_bytes(rel):
    try:
        with open(os.path.join(ROOT, *rel.split("/")), "rb") as fh:
            return fh.read()
    except OSError:
        return None


def drift(files):
    """[(rel, why)] for every generated file that the repo does not already hold, byte for byte."""
    out = []
    for rel, text in sorted(files.items()):
        want = text.encode("utf-8")
        got = repo_bytes(rel)
        if got is None:
            out.append((rel, "missing"))
        elif got != want:
            out.append((rel, "different bytes"))
    return out


def stale_contract_files(files):
    expected = {rel.split("/")[-1] for rel in files if rel.startswith("artifacts/contracts/")}
    cdir = os.path.join(ROOT, "artifacts", "contracts")
    if not os.path.isdir(cdir):
        return ["artifacts/contracts/ directory is missing"]
    return sorted(n for n in os.listdir(cdir)
                  if n.endswith(".json") and n not in expected)


def complain(rows, extra):
    lines = ["", "CONTRACT DRIFT -- committed artifacts/contracts/ no longer match careloop/tools.py.",
             "Regenerate and commit:  python scripts/gen_contracts.py"]
    for rel, why in rows:
        lines.append(f"  - {rel}: {why}")
    for name in extra:
        lines.append(f"  - artifacts/contracts/{name}: stale file, not produced by the generator")
    return "\n".join(lines)


# ------------------------------------------------------------------ the drift gate
def test_committed_contracts_are_exactly_what_the_generator_produces():
    files = gen.render()
    assert os.path.isdir(os.path.join(ROOT, "artifacts", "contracts")), \
        "artifacts/contracts/ missing: run `python scripts/gen_contracts.py`"
    rows = drift(files)
    extra = stale_contract_files(files)
    assert not rows and not extra, complain(rows, extra)


def test_a_hand_edited_schema_fails_the_drift_gate():
    """The gate must actually bite: editing one committed file is drift, not a style choice."""
    files = gen.render()
    assert not drift(files)
    target = os.path.join(ROOT, "artifacts", "contracts", "get_care_plan.schema.json")
    original = repo_bytes("artifacts/contracts/get_care_plan.schema.json")
    try:
        edited = json.loads(original)
        edited["description"] = edited["description"] + " (hand-edited)"
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(edited, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
        rows = drift(files)
        assert [rel for rel, _ in rows] == ["artifacts/contracts/get_care_plan.schema.json"], rows
        assert "python scripts/gen_contracts.py" in complain(rows, [])
    finally:
        with open(target, "wb") as fh:
            fh.write(original)
    assert not drift(files), "the file was not restored: the rest of the suite would lie"


def test_an_unregenerated_tools_change_fails_the_drift_gate():
    """And the other direction: the server defs move but artifacts/contracts/ was not regenerated."""
    files = gen.render()
    defn = next(d for d in tools.TOOL_DEFS if d["name"] == "mark_done")
    original = defn["description"]
    try:
        defn["description"] = original + " (new synonym: ticked it off)"
        moved = gen.render()
        rows = drift(moved)
        assert "artifacts/contracts/mark_done.schema.json" in [rel for rel, _ in rows], rows
        assert "artifacts/contracts/index.json" in [rel for rel, _ in rows], rows
        assert "frontend/client/src/contracts.ts" in [rel for rel, _ in rows], rows
    finally:
        defn["description"] = original
    assert not drift(gen.render())


# ------------------------------------------------------------------ the projection is honest
def test_the_committed_set_covers_every_declared_tool_and_nothing_else():
    names = sorted(d["name"] for d in tools.TOOL_DEFS)
    files = gen.render()
    schema_files = sorted(rel for rel in files if rel.endswith(".schema.json"))
    assert [os.path.basename(s).replace(".schema.json", "") for s in schema_files] == names
    for d in tools.TOOL_DEFS:
        path = os.path.join(ROOT, "artifacts", "contracts", f"{d['name']}.schema.json")
        assert os.path.isfile(path), f"{d['name']} has no committed contract file"


def test_every_committed_schema_restates_the_defs_it_came_from():
    """Faithfulness, checked against the defs rather than against the generator's own output."""
    for d in tools.TOOL_DEFS:
        with open(os.path.join(ROOT, "artifacts", "contracts", f"{d['name']}.schema.json"),
                  encoding="utf-8") as fh:
            c = json.load(fh)
        assert c["name"] == d["name"]
        assert c["description"] == d["description"]
        assert c["inputSchema"] == d["inputSchema"]
        assert c["outputSchema"] == d["outputSchema"]
        assert c["annotations"] == d.get("annotations", {})
        assert c["guest_safe"] is bool(d.get("guest_safe"))
        assert c["ui"] is bool(d.get("ui"))
        assert c["action"] == d.get("action")


def test_the_index_counts_the_tools_the_server_declares():
    with open(os.path.join(ROOT, "artifacts", "contracts", "index.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    assert index["tool_count"] == len(tools.TOOL_DEFS)
    assert [t["name"] for t in index["tools"]] == sorted(t["name"] for t in index["tools"])
    assert set(tools.GUEST_SAFE) == {t["name"] for t in index["tools"] if t["guest_safe"]}
    assert set(tools.UI_TOOLS) == {t["name"] for t in index["tools"] if t["ui"]}


def test_every_indexed_file_hash_matches_the_file_on_disk():
    with open(os.path.join(ROOT, "artifacts", "contracts", "index.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    for t in index["tools"]:
        raw = repo_bytes(f"artifacts/contracts/{t['file']}")
        assert raw is not None, t["file"]
        assert hashlib.sha256(raw).hexdigest() == t["sha256"], t["file"]
    parts = "\n".join(f"{t['name']}:{t['sha256']}" for t in index["tools"]) + "\n"
    assert hashlib.sha256(parts.encode("utf-8")).hexdigest() == index["manifest_sha256"]


def test_the_wire_hash_the_server_publishes_is_recorded_too():
    """index.json quotes careloop/tools.py manifest_hash() — the same value /api/diagnostics
    returns (asserted live in tests/test_platform.py), so both hashes describe one contract."""
    with open(os.path.join(ROOT, "artifacts", "contracts", "index.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    assert index["wire_manifest_hash"] == tools.manifest_hash()


def test_the_typescript_mirror_carries_the_same_contract():
    with open(os.path.join(ROOT, "frontend", "client", "src", "contracts.ts"),
              encoding="utf-8") as fh:
        ts = fh.read()
    with open(os.path.join(ROOT, "artifacts", "contracts", "index.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    assert f"export const TOOL_COUNT = {index['tool_count']};" in ts
    assert index["manifest_sha256"] in ts
    assert index["wire_manifest_hash"] in ts
    for name in (d["name"] for d in tools.TOOL_DEFS):
        assert f"'{name}'," in ts, name


def test_regeneration_is_byte_identical_and_carries_no_timestamp(tmp_path):
    """Byte-stability is what lets a byte comparison be the drift gate at all: sorted keys,
    LF newlines, trailing newline, and nothing that varies between runs."""
    def gen_into(base):
        assert gen.main(["--out", str(base)]) == 0
        out = {}
        for rel in sorted(gen.render()):
            with open(os.path.join(str(base), *rel.split("/")), "rb") as fh:
                out[rel] = fh.read()
        return out

    first, second = gen_into(tmp_path / "a"), gen_into(tmp_path / "b")
    assert first == second, "scripts/gen_contracts.py is not deterministic"
    for rel, raw in first.items():
        text = raw.decode("utf-8")                       # every file is valid UTF-8
        assert "\r" not in text, rel
        assert text.endswith("\n"), rel
        assert not _DATE_LIKE.search(text), f"{rel} appears to carry a timestamp"
