from __future__ import annotations

import argparse
import json
from pathlib import Path

from code_intelligence_agent.evaluation.report import render_weight_search_markdown
from code_intelligence_agent.evaluation.weight_search import WeightSearchRunner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tune fault-localization FinalScore weights on a benchmark manifest."
    )
    parser.add_argument("manifest", help="Path to benchmark manifest JSON")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Report format",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of top weight profiles to print.",
    )
    parser.add_argument(
        "--no-dynamic-coverage",
        action="store_true",
        help="Disable pytest trace coverage and use manifest fallback coverage.",
    )
    args = parser.parse_args()

    results = WeightSearchRunner(
        use_dynamic_coverage=not args.no_dynamic_coverage,
    ).search_manifest(Path(args.manifest))
    top_results = results[: max(1, args.top_n)]
    if args.format == "json":
        print(json.dumps([result.to_dict() for result in top_results], indent=2))
    else:
        print(render_weight_search_markdown(top_results, top_n=args.top_n))


if __name__ == "__main__":
    main()
