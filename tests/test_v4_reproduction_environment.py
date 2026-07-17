from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation import v4_reproduction_environment
from code_intelligence_agent.evaluation.v4_reproduction_environment import (
    build_environment_bootstrap_plan,
    environment_bootstrap_plan_fingerprint,
    environment_bootstrap_result_fingerprint,
    execute_environment_bootstrap,
    validate_bootstrap_requirements,
    validate_environment_bootstrap_plan,
    validate_manual_python_archives,
)


def test_bootstrap_requirements_require_exact_registry_versions():
    assert validate_bootstrap_requirements(
        ["pytest==8.3.1", "requests==2.32.3"]
    ) == []

    errors = validate_bootstrap_requirements(
        [
            "pytest>=8",
            "demo[extra]==1.0",
            "repo @ https://example.test/repo.whl",
            "conditional==1.0; python_version > '3.8'",
        ]
    )

    assert "exact_version_required:pytest" in errors
    assert "extras_forbidden:demo" in errors
    assert "direct_url_forbidden:repo" in errors
    assert "environment_marker_forbidden:conditional" in errors


def test_bootstrap_plan_uses_exact_base_runtime_and_isolated_target(tmp_path):
    base = tmp_path / "base"
    python = base / "cpython-3.11.9" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("fixture", encoding="utf-8")
    isolated = tmp_path / "isolated"

    plan = build_environment_bootstrap_plan(
        profiles=_profiles(),
        project="demo",
        python_version="3.11.9",
        base_runtime_root=base,
        isolated_runtime_root=isolated,
    )

    assert plan["base_python"] == str(python.resolve())
    assert plan["environment_path"] == str(
        (isolated / "demo-py3.11.9").resolve()
    )
    assert plan["commands"]["create_environment"][:3] == [
        str(python.resolve()),
        "-m",
        "venv",
    ]
    assert "--only-binary=:all:" in plan["commands"]["install_dependencies"]
    assert "--no-deps" in plan["commands"]["install_dependencies"]
    assert plan["pip_requirements"] == plan["requirements"]
    assert plan["policy"]["repository_setup_script_allowed"] is False
    assert plan["policy"]["shared_base_runtime_mutation_allowed"] is False
    assert validate_environment_bootstrap_plan(plan) == []
    assert environment_bootstrap_plan_fingerprint(plan) == plan["plan_sha256"]


def test_bootstrap_plan_uses_linux_runtime_mapping(tmp_path):
    profiles = _profiles()
    profiles["runtime_profiles"]["3.11.9"]["relative_executables"] = {
        "windows": "cpython-3.11.9/python.exe",
        "linux": "cpython-3.11.9/bin/python",
    }
    profiles["project_profiles"]["demo"]["manual_python_archives"] = [
        {
            "archive_id": "windows-only-1.0",
            "package": "windows-only",
            "version": "1.0",
            "url": "https://files.pythonhosted.org/packages/windows-only.zip",
            "sha256": "a" * 64,
            "size": 100,
            "archive_type": "zip",
            "platforms": ["windows"],
            "source_root": "windows_only-1.0",
            "install_members": ["windows_only"],
        }
    ]
    profiles["project_profiles"]["demo"][
        "required_runtime_modules_by_platform"
    ] = {"windows": ["windows_only"]}
    base = tmp_path / "base"
    python = base / "cpython-3.11.9" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("fixture", encoding="utf-8")

    plan = build_environment_bootstrap_plan(
        profiles=profiles,
        project="demo",
        python_version="3.11.9",
        base_runtime_root=base,
        isolated_runtime_root=tmp_path / "isolated",
        execution_platform="linux",
    )

    assert plan["execution_platform"] == "linux"
    assert plan["base_python"] == str(python.resolve())
    assert plan["target_python"].replace("\\", "/").endswith("/bin/python")
    assert plan["site_packages_path"].replace("\\", "/").endswith(
        "/lib/python3.11/site-packages"
    )
    assert plan["manual_python_archives"] == []
    assert "windows_only" not in plan["required_runtime_modules"]


