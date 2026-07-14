import json
from pathlib import Path

from code_intelligence_agent.core.fault_localizer import ScoreWeights
from code_intelligence_agent.evaluation.localization_split_evaluation import (
    LocalizationSplitEvaluator,
)
from code_intelligence_agent.evaluation.weight_search import (
    WeightProfile,
    evidence_v2_ablation_profiles,
    generate_evidence_v2_weight_profiles,
)


def test_localization_split_evaluator_selects_only_on_validation(tmp_path):
    template = tmp_path / "template.json"
    template.write_text(
        json.dumps(
            {
                "cases": [
                    _template_case("validation_case", "repo_validation", "repo/validation"),
                    _template_case("test_case", "repo_test", "repo/test"),
                    _template_case("blind_case", "repo_blind", "repo/blind"),
                ]
            }
        ),
        encoding="utf-8",
    )
    protocol = tmp_path / "protocol.json"
    protocol.write_text(
        json.dumps(
            {
                "schema_version": "test",
                "source_template": "template.json",
                "splits": {
                    "validation": {"source_groups": ["repo/validation"]},
                    "test": {"source_groups": ["repo/test"]},
                    "blind": {"source_groups": ["repo/blind"]},
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "evaluation"
    evaluator = LocalizationSplitEvaluator(use_dynamic_coverage=False)
    materialized = output / "materialized"
    from code_intelligence_agent.evaluation.benchmark_materializer import (
        BenchmarkMaterializer,
    )
    from code_intelligence_agent.evaluation.localization_split_evaluation import (
        _write_split_manifests,
    )

    manifest = BenchmarkMaterializer().materialize_template(template, materialized)
    split_manifests = _write_split_manifests(
        manifest,
        json.loads(protocol.read_text(encoding="utf-8")),
        output / "splits",
    )
    report = evaluator.evaluate_split_manifests(
        split_manifests,
        protocol=json.loads(protocol.read_text(encoding="utf-8")),
        output_dir=output,
        profiles=generate_evidence_v2_weight_profiles()[:2],
    )

    assert report.selection_scope == "validation_only"
    assert report.candidate_profile_count == 2
    assert set(report.split_results) == {"validation", "test", "blind"}
    assert all(item.case_count == 1 for item in report.split_results.values())
    assert report.non_regression_passed is True
    assert report.llm_signal_available is False
    assert {item["profile"] for item in report.ablation_results} == {
        "rule_only",
        "graph_only",
        "dynamic_only",
        "llm_only",
        "without_graph",
        "without_dynamic",
        "fusion",
    }
    assert Path(report.artifacts["json"]).exists()
    markdown = Path(report.artifacts["markdown"]).read_text(encoding="utf-8")
    assert "Weight selection uses only the validation split" in markdown
    assert "LLM Signal Available: `false`" in markdown


def test_evidence_v2_true_ablations_remove_only_the_target_signal():
    coverage = ScoreWeights(
        sbfl=0.2,
        graph=0.3,
        static=0.1,
        semantic=0.05,
        llm=0.05,
        risk=0.05,
        test_failure=0.15,
        traceback=0.1,
    )
    static = ScoreWeights(
        graph=0.4,
        static=0.4,
        semantic=0.1,
        llm=0.05,
        risk=0.05,
    )
    profiles = {
        item.name: item
        for item in evidence_v2_ablation_profiles(
            WeightProfile("fusion", coverage, static)
        )
    }

    assert profiles["without_graph"].coverage_weights == ScoreWeights(
        **{**coverage.to_dict(), "graph": 0.0}
    )
    assert profiles["without_graph"].static_only_weights == ScoreWeights(
        **{**static.to_dict(), "graph": 0.0}
    )
    assert profiles["without_dynamic"].coverage_weights == ScoreWeights(
        **{
            **coverage.to_dict(),
            "sbfl": 0.0,
            "test_failure": 0.0,
            "traceback": 0.0,
        }
    )
    assert profiles["without_dynamic"].static_only_weights == ScoreWeights(
        **{
            **static.to_dict(),
            "sbfl": 0.0,
            "test_failure": 0.0,
            "traceback": 0.0,
        }
    )


def _template_case(name, repo_path, upstream):
    return {
        "name": name,
        "repo_path": repo_path,
        "files": [
            {
                "target_path": "sample.py",
                "content": (
                    "def target(values):\n"
                    "    if len(values) >= 0:\n"
                    "        return values[0]\n"
                    "    return None\n"
                ),
            },
            {
                "target_path": "test_sample.py",
                "content": (
                    "from sample import target\n\n"
                    "def test_target_empty():\n"
                    "    assert target([]) is None\n"
                ),
            },
        ],
        "benchmark": {
            "buggy_functions": ["target"],
            "expected_rule_ids": ["always_true_len_check"],
            "failing_tests": ["test_target_empty"],
            "passed_tests": [],
            "test_args": [],
            "metadata": {
                "upstream": upstream,
                "bug_type": "condition error",
            },
        },
    }
