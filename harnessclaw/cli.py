"""`harnessclaw` command line."""

from __future__ import annotations

import argparse
import json
import logging
import shlex
import sys
from typing import Any

from harnessclaw import __version__
from harnessclaw.client import MemoryClient, ServerError
from harnessclaw.config import Config, default_home


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        return args.func(args) or 0
    except (ServerError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"harnessclaw: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harnessclaw", description="A personal assistant with a memory every AI tool can share.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(metavar="command")

    c = sub.add_parser("init", help="create the config and memory folder")
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=8765)
    c.set_defaults(func=cmd_init)

    c = sub.add_parser("serve", help="run the memory server, the assistant, and Discord if set up")
    c.set_defaults(func=cmd_serve)

    c = sub.add_parser("status", help="what the server holds and how it's configured")
    c.set_defaults(func=cmd_status)

    c = sub.add_parser("recall", help="the memory context for a message")
    c.add_argument("query")
    c.add_argument("--project")
    c.add_argument("--limit", type=int)
    c.add_argument("--json", action="store_true", help="print the facts as JSON")
    c.set_defaults(func=cmd_recall)

    c = sub.add_parser("remember", help="save a fact (recorded as written by you)")
    c.add_argument("content")
    c.add_argument("--category")
    c.add_argument("--project")
    c.add_argument("--private", action="store_true", help="only shown to clients with private access")
    c.add_argument("--replaces", metavar="ID")
    c.set_defaults(func=cmd_remember)

    c = sub.add_parser("forget", help="forget a fact by id")
    c.add_argument("id")
    c.set_defaults(func=cmd_forget)

    c = sub.add_parser("list", help="list facts")
    c.add_argument("--project")
    c.add_argument("--all", action="store_true", help="every project too")
    c.set_defaults(func=cmd_list)

    c = sub.add_parser("history", help="recent changes, newest first")
    c.add_argument("--limit", type=int, default=20)
    c.set_defaults(func=cmd_history)

    c = sub.add_parser("undo", help="undo a change from `history`")
    c.add_argument("id")
    c.set_defaults(func=cmd_undo)

    c = sub.add_parser("tidy", help="merge duplicates and drop non-facts now")
    c.add_argument("--dry-run", action="store_true", help="show what would change")
    c.set_defaults(func=cmd_tidy)

    c = sub.add_parser("client", help="manage the tokens harnesses use")
    csub = c.add_subparsers(metavar="action")
    a = csub.add_parser("add", help="add a client and print its token")
    a.add_argument("name")
    a.add_argument("--private", action="store_true", help="may read private facts (local models only)")
    a.set_defaults(func=cmd_client_add)
    a = csub.add_parser("list", help="list clients")
    a.set_defaults(func=cmd_client_list)

    c = sub.add_parser("connect", help="connect a harness to the memory server")
    csub = c.add_subparsers(metavar="harness")
    a = csub.add_parser("claude-code", help="MCP server + automatic recall hook for Claude Code")
    a.add_argument("--apply", action="store_true", help="make the changes (otherwise just show them)")
    a.add_argument("--client", default="claude-code", help="client name to use")
    a.set_defaults(func=cmd_connect_claude_code)

    c = sub.add_parser("chat", help="talk to the assistant from the terminal")
    c.add_argument("message", nargs="?", help="one message; leave out for an interactive chat")
    c.set_defaults(func=cmd_chat)

    c = sub.add_parser("discord", help="set up the Discord bot")
    csub = c.add_subparsers(metavar="action")
    a = csub.add_parser("setup", help="save the bot token and allow your Discord account")
    a.set_defaults(func=cmd_discord_setup)
    a = csub.add_parser("allow", help="allow a Discord user id")
    a.add_argument("user_id", type=int)
    a.set_defaults(func=cmd_discord_allow)
    a = csub.add_parser("disallow", help="stop answering a Discord user id")
    a.add_argument("user_id", type=int)
    a.set_defaults(func=cmd_discord_disallow)

    c = sub.add_parser("hook", help="hook entry points (called by harnesses, not by you)")
    csub = c.add_subparsers(metavar="harness")
    a = csub.add_parser("claude-code", help="UserPromptSubmit hook: reads the event on stdin")
    a.add_argument("--client", default="claude-code")
    a.set_defaults(func=cmd_hook_claude_code)
    return p


# ---------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    home = default_home()
    path = home / "config.toml"
    if path.exists():
        print(f"Already set up: {path}")
        return 0
    config = Config(home=home, host=args.host, port=args.port)
    config.add_client("user", private=True)
    config.save()
    from harnessclaw.memory.store import MemoryStore

    MemoryStore(home)
    print(f"Created {path}")
    print(f"Memory files go in {home / 'memory'}")
    print()
    print("Next:")
    print("  harnessclaw serve                      # start the server")
    print("  harnessclaw chat                       # talk to the assistant")
    print("  harnessclaw discord setup              # talk to it from Discord")
    print("  harnessclaw connect claude-code        # share the memory with Claude Code")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from harnessclaw.server import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.load()
    app = create_app(config)
    print(f"HarnessClaw memory server on {config.url}  (MCP: {config.url}/mcp)", flush=True)
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _print_json(MemoryClient.from_env().status())
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    result = MemoryClient.from_env().recall(args.query, project=args.project, limit=args.limit)
    if args.json:
        _print_json(result["facts"])
    else:
        print(result["context"] or "(no memories yet)")
    return 0


