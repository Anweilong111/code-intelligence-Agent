from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from code_intelligence_agent.agents.session_memory import (
    chat_with_session,
    delete_session_memory,
    inspect_session_memory,
    reset_session_memory,
    resume_session,
)


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == "chat":
        message = args.message or _read_message_from_stdin()
        result = chat_with_session(
            args.session,
            message,
            memory_root=args.memory_root,
            execute=args.execute,
        )
    elif args.command == "chat-ui":
        _run_chat_ui(args)
        return
    elif args.command == "resume":
        result = resume_session(args.session, memory_root=args.memory_root)
    elif args.command == "memory-show":
        result = inspect_session_memory(
            args.session,
            memory_root=args.memory_root,
            query=args.query,
            layer=args.layer,
            top_k=args.top_k,
        )
        print(_render_memory_result(result, fmt=args.format))
        return
    elif args.command == "memory-delete":
        if not args.yes:
            parser.error("memory-delete requires --yes")
        result = delete_session_memory(
            args.session,
            args.memory_id,
            memory_root=args.memory_root,
        )
        print(_render_memory_result(result, fmt=args.format))
        return
    elif args.command == "memory-reset":
        if not args.yes:
            parser.error("memory-reset requires --yes")
        result = reset_session_memory(
            args.session,
            memory_root=args.memory_root,
            scope=args.scope,
        )
        print(_render_memory_result(result, fmt=args.format))
        return
    else:
        parser.error(f"Unsupported session command: {args.command}")
        return
    print(_render_result(result, fmt=args.format))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Continue a code-intelligence Agent session from memory."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat = subparsers.add_parser("chat", help="Run one conversational Agent turn.")
    chat.add_argument("--session", required=True, help="Session id, session dir, or session json.")
    chat.add_argument("--message", help="User message for this turn. Reads stdin if omitted.")
    chat.add_argument("--memory-root", help="Override local Agent memory root.")
    chat.add_argument("--execute", action="store_true", help="Mark prepared actions as executed.")
    chat.add_argument("--format", choices=["json", "markdown"], default="markdown")

    chat_ui = subparsers.add_parser(
        "chat-ui",
        help="Run a continuous terminal chat loop for one Agent session.",
    )
    chat_ui.add_argument("--session", required=True, help="Session id, session dir, or session json.")
    chat_ui.add_argument("--memory-root", help="Override local Agent memory root.")
    chat_ui.add_argument(
        "--execute",
        action="store_true",
        help="Start with execution enabled for command-capable turns.",
    )
    chat_ui.add_argument("--format", choices=["json", "markdown"], default="markdown")

    resume = subparsers.add_parser("resume", help="Resume and summarize a session.")
    resume.add_argument("--session", required=True, help="Session id, session dir, or session json.")
    resume.add_argument("--memory-root", help="Override local Agent memory root.")
    resume.add_argument("--format", choices=["json", "markdown"], default="markdown")

    memory_show = subparsers.add_parser(
        "memory-show",
        help="Inspect traceable Top-k memory records for a session.",
    )
    memory_show.add_argument("--session", required=True)
    memory_show.add_argument("--memory-root")
    memory_show.add_argument("--query", default="")
    memory_show.add_argument("--layer", choices=[
        "",
        "working_memory",
        "session_memory",
        "repo_memory",
        "repair_memory",
        "cross_repo_pattern_memory",
    ], default="")
    memory_show.add_argument("--top-k", type=int, default=8)
    memory_show.add_argument("--format", choices=["json", "markdown"], default="markdown")

    memory_delete = subparsers.add_parser(
        "memory-delete",
        help="Delete one traceable memory record from a session.",
    )
    memory_delete.add_argument("--session", required=True)
    memory_delete.add_argument("--memory-id", required=True)
    memory_delete.add_argument("--memory-root")
    memory_delete.add_argument("--yes", action="store_true")
    memory_delete.add_argument("--format", choices=["json", "markdown"], default="markdown")

    memory_reset = subparsers.add_parser(
        "memory-reset",
        help="Reset session, repair, or all repository-scoped memory.",
    )
    memory_reset.add_argument("--session", required=True)
    memory_reset.add_argument("--scope", choices=["session", "repair", "all"], default="session")
    memory_reset.add_argument("--memory-root")
    memory_reset.add_argument("--yes", action="store_true")
    memory_reset.add_argument("--format", choices=["json", "markdown"], default="markdown")
    return parser


def _read_message_from_stdin() -> str:
    message = sys.stdin.read().strip()
    if not message:
        raise SystemExit("chat requires --message or stdin input")
    return message


