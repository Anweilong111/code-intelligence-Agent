import hashlib
import io
import json
import subprocess
import sys
import tempfile
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_intelligence_agent.evaluation import github_repository_checkout
from code_intelligence_agent.evaluation.github_benchmark_onboarding import (
    OnboardingQualityGateThresholds,
    _annotate_manifest_with_repository_test_evidence,
    _benchmark_result_payload,
    _pytest_args_from_python_module_command,
    _repository_test_evidence_readiness,
    _repository_test_regression_validation_command,
    _repository_test_manifest_evidence,
    build_repository_config_snapshot,
    evaluate_onboarding_quality_gate,
    main as onboarding_main,
    onboard_from_discovery,
    parse_github_repo_spec,
    parse_github_repo_spec_with_ref,
    github_ref_candidates_from_repo_spec,
    render_github_benchmark_onboarding_markdown,
)


def test_parse_github_repo_spec_accepts_common_github_inputs():
    assert parse_github_repo_spec("TheAlgorithms/Python") == (
        "TheAlgorithms",
        "Python",
    )
    assert parse_github_repo_spec("https://github.com/pallets/click.git") == (
        "pallets",
        "click",
    )
    assert parse_github_repo_spec("github.com/psf/requests/tree/main") == (
        "psf",
        "requests",
    )
    assert parse_github_repo_spec("git@github.com:pytest-dev/pytest.git") == (
        "pytest-dev",
        "pytest",
    )
    assert parse_github_repo_spec_with_ref(
        "https://github.com/psf/requests/tree/develop"
    ) == ("psf", "requests", "develop")
    assert parse_github_repo_spec_with_ref(
        "https://github.com/pallets/click/blob/main/src/click/core.py"
    ) == ("pallets", "click", "main")
    assert parse_github_repo_spec_with_ref(
        "https://github.com/example/project/tree/feature/slash"
    ) == ("example", "project", "feature")
    assert github_ref_candidates_from_repo_spec(
        "https://github.com/example/project/tree/feature/slash"
    ) == ["feature", "feature/slash"]
    assert github_ref_candidates_from_repo_spec(
        "https://github.com/example/project/blob/feature/slash/src/pkg/mod.py"
    )[:2] == ["feature", "feature/slash"]
    assert github_ref_candidates_from_repo_spec(
        "https://github.com/example/project/releases/tag/v1/rc1"
    ) == ["v1", "v1/rc1"]
    assert parse_github_repo_spec_with_ref(
        "https://github.com/python/cpython/commit/abc123"
    ) == ("python", "cpython", "abc123")
    assert parse_github_repo_spec_with_ref(
        "https://github.com/pallets/click/releases/tag/8.1.8"
    ) == ("pallets", "click", "8.1.8")
    with pytest.raises(ValueError, match="github.com"):
        parse_github_repo_spec("https://gitlab.com/example/project")


def test_repository_test_regression_validation_command_prefers_passing_execution():
    command = _repository_test_regression_validation_command(
        {
            "usable_for_regression_validation": True,
            "selected_execution": {
                "command": (
                    "python -m pytest -q "
                    "'tests/test_api.py::test_parse[pkg::empty value]'"
                )
            },
            "primary_validation_command": "python -m pytest tests",
        },
        {"recommended_execution_command": "python -m pytest tests/unit"},
    )

    assert command == (
        "python -m pytest -q 'tests/test_api.py::test_parse[pkg::empty value]'"
    )
    assert _pytest_args_from_python_module_command(command) == [
        "tests/test_api.py::test_parse[pkg::empty value]"
    ]


def test_onboarding_from_discovery_writes_benchmark_artifacts():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"

        report = onboard_from_discovery(
            _discovery_payload(raw_source),
            output_dir,
            recipes=["missing_len_zero_guard"],
            materialize_template=True,
            run_benchmark=True,
            use_dynamic_coverage=False,
            run_quality_gate=True,
            run_showcase_lite=True,
        )
        template_payload = json.loads(
            Path(report.output_paths["template"]).read_text(encoding="utf-8")
        )
        catalog_payload = json.loads(
            Path(report.output_paths["catalog"]).read_text(encoding="utf-8")
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )
        diagnostics = json.loads(
            Path(report.output_paths["diagnostics_json"]).read_text(encoding="utf-8")
        )
        report_payload = report.to_dict()
        onboarding_markdown = render_github_benchmark_onboarding_markdown(report)
        run_config_markdown = Path(
            report.output_paths["run_config_markdown"]
        ).read_text(encoding="utf-8")

        assert report.discovery_item_count == 2
        assert report.imported_source_count == 1
        assert report.selected_source_count == 1
        assert report.skipped_source_count == 1
        assert report.generated_candidate_count == 1
        assert report.ready_for_benchmark is True
        assert report.quality_summary["source_hit_rate"] == 1.0
        assert len(template_payload["cases"]) == 1
        assert len(catalog_payload["candidates"]) == 1
        assert Path(report.output_paths["sources"]).exists()
        assert Path(report.output_paths["source_mining_markdown"]).exists()
        assert Path(report.output_paths["selection_audit_json"]).exists()
        assert Path(report.output_paths["selection_audit_markdown"]).exists()
        assert Path(report.output_paths["diagnostics_json"]).exists()
        assert Path(report.output_paths["diagnostics_markdown"]).exists()
        assert Path(report.output_paths["materialized_manifest"]).exists()
        assert report.benchmark_run is not None
        assert report.benchmark_run["summary"]["case_count"] == 1
        assert report.benchmark_run["summary"]["patch_success_rate"] == 1.0
        assert report_payload["benchmarkization_readiness"]["status"] == (
            "benchmark_ready"
        )
        assert report_payload["benchmarkization_readiness"]["ready"] is True
        assert report_payload["benchmarkization_readiness"][
            "benchmark_cases"
        ] == 1
        assert report_payload["benchmarkization_readiness"][
            "remediation_plan"
        ]["primary_action_id"] == "publish_benchmark_evidence_bundle"
        assert report_payload["benchmarkization_readiness"][
            "remediation_plan"
        ]["auto_runnable_action_count"] == 0
        assert diagnostics["benchmarkization_readiness"]["status"] == (
            "benchmark_ready"
        )
        assert diagnostics["summary"]["benchmarkization_ready"] is True
        assert run_config["benchmarkization_readiness"]["status"] == (
            "benchmark_ready"
        )
        assert "Benchmarkization Readiness" in onboarding_markdown
        assert "- Benchmarkization: `benchmark_ready`" in onboarding_markdown
        assert "publish_benchmark_evidence_bundle" in onboarding_markdown
        assert "Benchmarkization Readiness" in run_config_markdown
        assert "publish_benchmark_evidence_bundle" in run_config_markdown
        assert Path(report.output_paths["benchmark_report_json"]).exists()
        assert Path(report.output_paths["benchmark_report_markdown"]).exists()
        assert report.quality_gate is not None
        assert report.quality_gate["passed"] is True
        assert Path(report.output_paths["quality_gate_json"]).exists()
        assert Path(report.output_paths["quality_gate_markdown"]).exists()
        assert report.showcase_lite is not None
        assert report.showcase_lite["headline"]["benchmark_cases"] == 1
        assert report.showcase_lite["rules"] == {"missing_len_zero_guard": 1}
        assert Path(report.output_paths["showcase_lite_json"]).exists()
        assert Path(report.output_paths["showcase_lite_markdown"]).exists()
        assert "GitHub Onboarding Showcase Lite" in Path(
            report.output_paths["showcase_lite_markdown"]
        ).read_text(encoding="utf-8")
        assert "GitHub Onboarding Selection Audit" in Path(
            report.output_paths["selection_audit_markdown"]
        ).read_text(encoding="utf-8")
        assert "GitHub Onboarding Diagnostics" in Path(
            report.output_paths["diagnostics_markdown"]
        ).read_text(encoding="utf-8")
        assert report.repository_test_command is not None
        assert report.repository_test_command["status"] == "skipped"
        assert report.repository_test_command["reason"] == "no_recommended_test_command"
        assert report.repository_test_environment_setup is not None
        assert report.repository_test_environment_setup["status"] == "skipped"
        assert report.repository_test_environment_setup["reason"] == (
            "no_install_command"
        )
        assert report.repository_test_environment_setup_result is not None
        assert report.repository_test_environment_setup_result["status"] == "skipped"
        assert report.repository_test_environment_setup_result["reason"] == (
            "execution_disabled"
        )
        assert report.repository_test_execution_plan is not None
        assert report.repository_test_execution_plan["status"] == "skipped"
        assert report.repository_test_execution_plan["reason"] == (
            "no_recommended_test_command"
        )
        assert report.repository_test_setup_doctor is not None
        assert report.repository_test_setup_doctor["status"] == "blocked"
        assert report.repository_test_setup_doctor["blocker"] == (
            "test_command:no_recommended_test_command"
        )
        assert run_config["repository_test_setup_doctor"]["status"] == "blocked"
        assert run_config["repository_test_setup_doctor"]["blocker"] == (
            "test_command:no_recommended_test_command"
        )
        assert report.repository_test_execution_result is not None
        assert report.repository_test_execution_result["status"] == "skipped"
        assert report.repository_test_execution_result["reason"] == "no_planned_command"
        assert report.repository_test_retry_plan is not None
        assert report.repository_test_retry_plan["status"] == "warning"
        assert report.repository_test_retry_plan["retry_recommended"] is False
        assert report.repository_test_retry_execution_result is not None
        assert report.repository_test_retry_execution_result["status"] == "skipped"
        assert (
            report.repository_test_retry_execution_result["reason"]
            == "execution_disabled"
        )
        assert report.repository_test_dynamic_evidence is not None
        assert report.repository_test_dynamic_evidence["evidence_level"] == (
            "not_executed"
        )
        assert (
            report.repository_test_dynamic_evidence["usable_for_localization"]
            is False
        )
        assert (
            report.repository_test_dynamic_evidence[
                "usable_for_patch_validation"
            ]
            is False
        )
        assert report.repository_test_fault_localization is not None
        assert report.repository_test_fault_localization["status"] == "skipped"
        assert report.repository_test_fault_localization["reason"] == (
            "dynamic_evidence_not_usable"
        )
        assert report.repository_test_patch_candidates is not None
        assert report.repository_test_patch_candidates["status"] == "skipped"
        assert report.repository_test_patch_candidates["reason"] == (
            "fault_localization_not_ready"
        )
        assert report.repository_test_patch_validation is not None
        assert report.repository_test_patch_validation["status"] == "skipped"
        assert report.repository_test_patch_validation["reason"] == (
            "patch_candidates_not_ready"
        )
        assert Path(report.output_paths["repository_test_command_json"]).exists()
        assert Path(report.output_paths["repository_test_command_markdown"]).exists()
        assert Path(
            report.output_paths["repository_test_execution_plan_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_environment_setup_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_environment_setup_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_environment_setup_result_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_environment_setup_result_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_execution_plan_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_setup_doctor_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_setup_doctor_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_execution_result_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_execution_result_markdown"]
        ).exists()
        assert Path(report.output_paths["repository_test_retry_plan_json"]).exists()
        assert Path(
            report.output_paths["repository_test_retry_plan_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_retry_execution_result_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_retry_execution_result_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_dynamic_evidence_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_dynamic_evidence_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_fault_localization_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_fault_localization_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_patch_candidates_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_patch_candidates_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_patch_validation_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_patch_validation_markdown"]
        ).exists()


def test_onboarding_benchmarkization_readiness_points_to_benchmark_run():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"

        report = onboard_from_discovery(
            _discovery_payload(raw_source),
            output_dir,
            recipes=["missing_len_zero_guard"],
        )
        diagnostics = json.loads(
            Path(report.output_paths["diagnostics_json"]).read_text(encoding="utf-8")
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )
        markdown = render_github_benchmark_onboarding_markdown(report)
        remediation_plan_path = Path(
            report.output_paths["benchmarkization_remediation_plan_json"]
        )
        remediation_markdown_path = Path(
            report.output_paths["benchmarkization_remediation_plan_markdown"]
        )
        remediation_plan = json.loads(
            remediation_plan_path.read_text(encoding="utf-8")
        )
        remediation_markdown = remediation_markdown_path.read_text(
            encoding="utf-8"
        )

        readiness = diagnostics["benchmarkization_readiness"]
        assert report.generated_candidate_count == 1
        assert readiness["status"] == "ready_to_run_benchmark"
        assert readiness["ready"] is False
        assert readiness["benchmark_run_present"] is False
        assert "benchmark_run_present" in readiness["blocking_reasons"]
        assert "run_template_benchmark" in "\n".join(readiness["next_actions"])
        assert readiness["remediation_plan"]["primary_action_id"] == (
            "run_template_benchmark"
        )
        assert readiness["remediation_plan"]["auto_runnable_action_count"] == 1
        assert readiness["remediation_plan"]["actions"][0]["auto_runnable"] is True
        assert "run_template_benchmark" in readiness["remediation_plan"][
            "actions"
        ][0]["command"]
        assert diagnostics["summary"]["benchmarkization_status"] == (
            "ready_to_run_benchmark"
        )
        assert run_config["benchmarkization_readiness"]["status"] == (
            "ready_to_run_benchmark"
        )
        assert run_config["resolved_artifacts"][
            "benchmarkization_remediation_plan_json"
        ] == str(remediation_plan_path)
        assert remediation_plan["kind"] == "benchmarkization_remediation_plan"
        assert remediation_plan["status"] == "ready_to_run_benchmark"
        assert remediation_plan["primary_action_id"] == "run_template_benchmark"
        assert remediation_plan["auto_runnable_action_count"] == 1
        assert "run_template_benchmark" in remediation_plan["primary_command"]
        assert remediation_plan["artifacts"]["template"] == report.output_paths[
            "template"
        ]
        assert "# Benchmarkization Remediation Plan" in remediation_markdown
        assert "run_template_benchmark" in remediation_markdown
        assert "- Benchmarkization: `ready_to_run_benchmark`" in markdown
        assert "Benchmarkization Readiness" in markdown
        assert "run_template_benchmark" in markdown


def test_repository_config_snapshot_materializes_ci_and_tox_files():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        payload = _discovery_payload_with_ci_config(raw_source)

        snapshot = build_repository_config_snapshot(payload, root / "snapshot_out")

        assert snapshot["status"] == "pass"
        assert snapshot["file_count"] == 2
        assert sorted(snapshot["files"]) == [
            ".github/workflows/tests.yml",
            "tox.ini",
        ]
        config_root = Path(snapshot["config_root"])
        assert (config_root / ".github" / "workflows" / "tests.yml").exists()
        assert (config_root / "tox.ini").exists()


def test_onboarding_uses_config_snapshot_for_ci_plan_when_checkout_missing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"

        report = onboard_from_discovery(
            _discovery_payload_with_ci_config(raw_source),
            output_dir,
            recipes=["missing_len_zero_guard"],
            run_benchmark=False,
            run_quality_gate=False,
            run_showcase_lite=False,
        )

        assert report.repository_config_snapshot is not None
        assert report.repository_config_snapshot["status"] == "pass"
        assert Path(report.output_paths["repository_config_snapshot_json"]).exists()
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )
        assert run_config["repository_config_snapshot"]["status"] == "pass"
        assert run_config["repository_config_snapshot"]["file_count"] == 2
        assert report.repository_test_environment is not None
        assert report.repository_test_environment["ci_config_source_count"] == 2
        assert report.repository_test_environment["ci_python_versions"] == ["3.11"]
        assert report.repository_test_environment["tox_envlist"] == ["py311"]
        assert report.repository_test_execution_plan is not None
        assert report.repository_test_execution_plan["status"] == "warning"
        assert report.repository_test_execution_plan["reason"] == (
            "full_repo_not_materialized"
        )
        assert report.repository_test_execution_plan[
            "recommended_execution_command"
        ] == "python -m pytest --tb=short tests"
        assert report.repository_test_execution_plan[
            "recommended_execution_level"
        ] == "ci"
        assert report.repository_test_execution_plan["candidate_commands"][0][
            "source"
        ] == "ci_config"
        assert report.repository_test_execution_plan[
            "ci_test_command_candidate_count"
        ] == 1
        assert report.repository_test_execution_result is not None
        assert report.repository_test_execution_result["status"] == "skipped"
        assert report.repository_test_execution_result["reason"] == (
            "plan_not_executable"
        )


