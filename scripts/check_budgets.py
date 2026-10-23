#!/usr/bin/env python3
"""check_budgets -- CareLoop's own engineering budgets (PRD Part O/P). Stdlib only.

This is a hard gate: it exits non-zero the moment any budget is broken, so `npm run
build && npm run sync` followed by this check in CI fails the pipeline rather than
shipping a regressed bundle. It does not talk to the network or import third-party libs.

Checks
  1. careloop/web/card.html exists and stays under the single-file card budget (100 kB).
  2. The card is self-contained: no `src=`/`href=` to an absolute/protocol-relative URL
     and no `{@html}` Svelte injection marker left in the shipped file.
  3. Each synced frontend bundle (simulator / companion / site) is under its budget.
  4. No `.env` (or `.env.*` other than `.env.example`) is present anywhere in the tree.
  5. No obvious committed secret: a real-looking value bound to one of the `.env.example`
     secret variable names, or a PEM private key, or an AWS access-key id. Generated /
     vendored directories (node_modules, data, dist, build, __pycache__, .pytest_cache,
     .hypothesis, .angular, .astro) and the built careloop/web output are not scanned, nor
     is `.tmp` -- every one of those is gitignored scratch that cannot reach a submission,
     and `.tmp` has already produced a false positive from a Chrome profile's own
     extension code. `.github` IS scanned: it is tracked, and CI files are publishable.

Budgets in BUDGETS / CARD_BUDGET below are OUR OWN limits, chosen from the size most
recently measured for each bundle plus a small margin -- they are NOT Amazon-imposed
ceilings. Raise or lower them freely; they exist to make bundle growth a deliberate,
reviewed change. Each current number and the reason it moved is written next to it below.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "careloop", "web")
CARD = os.path.join(WEB, "card.html")

KB = 1024

# --- budgets (bytes). Each budget = last measured size + headroom, and every number below
#     is the size the gate actually saw, with the reason it moved written next to it.
#     card      72.7 kB measured 2026-10-21, up 0.1 kB from 72.6 kB: the client now binds
#               globalThis.fetch at construction (browser "Illegal invocation" fix).
#     simulator was 155.9 kB measured 2026-10-21, down from 209.3 kB: sync_web.mjs no longer
#               writes a second identical copy of the 74 kB card into the simulator bundle
#               (the app links the /card route; nothing referenced /simulator/card.html).
#     companion 340.0 kB measured 2026-10-21, up from 323.0 kB: the ledger scrubber, the
#               adherence view and the print/share export panel are three features that
#               shipped after the old number was taken. No duplicate or dead module was
#               found to remove instead; the remaining lever is dropping zone.js for
#               zoneless change detection (~34 kB), not taken because it is a behaviour
#               change, not a size change. The companion has since been driven end-to-end
#               in headless Chrome, which fixed its API base, its base-uri and the
#               aliases shape without moving the bundle.
#     site       87.5 kB measured 2026-10-21, up from 68.0 kB: /a11y-lab (M14) is a real
#                page — 14.4 kB of HTML plus 5.4 kB of scoped CSS. Shrinking the budget
#                would have meant shrinking the lab.
#     simulator 200.2 kB measured 2026-10-22, up from 155.9 kB: the simulated Alexa+ device
#               runtime (M-lane Device Lab) shipped — ~26 kB of it is the GENERATED
#               interactionModel.ts, the same artifact scripts/make_addon.py writes for the
#               skill, so the device resolves utterances and voice budget from the real model
#               instead of inventing one; the rest is the envelope/SSML/SLU runtime and the
#               panel. The ASK SDK is `import type` only: no SDK JavaScript is in the bundle
#               (checked — no "ask-sdk" token anywhere under careloop/web/simulator). Cutting
#               the model would have made the simulation a mock.
#     simulator 211.6 kB measured 2026-10-22, up from 200.2 kB: dialog delegation. The
#               generated model is 29.5 kB (the note above measured ~26 kB) because it now
#               declares each intent's slots — required, capture, prompt, routed argument — and the device
#               reads its dialog from that declaration instead of guessing: the dialog rules
#               (nextDialogStep / fillSlot / buildDialogTurn / heldDialog) plus the Device
#               Lab's per-turn dialog row. Removing any of it puts an invented question
#               back into the simulation. ----
#     simulator 212.5 kB measured 2026-10-22, up 0.9 kB from 211.6 kB: the two devices were
#               held against each other instead of described side by side. The Lab now records
#               whether a turn actually sent a request (`called`), routes its voice calls
#               through the shipped model's own declared routing (`declaredRouting` /
#               `collectedArgs`) rather than the app text router, and primes the household
#               roster before the first phrase; the shared ref resolver reads rank words in
#               rank order. All of it is the same dialog the model already declared — no new
#               prompt, no new sample, no generated-model growth.
#     simulator 216.9 kB measured 2026-10-22, up 4.4 kB from 212.5 kB: the device's one
#               self-started turn. The shipped model grew a 15th prompt (`ReminderPrompt`) and a
#               `proactive.reminder` block that names the intent that arms it, the connection, the
#               resume interval and the two wire types, and both devices now build a real
#               `Connections.StartConnection` directive and read a `SessionResumedRequest` back —
#               `armReminder`/`fireReminder`, the machine's connection-owned window and the Device
#               Lab's reminder row. The budget moved 216 → 220 kB for it, deliberately: the
#               generated copy ships only `proactive.reminder` (the artifact's `note` prose stays in
#               `alexa/`, it is not something a browser should re-parse every turn), so what is left
#               here is protocol objects and dialog rules, none of which can be cut without putting
#               an invented sentence or an invented wire shape back into the simulation.
#     simulator 228.0 kB measured 2026-10-23, up 11.1 kB from 216.9 kB: the two delivery and
#               speaker features. The generated model grew again — `presentation.delivery`
#               (five styles, each with a rate, a volume, a word ceiling and its own prompt id)
#               and `voiceProfile.assertion`, generated FROM `careloop/policy_guard.py`'s own
#               `_meta` constants so the device and the service cannot drift on a field name — and
#               the device side is `answerDeviceLocal`/`holdAnswer`/`speakerMeta` plus the Lab's
#               per-turn style, claim and refusal rows. The budget moved 220 → 232 kB for it:
#               neither feature can be demoed without the style numbers and the claim keys being
#               read out of the artifact, and a hand-written copy of either is exactly the
#               invented-prose drift this repo gates against.
CARD_BUDGET = 100 * KB          # single-file MCP-App ceiling (the card was 72.7 kB)
BUNDLES = {
    "simulator": (os.path.join(WEB, "simulator"), 232 * KB),   # measured 228.0 kB
    "companion": (os.path.join(WEB, "companion"), 352 * KB),   # measured 340.0 kB
    "site":      (os.path.join(WEB, "site"),       92 * KB),   # measured 87.5 kB
}

# --- skip lists ---------------------------------------------------------------
PRUNE_DIRS = {"node_modules", "data", "dist", "build", "__pycache__", ".pytest_cache",
              ".hypothesis", ".angular", ".astro", ".git", ".tmp"}
# built frontends are regenerated and gitignored: covered by the size checks above only
SKIP_SCAN_DIRS = PRUNE_DIRS | {"web"}
TEXT_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".json", ".md", ".yml", ".yaml",
            ".css", ".html", ".txt", ".cfg", ".toml", ".ini", ".env"}
SKIP_FILES = {"package-lock.json"}

# --- secret patterns (variable names reused from .env.example) -----------------
SECRET_KEYS = [
    "CARELOOP_CLIENT_SECRET", "ALEXA_CLIENT_SECRET", "CARELOOP_TOKEN",
    "CARELOOP_FAMILY_PIN", "AWS_BEARER_TOKEN_BEDROCK", "AWS_SECRET_ACCESS_KEY",
]
# a documented template/demo value is not a leak
PLACEHOLDER_VALUES = {
    "", "change-me", "change-me-frontend", "change-me-alexa", "dev-token",
    "481542", "careloop-web-client", "alexa-client-placeholder", "s3cret-test",
}
PLACEHOLDER_TOKENS = ("change-me", "changeme", "placeholder", "example", "sample",
                      "dummy", "test", "todo", "fixme", "your-", "redacted", "dev-",
                      "xxx", "environ", "getenv")
# KEY = value | KEY: value | "KEY": "value"  (name may be quoted; value up to a delimiter)
_SECRET_ASSIGN = re.compile(
    r'(?<![A-Za-z0-9_])(' + "|".join(SECRET_KEYS) + r')["\']?\s*[=:]\s*["\']?'
    r'(?P<value>[^\s"\',;)\]}]*)')
_AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_ATTR = re.compile(r'(?:src|href)\s*=\s*["\']([^"\']*)["\']', re.I)
_EXTERNAL_URL = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*:)?//")


def dir_bytes(path: str) -> int:
    total = 0
    for _root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(_root, f))
            except OSError:
                pass
    return total


def kb(n: int) -> float:
    return n / KB


def iter_source_files():
    """Text files that look tracked: walk the tree, prune generated/vendored dirs."""
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_SCAN_DIRS]
        for f in files:
            if f in SKIP_FILES:
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext not in TEXT_EXT and not f.startswith(".env"):
                continue
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) > 4 * 1024 * 1024:   # skip anything huge
                    continue
            except OSError:
                continue
            yield p


def rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def is_placeholder(value: str) -> bool:
    low = value.lower()
    if value in PLACEHOLDER_VALUES:
        return True
    if not re.search(r"[A-Za-z0-9]", value):          # '...' , '${}' , empty
        return True
    return any(tok in low for tok in PLACEHOLDER_TOKENS)


def main() -> int:
    problems = []
    rows = []   # (label, measured_bytes, budget_bytes)

    # 1 + 2 -- the card -------------------------------------------------------
    if not os.path.isfile(CARD):
        problems.append("card.html missing: run `npm run build && npm run sync` first")
    else:
        size = os.path.getsize(CARD)
        rows.append(("card (single file)", size, CARD_BUDGET))
        if size > CARD_BUDGET:
            problems.append(f"card is {kb(size):.1f} kB, over the {kb(CARD_BUDGET):.0f} kB budget")
        html = open(CARD, encoding="utf-8", errors="ignore").read()
        for url in _ATTR.findall(html):
            u = url.strip()
            if u.lower().startswith("data:"):
                continue
            if _EXTERNAL_URL.match(u):
                problems.append(f"card references an external URL: {u[:80]}")
        if "{@html" in html:
            problems.append("card contains a '{@html}' injection marker")
        # The card is one file with its own budget. A byte-identical copy inside a bundle
        # would be 74 kB of nothing: it was there until 2026-10-21 and nothing referenced it.
        for name, (path, _budget) in BUNDLES.items():
            dup = os.path.join(path, "card.html")
            if os.path.isfile(dup):
                problems.append(f"card is duplicated into the {name} bundle "
                                f"({dup}); serve it from the /card route instead")

    # 3 -- bundle sizes -------------------------------------------------------
    for name, (path, budget) in BUNDLES.items():
        if not os.path.isdir(path):
            problems.append(f"{name} bundle missing at careloop/web/{name} (run sync)")
            continue
        size = dir_bytes(path)
        if size == 0:
            problems.append(f"{name} bundle at careloop/web/{name} is empty (sync did not populate it)")
            continue
        rows.append((name, size, budget))
        if size > budget:
            problems.append(f"{name} is {kb(size):.1f} kB, over the {kb(budget):.0f} kB budget")

    # 4 -- stray .env files ---------------------------------------------------
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in PRUNE_DIRS]
        for f in files:
            if f == ".env" or (f.startswith(".env.") and f != ".env.example"):
                problems.append(f"secret file present: {rel(os.path.join(root, f))}")

    # 5 -- committed secrets --------------------------------------------------
    for p in iter_source_files():
        try:
            text = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if _PEM.search(text):
            problems.append(f"{rel(p)}: contains a PEM private key")
        for m in _AWS_KEY.finditer(text):
            problems.append(f"{rel(p)}: contains an AWS access key id ({m.group(0)})")
        for m in _SECRET_ASSIGN.finditer(text):
            val = m.group("value")
            if not is_placeholder(val):
                problems.append(f"{rel(p)}: {m.group(1)} appears hard-coded to a real value")

    # report ------------------------------------------------------------------
    print(f"{'ITEM':<22}{'MEASURED':>12}{'BUDGET':>12}  STATUS")
    print("-" * 56)
    for label, measured, budget in rows:
        ok = measured <= budget
        print(f"{label:<22}{kb(measured):>10.1f} kB{kb(budget):>10.0f} kB  "
              f"{'ok' if ok else 'OVER':>4}  ({measured / budget * 100:.0f}%)")
    print("-" * 56)

    if problems:
        print(f"\nBUDGET CHECK FAILED ({len(problems)} problem(s)):")
        for pr in sorted(set(problems)):
            print(f"  - {pr}")
        print("\nThese budgets are CareLoop's own limits, not Amazon's -- see the ADR.")
        return 1

    print("\nBUDGET CHECK PASSED  (size, self-contained card, no .env, no secrets)")
    print("Note: budgets are our own, per-bundle measurements plus headroom (see the "
          "budget notes in this file), not Amazon-imposed ceilings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
