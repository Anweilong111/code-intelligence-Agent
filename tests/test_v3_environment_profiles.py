from __future__ import annotations

import json
import subprocess

from code_intelligence_agent.evaluation.v3_environment_profiles import (
    collect_environment_profiles,
)


def test_environment_profile_capture_binds_cases_and_omits_local_paths(tmp_path):
    executable = tmp_path / "outputs" / "runtime" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("fixture", encoding="utf-8")

    manifest, audit = collect_environment_profiles(
        _config(),
        _catalog(),
        root=tmp_path,
        runner=_runner,
    )

    assert audit["status"] == "pass", audit["errors"]
    assert audit["bound_case_count"] == 1
    profile = manifest["profiles"][0]
    assert profile["python_version"] == "3.11.9"
    assert profile["packages"] == [
        {"name": "pip", "version": "24.0"},
        {"name": "pytest", "version": "8.3.5"},
    ]
    assert len(profile["profile_sha256"]) == 64
    assert str(tmp_path) not in json.dumps(manifest)


def test_environment_profile_capture_rejects_runtime_version_mismatch(tmp_path):
    executable = tmp_path / "outputs" / "runtime" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("fixture", encoding="utf-8")

    config = _config()
    config["profiles"][0]["expected_python_version"] = "3.10.0"
    manifest, audit = collect_environment_profiles(
        config,
        _catalog(),
        root=tmp_path,
        runner=_runner,
    )

    assert audit["status"] == "fail"
    assert any("python_version_mismatch" in item for item in audit["errors"])
    assert manifest["profiles"] == []


def test_environment_profile_capture_rejects_catalog_binding_mismatch(tmp_path):
    executable = tmp_path / "outputs" / "runtime" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("fixture", encoding="utf-8")
    catalog = _catalog()
    catalog["cases"][0]["environment_profile_id"] = "another-profile"

    _, audit = collect_environment_profiles(
        _config(),
        catalog,
        root=tmp_path,
        runner=_runner,
    )

    assert "case_profile_id_mismatch:bugsinpy-demo-1" in audit["errors"]


def _runner(command, **kwargs):
    del kwargs
    if "-c" in command:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "python_version": "3.11.9",
                    "implementation": "cpython",
                    "architecture": "AMD64",
                    "platform": "win32",
                }
            ),
            stderr="",
        )
    return subprocess.CompletedProcess(
        command,
        0,
        stdout=json.dumps(
            [
                {"name": "pytest", "version": "8.3.5"},
                {"name": "pip", "version": "24.0"},
            ]
        ),
        stderr="",
    )


def _config() -> dict:
    return {
        "schema_version": "3.0",
        "platform_scope": "win32-amd64",
        "profiles": [
            {
                "profile_id": "demo-py3.11.9",
                "runtime_relative_dir": "outputs/runtime",
                "expected_python_version": "3.11.9",
                "case_ids": ["bugsinpy-demo-1"],
            }
        ],
    }


def _catalog() -> dict:
    return {
        "schema_version": "3.0",
        "catalog_sha256": "a" * 64,
        "cases": [
            {
                "case_id": "bugsinpy-demo-1",
                "python_version": "3.11.9",
                "environment_profile_id": "demo-py3.11.9",
            }
        ],
    }
