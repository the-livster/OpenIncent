"""`python -m icm_engine.assistant`: the OpenIncent MCP server on stdio.

The same server as `icm mcp`, launchable with an absolute interpreter path,
which is what MCP clients need (they start servers with a near-empty PATH).
"""

import argparse

from icm_engine.assistant.server import print_config, serve, serve_http


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m icm_engine.assistant",
        description="Serve OpenIncent to an MCP client (Claude Desktop, Claude Code, ...) over stdio.",
    )
    parser.add_argument("--db", default=None, help="Database file (default: ICM_DB_PATH, else the app's database)")
    parser.add_argument("--org", default="default", help="Organisation within the database")
    parser.add_argument("--payee", default=None, help="Answer as this payee only: their own pay, nothing else")
    parser.add_argument("--read-only", action="store_true", help="Leave out the pay-cycle tools")
    parser.add_argument("--print-config", action="store_true", help="Print the client configuration and exit")
    parser.add_argument("--http", action="store_true",
                        help="Serve everyone given access (icm access grant) over HTTP, each as themselves")
    parser.add_argument("--host", default="127.0.0.1", help="With --http: the address to listen on")
    parser.add_argument("--port", type=int, default=8765, help="With --http: the port to listen on")
    parser.add_argument("--public-url", default=None, help="With --http: the https:// address people connect to")
    args = parser.parse_args(argv)
    if args.http:
        if args.payee or args.read_only or args.print_config:
            parser.error("with --http each person signs in with their own access; "
                         "--payee, --read-only and --print-config are for stdio")
        serve_http(args.db, org=args.org, host=args.host, port=args.port, public_url=args.public_url)
    elif args.print_config:
        print_config(args.db, org=args.org, payee=args.payee, read_only=args.read_only)
    else:
        serve(args.db, org=args.org, payee=args.payee, read_only=args.read_only)


if __name__ == "__main__":
    main()
