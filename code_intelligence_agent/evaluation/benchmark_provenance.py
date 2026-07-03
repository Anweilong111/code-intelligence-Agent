from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DuplicateSignatureGroup:
    signature_hash: str
    case_count: int
    cases: list[str]
    source_group: str
    source_path: str
    ground_truth: list[str]
    expected_rules: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkProvenanceAudit:
    case_count: int
    source_group_count: int
    source_ref_count: int
    source_sha256_present_count: int
    stable_ref_count: int
    floating_ref_count: int
    case_provenance_coverage: float
    source_sha256_coverage: float
    stable_ref_coverage: float
    license_coverage: float
    materialized_mutation_coverage: float
    duplicate_case_name_count: int
    duplicate_signature_count: int
    duplicate_signature_case_count: int
    max_source_group_case_share: float
    max_source_file_case_share: float
    leakage_risk_score: float
    risk_level: str
    missing_provenance_cases: list[str]
    missing_sha256_sources: list[str]
    floating_ref_sources: list[str]
    duplicate_signatures: list[DuplicateSignatureGroup]
    source_groups: dict[str, int]
    top_source_files: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["duplicate_signatures"] = [
            item.to_dict() for item in self.duplicate_signatures
        ]
        return data


def benchmark_provenance_summary(
    report_or_cases: Any,
    template_path: str | Path | None = None,
) -> dict[str, Any]:
    return benchmark_provenance_audit(
        report_or_cases,
        template_path=template_path,
    ).to_dict()


def benchmark_provenance_audit(
    report_or_cases: Any,
    template_path: str | Path | None = None,
) -> BenchmarkProvenanceAudit:
    cases = _cases(report_or_cases)
    template_sources = _template_sources_by_case(template_path)
    case_count = len(cases)
    source_groups: dict[str, int] = {}
    source_files: dict[str, set[str]] = {}
    source_ref_count = 0
    source_sha_count = 0
    stable_ref_count = 0
    floating_ref_count = 0
    missing_sha_sources: list[str] = []
    floating_ref_sources: list[str] = []
    missing_provenance_cases: list[str] = []
    mutation_expected_count = 0
    mutation_present_count = 0
    license_count = 0
    complete_provenance_count = 0
    names: dict[str, int] = {}
    signatures: dict[str, list[Any]] = {}

    for case in cases:
        case_name = _case_name(case)
        metadata = _metadata(case)
        names[case_name] = names.get(case_name, 0) + 1
        source_group = _source_group(case)
        source_groups[source_group] = source_groups.get(source_group, 0) + 1
        if _has_complete_case_provenance(metadata):
            complete_provenance_count += 1
        else:
            missing_provenance_cases.append(case_name)
        if metadata.get("license"):
            license_count += 1
        if _expects_materialized_mutation(metadata):
            mutation_expected_count += 1
            if _has_materialized_mutation(metadata):
                mutation_present_count += 1
        refs = _source_refs(case, template_sources.get(case_name, []))
        for ref in refs:
            source_ref_count += 1
            key = _source_file_key(ref, metadata)
            source_files.setdefault(key, set()).add(case_name)
            if ref.get("sha256"):
                source_sha_count += 1
            else:
                missing_sha_sources.append(f"{case_name}:{key}")
            ref_name = str(ref.get("ref") or metadata.get("upstream_ref") or "")
            if _is_stable_ref(ref_name):
                stable_ref_count += 1
            else:
                floating_ref_count += 1
                floating_ref_sources.append(f"{case_name}:{key}")
        signature = _signature(case)
        signatures.setdefault(signature, []).append(case)

    duplicate_groups = _duplicate_signature_groups(signatures)
    duplicate_case_count = sum(item.case_count for item in duplicate_groups)
    duplicate_case_names = sum(max(0, count - 1) for count in names.values())
    max_group_share = _max_share(source_groups.values(), case_count)
    max_file_share = _max_share((len(cases) for cases in source_files.values()), case_count)
    case_provenance_coverage = _ratio(complete_provenance_count, case_count)
    source_sha_coverage = _ratio(source_sha_count, source_ref_count)
    stable_ref_coverage = _ratio(stable_ref_count, source_ref_count)
    mutation_coverage = (
        _ratio(mutation_present_count, mutation_expected_count)
        if mutation_expected_count
        else 1.0
    )
    risk_score = _leakage_risk_score(
        case_provenance_coverage=case_provenance_coverage,
        source_sha256_coverage=source_sha_coverage,
        stable_ref_coverage=stable_ref_coverage,
        has_sha_sources=source_sha_count > 0,
        materialized_mutation_coverage=mutation_coverage,
        duplicate_signature_case_count=duplicate_case_count,
        case_count=case_count,
        max_source_file_case_share=max_file_share,
    )
    return BenchmarkProvenanceAudit(
        case_count=case_count,
        source_group_count=len(source_groups),
        source_ref_count=source_ref_count,
        source_sha256_present_count=source_sha_count,
        stable_ref_count=stable_ref_count,
        floating_ref_count=floating_ref_count,
        case_provenance_coverage=case_provenance_coverage,
        source_sha256_coverage=source_sha_coverage,
        stable_ref_coverage=stable_ref_coverage,
        license_coverage=_ratio(license_count, case_count),
        materialized_mutation_coverage=mutation_coverage,
        duplicate_case_name_count=duplicate_case_names,
        duplicate_signature_count=len(duplicate_groups),
        duplicate_signature_case_count=duplicate_case_count,
        max_source_group_case_share=max_group_share,
        max_source_file_case_share=max_file_share,
        leakage_risk_score=risk_score,
        risk_level=_risk_level(risk_score),
        missing_provenance_cases=missing_provenance_cases[:20],
        missing_sha256_sources=missing_sha_sources[:20],
        floating_ref_sources=floating_ref_sources[:20],
        duplicate_signatures=duplicate_groups[:20],
        source_groups=dict(sorted(source_groups.items())),
        top_source_files=_top_source_files(source_files, limit=10),
    )