def test_onboarding_executes_repository_test_command_when_full_repo_root_is_available():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        full_repo = root / "full_repo"
        full_repo.mkdir()
        (full_repo / "test_smoke.py").write_text(
            "def test_repo_smoke():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        output_dir = root / "onboarded"

        report = onboard_from_discovery(
            _discovery_payload_with_pyproject(raw_source),
            output_dir,
            recipes=["missing_len_zero_guard"],
            repository_test_root=full_repo,
            repository_test_timeout=10,
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )
        markdown = render_github_benchmark_onboarding_markdown(report)

        assert report.repository_profile["recommended_test_command"] == (
            "python -m pytest"
        )
        assert report.repository_test_command is not None
        assert report.repository_test_environment is not None
        assert report.repository_test_environment_setup is not None
        assert report.repository_test_environment_setup_result is not None
        assert report.repository_test_execution_plan is not None
        assert report.repository_test_execution_result is not None
        assert report.repository_test_retry_plan is not None
        assert report.repository_test_environment["status"] == "warning"
        assert report.repository_test_environment["reason"] == (
            "config_files_missing_in_checkout"
        )
        assert report.repository_test_environment["recommended_install_command"] == (
            "python -m pip install -e ."
        )
        assert report.repository_test_environment_setup["status"] == "warning"
        assert report.repository_test_environment_setup["reason"] == (
            "config_files_missing_in_checkout"
        )
        assert report.repository_test_environment_setup_result["status"] == "skipped"
        assert report.repository_test_environment_setup_result["reason"] == (
            "execution_disabled"
        )
        assert report.repository_test_command["status"] == "pass"
        assert report.repository_test_command["executed"] is True
        assert report.repository_test_command["passed"] == 1
        assert report.repository_test_execution_plan["status"] == "warning"
        assert report.repository_test_execution_plan["reason"] == (
            "test_environment_warning"
        )
        assert report.repository_test_execution_plan[
            "recommended_execution_command"
        ] == "python -m pytest -q"
        assert report.repository_test_execution_plan["executable_now"] is True
        assert report.repository_test_execution_result["status"] == "pass"
        assert report.repository_test_execution_result["executed"] is True
        assert report.repository_test_execution_result["passed"] == 1
        assert report.repository_test_retry_plan["status"] == "skipped"
        assert report.repository_test_retry_plan["reason"] == "execution_passed"
        assert report.repository_test_retry_plan["retry_recommended"] is False
        assert report.repository_test_retry_execution_result is not None
        assert report.repository_test_retry_execution_result["status"] == "skipped"
        assert (
            report.repository_test_retry_execution_result["reason"]
            == "execution_disabled"
        )
        assert report.repository_test_dynamic_evidence is not None
        assert report.repository_test_dynamic_evidence["evidence_level"] == (
            "passing_tests"
        )
        assert (
            report.repository_test_dynamic_evidence[
                "usable_for_patch_validation"
            ]
            is False
        )
        assert (
            report.repository_test_dynamic_evidence[
                "usable_for_regression_validation"
            ]
            is True
        )
        assert (
            report.repository_test_dynamic_evidence[
                "recommended_validation_command"
            ]
            == "python -m pytest -q"
        )
        assert report.repository_test_fault_localization is not None
        assert report.repository_test_fault_localization["status"] == "skipped"
        assert report.repository_test_fault_localization["reason"] == (
            "dynamic_evidence_not_usable"
        )
        assert report.repository_test_patch_candidates is not None
        assert report.repository_test_patch_candidates["status"] == "skipped"
        assert report.repository_test_patch_candidates["reason"] == (
            "fault_localization_not_ready"
        )
        assert run_config["repository_test_environment"]["status"] == "warning"
        assert run_config["repository_test_environment"][
            "recommended_install_command"
        ] == "python -m pip install -e ."
        assert run_config["repository_test_environment_setup"]["status"] == "warning"
        assert run_config["repository_test_environment_setup"][
            "install_command_supported"
        ] is True
        assert run_config["repository_test_environment_setup_result"]["status"] == (
            "skipped"
        )
        assert run_config["repository_test_environment_setup_result"][
            "executed"
        ] is False
        assert run_config["repository_test_execution_plan"]["status"] == "warning"
        assert run_config["repository_test_execution_plan"][
            "recommended_execution_command"
        ] == "python -m pytest -q"
        assert run_config["repository_test_execution_plan"]["executable_now"] is True
        assert run_config["repository_test_execution_result"]["status"] == "pass"
        assert run_config["repository_test_execution_result"]["executed"] is True
        assert run_config["repository_test_retry_plan"]["status"] == "skipped"
        assert run_config["repository_test_retry_plan"]["retry_recommended"] is False
        assert (
            run_config["repository_test_retry_execution_result"]["status"]
            == "skipped"
        )
        assert (
            run_config["repository_test_retry_execution_result"]["retry_enabled"]
            is False
        )
        assert run_config["repository_test_dynamic_evidence"]["evidence_level"] == (
            "passing_tests"
        )
        assert (
            run_config["repository_test_dynamic_evidence"][
                "usable_for_patch_validation"
            ]
            is False
        )
        assert (
            run_config["repository_test_dynamic_evidence"][
                "usable_for_regression_validation"
            ]
            is True
        )
        assert run_config["repository_test_fault_localization"]["status"] == (
            "skipped"
        )
        assert run_config["repository_test_fault_localization"]["reason"] == (
            "dynamic_evidence_not_usable"
        )
        assert run_config["repository_test_patch_candidates"]["status"] == (
            "skipped"
        )
        assert run_config["repository_test_patch_candidates"]["reason"] == (
            "fault_localization_not_ready"
        )
        assert run_config["repository_test_patch_validation"]["status"] == (
            "skipped"
        )
        assert run_config["repository_test_patch_validation"]["reason"] == (
            "patch_candidates_not_ready"
        )
        assert run_config["repository_test_patch_validation"]["success_count"] == 0
        assert (
            run_config["repository_test_patch_validation"][
                "successful_reflection_candidate_count"
            ]
            == 0
        )
        assert run_config["repository_test_patch_validation"]["max_depth_executed"] == 0
        assert run_config["repository_test_command"]["status"] == "pass"
        assert run_config["repository_test_command"]["executed"] is True
        assert Path(report.output_paths["repository_test_environment_json"]).exists()
        assert Path(
            report.output_paths["repository_test_environment_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_environment_setup_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_environment_setup_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_environment_setup_result_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_execution_plan_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_execution_plan_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_execution_result_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_execution_result_markdown"]
        ).exists()
        assert Path(report.output_paths["repository_test_retry_plan_json"]).exists()
        assert Path(
            report.output_paths["repository_test_retry_plan_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_retry_execution_result_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_dynamic_evidence_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_fault_localization_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_patch_candidates_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_patch_validation_json"]
        ).exists()
        assert Path(report.output_paths["repository_test_command_json"]).exists()
        assert "Repository Test Command" in markdown
        assert "Repository Test Environment Setup" in markdown
        assert "Repository Test Environment Setup Result" in markdown
        assert "Planned Repository Test Command" in markdown
        assert "Planned Repository Test Result" in markdown
        assert "Repository Test Dynamic Evidence" in markdown
        assert "Repository Test Fault Localization" in markdown
        assert "Repository Test Patch Candidates" in markdown
        assert "Repository Test Patch Validation" in markdown


def test_onboarding_uses_failure_overlay_when_repository_tests_pass():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "sample.py"
        raw_source.write_text(
            "def shift_left(values):\n"
            "    shifted = []\n"
            "    for i in range(len(values)):\n"
            "        shifted.append(values[i + 1])\n"
            "    return shifted\n",
            encoding="utf-8",
        )
        full_repo = root / "full_repo"
        full_repo.mkdir()
        (full_repo / "sample.py").write_text(
            raw_source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (full_repo / "ignored.py").write_text(
            "def ignored_shift(values):\n"
            "    shifted = []\n"
            "    for i in range(len(values)):\n"
            "        shifted.append(values[i + 1])\n"
            "    return shifted\n",
            encoding="utf-8",
        )
        tests = full_repo / "tests"
        tests.mkdir()
        (tests / "test_smoke.py").write_text(
            "def test_repo_smoke():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        output_dir = root / "onboarded"
        discovery_payload = _single_source_discovery_payload(
            raw_source,
            source_path="sample.py",
            target_path="sample.py",
        )
        discovery_payload["files"].append(
            {
                "path": "pyproject.toml",
                "owner": "example",
                "repo": "algorithms",
                "ref": "v1.0.0",
            }
        )

        report = onboard_from_discovery(
            discovery_payload,
            output_dir,
            recipes=["possible_index_overrun"],
            repository_test_root=full_repo,
            repository_test_timeout=10,
            auto_dependency_sources=False,
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )

        assert report.repository_test_dynamic_evidence is not None
        assert report.repository_test_dynamic_evidence["evidence_level"] == (
            "passing_tests"
        )
        assert report.repository_test_failure_overlay is not None
        assert report.repository_test_failure_overlay["status"] == "pass"
        assert report.repository_test_failure_overlay["analysis_scope"]["enabled"] is True
        assert report.repository_test_failure_overlay["analysis_scope"][
            "existing_files"
        ] == ["sample.py"]
        assert report.repository_test_failure_overlay["static_finding_count"] == 1
        assert report.repository_test_failure_overlay["selected_case"]["rule_id"] == (
            "possible_index_overrun"
        )
        assert (
            report.repository_test_failure_overlay["dynamic_evidence"][
                "usable_for_localization"
            ]
            is True
        )
        assert report.repository_test_fault_localization is not None
        assert report.repository_test_fault_localization["status"] == "pass"
        assert report.repository_test_fault_localization["analysis_scope"][
            "enabled"
        ] is True
        assert report.repository_test_fault_localization["analysis_scope"][
            "existing_files"
        ] == ["sample.py"]
        assert report.repository_test_fault_localization["top_function"] == "shift_left"
        assert report.repository_test_patch_candidates is not None
        assert report.repository_test_patch_candidates["status"] == "pass"
        assert report.repository_test_patch_candidates["analysis_scope"][
            "enabled"
        ] is True
        assert report.repository_test_patch_candidates["analysis_scope"][
            "existing_files"
        ] == ["sample.py"]
        assert report.repository_test_patch_validation is not None
        assert report.repository_test_patch_validation["status"] == "pass"
        assert report.repository_test_patch_validation["success_count"] >= 1
        assert report.repository_test_patch_validation["repair_ready"] is True
        assert report.repository_test_patch_validation["regression_ready"] is False
        assert report.repository_test_patch_validation[
            "repair_validation_scope"
        ] == "narrow_only"
        assert report.repository_test_patch_validation["regression_validation"][
            "status"
        ] == "skipped"
        assert report.repository_test_patch_validation["best_patch"][
            "relative_file_path"
        ] == "sample.py"
        assert report.repository_test_repair_summary is not None
        assert report.repository_test_repair_summary["status"] == "pass"
        assert report.repository_test_repair_summary["reason"] == "repair_ready"
        assert (
            report.repository_test_repair_summary["conclusion"]
            == "ready_for_review"
        )
        assert report.repository_test_repair_summary["repair_ready"] is True
        assert (
            report.repository_test_repair_summary["repair_validation_scope"]
            == "narrow_only"
        )
        assert (
            report.repository_test_repair_summary["best_patch"][
                "relative_file_path"
            ]
            == "sample.py"
        )
        assert Path(report.output_paths["repository_test_repair_patch"]).exists()
        assert Path(
            report.output_paths["repository_test_repair_summary_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_repair_summary_markdown"]
        ).exists()
        assert run_config["repository_test_failure_overlay"]["status"] == "pass"
        assert run_config["repository_test_failure_overlay"]["analysis_scoped"] is True
        assert run_config["repository_test_failure_overlay"]["analysis_files"] == [
            "sample.py"
        ]
        assert run_config["repository_test_failure_overlay"][
            "analysis_file_count"
        ] == 1
        assert run_config["repository_test_failure_overlay"][
            "missing_analysis_path_count"
        ] == 0
        assert run_config["repository_test_failure_overlay"]["selected_function"] == (
            "shift_left"
        )
        assert run_config["repository_test_failure_overlay"][
            "public_api_evidence"
        ] == {
            "trigger_scope": "direct_function",
            "internal_target": "shift_left",
            "public_entrypoint": "shift_left",
            "public_call_args": ["[1]"],
            "trigger_expression": "shift_left([1])",
            "call_style": "call",
            "callable_kind": "function",
            "is_nested_target": False,
            "entrypoint_differs_from_internal_target": False,
        }
        assert run_config["repository_test_failure_overlay"][
            "overlay_case_context"
        ]["function_name"] == "shift_left"
        assert (
            run_config["repository_test_failure_overlay"]["selected_score"]
            == report.repository_test_failure_overlay["strategy_summary"][
                "selected_score"
            ]
        )
        assert (
            run_config["repository_test_failure_overlay"]["average_candidate_score"]
            > 0.0
        )
        assert (
            run_config["repository_test_failure_overlay"][
                "selected_score_breakdown"
            ]["static_confidence"]
            > 0.0
        )
        assert (
            run_config["repository_test_failure_overlay"]["candidate_score_preview"][0][
                "overlay_score"
            ]
            > 0.0
        )
        assert (
            run_config["repository_test_failure_overlay"][
                "candidate_rejection_count"
            ]
            == 0
        )
        assert (
            run_config["repository_test_failure_overlay"][
                "candidate_rejection_counts"
            ]
            == {}
        )
        assert (
            run_config["repository_test_failure_overlay"][
                "dominant_candidate_rejection_reason"
            ]
            == ""
        )
        assert (
            run_config["repository_test_failure_overlay"][
                "candidate_rejection_recommendations"
            ]
            == []
        )
        assert (
            run_config["repository_test_failure_overlay"]["next_overlay_extension"]
            == {}
        )
        assert (
            run_config["repository_test_failure_overlay"][
                "next_actionable_overlay_extension"
            ]
            == {}
        )
        assert run_config["repository_test_analysis_route"]["analysis_source"] == (
            "failure_overlay_dynamic_evidence"
        )
        assert run_config["repository_test_analysis_route"][
            "overlay_trigger_reason"
        ] == "natural_tests_passing"
        assert run_config["repository_test_analysis_route"]["phase2_ready"] is True
        assert run_config["repository_test_fault_localization"]["status"] == "pass"
        assert run_config["repository_test_fault_localization"][
            "public_api_evidence"
        ]["trigger_expression"] == "shift_left([1])"
        assert run_config["repository_test_fault_localization"][
            "overlay_case_context"
        ]["function_name"] == "shift_left"
        assert run_config["repository_test_patch_validation"]["status"] == "pass"
        assert (
            run_config["repository_test_patch_validation"]["repair_ready"] is True
        )
        assert (
            run_config["repository_test_patch_validation"]["regression_ready"]
            is False
        )
        assert run_config["repository_test_patch_validation"][
            "repair_validation_scope"
        ] == "narrow_only"
        assert run_config["repository_test_patch_validation"][
            "regression_validation_status"
        ] == "skipped"
        assert run_config["repository_test_patch_validation"][
            "best_patch_relative_file_path"
        ] == "sample.py"
        assert run_config["repository_test_patch_validation"][
            "best_patch_has_diff"
        ] is True
        assert run_config["repository_test_repair_summary"]["present"] is True
        assert run_config["repository_test_repair_summary"]["status"] == "pass"
        assert (
            run_config["repository_test_repair_summary"]["conclusion"]
            == "ready_for_review"
        )
        assert (
            run_config["repository_test_repair_summary"][
                "repair_validation_scope"
            ]
            == "narrow_only"
        )
        assert (
            run_config["repository_test_repair_summary"]["patch_path_present"]
            is True
        )
        assert Path(
            report.output_paths["repository_test_failure_overlay_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_failure_overlay_markdown"]
        ).exists()
        run_config_markdown = Path(report.output_paths["run_config_markdown"]).read_text(
            encoding="utf-8"
        )
        assert "Selected Score" in run_config_markdown
        assert "Candidate Score Preview" in run_config_markdown
        assert "Candidate Rejection Counts" in run_config_markdown
        assert "Dominant Candidate Rejection" in run_config_markdown
        assert "Next Overlay Extension" in run_config_markdown
        assert "Next Actionable Overlay Extension" in run_config_markdown
        assert "Repair Summary" in run_config_markdown
        assert "ready_for_review" in run_config_markdown
        assert "Scoped Analysis" in run_config_markdown
        assert "Analysis Files" in run_config_markdown
        assert "Repair Ready" in run_config_markdown
        assert "Regression Ready" in run_config_markdown
        assert "Regression Validation" in run_config_markdown
        assert "Best Patch File" in run_config_markdown
        assert "Public API Evidence" in run_config_markdown
        assert "direct_function: shift_left([1]) -> shift_left" in run_config_markdown
        onboarding_markdown = render_github_benchmark_onboarding_markdown(report)
        assert "Repository Test Failure Overlay Public API" in onboarding_markdown
        assert "direct_function: shift_left([1]) -> shift_left" in onboarding_markdown
        diagnostics = json.loads(
            Path(report.output_paths["diagnostics_json"]).read_text(encoding="utf-8")
        )
        readiness = diagnostics["repository_test_readiness"]
        assert readiness["status"] == "runtime_ready"
        assert readiness["runtime_evidence_chain_present"] is True
        assert readiness["benchmark_evidence_chain_present"] is False
        assert readiness["public_api_trace_present"] is True
        assert readiness["trigger_expression"] == "shift_left([1])"
        assert readiness["public_entrypoint"] == "shift_left"
        assert readiness["internal_target"] == "shift_left"
        assert diagnostics["summary"][
            "repository_test_runtime_evidence_chain_present"
        ] is True
        benchmarkization = diagnostics["benchmarkization_readiness"]
        assert benchmarkization["status"] == "blocked_at_candidate_generation"
        assert benchmarkization["ready"] is False
        assert benchmarkization["repository_test_evidence_status"] == "runtime_ready"
        assert "generated_candidates" in benchmarkization["blocking_reasons"]
        assert benchmarkization["remediation_plan"]["primary_action_id"] == (
            "inspect_recipe_misses"
        )
        assert benchmarkization["remediation_plan"]["manual_action_count"] >= 1
        assert "source_mining.md" in "\n".join(
            benchmarkization["next_actions"]
        )
        assert run_config["benchmarkization_readiness"]["status"] == (
            "blocked_at_candidate_generation"
        )
        diagnostics_markdown = Path(
            report.output_paths["diagnostics_markdown"]
        ).read_text(encoding="utf-8")
        remediation_plan = json.loads(
            Path(
                report.output_paths["benchmarkization_remediation_plan_json"]
            ).read_text(encoding="utf-8")
        )
        remediation_markdown = Path(
            report.output_paths["benchmarkization_remediation_plan_markdown"]
        ).read_text(encoding="utf-8")
        assert "Benchmarkization Readiness" in diagnostics_markdown
        assert "blocked_at_candidate_generation" in diagnostics_markdown
        assert "inspect_recipe_misses" in diagnostics_markdown
        assert remediation_plan["status"] == "blocked_at_candidate_generation"
        assert remediation_plan["primary_action_id"] == "inspect_recipe_misses"
        assert remediation_plan["manual_action_count"] >= 1
        assert "generated_candidates" in remediation_plan["blocking_reasons"]
        assert "source_mining.md" in remediation_markdown
        assert "Repository Test Evidence Readiness" in diagnostics_markdown
        assert "Runtime Evidence Chain: True" in diagnostics_markdown
        assert "`shift_left([1])` -> `shift_left` -> `shift_left`" in (
            diagnostics_markdown
        )


def test_onboarding_manifest_annotation_records_repository_public_api_evidence(
    tmp_path,
):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "shift_left_case",
                        "repo_path": "repo",
                        "buggy_functions": ["shift_left"],
                        "expected_rule_ids": ["possible_index_overrun"],
                        "failing_tests": [],
                        "passed_tests": [],
                        "test_args": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    public_api_evidence = {
        "trigger_scope": "direct_function",
        "internal_target": "shift_left",
        "public_entrypoint": "shift_left",
        "public_call_args": ["[1]"],
        "trigger_expression": "shift_left([1])",
        "call_style": "call",
        "callable_kind": "function",
        "is_nested_target": False,
        "entrypoint_differs_from_internal_target": False,
    }
    overlay_case_context = {
        "rule_id": "possible_index_overrun",
        "function_name": "shift_left",
        "qualified_name": "shift_left",
        "callable_kind": "function",
        "relative_file_path": "sample.py",
        "expected_exception": "IndexError",
        "public_api_evidence": public_api_evidence,
    }
    evidence = _repository_test_manifest_evidence(
        natural_evidence={
            "evidence_level": "passing_tests",
            "usable_for_localization": False,
        },
        failure_overlay={
            "status": "pass",
            "reason": "overlay_dynamic_evidence_generated",
            "overlay_root": str(tmp_path / "overlay"),
            "selected_case": {
                "rule_id": "possible_index_overrun",
                "function_name": "shift_left",
                "public_api_evidence": public_api_evidence,
            },
            "dynamic_evidence": {
                "evidence_level": "failing_tests",
                "usable_for_localization": True,
                "usable_for_patch_validation": True,
                "recommended_validation_command": (
                    "python -m pytest -q tests/test_overlay.py::test_shift_left"
                ),
                "overlay_case_context": overlay_case_context,
            },
            "recommended_validation_command": (
                "python -m pytest -q tests/test_overlay.py::test_shift_left"
            ),
        },
        fault_localization={
            "status": "pass",
            "reason": "localized_from_dynamic_evidence",
            "top_function": "shift_left",
            "top_score": 0.95,
            "public_api_evidence": public_api_evidence,
            "overlay_case_context": overlay_case_context,
        },
        execution_plan={"repository_root": str(tmp_path / "repo")},
    )

    _annotate_manifest_with_repository_test_evidence(manifest_path, evidence)
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_payload["cases"][0]["name"] == "shift_left_case"
    assert manifest_payload["repository_test_evidence"]["analysis_route"][
        "analysis_source"
    ] == "failure_overlay_dynamic_evidence"
    assert manifest_payload["repository_test_evidence"]["failure_overlay"][
        "public_api_evidence"
    ]["trigger_expression"] == "shift_left([1])"
    assert manifest_payload["repository_test_evidence"]["fault_localization"][
        "public_api_evidence"
    ]["internal_target"] == "shift_left"
    benchmark_payload = _benchmark_result_payload(
        {
            "template_validation": {"is_valid": True},
            "manifest_path": str(manifest_path),
            "manifest_validation": {"is_valid": True},
            "report_artifacts": {"json": "benchmark_report.json"},
            "benchmark_report": _FakeBenchmarkReport(),
        },
        tmp_path / "benchmark_run",
    )
    assert benchmark_payload["repository_test_evidence"]["failure_overlay"][
        "public_api_evidence"
    ]["trigger_expression"] == "shift_left([1])"
    readiness = _repository_test_evidence_readiness(
        SimpleNamespace(
            benchmark_run=benchmark_payload,
            repository_test_execution_result=None,
            repository_test_dynamic_evidence=None,
            repository_test_failure_overlay=None,
            repository_test_fault_localization=None,
            repository_test_patch_candidates=None,
            repository_test_patch_validation=None,
        )
    )
    assert readiness["status"] == "benchmark_ready"
    assert readiness["benchmark_repository_test_evidence_present"] is True
    assert readiness["benchmark_evidence_chain_present"] is True
    assert readiness["runtime_evidence_chain_present"] is False
    assert readiness["analysis_source"] == "failure_overlay_dynamic_evidence"
    assert readiness["trigger_expression"] == "shift_left([1])"


def test_onboarding_can_checkout_repository_before_test_command():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"
        checkout_commands = []
        setup_commands = []
        planned_execution_commands = []

        def fake_checkout_runner(command, cwd, capture_output, text, timeout, check):
            del cwd, capture_output, text, timeout, check
            checkout_commands.append(command)
            checkout_path = Path(command[-1])
            checkout_path.mkdir(parents=True)
            (checkout_path / ".git").mkdir()
            (checkout_path / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\n"
                "testpaths = ['tests']\n",
                encoding="utf-8",
            )
            maths_dir = checkout_path / "maths"
            maths_dir.mkdir()
            _write_average_mean(maths_dir)
            tests_dir = checkout_path / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_smoke.py").write_text(
                "def test_checkout_smoke():\n"
                "    assert True\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "cloned", "")

        def fake_setup_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            setup_commands.append((command, cwd))
            return subprocess.CompletedProcess(command, 0, "setup ok", "")

        def fake_test_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            planned_execution_commands.append((command, cwd))
            return subprocess.CompletedProcess(command, 0, "1 passed", "")

        report = onboard_from_discovery(
            _discovery_payload_with_pyproject(raw_source),
            output_dir,
            recipes=["missing_len_zero_guard"],
            checkout_repository_tests=True,
            repository_checkout_runner=fake_checkout_runner,
            run_repository_test_environment_setup=True,
            repository_test_environment_setup_runner=fake_setup_runner,
            repository_test_execution_runner=fake_test_runner,
            repository_checkout_timeout=10,
            repository_test_timeout=10,
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )

        assert report.repository_checkout is not None
        assert report.repository_checkout["status"] == "pass"
        assert report.repository_checkout["reason"] == "checkout_created"
        assert report.repository_checkout_sources is not None
        assert report.repository_checkout_sources["discovery"][
            "included_file_count"
        ] >= 3
        assert report.repository_test_command is not None
        assert report.repository_test_environment_setup is not None
        assert report.repository_test_environment_setup_result is not None
        assert report.repository_test_execution_plan is not None
        assert report.repository_test_execution_result is not None
        assert report.repository_test_retry_plan is not None
        assert report.repository_test_command["status"] == "pass"
        assert report.repository_test_command["executed"] is True
        assert report.repository_test_command["passed"] == 1
        assert report.repository_test_execution_plan[
            "recommended_execution_level"
        ] == "narrow"
        assert report.repository_test_execution_plan[
            "recommended_execution_command"
        ] == "python -m pytest -q tests"
        assert report.repository_test_environment["pytest_config_testpaths"] == [
            "tests"
        ]
        assert report.repository_test_execution_plan["configured_test_paths"] == [
            "tests"
        ]
        assert report.repository_test_execution_result["status"] == "pass"
        assert report.repository_test_execution_result["executed"] is True
        assert report.repository_test_execution_result["command"] == (
            "python -m pytest -q tests"
        )
        assert report.repository_test_execution_result[
            "python_executable"
        ] == report.repository_test_environment_setup["venv_python"]
        assert report.repository_test_execution_result[
            "python_executable_source"
        ] == "repository_test_environment_setup"
        assert report.repository_test_retry_plan["status"] == "skipped"
        assert report.repository_test_retry_plan["reason"] == "execution_passed"
        assert report.repository_test_retry_execution_result is not None
        assert report.repository_test_retry_execution_result["status"] == "skipped"
        assert report.repository_test_dynamic_evidence is not None
        assert report.repository_test_dynamic_evidence["evidence_level"] == (
            "passing_tests"
        )
        assert (
            report.repository_test_dynamic_evidence[
                "recommended_validation_command"
            ]
            == "python -m pytest -q tests"
        )
        assert report.repository_test_fault_localization is not None
        assert report.repository_test_fault_localization["status"] == "skipped"
        assert report.repository_test_fault_localization["reason"] == (
            "dynamic_evidence_not_usable"
        )
        assert report.repository_test_patch_candidates is not None
        assert report.repository_test_patch_candidates["status"] == "skipped"
        assert report.repository_test_patch_candidates["reason"] == (
            "fault_localization_not_ready"
        )
        assert report.repository_test_patch_validation is not None
        assert report.repository_test_patch_validation["status"] == "skipped"
        assert report.repository_test_patch_validation["reason"] == (
            "patch_candidates_not_ready"
        )
        assert report.repository_test_environment_setup["status"] == "pass"
        assert report.repository_test_environment_setup[
            "install_command_supported"
        ] is True
        assert report.repository_test_environment_setup_result["status"] == "pass"
        assert report.repository_test_environment_setup_result["executed"] is True
        assert report.repository_test_environment_setup_result[
            "create_returncode"
        ] == 0
        assert report.repository_test_environment_setup_result[
            "install_returncode"
        ] == 0
        assert report.generated_candidate_count == 1
        assert run_config["actions"]["checkout_repository_tests"] is True
        assert run_config["repository_checkout"]["status"] == "pass"
        assert run_config["repository_checkout_sources"]["present"] is True
        assert run_config["repository_checkout_sources"]["included_file_count"] >= 3
        assert run_config["repository_test_execution_result"][
            "python_executable"
        ] == report.repository_test_environment_setup["venv_python"]
        assert run_config["repository_test_execution_result"][
            "python_executable_source"
        ] == "repository_test_environment_setup"
        assert run_config["repository_test_retry_plan"]["status"] == "skipped"
        assert run_config["repository_test_retry_plan"]["retry_recommended"] is False
        assert (
            run_config["repository_test_retry_execution_result"]["status"]
            == "skipped"
        )
        assert run_config["repository_test_dynamic_evidence"]["evidence_level"] == (
            "passing_tests"
        )
        assert (
            run_config["repository_test_dynamic_evidence"][
                "recommended_validation_command"
            ]
            == "python -m pytest -q tests"
        )
        assert run_config["repository_test_fault_localization"]["status"] == (
            "skipped"
        )
        assert run_config["repository_test_patch_candidates"]["status"] == (
            "skipped"
        )
        assert run_config["repository_test_patch_validation"]["status"] == (
            "skipped"
        )
        checkout_path = Path(report.repository_checkout["checkout_path"]).resolve()
        imported_average = [
            source
            for source in report.import_report.source_entries
            if source.get("source_path") == "maths/average_mean.py"
        ]
        assert imported_average
        assert Path(imported_average[0]["raw_url"]).resolve().is_relative_to(
            checkout_path
        )
        assert Path(report.output_paths["repository_checkout_json"]).exists()
        assert Path(report.output_paths["repository_checkout_markdown"]).exists()
        assert Path(report.output_paths["repository_checkout_sources_json"]).exists()
        assert Path(
            report.output_paths["repository_checkout_sources_markdown"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_execution_plan_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_environment_setup_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_environment_setup_result_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_execution_result_json"]
        ).exists()
        assert Path(report.output_paths["repository_test_retry_plan_json"]).exists()
        assert Path(
            report.output_paths["repository_test_retry_execution_result_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_dynamic_evidence_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_fault_localization_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_patch_candidates_json"]
        ).exists()
        assert Path(
            report.output_paths["repository_test_patch_validation_json"]
        ).exists()
        assert checkout_commands
        assert len(setup_commands) == 2
        assert len(planned_execution_commands) == 1
        assert planned_execution_commands[0][0][0] == (
            report.repository_test_environment_setup["venv_python"]
        )


def test_onboarding_checkout_sources_preserve_default_branch_ref_provenance():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"
        payload = _discovery_payload_with_pyproject(raw_source)
        payload.update(
            {
                "owner": "example",
                "repo": "algorithms",
                "ref": "main",
                "discovery": {
                    "mode": "tree",
                    "owner": "example",
                    "repo": "algorithms",
                    "ref": "main",
                    "requested_ref": None,
                    "ref_source": "default_branch",
                    "recursive": True,
                },
            }
        )

        def fake_checkout_runner(command, cwd, capture_output, text, timeout, check):
            del cwd, capture_output, text, timeout, check
            checkout_path = Path(command[-1])
            checkout_path.mkdir(parents=True)
            maths_dir = checkout_path / "maths"
            maths_dir.mkdir()
            _write_average_mean(maths_dir)
            (checkout_path / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "cloned", "")

        report = onboard_from_discovery(
            payload,
            output_dir,
            recipes=["missing_len_zero_guard"],
            checkout_repository_tests=True,
            repository_checkout_runner=fake_checkout_runner,
            run_repository_test_command=False,
        )
        checkout_discovery = report.repository_checkout_sources["discovery"]
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(
                encoding="utf-8"
            )
        )

        assert report.discovery_metadata["ref"] == "main"
        assert report.discovery_metadata["requested_ref"] is None
        assert report.discovery_metadata["ref_source"] == "default_branch"
        assert checkout_discovery["ref"] == "main"
        assert checkout_discovery["requested_ref"] is None
        assert checkout_discovery["ref_source"] == "default_branch"
        assert run_config["discovery"]["metadata"]["requested_ref"] is None
        assert run_config["discovery"]["metadata"]["ref_source"] == "default_branch"


def test_onboarding_checkout_plans_and_executes_narrow_unittest_command():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"
        planned_execution_commands = []

        def fake_checkout_runner(command, cwd, capture_output, text, timeout, check):
            del cwd, capture_output, text, timeout, check
            checkout_path = Path(command[-1])
            checkout_path.mkdir(parents=True)
            (checkout_path / ".git").mkdir()
            maths_dir = checkout_path / "maths"
            maths_dir.mkdir()
            _write_average_mean(maths_dir)
            tests_dir = checkout_path / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_sample.py").write_text(
                "import unittest\n\n"
                "class SampleTest(unittest.TestCase):\n"
                "    def test_checkout_smoke(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "cloned", "")

        def fake_test_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            planned_execution_commands.append((command, cwd))
            return subprocess.CompletedProcess(
                command,
                0,
                "Ran 1 test in 0.001s\n\nOK\n",
                "",
            )

        report = onboard_from_discovery(
            _discovery_payload(raw_source),
            output_dir,
            recipes=["missing_len_zero_guard"],
            checkout_repository_tests=True,
            repository_checkout_runner=fake_checkout_runner,
            repository_test_execution_runner=fake_test_runner,
            repository_checkout_timeout=10,
            repository_test_timeout=10,
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )

        assert report.repository_test_execution_plan is not None
        assert report.repository_test_execution_plan[
            "recommended_execution_runner"
        ] == "unittest"
        assert report.repository_test_execution_plan[
            "recommended_execution_level"
        ] == "narrow"
        assert report.repository_test_execution_plan[
            "recommended_execution_risk"
        ] == "low"
        assert report.repository_test_execution_plan[
            "recommended_execution_command"
        ] == "python -m unittest discover -s tests -p test_sample.py"
        assert report.repository_test_execution_plan["executable_now"] is True
        assert planned_execution_commands
        assert planned_execution_commands[0][0][1:3] == ["-m", "unittest"]
        assert planned_execution_commands[0][0][3:] == [
            "discover",
            "-s",
            "tests",
            "-p",
            "test_sample.py",
        ]
        assert report.repository_test_execution_result is not None
        assert report.repository_test_execution_result["status"] == "pass"
        assert report.repository_test_execution_result["execution_runner"] == (
            "unittest"
        )
        assert report.repository_test_execution_result["command"] == (
            "python -m unittest discover -s tests -p test_sample.py"
        )
        assert report.repository_test_dynamic_evidence is not None
        assert report.repository_test_dynamic_evidence["evidence_level"] == (
            "passing_tests"
        )
        assert report.repository_test_dynamic_evidence[
            "usable_for_regression_validation"
        ] is True
        assert report.repository_test_dynamic_evidence[
            "recommended_validation_command"
        ] == "python -m unittest discover -s tests -p test_sample.py"
        assert run_config["repository_test_execution_plan"][
            "recommended_execution_runner"
        ] == "unittest"
        assert run_config["repository_test_dynamic_evidence"][
            "recommended_validation_command"
        ] == "python -m unittest discover -s tests -p test_sample.py"


def test_onboarding_auto_retry_prerequisite_setup_runs_after_missing_dependency():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"
        checkout_commands = []
        setup_commands = []
        test_commands = []

        def fake_checkout_runner(command, cwd, capture_output, text, timeout, check):
            del cwd, capture_output, text, timeout, check
            checkout_commands.append(command)
            checkout_path = Path(command[-1])
            checkout_path.mkdir(parents=True)
            (checkout_path / ".git").mkdir()
            (checkout_path / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\n"
                "testpaths = ['tests']\n",
                encoding="utf-8",
            )
            (checkout_path / "sample.py").write_text(
                "def ok():\n"
                "    return True\n",
                encoding="utf-8",
            )
            tests_dir = checkout_path / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_sample.py").write_text(
                "def test_sample():\n"
                "    assert True\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "cloned", "")

        def fake_setup_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            setup_commands.append((command, cwd))
            return subprocess.CompletedProcess(command, 0, "setup ok", "")

        def fake_test_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            test_commands.append((command, cwd))
            if len(test_commands) == 1:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    "ModuleNotFoundError: No module named 'requests'",
                )
            return subprocess.CompletedProcess(command, 0, "1 passed", "")

        report = onboard_from_discovery(
            _discovery_payload_with_pyproject(raw_source),
            output_dir,
            recipes=["missing_len_zero_guard"],
            checkout_repository_tests=True,
            repository_checkout_runner=fake_checkout_runner,
            run_repository_test_retry_prerequisites=True,
            auto_repository_test_retry=True,
            auto_repository_test_retry_max_risk="high",
            auto_repository_test_retry_allowed_runners=["pytest"],
            repository_test_environment_setup_runner=fake_setup_runner,
            repository_test_execution_runner=fake_test_runner,
            repository_test_retry_execution_runner=fake_test_runner,
            repository_checkout_timeout=10,
            repository_test_timeout=10,
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )

        assert checkout_commands
        assert len(setup_commands) == 2
        assert len(test_commands) == 2
        assert report.repository_test_execution_result["status"] == "fail"
        assert report.repository_test_execution_result["failure_category"] == (
            "missing_dependency"
        )
        assert report.repository_test_execution_result["python_executable_source"] == (
            "current_interpreter"
        )
        assert report.repository_test_retry_plan["retry_strategy"] == (
            "run_environment_setup_then_retry"
        )
        assert report.repository_test_environment_setup_result["status"] == "pass"
        assert (
            report.repository_test_environment_setup_result["triggered_by"]
            == "repository_test_retry_prerequisite"
        )
        assert (
            report.repository_test_environment_setup_result["auto_retry_prerequisite"]
            is True
        )
        assert report.repository_test_retry_execution_result["status"] == "pass"
        assert report.repository_test_retry_execution_result["executed"] is True
        assert (
            report.repository_test_retry_execution_result["retry_enabled_source"]
            == "auto_repository_test_retry"
        )
        assert (
            report.repository_test_retry_execution_result[
                "auto_repository_test_retry_applied"
            ]
            is True
        )
        assert (
            report.repository_test_retry_execution_result[
                "retry_setup_prerequisite_satisfied"
            ]
            is True
        )
        assert (
            report.repository_test_retry_execution_result[
                "retry_setup_prerequisite_auto_executed"
            ]
            is True
        )
        assert report.repository_test_retry_execution_result[
            "python_executable"
        ] == report.repository_test_environment_setup["venv_python"]
        assert report.repository_test_retry_execution_result[
            "python_executable_source"
        ] == "repository_test_environment_setup"
        assert test_commands[0][0][0] != report.repository_test_environment_setup[
            "venv_python"
        ]
        assert test_commands[1][0][0] == report.repository_test_environment_setup[
            "venv_python"
        ]
        assert report.repository_test_dynamic_evidence["source"] == (
            "retry_execution_result"
        )
        assert report.repository_test_dynamic_evidence["evidence_level"] == (
            "passing_tests"
        )
        assert (
            report.repository_test_dynamic_evidence[
                "usable_for_regression_validation"
            ]
            is True
        )
        assert (
            report.repository_test_dynamic_evidence[
                "usable_for_patch_validation"
            ]
            is False
        )
        assert (
            run_config["actions"]["run_repository_test_retry"] is False
        )
        assert (
            run_config["actions"]["run_repository_test_retry_prerequisites"] is True
        )
        assert run_config["actions"]["auto_repository_test_retry"] is True
        assert (
            run_config["actions"]["auto_repository_test_retry_max_risk"] == "high"
        )
        assert run_config["actions"]["auto_repository_test_retry_allowed_runners"] == [
            "pytest"
        ]
        assert (
            run_config["repository_test_environment_setup_result"][
                "triggered_by"
            ]
            == "repository_test_retry_prerequisite"
        )
        assert (
            run_config["repository_test_retry_execution_result"][
                "retry_setup_prerequisite_auto_executed"
            ]
            is True
        )


def test_onboarding_repairs_missing_pytest_fixture_with_single_plugin_candidate():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"
        discovery = _discovery_payload_with_ci_config(raw_source)
        requirements = "pytest-mock==2.0.0\npytest-httpbin==2.0.0\n"
        discovery["files"].append(
            {
                "path": "requirements-dev.txt",
                "content": requirements,
                "size": len(requirements),
                "owner": "example",
                "repo": "algorithms",
                "ref": "v1.0.0",
            }
        )
        setup_commands = []
        planned_commands = []
        retry_commands = []

        def fake_checkout_runner(command, cwd, capture_output, text, timeout, check):
            del cwd, capture_output, text, timeout, check
            checkout_path = Path(command[-1])
            checkout_path.mkdir(parents=True)
            (checkout_path / ".git").mkdir()
            (checkout_path / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\n"
                "testpaths = ['tests']\n",
                encoding="utf-8",
            )
            (checkout_path / "tox.ini").write_text(
                "[tox]\n"
                "envlist = py311\n"
                "\n"
                "[testenv]\n"
                "commands = python -m pytest --tb=short tests\n",
                encoding="utf-8",
            )
            (checkout_path / "requirements-dev.txt").write_text(
                requirements,
                encoding="utf-8",
            )
            tests_dir = checkout_path / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_sample.py").write_text(
                "def test_sample(mocker):\n"
                "    assert True\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "cloned", "")

        def fake_setup_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            setup_commands.append((command, cwd))
            return subprocess.CompletedProcess(command, 0, "setup ok", "")

        def fake_planned_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            planned_commands.append((command, cwd))
            return subprocess.CompletedProcess(
                command,
                2,
                "",
                "ERROR collecting tests/test_sample.py\nImportError while importing test module",
            )

        def fake_retry_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            retry_commands.append((command, cwd))
            if len(retry_commands) == 1:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "ERROR tests/test_sample.py::test_sample\n"
                    "fixture 'mocker' not found\n",
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "1 passed", "")

        report = onboard_from_discovery(
            discovery,
            output_dir,
            recipes=["missing_len_zero_guard"],
            checkout_repository_tests=True,
            repository_checkout_runner=fake_checkout_runner,
            run_repository_test_environment_setup=True,
            run_repository_test_retry=True,
            auto_repository_test_retry=True,
            auto_repository_test_retry_max_risk="high",
            auto_repository_test_retry_allowed_runners=["pytest"],
            repository_test_environment_setup_runner=fake_setup_runner,
            repository_test_execution_runner=fake_planned_runner,
            repository_test_retry_execution_runner=fake_retry_runner,
            repository_checkout_timeout=10,
            repository_test_timeout=10,
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )

        assert len(planned_commands) == 1
        assert len(retry_commands) == 2
        assert len(setup_commands) == 3
        assert setup_commands[-1][0][-1] == "pytest-mock==2.0.0"
        assert report.repository_test_environment_setup is not None
        assert report.repository_test_environment_setup[
            "pytest_plugin_dependency_candidates"
        ] == ["pytest-mock==2.0.0", "pytest-httpbin==2.0.0"]
        assert report.repository_test_retry_execution_result["failure_category"] == (
            "missing_pytest_fixture"
        )
        assert report.repository_test_pytest_plugin_repair is not None
        assert report.repository_test_pytest_plugin_repair["status"] == "pass"
        assert report.repository_test_pytest_plugin_repair["executed"] is True
        assert report.repository_test_pytest_plugin_repair["fixture"] == "mocker"
        assert report.repository_test_pytest_plugin_repair[
            "plugin_requirement"
        ] == "pytest-mock==2.0.0"
        assert (
            report.repository_test_pytest_plugin_repair_retry_execution_result
            is not None
        )
        assert (
            report.repository_test_pytest_plugin_repair_retry_execution_result[
                "status"
            ]
            == "pass"
        )
        assert report.repository_test_dynamic_evidence["source"] == (
            "retry_execution_result"
        )
        assert report.repository_test_dynamic_evidence["evidence_level"] == (
            "passing_tests"
        )
        assert (
            report.repository_test_dynamic_evidence[
                "usable_for_regression_validation"
            ]
            is True
        )
        assert run_config["repository_test_pytest_plugin_repair"]["status"] == "pass"
        assert run_config["repository_test_pytest_plugin_repair"]["executed"] is True
        assert (
            run_config["repository_test_pytest_plugin_repair"]["plugin_requirement"]
            == "pytest-mock==2.0.0"
        )
        assert (
            run_config[
                "repository_test_pytest_plugin_repair_retry_execution_result"
            ]["status"]
            == "pass"
        )
        assert Path(
            report.output_paths["repository_test_pytest_plugin_repair_json"]
        ).exists()
        assert Path(
            report.output_paths[
                "repository_test_pytest_plugin_repair_retry_execution_result_json"
            ]
        ).exists()


def test_onboarding_narrows_timeout_after_pytest_plugin_repair():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"
        discovery = _discovery_payload_with_ci_config(raw_source)
        requirements = "pytest-mock==2.0.0\n"
        discovery["files"].append(
            {
                "path": "requirements-dev.txt",
                "content": requirements,
                "size": len(requirements),
                "owner": "example",
                "repo": "algorithms",
                "ref": "v1.0.0",
            }
        )
        setup_commands = []
        retry_commands = []

        def fake_checkout_runner(command, cwd, capture_output, text, timeout, check):
            del cwd, capture_output, text, timeout, check
            checkout_path = Path(command[-1])
            checkout_path.mkdir(parents=True)
            (checkout_path / ".git").mkdir()
            (checkout_path / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\n"
                "testpaths = ['tests']\n",
                encoding="utf-8",
            )
            (checkout_path / "tox.ini").write_text(
                "[tox]\n"
                "envlist = py311\n"
                "\n"
                "[testenv]\n"
                "commands = python -m pytest tests\n",
                encoding="utf-8",
            )
            (checkout_path / "requirements-dev.txt").write_text(
                requirements,
                encoding="utf-8",
            )
            tests_dir = checkout_path / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_sample.py").write_text(
                "def test_sample(mocker):\n"
                "    assert True\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "cloned", "")

        def fake_setup_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            setup_commands.append((command, cwd))
            return subprocess.CompletedProcess(command, 0, "setup ok", "")

        def fake_planned_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            return subprocess.CompletedProcess(
                command,
                2,
                "",
                "ERROR collecting tests/test_sample.py\nImportError while importing test module",
            )

        def fake_retry_runner(command, cwd, capture_output, text, timeout, check, env):
            del cwd, capture_output, text, timeout, check, env
            retry_commands.append(command)
            if len(retry_commands) == 1:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "ERROR tests/test_sample.py::test_sample\n"
                    "fixture 'mocker' not found\n",
                    "",
                )
            if len(retry_commands) == 2:
                raise subprocess.TimeoutExpired(command, 10, output="............")
            return subprocess.CompletedProcess(command, 0, "1 passed", "")

        report = onboard_from_discovery(
            discovery,
            output_dir,
            recipes=["missing_len_zero_guard"],
            checkout_repository_tests=True,
            repository_checkout_runner=fake_checkout_runner,
            run_repository_test_environment_setup=True,
            run_repository_test_retry=True,
            auto_repository_test_retry=True,
            auto_repository_test_retry_max_risk="high",
            auto_repository_test_retry_allowed_runners=["pytest"],
            repository_test_environment_setup_runner=fake_setup_runner,
            repository_test_execution_runner=fake_planned_runner,
            repository_test_retry_execution_runner=fake_retry_runner,
            repository_checkout_timeout=10,
            repository_test_timeout=10,
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )

        assert len(setup_commands) == 3
        assert len(retry_commands) == 3
        assert retry_commands[2][-1] == "tests/test_sample.py"
        assert report.repository_test_pytest_plugin_repair["status"] == "pass"
        assert (
            report.repository_test_pytest_plugin_repair_retry_execution_result[
                "failure_category"
            ]
            == "timeout"
        )
        assert report.repository_test_timeout_narrowing is not None
        assert report.repository_test_timeout_narrowing["status"] == "pass"
        assert report.repository_test_timeout_narrowing["executed"] is True
        assert report.repository_test_timeout_narrowing["attempt_count"] == 1
        assert report.repository_test_timeout_narrowing[
            "selected_command"
        ] == "python -m pytest -q --maxfail=1 tests/test_sample.py"
        assert report.repository_test_timeout_narrowing[
            "selected_failure_category"
        ] == "none"
        assert report.repository_test_dynamic_evidence["evidence_level"] == (
            "passing_tests"
        )
        assert report.repository_test_dynamic_evidence[
            "recommended_validation_command"
        ] == "python -m pytest -q --maxfail=1 tests/test_sample.py"
        assert run_config["repository_test_timeout_narrowing"]["status"] == "pass"
        assert (
            run_config["repository_test_timeout_narrowing"]["selected_command"]
            == "python -m pytest -q --maxfail=1 tests/test_sample.py"
        )
        assert run_config["repository_test_effective_execution_result"][
            "present"
        ] is True
        assert run_config["repository_test_effective_execution_result"][
            "source"
        ] == "dynamic_evidence"
        assert run_config["repository_test_effective_execution_result"][
            "status"
        ] == "pass"
        assert run_config["repository_test_effective_execution_result"][
            "command"
        ] == "python -m pytest -q --maxfail=1 tests/test_sample.py"
        assert run_config["repository_test_effective_execution_result"][
            "failure_category"
        ] == "none"
        assert Path(report.output_paths["repository_test_timeout_narrowing_json"]).exists()


