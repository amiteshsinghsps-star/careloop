"""Start CareLoop with demo defaults, cross-platform (no `make`, no shell env syntax).

Usage:  python scripts/serve.py [--auth off|token|oauth] [--port 8080] [--chaos ...]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth", default=os.environ.get("CARELOOP_AUTH", "off"))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", default=os.environ.get("PORT", "8080"))
    ap.add_argument("--chaos", default=os.environ.get("CARELOOP_CHAOS", ""),
                    help="comma list of: bedrock_throttle, verify_down, latency_800ms, db_locked")
    ap.add_argument("--public-url", default=os.environ.get("CARELOOP_PUBLIC_URL", ""))
    args = ap.parse_args()

    os.environ["CARELOOP_AUTH"] = args.auth
    os.environ["HOST"] = args.host
    os.environ["PORT"] = str(args.port)
    os.environ["CARELOOP_DEMO"] = os.environ.get("CARELOOP_DEMO", "1")
    if args.chaos:
        os.environ["CARELOOP_CHAOS"] = args.chaos
    if args.public_url:
        os.environ["CARELOOP_PUBLIC_URL"] = args.public_url.rstrip("/")
    elif args.auth == "off":
        os.environ["CARELOOP_PUBLIC_URL"] = f"http://{args.host}:{args.port}"

    # Settings is a module-level singleton: it must be imported after the env is set.
    for name in [m for m in sys.modules if m.startswith("careloop")]:
        del sys.modules[name]
    from careloop import mcp_server
    from careloop.config import CHAOS_FLAGS
    bad = [f for f in (x.strip() for x in args.chaos.split(",")) if f and f not in CHAOS_FLAGS]
    if bad:
        print(f"  note: unknown chaos flag(s) {', '.join(bad)} do nothing; known: {', '.join(CHAOS_FLAGS)}")

    httpd = mcp_server.build_server(args.host, int(args.port))
    print(f"CareLoop  http://{args.host}:{args.port}")
    print(f"  site        http://{args.host}:{args.port}/")
    print(f"  simulator   http://{args.host}:{args.port}/simulator")
    print(f"  companion   http://{args.host}:{args.port}/companion")
    print(f"  judge       http://{args.host}:{args.port}/judge")
    print(f"  MCP         http://{args.host}:{args.port}/mcp   (auth={args.auth}, chaos={args.chaos or 'none'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