def cmd_remember(args: argparse.Namespace) -> int:
    fields = {"category": args.category, "project": args.project, "private": args.private,
              "replaces": args.replaces}
    result = MemoryClient.from_env().remember(args.content, **fields)
    fact = result["fact"]
    print(f"{'Saved' if result['changed'] else 'Already remembered'} [{fact['id']}] {fact['content']}")
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    fact = MemoryClient.from_env().forget(args.id)["forgot"]
    print(f"Forgot [{fact['id']}] {fact['content']}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    facts = MemoryClient.from_env().facts(project=args.project, all_projects=args.all)
    if not facts:
        print("(no memories yet)")
    for f in sorted(facts, key=lambda f: (f["scope"], f["category"], f["at"])):
        where = "" if f["scope"] == "personal" else f" {f['scope']}"
        lock = " private" if f["private"] else ""
        print(f"{f['id']}  [{f['category']}{where}{lock}]  {f['content']}  ({f['by']})")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    for c in MemoryClient.from_env().history(args.limit):
        mark = "  (undone)" if c["undone"] else ""
        print(f"{c['id']}  {c['at']}  {c['action']} by {c['by']}{mark}")
        for f in c["before"]:
            print(f"    - {f['content']}")
        for f in c["after"]:
            print(f"    + {f['content']}")
        if c.get("reason"):
            print(f"    ({c['reason']})")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    change = MemoryClient.from_env().undo(args.id)["change"]
    print(f"Undid {args.id}" if change else "Nothing left to undo for that change.")
    return 0


def cmd_tidy(args: argparse.Namespace) -> int:
    report = MemoryClient.from_env(timeout=600).tidy(dry_run=args.dry_run)
    verb = "Would apply" if args.dry_run else "Applied"
    print(f"{verb} {len(report['applied'])} change(s).")
    for p in report["applied"]:
        detail = f"→ {p['content']}" if p["op"] == "merge" else f"({p['reason']})"
        print(f"  {p['op']} {', '.join(p['ids'])} {detail}")
    for p in report["rejected"]:
        print(f"  skipped {p['op']} {', '.join(p['ids'])}: {p['why']}")
    for e in report["errors"]:
        print(f"  error: {e}")
    return 0


def cmd_client_add(args: argparse.Namespace) -> int:
    config = Config.load()
    client = config.add_client(args.name, private=args.private)
    config.save()
    print(f"Added {client.name!r}{' with private access' if client.private else ''}.")
    print(f"  URL:   {config.url}/mcp")
    print(f"  Token: {client.token}")
    return 0


def cmd_client_list(args: argparse.Namespace) -> int:
    for c in Config.load().clients:
        print(f"{c.name}{'  (private access)' if c.private else ''}")
    return 0


def cmd_connect_claude_code(args: argparse.Namespace) -> int:
    from harnessclaw.connect import apply_claude_code, plan_claude_code

    config = Config.load()
    plan = plan_claude_code(config, args.client)
    if args.apply:
        for line in apply_claude_code(config, plan):
            print(f"✓ {line}")
        print("\nStart a new Claude Code session to use it.")
        return 0
    print("This would connect Claude Code to the memory server:\n")
    step = 1
    if plan.created_client:
        print(f"{step}. Add a client {plan.client.name!r} (no private access) to {config.path}")
        step += 1
    print(f"{step}. Register the MCP server (remember / recall / forget tools):")
    print(f"     {shlex.join(plan.mcp_command)}")
    print(f"{step + 1}. Add a UserPromptSubmit hook to {plan.settings_path} so relevant")
    print("   memories are added to every prompt:")
    print("     " + json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [plan.hook]}]}}))
    print("\nRun again with --apply to make these changes.")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    client = MemoryClient.from_env(timeout=900)
    if args.message:
        print(client.chat(args.message))
        return 0
    print("Chatting with your assistant. /help for commands, Ctrl-D to quit.")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text:
            print("\n" + client.chat(text))


def cmd_discord_setup(args: argparse.Namespace) -> int:
    import asyncio
    import getpass

    from harnessclaw.channels.discord_bot import pair_discord

    Config.load()  # fail early if there's no config yet
    print("1. Go to https://discord.com/developers/applications, click New Application,")
    print("   then open Bot and click Reset Token to get the bot token.")
    print("2. Paste the token here.")
    token = getpass.getpass("Bot token: ").strip()
    if not token:
        print("No token given; nothing changed.")
        return 1
    user = asyncio.run(pair_discord(token, print))
    config = Config.load()
    config.discord_bot_token = token
    if user is not None and user["id"] not in config.discord_allowed_users:
        config.discord_allowed_users.append(user["id"])
    config.save()
    print(f"\nSaved to {config.path}.")
    print("Restart `harnessclaw serve` and DM your bot.")
    return 0


def cmd_discord_allow(args: argparse.Namespace) -> int:
    config = Config.load()
    if args.user_id not in config.discord_allowed_users:
        config.discord_allowed_users.append(args.user_id)
        config.save()
    print(f"Allowed {args.user_id}. A running server picks this up right away.")
    return 0


def cmd_discord_disallow(args: argparse.Namespace) -> int:
    config = Config.load()
    config.discord_allowed_users = [u for u in config.discord_allowed_users if u != args.user_id]
    config.save()
    print(f"{args.user_id} can no longer talk to the assistant.")
    return 0


def cmd_hook_claude_code(args: argparse.Namespace) -> int:
    from harnessclaw.connect import claude_code_hook

    out = claude_code_hook(sys.stdin.read(), args.client)
    if out:
        print(out)
    return 0


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
