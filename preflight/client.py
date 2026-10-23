"""client — a minimal MCP Streamable-HTTP client on the standard library only.

Deliberately *not* a friendly wrapper: the preflight checks need to send badly formed
requests (wrong Accept, foreign Origin, unknown session id, batch arrays, truncated
JSON) and to read the exact status code and headers back, so every call returns the
raw reply and never raises for a 4xx/5xx.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ACCEPT = "application/json, text/event-stream"
JSON_CT = "application/json"
_UNSET = object()


class ConnectionFailure(Exception):
    """The endpoint could not be reached at all: exit code 2, never a FAIL line."""


class Reply:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers or {}
        self.body = body or b""

    def header(self, name, default=None):
        low = str(name).lower()
        for k, v in self.headers.items():
            if str(k).lower() == low:
                return v
        return default

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception:
            return None

    def text(self):
        return self.body.decode("utf-8", errors="replace")

    def __repr__(self):
        return "<Reply %d %db>" % (self.status, len(self.body))


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    """3xx must be observable: the authorization screen's redirect carries the code."""

    def http_response(self, request, response):
        if response.code in (301, 302, 303, 307, 308):
            return response
        return super().http_response(request, response)

    https_response = http_response


class McpClient:
    def __init__(self, mcp_url, token=None, timeout=20.0):
        parts = urllib.parse.urlsplit(mcp_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ConnectionFailure("not an http(s) MCP endpoint: %r" % mcp_url)
        self.mcp_url = mcp_url
        self.base = "%s://%s" % (parts.scheme, parts.netloc)
        self.token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)
        self.last_ms = 0.0

    # ------------------------------------------------------------------ plain HTTP
    def http(self, method, path, body=None, headers=None, ctype=None):
        """path is relative to the endpoint origin, e.g. '/mcp' or '/.well-known/...'."""
        import time
        url = path if str(path).startswith("http") else self.base + path
        data = body
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
        if isinstance(data, str):
            data = data.encode()
        h = dict(headers or {})
        if data is not None and ctype and "Content-Type" not in h:
            h["Content-Type"] = ctype
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        t0 = time.perf_counter()
        try:
            with self._opener.open(req, timeout=self.timeout) as res:
                payload, status, hdrs = res.read(), res.status, dict(res.headers)
        except urllib.error.HTTPError as e:                 # 4xx/5xx: still a reply
            payload, status, hdrs = e.read(), e.code, dict(e.headers or {})
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ConnectionFailure("%s %s -> %s" % (method, url, getattr(e, "reason", e)))
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        return Reply(status, hdrs, payload)

    def get(self, path, headers=None):
        return self.http("GET", path, headers=headers)

    def post_json(self, path, obj, headers=None):
        return self.http("POST", path, obj, headers=headers, ctype=JSON_CT)

    def post_form(self, path, fields, headers=None):
        h = {"Content-Type": "application/x-www-form-urlencoded"}
        h.update(headers or {})
        return self.http("POST", path, urllib.parse.urlencode(fields).encode(), headers=h)

    # ------------------------------------------------------------------ MCP layer
    def rpc(self, payload, session=None, protocol=None, token=_UNSET, headers=None, path=None):
        """POST one JSON-RPC message (dict, list or raw bytes) to the MCP endpoint."""
        h = {"Content-Type": JSON_CT, "Accept": DEFAULT_ACCEPT}
        h.update(headers or {})
        tok = self.token if token is _UNSET else token
        if tok:
            h["Authorization"] = "Bearer " + tok
        if session:
            h["MCP-Session-Id"] = session
        if protocol:
            h["MCP-Protocol-Version"] = protocol
        return self.http("POST", path or self.mcp_url, payload, headers=h)

    def call(self, method, params=None, mid="1", session=None, protocol="2025-11-25",
             token=_UNSET, headers=None):
        payload = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            payload["params"] = params
        return self.rpc(payload, session=session, protocol=protocol, token=token, headers=headers)

    def tool(self, name, arguments=None, **kw):
        return self.call("tools/call", {"name": name, "arguments": arguments or {}}, **kw)

    def initialize(self, protocol="2025-11-25", token=_UNSET, client_info=None):
        return self.call("initialize", {
            "protocolVersion": protocol, "capabilities": {},
            "clientInfo": client_info or {"name": "alexa-mcp-preflight", "version": "1.0"}},
            mid="init", token=token, protocol=None)

    def session_id(self, reply):
        return reply.header("MCP-Session-Id")
