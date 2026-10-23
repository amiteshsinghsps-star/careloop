"""checks.auth — OAuth 2.1 + PKCE, RFC 9728 metadata and token-scope checks."""
import base64
import hashlib
import json
import secrets
import urllib.parse

from .context import PROTOCOLS

SECTION = "auth"


def run(ctx, rep):
    unauthorized_challenge(ctx, rep)
    protected_resource_doc(ctx, rep)
    authorization_server_doc(ctx, rep)
    documents_agree(ctx, rep)
    pkce_only_s256(ctx, rep)
    no_dcr_no_oidc(ctx, rep)
    plain_challenge_refused(ctx, rep)
    redirect_uri_strict(ctx, rep)
    resource_audience_checked(ctx, rep)
    query_string_credentials_refused(ctx, rep)
    client_credentials_scoped(ctx, rep)
    linking_flow(ctx, rep)
    revocation(ctx, rep)


# ------------------------------------------------------------------ 401 challenge
def unauthorized_challenge(ctx, rep):
    cid = "unauthenticated-401-challenge"
    if ctx.opts.skip_auth:
        return rep.skip(cid, SECTION, "skipped (--skip-auth)")
    if ctx.auth_mode == "off":
        return rep.skip(cid, SECTION, "the server runs with CARELOOP_AUTH=off, so there is no "
                                      "token challenge to audit (by design, demo mode)")
    payload = {"jsonrpc": "2.0", "id": "u-1", "method": "initialize", "params": {}}
    r = ctx.client.rpc(payload, token=None, headers={})
    chal = r.header("WWW-Authenticate") or ""
    well_known = ctx.client.base + "/.well-known/oauth-protected-resource"
    if r.status == 401 and "resource_metadata=" in chal and well_known in chal.replace("\\/", "/"):
        return rep.ok(cid, SECTION, "anonymous POST /mcp -> 401 with a Bearer challenge naming %s"
                      % well_known)
    return rep.bad(cid, SECTION, "anonymous POST /mcp -> %d, WWW-Authenticate: %r "
                   "(expected 401 + resource_metadata)" % (r.status, chal))


def protected_resource_doc(ctx, rep):
    cid = "protected-resource-metadata"
    status, doc = ctx.metadata()[0]
    if status != 200 or not isinstance(doc, dict):
        return rep.bad(cid, SECTION, "GET /.well-known/oauth-protected-resource -> %d" % status)
    problems = []
    want = ctx.client.mcp_url
    if doc.get("resource", "").rstrip("/") != want.rstrip("/"):
        problems.append("resource %r does not name this MCP endpoint %r" % (doc.get("resource"), want))
    if not isinstance(doc.get("authorization_servers"), list) or not doc["authorization_servers"]:
        problems.append("authorization_servers missing")
    if not doc.get("scopes_supported"):
        problems.append("scopes_supported missing")
    if doc.get("bearer_methods_supported") not in (None, ["header"]) and "header" not in (
            doc.get("bearer_methods_supported") or []):
        problems.append("bearer_methods_supported should include 'header'")
    return _finish(rep, cid, problems, "resource=%s, servers=%s, scopes=%s"
                   % (doc.get("resource"), doc.get("authorization_servers"), doc.get("scopes_supported")))


