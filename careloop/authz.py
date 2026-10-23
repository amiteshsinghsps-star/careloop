"""authz — one decision point for every read and write (N3).

Two independent implementations of the same rules live here:

* `decide()`      — the runtime table authorizer used by the server.
* `cedar_decide()`— a small evaluator for the restricted Cedar subset in
  `artifacts/policies.cedar`, used by the CI **differential test**: random requests
  are evaluated by both and any disagreement fails the build.

We say "differentially tested", never "proved".
"""
ACTIONS = ("ViewPlan", "ViewDose", "MarkDone", "ConfirmItem", "AddNote",
           "DeleteItem", "ExportPlan", "ManagePeople")

# (role, action) -> True for companion surface; Alexa-only restrictions applied below
_TABLE = {
    "caregiver": {"ViewPlan", "ViewDose", "MarkDone", "ConfirmItem", "AddNote",
                  "DeleteItem", "ExportPlan", "ManagePeople"},
    "elder": {"ViewPlan", "MarkDone", "ConfirmItem", "AddNote"},
    "viewer": {"ViewPlan"},
}

ALEXA_FORBIDDEN = {"ViewDose", "ExportPlan"}


class Decision:
    def __init__(self, allow: bool, reason: str):
        self.allow = allow
        self.reason = reason

    def __bool__(self):
        return self.allow

    def __repr__(self):
        return f"Decision({self.allow}, {self.reason!r})"


def decide(principal: dict, action: str, resource: dict, context: dict) -> Decision:
    """Runtime authorizer. principal={hid, role}; resource={hid}; context={surface}."""
    if action not in ACTIONS:
        return Decision(False, "unknown-action")
    if principal.get("hid") != resource.get("hid"):
        return Decision(False, "cross-household")
    role = principal.get("role", "viewer")
    allowed = _TABLE.get(role, set())
    if action not in allowed:
        return Decision(False, f"role-{role}-denies")
    surface = context.get("surface", "companion")
    if surface == "alexa" and action in ALEXA_FORBIDDEN:
        return Decision(False, "alexa-surface-dose-free")
    return Decision(True, "allow")


# ------------------------------------------------------------------ mini Cedar
# Supports the restricted grammar we ship:
#   permit|forbid (principal == Entity::"Role" [or in Set], action == Action::"X" | in Set,
#                  resource) when { <cond> };
#   <cond> ::= <term> (&& <term>)*
#   <term>  ::= context.surface == "v" | principal.hid != resource.hid
#             | principal.hid == resource.hid | principal.role == "v"
import re

_HEADER = re.compile(
    r'(permit|forbid)\s*\(\s*principal(?:\s*==\s*Principal::"(\w+)"|\s+in\s+Principal::"([\w]+)")'
    r'(?:\s*,\s*action\s+in\s+Action::"([\w]+)"|\s*,\s*action\s*==\s*Action::"([\w]+)")'
    r'\s*,\s*resource\s*\)(?:\s*(?:unless\s*\{([^}]*)\}|when\s*\{([^}]*)\}))?\s*;', re.S)


def _eval_cond(text: str, principal, action, resource, context) -> bool:
    """Evaluate an AND-only condition; an empty condition is always true."""
    for term in re.split(r"&&", text or ""):
        term = term.strip()
        if not term:
            continue
        m = re.match(r'\s*(\w+)\.(\w+)\s*(==|!=)\s*"([^"]*)"\s*', term)
        if m:
            obj, attr, op, val = m.groups()
            got = {"principal": principal, "resource": resource, "context": context}[obj].get(attr)
            ok = (got == val) if op == "==" else (got != val)
            if not ok:
                return False
            continue
        m = re.match(r'\s*(\w+)\.(\w+)\s*(==|!=)\s*(\w+)\.(\w+)\s*', term)
        if m:
            o1, a1, op, o2, a2 = m.groups()
            g1 = {"principal": principal, "resource": resource, "context": context}[o1].get(a1)
            g2 = {"principal": principal, "resource": resource, "context": context}[o2].get(a2)
            if not ((g1 == g2) if op == "==" else (g1 != g2)):
                return False
            continue
        raise ValueError(f"unsupported Cedar condition term: {term!r}")
    return True


class CedarPolicySet:
    def __init__(self, source: str):
        self.policies = []
        for kind, prole, prole_in, aset, aone, unless, when in _HEADER.findall(source):
            roles = {prole} if prole else set(re.split(r"[,\s]+", prole_in.replace("_", " ")))
            action = aone or aset
            self.policies.append({
                "effect": kind, "roles": roles, "action": action,
                "when": (when or "").strip(), "unless": (unless or "").strip(),
            })

    def decide(self, principal, action, resource, context):
        def matches(p):
            if p["action"] != action or not (principal.get("role") in p["roles"]
                                             or principal.get("role_group") in p["roles"]):
                return False
            # `when` must hold; `unless` must not (an absent clause is vacuously satisfied)
            if p["unless"] and _eval_cond(p["unless"], principal, action, resource, context):
                return False
            return _eval_cond(p["when"], principal, action, resource, context)

        if any(matches(p) for p in self.policies if p["effect"] == "forbid"):
            return Decision(False, "cedar-forbid")
        permitted = any(matches(p) for p in self.policies if p["effect"] == "permit")
        return Decision(permitted, "cedar-permit" if permitted else "cedar-default-deny")


def load_cedar(path: str) -> CedarPolicySet:
    with open(path, "r", encoding="utf-8") as f:
        return CedarPolicySet(f.read())
