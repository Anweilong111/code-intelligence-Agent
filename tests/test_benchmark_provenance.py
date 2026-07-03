import json
import tempfile
from types import SimpleNamespace
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_provenance import (
    benchmark_provenance_audit,
    benchmark_provenance_summary,
)


def test_benchmark_provenance_audit_scores_complete_unique_cases():
    cases = [
        _case(
            "case_a",
            function="target_a",
            mutation_replace="bug_a",
            source_path="src/mod_a.py",
        ),
        _case(
            "case_b",
            function="target_b",
            mutation_replace="bug_b",
            source_path="src/mod_b.py",
        ),
    ]

    audit = benchmark_provenance_audit(cases)

    assert audit.case_count == 2
    assert audit.source_group_count == 1
    assert audit.source_ref_count == 2
    assert audit.source_sha256_coverage == 1.0
    assert audit.stable_ref_coverage == 1.0
    assert audit.case_provenance_coverage == 1.0
    assert audit.license_coverage == 1.0
    assert audit.materialized_mutation_coverage == 1.0
    assert audit.duplicate_signature_count == 0
    assert audit.leakage_risk_score == 0.0
    assert audit.risk_level == "low"


def test_benchmark_provenance_audit_detects_duplicate_bug_signatures():
    cases = [
        _case("case_a", function="target", mutation_replace="same_bug"),
        _case("case_b", function="target", mutation_replace="same_bug"),
    ]

    summary = benchmark_provenance_summary(cases)

    assert summary["duplicate_signature_count"] == 1
    assert summary["duplicate_signature_case_count"] == 2
    assert summary["duplicate_signatures"][0]["cases"] == ["case_a", "case_b"]
    assert summary["leakage_risk_score"] > 0.0


def test_benchmark_provenance_audit_can_read_template_source_sha256():
    with tempfile.TemporaryDirectory() as tmp_dir:
        template = Path(tmp_dir) / "template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "case_from_template",
                            "repo_path": "repo",
                            "sources": [
                                {
                                    "owner": "owner",
                                    "repo": "repo",
                                    "ref": "main",
                                    "source_path": "src/mod.py",
                                    "target_path": "mod.py",
                                    "sha256": "a" * 64,
                                }
                            ],
                            "benchmark": {
                                "metadata": {
                                    "source": "github_raw_mutation",
                                    "upstream": "owner/repo",
                                    "upstream_ref": "main",
                                    "upstream_path": "src/mod.py",
                                    "license": "MIT",
                                }
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        case = _case(
            "case_from_template",
            function="target",
            mutation_replace="bug",
            include_source_files=False,
        )

        audit = benchmark_provenance_audit(case.to_dict()["cases"], template_path=template)

        assert audit.source_ref_count == 1
        assert audit.source_sha256_present_count == 1
        assert audit.source_sha256_coverage == 1.0
        assert audit.stable_ref_coverage == 0.0
        assert audit.floating_ref_sources == [
            "case_from_template:owner/repo@main:src/mod.py"
        ]


def test_benchmark_provenance_audit_reads_inline_template_sources():
    case = {
        "name": "inline_template_case",
        "sources": [
            {
                "owner": "owner",
                "repo": "repo",
                "ref": "v1.2.3",
                "source_path": "src/mod.py",
                "target_path": "mod.py",
                "sha256": "b" * 64,
                "license": "MIT",
            }
        ],
        "benchmark": {
            "buggy_functions": ["target"],
            "expected_rule_ids": ["rule_a"],
            "metadata": {
                "source": "github_raw_recipe_generation",
                "upstream": "owner/repo",
                "upstream_ref": "v1.2.3",
                "upstream_path": "src/mod.py",
                "license": "MIT",
            },
        },
    }

    audit = benchmark_provenance_audit([case])

    assert audit.source_ref_count == 1
    assert audit.source_sha256_coverage == 1.0
    assert audit.stable_ref_coverage == 1.0
    assert audit.license_coverage == 1.0


def test_benchmark_provenance_audit_penalizes_floating_branch_refs():
    cases = [
        _case(
            "case_main",
            function="target",
            mutation_replace="bug",
            ref="main",
        ),
        _case(
            "case_tag",
            function="target_other",
            mutation_replace="other_bug",
            ref="v1.2.3",
        ),
    ]

    audit = benchmark_provenance_audit(cases)

    assert audit.source_ref_count == 2
    assert audit.stable_ref_count == 1
    assert audit.floating_ref_count == 1
    assert audit.stable_ref_coverage == 0.5
    assert audit.floating_ref_sources == ["case_main:owner/repo@main:src/mod.py"]
    assert audit.leakage_risk_score > 0.0


def _case(
    name: str,
    *,
    function: str,
    mutation_replace: str,
    source_path: str = "src/mod.py",
    include_source_files: bool = True,
    ref: str = "v1.0.0",
) -> SimpleNamespace:
    metadata = {
        "source": "github_raw_mutation",
        "upstream": "owner/repo",
        "upstream_ref": ref,
        "upstream_path": source_path,
        "license": "MIT",
        "materialized_mutations": [
            {
                "target_path": "mod.py",
                "find": "return ok",
                "replace": mutation_replace,
            }
        ],
    }
    if include_source_files:
        metadata["source_files"] = [
            {
                "owner": "owner",
                "repo": "repo",
                "ref": ref,
                "source_path": source_path,
                "target_path": "mod.py",
                "sha256": "a" * 64,
            }
        ]
    case = {
        "case_name": name,
        "metadata": metadata,
        "ground_truth": [function],
        "expected_rule_ids": ["rule_a"],
    }
    return SimpleNamespace(
        **case,
        to_dict=lambda: {"cases": [case]},
    )