def authorization_server_doc(ctx, rep):
    cid = "authorization-server-metadata"
    status, doc = ctx.metadata()[1]
    if status != 200 or not isinstance(doc, dict):
        return rep.bad(cid, SECTION, "GET /.well-known/oauth-authorization-server -> %d" % status)
    problems = []
    base = ctx.client.base
    for key, want in (("issuer", base), ("authorization_endpoint", base + "/oauth/authorize"),
                      ("token_endpoint", base + "/oauth/token")):
        got = (doc.get(key) or "").rstrip("/")
        if got != want.rstrip("/"):
            problems.append("%s is %r, must be %r" % (key, doc.get(key), want))
    if "code" not in (doc.get("response_types_supported") or []):
        problems.append("response_types_supported must include 'code'")
    grants = doc.get("grant_types_supported") or []
    for g in ("authorization_code", "refresh_token"):
        if g not in grants:
            problems.append("grant_types_supported missing %s" % g)
    if "client_secret_basic" not in (doc.get("token_endpoint_auth_methods_supported") or []):
        problems.append("token_endpoint_auth_methods_supported must include client_secret_basic")
    if problems:
        return rep.bad(cid, SECTION, "; ".join(problems))
    rep.ok(cid, SECTION, "issuer/authorization/token endpoints all resolve to this host and the "
                         "advertised endpoints answer")


def documents_agree(ctx, rep):
    cid = "metadata-internally-consistent"
    (pstatus, prot), (astatus, asm) = ctx.metadata()
    if pstatus != 200 or astatus != 200:
        return rep.skip(cid, SECTION, "one of the metadata documents is not reachable (PR %d, AS %d)"
                       % (pstatus, astatus))
    problems = []
    servers = prot.get("authorization_servers") or []
    if servers and asm.get("issuer") and servers[0].rstrip("/") != asm["issuer"].rstrip("/"):
        problems.append("protected-resource says the AS is %r but the AS metadata issuer is %r"
                        % (servers[0], asm.get("issuer")))
    if set(prot.get("scopes_supported") or []) != set(asm.get("scopes_supported") or []):
        problems.append("scopes differ: PR=%s AS=%s" % (prot.get("scopes_supported"),
                                                        asm.get("scopes_supported")))
    # every advertised endpoint must actually exist
    t = ctx.client.post_form("/oauth/token", {"grant_type": "client_credentials"})
    if t.status not in (400, 401):
        problems.append("advertised token endpoint answered %d to an uncredentialed request" % t.status)
    a = ctx.client.get("/oauth/authorize")
    if a.status != 400 or "invalid_request" not in a.text():
        problems.append("advertised authorization endpoint did not reject a bare request (HTTP %d)" % a.status)
    mcp_url = prot.get("resource", "")
    if mcp_url and mcp_url.rstrip("/") != ctx.client.mcp_url.rstrip("/"):
        problems.append("the resource this client is talking to is %s but the document publishes %s"
                        % (ctx.client.mcp_url, mcp_url))
    return _finish(rep, cid, problems,
                   "issuer == authorization_servers[0], scopes agree, both endpoints resolve")


def pkce_only_s256(ctx, rep):
    cid = "pkce-s256-only"
    status, doc = ctx.metadata()[1]
    if status != 200 or not isinstance(doc, dict):
        return rep.skip(cid, SECTION, "authorization-server metadata not reachable (%d)" % status)
    methods = doc.get("code_challenge_methods_supported")
    if methods == ["S256"]:
        return rep.ok(cid, SECTION, "code_challenge_methods_supported is exactly ['S256']")
    return rep.bad(cid, SECTION, "code_challenge_methods_supported is %r; only S256 may be offered"
                   % (methods,))


def no_dcr_no_oidc(ctx, rep):
    cid = "no-dynamic-registration-no-oidc"
    status, doc = ctx.metadata()[1]
    if status != 200 or not isinstance(doc, dict):
        return rep.skip(cid, SECTION, "authorization-server metadata not reachable (%d)" % status)
    problems = []
    for key in ("registration_endpoint", "jwks_uri", "userinfo_endpoint", "end_session_endpoint",
                "introspection_endpoint", "revocation_endpoint"):
        if key in doc:
            problems.append("%s is advertised (static clients + opaque bearer tokens only)" % key)
    blob = json.dumps(doc).lower()
    if "openid" in blob or "id_token" in blob or "userinfo" in blob:
        problems.append("the document advertises OIDC surface (openid / id_token / userinfo)")
    identity = [s for s in (doc.get("scopes_supported") or [])
                if s.split(":")[0] in ("openid", "email", "profile", "offline_access")
                or s in ("openid", "email", "profile", "offline_access")]
    if identity:
        problems.append("OIDC identity scopes offered: %s" % ", ".join(identity))
    return _finish(rep, cid, problems,
                   "grant types=%s, scopes=%s, no registration or OIDC endpoints"
                   % (doc.get("grant_types_supported"), doc.get("scopes_supported")))