def test_onboarding_auto_retry_respects_allowed_runner_whitelist():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"
        checkout_commands = []
        setup_commands = []
        test_commands = []

        def fake_checkout_runner(command, cwd, capture_output, text, timeout, check):
            del cwd, capture_output, text, timeout, check
            checkout_commands.append(command)
            checkout_path = Path(command[-1])
            checkout_path.mkdir(parents=True)
            (checkout_path / ".git").mkdir()
            (checkout_path / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\n"
                "testpaths = ['tests']\n",
                encoding="utf-8",
            )
            (checkout_path / "sample.py").write_text(
                "def ok():\n"
                "    return True\n",
                encoding="utf-8",
            )
            tests_dir = checkout_path / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_sample.py").write_text(
                "def test_sample():\n"
                "    assert True\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "cloned", "")

        def fake_setup_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            setup_commands.append((command, cwd))
            return subprocess.CompletedProcess(command, 0, "setup ok", "")

        def fake_test_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            test_commands.append((command, cwd))
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "ModuleNotFoundError: No module named 'requests'",
            )

        report = onboard_from_discovery(
            _discovery_payload_with_pyproject(raw_source),
            output_dir,
            recipes=["missing_len_zero_guard"],
            checkout_repository_tests=True,
            repository_checkout_runner=fake_checkout_runner,
            run_repository_test_retry_prerequisites=True,
            auto_repository_test_retry=True,
            auto_repository_test_retry_max_risk="high",
            auto_repository_test_retry_allowed_runners=["unittest"],
            repository_test_environment_setup_runner=fake_setup_runner,
            repository_test_execution_runner=fake_test_runner,
            repository_test_retry_execution_runner=fake_test_runner,
            repository_checkout_timeout=10,
            repository_test_timeout=10,
        )

        assert checkout_commands
        assert len(setup_commands) == 2
        assert len(test_commands) == 1
        assert report.repository_test_retry_plan["retry_command"].startswith(
            "python -m pytest"
        )
        assert report.repository_test_retry_execution_result["status"] == "skipped"
        assert (
            report.repository_test_retry_execution_result["reason"]
            == "execution_disabled"
        )
        assert (
            report.repository_test_retry_execution_result["retry_enabled_source"]
            == "disabled"
        )
        assert (
            report.repository_test_retry_execution_result[
                "auto_repository_test_retry_applied"
            ]
            is False
        )
        assert report.repository_test_retry_execution_result[
            "auto_repository_test_retry_allowed_runners"
        ] == ["unittest"]


