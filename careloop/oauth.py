"""oauth — OAuth 2.1 authorization-code + PKCE (S256), static clients, household-scoped
tokens, RFC 9728 protected-resource metadata. Implements the Alexa+ authentication
checklist (Part E.4 / D.2 items 7-11):

* tokens carry `sub` = link id and `hid` = household; every tool call is scoped;
* `client_credentials` issues ONLY `mcp:service` tokens, expires_in <= 3600, no refresh;
* refresh grant does NOT require `resource` (Alexa omits it); refresh rotates;
* access tokens are bound to an audience; tokens for another resource are rejected;
* redirect URIs: all registered URIs accepted, only exact matches;
* never accepts credentials in the query string.
"""
import base64
import hashlib
import hmac
import re
import secrets
import time

from . import store, policy_guard as pg
from .config import settings

CODE_TTL = 300
_B64URL = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


def _h(x: str) -> str:
    return hashlib.sha256(x.encode()).hexdigest()


def _now():
    return time.time()


# ------------------------------------------------------------------ metadata
def protected_resource_metadata(public_url: str) -> dict:
    # `resource` must exactly match the manifest MCP URI, including trailing slash rules
    return {
        "resource": public_url + "/mcp",
        "authorization_servers": [public_url],
        "scopes_supported": ["mcp:tools", "mcp:service"],
        "bearer_methods_supported": ["header"],
    }


def as_metadata(public_url: str) -> dict:
    return {
        "issuer": public_url,
        "authorization_endpoint": public_url + "/oauth/authorize",
        "token_endpoint": public_url + "/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "scopes_supported": ["mcp:tools", "mcp:service"],
    }


# ------------------------------------------------------------------ clients
def _client_secret(client_id: str) -> str | None:
    if client_id == settings.client_id:
        return settings.client_secret
    if client_id == settings.alexa_client_id:
        return settings.alexa_client_secret
    return None


def check_client_basic(auth_header: str | None):
    """Returns (client_id, secret_ok). Credentials must come in the header only."""
    if not auth_header or not auth_header.lower().startswith("basic "):
        return None, False
    try:
        raw = base64.b64decode(auth_header.split(" ", 1)[1]).decode()
        cid, _, sec = raw.partition(":")
    except Exception:
        return None, False
    sec_ok = _client_secret(cid) is not None and hmac.compare_digest(_client_secret(cid), sec)
    return cid, sec_ok


def valid_redirect(client_id: str, redirect_uri: str) -> bool:
    g = store.global_db()
    row = g.conn.execute("SELECT 1 FROM links WHERE link_id=?", (client_id,)).fetchone()
    if redirect_uri in settings.redirect_uris:
        return True
    return False


# ------------------------------------------------------------------ authorize
def authorize_get(params: dict) -> dict:
    """Validate the authorization request; returns either an error or a login prompt."""
    need = ["response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method", "state"]
    for k in need:
        if not params.get(k):
            return {"error": f"invalid_request: missing {k}"}
    if params["response_type"] != "code":
        return {"error": "unsupported_response_type"}
    if params["code_challenge_method"] != "S256":
        return {"error": "invalid_request: only S256 is supported"}
    if not _B64URL.match(params["code_challenge"] or ""):
        return {"error": "invalid_request: bad code_challenge"}
    if not valid_redirect(params["client_id"], params["redirect_uri"]):
        return {"error": "invalid_redirect_uri"}
    res = params.get("resource")
    if res and res.rstrip("/") != settings.public_url.rstrip("/") and res != settings.mcp_url:
        return {"error": "invalid_request: resource mismatch"}
    return {"ok": True, "params": params}


def authorize_login(params: dict, code: str, pin: str, consent: bool) -> dict:
    """Household login + one-time consent (Policy §3). Returns redirect or error."""
    g = store.global_db()
    hh = g.household_by_code(code or "")
    if not hh or not g.check_pin(hh["hid"], pin or ""):
        time.sleep(0.4)  # slow down PIN brute force
        return {"error": "Invalid household code or PIN."}
    if not consent:
        return {"error": "Consent is required to link accounts."}
    link_id = g.add_link(hh["hid"], f"member-{secrets.token_hex(2)}", role="caregiver")
    created = _issue_code(params, hh["hid"], link_id)
    return created