def test_bootstrap_without_authorization_executes_nothing(tmp_path):
    plan = _plan(tmp_path)
    calls = 0

    def forbidden_runner(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError((args, kwargs))

    result = execute_environment_bootstrap(
        plan,
        authorize_dependency_install=False,
        runner=forbidden_runner,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "dependency_install_authorization_required"
    assert result["commands"] == []
    assert calls == 0
    assert environment_bootstrap_result_fingerprint(result) == result["result_sha256"]


def test_authorized_bootstrap_creates_environment_installs_and_probes(tmp_path):
    plan = _plan(tmp_path)
    calls: list[list[str]] = []

    def fake_runner(command, **kwargs):
        del kwargs
        calls.append(list(command))
        if command[1:3] == ["-m", "venv"]:
            target = Path(command[3]) / "Scripts" / "python.exe"
            target.parent.mkdir(parents=True)
            target.write_text("fixture", encoding="utf-8")
        if command[-2:] == ["freeze", "--all"]:
            output = "\n".join(plan["requirements"]) + "\n"
        else:
            output = "3.11.9\n" if "-c" in command else "ok\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    result = execute_environment_bootstrap(
        plan,
        authorize_dependency_install=True,
        proxy_url="http://127.0.0.1:7897",
        runner=fake_runner,
        runtime_probe=lambda python, version, modules: {
            "status": "pass",
            "reason": "fixture",
            "python": str(python),
            "version": version,
            "available_modules": modules,
            "missing_modules": [],
        },
    )

    assert result["status"] == "pass"
    assert result["reason"] == "isolated_runtime_ready"
    assert result["policy"]["repository_setup_script_executed"] is False
    assert result["policy"]["repository_project_installed"] is False
    assert any(command[1:3] == ["-m", "venv"] for command in calls)
    assert any(command[1:4] == ["-m", "pip", "install"] for command in calls)
    assert environment_bootstrap_result_fingerprint(result) == result["result_sha256"]


def test_partial_environment_is_not_deleted_or_reused(tmp_path):
    plan = _plan(tmp_path)
    environment = Path(plan["environment_path"])
    environment.mkdir(parents=True)
    marker = environment / "partial.txt"
    marker.write_text("preserve", encoding="utf-8")

    result = execute_environment_bootstrap(
        plan,
        authorize_dependency_install=True,
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError((args, kwargs))
        ),
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "partial_environment_requires_manual_cleanup"
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_bootstrap_fails_when_installed_distribution_version_drifts(tmp_path):
    plan = _plan(tmp_path)

    def fake_runner(command, **kwargs):
        del kwargs
        if command[1:3] == ["-m", "venv"]:
            target = Path(command[3]) / "Scripts" / "python.exe"
            target.parent.mkdir(parents=True)
            target.write_text("fixture", encoding="utf-8")
        if command[-2:] == ["freeze", "--all"]:
            output = "pytest==8.3.0\nrequests==2.32.3\n"
        else:
            output = "ok\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    result = execute_environment_bootstrap(
        plan,
        authorize_dependency_install=True,
        runner=fake_runner,
        runtime_probe=lambda *_: (_ for _ in ()).throw(
            AssertionError("module probe must not run after a version mismatch")
        ),
    )

    assert result["status"] == "fail"
    assert result["reason"] == "frozen_dependency_audit_failed"
    assert result["commands"][-1]["mismatched_distributions"] == [
        {"package": "pytest", "expected": "8.3.1", "observed": "8.3.0"}
    ]


def test_bootstrap_rejects_authenticated_or_remote_proxy(tmp_path):
    plan = _plan(tmp_path)

    with pytest.raises(ValueError, match="loopback"):
        execute_environment_bootstrap(
            plan,
            authorize_dependency_install=True,
            proxy_url="http://user:secret@example.test:8080",
        )


def test_bootstrap_plan_detects_command_tampering(tmp_path):
    plan = _plan(tmp_path)
    tampered = copy.deepcopy(plan)
    tampered["commands"]["install_dependencies"].append("unregistered-package")

    errors = validate_environment_bootstrap_plan(tampered)

    assert "plan_sha256_mismatch" in errors
    assert "install_dependencies_command_mismatch" in errors


