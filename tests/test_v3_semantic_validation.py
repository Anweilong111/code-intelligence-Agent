from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation import v3_semantic_validation as semantic
from code_intelligence_agent.evaluation.v3_repair_trial import EditableRegion
from code_intelligence_agent.tools.boundary_probe import BoundaryProbeResult


def test_semantic_validation_runs_differential_mutation_and_property_gates(
    tmp_path,
    monkeypatch,
):
    old = "def repair(value):\n    return value\n"
    new = "def repair(value):\n    return value + 1\n"
    seed, patched = _repositories(tmp_path, {"app.py": old}, {"app.py": new})
    region = _region("app.py", "repair", old)
    candidate = _candidate(region, new)
    case = _case()
    case["semantic_validation"] = {
        "commands": [
            {
                "kind": "property",
                "command": [
                    "{python}",
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_property.py",
                ],
            }
        ]
    }
    monkeypatch.setattr(semantic, "execute_test_commands", _fake_test_execution)

    result = semantic.validate_v3_semantic_candidate(
        candidate,
        editable_regions=[region],
        seed_repository=seed,
        patched_repository=patched,
        case=case,
        python_executable="python",
        targeted_timeout=10,
        regression_timeout=10,
    )

    assert result["status"] == "pass", result
    assert result["claim_eligible"] is True
    checks = {row["check_id"]: row for row in result["checks"]}
    assert checks["target_behavior_differential"]["status"] == "pass"
    assert checks["target_behavior_differential"]["patched_execution_reused"] is False
    assert checks["reverse_mutation_sensitivity"]["killed_mutation_count"] == 1
    assert checks["manifest_semantic_commands"]["status"] == "pass"


def test_semantic_validation_rejects_public_api_signature_change(
    tmp_path,
    monkeypatch,
):
    old = "def repair(value):\n    return value\n"
    new = "def repair(value, offset=1):\n    return value + offset\n"
    seed, patched = _repositories(tmp_path, {"app.py": old}, {"app.py": new})
    region = _region("app.py", "repair", old)
    monkeypatch.setattr(semantic, "execute_test_commands", _fake_test_execution)

    result = semantic.validate_v3_semantic_candidate(
        _candidate(region, new),
        editable_regions=[region],
        seed_repository=seed,
        patched_repository=patched,
        case=_case(),
        python_executable="python",
        targeted_timeout=10,
        regression_timeout=10,
    )

    assert result["status"] == "fail"
    assert result["claim_eligible"] is False
    contract = next(
        row for row in result["checks"] if row["check_id"] == "api_contract_compatibility"
    )
    assert contract["status"] == "fail"
    assert contract["changed_contracts"] == ["app.py::repair"]


@pytest.mark.parametrize(
    ("probe_status", "semantic_status"),
    (("fail", "fail"), ("blocker", "blocker")),
)
def test_executed_generated_boundary_probe_is_authoritative(
    tmp_path,
    monkeypatch,
    probe_status,
    semantic_status,
):
    old = "def repair(value):\n    return value\n"
    new = "def repair(value):\n    return value + 1\n"
    seed, patched = _repositories(tmp_path, {"app.py": old}, {"app.py": new})
    region = _region("app.py", "repair", old)
    candidate = _candidate(region, new)
    candidate["semantic_rule_ids"] = ["possible_index_overrun"]
    monkeypatch.setattr(semantic, "execute_test_commands", _fake_test_execution)
    monkeypatch.setattr(
        semantic,
        "run_boundary_probe",
        lambda *args, **kwargs: BoundaryProbeResult(
            status=probe_status,
            reason="forbidden_boundary_exception_observed",
            rule_id="possible_index_overrun",
            case_count=1,
        ),
    )

    result = semantic.validate_v3_semantic_candidate(
        candidate,
        editable_regions=[region],
        seed_repository=seed,
        patched_repository=patched,
        case=_case(),
        python_executable="python",
        targeted_timeout=10,
        regression_timeout=10,
    )

    assert result["status"] == semantic_status
    boundary = next(
        row
        for row in result["checks"]
        if row["check_id"] == "generated_boundary_property_probe"
    )
    assert boundary["required"] is True
    assert boundary["status"] == probe_status