# ------------------------------------------------------------------ authorize screen
def _authorize_url(ctx, **over):
    verifier = over.pop("verifier", None) or secrets.token_urlsafe(40)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {"response_type": "code", "client_id": ctx.opts.client_id,
              "redirect_uri": ctx.opts.redirect_uri, "state": "preflight-state-1",
              "code_challenge": challenge, "code_challenge_method": "S256",
              "scope": "mcp:tools", "resource": ctx.client.mcp_url}
    params.update(over)
    if "code_challenge" not in over:
        params["code_challenge"] = challenge
    return verifier, urllib.parse.urlencode(params)


def plain_challenge_refused(ctx, rep):
    cid = "plain-code-challenge-refused"
    _, query = _authorize_url(ctx, code_challenge="x" * 43, code_challenge_method="plain")
    r = ctx.client.get("/oauth/authorize?" + query)
    text = r.text()
    if r.status == 400 and "S256" in text and "code=" not in text:
        return rep.ok(cid, SECTION, "code_challenge_method=plain is refused with 400 and never issues a code")
    if r.status == 400:
        return rep.bad(cid, SECTION, "plain challenge refused (400) but the error does not name S256: %s"
                       % _snippet(text))
    return rep.bad(cid, SECTION, "authorize accepted a plain PKCE challenge: HTTP %d" % r.status)


def redirect_uri_strict(ctx, rep):
    cid = "redirect-uri-strict-match"
    _, query = _authorize_url(ctx, redirect_uri="https://attacker-preflight.example/steal",
                              code_challenge="y" * 43)
    r = ctx.client.get("/oauth/authorize?" + query)
    good_query = _authorize_url(ctx, code_challenge="z" * 43)[1]
    good = ctx.client.get("/oauth/authorize?" + good_query)
    if r.status == 400 and "invalid_redirect_uri" in r.text() and good.status == 200:
        return rep.ok(cid, SECTION, "an unregistered redirect_uri is refused; a registered one renders "
                                    "the consent screen")
    return rep.bad(cid, SECTION, "unregistered redirect -> HTTP %d; registered -> HTTP %d "
                   "(expected 400 / 200)" % (r.status, good.status))


def resource_audience_checked(ctx, rep):
    cid = "resource-audience-checked"
    _, query = _authorize_url(ctx, resource="https://other-mcp.example/mcp", code_challenge="w" * 43)
    r = ctx.client.get("/oauth/authorize?" + query)
    if r.status == 400 and "resource mismatch" in r.text():
        return rep.ok(cid, SECTION, "an authorization request bound to another server's resource is refused")
    return rep.bad(cid, SECTION, "authorize accepted resource=https://other-mcp.example/mcp (HTTP %d): "
                                 "tokens are not audience-bound at request time" % r.status)


def query_string_credentials_refused(ctx, rep):
    cid = "credentials-in-query-rejected"
    g = ctx.client.get("/oauth/token?client_id=preflight&access_token=secret-value")
    body = g.json() or {}
    f = ctx.client.post_form("/oauth/token", {"grant_type": "client_credentials",
                                              "client_id": ctx.opts.client_id,
                                              "client_secret": ctx.opts.client_secret or "x"})
    fdoc = f.json() or {}
    if g.status == 400 and "credentials_in_query_rejected" in json.dumps(body) \
            and f.status == 400 and "Basic header" in str(fdoc.get("hint", "")):
        return rep.ok(cid, SECTION, "GET credentials in the query string -> 400; a secret in the form body "
                                    "-> 400 with 'send credentials in the Basic header only'")
    return rep.bad(cid, SECTION, "query-string GET -> %d %s; form-body secret -> %d %s "
                   "(expected 400 both)" % (g.status, body, f.status, fdoc))


