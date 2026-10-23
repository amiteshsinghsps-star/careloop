"""context — the shared run state every check module reads from.

One object holds the endpoint, the credentials we managed to obtain, the initialised
session and the seeded care plan, so checks stay small and order-independent.
"""
import json
import os

from . import client as C

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
EXPECTED_INTENTS = ("about_careloop", "get_care_plan", "explain_instruction", "mark_done",
                    "confirm_instruction", "add_note", "delete_instruction", "list_people",
                    "todays_plan", "what_changed", "prepare_next_visit", "daily_digest")
GUEST_SAFE_EXPECTED = {"about_careloop"}
CARD_URI = "ui://careloop/card"
_DEFAULT = object()      # sentinel: "use whatever credential the client already holds"

# an adult visit transcript: contains a real dose, so masking must be observable
SEED_TRANSCRIPT = ("Doctor: Your haemoglobin is low. Take 200 mg iron after dinner every day "
                  "for two months. Doctor: Avoid tea with meals because it blocks the iron. "
                  "Nurse: Check the blood pressure every morning and write it down. "
                  "Doctor: Come back after six weeks.")
SEED_NOTE = "Take 200 mg iron after dinner every day"


class Opts:
    def __init__(self, url, token=None, skip_auth=False, json_out=False, fail_fast=False,
                 client_id=None, client_secret=None, household_code=None, household_pin=None,
                 redirect_uri=None, timeout=20.0):
        self.url = url
        self.token = token
        self.skip_auth = skip_auth
        self.json_out = json_out
        self.fail_fast = fail_fast
        self.client_id = client_id or os.environ.get("CARELOOP_CLIENT_ID") or "careloop-web-client"
        self.client_secret = client_secret or os.environ.get("CARELOOP_CLIENT_SECRET")
        self.household_code = household_code or os.environ.get("CARELOOP_HOUSEHOLD_CODE") or "CASA01"
        self.household_pin = household_pin or os.environ.get("CARELOOP_FAMILY_PIN")
        self.redirect_uri = redirect_uri or os.environ.get("CARELOOP_REDIRECT_URI") \
            or "http://localhost:8080/callback"
        self.timeout = timeout

    @property
    def can_link(self):
        """Full linking needs a registered client AND a household the operator told us about."""
        return bool(self.client_secret and self.household_pin)


