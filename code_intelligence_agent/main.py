from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.agents.patch_generator_factory import build_patch_generator
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser


def analyze_path(path: str | Path, patch_mode: str = "rule") -> dict:
    parser = RepoParser()
    parsed = parser.parse(path)
    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    detector = RuleBasedBugDetector()
    findings = detector.detect(parsed.functions)
    suspicious = detector.rank(parsed.functions, findings, call_graph)
    localized = FaultLocalizer().rank(program_graph, findings)
    patch_candidates = build_patch_generator(patch_mode).generate(
        path,
        parsed.functions,
        localized,
    )
    return {
        "repo": parsed.to_dict(),
        "call_graph": call_graph.to_dict(),
        "program_graph": program_graph.to_dict(),
        "findings": [finding.to_dict() for finding in findings],
        "suspicious_functions": [item.to_dict() for item in suspicious],
        "fault_localization": [item.to_dict() for item in localized],
        "patch_candidates": [item.to_dict() for item in patch_candidates],
    }


def main(argv: list[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if _should_route_to_session_agent(raw_argv):
        from code_intelligence_agent.agents.session_cli import main as session_main

        session_main(raw_argv)
        return
    if _should_route_to_repo_agent(raw_argv):
        from code_intelligence_agent.evaluation.github_repo_intelligence import (
            main as repo_agent_main,
        )

        repo_agent_main(_repo_agent_argv(raw_argv))
        return

    cli = argparse.ArgumentParser(
        description=(
            "Static code analysis for a local Python path. Use --agent or the "
            "`agent` subcommand for arbitrary GitHub repository analysis."
        )
    )
    cli.add_argument("path", help="Python file or repository path")
    cli.add_argument(
        "--patch-mode",
        choices=["rule", "llm"],
        default="rule",
        help="Patch generation mode",
    )
    args = cli.parse_args(raw_argv)
    print(
        json.dumps(
            analyze_path(args.path, patch_mode=args.patch_mode),
            indent=2,
            ensure_ascii=False,
        )
    )


def _should_route_to_repo_agent(argv: list[str]) -> bool:
    if argv and argv[0] in {"agent", "repo-agent", "github-agent"}:
        return True
    return any(
        item == "--agent"
        or item.startswith("--execution-profile")
        or item.startswith("--repository-test-")
        or item.startswith("--auto-controller")
        for item in argv
    )


def _should_route_to_session_agent(argv: list[str]) -> bool:
    return bool(
        argv
        and argv[0]
        in {
            "chat",
            "chat-ui",
            "resume",
            "memory-show",
            "memory-delete",
            "memory-reset",
        }
    )


def _repo_agent_argv(argv: list[str]) -> list[str]:
    if argv and argv[0] in {"agent", "repo-agent", "github-agent"}:
        routed = list(argv[1:])
        if "--agent" not in routed:
            routed.append("--agent")
        return routed
    return argv


if __name__ == "__main__":
    main()