def test_bootstrap_plan_rejects_refingerprinted_pip_requirement_override(tmp_path):
    plan = _plan(tmp_path)
    tampered = copy.deepcopy(plan)
    tampered["pip_requirements"].append("unregistered-package==1.0")
    tampered["commands"]["install_dependencies"].append(
        "unregistered-package==1.0"
    )
    tampered["plan_sha256"] = environment_bootstrap_plan_fingerprint(tampered)

    errors = validate_environment_bootstrap_plan(tampered)

    assert "plan_sha256_mismatch" not in errors
    assert "pip_requirements_mismatch" in errors


def test_bootstrap_plan_rejects_refingerprinted_site_packages_redirect(tmp_path):
    plan = _plan(tmp_path)
    tampered = copy.deepcopy(plan)
    tampered["site_packages_path"] = str(
        Path(plan["environment_path"]) / "redirected-site-packages"
    )
    tampered["plan_sha256"] = environment_bootstrap_plan_fingerprint(tampered)

    errors = validate_environment_bootstrap_plan(tampered)

    assert "plan_sha256_mismatch" not in errors
    assert "site_packages_path_mismatch" in errors


def test_hash_pinned_pure_python_archive_is_installed_without_setup(tmp_path):
    archive_bytes = _manual_archive_bytes()
    profiles = _profiles()
    profiles["project_profiles"]["demo"]["manual_python_archives"] = [
        {
            "archive_id": "demo-console-0.5",
            "package": "demo-console",
            "version": "0.5",
            "url": "https://files.pythonhosted.org/packages/demo-console-0.5.zip",
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "size": len(archive_bytes),
            "archive_type": "zip",
            "source_root": "demo_console-0.5",
            "install_members": ["demo_console", "run.py"],
        }
    ]
    profiles["project_profiles"]["demo"]["required_runtime_modules"].append(
        "demo_console"
    )
    base = tmp_path / "base"
    python = base / "cpython-3.11.9" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("fixture", encoding="utf-8")
    plan = build_environment_bootstrap_plan(
        profiles=profiles,
        project="demo",
        python_version="3.11.9",
        base_runtime_root=base,
        isolated_runtime_root=tmp_path / "isolated",
    )

    def fake_runner(command, **kwargs):
        del kwargs
        if command[1:3] == ["-m", "venv"]:
            target = Path(command[3]) / "Scripts" / "python.exe"
            target.parent.mkdir(parents=True)
            target.write_text("fixture", encoding="utf-8")
        if command[-2:] == ["freeze", "--all"]:
            output = "\n".join(plan["requirements"]) + "\n"
        else:
            output = "3.11.9\n" if "-c" in command else "ok\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    result = execute_environment_bootstrap(
        plan,
        authorize_dependency_install=True,
        runner=fake_runner,
        runtime_probe=lambda python, version, modules: {
            "status": "pass",
            "reason": "fixture",
            "python": str(python),
            "version": version,
            "available_modules": modules,
            "missing_modules": [],
        },
        archive_fetcher=lambda url, proxy, size: archive_bytes,
    )

    site_packages = Path(plan["site_packages_path"])
    archive_stage = next(
        stage
        for stage in result["commands"]
        if stage["stage"] == "install_manual_archive:demo-console-0.5"
    )
    assert result["status"] == "pass"
    assert archive_stage["reason"] == "hash_pinned_pure_python_archive_installed"
    assert archive_stage["setup_script_executed"] is False
    assert (site_packages / "demo_console" / "__init__.py").is_file()
    assert (site_packages / "run.py").is_file()
    assert not (site_packages / "setup.py").exists()


