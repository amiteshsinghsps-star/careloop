"""One-shot proof of life for a judge: handshake, tool list, one capability call, one
care-plan call. Prints the tool count, the manifest hash and whether the plan came back
dose-masked. Stdlib only, non-zero exit on any failure.

The dose check is the INDEPENDENT detector (`preflight.policy.contains_dose`) — deliberately not
`careloop.policy_guard`, the guard it is auditing — so a masked leak by the server is still caught
here. It also redacts opaque ids first: a hex item ref such as `i-740cc822` contains `740cc`, which a
naive number+unit scan reads as a dose, and a judge's PASS must not depend on which ref the store
happened to mint.

    python scripts/smoke.py [http://127.0.0.1:8080/mcp] [--token TOKEN]
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.request

# Run as a file path (`python scripts/smoke.py`), so the repo root is not on sys.path by itself and
# the independent detector has to be reached explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from preflight.policy import dose_leaks

MASK_PHRASE = "your prescribed amount"


def rpc(url, method, params=None, mid="1", session=None, token=None):
    payload = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        payload["params"] = params
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session:
        headers["MCP-Session-Id"] = session
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=15) as res:
        return res.headers.get("MCP-Session-Id"), json.loads(res.read() or b"{}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", nargs="?", default="http://127.0.0.1:8080/mcp")
    ap.add_argument("--token", help="a linked OAuth access token, if the server needs one")
    a = ap.parse_args()
    sid, init = rpc(a.url, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                          "clientInfo": {"name": "smoke", "version": "1"}},
                    mid="1", token=a.token)
    res = init["result"]
    print("handshake  %s from %s, session %s" % (res["protocolVersion"], res["serverInfo"], sid))
    _, listed = rpc(a.url, "tools/list", {}, mid="2", session=sid, token=a.token)
    tools = listed["result"]["tools"]
    core = [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"],
             "outputSchema": t["outputSchema"]} for t in tools]
    print("tools      %d declared, manifest hash %s"
          % (len(tools), hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()))
    _, about = rpc(a.url, "tools/call", {"name": "about_careloop", "arguments": {}},
                   mid="3", session=sid, token=a.token)
    print("about      %d capabilities, spoken: %s"
          % (len(about["result"]["structuredContent"]["capabilities"]),
             about["result"]["content"][0]["text"][:70]))
    _, plan = rpc(a.url, "tools/call", {"name": "get_care_plan", "arguments": {}},
                  mid="4", session=sid, token=a.token)
    result, blob = plan["result"], json.dumps(plan["result"])
    items = result["structuredContent"].get("items") or []
    spoken = " ".join(c.get("text", "") for c in result["content"])
    # The field paths a leak would sit on, never the value: a judge's failing line must not itself
    # speak a medicine amount.
    leaks = (dose_leaks(spoken, "spoken")
             + dose_leaks({k: v for k, v in result["structuredContent"].items()
                           if k in ("items", "avoid", "slots")}))
    dose_found = bool(leaks)
    print("care plan  %d item(s), dose-masked %s (%s in answer, masked phrase %s)"
          % (len(items), "YES" if not dose_found else "NO",
             "no quantity found" if not dose_found
             else "a quantity leaked at " + ", ".join(path for path, _ in leaks),
             "present" if MASK_PHRASE in blob else "absent"))
    ok = not dose_found and tools and not result.get("isError") and not about["result"].get("isError")
    print("smoke: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("smoke failed: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        sys.exit(1)