def test_semantic_validation_rejects_edit_that_survives_complete_oracle(
    tmp_path,
    monkeypatch,
):
    app_old = "def repair(value):\n    return value\n"
    app_new = "def repair(value):\n    return value + 1\n"
    helper_old = "def helper(value):\n    return value\n"
    helper_new = "def helper(value):\n    return value + 0\n"
    seed, patched = _repositories(
        tmp_path,
        {"app.py": app_old, "helper.py": helper_old},
        {"app.py": app_new, "helper.py": helper_new},
    )
    app_region = _region("app.py", "repair", app_old)
    helper_region = _region("helper.py", "helper", helper_old)
    candidate = {
        "files": [
            _file_edit(app_region, app_new),
            _file_edit(helper_region, helper_new),
        ],
        "risk": "low",
    }
    monkeypatch.setattr(semantic, "execute_test_commands", _fake_test_execution)

    result = semantic.validate_v3_semantic_candidate(
        candidate,
        editable_regions=[app_region, helper_region],
        seed_repository=seed,
        patched_repository=patched,
        case=_case(),
        python_executable="python",
        targeted_timeout=10,
        regression_timeout=10,
    )

    assert result["status"] == "fail"
    mutation = next(
        row
        for row in result["checks"]
        if row["check_id"] == "reverse_mutation_sensitivity"
    )
    assert mutation["surviving_mutation_count"] == 1
    survivor = next(row for row in mutation["mutations"] if row["status"] == "fail")
    assert survivor["path"] == "helper.py"
    assert survivor["reason"] == "reverse_mutation_survived_complete_test_oracle"


def test_semantic_validation_blocks_unsafe_manifest_command(
    tmp_path,
    monkeypatch,
):
    old = "def repair(value):\n    return value\n"
    new = "def repair(value):\n    return value + 1\n"
    seed, patched = _repositories(tmp_path, {"app.py": old}, {"app.py": new})
    region = _region("app.py", "repair", old)
    case = _case()
    case["semantic_validation"] = {
        "commands": [
            {
                "kind": "property",
                "command": ["{python}", "-m", "http.server"],
            }
        ]
    }
    monkeypatch.setattr(semantic, "execute_test_commands", _fake_test_execution)

    result = semantic.validate_v3_semantic_candidate(
        _candidate(region, new),
        editable_regions=[region],
        seed_repository=seed,
        patched_repository=patched,
        case=case,
        python_executable="python",
        targeted_timeout=10,
        regression_timeout=10,
    )

    assert result["status"] == "blocker"
    command_check = next(
        row
        for row in result["checks"]
        if row["check_id"] == "manifest_semantic_commands"
    )
    assert command_check["status"] == "blocker"
    assert command_check["commands"][0]["reason"] == (
        "semantic_command_module_not_allowed"
    )


def test_semantic_validation_without_complete_test_oracle_is_not_claim_eligible(
    tmp_path,
    monkeypatch,
):
    old = "def repair(value):\n    return value\n"
    new = "def repair(value):\n    return value + 1\n"
    seed, patched = _repositories(tmp_path, {"app.py": old}, {"app.py": new})
    region = _region("app.py", "repair", old)
    case = _case()
    case["targeted_test_commands"] = []
    case["regression_command"] = []
    monkeypatch.setattr(semantic, "execute_test_commands", _fake_test_execution)

    result = semantic.validate_v3_semantic_candidate(
        _candidate(region, new),
        editable_regions=[region],
        seed_repository=seed,
        patched_repository=patched,
        case=case,
        python_executable="python",
        targeted_timeout=10,
        regression_timeout=10,
    )

    assert result["status"] == "not_applicable"
    assert result["claim_eligible"] is False
    assert set(result["incomplete_check_ids"]) == {
        "target_behavior_differential",
        "reverse_mutation_sensitivity",
    }


def test_workspace_consistency_rejects_removed_symbol_still_imported(tmp_path):
    module_old = "TOKEN = 1\n"
    module_new = "VALUE = 1\n"
    consumer = "from package.constants import TOKEN\n"
    seed, patched = _repositories(
        tmp_path,
        {
            "package/constants.py": module_old,
            "package/consumer.py": consumer,
        },
        {
            "package/constants.py": module_new,
            "package/consumer.py": consumer,
        },
    )
    region = _module_region("package/constants.py", module_old)

    result = semantic._workspace_consistency_check(
        _candidate(region, module_new),
        editable_regions=[region],
        seed=seed,
        patched=patched,
    )

    assert result["status"] == "fail"
    assert result["cross_file_broken_import_count"] == 1
    assert result["broken_imports"] == [
        {
            "importer": "package/consumer.py",
            "module": "package.constants",
            "symbol": "TOKEN",
        }
    ]


def test_workspace_consistency_rejects_candidate_not_reflected(tmp_path):
    old = "def repair(value):\n    return value\n"
    replacement = "def repair(value):\n    return value + 1\n"
    seed, patched = _repositories(tmp_path, {"app.py": old}, {"app.py": old})
    region = _region("app.py", "repair", old)

    result = semantic._workspace_consistency_check(
        _candidate(region, replacement),
        editable_regions=[region],
        seed=seed,
        patched=patched,
    )

    assert result["status"] == "fail"
    assert "files[0].replacement_not_reflected" in result["errors"]


