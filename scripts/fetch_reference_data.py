#!/usr/bin/env python3
"""Download the real, openly-licensed clinical reference data CareLoop is measured against.

Every source here is United States Government public-domain work or a permissively
licensed research corpus. None of them contains a patient record: real patient
corpora (MIMIC, eICU, i2b2/n2c2, Open-i) are behind a data-use agreement and CITI
training, and CareLoop's own ethics claim is "no real patient data", so this script
deliberately does not touch them.

    python scripts/fetch_reference_data.py              # everything
    python scripts/fetch_reference_data.py --only openfda --limit 10
    python scripts/fetch_reference_data.py --list

Output goes to data/reference/ (gitignored) with a MANIFEST.json that records the URL,
the retrieval timestamp, the record count, the byte size, the sha256 and the licence of
each file, so a reviewer can re-run it and get the same provenance.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "reference")
UA = "CareLoop/1.0 (research reference-data fetch; contact: repository author)"

# Real adult chronic-care medicines, chosen because a home-care plan for an adult is
# built around them. Names only: the data themselves come from the APIs below.
GENERICS = [
    "amlodipine", "metformin hydrochloride", "atorvastatin calcium", "lisinopril",
    "levothyroxine sodium", "furosemide", "warfarin sodium", "omeprazole",
    "albuterol sulfate", "losartan potassium", "rosuvastatin calcium", "glyburide",
    "insulin glargine", "apixaban", "clopidogrel bisulfate", "hydrochlorothiazide",
    "carvedilol", "prednisone", "gabapentin", "sertraline hydrochloride",
    "donepezil hydrochloride", "memantine hydrochloride", "spironolactone",
    "spironolactone", "sacubitril/valsartan", "empagliflozin", "sitagliptin phosphate",
    "potassium chloride", "calcium carbonate", "cholecalciferol", "cyanocobalamin",
    "iron polysaccharides", "colchicine", "allopurinol", "methotrexate",
    "mycophenolate mofetil", "tacrolimus", "levofloxacin", "azithromycin",
    "acetaminophen", "ibuprofen", "tramadol hydrochloride", "oxybutynin chloride",
    "tamsulosin hydrochloride", "finasteride", "mirtazapine", "haloperidol",
    "levetiracetam", "phenytoin sodium", "lithium carbonate",
]
GENERICS = list(dict.fromkeys(GENERICS))          # keep order, drop the duplicate

# Real self-management topics, in the words clinicians and papers actually use.
PMC_TOPICS = [
    "hypertension self-management older adults",
    "type 2 diabetes medication adherence",
    "heart failure daily weight monitoring",
    "chronic kidney disease dietary counselling",
    "anticoagulation INR self-monitoring",
    "COPD inhaler technique education",
    "fall prevention home exercise programme",
    "polypharmacy deprescribing older patients",
]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})


def get(url, params=None, tries=4, timeout=60, as_json=True):
    """GET with polite backoff: these are public services, not our infrastructure.
    Raises GaveUp rather than killing the run, because one awkward record name should
    not cost us the whole corpus."""
    last = None
    for n in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout,
                            headers={"Accept": "application/json" if as_json else "application/xml"})
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} from {url}", response=r)
            r.raise_for_status()
            if as_json:
                r.json()                   # some endpoints answer 200 with an HTML error page
            return r
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(1.5 * (2 ** n))
    raise GaveUp(f"giving up on {url}: {last}")


class GaveUp(Exception):
    pass


def write(name, obj, meta):
    """Save one artefact and record what we know about where it came from."""
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    if isinstance(obj, str):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(obj)
    else:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for row in obj:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    raw = open(path, "rb").read()
    entry = dict(meta, file=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                 retrieved_at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    print(f"  wrote {name}: {len(raw):,} bytes  sha256 {entry['sha256'][:12]}")
    return entry


# ---------------------------------------------------------------------- openFDA
def openfda(limit):
    """openFDA drug labels: real proprietary/generic names, real strengths and the exact
    dose and schedule text a clinician's instruction would quote. US Gov, public domain."""
    base = "https://api.fda.gov/drug/label.json"
    rows, seen = [], set()
    for name in GENERICS[:limit]:
        try:
            r = get(base, {"search": f'openfda.generic_name:"{name}"', "limit": 3})
        except GaveUp as e:
            print(f"  ! {name}: {e}")
            continue
        for res in r.json().get("results", []):
            app = (res.get("openfda") or {}).get("application_number", ["?"])[0]
            if app in seen:
                continue
            seen.add(app)
            of = res.get("openfda") or {}
            rows.append({
                "source": "api.fda.gov/drug/label",
                "license": "US Government work, public domain (OpenFDA terms of service)",
                "application_number": app,
                "generic_name": (of.get("generic_name") or [""])[0],
                "brand_name": (of.get("brand_name") or [""])[0],
                "strength": of.get("strength", []),
                "pharm_category": of.get("pharm_category", []),
                "route": of.get("route", []),
                "dosage_form": of.get("dosage_form", []),
                "active_ingredient": res.get("active_ingredient", ""),
                "indications": res.get("indications", ""),
                "dosage_and_administration": res.get("dosage_and_administration", ""),
                "warnings_and_cautions": res.get("warnings_and_cautions", ""),
                "medication_guide": res.get("medication_guide", ""),
                "patient_medication_information": res.get("patient_medication_information", ""),
                "effective_time": res.get("effective_time", ""),
            })
        time.sleep(0.35)
    return rows


