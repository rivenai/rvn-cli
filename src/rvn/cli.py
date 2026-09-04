"""Argument parsing and dispatch for the rvn CLI."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import DEFAULT_BASE_URL, MissingKeyError, resolve_key
from .transport import RivenAPIError

PROG = "rvn"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="rvn — the Riven CLI. Grounded answers, citations, deep research, and Pages over the Riven gateway.",
        epilog="Auth: RIVEN_API_KEY env, --key flag, or rvn login (stored at ~/.rvn/key).",
    )
    parser.add_argument("--version", action="version", version=f"rvn {__version__}")
    parser.add_argument(
        "--key",
        metavar="RVN_KEY",
        default=None,
        help="Riven API key (rvn_*). Prefer RIVEN_API_KEY env or rvn login; the key is never printed.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Gateway base URL (default {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON (scripting mode).",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_chat = sub.add_parser("chat", help="one-shot grounded ask (default model riven-research)")
    p_chat.add_argument("query", nargs="*", help="the question (omit with -i for the REPL)")
    p_chat.add_argument("-i", "--interactive", action="store_true", help="interactive REPL")
    p_chat.add_argument("-m", "--model", default="riven-research", help="model id (default riven-research)")
    p_chat.add_argument("--no-stream", action="store_true", help="buffer and print once (no SSE)")

    p_models = sub.add_parser("models", help="list models available on the gateway (/v1/models)")

    p_deep = sub.add_parser("deep", help="deep research run with streamed progress")
    p_deep.add_argument("query", nargs="*", help="the research question")
    p_deep.add_argument("--timeout", type=float, default=300, help="seconds to wait for the run (default 300)")
    p_deep.add_argument("--task", metavar="ID", default=None, help="fetch an existing task's report by id")

    p_pages = sub.add_parser("pages", help="generate a Riven Page (Pages v0.1 API)")
    p_pages.add_argument("topic", nargs="*", help="the page topic")
    p_pages.add_argument("--tier", default="research", choices=["research", "pro", "core"], help="generation tier")

    p_login = sub.add_parser("login", help="store an API key at ~/.rvn/key (console flow or paste)")
    sub.add_parser("logout", help="remove the stored API key")
    sub.add_parser("whoami", help="verify the stored key against the gateway")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # --- commands that need no key -----------------------------------------
    if args.command == "login":
        from .commands import login as do_login

        do_login()
        return 0
    if args.command == "logout":
        from .config import stored_key_location

        path = stored_key_location()
        if path.is_file():
            path.unlink()
            print(f"Removed {path}")
        else:
            print("No stored key.")
        return 0

    if not args.command:
        parser.print_help()
        return 0 if argv in (None, []) else 2

    # --- all remaining commands need auth ----------------------------------
    try:
        api_key = resolve_key(args.key)
    except (MissingKeyError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2

    from . import commands as cmd

    try:
        if args.command == "chat":
            query = " ".join(args.query).strip()
            if args.interactive:
                cmd.repl(api_key, args.base_url, args.model)
                return 0
            if not query:
                print("chat: pass a query (or -i for the REPL). Try: rvn chat \"who is the CEO of NVIDIA\"", file=sys.stderr)
                return 2
            result = cmd.chat(
                query,
                api_key,
                args.base_url,
                model=args.model,
                as_json=args.json_mode,
                stream=not args.no_stream,
            )
            if args.json_mode:
                cmd._emit_json(result)
            return 0

        if args.command == "models":
            cmd.models(api_key, args.base_url, as_json=args.json_mode)
            return 0

        if args.command == "deep":
            if args.task:
                url = f"{args.base_url}/tasks/{args.task}/artifacts"
                from .transport import request

                status, arts, _ = request(url, api_key, "GET")
                artifacts = arts.get("artifacts", [])
                report = next((a.get("content_md") for a in artifacts if a.get("kind") == "report"), None)
                if report is None:
                    report = next((a.get("content_md") for a in artifacts), "(no artifacts)")
                if args.json_mode:
                    cmd._emit_json({"task_id": args.task, "artifacts": [
                        {"id": a.get("id"), "kind": a.get("kind"), "title": a.get("title")} for a in artifacts], "report": report})
                else:
                    print(report)
                return 0
            query = " ".join(args.query).strip()
            if not query:
                print("deep: pass a research query. Try: rvn deep \"compare rust and zig for embedded systems\"", file=sys.stderr)
                return 2
            result = cmd.deep(query, api_key, args.base_url, as_json=args.json_mode, timeout_s=args.timeout)
            if args.json_mode:
                cmd._emit_json(result)
            return 0

        if args.command == "pages":
            topic = " ".join(args.topic).strip()
            if not topic:
                print("pages: pass a topic. Try: rvn pages \"state of AI inference pricing 2026\"", file=sys.stderr)
                return 2
            cmd.pages(topic, api_key, args.base_url, as_json=args.json_mode, tier=args.tier)
            return 0

        if args.command == "whoami":
            cmd.whoami(api_key, args.base_url, as_json=args.json_mode)
            return 0

        parser.error(f"unknown command {args.command!r}")
        return 2

    except RivenAPIError as e:
        if args.json_mode:
            cmd._emit_json(e.to_json_dict())
        else:
            print(str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n(interrupted)", file=sys.stderr)
        return 130


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