def _cases(report_or_cases: Any) -> list[Any]:
    if isinstance(report_or_cases, list):
        return report_or_cases
    if isinstance(report_or_cases, dict):
        return list(report_or_cases.get("cases", []))
    return list(getattr(report_or_cases, "cases", []))


def _template_sources_by_case(
    template_path: str | Path | None,
) -> dict[str, list[dict[str, Any]]]:
    if template_path is None:
        return {}
    path = Path(template_path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, list[dict[str, Any]]] = {}
    for case in data.get("cases", []):
        if not isinstance(case, dict):
            continue
        name = case.get("name")
        if not isinstance(name, str) or not name:
            continue
        sources = case.get("sources", [])
        mapping[name] = [
            source for source in sources if isinstance(source, dict)
        ]
    return mapping


def _source_refs(
    case: Any,
    template_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    metadata = _metadata(case)
    source_files = metadata.get("source_files")
    if isinstance(source_files, list) and source_files:
        return [item for item in source_files if isinstance(item, dict)]
    case_sources = _get(case, "sources", [])
    if isinstance(case_sources, list) and case_sources:
        return [item for item in case_sources if isinstance(item, dict)]
    if template_sources:
        return template_sources
    if metadata.get("upstream") or metadata.get("upstream_path"):
        return [
            {
                "owner": str(metadata.get("upstream", "")).split("/")[0],
                "repo": "/".join(str(metadata.get("upstream", "")).split("/")[1:]),
                "ref": metadata.get("upstream_ref", ""),
                "source_path": metadata.get("upstream_path", ""),
                "target_path": metadata.get("upstream_path", ""),
            }
        ]
    return []


def _source_file_key(ref: dict[str, Any], metadata: dict[str, Any]) -> str:
    owner = str(ref.get("owner") or "").strip()
    repo = str(ref.get("repo") or "").strip()
    upstream = "/".join(item for item in [owner, repo] if item)
    if not upstream:
        upstream = str(metadata.get("upstream") or "unspecified")
    ref_name = str(ref.get("ref") or metadata.get("upstream_ref") or "")
    path = str(ref.get("source_path") or ref.get("target_path") or metadata.get("upstream_path") or "")
    return f"{upstream}@{ref_name}:{path}"


def _is_stable_ref(ref_name: str) -> bool:
    ref = ref_name.strip()
    if not ref:
        return False
    if ref.startswith("refs/tags/"):
        return True
    if ref.startswith("refs/heads/"):
        return False
    lowered = ref.lower()
    if lowered in {
        "head",
        "latest",
        "main",
        "master",
        "develop",
        "development",
        "dev",
        "trunk",
        "stable",
    }:
        return False
    if re.fullmatch(r"[0-9a-fA-F]{40}", ref):
        return True
    return bool(re.fullmatch(r"v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z._-]+)?", ref))


def _signature(case: Any) -> str:
    metadata = _metadata(case)
    mutations = []
    for mutation in metadata.get("materialized_mutations", []):
        if not isinstance(mutation, dict):
            continue
        mutations.append(
            (
                mutation.get("target_path", ""),
                mutation.get("find", ""),
                mutation.get("replace", ""),
            )
        )
    payload = {
        "source_group": _source_group(case),
        "source_path": metadata.get("upstream_path", ""),
        "ground_truth": sorted(str(item) for item in _case_ground_truth(case)),
        "expected_rules": sorted(
            str(item) for item in _case_expected_rules(case)
        ),
        "benchmark_shape": _benchmark_shape(metadata),
        "mutations": sorted(mutations),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _benchmark_shape(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "cross_file_trace",
        "multi_source_raw",
        "bugs_per_case",
        "search_pressure",
        "wrapper_function",
        "wrapper_hops",
        "expected_min_patch_candidates",
        "expected_multi_patch_bundle_size",
        "hard_case_target_signal",
    )
    shape = {key: metadata.get(key) for key in keys if key in metadata}
    target_signals = metadata.get("hard_case_target_signals")
    if isinstance(target_signals, list):
        shape["hard_case_target_signals"] = sorted(str(item) for item in target_signals)
    return shape


def _duplicate_signature_groups(
    signatures: dict[str, list[Any]],
) -> list[DuplicateSignatureGroup]:
    groups: list[DuplicateSignatureGroup] = []
    for signature_hash, cases in signatures.items():
        if len(cases) <= 1:
            continue
        first = cases[0]
        metadata = _metadata(first)
        groups.append(
            DuplicateSignatureGroup(
                signature_hash=signature_hash,
                case_count=len(cases),
                cases=[_case_name(case) for case in cases],
                source_group=_source_group(first),
                source_path=str(metadata.get("upstream_path", "")),
                ground_truth=sorted(str(item) for item in _case_ground_truth(first)),
                expected_rules=sorted(str(item) for item in _case_expected_rules(first)),
            )
        )
    return sorted(groups, key=lambda item: (-item.case_count, item.signature_hash))


def _top_source_files(
    source_files: dict[str, set[str]],
    limit: int,
) -> list[dict[str, Any]]:
    rows = [
        {
            "source_file": key,
            "case_count": len(cases),
            "cases": sorted(cases)[:10],
        }
        for key, cases in source_files.items()
    ]
    return sorted(rows, key=lambda row: (-int(row["case_count"]), row["source_file"]))[
        :limit
    ]


def _has_complete_case_provenance(metadata: dict[str, Any]) -> bool:
    return all(
        bool(metadata.get(key))
        for key in ("source", "upstream", "upstream_ref", "upstream_path")
    )


def _expects_materialized_mutation(metadata: dict[str, Any]) -> bool:
    source = str(metadata.get("source", ""))
    return source.startswith("github_raw")


def _has_materialized_mutation(metadata: dict[str, Any]) -> bool:
    mutations = metadata.get("materialized_mutations")
    return isinstance(mutations, list) and bool(mutations)


def _source_group(case: Any) -> str:
    metadata = _metadata(case)
    for key in ("upstream", "source_project", "source_repo", "repo", "project"):
        value = metadata.get(key)
        if value:
            return str(value)
    name = _case_name(case)
    if name.startswith("cpython_"):
        return "python/cpython"
    if name.startswith("thealgorithms_"):
        return "TheAlgorithms/Python"
    if name.startswith("pluggy_"):
        return "pytest-dev/pluggy"
    if name.startswith("click_"):
        return "pallets/click"
    return "unspecified"


def _metadata(case: Any) -> dict[str, Any]:
    metadata = _get(case, "metadata", {})
    if isinstance(metadata, dict) and metadata:
        return metadata
    benchmark = _get(case, "benchmark", {})
    if isinstance(benchmark, dict):
        benchmark_metadata = benchmark.get("metadata", {})
        if isinstance(benchmark_metadata, dict):
            return benchmark_metadata
    return {}


def _case_ground_truth(case: Any) -> list[Any]:
    ground_truth = _get(case, "ground_truth", None)
    if isinstance(ground_truth, list):
        return ground_truth
    benchmark = _get(case, "benchmark", {})
    if isinstance(benchmark, dict):
        buggy_functions = benchmark.get("buggy_functions")
        if isinstance(buggy_functions, list):
            return buggy_functions
    return []


def _case_expected_rules(case: Any) -> list[Any]:
    expected_rules = _get(case, "expected_rule_ids", None)
    if isinstance(expected_rules, list):
        return expected_rules
    benchmark = _get(case, "benchmark", {})
    if isinstance(benchmark, dict):
        benchmark_rules = benchmark.get("expected_rule_ids")
        if isinstance(benchmark_rules, list):
            return benchmark_rules
    return []


def _case_name(case: Any) -> str:
    return str(_get(case, "case_name", _get(case, "name", "")))


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _max_share(values: Any, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(max((int(value) / total for value in values), default=0.0), 4)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _leakage_risk_score(
    *,
    case_provenance_coverage: float,
    source_sha256_coverage: float,
    stable_ref_coverage: float,
    has_sha_sources: bool,
    materialized_mutation_coverage: float,
    duplicate_signature_case_count: int,
    case_count: int,
    max_source_file_case_share: float,
) -> float:
    duplicate_rate = duplicate_signature_case_count / case_count if case_count else 0.0
    sha_gap = 1.0 - source_sha256_coverage if has_sha_sources else 0.0
    stable_ref_gap = 1.0 - stable_ref_coverage if case_count else 0.0
    concentration_excess = max(0.0, max_source_file_case_share - 0.50) / 0.50
    score = (
        0.20 * (1.0 - case_provenance_coverage)
        + 0.20 * (1.0 - materialized_mutation_coverage)
        + 0.15 * sha_gap
        + 0.10 * stable_ref_gap
        + 0.25 * duplicate_rate
        + 0.10 * min(1.0, concentration_excess)
    )
    return round(score, 4)


def _risk_level(score: float) -> str:
    if score >= 0.35:
        return "high"
    if score >= 0.15:
        return "medium"
    return "low"