def test_safe_file_rejects_symlink_target(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    real = root / "real.py"
    real.write_text("VALUE = 1\n", encoding="utf-8")
    link = root / "linked.py"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    assert semantic._safe_file(root, "linked.py") is None


def test_safe_file_rejects_parent_traversal(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()

    assert semantic._safe_file(root, "../outside.py") is None
    assert semantic._safe_file(root, "C:/outside.py") is None
    assert semantic._safe_file(root, "bad\x00path.py") is None


def test_target_differential_rejects_when_patched_target_still_fails(
    tmp_path,
    monkeypatch,
):
    old = "def repair(value):\n    return value\n"
    new = "def repair(value):\n    return value + 1\n"
    seed, patched = _repositories(tmp_path, {"app.py": old}, {"app.py": new})
    region = _region("app.py", "repair", old)
    monkeypatch.setattr(semantic, "execute_test_commands", _always_failing_execution)

    result = semantic.validate_v3_semantic_candidate(
        _candidate(region, new),
        editable_regions=[region],
        seed_repository=seed,
        patched_repository=patched,
        case=_case(),
        python_executable="python",
        targeted_timeout=10,
        regression_timeout=10,
    )

    assert result["status"] == "fail"
    differential = next(
        row
        for row in result["checks"]
        if row["check_id"] == "target_behavior_differential"
    )
    assert differential["reason"] == (
        "patched_workspace_does_not_pass_targeted_test"
    )


def _repositories(
    tmp_path: Path,
    seed_files: dict[str, str],
    patched_files: dict[str, str],
) -> tuple[Path, Path]:
    seed = tmp_path / "seed"
    patched = tmp_path / "patched"
    seed.mkdir()
    patched.mkdir()
    for relative, source in seed_files.items():
        path = seed / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    for relative, source in patched_files.items():
        path = patched / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return seed, patched


def _region(path: str, name: str, source: str) -> EditableRegion:
    return EditableRegion(
        path=path,
        function_id=f"{path}::{name}",
        function_name=name,
        start_line=1,
        end_line=len(source.splitlines()),
        rank=1,
        score=1.0,
        original_sha256=hashlib.sha256(source.strip("\n").encode("utf-8")).hexdigest(),
        source=source.strip("\n"),
    )


def _module_region(path: str, source: str) -> EditableRegion:
    normalized = source.rstrip("\n")
    return EditableRegion(
        path=path,
        function_id=f"{path}::<module>",
        function_name="<module>",
        start_line=1,
        end_line=len(source.splitlines()),
        rank=1,
        score=1.0,
        original_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        source=normalized,
        region_kind="module",
    )


def _candidate(region: EditableRegion, replacement: str) -> dict:
    return {
        "files": [_file_edit(region, replacement)],
        "risk": "low",
    }


def _file_edit(region: EditableRegion, replacement: str) -> dict:
    return {
        "path": region.path,
        "original_sha256": region.original_sha256,
        "replacement": replacement.strip("\n"),
        "function_id": region.function_id,
        "function_name": region.function_name,
        "start_line": region.start_line,
        "end_line": region.end_line,
    }


def _case() -> dict:
    return {
        "case_id": "semantic-case",
        "targeted_test_commands": [
            ["{python}", "-m", "pytest", "-q", "tests/test_target.py"]
        ],
        "regression_command": ["{python}", "-m", "pytest", "-q"],
        "test_environment": {},
    }


def _fake_test_execution(
    commands,
    *,
    repository_root,
    python_executable,
    timeout,
    test_environment,
):
    del commands, python_executable, timeout, test_environment
    source = (Path(repository_root) / "app.py").read_text(encoding="utf-8")
    passed = "return value + 1" in source
    return {
        "status": "pass" if passed else "fail",
        "reason": "tests_passed" if passed else "tests_failed",
        "environment_blocker": False,
        "results": [
            {
                "status": "pass" if passed else "fail",
                "returncode": 0 if passed else 1,
                "test_count": 1,
                "passed": 1 if passed else 0,
                "failed": 0 if passed else 1,
                "timeout": False,
            }
        ],
    }


def _always_failing_execution(
    commands,
    *,
    repository_root,
    python_executable,
    timeout,
    test_environment,
):
    del commands, repository_root, python_executable, timeout, test_environment
    return {
        "status": "fail",
        "reason": "tests_failed",
        "environment_blocker": False,
        "results": [
            {
                "status": "fail",
                "returncode": 1,
                "test_count": 1,
                "passed": 0,
                "failed": 1,
                "timeout": False,
            }
        ],
    }
