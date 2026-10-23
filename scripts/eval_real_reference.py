#!/usr/bin/env python3
"""Score the extractor and the voice guard against REAL clinical prose, not our own cases.

Run `python scripts/fetch_reference_data.py` first, then:

    python scripts/eval_real_reference.py            # table + data/reference/report.json
    python scripts/eval_real_reference.py --verbose  # show the misses and the extras

Why this exists: `scripts/eval/cases/` is hand-authored synthetic speech, which is the right
fixture for the gated numbers but the wrong fixture for "would this hold on text nobody
wrote for our parser?".  These sentences come from FDA drug labels and open-access
literature, so the drug names, the strengths and the schedules are the real ones.

The recall here is deliberately NOT a gate and NOT merged into the gated corpus.  A
rules extractor tuned to a clinician's spoken English is expected to miss formal label
prose, and pretending otherwise would be exactly the kind of unfalsifiable number this
repo promises not to publish.  What IS asserted, with a non-zero exit:

  * hallucination rate must be 0      (no item whose words are not in its own transcript)
  * guarded dose leaks must be 0      (nothing survives the Alexa surface gate)
  * UNGUARDED dose leaks must be > 0  (the corpus really does contain dose text, so the
                                       leak test above is not vacuously true)
"""
import argparse
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from careloop import extract, policy_guard as pg               # noqa: E402
from careloop.config import Settings                            # noqa: E402
from careloop import store                                      # noqa: E402
from scripts.eval import metrics                                         # noqa: E402

REF = os.path.join(ROOT, "data", "reference")
DOSE_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|g|kg|ml|iu|units?|mg/kg|mcmol)\b", re.I)

# Which real sentence patterns count as which care-item kind.  Ordered: the first hit wins,
# so an explicit "do not" is never silently absorbed by the generic medication rule.
CUES = (
    ("avoid", re.compile(r"\b(do not|don't|avoid|should not|must not|refrain from|contraindicated)\b", re.I)),
    ("monitor", re.compile(r"\b(monitor|check|measure|weigh|record|observe|test)\b[^.]{0,60}"
                           r"\b(blood pressure|weight|glucose|INR|heart rate|renal function|symptoms|level)\b", re.I)),
    ("appointment", re.compile(r"\b(follow-up|follow up|appointment|re-evaluate|reevaluate|return to the clinic|visit)\b", re.I)),
    ("diet", re.compile(r"\b(diet|dietary|alcohol|grapefruit|salt|sodium|potassium|food|fasting)\b", re.I)),
    ("task", re.compile(r"\b(store|discard|dispose|clean|inspect|shake|prime|rinse|apply)\b", re.I)),
    ("medication", re.compile(r"\b(take|administer|swallow|inhale|inject|oral|orally|by mouth|topical|dose|dosing|tablet|capsule|inhaler|once daily|twice daily|every day)\b", re.I)),
)


def sentences(text: str):
    """Real label prose into candidate sentences, cleaned of tables and cross-references."""
    out = []
    for raw in re.split(r"(?<=[.;])\s+(?=[A-Z0-9\\[])", text or ""):
        s = raw.strip().strip('"').rstrip(".")
        s = re.sub(r"\s+", " ", s)
        if not (40 <= len(s) <= 240):
            continue
        if "[" in s or "]" in s or ":" in s or s.count("(") != s.count(")"):
            continue
        if sum(1 for w in s.split() if w.isupper() and len(w) > 3) > 3:
            continue                       # mostly a product-name / legal fragment
        if s.count("-") > 4:
            continue
        out.append(s)
    return out


def kind_of(s: str):
    for kind, rx in CUES:
        if rx.search(s):
            return kind
    return None


def txt(v):
    """openFDA returns some sections as a list of parallel strings; flatten either shape."""
    if isinstance(v, list):
        return " ".join(txt(x) for x in v)
    return str(v or "")


def xml_text(blob: str):
    """PMC JATS/XML into plain prose: keep abstract and body paragraphs, drop tags."""
    keep = re.findall(r"<(?:abstract|body)[^>]*>(.*?)</(?:abstract|body)>", blob or "", re.S)
    chunks = []
    for part in keep:
        part = re.sub(r"<del>", " ", part)
        part = re.sub(r"<[^>]+>", " ", part)
        chunks.append(part)
    return " ".join(chunks)


LABEL = {"openfda": "openFDA drug labels", "pmc": "PMC open-access abstracts",
         "clintries": "ClinicalTrials.gov eligibility and intervention text",
         "pmc_text": "PMC open-access full text"}

# Which real fields of each source count as instruction-bearing prose.
FIELDS = {
    "openfda": ("dosage_and_administration", "indications", "patient_medication_information",
                "medication_guide", "warnings_and_cautions"),
    "pmc": ("abstract",),
    "clintries": ("brief_summary", "eligibility", "interventions"),
    "pmc_text": (),                                  # handled through xml_text()
}