class Ctx:
    def __init__(self, opts):
        self.opts = opts
        self.client = C.McpClient(opts.url, token=opts.token, timeout=opts.timeout)
        self.auth_mode = None            # from /readyz
        self.protected = None            # /.well-known/oauth-protected-resource
        self.asmeta = None               # /.well-known/oauth-authorization-server
        self.session = None
        self.tools = []                  # tools/list payload
        self.tool_names = []
        self.person = None               # seeded plan scope
        self.items_seen = 0
        self.seed_way = None             # "capture" | "add_note" | None
        self.linked = None               # tokens obtained by our own PKCE run
        self.link_out = None             # raw outcome of that run, so it is executed once
        self.service_token = None
        self.companion_cookie = None

    # ------------------------------------------------------------------ plumbing
    def discover(self):
        """/healthz then /readyz; raises ConnectionFailure if the endpoint is dead."""
        alive = self.client.get("/healthz")
        if alive.status >= 500:
            raise C.ConnectionFailure("endpoint unhealthy: GET /healthz -> %d" % alive.status)
        r = self.client.get("/readyz")
        doc = r.json() or {}
        self.auth_mode = doc.get("auth") or ("unknown" if r.status != 200 else "off")
        return self.auth_mode

    def metadata(self):
        if self.protected is None:
            rp = self.client.get("/.well-known/oauth-protected-resource")
            self.protected = (rp.status, rp.json())
        if self.asmeta is None:
            ra = self.client.get("/.well-known/oauth-authorization-server")
            self.asmeta = (ra.status, ra.json())
        return self.protected, self.asmeta

    def call(self, method, params=None, mid="c", token=_DEFAULT, session=True, protocol=None):
        return self.client.call(method, params, mid=mid, session=(self.session if session else None),
                                protocol=protocol or "2025-11-25",
                                token=(self.client.token if token is _DEFAULT else token))

    def tool(self, name, arguments=None, **kw):
        kw.setdefault("mid", "t-" + name)
        return self.call("tools/call", {"name": name, "arguments": arguments or {}}, **kw)

    def authenticated(self):
        return bool(self.client.token or self.auth_mode == "off")

    def unauth_reason(self, what):
        if self.opts.skip_auth:
            return "skipped (--skip-auth): %s" % what
        if self.auth_mode == "oauth" and not self.client.token:
            return "no credentials: the endpoint is OAuth-protected, pass --token or client creds"
        return None

    # ------------------------------------------------------------------ plan seed
    def ensure_plan(self):
        """Make the household hold at least one dose-bearing item, by any legal route.

        Returns (ok, note). Tries the companion capture API (needs a session or dev mode),
        then the voice `add_note` path, which is the only one available against a remote
        endpoint reached with an access token alone.
        """
        if not self.person:
            people = self.known_people()
            if people:
                self.person = people[0]
        n = self.plan_items()
        if n:
            self.seed_way = "pre-existing"
            return True, "%d plan item(s) already recorded%s" % (
                n, " for " + self.person if self.person else "")
        if self._capture_seed():
            self.seed_way = "capture"
            if not self.person:
                self.person = "Preflight"
            return True, "adult visit captured through /api/capture"
        if self._note_seed():
            self.seed_way = "add_note"
            return True, "dose-bearing note stored through the voice surface"
        return False, "no plan items and no seeding path (capture API refused, add_note refused)"

    def plan_items(self, person=None):
        """Number of items on the voice care plan (None when the call did not answer)."""
        args = {}
        person = person if person is not None else self.person
        if person:
            args["person"] = person
        rep = self.tool("get_care_plan", args, mid="seed-scope")
        sc = _sc(rep.json())
        items = sc.get("items")
        if isinstance(items, list):
            self.items_seen = len(items)
            if sc.get("person"):
                self.person = sc["person"]
            return len(items)
        return None

    def known_people(self):
        rep = self.tool("list_people", {}, mid="people")
        sc = ((rep.json() or {}).get("result") or {}).get("structuredContent") or {}
        return [p.get("name") for p in (sc.get("people") or []) if isinstance(p, dict) and p.get("name")]

    def companion_login(self):
        if self.companion_cookie or not self.opts.household_pin:
            return self.companion_cookie
        r = self.client.post_json("/api/login", {"code": self.opts.household_code,
                                                 "pin": self.opts.household_pin})
        if r.status == 200 and r.header("Set-Cookie"):
            self.companion_cookie = r.header("Set-Cookie").split(";")[0]
        return self.companion_cookie

    def _capture_seed(self):
        headers = {}
        r = self.client.post_json("/api/capture", {"transcript": SEED_TRANSCRIPT,
                                                   "person": "Preflight"}, headers=headers)
        if r.status == 401:
            cookie = self.companion_login()
            if not cookie:
                return False
            r = self.client.post_json("/api/capture", {"transcript": SEED_TRANSCRIPT,
                                                       "person": "Preflight"},
                                      headers={"Cookie": cookie})
        if r.status != 200:
            return False
        try:
            added = (json.loads(r.body) or {}).get("added", 0)
        except Exception:
            return False
        return bool(added)

    def _note_seed(self):
        people = self.known_people()
        args = {"text": SEED_NOTE}
        if people:
            args["person"] = people[0]
        rep = self.tool("add_note", args, mid="seed-note")
        sc = _sc(rep.json())
        if sc.get("items"):
            self.person = args["person"] if "person" in args else (people[0] if people else None)
            return True
        return False

    # ------------------------------------------------------------------ session
    def bootstrap_session(self):
        """Ensure an initialised session exists (transport checks do this first, normally)."""
        if self.session:
            return True
        rep = self.client.initialize()
        if rep.status == 200:
            self.session = rep.header("MCP-Session-Id")
        return bool(self.session)


def _sc(reply_json):
    return ((reply_json or {}).get("result") or {}).get("structuredContent") or {}