# ------------------------------------------------------------------ token endpoint
def _basic(cid, secret):
    return "Basic " + base64.b64encode(("%s:%s" % (cid, secret)).encode()).decode()


def client_credentials_scoped(ctx, rep):
    cid = "client-credentials-service-scoped"
    if ctx.opts.skip_auth:
        return rep.skip(cid, SECTION, "skipped (--skip-auth)")
    if not ctx.opts.client_secret:
        return rep.skip(cid, SECTION, "no client secret available: pass --client-secret or set "
                                      "CARELOOP_CLIENT_SECRET to audit the service grant")
    hdr = {"Authorization": _basic(ctx.opts.client_id, ctx.opts.client_secret)}
    r = ctx.client.post_form("/oauth/token", {"grant_type": "client_credentials",
                                              "scope": "mcp:service"}, headers=hdr)
    doc = r.json() or {}
    if r.status != 200:
        return rep.bad(cid, SECTION, "client_credentials -> HTTP %d %s (is CARELOOP_CLIENT_SECRET set "
                       "on the server?)" % (r.status, doc))
    problems = []
    if doc.get("scope") != "mcp:service":
        problems.append("scope is %r, must be service-only" % doc.get("scope"))
    if not (0 < int(doc.get("expires_in") or 0) <= 3600):
        problems.append("expires_in %r is not within 3600 s" % doc.get("expires_in"))
    if "refresh_token" in doc:
        problems.append("a client_credentials grant must not return a refresh token")
    if "hid" in doc or "sub" in doc:
        problems.append("the token response leaks an internal identity: %s" % sorted(doc))
    if doc.get("token_type", "").lower() != "bearer":
        problems.append("token_type %r" % doc.get("token_type"))
    # and the service token must not be a household identity
    over = ctx.client.post_form("/oauth/token", {"grant_type": "client_credentials",
                                                 "scope": "mcp:tools"}, headers=hdr)
    if over.status != 400 or "invalid_scope" not in json.dumps(over.json() or {}):
        problems.append("asking for mcp:tools via client_credentials -> HTTP %d (expected 400 invalid_scope)"
                        % over.status)
    ctx.service_token = doc.get("access_token")
    return _finish(rep, cid, problems,
                   "expires_in=%s scope=%s refresh=%s; a tools scope request is refused"
                   % (doc.get("expires_in"), doc.get("scope"), "refresh_token" in doc))


def linking_flow(ctx, rep):
    """Full PKCE: consent screen -> code -> token, plus single-use codes and verifiers."""
    cid = "pkce-linking-flow"
    if ctx.link_out is None:
        ctx.link_out = _do_link(ctx)
    out = ctx.link_out
    if out.get("skip"):
        return rep.skip(cid, SECTION, out["skip"])
    if out.get("problems"):
        return rep.bad(cid, SECTION, "; ".join(out["problems"][:4]))
    tokens = out.get("tokens") or {}
    ctx.linked = dict(tokens)
    return rep.ok(cid, SECTION,
                  "consent screen rendered, code delivered only to the registered redirect URI with "
                  "state preserved, wrong verifier + reused code both refused, scope=%s, expires_in=%s, "
                  "no internal identity in the token response"
                  % (tokens.get("scope"), tokens.get("expires_in")))


