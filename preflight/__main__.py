"""alexa-mcp-preflight CLI.

    python -m preflight http://127.0.0.1:8080/mcp [--json] [--fail-fast] [--skip-auth]
                                                   [--token TOKEN] [options]

Exit codes: 0 every executed check passed, 1 at least one FAIL, 2 the endpoint could not
be reached (or the run could not be completed at all).
"""
import argparse
import sys

from . import __version__
from . import checks_auth, checks_safety, checks_surface, checks_transport
from .client import ConnectionFailure
from .context import Ctx, Opts
from .report import Reporter

EPILOG = """\
examples:
  # local demo server (auth off): transport + safety + performance + card, no OAuth probing
  python -m preflight http://127.0.0.1:8080/mcp --skip-auth

  # a linked household on the OAuth server
  python -m preflight http://127.0.0.1:8081/mcp --token <access token from the linking flow>

  # let preflight do the PKCE linking itself (needs the registered client + household PIN)
  set CARELOOP_CLIENT_SECRET=... CARELOOP_FAMILY_PIN=...
  python -m preflight http://127.0.0.1:8081/mcp

  # machine-readable
  python -m preflight http://127.0.0.1:8080/mcp --json | python -m json.tool
"""


def build_parser():
    ap = argparse.ArgumentParser(
        prog="python -m preflight",
        description=("Audit a CareLoop-style MCP endpoint the way an Alexa+ skill would: MCP "
                     "Streamable-HTTP contract, OAuth 2.1 + PKCE, the dose-free voice surface, "
                     "the round-trip latency budget and the mcp-app card."),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("url", help="the MCP endpoint, e.g. http://127.0.0.1:8080/mcp")
    ap.add_argument("--token", metavar="TOKEN", help="a linked OAuth access token to call tools with")
    ap.add_argument("--json", action="store_true", help="emit one JSON document instead of lines")
    ap.add_argument("--fail-fast", action="store_true", dest="fail_fast",
                    help="stop at the first FAIL instead of finishing the audit")
    ap.add_argument("--skip-auth", action="store_true", dest="skip_auth",
                    help="do not attempt any OAuth linking; auth checks are reported as SKIP")
    ap.add_argument("--client-id", metavar="ID", help="registered OAuth client id "
                                                      "(default $CARELOOP_CLIENT_ID)")
    ap.add_argument("--client-secret", metavar="SECRET", help="registered OAuth client secret "
                                                              "(default $CARELOOP_CLIENT_SECRET)")
    ap.add_argument("--household-code", metavar="CODE", help="household code used for linking and "
                                                             "seeding (default CASA01)")
    ap.add_argument("--household-pin", metavar="PIN", help="family PIN; without it the PKCE linking "
                                                           "and companion-capture checks are SKIPped")
    ap.add_argument("--redirect-uri", metavar="URI", help="a redirect URI the server has registered "
                                                          "(default http://localhost:8080/callback)")
    ap.add_argument("--timeout", type=float, default=20.0, help="per-request timeout in seconds")
    ap.add_argument("--version", action="version", version="alexa-mcp-preflight %s" % __version__)
    return ap


def run(opts, quiet=False):
    rep = Reporter(as_json=opts.json_out, fail_fast=opts.fail_fast)
    ctx = Ctx(opts)
    try:
        ctx.discover()
    except ConnectionFailure as e:
        return _unreachable(opts, e)
    # If we have no token but can link, do it before the transport checks so the whole
    # audit runs against a real linked household.
    if not opts.token and not opts.skip_auth and ctx.auth_mode != "off" and opts.can_link:
        ctx.link_out = checks_auth._do_link(ctx)
        tokens = ctx.link_out.get("tokens") or {}
        if tokens.get("access_token"):
            ctx.client.token = tokens["access_token"]
            ctx.bootstrap_session()
    steps = [checks_transport.run, checks_auth.run, checks_safety.run,
             checks_transport.run_after_seed, surface_performance, surface_card]
    for step in steps:
        if rep.halted:
            break
        try:
            step(ctx, rep)
        except ConnectionFailure as e:
            return _unreachable(opts, e)
    meta = {"endpoint": opts.url, "auth_mode": ctx.auth_mode or "unknown",
            "session": bool(ctx.session), "seeded": ctx.seed_way,
            "checks_run": len(rep.results), "stopped_early": rep.halted}
    code = rep.emit(meta)
    if rep.halted and not quiet:
        rep.stream.write("(--fail-fast: later checks were not executed)\n")
    return code


def surface_performance(ctx, rep):
    return checks_surface.run_performance(ctx, rep)


def surface_card(ctx, rep):
    return checks_surface.run_card(ctx, rep)


def _unreachable(opts, err):
    message = "cannot reach the MCP endpoint %s: %s" % (opts.url, err)
    if opts.json_out:
        import json
        print(json.dumps({"checks": [], "summary": {"endpoint": opts.url, "error": str(err),
                                                    "exit_code": 2}}, indent=2))
    else:
        print(message, file=sys.stderr)
    return 2


def main(argv=None):
    args = build_parser().parse_args(argv)
    opts = Opts(url=args.url, token=args.token, skip_auth=args.skip_auth, json_out=args.json,
                fail_fast=args.fail_fast, client_id=args.client_id, client_secret=args.client_secret,
                household_code=args.household_code, household_pin=args.household_pin,
                redirect_uri=args.redirect_uri, timeout=args.timeout)
    try:
        return run(opts)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 2
    except Exception as e:                       # never hand a judge a traceback
        print("preflight could not finish: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