def _issue_code(params: dict, hid: str, link_id: str) -> dict:
    g = store.global_db()
    code = secrets.token_urlsafe(32)
    scope = params.get("scope", "mcp:tools") or "mcp:tools"
    g.conn.execute("INSERT INTO auth_codes(code_hash, hid, sub, link_id, challenge, method, client_id,"
                   " redirect_uri, scope, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (_h(code), hid, link_id, link_id, params["code_challenge"],
                    params["code_challenge_method"], params["client_id"], params["redirect_uri"],
                    scope, _now()))
    g.conn.commit()
    sep = "&" if "?" in params["redirect_uri"] else "?"
    loc = f"{params['redirect_uri']}{sep}code={code}&state={params['state']}"
    return {"redirect": loc}


# ------------------------------------------------------------------ token
def token(form: dict, auth_header: str | None) -> dict:
    cid, ok = check_client_basic(auth_header)
    if not cid or not ok:
        return {"error": "invalid_client", "status": 401}
    grant = form.get("grant_type")
    if grant == "client_credentials":
        return _client_credentials(cid, form)
    if grant == "authorization_code":
        return _auth_code(form, cid)
    if grant == "refresh_token":
        return _refresh(form)
    return {"error": "unsupported_grant_type", "status": 400}


def _client_credentials(cid: str, form: dict) -> dict:
    scope = form.get("scope", "mcp:service")
    if scope and "mcp:service" not in scope:
        return {"error": "invalid_scope", "status": 400}
    if cid not in (settings.client_id, settings.alexa_client_id):
        return {"error": "invalid_client", "status": 401}
    tok = secrets.token_urlsafe(32)
    ttl = settings.service_ttl
    g = store.global_db()
    g.conn.execute("INSERT INTO tokens(token_hash, kind, hid, sub, link_id, scope, client_id, audience,"
                   " expires_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (_h(tok), "service", None, "service", None, "mcp:service", cid,
                    settings.mcp_url, _now() + ttl, _now()))
    g.conn.commit()
    # expires_in <= 3600, deliberately NO refresh token (checklist)
    return {"access_token": tok, "token_type": "Bearer", "expires_in": ttl, "scope": "mcp:service"}


def _auth_code(form: dict, cid: str) -> dict:
    g = store.global_db()
    code = form.get("code", "")
    row = g.conn.execute("SELECT * FROM auth_codes WHERE code_hash=?", (_h(code),)).fetchone()
    if not row or row["used"]:
        return {"error": "invalid_grant", "status": 400}
    if _now() - row["created_at"] > CODE_TTL:
        return {"error": "invalid_grant: code expired", "status": 400}
    if row["client_id"] != cid or row["redirect_uri"] != form.get("redirect_uri"):
        return {"error": "invalid_grant", "status": 400}
    # RFC 6749 §4.1.2: a code is presented once. Consume it before the PKCE check so a
    # failed attempt cannot leave a still-redeemable code behind.
    g.conn.execute("UPDATE auth_codes SET used=1 WHERE code_hash=?", (_h(code),))
    g.conn.commit()
    verifier = form.get("code_verifier", "")
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if not hmac.compare_digest(digest, row["challenge"]):
        return {"error": "invalid_grant: PKCE check failed", "status": 400}
    res = _mint_user_tokens(g, row["hid"], row["link_id"], row["scope"], cid, require_resource=False)
    g.conn.commit()
    return res


