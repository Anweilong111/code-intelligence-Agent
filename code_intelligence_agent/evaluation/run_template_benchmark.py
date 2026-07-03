from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from code_intelligence_agent.agents.llm_fault_scorer import build_llm_fault_scorer
from code_intelligence_agent.agents.patch_generator_factory import build_patch_generator
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator
from code_intelligence_agent.evaluation.llm_judge import build_judge
from code_intelligence_agent.evaluation.report import render_benchmark_markdown
from code_intelligence_agent.search.patch_judge import build_patch_judge


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a benchmark template, materialize it, validate the generated "
            "manifest, and run benchmark evaluation."
        )
    )
    parser.add_argument("template", help="Benchmark template JSON")
    parser.add_argument("output_dir", help="Output directory for generated benchmark")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
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
    parser.add_argument(
        "--source-cache-dir",
        help=(
            "Optional shared raw-source cache directory. Defaults to "
            "<output_dir>/.source_cache."
        ),
    )
    args = parser.parse_args()

    result = run_template_benchmark(
        template_path=Path(args.template),
        output_dir=Path(args.output_dir),
        patch_mode=args.patch_mode,
        judge_mode=args.judge_mode,
        patch_judge_mode=args.patch_judge_mode,
        llm_score_mode=args.llm_score_mode,
        use_dynamic_coverage=not args.no_dynamic_coverage,
        source_cache_dir=Path(args.source_cache_dir)
        if args.source_cache_dir
        else None,
    )
    if args.format == "json":
        print(json.dumps(_json_ready(result), indent=2, ensure_ascii=False))
    else:
        print(render_benchmark_markdown(result["benchmark_report"]))


def run_template_benchmark(
    template_path: Path,
    output_dir: Path,
    patch_mode: str = "rule",
    judge_mode: str = "none",
    patch_judge_mode: str = "none",
    llm_score_mode: str = "none",
    use_dynamic_coverage: bool = True,
    source_cache_dir: Path | None = None,
    repository_test_evidence: dict | None = None,
) -> dict:
    validator = BenchmarkValidator()
    template_validation = validator.validate_template(template_path)
    if not template_validation.is_valid:
        raise ValueError(_validation_error("template", template_validation.errors))

    manifest_path = BenchmarkMaterializer().materialize_template(
        template_path,
        output_dir,
        source_cache_dir=source_cache_dir,
    )
    _annotate_manifest_with_repository_test_evidence(
        manifest_path,
        repository_test_evidence or {},
    )
    manifest_validation = validator.validate_manifest(manifest_path)
    if not manifest_validation.is_valid:
        raise ValueError(_validation_error("manifest", manifest_validation.errors))

    benchmark_report = BenchmarkRunner(
        localizer=FaultLocalizer(
            llm_scorer=build_llm_fault_scorer(llm_score_mode)
        ),
        patch_generator=build_patch_generator(patch_mode),
        judge=build_judge(judge_mode),
        patch_judge=build_patch_judge(patch_judge_mode),
        use_dynamic_coverage=use_dynamic_coverage,
    ).run_manifest(manifest_path)
    repository_test_evidence = _manifest_repository_test_evidence(manifest_path)
    if repository_test_evidence:
        benchmark_report = replace(
            benchmark_report,
            repository_test_evidence=repository_test_evidence,
        )
    report_artifacts = _write_report_artifacts(output_dir, benchmark_report)
    return {
        "template_validation": template_validation.to_dict(),
        "manifest_path": str(manifest_path),
        "manifest_validation": manifest_validation.to_dict(),
        "report_artifacts": report_artifacts,
        "benchmark_report": benchmark_report,
    }


def _validation_error(kind: str, errors) -> str:
    details = "; ".join(
        f"{issue.location}: {issue.message}" for issue in errors
    )
    return f"Invalid benchmark {kind}: {details}"


def _json_ready(result: dict) -> dict:
    return {
        "template_validation": result["template_validation"],
        "manifest_path": result["manifest_path"],
        "manifest_validation": result["manifest_validation"],
        "report_artifacts": result["report_artifacts"],
        "benchmark_report": result["benchmark_report"].to_dict(),
    }


def _manifest_repository_test_evidence(manifest_path: str | Path) -> dict:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    evidence = payload.get("repository_test_evidence")
    return evidence if isinstance(evidence, dict) else {}


def _annotate_manifest_with_repository_test_evidence(
    manifest_path: str | Path,
    evidence: dict,
) -> None:
    if not evidence:
        return
    path = Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return
    payload["repository_test_evidence"] = evidence
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_report_artifacts(output_dir: Path, benchmark_report) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "benchmark_report.json"
    markdown_path = output_dir / "benchmark_report.md"
    json_path.write_text(
        json.dumps(benchmark_report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_benchmark_markdown(benchmark_report),
        encoding="utf-8",
    )
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


if __name__ == "__main__":
    main()