def _run_chat_ui(args: argparse.Namespace) -> None:
    execute = bool(args.execute)
    initial = resume_session(args.session, memory_root=args.memory_root)
    session = _dict(initial.get("session"))
    print("# Code Intelligence Agent Chat")
    print()
    print(f"- Session ID: `{_md(session.get('session_id'))}`")
    print(f"- Repo: `{_md(session.get('repo'))}`")
    print(f"- Execute Mode: {'on' if execute else 'off'}")
    print("- Commands: `exit`, `quit`, `:help`, `:resume`, `:execute on`, `:execute off`")
    print()
    while True:
        try:
            raw_message = input("You> ")
        except EOFError:
            print()
            print("[chat-ui] EOF received; exiting.")
            return
        except KeyboardInterrupt:
            print()
            print("[chat-ui] interrupted; exiting.")
            return

        message = raw_message.strip()
        if not message:
            continue
        command = message.lower()
        if command in {"exit", "quit", ":q", ":quit", ":exit"}:
            print("[chat-ui] bye.")
            return
        if command in {"help", ":help"}:
            _print_chat_ui_help()
            continue
        if command == ":resume":
            result = resume_session(args.session, memory_root=args.memory_root)
            print(_render_result(result, fmt=args.format))
            continue
        if command in {":execute on", "/execute on"}:
            execute = True
            print("[chat-ui] Execute Mode: on")
            continue
        if command in {":execute off", "/execute off"}:
            execute = False
            print("[chat-ui] Execute Mode: off")
            continue
        one_shot_execute = False
        if command.startswith(":execute "):
            message = message[len(":execute ") :].strip()
            if not message:
                print("[chat-ui] Usage: :execute <message>")
                continue
            one_shot_execute = True

        result = chat_with_session(
            args.session,
            message,
            memory_root=args.memory_root,
            execute=execute or one_shot_execute,
        )
        print(_render_result(result, fmt=args.format))


def _print_chat_ui_help() -> None:
    print(
        "\n".join(
            [
                "# Chat UI Commands",
                "",
                "- `exit` / `quit`: leave the terminal chat loop.",
                "- `:resume`: summarize the current session memory.",
                "- `:execute on`: execute command-capable future turns.",
                "- `:execute off`: return to planning-only turns.",
                "- `:execute <message>`: execute only this one turn.",
                "",
            ]
        )
    )


def _render_result(result: dict[str, Any], *, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(result, indent=2, ensure_ascii=False)
    session = _dict(result.get("session"))
    intent = _dict(result.get("intent"))
    decision = _dict(result.get("decision"))
    evidence = _dict(result.get("memory_usage_evidence"))
    lines = [
        "# Agent Session Turn",
        "",
        f"- Status: `{_md(result.get('status'))}`",
        f"- Session ID: `{_md(session.get('session_id'))}`",
        f"- Repo: `{_md(session.get('repo'))}`",
        f"- Intent: `{_md(intent.get('intent'))}`",
        f"- Action: `{_md(decision.get('action_id'))}`",
        f"- Answer: {_md(result.get('answer'))}",
        f"- Next Action: {_md(decision.get('next_action') or 'none')}",
        f"- Session Report: `{_md(session.get('session_report_path'))}`",
        "",
        "## Memory Usage Evidence",
        "",
        f"- Repo Profile Loaded: {str(bool(evidence.get('repo_profile_loaded'))).lower()}",
        f"- Top-k Loaded: {_int(evidence.get('topk_loaded', 0))}",
        f"- Test Result Loaded: {str(bool(evidence.get('test_result_loaded'))).lower()}",
        f"- Patch Attempt Memory Loaded: {_int(evidence.get('patch_attempt_memory_loaded', 0))}",
        f"- Blocker Memory Loaded: {_int(evidence.get('blocker_memory_loaded', 0))}",
        f"- Prior Turn Count: {_int(evidence.get('prior_turn_count', 0))}",
        "",
    ]
    command = str(decision.get("command") or "")
    if command:
        lines.extend(["## Prepared Command", "", f"`{_md(command)}`", ""])
    environment = _dict(decision.get("environment"))
    if environment:
        lines.extend(["## Prepared Environment", ""])
        for key, value in environment.items():
            lines.append(f"- `{_md(key)}` = `{_md(value)}`")
        lines.append("")
    return "\n".join(lines)


def _render_memory_result(result: dict[str, Any], *, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(result, indent=2, ensure_ascii=False)
    retrieval = _dict(result.get("retrieval"))
    lines = [
        "# Agent Memory Operation",
        "",
        f"- Status: `{_md(result.get('status'))}`",
        f"- Reason: `{_md(result.get('reason'))}`",
    ]
    if result.get("scope"):
        lines.append(f"- Scope: `{_md(result.get('scope'))}`")
    if result.get("memory_id"):
        lines.append(f"- Memory ID: `{_md(result.get('memory_id'))}`")
    if retrieval:
        lines.extend(
            [
                f"- Algorithm: `{_md(retrieval.get('algorithm'))}`",
                f"- Selected: {_int(retrieval.get('selected_count', 0))}/{_int(retrieval.get('candidate_count', 0))}",
                "",
                "| Memory ID | Layer | Kind | Score | Source | Evidence |",
                "| --- | --- | --- | ---: | --- | --- |",
            ]
        )
        for item_value in retrieval.get("records", []):
            item = _dict(item_value)
            lines.append(
                "| "
                f"`{_md(item.get('memory_id'))}` | "
                f"`{_md(item.get('layer'))}` | "
                f"`{_md(item.get('kind'))}` | "
                f"{float(item.get('retrieval_score') or 0.0):.4f} | "
                f"{_md(item.get('source'))} | {_md(item.get('evidence_path'))} |"
            )
    return "\n".join(lines) + "\n"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