def _do_link(ctx):
    out = {"steps": [], "problems": []}
    if ctx.opts.skip_auth:
        out["skip"] = "skipped (--skip-auth)"
        return out
    if not ctx.opts.can_link:
        out["skip"] = ("needs both a client secret and a household PIN: pass --client-secret and "
                       "--household-pin (or CARELOOP_CLIENT_SECRET / CARELOOP_FAMILY_PIN)")
        return out
    hdr = {"Authorization": _basic(ctx.opts.client_id, ctx.opts.client_secret)}
    # 1. consent screen renders, with a password field and a consent checkbox
    verifier, query = _authorize_url(ctx)
    screen = ctx.client.get("/oauth/authorize?" + query)
    page = screen.text()
    out["steps"].append("consent screen HTTP %d" % screen.status)
    if screen.status != 200 or "consent" not in page.lower():
        out["problems"].append("no consent screen at HTTP %d" % screen.status)
    if 'type="password"' not in page:
        out["problems"].append("the PIN field is not a password field")
    # 2. a wrong PIN must not yield a code
    bad = ctx.client.post_form("/oauth/authorize?" + query,
                               {"code": ctx.opts.household_code, "pin": "000000", "consent": "1"},
                               {"Content-Type": "application/x-www-form-urlencoded"})
    if bad.status != 200 or "code=" in bad.text():
        out["problems"].append("a wrong PIN reached a code (HTTP %d)" % bad.status)
    # 3. consent is mandatory
    noconsent = ctx.client.post_form("/oauth/authorize?" + query,
                                     {"code": ctx.opts.household_code, "pin": ctx.opts.household_pin},
                                     {"Content-Type": "application/x-www-form-urlencoded"})
    if noconsent.status == 302 or "consent" not in noconsent.text().lower():
        out["problems"].append("linking without consent was not refused")
    # 4. real link -> 302 with code + state, nothing else
    ok = ctx.client.post_form("/oauth/authorize?" + query,
                              {"code": ctx.opts.household_code, "pin": ctx.opts.household_pin,
                               "consent": "1"}, {"Content-Type": "application/x-www-form-urlencoded"})
    if ok.status != 302:
        out["problems"].append("consented link -> HTTP %d, expected 302" % ok.status)
        return out
    loc = ok.header("Location") or ""
    parts = urllib.parse.urlsplit(loc)
    q = urllib.parse.parse_qs(parts.query)
    if not q.get("code") or q.get("state") != ["preflight-state-1"]:
        out["problems"].append("the redirect lost the code or the state: %r" % loc)
        return out
    if not loc.startswith(ctx.opts.redirect_uri):
        out["problems"].append("redirected somewhere other than the registered URI: %r" % loc)
    code = (q.get("code") or [""])[0]
    out["code"] = code
    out["verifier"] = verifier
    out["query"] = query
    # 5. wrong verifier must fail and must burn the code
    wrong = ctx.client.post_form("/oauth/token", {"grant_type": "authorization_code", "code": code,
                                                  "redirect_uri": ctx.opts.redirect_uri,
                                                  "code_verifier": secrets.token_urlsafe(40)}, hdr)
    if wrong.status != 400:
        out["problems"].append("a wrong code_verifier -> HTTP %d (expected 400)" % wrong.status)
    reuse = ctx.client.post_form("/oauth/token", {"grant_type": "authorization_code", "code": code,
                                                  "redirect_uri": ctx.opts.redirect_uri,
                                                  "verifier": verifier, "code_verifier": verifier}, hdr)
    if reuse.status != 400:
        out["problems"].append("a code could be presented twice (HTTP %d)" % reuse.status)
    # 6. a fresh code with the right verifier yields tokens
    verifier2, query2 = _authorize_url(ctx)
    ok2 = ctx.client.post_form("/oauth/authorize?" + query2,
                               {"code": ctx.opts.household_code, "pin": ctx.opts.household_pin,
                                "consent": "1"}, {"Content-Type": "application/x-www-form-urlencoded"})
    loc2 = ok2.header("Location") or ""
    code2 = (urllib.parse.parse_qs(urllib.parse.urlsplit(loc2).query).get("code") or [""])[0]
    tok = ctx.client.post_form("/oauth/token", {"grant_type": "authorization_code", "code": code2,
                                                "redirect_uri": ctx.opts.redirect_uri,
                                                "code_verifier": verifier2}, hdr)
    doc = tok.json() or {}
    if tok.status != 200:
        out["problems"].append("token exchange -> HTTP %d %s" % (tok.status, doc))
        return out
    out["tokens"] = doc
    for key in ("access_token", "refresh_token"):
        if not doc.get(key):
            out["problems"].append("no %s in the token response" % key)
    if doc.get("scope") != "mcp:tools":
        out["problems"].append("linked scope is %r, expected mcp:tools" % doc.get("scope"))
    if not (0 < int(doc.get("expires_in") or 0) <= 3600):
        out["problems"].append("access token lifetime %r exceeds 3600 s" % doc.get("expires_in"))
    for leak in ("hid", "link_id", "sub", "role"):
        if leak in doc:
            out["problems"].append("the token response leaks %r" % leak)
    # 7. the access token works on the MCP endpoint
    probe = ctx.client.call("ping", mid="lk-1", token=doc.get("access_token"), protocol=PROTOCOLS[0])
    if probe.status != 200:
        out["problems"].append("the linked token cannot use the endpoint (ping -> %d)" % probe.status)
    return out


