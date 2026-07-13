from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.core.fault_localizer import (
    FaultLocalizer,
    ScoreWeights,
    evidence_v2_localization_config,
)
from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.weight_search import (
    WeightProfile,
    WeightSearchResult,
    WeightSearchRunner,
    evidence_v2_ablation_profiles,
    generate_evidence_v2_weight_profiles,
)


NON_REGRESSION_METRICS = ("top1", "top3", "top5", "mrr", "map")


@dataclass(frozen=True)
class LocalizationSplitResult:
    split: str
    manifest: str
    case_count: int
    v1: dict[str, Any]
    v2: dict[str, Any]
    metric_deltas: dict[str, float]
    non_regression: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocalizationSplitEvaluationReport:
    schema_version: str
    status: str
    reason: str
    selection_scope: str
    selected_profile: dict[str, Any]
    candidate_profile_count: int
    split_results: dict[str, LocalizationSplitResult]
    ablation_scope: list[str]
    ablation_results: list[dict[str, Any]]
    llm_signal_available: bool
    non_regression_passed: bool
    protocol: dict[str, Any]
    artifacts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["split_results"] = {
            name: result.to_dict() for name, result in self.split_results.items()
        }
        return data


class LocalizationSplitEvaluator:
    def __init__(self, *, use_dynamic_coverage: bool = True) -> None:
        self.use_dynamic_coverage = use_dynamic_coverage

    def run_protocol(
        self,
        protocol_path: str | Path,
        output_dir: str | Path,
    ) -> LocalizationSplitEvaluationReport:
        protocol_file = Path(protocol_path).resolve()
        protocol = json.loads(protocol_file.read_text(encoding="utf-8"))
        output_root = Path(output_dir).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        source_template = _resolve_protocol_path(
            protocol_file,
            str(protocol.get("source_template") or ""),
        )
        materialized_root = output_root / "materialized"
        manifest = BenchmarkMaterializer().materialize_template(
            source_template,
            materialized_root,
            source_cache_dir=output_root / "source_cache",
        )
        split_manifests = _write_split_manifests(
            manifest,
            protocol,
            output_root / "splits",
        )
        return self.evaluate_split_manifests(
            split_manifests,
            protocol=protocol,
            output_dir=output_root,
        )

    def evaluate_split_manifests(
        self,
        split_manifests: dict[str, Path],
        *,
        protocol: dict[str, Any],
        output_dir: str | Path,
        profiles: list[WeightProfile] | None = None,
    ) -> LocalizationSplitEvaluationReport:
        required = {"validation", "test", "blind"}
        missing = sorted(required.difference(split_manifests))
        if missing:
            raise ValueError(f"Missing localization splits: {', '.join(missing)}")
        output_root = Path(output_dir).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        candidate_profiles = list(profiles or generate_evidence_v2_weight_profiles())
        v2_runner = WeightSearchRunner(
            localizer=FaultLocalizer(evidence_v2_localization_config()),
            use_dynamic_coverage=self.use_dynamic_coverage,
        )
        v1_runner = WeightSearchRunner(
            localizer=FaultLocalizer(),
            use_dynamic_coverage=self.use_dynamic_coverage,
        )
        validation_results = v2_runner.search_manifest(
            split_manifests["validation"],
            profiles=candidate_profiles,
        )
        if not validation_results:
            raise ValueError("Validation split produced no weight-search result.")
        selected_result = validation_results[0]
        selected_profile = WeightProfile(
            selected_result.profile,
            selected_result.coverage_weights,
            selected_result.static_only_weights,
        )
        split_results: dict[str, LocalizationSplitResult] = {}
        for split in ("validation", "test", "blind"):
            manifest = split_manifests[split]
            if split == "validation":
                v2_result = selected_result
            else:
                v2_result = v2_runner.search_manifest(
                    manifest,
                    profiles=[selected_profile],
                )[0]
            v1_result = v1_runner.search_manifest(
                manifest,
                profiles=[WeightProfile("legacy_v1", ScoreWeights())],
            )[0]
            split_results[split] = _split_result(
                split=split,
                manifest=manifest,
                v1=v1_result,
                v2=v2_result,
            )

        evaluation_manifest = _combine_split_manifests(
            [split_manifests["test"], split_manifests["blind"]],
            output_root / "splits" / "test_blind_evaluation.json",
        )
        ablation_results = v2_runner.search_manifest(
            evaluation_manifest,
            profiles=evidence_v2_ablation_profiles(
                WeightProfile(
                    "fusion",
                    selected_profile.coverage_weights,
                    selected_profile.static_only_weights,
                )
            ),
        )
        non_regression_passed = all(
            result.non_regression for result in split_results.values()
        )
        status = "pass" if non_regression_passed else "warning"
        reason = (
            "evidence_v2_meets_v1_non_regression_gate"
            if non_regression_passed
            else "evidence_v2_metric_regression_detected"
        )
        artifacts = {
            "json": str(output_root / "localization_split_evaluation.json"),
            "markdown": str(output_root / "localization_split_evaluation.md"),
        }
        report = LocalizationSplitEvaluationReport(
            schema_version="2.0",
            status=status,
            reason=reason,
            selection_scope="validation_only",
            selected_profile=selected_result.to_dict(),
            candidate_profile_count=len(candidate_profiles),
            split_results=split_results,
            ablation_scope=["test", "blind"],
            ablation_results=[result.to_dict() for result in ablation_results],
            llm_signal_available=False,
            non_regression_passed=non_regression_passed,
            protocol=protocol,
            artifacts=artifacts,
        )
        write_localization_split_evaluation(report, output_root)
        return report