def test_onboarding_checkout_uses_archive_fallback_after_git_failure(monkeypatch):
    monkeypatch.setattr(
        github_repository_checkout.shutil,
        "which",
        lambda name: "git",
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"
        checkout_commands = []
        planned_execution_commands = []

        def fake_checkout_runner(command, cwd, capture_output, text, timeout, check):
            del cwd, capture_output, text, timeout, check
            checkout_commands.append(command)
            return subprocess.CompletedProcess(command, 128, "", "connection reset")

        archive_payload = _archive_zip_bytes(
            {
                "project-main/pyproject.toml": (
                    "[tool.pytest.ini_options]\n"
                    "testpaths = ['tests']\n"
                ),
                "project-main/maths/average_mean.py": raw_source.read_text(
                    encoding="utf-8"
                ),
                "project-main/tests/test_smoke.py": (
                    "def test_checkout_smoke():\n"
                    "    assert True\n"
                ),
            }
        )

        def fake_archive_opener(request, timeout):
            del timeout
            assert request.full_url.endswith("/zip/v1.0.0")
            return _FakeResponse(archive_payload)

        def fake_test_runner(command, cwd, capture_output, text, timeout, check, env):
            del capture_output, text, timeout, check, env
            planned_execution_commands.append((command, cwd))
            return subprocess.CompletedProcess(command, 0, "1 passed", "")

        monkeypatch.setattr(
            github_repository_checkout.urllib.request,
            "urlopen",
            fake_archive_opener,
        )

        report = onboard_from_discovery(
            _discovery_payload_with_pyproject(raw_source),
            output_dir,
            recipes=["missing_len_zero_guard"],
            checkout_repository_tests=True,
            repository_checkout_runner=fake_checkout_runner,
            repository_test_execution_runner=fake_test_runner,
            repository_checkout_timeout=10,
            repository_test_timeout=10,
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )

        assert report.repository_checkout is not None
        assert report.repository_checkout["status"] == "pass"
        assert report.repository_checkout["reason"] == "archive_checkout_created"
        assert report.repository_checkout["checkout_method"] == "archive"
        assert report.repository_checkout_sources is not None
        assert report.repository_checkout_sources["discovery"][
            "included_file_count"
        ] >= 3
        assert report.repository_test_execution_plan is not None
        assert report.repository_test_execution_plan[
            "recommended_execution_command"
        ] == "python -m pytest -q tests"
        assert report.repository_test_execution_plan["executable_now"] is True
        assert report.repository_test_execution_result is not None
        assert report.repository_test_execution_result["status"] == "pass"
        assert run_config["repository_checkout"]["checkout_method"] == "archive"
        assert checkout_commands
        assert planned_execution_commands


def test_onboarding_augments_package_dependencies_before_benchmark():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        formatting, compat, parser = _write_click_formatting_package(root)
        output_dir = root / "onboarded_click"

        report = onboard_from_discovery(
            _click_dependency_discovery_payload(formatting, compat, parser),
            output_dir,
            recipes=["inplace_api_return_value"],
            target_prefix="click",
            materialize_template=True,
            run_benchmark=True,
            use_dynamic_coverage=False,
            run_quality_gate=True,
            run_showcase_lite=True,
            quality_gate_thresholds=OnboardingQualityGateThresholds(
                min_source_hit_rate=0.0,
            ),
        )
        template_payload = json.loads(
            Path(report.output_paths["template"]).read_text(encoding="utf-8")
        )
        template_case = template_payload["cases"][0]
        source_targets = [
            source["target_path"] for source in template_case["sources"]
        ]
        overlay_targets = [
            file["target_path"] for file in template_case["files"]
        ]
        test_content = next(
            file["content"]
            for file in template_case["files"]
            if file["target_path"] == "test_join_options_api.py"
        )

        assert report.generated_candidate_count == 1
        assert source_targets == [
            "click/formatting.py",
            "click/_compat.py",
            "click/parser.py",
        ]
        assert "click/__init__.py" in overlay_targets
        assert "from click.formatting import join_options" in test_content
        assert Path(report.output_paths["multi_source_augmentation_json"]).exists()
        assert Path(report.output_paths["multi_source_augmentation_markdown"]).exists()
        assert report.benchmark_run is not None
        assert report.benchmark_run["summary"]["case_count"] == 1
        assert report.benchmark_run["summary"]["top1"] == 1.0
        assert report.benchmark_run["summary"]["map"] == 1.0
        assert report.benchmark_run["summary"]["patch_success_rate"] == 1.0
        assert report.quality_gate is not None
        assert report.quality_gate["passed"] is True


def test_onboarding_infers_target_prefix_for_src_layout_package():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        init_source = root / "click_init.py"
        init_source.write_text("", encoding="utf-8")
        formatting = _write_standalone_join_options(root)

        report = onboard_from_discovery(
            {
                "files": [
                    _source_item(init_source, "src/click/__init__.py", "__init__.py"),
                    _source_item(
                        formatting,
                        "src/click/formatting.py",
                        "formatting.py",
                    ),
                ]
            },
            root / "onboarded_auto_target_prefix",
            recipes=["inplace_api_return_value"],
            auto_dependency_sources=False,
        )
        sources_payload = json.loads(
            Path(report.output_paths["sources"]).read_text(encoding="utf-8")
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )
        template_payload = json.loads(
            Path(report.output_paths["template"]).read_text(encoding="utf-8")
        )

        assert report.repository_profile["recommended_target_prefix"] == "click"
        assert report.quality_summary["target_prefix"] == "click"
        assert report.quality_summary["target_prefix_source"] == "auto_src_layout"
        assert run_config["limits"]["target_prefix"] == "click"
        assert run_config["limits"]["target_prefix_source"] == "auto_src_layout"
        assert [source["target_path"] for source in sources_payload["sources"]] == [
            "click/__init__.py",
            "click/formatting.py",
        ]
        assert template_payload["cases"][0]["sources"][0]["target_path"] == (
            "click/formatting.py"
        )


def test_onboarding_auto_dependency_sources_cover_click_style_import_chain():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        formatting, parser, exceptions, utils, globals_source = (
            _write_deep_click_formatting_package(root)
        )
        output_dir = root / "onboarded_click_deep"

        report = onboard_from_discovery(
            {
                "files": [
                    _click_source_item(
                        formatting, "src/click/formatting.py", "formatting.py"
                    ),
                    _click_source_item(parser, "src/click/parser.py", "parser.py"),
                    _click_source_item(
                        exceptions, "src/click/exceptions.py", "exceptions.py"
                    ),
                    _click_source_item(utils, "src/click/utils.py", "utils.py"),
                    _click_source_item(
                        globals_source, "src/click/globals.py", "globals.py"
                    ),
                ]
            },
            output_dir,
            include=["src/click/formatting.py"],
            recipes=["inplace_api_return_value"],
            target_prefix="click",
            materialize_template=True,
            run_benchmark=True,
            use_dynamic_coverage=False,
            run_quality_gate=True,
            run_showcase_lite=True,
            quality_gate_thresholds=OnboardingQualityGateThresholds(
                min_source_hit_rate=0.0,
            ),
        )
        template_payload = json.loads(
            Path(report.output_paths["template"]).read_text(encoding="utf-8")
        )
        source_targets = [
            source["target_path"] for source in template_payload["cases"][0]["sources"]
        ]
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )

        assert report.imported_source_count == 1
        assert report.quality_summary["dependency_source_count"] == 5
        assert source_targets == [
            "click/formatting.py",
            "click/parser.py",
            "click/exceptions.py",
            "click/utils.py",
            "click/globals.py",
        ]
        assert Path(report.output_paths["dependency_source_import_json"]).exists()
        assert Path(report.output_paths["dependency_source_import_markdown"]).exists()
        assert Path(report.output_paths["dependency_sources"]).exists()
        assert template_payload["cases"][0]["benchmark"]["metadata"][
            "dependency_max_depth"
        ] == 4
        assert run_config["limits"]["auto_dependency_sources"] is True
        assert run_config["limits"]["dependency_source_count"] == 5
        assert run_config["limits"]["dependency_max_depth"] == 4
        assert report.benchmark_run is not None
        assert report.benchmark_run["summary"]["patch_success_rate"] == 1.0
        assert report.quality_gate is not None
        assert report.quality_gate["passed"] is True


