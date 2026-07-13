import json

import pytest

from code_intelligence_agent.evaluation.v1_onboarding_slice_aggregate import (
    build_v1_onboarding_slice_aggregate,
    main,
    render_v1_onboarding_slice_aggregate_markdown,
)


def test_v1_onboarding_slice_aggregate_reports_progress_and_next_slice():
    manifest = {
        "runs": [
            {"name": "case_0", "repo": "owner/repo0", "scenario_tags": ["v1_onboarding"]},
            {"name": "case_1", "repo": "owner/repo1", "scenario_tags": ["v1_onboarding", "pytest_project"]},
            {"name": "case_2", "repo": "owner/repo2", "scenario_tags": ["v1_onboarding"]},
        ]
    }
    suite = {
        "summary": {"suite_slice_applied": True},
        "runs": [
            {
                "name": "case_0",
                "repo": "owner/repo0",
                "output_dir": "out/case_0",
                "status": "pass",
                "passed": True,
                "elapsed_ms": 100,
            },
            {
                "name": "case_1",
                "repo": "owner/repo1",
                "output_dir": "out/case_1",
                "status": "pass",
                "passed": True,
                "elapsed_ms": 200,
            },
        ],
    }

    aggregate = build_v1_onboarding_slice_aggregate(
        manifest,
        [suite],
        manifest_path="manifest.json",
        suite_paths=["slice_0_2.json"],
    )
    markdown = render_v1_onboarding_slice_aggregate_markdown(aggregate)

    assert aggregate["status"] == "partial"
    assert aggregate["summary"]["completed_count"] == 2
    assert aggregate["summary"]["missing_count"] == 1
    assert aggregate["summary"]["agent_passed_count"] == 2
    assert aggregate["summary"]["suite_slice_applied"] is True
    assert aggregate["summary"]["suite_run_elapsed_ms_average"] == 150.0
    assert aggregate["summary"]["next_missing_start_index"] == 2
    assert aggregate["missing_runs"] == [
        {"manifest_index": 2, "name": "case_2", "repo": "owner/repo2"}
    ]
    assert "Run next onboarding slice with --start-index 2 --limit-runs 1." in (
        aggregate["next_actions"]
    )
    assert "case_2" in markdown


def test_v1_onboarding_slice_aggregate_marks_complete_when_all_pass():
    manifest = {
        "runs": [
            {"name": "case_0", "repo": "owner/repo0"},
            {"name": "case_1", "repo": "owner/repo1"},
        ]
    }
    aggregate = build_v1_onboarding_slice_aggregate(
        manifest,
        [
            {
                "runs": [
                    {"name": "case_0", "passed": True, "status": "pass", "elapsed_ms": 100},
                    {"name": "case_1", "passed": True, "status": "pass", "elapsed_ms": 300},
                ]
            }
        ],
    )

    assert aggregate["status"] == "complete"
    assert aggregate["summary"]["suite_slice_applied"] is False
    assert aggregate["summary"]["run_count"] == 2
    assert aggregate["summary"]["manifest_run_count"] == 2
    assert aggregate["summary"]["suite_run_elapsed_ms_average"] == 200.0
    assert aggregate["failed_runs"] == []
    assert aggregate["missing_runs"] == []


def test_v1_onboarding_slice_aggregate_does_not_mark_full_failure_as_slice():
    manifest = {
        "runs": [
            {"name": "case_0", "repo": "owner/repo0"},
            {"name": "case_1", "repo": "owner/repo1"},
        ]
    }
    aggregate = build_v1_onboarding_slice_aggregate(
        manifest,
        [
            {
                "runs": [
                    {"name": "case_0", "passed": True, "status": "pass"},
                    {
                        "name": "case_1",
                        "passed": False,
                        "status": "failed",
                        "error": "objective compliance failed",
                    },
                ]
            }
        ],
    )

    assert aggregate["status"] == "partial"
    assert aggregate["summary"]["suite_slice_applied"] is False
    assert aggregate["summary"]["completed_count"] == 2
    assert aggregate["summary"]["missing_count"] == 0
    assert aggregate["summary"]["failed_count"] == 1


def test_v1_onboarding_slice_aggregate_cli_writes_artifacts(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.json"
    suite_path = tmp_path / "slice.json"
    output_dir = tmp_path / "out"
    manifest_path.write_text(
        json.dumps({"runs": [{"name": "case_0", "repo": "owner/repo"}]}),
        encoding="utf-8",
    )
    suite_path.write_text(
        json.dumps({"runs": [{"name": "case_0", "passed": True, "status": "pass"}]}),
        encoding="utf-8",
    )

    main([str(manifest_path), str(output_dir), str(suite_path), "--format", "markdown", "--require-complete"])
    stdout = capsys.readouterr().out

    assert "V1 Onboarding Slice Aggregate" in stdout
    assert (output_dir / "v1_onboarding_slice_aggregate.json").exists()
    assert (output_dir / "v1_onboarding_slice_aggregate.md").exists()

    suite_path.write_text(json.dumps({"runs": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main([str(manifest_path), str(output_dir), str(suite_path), "--require-complete"])
    assert exc.value.code == 1
