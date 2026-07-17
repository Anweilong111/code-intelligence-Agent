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
    if _should_route_to_v4_reproduction_environment(raw_argv):
        from code_intelligence_agent.evaluation.v4_reproduction_environment import (
            main as v4_reproduction_environment_main,
        )

        v4_reproduction_environment_main(raw_argv[1:])
        return
    if _should_route_to_v4_real_bug_reproduction(raw_argv):
        from code_intelligence_agent.evaluation.v4_real_bug_reproduction import (
            main as v4_real_bug_reproduction_main,
        )

        v4_real_bug_reproduction_main(raw_argv[1:])
        return
    if _should_route_to_v4_real_bug_benchmark(raw_argv):
        from code_intelligence_agent.evaluation.v4_real_bug_benchmark import (
            main as v4_real_bug_benchmark_main,
        )

        v4_real_bug_benchmark_main(raw_argv[1:])
        return
    if _should_route_to_v4_protocol_audit(raw_argv):
        from code_intelligence_agent.evaluation.v4_experiment_protocol import (
            main as v4_protocol_audit_main,
        )

        v4_protocol_audit_main(raw_argv[1:])
        return
    if _should_route_to_v3_release_evaluation(raw_argv):
        from code_intelligence_agent.evaluation.v3_release_evaluation import (
            main as v3_release_evaluation_main,
        )

        v3_release_evaluation_main(raw_argv[1:])
        return
    if _should_route_to_v3_security_evaluation(raw_argv):
        from code_intelligence_agent.evaluation.v3_security_evaluation import (
            main as v3_security_evaluation_main,
        )

        v3_security_evaluation_main(raw_argv[1:])
        return
    if _should_route_to_v3_memory_evaluation(raw_argv):
        from code_intelligence_agent.evaluation.v3_memory_evaluation import (
            main as v3_memory_evaluation_main,
        )

        v3_memory_evaluation_main(raw_argv[1:])
        return
    if _should_route_to_v3_semantic_evaluation(raw_argv):
        from code_intelligence_agent.evaluation.v3_semantic_evaluation import (
            main as v3_semantic_evaluation_main,
        )

        v3_semantic_evaluation_main(raw_argv[1:])
        return
    if _should_route_to_v3_localization_evaluation(raw_argv):
        from code_intelligence_agent.evaluation.v3_localization_evaluation import (
            main as v3_localization_evaluation_main,
        )

        v3_localization_evaluation_main(raw_argv[1:])
        return
    if _should_route_to_v3_repair_evaluation(raw_argv):
        from code_intelligence_agent.evaluation.v3_repair_evaluation import (
            main as v3_repair_evaluation_main,
        )

        v3_repair_evaluation_main(raw_argv[1:])
        return
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


def _should_route_to_v3_repair_evaluation(argv: list[str]) -> bool:
    return bool(argv and argv[0] in {"v3-repair-eval", "v3-repair-evaluation"})


def _should_route_to_v4_protocol_audit(argv: list[str]) -> bool:
    return bool(
        argv
        and argv[0]
        in {"v4-protocol-audit", "v4-experiment-protocol-audit"}
    )


def _should_route_to_v4_real_bug_benchmark(argv: list[str]) -> bool:
    return bool(
        argv
        and argv[0]
        in {
            "v4-benchmark-catalog",
            "v4-real-bug-benchmark",
        }
    )


def _should_route_to_v4_real_bug_reproduction(argv: list[str]) -> bool:
    return bool(
        argv
        and argv[0]
        in {
            "v4-reproduce",
            "v4-reproduction",
        }
    )


def _should_route_to_v4_reproduction_environment(argv: list[str]) -> bool:
    return bool(
        argv
        and argv[0]
        in {
            "v4-bootstrap-runtime",
            "v4-reproduction-environment",
        }
    )


def _should_route_to_v3_release_evaluation(argv: list[str]) -> bool:
    return bool(
        argv
        and argv[0]
        in {"v3-release-eval", "v3-release-evaluation", "v3-unified-eval"}
    )


def _should_route_to_v3_localization_evaluation(argv: list[str]) -> bool:
    return bool(
        argv
        and argv[0]
        in {"v3-localization-eval", "v3-localization-evaluation"}
    )


def _should_route_to_v3_semantic_evaluation(argv: list[str]) -> bool:
    return bool(
        argv
        and argv[0]
        in {"v3-semantic-eval", "v3-semantic-evaluation"}
    )


def _should_route_to_v3_memory_evaluation(argv: list[str]) -> bool:
    return bool(
        argv
        and argv[0]
        in {"v3-memory-eval", "v3-memory-evaluation"}
    )


def _should_route_to_v3_security_evaluation(argv: list[str]) -> bool:
    return bool(
        argv
        and argv[0]
        in {"v3-security-eval", "v3-security-evaluation"}
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