def test_hash_pinned_conda_binary_replaces_missing_linux_wheel(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        v4_reproduction_environment,
        "_host_execution_platform",
        lambda: "linux",
    )
    archive_bytes = _conda_binary_archive_bytes()
    profiles = _profiles()
    profiles["runtime_profiles"]["3.11.9"]["relative_executables"] = {
        "windows": "cpython-3.11.9/python.exe",
        "linux": "cpython-3.11.9/bin/python",
    }
    project = profiles["project_profiles"]["demo"]
    project["bootstrap_requirements"].append("psutil==5.7.0")
    project["required_runtime_modules"].append("psutil")
    project["manual_python_archives"] = [_conda_archive_profile(archive_bytes)]
    base = tmp_path / "base"
    python = base / "cpython-3.11.9" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("fixture", encoding="utf-8")
    plan = build_environment_bootstrap_plan(
        profiles=profiles,
        project="demo",
        python_version="3.11.9",
        base_runtime_root=base,
        isolated_runtime_root=tmp_path / "isolated",
        execution_platform="linux",
    )

    def fake_runner(command, **kwargs):
        del kwargs
        if command[1:3] == ["-m", "venv"]:
            target = Path(plan["target_python"])
            target.parent.mkdir(parents=True)
            target.write_text("fixture", encoding="utf-8")
        if command[-2:] == ["freeze", "--all"]:
            output = "\n".join(plan["requirements"]) + "\n"
        else:
            output = "3.11.9\n" if "-c" in command else "ok\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    result = execute_environment_bootstrap(
        plan,
        authorize_dependency_install=True,
        runner=fake_runner,
        runtime_probe=lambda python, version, modules: {
            "status": "pass",
            "reason": "fixture",
            "python": str(python),
            "version": version,
            "available_modules": modules,
            "missing_modules": [],
        },
        archive_fetcher=lambda url, proxy, size: archive_bytes,
    )

    install_command = plan["commands"]["install_dependencies"]
    site_packages = Path(plan["site_packages_path"])
    archive_stage = next(
        stage
        for stage in result["commands"]
        if stage["stage"]
        == "install_manual_archive:psutil-5.7.0-conda-linux-cp311"
    )
    assert "psutil==5.7.0" in plan["requirements"]
    assert "psutil==5.7.0" not in plan["pip_requirements"]
    assert "psutil==5.7.0" not in install_command
    assert result["status"] == "pass"
    assert result["policy"]["manual_binary_archive_count"] == 1
    assert archive_stage["reason"] == "hash_pinned_conda_python_binary_installed"
    assert archive_stage["setup_script_executed"] is False
    assert (site_packages / "psutil" / "__init__.py").is_file()
    assert (
        site_packages
        / "psutil"
        / "_psutil_linux.cpython-311-x86_64-linux-gnu.so"
    ).is_file()
    assert (site_packages / "psutil-5.7.0.dist-info" / "METADATA").is_file()
    assert not (site_packages / "psutil" / "tests").exists()


def test_manual_archive_profile_rejects_remote_host_and_unsafe_member():
    errors = validate_manual_python_archives(
        [
            {
                "archive_id": "unsafe",
                "package": "demo",
                "version": "1.0",
                "url": "https://example.test/demo.zip",
                "sha256": "a" * 64,
                "size": 100,
                "archive_type": "zip",
                "source_root": "demo-1.0",
                "install_members": ["../escape.py"],
            }
        ]
    )

    assert "manual_archive:0:source_url_is_not_allowed" in errors
    assert "manual_archive:0:install_member_is_unsafe" in errors


def test_conda_archive_profile_rejects_wrong_registry():
    profile = _conda_archive_profile(b"fixture")
    assert validate_manual_python_archives([profile]) == []
    profile["url"] = (
        "https://files.pythonhosted.org/packages/"
        "psutil-5.7.0-py311h123_0.tar.bz2"
    )

    errors = validate_manual_python_archives([profile])

    assert "manual_archive:0:source_url_is_not_allowed" in errors


def test_conda_binary_archive_rejects_metadata_drift_and_links():
    drifted = _conda_binary_archive_bytes(index_version="5.7.1")
    _, drift_errors = (
        v4_reproduction_environment._validated_conda_archive_writes(
            drifted,
            archive=_conda_archive_profile(drifted),
        )
    )
    linked = _conda_binary_archive_bytes(include_link=True)
    _, link_errors = v4_reproduction_environment._validated_conda_archive_writes(
        linked,
        archive=_conda_archive_profile(linked),
    )

    assert "conda_index_version_mismatch" in drift_errors
    assert "archive_link_member_is_forbidden" in link_errors