def write_localization_split_evaluation(
    report: LocalizationSplitEvaluationReport,
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "localization_split_evaluation.json"
    markdown_path = root / "localization_split_evaluation.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_localization_split_evaluation(report),
        encoding="utf-8",
    )
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


def render_localization_split_evaluation(
    report: LocalizationSplitEvaluationReport,
) -> str:
    selected = report.selected_profile
    lines = [
        "# Evidence V2 Localization Split Evaluation",
        "",
        f"- Status: `{report.status}`",
        f"- Reason: `{report.reason}`",
        f"- Selection Scope: `{report.selection_scope}`",
        f"- Selected Profile: `{selected.get('profile', '')}`",
        f"- Candidate Profiles: {report.candidate_profile_count}",
        f"- V1 Non-Regression: `{str(report.non_regression_passed).lower()}`",
        f"- LLM Signal Available: `{str(report.llm_signal_available).lower()}`",
        "",
        "## V1 vs Evidence V2",
        "",
        "| Split | Cases | V1 Top-1 | V2 Top-1 | V1 Top-3 | V2 Top-3 | V1 Top-5 | V2 Top-5 | V1 MRR | V2 MRR | V1 MAP | V2 MAP | V2 Latency ms | Gate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for split in ("validation", "test", "blind"):
        item = report.split_results[split]
        lines.append(
            "| "
            f"{split} | {item.case_count} | "
            f"{_metric(item.v1, 'top1'):.4f} | {_metric(item.v2, 'top1'):.4f} | "
            f"{_metric(item.v1, 'top3'):.4f} | {_metric(item.v2, 'top3'):.4f} | "
            f"{_metric(item.v1, 'top5'):.4f} | {_metric(item.v2, 'top5'):.4f} | "
            f"{_metric(item.v1, 'mrr'):.4f} | {_metric(item.v2, 'mrr'):.4f} | "
            f"{_metric(item.v1, 'map'):.4f} | {_metric(item.v2, 'map'):.4f} | "
            f"{_metric(item.v2, 'mean_localization_latency_ms'):.4f} | "
            f"{'pass' if item.non_regression else 'regression'} |"
        )
    lines.extend(
        [
            "",
            "## Ablation On Unseen Evaluation Splits",
            "",
            "| Profile | Cases | Top-1 | Top-3 | Top-5 | MRR | MAP | Latency ms |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in report.ablation_results:
        lines.append(
            "| "
            f"{item.get('profile', '')} | {int(item.get('case_count', 0))} | "
            f"{_metric(item, 'top1'):.4f} | {_metric(item, 'top3'):.4f} | "
            f"{_metric(item, 'top5'):.4f} | {_metric(item, 'mrr'):.4f} | "
            f"{_metric(item, 'map'):.4f} | "
            f"{_metric(item, 'mean_localization_latency_ms'):.4f} |"
        )
    lines.extend(
        [
            "",
            "## Evidence Contract",
            "",
            "- Weight selection uses only the validation split.",
            "- Test and blind splits are evaluated after the profile is frozen.",
            "- TestFailureScore requires executed failing-test identifiers.",
            "- StackTraceScore requires dynamically parsed stack frames.",
            "- LLM-only results are not attributed to an LLM when no scorer is configured.",
            "- Sandbox or pytest evidence remains authoritative for repair success.",
        ]
    )
    return "\n".join(lines)


def _write_split_manifests(
    manifest_path: Path,
    protocol: dict[str, Any],
    output_dir: Path,
) -> dict[str, Path]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("Materialized benchmark manifest has no cases list.")
    output_dir.mkdir(parents=True, exist_ok=True)
    split_paths: dict[str, Path] = {}
    seen_groups: set[str] = set()
    split_config = protocol.get("splits", {})
    for split in ("validation", "test", "blind"):
        config = split_config.get(split, {}) if isinstance(split_config, dict) else {}
        groups = {
            str(item) for item in config.get("source_groups", [])
        } if isinstance(config, dict) else set()
        overlap = seen_groups.intersection(groups)
        if overlap:
            raise ValueError(
                f"Source groups overlap across localization splits: {sorted(overlap)}"
            )
        seen_groups.update(groups)
        selected = [
            _absolutize_repo_path(case, manifest_path.parent)
            for case in cases
            if _source_group(case) in groups
        ]
        max_cases = int(config.get("max_cases", 0)) if isinstance(config, dict) else 0
        if max_cases > 0:
            selected = selected[:max_cases]
        if not selected:
            raise ValueError(f"Localization split {split} contains no cases.")
        path = output_dir / f"{split}.json"
        path.write_text(
            json.dumps({"cases": selected}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        split_paths[split] = path
    return split_paths


def _combine_split_manifests(paths: list[Path], output_path: Path) -> Path:
    cases: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases.extend(payload.get("cases", []))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"cases": cases}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


def _split_result(
    *,
    split: str,
    manifest: Path,
    v1: WeightSearchResult,
    v2: WeightSearchResult,
) -> LocalizationSplitResult:
    deltas = {
        metric: round(float(getattr(v2, metric)) - float(getattr(v1, metric)), 4)
        for metric in NON_REGRESSION_METRICS
    }
    return LocalizationSplitResult(
        split=split,
        manifest=str(manifest),
        case_count=v2.case_count,
        v1=v1.to_dict(),
        v2=v2.to_dict(),
        metric_deltas=deltas,
        non_regression=all(delta >= -1e-9 for delta in deltas.values()),
    )


def _source_group(case: Any) -> str:
    if not isinstance(case, dict):
        return "unspecified"
    metadata = case.get("metadata", {})
    if not isinstance(metadata, dict):
        return "unspecified"
    for key in ("upstream", "source_repo", "source_project", "repo", "project"):
        if metadata.get(key):
            return str(metadata[key])
    return "unspecified"


def _absolutize_repo_path(case: Any, manifest_root: Path) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError("Localization benchmark case must be an object.")
    result = dict(case)
    repo_path = Path(str(result.get("repo_path") or ""))
    if not repo_path.is_absolute():
        repo_path = (manifest_root / repo_path).resolve()
    result["repo_path"] = str(repo_path)
    return result


def _resolve_protocol_path(protocol_path: Path, value: str) -> Path:
    if not value:
        raise ValueError("Localization protocol must define source_template.")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (protocol_path.parent / path).resolve()


def _metric(payload: dict[str, Any], name: str) -> float:
    try:
        return float(payload.get(name, 0.0))
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run split-aware Evidence V2 fault-localization evaluation."
    )
    parser.add_argument("protocol", help="Localization split protocol JSON")
    parser.add_argument("output_dir", help="Evaluation output directory")
    parser.add_argument(
        "--no-dynamic-coverage",
        action="store_true",
        help="Use manifest fallback evidence instead of executing trace coverage.",
    )
    parser.add_argument(
        "--require-non-regression",
        action="store_true",
        help="Exit non-zero when any V2 localization metric is below V1.",
    )
    args = parser.parse_args()
    report = LocalizationSplitEvaluator(
        use_dynamic_coverage=not args.no_dynamic_coverage
    ).run_protocol(args.protocol, args.output_dir)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    if args.require_non_regression and not report.non_regression_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