def test_onboarding_can_disable_auto_dependency_sources():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        formatting, parser, exceptions, utils, globals_source = (
            _write_deep_click_formatting_package(root)
        )
        output_dir = root / "onboarded_click_no_auto_deps"

        report = onboard_from_discovery(
            {
                "files": [
                    _click_source_item(
                        formatting, "src/click/formatting.py", "formatting.py"
                    ),
                    _click_source_item(parser, "src/click/parser.py", "parser.py"),
                    _click_source_item(
                        exceptions, "src/click/exceptions.py", "exceptions.py"
                    ),
                    _click_source_item(utils, "src/click/utils.py", "utils.py"),
                    _click_source_item(
                        globals_source, "src/click/globals.py", "globals.py"
                    ),
                ]
            },
            output_dir,
            include=["src/click/formatting.py"],
            recipes=["inplace_api_return_value"],
            target_prefix="click",
            auto_dependency_sources=False,
            run_quality_gate=True,
            quality_gate_thresholds=OnboardingQualityGateThresholds(
                require_benchmark_run=False,
            ),
        )
        template_payload = json.loads(
            Path(report.output_paths["template"]).read_text(encoding="utf-8")
        )
        source_targets = [
            source["target_path"] for source in template_payload["cases"][0]["sources"]
        ]
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )

        assert source_targets == ["click/formatting.py"]
        assert "dependency_sources" not in report.output_paths
        assert report.quality_summary["auto_dependency_sources"] is False
        assert report.quality_summary["dependency_source_count"] == 1
        assert run_config["limits"]["auto_dependency_sources"] is False