def build_cases():
    """One case per real source document, gold items being verbatim source sentences."""
    cases, sources = [], {"openfda": 0, "pmc": 0, "clintries": 0, "pmc_text": 0}
    for name in ("openfda.jsonl", "pmc.jsonl", "clintries.jsonl", "pmc_text.jsonl"):
        key = name.split(".")[0]
        path = os.path.join(REF, name)
        if not os.path.exists(path):
            continue
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        for r in rows:
            if key == "pmc_text":
                text = xml_text(r.get("xml", ""))
            else:
                text = " ".join(filter(None, [txt(r.get(f)) for f in FIELDS[key]]))
            gold, seen = [], set()
            for s in sentences(text):
                k = kind_of(s)
                if not k or s in seen:
                    continue
                seen.add(s)
                gold.append({"kind": k, "quote": s, "schedule": "absent"})
            if len(gold) < 2:
                continue
            person = "Papa"                 # adults only, the corpus rule
            lines = [f"Doctor: {g['quote']}" for g in gold]
            cases.append({"id": f"real-{key}-{len(cases)+1:03d}",
                          "title": (r.get("generic_name") or r.get("title") or r.get("topic") or "")[:70],
                          "person": person,
                          "lines": lines,
                          "transcript": "\n".join(lines),
                          "items": gold,
                          "source": r.get("source", name),
                          "license": r.get("license", "")})
            sources[key] += len(gold)
    return cases, sources


def main():
    ap = argparse.ArgumentParser(description="Score CareLoop against real reference prose.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--max", type=int, default=200, help="cases to score")
    a = ap.parse_args()

    cases, counts = build_cases()
    if not cases:
        print("no reference data yet: run python scripts/fetch_reference_data.py first")
        return 2
    cases = cases[:a.max]

    tmp = tempfile.mkdtemp(prefix="careloop-real-")
    settings = Settings(env={"CARELOOP_DB_ROOT": tmp, "CARELOOP_AUTH": "off",
                             "CARELOOP_ENCRYPT_FIELDS": "0", "CARELOOP_PUBLIC_URL": "http://127.0.0.1:8080"})
    store.init(settings)

    rows = []
    tp = fp = fn = 0
    halluc = guarded_leaks = unguarded_leaks = 0
    for c in cases:
        produced = extract.process_visit(c["transcript"], settings, cascade=False)["items"]
        m = metrics.match_items(c["items"], produced)
        tp += len(m["pairs"]); fn += len(m["missed"]); fp += len(m["extra"])
        bad = metrics.hallucinations(produced, c["transcript"])
        halluc += len(bad)
        # A constant ref on purpose: an id that contains digits can itself look like a dose
        # to a leak detector, and this table must not be polluted by the fixture.
        shaped = [metrics.stored_shape(it, ref="i-realitem") for it in produced]
        leaks_on = sum(len(metrics.voice_leaks(c["person"], it)) for it in shaped)
        leaks_off = sum(len(metrics.voice_leaks(c["person"], it, guard=False)) for it in shaped)
        guarded_leaks += leaks_on
        unguarded_leaks += leaks_off
        rows.append({"id": c["id"], "title": c["title"], "gold": len(c["items"]),
                     "produced": len(produced), "matched": len(m["pairs"]),
                     "missed": [c["items"][i]["quote"] for i in m["missed"]],
                     "extra": [produced[i].get("quote", "") for i in m["extra"]],
                     "hallucinations": len(bad), "leaks_guarded": leaks_on,
                     "leaks_unguarded": leaks_off, "source": c["source"]})

    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    from_src = [LABEL[s] for s, n in counts.items() if n]
    summary = {"corpus": {"cases": len(cases), "gold_items": tp + fn,
                          "gold_by_source": counts,
                          "sentences_are_verbatim_from": from_src,
                          "no_patient_data": True},
               "engine": "careloop.extract rules (cascade off, deterministic, offline)",
               "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
               "tp": tp, "fp": fp, "fn": fn,
               "hallucinations": halluc,
               "dose_leaks_with_guard": guarded_leaks,
               "dose_leaks_without_guard": unguarded_leaks,
               "gates": {"hallucination_zero": halluc == 0,
                         "guarded_leaks_zero": guarded_leaks == 0,
                         "corpus_actually_contains_doses": unguarded_leaks > 0},
               "note": "recall here is a stress reading on text nobody wrote for this parser; "
                       "it is NOT the gated corpus number and is NOT comparable to it."}
    os.makedirs(REF, exist_ok=True)
    with open(os.path.join(REF, "report.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "cases": rows}, f, indent=2, ensure_ascii=False)

    print(f"{len(cases)} real-source cases, {tp+fn} gold sentences from: "
          + ", ".join(from_src))
    print(f"precision {prec:.3f}  recall {rec:.3f}  F1 {f1:.3f}   tp {tp} fp {fp} fn {fn}")
    print(f"hallucinations {halluc}  dose leaks through the Alexa gate {guarded_leaks}  "
          f"without the gate {unguarded_leaks}")
    if a.verbose:
        for r in rows:
            if r["missed"] or r["extra"]:
                print(f"\n{r['id']}  {r['title']}")
                for q in r["missed"][:4]:
                    print("   MISSED  " + q[:120])
                for q in r["extra"][:4]:
                    print("   EXTRA   " + q[:120])
    print("\nwrote data/reference/report.json")

    failed = [k for k, ok in summary["gates"].items() if not ok]
    if failed:
        print("GATE FAILED: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