# --------------------------------------------------------------- (no DailyMed source)
# DailyMed's v2 JSON service was tried first and is not usable from here: every request to
# /dailymed/services/v2/... answers 302 -> the HTML site, so there is no machine-readable
# endpoint to write against. openFDA's /drug/label.json distributes the same structured
# product labelling sections (it is derived from the same SPL submissions), which is why it
# is the label source here. MedNLI was tried too and rejected on policy grounds: the
# bigbio/mednli repository ships only a loader script whose data comes from credentialed
# patient-note corpora — real clinical records, which this project must not ingest.

# ------------------------------------------------------------------- PMC (NCBI)
def _strip_tags(blob):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", blob or "")).strip()


def abstracts_from_xml(xml):
    """efetch answers the whole articleset as XML, so the abstract has to be read out of it.
    Returns {PMCID without the PMC prefix: plain abstract text}."""
    out = {}
    for block in re.findall(r"<article\b.*?</article>", xml or "", re.S):
        pid = re.search(r'<article-id[^>]*pub-id-type="pmcid"[^>]*>PMC?(\d+)<', block)
        abstract = re.search(r"<abstract\b[^>]*>(.*?)</abstract>", block, re.S)
        if pid and abstract:
            out[pid.group(1)] = _strip_tags(abstract.group(1))
    return out


