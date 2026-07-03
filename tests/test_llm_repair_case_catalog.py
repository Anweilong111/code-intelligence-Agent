import json

import pytest

from code_intelligence_agent.evaluation.llm_repair_case_catalog import (
    build_llm_repair_case_catalog_audit,
    main,
    render_llm_repair_case_catalog_audit_markdown,
)


def test_llm_repair_case_catalog_flags_missing_cases_and_sources(tmp_path):
    existing_report = tmp_path / "source_suite.json"
    existing_report.write_text(json.dumps({"suite_name": "source"}), encoding="utf-8")
    catalog_path = tmp_path / "catalog.json"
    catalog = {
        "name": "p6_small_catalog",
        "targets": _small_targets(case_count=4, agent_loop_trace_complete=3),
        "cases": [
            {
                "case_id": "direct_guard",
                "repo": "example/direct",
                "expected_class": "llm_direct_success",
                "source_report_path": "source_suite.json",
            },
            {
                "case_id": "reflection_guard",
                "repo": "example/reflection",
                "expected_class": "llm_reflection_success",
                "source_report_path": "source_suite.json",
            },
            {
                "case_id": "missing_key_blocker",
                "repo": "example/blocker",
                "expected_class": "llm_blocker",
                "expected_blocker_category": "llm_failed_blocker",
                "source_report_path": "source_suite.json",
            },
            {
                "case_id": "planned_missing_case",
                "repo": "example/planned",
                "expected_class": "llm_blocker",
                "expected_blocker_category": "environment_blocker",
                "source_report_path": "missing_suite.json",
            },
        ],
    }
    matrix = {
        "matrix": [
            _matrix_row("direct_guard", "llm_direct_success"),
            _matrix_row("reflection_guard", "llm_reflection_success"),
            _matrix_row(
                "missing_key_blocker",
                "llm_blocker",
                blocker_category="llm_failed_blocker",
            ),
        ]
    }

    audit = build_llm_repair_case_catalog_audit(
        catalog,
        matrix,
        catalog_path=str(catalog_path),
    )
    markdown = render_llm_repair_case_catalog_audit_markdown(audit)

    assert audit["status"] == "incomplete"
    assert audit["summary"]["declared_case_count"] == 4
    assert audit["summary"]["matched_case_count"] == 3
    assert audit["summary"]["missing_case_count"] == 1
    assert audit["summary"]["missing_source_report_count"] == 1
    assert "matched_repair_case_count" in audit["missing"]
    assert "all_declared_cases_matched" in audit["missing"]
    assert "missing_source_report_count" in audit["missing"]
    cases = {case["case_id"]: case for case in audit["cases"]}
    assert cases["planned_missing_case"]["matched"] is False
    assert cases["planned_missing_case"]["notes"] == ["matrix_row_missing"]
    assert "planned_missing_case" in markdown
    assert "sk-" not in json.dumps(audit)


def test_llm_repair_case_catalog_passes_when_matrix_evidence_matches(tmp_path):
    source_report = tmp_path / "source_suite.json"
    source_report.write_text(json.dumps({"suite_name": "source"}), encoding="utf-8")
    catalog_path = tmp_path / "catalog.json"
    cases = []
    rows = []
    for index in range(5):
        name = f"direct_{index}"
        cases.append(_case(name, "llm_direct_success"))
        rows.append(_matrix_row(name, "llm_direct_success", judge_accept=True))
    for index in range(3):
        name = f"reflection_{index}"
        cases.append(_case(name, "llm_reflection_success"))
        rows.append(
            _matrix_row(
                name,
                "llm_reflection_success",
                judge_accept=True,
                judge_reject=True,
            )
        )
    blocker_categories = (
        ["llm_failed_blocker"] * 3
        + ["environment_blocker"] * 3
        + ["no_test_oracle_blocker"] * 3
        + ["safety_gate_blocker"] * 3
    )
    for index, category in enumerate(blocker_categories):
        name = f"blocker_{index}"
        cases.append(_case(name, "llm_blocker", blocker_category=category))
        rows.append(_matrix_row(name, "llm_blocker", blocker_category=category))
    catalog = {
        "name": "p6_complete_catalog",
        "source_reports": ["source_suite.json"],
        "cases": cases,
    }

    audit = build_llm_repair_case_catalog_audit(
        catalog,
        {"matrix": rows},
        catalog_path=str(catalog_path),
    )

    assert audit["status"] == "pass"
    assert audit["summary"]["declared_case_count"] == 20
    assert audit["summary"]["matched_case_count"] == 20
    assert audit["counts"]["llm_direct_success_count"] == 5
    assert audit["counts"]["llm_reflection_success_count"] == 3
    assert audit["counts"]["llm_blocker_count"] == 12
    assert audit["counts"]["patch_judge_llm_ready_case_count"] == 8
    assert audit["counts"]["patch_judge_accept_success_count"] == 8
    assert audit["counts"]["patch_judge_reject_failure_count"] == 3
    assert audit["counts"]["agent_loop_trace_complete_count"] == 20
    assert audit["missing"] == []