def _plan(tmp_path: Path) -> dict:
    base = tmp_path / "base"
    python = base / "cpython-3.11.9" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("fixture", encoding="utf-8")
    return build_environment_bootstrap_plan(
        profiles=_profiles(),
        project="demo",
        python_version="3.11.9",
        base_runtime_root=base,
        isolated_runtime_root=tmp_path / "isolated",
    )


def _profiles() -> dict:
    return {
        "schema_version": "4.0",
        "profile_id": "fixture",
        "setup_script_policy": "never_execute",
        "runtime_profiles": {
            "3.11.9": {"relative_executable": "cpython-3.11.9/python.exe"}
        },
        "project_profiles": {
            "demo": {
                "execute_benchmark_setup_script": False,
                "dependency_install_requires_authorization": True,
                "native_build_adapter_required": False,
                "isolated_environment_template": "demo-py{version}",
                "bootstrap_requirements": [
                    "pytest==8.3.1",
                    "requests==2.32.3",
                ],
                "required_runtime_modules": ["pytest", "requests"],
                "pythonpath_entries": ["."],
                "command_module_rewrites": [],
                "preparation_files": [],
            }
        },
    }


def _manual_archive_bytes() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("demo_console-0.5/demo_console/__init__.py", "VALUE = 1\n")
        archive.writestr("demo_console-0.5/run.py", "VALUE = 2\n")
        archive.writestr("demo_console-0.5/setup.py", "raise RuntimeError()\n")
    return payload.getvalue()


def _conda_archive_profile(archive_bytes: bytes) -> dict:
    return {
        "archive_id": "psutil-5.7.0-conda-linux-cp311",
        "package": "psutil",
        "version": "5.7.0",
        "url": (
            "https://conda.anaconda.org/conda-forge/linux-64/"
            "psutil-5.7.0-py311h123_0.tar.bz2"
        ),
        "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "size": len(archive_bytes),
        "archive_type": "conda-tar-bz2",
        "artifact_kind": "conda_python_binary",
        "platforms": ["linux"],
        "source_root": "lib/python3.11/site-packages",
        "install_members": ["psutil", "psutil-5.7.0.dist-info"],
        "dist_info_dir": "psutil-5.7.0.dist-info",
        "native_module_roots": ["psutil"],
        "exclude_members": ["psutil/tests", "psutil/__pycache__"],
        "replaces_pip_requirement": True,
        "conda_build": "py311h123_0",
        "conda_subdir": "linux-64",
        "wheel_tag": "cp311-cp311-linux_x86_64",
        "allowed_native_suffixes": [".cpython-311-x86_64-linux-gnu.so"],
    }


def _conda_binary_archive_bytes(
    *,
    index_version: str = "5.7.0",
    include_link: bool = False,
) -> bytes:
    payload = io.BytesIO()
    index = {
        "name": "psutil",
        "version": index_version,
        "build": "py311h123_0",
        "subdir": "linux-64",
    }
    files = {
        "info/index.json": json.dumps(index).encode("utf-8"),
        "lib/python3.11/site-packages/psutil/__init__.py": b"VALUE = 1\n",
        (
            "lib/python3.11/site-packages/psutil/"
            "_psutil_linux.cpython-311-x86_64-linux-gnu.so"
        ): b"fixture-native-binary",
        "lib/python3.11/site-packages/psutil/tests/test_demo.py": b"VALUE = 2\n",
        (
            "lib/python3.11/site-packages/psutil-5.7.0.dist-info/WHEEL"
        ): (
            b"Wheel-Version: 1.0\n"
            b"Root-Is-Purelib: false\n"
            b"Tag: cp311-cp311-linux_x86_64\n"
        ),
        (
            "lib/python3.11/site-packages/psutil-5.7.0.dist-info/METADATA"
        ): b"Metadata-Version: 2.1\nName: psutil\nVersion: 5.7.0\n",
    }
    with tarfile.open(fileobj=payload, mode="w:bz2") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
        if include_link:
            link = tarfile.TarInfo(
                "lib/python3.11/site-packages/psutil/unsafe-link"
            )
            link.type = tarfile.SYMTYPE
            link.linkname = "../../../../escape"
            archive.addfile(link)
    return payload.getvalue()