def test_onboarding_markdown_includes_next_step_command():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        report = onboard_from_discovery(
            _discovery_payload(raw_source),
            root / "onboarded",
            recipes=["missing_len_zero_guard"],
        )

        markdown = render_github_benchmark_onboarding_markdown(report)

        assert "# GitHub Benchmark Onboarding" in markdown
        assert "Generated Benchmark Candidates: 1" in markdown
        assert "run_template_benchmark" in markdown
        assert "source_mining_template.json" in markdown


def test_onboarding_limits_selected_sources_before_mining():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_sources = [
            _write_average_mean(root, filename="average_mean.py", function_name="mean"),
            _write_average_mean(
                root,
                filename="average_total.py",
                function_name="average_total",
            ),
        ]

        report = onboard_from_discovery(
            _multi_source_discovery_payload(raw_sources),
            root / "onboarded",
            recipes=["missing_len_zero_guard"],
            max_sources=1,
        )
        sources_payload = json.loads(
            Path(report.output_paths["sources"]).read_text(encoding="utf-8")
        )
        template_payload = json.loads(
            Path(report.output_paths["template"]).read_text(encoding="utf-8")
        )

        assert report.imported_source_count == 2
        assert report.selected_source_count == 1
        assert report.source_limit == 1
        assert len(sources_payload["sources"]) == 1
        assert report.generated_candidate_count == 1
        assert len(template_payload["cases"]) == 1


def test_onboarding_source_limit_prefers_directory_diversity():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_sources = [
            _write_average_mean(
                root,
                filename="average_a.py",
                function_name="average_a",
            ),
            _write_average_mean(
                root,
                filename="average_b.py",
                function_name="average_b",
            ),
            _write_average_mean(
                root,
                filename="average_c.py",
                function_name="average_c",
            ),
        ]

        report = onboard_from_discovery(
            _directory_diversity_discovery_payload(raw_sources),
            root / "onboarded",
            recipes=["missing_len_zero_guard"],
            max_sources=2,
        )
        sources_payload = json.loads(
            Path(report.output_paths["sources"]).read_text(encoding="utf-8")
        )
        selection_audit = json.loads(
            Path(report.output_paths["selection_audit_json"]).read_text(
                encoding="utf-8"
            )
        )
        selection_audit_markdown = Path(
            report.output_paths["selection_audit_markdown"]
        ).read_text(encoding="utf-8")
        selected_source_paths = [
            source["source_path"] for source in sources_payload["sources"]
        ]
        source_diversity = selection_audit["source_diversity"]

        assert report.imported_source_count == 3
        assert report.selected_source_count == 2
        assert report.quality_summary["source_limit_strategy"] == (
            "layout_recipe_aware_diversity"
        )
        assert report.quality_summary["source_limit_applied"] is True
        assert report.quality_summary["all_source_directory_count"] == 2
        assert report.quality_summary["selected_source_directory_count"] == 2
        assert report.quality_summary["source_directory_coverage"] == 1.0
        assert report.quality_summary["omitted_source_count"] == 1
        assert source_diversity["imported_source_count"] == 3
        assert source_diversity["selected_source_count"] == 2
        assert source_diversity["omitted_source_count"] == 1
        assert source_diversity["all_source_directory_count"] == 2
        assert source_diversity["selected_source_directory_count"] == 2
        assert source_diversity["source_directory_coverage"] == 1.0
        assert source_diversity["omitted_source_directory_counts"] == {"maths": 1}
        assert [
            source["directory"] for source in selection_audit["selected_sources"]
        ] == ["maths", "stats"]
        assert selected_source_paths == [
            "maths/average_a.py",
            "stats/average_c.py",
        ]
        assert "## Omitted Summary" in selection_audit_markdown


def test_onboarding_source_limit_prefers_recipe_hits_before_path_order():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        plain_source = _write_plain_add(root, filename="aaa_plain.py")
        formatting = _write_standalone_join_options(root)

        report = onboard_from_discovery(
            {
                "files": [
                    _click_source_item(
                        plain_source, "src/click/aaa_plain.py", "aaa_plain.py"
                    ),
                    _click_source_item(
                        formatting, "src/click/z_formatting.py", "formatting.py"
                    ),
                ]
            },
            root / "onboarded_recipe_aware",
            target_prefix="click",
            max_sources=1,
        )
        sources_payload = json.loads(
            Path(report.output_paths["sources"]).read_text(encoding="utf-8")
        )
        recipe_selection = json.loads(
            Path(report.output_paths["recipe_selection_json"]).read_text(
                encoding="utf-8"
            )
        )
        run_config = json.loads(
            Path(report.output_paths["run_config_json"]).read_text(encoding="utf-8")
        )

        assert report.imported_source_count == 2
        assert report.selected_source_count == 1
        assert report.generated_candidate_count == 1
        assert report.quality_summary["source_limit_strategy"] == (
            "layout_recipe_aware_diversity"
        )
        assert report.quality_summary["recipe_selection_mode"] == "auto_topk"
        assert report.quality_summary["selected_recipes"][0] == (
            "inplace_api_return_value"
        )
        assert "inplace_api_return_value" in recipe_selection["selected_recipes"]
        assert run_config["limits"]["recipe_selection_mode"] == "auto_topk"
        assert run_config["limits"]["selected_recipes"][0] == (
            "inplace_api_return_value"
        )
        assert Path(report.output_paths["recipe_selection_markdown"]).exists()
        assert sources_payload["sources"][0]["source_path"] == (
            "src/click/z_formatting.py"
        )


def test_onboarding_source_limit_prefers_package_code_over_tests():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        package_source = _write_average_mean(
            root,
            filename="package_average.py",
            function_name="package_average",
        )
        test_source = _write_average_mean(
            root,
            filename="test_average.py",
            function_name="test_average_helper",
        )

        report = onboard_from_discovery(
            {
                "files": [
                    _source_item(
                        test_source,
                        "tests/test_average.py",
                        "tests/test_average.py",
                    ),
                    _source_item(
                        package_source,
                        "src/pkg/average.py",
                        "pkg/average.py",
                    ),
                ]
            },
            root / "onboarded_layout_aware",
            recipes=["missing_len_zero_guard"],
            preserve_paths=True,
            max_sources=2,
        )
        sources_payload = json.loads(
            Path(report.output_paths["sources"]).read_text(encoding="utf-8")
        )
        selection_audit = json.loads(
            Path(report.output_paths["selection_audit_json"]).read_text(
                encoding="utf-8"
            )
        )
        selection_audit_markdown = Path(
            report.output_paths["selection_audit_markdown"]
        ).read_text(encoding="utf-8")

        assert report.imported_source_count == 2
        assert report.selected_source_count == 1
        assert report.source_limit == 2
        assert report.generated_candidate_count == 1
        assert report.quality_summary["source_limit_strategy"] == (
            "layout_recipe_aware_diversity"
        )
        assert sources_payload["sources"][0]["source_path"] == "src/pkg/average.py"
        assert report.quality_summary["omitted_source_directory_counts"] == {
            "tests": 1
        }
        selected = selection_audit["selected_sources"][0]
        omitted = selection_audit["omitted_sources_preview"][0]
        assert selected["source_path"] == "src/pkg/average.py"
        assert selected["preferred_mining_source"] is True
        assert selected["layout_score"] > 0
        assert selected["recipe_score"] > 0
        assert selected["total_score"] == (
            selected["layout_score"] + selected["recipe_score"]
        )
        assert omitted["source_path"] == "tests/test_average.py"
        assert omitted["preferred_mining_source"] is False
        assert omitted["layout_score"] < 0
        assert omitted["recipe_score"] > 0
        assert "Omitted Sources Preview" in selection_audit_markdown
        assert "Preferred" in selection_audit_markdown


def test_onboarding_limits_candidates_before_template_and_benchmark():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_sources = [
            _write_average_mean(root, filename="average_mean.py", function_name="mean"),
            _write_average_mean(
                root,
                filename="average_total.py",
                function_name="average_total",
            ),
        ]

        report = onboard_from_discovery(
            _multi_source_discovery_payload(raw_sources),
            root / "onboarded",
            recipes=["missing_len_zero_guard"],
            max_candidates=1,
            run_benchmark=True,
            use_dynamic_coverage=False,
        )
        catalog_payload = json.loads(
            Path(report.output_paths["catalog"]).read_text(encoding="utf-8")
        )
        template_payload = json.loads(
            Path(report.output_paths["template"]).read_text(encoding="utf-8")
        )
        candidate_sources = json.loads(
            Path(report.output_paths["candidate_sources"]).read_text(encoding="utf-8")
        )

        assert report.imported_source_count == 2
        assert report.selected_source_count == 2
        assert report.generated_candidate_count == 1
        assert report.candidate_limit == 1
        assert report.quality_summary["unlimited_candidate_count"] == 2
        assert report.quality_summary["candidate_limit_applied"] is True
        assert len(catalog_payload["candidates"]) == 1
        assert len(template_payload["cases"]) == 1
        assert len(candidate_sources["sources"]) == 1
        assert report.benchmark_run is not None
        assert report.benchmark_run["summary"]["case_count"] == 1


def test_onboarding_candidate_limit_prefers_rule_diversity():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_sources = [
            _write_average_mean(root, filename="average_mean.py", function_name="mean"),
            _write_average_mean(
                root,
                filename="average_total.py",
                function_name="average_total",
            ),
            _write_bubble_sort(root),
        ]

        report = onboard_from_discovery(
            _multi_source_discovery_payload(raw_sources),
            root / "onboarded",
            recipes=["missing_len_zero_guard", "possible_index_overrun"],
            max_candidates=2,
        )
        template_payload = json.loads(
            Path(report.output_paths["template"]).read_text(encoding="utf-8")
        )
        selection_audit = json.loads(
            Path(report.output_paths["selection_audit_json"]).read_text(
                encoding="utf-8"
            )
        )
        selection_audit_markdown = Path(
            report.output_paths["selection_audit_markdown"]
        ).read_text(encoding="utf-8")
        selected_rules = {
            rule_id
            for case in template_payload["cases"]
            for rule_id in case["benchmark"]["expected_rule_ids"]
        }
        candidate_diversity = selection_audit["candidate_diversity"]

        assert report.generated_candidate_count == 2
        assert report.quality_summary["candidate_limit_strategy"] == "diversity_greedy"
        assert report.quality_summary["unlimited_candidate_count"] == 3
        assert report.quality_summary["all_rule_count"] == 2
        assert report.quality_summary["selected_rule_count"] == 2
        assert report.quality_summary["candidate_rule_coverage"] == 1.0
        assert report.quality_summary["all_candidate_source_count"] == 3
        assert report.quality_summary["candidate_source_coverage"] == 0.666667
        assert report.quality_summary["omitted_candidate_count"] == 1
        assert candidate_diversity["unlimited_candidate_count"] == 3
        assert candidate_diversity["selected_candidate_count"] == 2
        assert candidate_diversity["omitted_candidate_count"] == 1
        assert candidate_diversity["all_rule_count"] == 2
        assert candidate_diversity["selected_rule_count"] == 2
        assert candidate_diversity["rule_coverage"] == 1.0
        assert candidate_diversity["omitted_rule_counts"] == {
            "missing_len_zero_guard": 1
        }
        assert set(candidate_diversity["rule_counts"]) == {
            "missing_len_zero_guard",
            "possible_index_overrun",
        }
        assert selected_rules == {
            "missing_len_zero_guard",
            "possible_index_overrun",
        }
        assert "## Omitted Summary" in selection_audit_markdown


def test_onboarding_quality_gate_checks_selection_diversity():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_sources = [
            _write_average_mean(
                root,
                filename="average_a.py",
                function_name="average_a",
            ),
            _write_average_mean(
                root,
                filename="average_b.py",
                function_name="average_b",
            ),
            _write_bubble_sort(root),
        ]

        report = onboard_from_discovery(
            _directory_diversity_discovery_payload(raw_sources),
            root / "onboarded",
            recipes=["missing_len_zero_guard", "possible_index_overrun"],
            max_sources=2,
            max_candidates=2,
        )

        passing = evaluate_onboarding_quality_gate(
            report,
            thresholds=OnboardingQualityGateThresholds(
                require_benchmark_run=False,
                min_selected_source_directories=2,
                min_selected_rules=2,
                min_selected_bug_types=2,
                min_source_directory_coverage=1.0,
                min_candidate_rule_coverage=1.0,
                min_candidate_bug_type_coverage=1.0,
                min_candidate_source_coverage=1.0,
            ),
        )
        failing = evaluate_onboarding_quality_gate(
            report,
            thresholds=OnboardingQualityGateThresholds(
                require_benchmark_run=False,
                min_selected_source_directories=3,
                min_selected_rules=3,
            ),
        )
        failing_by_name = {check.name: check for check in failing.checks}

        assert passing.passed is True
        assert failing.passed is False
        assert failing_by_name["selected_source_directories"].passed is False
        assert failing_by_name["selected_rules"].passed is False


def test_onboarding_quality_gate_checks_selection_coverage_ratios():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_sources = [
            _write_average_mean(root, filename="average_mean.py", function_name="mean"),
            _write_average_mean(
                root,
                filename="average_total.py",
                function_name="average_total",
            ),
            _write_bubble_sort(root),
        ]

        report = onboard_from_discovery(
            _multi_source_discovery_payload(raw_sources),
            root / "onboarded",
            recipes=["missing_len_zero_guard", "possible_index_overrun"],
            max_candidates=2,
        )

        result = evaluate_onboarding_quality_gate(
            report,
            thresholds=OnboardingQualityGateThresholds(
                require_benchmark_run=False,
                min_candidate_rule_coverage=1.0,
                min_candidate_bug_type_coverage=1.0,
                min_candidate_source_coverage=1.0,
            ),
        )
        checks_by_name = {check.name: check for check in result.checks}

        assert result.passed is False
        assert checks_by_name["candidate_rule_coverage"].actual == "1.0000"
        assert checks_by_name["candidate_rule_coverage"].passed is True
        assert checks_by_name["candidate_bug_type_coverage"].actual == "1.0000"
        assert checks_by_name["candidate_bug_type_coverage"].passed is True
        assert checks_by_name["candidate_source_coverage"].actual == "0.6667"
        assert checks_by_name["candidate_source_coverage"].passed is False