def test_llm_repair_case_catalog_cli_writes_artifacts_and_fails_on_require_pass(
    tmp_path,
    capsys,
):
    catalog_path = tmp_path / "catalog.json"
    matrix_path = tmp_path / "llm_repair_evaluation_matrix.json"
    output_dir = tmp_path / "audit"
    catalog_path.write_text(
        json.dumps(
            {
                "name": "cli_catalog",
                "matrix_path": "llm_repair_evaluation_matrix.json",
                "targets": _small_targets(case_count=1, agent_loop_trace_complete=1),
                "cases": [
                    {
                        "case_id": "direct_guard",
                        "expected_class": "llm_direct_success",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    matrix_path.write_text(
        json.dumps({"matrix": [_matrix_row("direct_guard", "llm_direct_success")]}),
        encoding="utf-8",
    )

    main([str(catalog_path), str(output_dir), "--format", "json"])
    stdout = capsys.readouterr().out
    payload = json.loads(
        (output_dir / "llm_repair_case_catalog_audit.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["status"] == "pass"
    assert output_dir.joinpath("llm_repair_case_catalog_audit.md").exists()
    assert "llm_repair_case_catalog_targets_met" in stdout

    catalog_path.write_text(
        json.dumps(
            {
                "name": "cli_catalog_incomplete",
                "matrix_path": "llm_repair_evaluation_matrix.json",
                "cases": [
                    {
                        "case_id": "missing",
                        "expected_class": "llm_direct_success",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        main([str(catalog_path), str(output_dir), "--require-pass"])
    assert exc.value.code == 1


def _case(
    name: str,
    expected_class: str,
    *,
    blocker_category: str = "",
) -> dict:
    return {
        "case_id": name,
        "repo": f"example/{name}",
        "expected_class": expected_class,
        "expected_blocker_category": blocker_category,
        "source_report_path": "source_suite.json",
    }


def _matrix_row(
    name: str,
    class_name: str,
    *,
    blocker_category: str = "",
    judge_accept: bool = False,
    judge_reject: bool = False,
) -> dict:
    return {
        "name": name,
        "repo": f"example/{name}",
        "class": class_name,
        "evidence_status": "complete",
        "blocker_category": blocker_category,
        "report_path": f"out/{name}/github_repo_intelligence.json",
        "patch_judge_mode": "llm" if judge_accept or judge_reject else "none",
        "patch_judge_status": "ready" if judge_accept or judge_reject else "none",
        "patch_judge_candidate_count": 1 if judge_accept or judge_reject else 0,
        "patch_judge_outcome_counts": {
            **({"accept_success": 1} if judge_accept else {}),
            **({"reject_failure": 1} if judge_reject else {}),
        },
        "agent_loop_evidence": {
            "observe": f"Observed {name}.",
            "plan": "Plan repair action.",
            "act": "Run repair action.",
            "verify": "Validate patch in sandbox.",
            "reflect": "Record verification feedback.",
            "replan": "Emit report or next blocker.",
        },
    }


def _small_targets(*, case_count: int, agent_loop_trace_complete: int) -> dict:
    return {
        "case_count": case_count,
        "llm_direct_success": 1,
        "llm_reflection_success": 1 if case_count > 1 else 0,
        "llm_blocker": 1 if case_count > 1 else 0,
        "llm_direct_evidence_complete": 1,
        "llm_reflection_evidence_complete": 1 if case_count > 1 else 0,
        "llm_blocker_evidence_complete": 1 if case_count > 1 else 0,
        "llm_patch_judge_ready": 0,
        "llm_patch_judge_accept_success": 0,
        "llm_patch_judge_reject_failure": 0,
        "llm_failed_blocker": 1 if case_count > 1 else 0,
        "environment_blocker": 0,
        "no_test_oracle_blocker": 0,
        "safety_gate_blocker": 0,
        "agent_loop_trace_complete": agent_loop_trace_complete,
    }