def revocation(ctx, rep):
    """A revoked credential must stop working immediately (proven over HTTP via rotation)."""
    cid = "revoked-token-rejected"
    if ctx.opts.skip_auth:
        return rep.skip(cid, SECTION, "skipped (--skip-auth)")
    if not (ctx.linked and ctx.linked.get("refresh_token")):
        return rep.skip(cid, SECTION, "needs a household token this run minted itself (linking was "
                                      "skipped): revocation is audited through the refresh rotation "
                                      "that revokes the credential it replaced")
    hdr = {"Authorization": _basic(ctx.opts.client_id, ctx.opts.client_secret)}
    old_access = ctx.linked["access_token"]
    old_refresh = ctx.linked["refresh_token"]
    r = ctx.client.post_form("/oauth/token", {"grant_type": "refresh_token",
                                              "refresh_token": old_refresh}, hdr)
    doc = r.json() or {}
    if r.status != 200:
        return rep.bad(cid, SECTION, "refresh grant (no `resource` parameter, as Alexa sends it) "
                       "-> HTTP %d %s" % (r.status, doc))
    problems = []
    new_access = doc.get("access_token")
    if new_access == old_access:
        problems.append("rotation returned the same access token")
    dead = ctx.client.call("ping", mid="rv-1", token=old_access, session=None)
    chal = dead.header("WWW-Authenticate") or ""
    if dead.status != 401:
        problems.append("the revoked access token still works on /mcp (HTTP %d)" % dead.status)
    replay = ctx.client.post_form("/oauth/token", {"grant_type": "refresh_token",
                                                   "refresh_token": old_refresh}, hdr)
    if replay.status == 200:
        problems.append("the old refresh token is still redeemable after rotation")
    live = ctx.client.call("ping", mid="rv-2", token=new_access, session=None)
    if live.status != 200:
        problems.append("the freshly rotated token does not work (HTTP %d)" % live.status)
    if not chal.startswith("Bearer"):
        problems.append("the 401 carried no Bearer challenge")
    ctx.linked["access_token"] = new_access
    ctx.linked["refresh_token"] = doc.get("refresh_token")
    # if this run is driving with the token it minted, keep using the live one
    if ctx.client.token == old_access:
        ctx.client.token = new_access
        ctx.session = None
        ctx.bootstrap_session()
    return _finish(rep, cid, problems,
                   "after rotation the replaced access token gets 401 + Bearer challenge and the "
                   "spent refresh token is refused")


# ------------------------------------------------------------------ small helpers
def _finish(rep, cid, problems, good_detail):
    if problems:
        return rep.bad(cid, SECTION, "; ".join(problems[:4]))
    return rep.ok(cid, SECTION, good_detail)


def _snippet(text, n=140):
    return " ".join(str(text or "").split())[:n]
