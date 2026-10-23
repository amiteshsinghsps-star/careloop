"""report — check results, aligned human output and the --json document."""
import json
import sys

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
SECTIONS = ("transport", "auth", "safety", "performance", "card")
_IDW = 32


class Reporter:
    """Collects check results and renders them (aligned lines, or one JSON document)."""

    def __init__(self, as_json=False, fail_fast=False, stream=None):
        self.results = []
        self.as_json = as_json
        self.fail_fast = fail_fast
        self.stream = stream or sys.stdout
        self.stopped = False

    # ---------------------------------------------------------------- collect
    def add(self, cid, section, status, detail=""):
        self.results.append({"id": cid, "section": section, "status": status,
                             "detail": " ".join(str(detail or "").split())})
        if status == FAIL and self.fail_fast:
            self.stopped = True
        return status

    def ok(self, cid, section, detail=""):
        return self.add(cid, section, PASS, detail)

    def bad(self, cid, section, detail=""):
        return self.add(cid, section, FAIL, detail)

    def skip(self, cid, section, reason):
        return self.add(cid, section, SKIP, reason)

    def dropped(self, cid, section, reason="not executed (--fail-fast stopped the run)"):
        """Placeholder for a check never executed because the run stopped early."""
        return self.skip(cid, section, reason)

    @property
    def halted(self):
        return self.stopped

    def counts(self):
        c = {PASS: 0, FAIL: 0, SKIP: 0}
        for r in self.results:
            c[r["status"]] = c.get(r["status"], 0) + 1
        return c

    # ----------------------------------------------------------------- output
    def emit(self, meta):
        c = self.counts()
        code = 1 if c[FAIL] else 0
        if self.as_json:
            doc = {"checks": self.results,
                   "summary": {"pass": c[PASS], "fail": c[FAIL], "skip": c[SKIP],
                               "total": len(self.results), "exit_code": code, **meta}}
            print(_safe(json.dumps(doc, indent=2)))
            return code
        w = self.stream.write
        w(_safe("alexa-mcp-preflight  %s\n" % meta.get("endpoint", "")))
        w(_safe("target auth mode: %s\n" % meta.get("auth_mode", "?")))
        for section in SECTIONS:
            rows = [r for r in self.results if r["section"] == section]
            if not rows:
                continue
            w("\n[%s]\n" % section)
            for r in rows:
                w(_safe("  %-5s %-*s %s\n" % (r["status"], _IDW, r["id"], _one_line(r["detail"]))))
        w("\n%s\n" % ("-" * 72))
        w("checks: %d   pass: %d   fail: %d   skip: %d   ->  exit %d\n"
          % (len(self.results), c[PASS], c[FAIL], c[SKIP], code))
        fails = [r["id"] for r in self.results if r["status"] == FAIL]
        if fails:
            w("failed: %s\n" % ", ".join(fails))
        return code


def _safe(text):
    """Never crash on an unencodable character inside a check detail (Windows consoles)."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(enc)
    except (UnicodeEncodeError, LookupError):
        text = text.encode(enc, "replace").decode(enc, "replace")
    return text


def _one_line(text, width=94):
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[:width - 3] + "..."