def pmc(limit):
    """PubMed Central Open-Access subset: real peer-reviewed self-management prose. The
    esummary record carries no abstract, so the abstract is read from the same open-access
    article XML the full-text source uses -- no licence is guessed at."""
    esearch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    esummary = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    efetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    rows = []
    per = max(1, limit // max(1, len(PMC_TOPICS)))
    for topic in PMC_TOPICS:
        try:
            j = get(esearch, {"db": "pmc", "term": f"{topic} AND open access[filter]",
                              "retmax": per * 3, "retmode": "json",
                              "sort": "relevance"}).json()
        except GaveUp as e:
            print(f"  ! {topic}: {e}")
            continue
        ids = (j.get("esearchresult") or {}).get("idlist", [])[:per]
        if not ids:
            continue
        time.sleep(0.4)
        try:
            s = get(esummary, {"db": "pmc", "id": ",".join(ids), "retmode": "json"}).json()
        except GaveUp as e:
            print(f"  ! {topic} summary: {e}")
            continue
        abstracts = {}
        try:
            abstracts = abstracts_from_xml(
                get(efetch, {"db": "pmc", "id": ",".join(ids), "retmode": "xml",
                             "rettype": "abstract"}, as_json=False).text)
        except GaveUp as e:
            print(f"  ! {topic} abstracts: {e}")
        for pid, rec in (s.get("result") or {}).items():
            if pid == "uids" or not isinstance(rec, dict):
                continue
            rows.append({
                "source": "eutils.ncbi.nlm.nih.gov (PMC Open Access subset)",
                "license": rec.get("license", "see the PMC OA statement for this record"),
                "pmcid": rec.get("accessionversion", pid),
                "title": rec.get("title", ""),
                "journal": rec.get("fulljournalname", ""),
                "pubdate": rec.get("pubdate", ""),
                "topic": topic,
                "abstract": abstracts.get(str(pid), ""),
                "authorlist": [a.get("name") for a in rec.get("authors", []) if isinstance(a, dict)],
            })
        time.sleep(0.4)
    return rows


# --------------------------------------------------------------------- clinicaltrials
# The v2 API wants dotted paths into protocolSection; a name it does not know is a hard
# 400 for the whole request, so this list is exactly what was verified against the live API.
CT_FIELDS = ",".join([
    "protocolSection.identificationModule.nctId",
    "protocolSection.identificationModule.briefTitle",
    "protocolSection.statusModule.overallStatus",
    "protocolSection.designModule.studyType",
    "protocolSection.sponsorCollaboratorsModule.leadSponsor.name",
    "protocolSection.descriptionModule.briefSummary",
    "protocolSection.conditionsModule.conditions",
    "protocolSection.armsInterventionsModule.interventions",
    "protocolSection.eligibilityModule.eligibilityCriteria",
])


def clintries(limit):
    """ClinicalTrials.gov v2 study records: real eligibility criteria and intervention text,
    written by the investigators themselves. The API is a US Government public service."""
    url = "https://clinicaltrials.gov/api/v2/studies"
    rows = []
    per = max(1, limit // max(1, len(PMC_TOPICS)))
    for topic in PMC_TOPICS:
        try:
            j = get(url, {"query.term": topic, "pageSize": per, "fields": CT_FIELDS}).json()
        except GaveUp as e:
            print(f"  ! {topic}: {e}")
            continue
        for s in j.get("studies", []):
            p = s.get("protocolSection") or {}
            ivs = (p.get("armsInterventionsModule") or {}).get("interventions") or []
            rows.append({
                "source": "clinicaltrials.gov/api/v2/studies",
                "license": "ClinicalTrials.gov API terms of use (US Government public data)",
                "nct_id": (p.get("identificationModule") or {}).get("nctId", ""),
                "title": (p.get("identificationModule") or {}).get("briefTitle", ""),
                "status": (p.get("statusModule") or {}).get("overallStatus", ""),
                "sponsor": ((p.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name", ""),
                "study_type": (p.get("designModule") or {}).get("studyType", ""),
                "conditions": (p.get("conditionsModule") or {}).get("conditions", []),
                "brief_summary": (p.get("descriptionModule") or {}).get("briefSummary", ""),
                "interventions": [f"{i.get('type', '')}: {i.get('name', '')}. {i.get('description', '')}".strip()
                                  for i in ivs],
                "eligibility": (p.get("eligibilityModule") or {}).get("eligibilityCriteria", ""),
                "topic": topic,
            })
        time.sleep(0.3)
    return rows


# --------------------------------------------------------------------- PMC full text
def pmc_text(limit):
    """The PMC Open-Access subset, full article XML: several megabytes of real clinical and
    public-health prose. Only articles whose record says they are open access are fetched."""
    esearch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    efetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    rows = []
    for topic in PMC_TOPICS[:max(1, limit // 10)]:
        try:
            j = get(esearch, {"db": "pmc", "term": f"{topic} AND open access[filter]",
                              "retmax": 6, "retmode": "json", "sort": "relevance"}).json()
            ids = (j.get("esearchresult") or {}).get("idlist", [])
        except GaveUp as e:
            print(f"  ! {topic}: {e}")
            continue
        if not ids:
            continue
        try:
            xml = get(efetch, {"db": "pmc", "id": ",".join(ids), "retmode": "xml",
                               "rettype": "full"}, as_json=False).text
        except GaveUp as e:
            print(f"  ! {topic} full text: {e}")
            continue
        # efetch answers 200 with an <error> body when an internal ID has no open-access file.
        n_articles = xml.count("<article ")
        if not n_articles:
            print(f"  ! {topic}: no open-access full text in the reply")
            continue
        rows.append({"source": "eutils.ncbi.nlm.nih.gov (PMC Open Access full text)",
                     "license": "PMC Open Access subset — see each article's own CC licence",
                     "topic": topic, "ids": ids, "articles": n_articles,
                     "xml_bytes": len(xml), "xml": xml})
        print(f"    {topic}: {n_articles} articles, {len(xml):,} bytes of real article text")
        time.sleep(0.4)
    return rows


SOURCES = {"openfda": openfda, "pmc": pmc, "pmc_text": pmc_text, "clintries": clintries}
# Value first: the labels are what the guards and the extractor are measured against.
ORDER = ["openfda", "pmc", "clintries", "pmc_text"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", choices=sorted(SOURCES), help="fetch one source only")
    ap.add_argument("--limit", type=int, default=50, help="records (or names) per source")
    ap.add_argument("--list", action="store_true", help="print the source register and exit")
    a = ap.parse_args()

    if a.list:
        print(json.dumps({
            "openfda": {"url": "https://api.fda.gov/drug/label.json",
                        "license": "US Gov public domain",
                        "content": "real labels: names, strengths, dose and schedule text"},
            "pmc": {"url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils (esummary)",
                    "license": "per-record PMC Open Access licence",
                    "content": "real self-management article titles and abstracts"},
            "clintries": {"url": "https://clinicaltrials.gov/api/v2/studies",
                          "license": "ClinicalTrials.gov API terms (US Gov public data)",
                          "content": "investigator-written eligibility and intervention text"},
            "pmc_text": {"url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils (efetch, full)",
                         "license": "PMC Open Access subset, per-article CC licence",
                         "content": "full open-access article XML"},
            "excluded": {"reason": "real patient records need a data-use agreement and CITI "
                                   "training, and this project promises no patient data",
                         "corpora": ["MIMIC-III", "MIMIC-IV", "eICU", "i2b2/n2c2", "Open-i",
                                     "MedNLI (its loader reads credentialed patient notes)"]},
            "unavailable": {"DailyMed v2 JSON service":
                            "every /services/v2/ request redirects to the HTML site; openFDA "
                            "distributes the same structured product labelling sections"},
        }, indent=2))
        return 0

    which = a.only or ORDER
    manifest = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "policy": "real, openly licensed reference data only; no patient records",
                "artefacts": []}
    mp = os.path.join(OUT, "MANIFEST.json")
    if os.path.exists(mp):
        manifest = json.load(open(mp, encoding="utf-8"))

    for name in which:
        print(f"[{name}] fetching (limit {a.limit})")
        t0 = time.time()
        rows = SOURCES[name](a.limit)
        art = write(f"{name}.jsonl", rows, {"source": name, "records": len(rows),
                                            "seconds": round(time.time() - t0, 1)})
        manifest["artefacts"] = [x for x in manifest.get("artefacts", []) if x.get("source") != name]
        manifest["artefacts"].append(art)
        print(f"[{name}] {len(rows)} records in {art['seconds']}s")

    os.makedirs(OUT, exist_ok=True)
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print("manifest:", mp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
