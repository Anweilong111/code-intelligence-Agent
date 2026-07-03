from __future__ import annotations

import argparse
import json
from pathlib import Path

from code_intelligence_agent.agents.llm_fault_scorer import build_llm_fault_scorer
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.evaluation.ablation import BenchmarkAblationRunner
from code_intelligence_agent.evaluation.report import render_ablation_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CIA benchmark ablation study.")
    parser.add_argument("manifest", help="Path to benchmark manifest JSON")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Report format",
    )
    parser.add_argument(
        "--llm-score-mode",
        choices=["none", "llm"],
        default="none",
        help="Optional LLMScore signal for fault localization.",
    )
    parser.add_argument(
        "--no-dynamic-coverage",
        action="store_true",
        help="Disable pytest trace coverage and use manifest fallback coverage.",
    )
    args = parser.parse_args()

    results = BenchmarkAblationRunner(
        localizer=FaultLocalizer(
            llm_scorer=build_llm_fault_scorer(args.llm_score_mode)
        ),
        use_dynamic_coverage=not args.no_dynamic_coverage,
    ).run_manifest(Path(args.manifest))
    if args.format == "json":
        print(json.dumps([result.__dict__ for result in results], indent=2))
    else:
        print(render_ablation_markdown(results))


if __name__ == "__main__":
    main()
