from __future__ import annotations

import hashlib
import json
from pathlib import Path

from code_intelligence_agent.evaluation import v3_semantic_evaluation as evaluation
from code_intelligence_agent.evaluation.v3_repair_evaluation import (
    _v3_trial_implementation_sha256,
)


def test_human_fix_candidate_uses_only_declared_production_source(tmp_path):
    bug_root = tmp_path / "bug"
    fix_root = tmp_path / "fix"
    old = "\nVALUE = 1\n"
    new = "\nVALUE = 2\n"
    _write(bug_root / "package" / "module.py", old)
    _write(fix_root / "package" / "module.py", new)
    case = {"ground_truth": {"source_files": ["package/module.py"]}}

    candidate, regions, audit = evaluation._human_fix_module_candidate(
        case,
        bug_root=bug_root,
        fix_root=fix_root,
    )

    assert audit["status"] == "pass"
    assert audit["ground_truth_source_files_used"] is True
    assert [region.path for region in regions] == ["package/module.py"]
    assert regions[0].source == old.rstrip("\n")
    assert candidate["files"][0]["replacement"] == new.rstrip("\n")
    assert candidate["allow_signature_change"] is False


def test_human_fix_candidate_rejects_test_and_non_python_files(tmp_path):
    bug_root = tmp_path / "bug"
    fix_root = tmp_path / "fix"
    for relative in ("tests/test_module.py", "spec/check.py", "README.md"):
        _write(bug_root / relative, "old\n")
        _write(fix_root / relative, "new\n")
    case = {
        "ground_truth": {
            "source_files": [
                "tests/test_module.py",
                "spec/check.py",
                "README.md",
            ],
            "test_files": ["spec/check.py"],
        }
    }

    candidate, regions, audit = evaluation._human_fix_module_candidate(
        case,
        bug_root=bug_root,
        fix_root=fix_root,
    )

    assert candidate["files"] == []
    assert regions == []
    assert audit["status"] == "fail"
    assert audit["errors"] == [
        "non_production_python_source_not_allowed:README.md",
        "declared_test_source_not_allowed:spec/check.py",
        "non_production_python_source_not_allowed:tests/test_module.py",
    ]


def test_semantic_calibration_release_is_lf_stable_and_labels_oracle(tmp_path):
    payload = {
        "status": "pass",
        "case_count": 1,
        "pass_count": 1,
        "false_rejection_count": 0,
        "blocker_count": 0,
        "reverse_mutation_killed_count": 1,
        "reverse_mutation_count": 1,
        "cases": [
            {
                "case_id": "case-1",
                "repository": "owner/repo",
                "source_file_count": 1,
                "semantic_status": "pass",
                "status": "pass",
                "human_fix_oracle_used": True,
                "agent_repair_claim": False,
            }
        ],
        "claim_boundary": "Human fix content is calibration-only.",
    }

    paths = evaluation.write_v3_semantic_calibration_release(payload, tmp_path)

    json_bytes = Path(paths["json"]).read_bytes()
    markdown_bytes = Path(paths["markdown"]).read_bytes()
    assert b"\r\n" not in json_bytes
    assert b"\r\n" not in markdown_bytes
    assert json.loads(json_bytes)["cases"][0]["agent_repair_claim"] is False
    assert b"do not count as Agent repair successes" in markdown_bytes


def test_semantic_evaluation_defaults_to_runtime_source_config():
    args = evaluation.build_arg_parser().parse_args(
        ["output", "--case-id", "case-1"]
    )

    normalized = args.environment_profiles.replace("\\", "/")
    assert normalized.endswith(
        "datasets/v3_real_bugs/environment_profile_sources.json"
    )


def test_committed_phase5_verification_hashes_current_artifacts():
    root = Path(__file__).resolve().parents[1]
    verification = json.loads(
        (root / "docs" / "v3" / "phase5_verification.json").read_text(
            encoding="utf-8"
        )
    )
    calibration = json.loads(
        (root / "docs" / "v3" / "phase5_semantic_calibration.json").read_text(
            encoding="utf-8"
        )
    )
    artifacts = {
        "phase5_semantic_calibration.json": (
            root / "docs" / "v3" / "phase5_semantic_calibration.json"
        ),
        "phase5_semantic_calibration.md": (
            root / "docs" / "v3" / "phase5_semantic_calibration.md"
        ),
        "phase5_semantic_validation_protocol.md": (
            root / "docs" / "v3" / "phase5_semantic_validation_protocol.md"
        ),
        "v3_semantic_validation.py": (
            root
            / "code_intelligence_agent"
            / "evaluation"
            / "v3_semantic_validation.py"
        ),
        "v3_semantic_evaluation.py": (
            root
            / "code_intelligence_agent"
            / "evaluation"
            / "v3_semantic_evaluation.py"
        ),
    }

    assert verification["status"] == "pass"
    assert verification["implementation"]["trial_implementation_sha256"] == (
        _v3_trial_implementation_sha256()
    )
    assert calibration["status"] == "pass"
    assert "not Agent-generated repairs" in calibration["claim_boundary"]
    assert all(case["agent_repair_claim"] is False for case in calibration["cases"])
    assert all(
        _sha256(path) == verification["artifacts"][name]
        for name, path in artifacts.items()
    )
    for name in (
        "phase5_semantic_calibration.json",
        "phase5_semantic_calibration.md",
        "phase5_semantic_validation_protocol.md",
    ):
        assert b"\r\n" not in artifacts[name].read_bytes()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
