from __future__ import annotations

import argparse
import json
from pathlib import Path

from code_intelligence_agent.agents.llm_fault_scorer import build_llm_fault_scorer
from code_intelligence_agent.agents.patch_generator_factory import build_patch_generator
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.llm_judge import build_judge
from code_intelligence_agent.evaluation.report import render_benchmark_markdown
from code_intelligence_agent.search.patch_judge import build_patch_judge


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CIA benchmark manifest.")
    parser.add_argument("manifest", help="Path to benchmark manifest JSON")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Report format",
    )
    parser.add_argument(
        "--patch-mode",
        choices=["rule", "llm"],
        default="rule",
        help="Patch generation mode",
    )
    parser.add_argument(
        "--no-dynamic-coverage",
        action="store_true",
        help="Disable pytest trace coverage and use manifest fallback coverage.",
    )
    parser.add_argument(
        "--judge-mode",
        choices=["none", "llm"],
        default="none",
        help="Optional LLM-as-judge mode. The llm mode defaults to DeepSeek.",
    )
    parser.add_argument(
        "--patch-judge-mode",
        choices=["none", "llm"],
        default="none",
        help=(
            "Optional patch-level LLM judge used inside BeamSearch scoring. "
            "The llm mode defaults to DeepSeek."
        ),
    )
    parser.add_argument(
        "--llm-score-mode",
        choices=["none", "llm"],
        default="none",
        help="Optional LLMScore signal for fault localization.",
    )
    args = parser.parse_args()

    report = BenchmarkRunner(
        localizer=FaultLocalizer(
            llm_scorer=build_llm_fault_scorer(args.llm_score_mode)
        ),
        patch_generator=build_patch_generator(args.patch_mode),
        judge=build_judge(args.judge_mode),
        patch_judge=build_patch_judge(args.patch_judge_mode),
        use_dynamic_coverage=not args.no_dynamic_coverage,
    ).run_manifest(Path(args.manifest))
    if args.format == "markdown":
        print(render_benchmark_markdown(report))
    else:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