def test_onboarding_cli_from_discovery_writes_report():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        discovery = root / "discovery.json"
        output_dir = root / "onboarded"
        discovery.write_text(
            json.dumps(_discovery_payload(raw_source)),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_benchmark_onboarding",
                "from-discovery",
                str(discovery),
                str(output_dir),
                "--recipe",
                "missing_len_zero_guard",
                "--max-sources",
                "1",
                "--max-candidates",
                "1",
                "--format",
                "markdown",
                "--run-benchmark",
                "--no-dynamic-coverage",
                "--run-quality-gate",
                "--run-showcase-lite",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report_payload = json.loads(
            (output_dir / "onboarding_report.json").read_text(encoding="utf-8")
        )

        assert completed.returncode == 0
        assert "# GitHub Benchmark Onboarding" in completed.stdout
        assert "Benchmark Patch Success: 1.0000" in completed.stdout
        assert "Quality Gate: PASS" in completed.stdout
        assert "Showcase Lite: written" in completed.stdout
        assert "Source Coverage: groups=1.000" in completed.stdout
        assert "Candidate Coverage: rules=1.000" in completed.stdout
        assert report_payload["generated_candidate_count"] == 1
        assert report_payload["selected_source_count"] == 1
        assert report_payload["source_limit"] == 1
        assert report_payload["candidate_limit"] == 1
        assert report_payload["quality_summary"]["source_group_coverage"] == 1.0
        assert report_payload["quality_summary"]["candidate_rule_coverage"] == 1.0
        assert report_payload["benchmark_run"]["summary"]["patch_success_rate"] == 1.0
        assert report_payload["quality_gate"]["passed"] is True
        assert report_payload["showcase_lite"]["headline"]["benchmark_cases"] == 1
        assert report_payload["showcase_lite"]["headline"][
            "source_group_coverage"
        ] == 1.0
        assert report_payload["showcase_lite"]["headline"][
            "candidate_rule_coverage"
        ] == 1.0
        assert report_payload["run_config"]["preset"] == "manual"
        assert report_payload["run_config"]["actions"]["run_benchmark"] is True
        assert report_payload["run_config"]["actions"]["run_quality_gate"] is True
        assert "run_config_json" in report_payload["output_paths"]
        assert (output_dir / "onboarding_report.md").exists()
        assert (output_dir / "source_mining_template.json").exists()
        assert (output_dir / "benchmark_run" / "benchmark_report.json").exists()
        assert (output_dir / "onboarding_quality_gate.md").exists()
        assert (output_dir / "onboarding_showcase_lite.md").exists()
        assert (output_dir / "onboarding_run_config.json").exists()
        assert (output_dir / "onboarding_run_config.md").exists()


def test_onboarding_cli_smoke_preset_runs_end_to_end_artifacts():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        discovery = root / "discovery.json"
        output_dir = root / "onboarded"
        discovery.write_text(
            json.dumps(_discovery_payload(raw_source)),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_benchmark_onboarding",
                "from-discovery",
                str(discovery),
                str(output_dir),
                "--recipe",
                "missing_len_zero_guard",
                "--preset",
                "smoke",
                "--format",
                "markdown",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report_payload = json.loads(
            (output_dir / "onboarding_report.json").read_text(encoding="utf-8")
        )
        selection_audit = json.loads(
            (output_dir / "onboarding_selection_audit.json").read_text(
                encoding="utf-8"
            )
        )
        run_config = json.loads(
            (output_dir / "onboarding_run_config.json").read_text(encoding="utf-8")
        )

        assert completed.returncode == 0
        assert "Preset: `smoke`" in completed.stdout
        assert "Benchmark Patch Success: 1.0000" in completed.stdout
        assert "Quality Gate: PASS" in completed.stdout
        assert "Showcase Lite: written" in completed.stdout
        assert "Smoke Validation: PASS" in completed.stdout
        assert report_payload["preset"] == "smoke"
        assert report_payload["run_config"]["preset"] == "smoke"
        assert run_config["preset"] == "smoke"
        assert run_config["actions"] == {
            "materialize_template": True,
            "run_benchmark": True,
            "run_quality_gate": True,
            "run_showcase_lite": True,
            "run_smoke_validation": True,
            "run_repository_test_command": True,
            "run_repository_test_environment_setup": False,
            "run_repository_test_retry": False,
            "run_repository_test_retry_prerequisites": False,
            "auto_repository_test_retry": False,
            "auto_repository_test_retry_max_risk": "low",
            "auto_repository_test_retry_allowed_runners": [],
            "checkout_repository_tests": False,
        }
        assert run_config["benchmark"]["use_dynamic_coverage"] is False
        assert run_config["quality_gate"]["present"] is True
        assert run_config["quality_gate"]["passed"] is True
        assert run_config["smoke_validation"]["present"] is True
        assert run_config["smoke_validation"]["passed"] is True
        assert selection_audit["headline"]["preset"] == "smoke"
        assert report_payload["source_limit"] == 20
        assert report_payload["candidate_limit"] == 10
        assert report_payload["benchmark_run"]["summary"]["case_count"] == 1
        assert report_payload["quality_gate"]["passed"] is True
        assert report_payload["showcase_lite"]["headline"]["benchmark_cases"] == 1
        assert report_payload["showcase_lite"]["headline"]["preset"] == "smoke"
        assert report_payload["smoke_validation"]["passed"] is True
        assert "smoke_validation_json" in report_payload["output_paths"]
        assert (output_dir / "materialized" / "manifest.json").exists()
        assert (output_dir / "benchmark_run" / "benchmark_report.json").exists()
        assert (output_dir / "onboarding_quality_gate.json").exists()
        assert (output_dir / "onboarding_showcase_lite.json").exists()
        assert (output_dir / "onboarding_smoke_validation.json").exists()
        assert (output_dir / "onboarding_smoke_validation.md").exists()
        assert (output_dir / "onboarding_run_config.json").exists()
        assert (output_dir / "onboarding_run_config.md").exists()


def test_onboarding_cli_mining_preset_skips_benchmark_requirement():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        discovery = root / "discovery.json"
        output_dir = root / "onboarded"
        discovery.write_text(
            json.dumps(_discovery_payload(raw_source)),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_benchmark_onboarding",
                "from-discovery",
                str(discovery),
                str(output_dir),
                "--recipe",
                "missing_len_zero_guard",
                "--preset",
                "mining",
                "--format",
                "markdown",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report_payload = json.loads(
            (output_dir / "onboarding_report.json").read_text(encoding="utf-8")
        )
        selection_audit = json.loads(
            (output_dir / "onboarding_selection_audit.json").read_text(
                encoding="utf-8"
            )
        )
        run_config = json.loads(
            (output_dir / "onboarding_run_config.json").read_text(encoding="utf-8")
        )
        check_names = {
            check["name"] for check in report_payload["quality_gate"]["checks"]
        }

        assert completed.returncode == 0
        assert "Preset: `mining`" in completed.stdout
        assert "Quality Gate: PASS" in completed.stdout
        assert "Showcase Lite: written" in completed.stdout
        assert report_payload["preset"] == "mining"
        assert report_payload["run_config"]["preset"] == "mining"
        assert run_config["preset"] == "mining"
        assert run_config["actions"] == {
            "materialize_template": False,
            "run_benchmark": False,
            "run_quality_gate": True,
            "run_showcase_lite": True,
            "run_smoke_validation": False,
            "run_repository_test_command": True,
            "run_repository_test_environment_setup": False,
            "run_repository_test_retry": False,
            "run_repository_test_retry_prerequisites": False,
            "auto_repository_test_retry": False,
            "auto_repository_test_retry_max_risk": "low",
            "auto_repository_test_retry_allowed_runners": [],
            "checkout_repository_tests": False,
        }
        assert run_config["quality_gate"]["thresholds"][
            "require_benchmark_run"
        ] is False
        assert selection_audit["headline"]["preset"] == "mining"
        assert report_payload["source_limit"] == 50
        assert report_payload["candidate_limit"] == 20
        assert report_payload["benchmark_run"] is None
        assert report_payload["showcase_lite"]["headline"]["preset"] == "mining"
        assert report_payload["quality_gate"]["thresholds"][
            "require_benchmark_run"
        ] is False
        assert "benchmark_run_present" not in check_names
        assert not (output_dir / "benchmark_run").exists()
        assert (output_dir / "onboarding_run_config.json").exists()


def test_onboarding_cli_repo_url_uses_tree_onboarding_with_mining_preset():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"
        opener = _FakeOpener(
            [
                {"default_branch": "main"},
                {
                    "sha": "abc123",
                    "tree": [
                        {
                            "path": "maths/average_mean.py",
                            "type": "blob",
                            "raw_url": str(raw_source),
                            "sha256": hashlib.sha256(
                                raw_source.read_bytes()
                            ).hexdigest(),
                            "license": "MIT",
                        }
                    ],
                }
            ]
        )

        onboarding_main(
            [
                "repo",
                "https://github.com/example/project.git",
                str(output_dir),
                "--recipe",
                "missing_len_zero_guard",
                "--preset",
                "mining",
                "--no-require-ready-for-benchmark",
                "--format",
                "json",
            ],
            opener=opener,
        )
        report_payload = json.loads(
            (output_dir / "onboarding_report.json").read_text(encoding="utf-8")
        )
        run_config = json.loads(
            (output_dir / "onboarding_run_config.json").read_text(encoding="utf-8")
        )
        discovery_fetch = json.loads(
            (output_dir / "onboarding_discovery_fetch.json").read_text(
                encoding="utf-8"
            )
        )

        assert opener.urls == [
            "https://api.github.com/repos/example/project",
            "https://api.github.com/repos/example/project/git/trees/main?recursive=1"
        ]
        assert report_payload["mode"] == "tree"
        assert report_payload["source"] == "github-tree:example/project@main"
        assert report_payload["requested_urls"] == opener.urls
        assert report_payload["discovery_metadata"]["requested_ref"] is None
        assert report_payload["discovery_metadata"]["ref_source"] == "default_branch"
        assert report_payload["discovery_metadata"]["recursive"] is True
        assert report_payload["import_report"]["source_entries"][0]["ref"] == "main"
        assert report_payload["preset"] == "mining"
        assert report_payload["selected_source_count"] == 1
        assert report_payload["generated_candidate_count"] == 1
        assert report_payload["source_limit"] == 50
        assert report_payload["candidate_limit"] == 20
        assert report_payload["quality_gate"]["passed"] is True
        assert report_payload["quality_gate"]["thresholds"][
            "require_benchmark_run"
        ] is False
        assert run_config["preset"] == "mining"
        assert run_config["mode"] == "tree"
        assert run_config["discovery"]["requested_url_count"] == 2
        assert run_config["discovery"]["metadata"]["ref_source"] == "default_branch"
        assert run_config["actions"]["run_quality_gate"] is True
        assert (
            run_config["resolved_artifacts"]["discovery_fetch_json"]
            == str(output_dir / "onboarding_discovery_fetch.json")
        )
        assert discovery_fetch["mode"] == "tree"
        assert discovery_fetch["requested_urls"] == opener.urls
        assert discovery_fetch["import_report"]["source_count"] == 1
        assert (output_dir / "onboarding_selection_audit.json").exists()
        assert (output_dir / "onboarding_showcase_lite.json").exists()
        assert (output_dir / "onboarding_discovery_fetch.md").exists()


def test_onboarding_cli_repo_url_uses_ref_inferred_from_tree_url():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        output_dir = root / "onboarded"
        opener = _FakeOpener(
            [
                {
                    "sha": "dev123",
                    "tree": [
                        {"path": "pyproject.toml", "type": "blob"},
                        {
                            "path": "maths/average_mean.py",
                            "type": "blob",
                            "raw_url": str(raw_source),
                            "sha256": hashlib.sha256(
                                raw_source.read_bytes()
                            ).hexdigest(),
                            "license": "MIT",
                        },
                    ],
                }
            ]
        )

        onboarding_main(
            [
                "repo",
                "https://github.com/example/project/tree/develop",
                str(output_dir),
                "--recipe",
                "missing_len_zero_guard",
                "--preset",
                "mining",
                "--no-require-ready-for-benchmark",
                "--format",
                "json",
            ],
            opener=opener,
        )
        report_payload = json.loads(
            (output_dir / "onboarding_report.json").read_text(encoding="utf-8")
        )

        assert opener.urls == [
            "https://api.github.com/repos/example/project/git/trees/develop?recursive=1"
        ]
        assert report_payload["source"] == "github-tree:example/project@develop"
        assert report_payload["discovery_metadata"]["ref"] == "develop"
        assert report_payload["discovery_metadata"]["requested_ref"] == "develop"
        assert report_payload["discovery_metadata"]["ref_source"] == "explicit"
        assert report_payload["import_report"]["source_entries"][0]["ref"] == (
            "develop"
        )


def test_onboarding_cli_repo_url_reports_github_rate_limit_without_traceback(capsys):
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir) / "onboarded"

        with pytest.raises(SystemExit) as exc_info:
            onboarding_main(
                [
                    "repo",
                    "https://github.com/example/project",
                    str(output_dir),
                    "--ref",
                    "main",
                    "--preset",
                    "mining",
                    "--format",
                    "json",
                ],
                opener=_FailingHTTPErrorOpener(
                    status=403,
                    reason="rate limit exceeded",
                    body={"message": "API rate limit exceeded"},
                    headers={"X-RateLimit-Remaining": "0"},
                ),
            )
        captured = capsys.readouterr()

        assert exc_info.value.code == 1
        assert "HTTP 403" in captured.err
        assert "GITHUB_TOKEN" in captured.err
        assert "from-discovery" in captured.err
        assert "Traceback" not in captured.err
        assert not (output_dir / "onboarding_report.json").exists()


def test_onboarding_diagnostics_explains_raw_source_read_failure():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        missing_source = root / "missing_average_mean.py"
        output_dir = root / "onboarded"

        report = onboard_from_discovery(
            _single_source_discovery_payload(
                missing_source,
                source_path="maths/missing_average_mean.py",
                target_path="missing_average_mean.py",
            ),
            output_dir,
            recipes=["missing_len_zero_guard"],
            run_quality_gate=True,
            quality_gate_thresholds=OnboardingQualityGateThresholds(
                require_benchmark_run=False,
            ),
        )
        diagnostics = json.loads(
            Path(report.output_paths["diagnostics_json"]).read_text(encoding="utf-8")
        )
        diagnostics_markdown = Path(
            report.output_paths["diagnostics_markdown"]
        ).read_text(encoding="utf-8")
        issue_codes = {issue["code"] for issue in diagnostics["issues"]}

        assert report.generated_candidate_count == 0
        assert diagnostics["headline"]["status"] == "fail"
        assert diagnostics["headline"]["first_failing_stage"] == "source_mining"
        assert "source_read_errors" in issue_codes
        assert "no_generated_candidates" in issue_codes
        assert diagnostics["source_read_errors"][0]["target_path"] == (
            "missing_average_mean.py"
        )
        assert diagnostics["source_read_errors"][0]["reasons"][0].startswith(
            "source_read_error="
        )
        assert "--source-cache-dir" in "\n".join(diagnostics["next_actions"])
        assert "source_read_errors" in diagnostics_markdown


def test_onboarding_diagnostics_explains_recipe_misses_without_read_error():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_plain_add(root)
        output_dir = root / "onboarded"

        report = onboard_from_discovery(
            _single_source_discovery_payload(
                raw_source,
                source_path="maths/plain_add.py",
                target_path="plain_add.py",
            ),
            output_dir,
            recipes=["missing_len_zero_guard"],
            run_quality_gate=True,
            quality_gate_thresholds=OnboardingQualityGateThresholds(
                require_benchmark_run=False,
            ),
        )
        diagnostics = json.loads(
            Path(report.output_paths["diagnostics_json"]).read_text(encoding="utf-8")
        )
        issue_codes = {issue["code"] for issue in diagnostics["issues"]}

        assert report.generated_candidate_count == 0
        assert diagnostics["headline"]["status"] == "fail"
        assert "no_generated_candidates" in issue_codes
        assert diagnostics["source_read_errors"] == []
        assert diagnostics["recipe_misses"][0]["target_path"] == "plain_add.py"
        assert diagnostics["recipe_misses"][0]["reasons"]
        assert diagnostics["recipe_suggestions"][0]["recipe"] == (
            "missing_len_zero_guard"
        )
        assert diagnostics["recipe_suggestions"][0]["top_reasons"][0]["reason"] == (
            "no_empty_guard_len_denominator_function"
        )
        assert any(
            "empty-input guards" in action
            for action in diagnostics["recipe_suggestions"][0]["suggested_actions"]
        )
        assert "Inspect source_mining.md" in "\n".join(diagnostics["next_actions"])
        diagnostics_markdown = Path(
            report.output_paths["diagnostics_markdown"]
        ).read_text(encoding="utf-8")
        assert "Recipe Suggestions" in diagnostics_markdown
        assert "missing_len_zero_guard" in diagnostics_markdown


def test_onboarding_cli_applies_quality_gate_diversity_thresholds():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        discovery = root / "discovery.json"
        output_dir = root / "onboarded"
        discovery.write_text(
            json.dumps(_discovery_payload(raw_source)),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_benchmark_onboarding",
                "from-discovery",
                str(discovery),
                str(output_dir),
                "--recipe",
                "missing_len_zero_guard",
                "--format",
                "markdown",
                "--run-benchmark",
                "--no-dynamic-coverage",
                "--run-quality-gate",
                "--min-selected-rules",
                "2",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report_payload = json.loads(
            (output_dir / "onboarding_report.json").read_text(encoding="utf-8")
        )
        checks_by_name = {
            check["name"]: check
            for check in report_payload["quality_gate"]["checks"]
        }

        assert completed.returncode == 0
        assert "Quality Gate: FAIL" in completed.stdout
        assert report_payload["quality_gate"]["thresholds"]["min_selected_rules"] == 2
        assert report_payload["quality_gate"]["passed"] is False
        assert checks_by_name["selected_rules"]["actual"] == "1"
        assert checks_by_name["selected_rules"]["passed"] is False


def test_onboarding_cli_applies_quality_gate_coverage_thresholds():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_sources = [
            _write_average_mean(root, filename="average_mean.py", function_name="mean"),
            _write_average_mean(
                root,
                filename="average_total.py",
                function_name="average_total",
            ),
            _write_bubble_sort(root),
        ]
        discovery = root / "discovery.json"
        output_dir = root / "onboarded"
        discovery.write_text(
            json.dumps(_multi_source_discovery_payload(raw_sources)),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_benchmark_onboarding",
                "from-discovery",
                str(discovery),
                str(output_dir),
                "--recipe",
                "missing_len_zero_guard",
                "--recipe",
                "possible_index_overrun",
                "--max-candidates",
                "2",
                "--format",
                "markdown",
                "--run-quality-gate",
                "--no-require-benchmark-run",
                "--min-candidate-source-coverage",
                "1.0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report_payload = json.loads(
            (output_dir / "onboarding_report.json").read_text(encoding="utf-8")
        )
        checks_by_name = {
            check["name"]: check
            for check in report_payload["quality_gate"]["checks"]
        }

        assert completed.returncode == 0
        assert "Quality Gate: FAIL" in completed.stdout
        assert report_payload["quality_gate"]["thresholds"][
            "min_candidate_source_coverage"
        ] == 1.0
        assert report_payload["quality_gate"]["passed"] is False
        assert checks_by_name["candidate_source_coverage"]["actual"] == "0.6667"
        assert checks_by_name["candidate_source_coverage"]["passed"] is False


def test_onboarding_cli_applies_benchmark_quality_thresholds():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        discovery = root / "discovery.json"
        output_dir = root / "onboarded"
        discovery.write_text(
            json.dumps(_discovery_payload(raw_source)),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_benchmark_onboarding",
                "from-discovery",
                str(discovery),
                str(output_dir),
                "--recipe",
                "missing_len_zero_guard",
                "--format",
                "markdown",
                "--run-benchmark",
                "--no-dynamic-coverage",
                "--run-quality-gate",
                "--min-benchmark-cases",
                "2",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report_payload = json.loads(
            (output_dir / "onboarding_report.json").read_text(encoding="utf-8")
        )
        checks_by_name = {
            check["name"]: check
            for check in report_payload["quality_gate"]["checks"]
        }

        assert completed.returncode == 0
        assert "Quality Gate: FAIL" in completed.stdout
        assert report_payload["quality_gate"]["thresholds"]["min_benchmark_cases"] == 2
        assert report_payload["quality_gate"]["passed"] is False
        assert checks_by_name["benchmark_cases"]["actual"] == "1"
        assert checks_by_name["benchmark_cases"]["passed"] is False


def test_onboarding_cli_rejects_invalid_quality_gate_thresholds():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        discovery = root / "discovery.json"
        output_dir = root / "onboarded"
        discovery.write_text(
            json.dumps(_discovery_payload(raw_source)),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_benchmark_onboarding",
                "from-discovery",
                str(discovery),
                str(output_dir),
                "--recipe",
                "missing_len_zero_guard",
                "--run-quality-gate",
                "--min-patch-success-rate",
                "1.1",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode != 0
        assert "min_patch_success_rate must be between 0.0 and 1.0" in (
            completed.stderr + completed.stdout
        )
        assert not (output_dir / "onboarding_report.json").exists()


def test_onboarding_cli_allows_mining_only_quality_gate():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = _write_average_mean(root)
        discovery = root / "discovery.json"
        output_dir = root / "onboarded"
        discovery.write_text(
            json.dumps(_discovery_payload(raw_source)),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_benchmark_onboarding",
                "from-discovery",
                str(discovery),
                str(output_dir),
                "--recipe",
                "missing_len_zero_guard",
                "--format",
                "markdown",
                "--run-quality-gate",
                "--no-require-benchmark-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report_payload = json.loads(
            (output_dir / "onboarding_report.json").read_text(encoding="utf-8")
        )
        check_names = {
            check["name"] for check in report_payload["quality_gate"]["checks"]
        }

        assert completed.returncode == 0
        assert "Quality Gate: PASS" in completed.stdout
        assert report_payload["benchmark_run"] is None
        assert report_payload["quality_gate"]["thresholds"][
            "require_benchmark_run"
        ] is False
        assert "benchmark_run_present" not in check_names


class _FakeResponse:
    def __init__(self, payload):
        if isinstance(payload, bytes):
            self.payload = payload
        elif isinstance(payload, str):
            self.payload = payload.encode("utf-8")
        else:
            self.payload = json.dumps(payload).encode("utf-8")
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.payload) - self.offset
        start = self.offset
        end = min(len(self.payload), start + size)
        self.offset = end
        return self.payload[start:end]


class _FakeOpener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.urls = []
        self.headers = []

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        self.headers.append(dict(request.header_items()))
        return _FakeResponse(self.payloads.pop(0))


class _FailingHTTPErrorOpener:
    def __init__(self, *, status, reason, body, headers=None):
        self.status = status
        self.reason = reason
        self.body = body
        self.headers = headers or {}

    def __call__(self, request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            self.status,
            self.reason,
            self.headers,
            io.BytesIO(json.dumps(self.body).encode("utf-8")),
        )


def _discovery_payload(raw_source: Path) -> dict:
    digest = hashlib.sha256(raw_source.read_bytes()).hexdigest()
    return {
        "files": [
            {
                "path": "maths/average_mean.py",
                "raw_url": str(raw_source),
                "target_path": "average_mean.py",
                "owner": "example",
                "repo": "algorithms",
                "ref": "v1.0.0",
                "sha256": digest,
                "license": "MIT",
            },
            {
                "path": "README.md",
                "raw_url": str(raw_source),
                "target_path": "README.md",
            },
        ]
    }


def _discovery_payload_with_pyproject(raw_source: Path) -> dict:
    payload = _discovery_payload(raw_source)
    payload["files"].append(
        {
            "path": "pyproject.toml",
            "owner": "example",
            "repo": "algorithms",
            "ref": "v1.0.0",
        }
    )
    return payload


def _discovery_payload_with_ci_config(raw_source: Path) -> dict:
    payload = _discovery_payload(raw_source)
    payload["owner"] = "example"
    payload["repo"] = "algorithms"
    payload["ref"] = "v1.0.0"
    workflow = (
        "name: tests\n"
        "jobs:\n"
        "  test:\n"
        "    strategy:\n"
        "      matrix:\n"
        "        python-version: ['3.11']\n"
        "    steps:\n"
        "      - uses: actions/setup-python@v5\n"
        "      - run: python -m pytest --tb=short tests\n"
    )
    tox = (
        "[tox]\n"
        "envlist = py311\n"
        "\n"
        "[testenv]\n"
        "commands = python -m pytest --tb=short tests\n"
    )
    payload["files"].extend(
        [
            {
                "path": ".github/workflows/tests.yml",
                "content": workflow,
                "size": len(workflow),
                "owner": "example",
                "repo": "algorithms",
                "ref": "v1.0.0",
            },
            {
                "path": "tox.ini",
                "content": tox,
                "size": len(tox),
                "owner": "example",
                "repo": "algorithms",
                "ref": "v1.0.0",
            },
        ]
    )
    return payload


def _multi_source_discovery_payload(raw_sources: list[Path]) -> dict:
    files = []
    for index, raw_source in enumerate(raw_sources):
        digest = hashlib.sha256(raw_source.read_bytes()).hexdigest()
        files.append(
            {
                "path": f"maths/{raw_source.name}",
                "raw_url": str(raw_source),
                "target_path": raw_source.name,
                "owner": "example",
                "repo": "algorithms",
                "ref": "v1.0.0",
                "sha256": digest,
                "license": "MIT",
                "source_index": index,
            }
        )
    return {"files": files}


def _single_source_discovery_payload(
    raw_source: Path,
    *,
    source_path: str,
    target_path: str,
) -> dict:
    item = {
        "path": source_path,
        "raw_url": str(raw_source),
        "target_path": target_path,
        "owner": "example",
        "repo": "algorithms",
        "ref": "v1.0.0",
        "license": "MIT",
    }
    if raw_source.exists():
        item["sha256"] = hashlib.sha256(raw_source.read_bytes()).hexdigest()
    return {"files": [item]}


def _directory_diversity_discovery_payload(raw_sources: list[Path]) -> dict:
    source_paths = [
        "maths/average_a.py",
        "maths/average_b.py",
        "stats/average_c.py",
    ]
    files = []
    for raw_source, source_path in zip(raw_sources, source_paths, strict=True):
        digest = hashlib.sha256(raw_source.read_bytes()).hexdigest()
        files.append(
            {
                "path": source_path,
                "raw_url": str(raw_source),
                "target_path": raw_source.name,
                "owner": "example",
                "repo": "algorithms",
                "ref": "v1.0.0",
                "sha256": digest,
                "license": "MIT",
            }
        )
    return {"files": files}


def _click_dependency_discovery_payload(
    formatting: Path,
    compat: Path,
    parser: Path,
) -> dict:
    return {
        "files": [
            _click_source_item(formatting, "src/click/formatting.py", "formatting.py"),
            _click_source_item(compat, "src/click/_compat.py", "_compat.py"),
            _click_source_item(parser, "src/click/parser.py", "parser.py"),
        ]
    }


def _click_source_item(raw_source: Path, source_path: str, target_path: str) -> dict:
    return {
        "path": source_path,
        "raw_url": str(raw_source),
        "target_path": target_path,
        "owner": "pallets",
        "repo": "click",
        "ref": "8.1.7",
        "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
        "license": "BSD-3-Clause",
    }


def _source_item(raw_source: Path, source_path: str, target_path: str) -> dict:
    return {
        "path": source_path,
        "raw_url": str(raw_source),
        "target_path": target_path,
        "owner": "example",
        "repo": "layout",
        "ref": "main",
        "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
        "license": "MIT",
    }


def _write_average_mean(
    root: Path,
    *,
    filename: str = "average_mean.py",
    function_name: str = "mean",
) -> Path:
    raw_source = root / filename
    raw_source.write_text(
        f"def {function_name}(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    return raw_source


class _FakeBenchmarkReport:
    def to_dict(self):
        return {
            "summary": {
                "case_count": 1,
                "top1": 1.0,
                "map": 1.0,
                "patch_success_rate": 1.0,
            },
            "cases": [],
        }


def _archive_zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return buffer.getvalue()


def _write_click_formatting_package(root: Path) -> tuple[Path, Path, Path]:
    compat = root / "_compat.py"
    compat.write_text(
        "def term_len(value):\n"
        "    return len(str(value))\n",
        encoding="utf-8",
    )
    parser = root / "parser.py"
    parser.write_text(
        "def split_opt(opt):\n"
        "    if opt.startswith('--'):\n"
        "        return '--', opt[2:]\n"
        "    return opt[:1], opt[1:]\n",
        encoding="utf-8",
    )
    formatting = root / "formatting.py"
    formatting.write_text(
        "from ._compat import term_len\n"
        "from .parser import split_opt\n\n\n"
        "def join_options(options):\n"
        "    rv = []\n"
        "    any_prefix_is_slash = False\n"
        "    for opt in options:\n"
        "        prefix, value = split_opt(opt)\n"
        "        if prefix == '/':\n"
        "            any_prefix_is_slash = True\n"
        "        rv.append((len(prefix), opt))\n"
        "    rv.sort(key=lambda x: x[0])\n"
        "    return ', '.join(x[1] for x in rv), any_prefix_is_slash\n",
        encoding="utf-8",
    )
    return formatting, compat, parser


def _write_deep_click_formatting_package(
    root: Path,
) -> tuple[Path, Path, Path, Path, Path]:
    globals_source = root / "globals.py"
    globals_source.write_text(
        "def resolve_color_default(value=None):\n"
        "    return value\n",
        encoding="utf-8",
    )
    utils = root / "utils.py"
    utils.write_text(
        "from .globals import resolve_color_default\n\n\n"
        "def echo(message):\n"
        "    resolve_color_default(None)\n"
        "    return message\n",
        encoding="utf-8",
    )
    exceptions = root / "exceptions.py"
    exceptions.write_text(
        "from .utils import echo\n\n\n"
        "class BadArgumentUsage(Exception):\n"
        "    pass\n\n\n"
        "def format_message(message):\n"
        "    return echo(message)\n",
        encoding="utf-8",
    )
    parser = root / "parser.py"
    parser.write_text(
        "from .exceptions import BadArgumentUsage\n\n\n"
        "def split_opt(opt):\n"
        "    if not opt:\n"
        "        raise BadArgumentUsage('empty option')\n"
        "    if opt.startswith('--'):\n"
        "        return '--', opt[2:]\n"
        "    return opt[:1], opt[1:]\n",
        encoding="utf-8",
    )
    formatting = root / "formatting.py"
    formatting.write_text(
        "from .parser import split_opt\n\n\n"
        "def join_options(options):\n"
        "    rv = []\n"
        "    any_prefix_is_slash = False\n"
        "    for opt in options:\n"
        "        prefix, value = split_opt(opt)\n"
        "        if prefix == '/':\n"
        "            any_prefix_is_slash = True\n"
        "        rv.append((len(prefix), opt))\n"
        "    rv.sort(key=lambda x: x[0])\n"
        "    return ', '.join(x[1] for x in rv), any_prefix_is_slash\n",
        encoding="utf-8",
    )
    return formatting, parser, exceptions, utils, globals_source


def _write_bubble_sort(root: Path) -> Path:
    raw_source = root / "bubble_sort.py"
    raw_source.write_text(
        "def bubble_sort_recursive(collection):\n"
        "    length = len(collection)\n"
        "    for i in range(length - 1):\n"
        "        if collection[i] > collection[i + 1]:\n"
        "            collection[i], collection[i + 1] = collection[i + 1], collection[i]\n"
        "    if length <= 1:\n"
        "        return collection\n"
        "    return bubble_sort_recursive(collection[:-1]) + [collection[-1]]\n",
        encoding="utf-8",
    )
    return raw_source


def _write_standalone_join_options(root: Path) -> Path:
    raw_source = root / "standalone_formatting.py"
    raw_source.write_text(
        "def join_options(options):\n"
        "    rv = []\n"
        "    any_prefix_is_slash = False\n"
        "    for opt in options:\n"
        "        prefix = opt[:2] if opt.startswith('--') else opt[:1]\n"
        "        if prefix == '/':\n"
        "            any_prefix_is_slash = True\n"
        "        rv.append((len(prefix), opt))\n"
        "    rv.sort(key=lambda x: x[0])\n"
        "    return ', '.join(x[1] for x in rv), any_prefix_is_slash\n",
        encoding="utf-8",
    )
    return raw_source


def _write_plain_add(root: Path, *, filename: str = "plain_add.py") -> Path:
    raw_source = root / filename
    raw_source.write_text(
        "def add(left, right):\n"
        "    return left + right\n",
        encoding="utf-8",
    )
    return raw_source