def _refresh(form: dict) -> dict:
    g = store.global_db()
    rt = form.get("refresh_token", "")
    row = g.conn.execute("SELECT * FROM refresh_map WHERE ref_hash=?", (_h(rt),)).fetchone()
    if not row or row["revoked"]:
        return {"error": "invalid_grant", "status": 400}
    # rotation: old refresh token dies with this request
    g.conn.execute("UPDATE refresh_map SET revoked=1 WHERE ref_hash=?", (_h(rt),))
    g.conn.execute("UPDATE tokens SET revoked=1 WHERE token_hash=?", (row["token_hash"],))
    lk = g.link(row["link_id"])
    if lk is None or lk["status"] != "linked":
        return {"error": "invalid_grant: link revoked", "status": 400}
    res = _mint_user_tokens(g, row["hid"], row["link_id"], "mcp:tools", row["hid"],
                            require_resource=False, reuse_link=True)
    g.conn.commit()
    return res


def _mint_user_tokens(g, hid, link_id, scope, cid, require_resource=False, reuse_link=False) -> dict:
    at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = _now()
    g.conn.execute("INSERT INTO tokens(token_hash, kind, hid, sub, link_id, scope, client_id, audience,"
                   " expires_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (_h(at), "access", hid, link_id, link_id, "mcp:tools", cid if not reuse_link else link_id,
                    settings.mcp_url, now + settings.access_ttl, now))
    g.conn.execute("INSERT INTO refresh_map(ref_hash, token_hash, hid, sub, link_id, created_at)"
                   " VALUES(?,?,?,?,?,?)", (_h(rt), _h(at), hid, link_id, link_id, now))
    return {"access_token": at, "token_type": "Bearer", "expires_in": settings.access_ttl,
            "refresh_token": rt, "scope": "mcp:tools"}


# ------------------------------------------------------------------ verification
def verify_bearer(auth_header: str | None) -> dict | None:
    """Returns the token record or None. Audience-bound; header-only Bearer."""
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    tok = auth_header.split(" ", 1)[1].strip()
    g = store.global_db()
    row = g.conn.execute("SELECT * FROM tokens WHERE token_hash=?", (_h(tok),)).fetchone()
    if not row or row["revoked"] or row["expires_at"] < _now():
        return None
    if row["audience"] != settings.mcp_url:  # reject tokens not issued for this server
        return None
    return dict(row)


def link_identity(token_row: dict) -> dict | None:
    if token_row["kind"] != "access":
        return None
    lk = store.global_db().link(token_row["link_id"])
    if lk is None or lk["status"] != "linked":
        return None
    return {"hid": lk["hid"], "sub": lk["sub"], "link_id": lk["link_id"], "role": lk["role"]}


# ------------------------------------------------------------------ consent copy
CONSENT_TEXT = (
    "By linking, CareLoop will share with the assistant you link: the care items, quotes and "
    "schedules you recorded for this household — never medicine amounts on the voice surface. "
    "You can unlink at any time from the companion page; unlinked credentials stop working "
    "immediately and relinking starts a fresh household session. CareLoop stores only what you "
    "record. This permission is asked once.")


def login_page_html(next_params: dict, error: str = "") -> str:
    import urllib.parse
    q = urllib.parse.urlencode({k: v for k, v in next_params.items() if k in
                                ("client_id", "redirect_uri", "state", "code_challenge",
                                 "code_challenge_method", "scope", "resource", "response_type")})
    err = f'<p class="err">{pg.escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link CareLoop to your assistant</title><link rel="stylesheet" href="/static/base.css"></head>
<body class="center"><main class="panel" role="main">
<h1>Link CareLoop</h1>
{err}
<form method="POST" action="/oauth/authorize?{q}" autocomplete="off">
 <label>Household code <input name="code" required autofocus maxlength="12" style="text-transform:uppercase"></label>
 <label>Family PIN <input name="pin" type="password" inputmode="numeric" required maxlength="8"></label>
 <label class="consent"><input type="checkbox" name="consent" value="1" required>
  I agree: <span>{CONSENT_TEXT}</span></label>
 <button type="submit">Link and allow</button>
</form>
<p class="small">Ask anyone in your household for the code, or create one on the CareLoop home page.
Not medical advice.</p></main></body></html>"""
