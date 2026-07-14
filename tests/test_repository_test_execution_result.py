import subprocess
import sys
from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_execution_result import (
    execute_repository_test_plan,
    render_repository_test_execution_result_markdown,
    write_repository_test_execution_result_artifacts,
)


def test_repository_test_execution_result_runs_safe_python_module_command(tmp_path):
    (tmp_path / "test_sample.py").write_text(
        "def test_smoke():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "smoke",
            "recommended_execution_risk": "medium",
            "recommended_execution_scope": "pytest discovery",
            "executable_now": True,
        },
        repository_root=tmp_path,
        timeout=10,
    )
    paths = write_repository_test_execution_result_artifacts(payload, tmp_path / "out")
    markdown = render_repository_test_execution_result_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["executed"] is True
    assert payload["returncode"] == 0
    assert payload["passed"] == 1
    assert payload["failed"] == 0
    assert payload["command_args"][0] == sys.executable
    assert payload["command_args"][1:3] == ["-m", "pytest"]
    assert payload["python_executable"] == sys.executable
    assert payload["python_executable_source"] == "current_interpreter"
    assert payload["failure_category"] == "none"
    assert "Repository Test Execution Result" in markdown
    assert Path(paths["repository_test_execution_result_json"]).exists()
    assert Path(paths["repository_test_execution_result_markdown"]).exists()


def test_repository_test_execution_result_uses_python_executable_override(tmp_path):
    commands = []
    venv_python = tmp_path / ".repo_test_venv" / "Scripts" / "python.exe"

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del capture_output, text, timeout, check, env
        commands.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "2 passed", "")

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q tests",
            "recommended_execution_level": "narrow",
            "recommended_execution_risk": "low",
            "executable_now": True,
        },
        repository_root=tmp_path,
        python_executable=venv_python,
        python_executable_source="repository_test_environment_setup",
        runner=fake_runner,
    )
    markdown = render_repository_test_execution_result_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["command_args"][:3] == [str(venv_python), "-m", "pytest"]
    assert payload["python_executable"] == str(venv_python)
    assert (
        payload["python_executable_source"] == "repository_test_environment_setup"
    )
    assert payload["passed"] == 2
    assert payload["failure_category"] == "none"
    assert commands[0][0][0] == str(venv_python)
    assert "Python Executable Source" in markdown


def test_repository_test_execution_result_runs_from_recommended_working_dir(tmp_path):
    api_root = tmp_path / "services" / "api"
    api_root.mkdir(parents=True)
    calls = []

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del capture_output, text, timeout, check, env
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "1 passed", "")

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q tests/test_api.py",
            "recommended_execution_level": "narrow",
            "recommended_execution_risk": "low",
            "recommended_execution_scope": "subproject pytest",
            "recommended_execution_runner": "pytest",
            "recommended_working_dir": "services/api",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=fake_runner,
    )
    markdown = render_repository_test_execution_result_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["repository_root"] == str(tmp_path)
    assert payload["working_dir"] == "services/api"
    assert payload["cwd"] == str(api_root)
    assert calls[0][1] == api_root
    assert "Working Dir: `services/api`" in markdown


def test_repository_test_execution_result_blocks_unsafe_working_dir(tmp_path):
    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "narrow",
            "recommended_execution_risk": "low",
            "recommended_working_dir": "../outside",
            "executable_now": True,
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "selected_working_dir_missing"
    assert payload["executed"] is False


def test_repository_test_execution_result_applies_planned_environment_variables(tmp_path):
    seen_env = {}

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del command, cwd, capture_output, text, timeout, check
        seen_env.update(env)
        return subprocess.CompletedProcess(["python", "-m", "pytest"], 0, "1 passed", "")

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "narrow",
            "recommended_execution_risk": "low",
            "executable_now": True,
            "planned_environment_variables": {
                "DJANGO_SETTINGS_MODULE": "mysite.settings",
                "FLASK_APP": "app:app",
                "unsafe-name": "ignored",
            },
        },
        repository_root=tmp_path,
        runner=fake_runner,
    )
    markdown = render_repository_test_execution_result_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["planned_environment_variables"] == {
        "DJANGO_SETTINGS_MODULE": "mysite.settings",
        "FLASK_APP": "app:app",
    }
    assert seen_env["DJANGO_SETTINGS_MODULE"] == "mysite.settings"
    assert seen_env["FLASK_APP"] == "app:app"
    assert "DJANGO_SETTINGS_MODULE" in markdown


def test_repository_test_execution_result_adds_src_layout_pythonpath(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    src = tmp_path / "src"
    package = src / "samplepkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    seen_env = {}

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del command, cwd, capture_output, text, timeout, check
        seen_env.update(env)
        return subprocess.CompletedProcess(["python", "-m", "pytest"], 0, "1 passed", "")

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "narrow",
            "recommended_execution_risk": "low",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=fake_runner,
    )
    markdown = render_repository_test_execution_result_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["automatic_environment_variable_names"] == ["PYTHONPATH"]
    assert payload["automatic_environment_variables"]["PYTHONPATH"] == str(src.resolve())
    assert seen_env["PYTHONPATH"] == str(src.resolve())
    assert "Automatic Environment Variables" in markdown


def test_repository_test_execution_result_parses_unittest_success_counts(tmp_path):
    def unittest_success_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "unittest", "discover"],
            0,
            "",
            "test_add_one (tests.test_simple.TestSimple.test_add_one) ... ok\n"
            "\n"
            "----------------------------------------------------------------------\n"
            "Ran 1 test in 0.001s\n"
            "\n"
            "OK\n",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": (
                "python -m unittest discover -s tests -p test_simple.py"
            ),
            "recommended_execution_level": "narrow",
            "recommended_execution_risk": "low",
            "recommended_execution_runner": "unittest",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=unittest_success_runner,
    )
    markdown = render_repository_test_execution_result_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["test_count"] == 1
    assert payload["test_count_source"] == "unittest_summary"
    assert payload["passed"] == 1
    assert payload["failed"] == 0
    assert payload["errors"] == 0
    assert payload["skipped"] == 0
    assert payload["parsed_test_counts"]["total"] == 1
    assert "Test Count: 1" in markdown


def test_repository_test_execution_result_skips_when_plan_not_executable():
    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "smoke",
            "recommended_execution_risk": "medium",
            "executable_now": False,
        }
    )

    assert payload["status"] == "skipped"
    assert payload["executed"] is False
    assert payload["reason"] == "plan_not_executable"
    assert payload["python_executable"] == sys.executable
    assert payload["python_executable_source"] == "current_interpreter"
    assert payload["failure_category"] == "not_executed"
    assert payload["failure_signal"] == "plan_not_executable"


def test_repository_test_execution_result_rejects_unsupported_command(tmp_path):
    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "pytest -q",
            "recommended_execution_level": "smoke",
            "recommended_execution_risk": "medium",
            "executable_now": True,
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "unsupported_command"
    assert payload["executed"] is False


def test_repository_test_execution_result_records_timeout(tmp_path):
    def timeout_runner(*args, **kwargs):
        del args, kwargs
        raise subprocess.TimeoutExpired(
            cmd=["python", "-m", "pytest"],
            timeout=1,
            output="partial stdout",
            stderr="partial stderr",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "smoke",
            "recommended_execution_risk": "medium",
            "executable_now": True,
        },
        repository_root=tmp_path,
        timeout=1,
        runner=timeout_runner,
    )

    assert payload["status"] == "fail"
    assert payload["executed"] is True
    assert payload["reason"] == "timeout"
    assert payload["timeout"] is True
    assert payload["failure_category"] == "timeout"
    assert "partial stdout" in payload["stdout_preview"]


def test_repository_test_execution_result_classifies_missing_dependency(tmp_path):
    def missing_dependency_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            2,
            "",
            "ModuleNotFoundError: No module named 'requests'\n",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "smoke",
            "recommended_execution_risk": "medium",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=missing_dependency_runner,
    )
    markdown = render_repository_test_execution_result_markdown(payload)

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "missing_dependency"
    assert payload["failure_signal"] == "missing_module:requests"
    assert "dependency" in payload["diagnostic_summary"].lower()
    assert any("requests" in action for action in payload["next_actions"])
    assert "Failure Category" in markdown
    assert "Diagnostic Summary" in markdown


def test_repository_test_execution_result_classifies_missing_native_extension(
    tmp_path,
):
    def missing_native_extension_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            4,
            "",
            (
                "ImportError while loading conftest 'tests/conftest.py'.\n"
                "E   UserWarning: Polars binary is missing!\n"
            ),
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q tests",
            "recommended_execution_level": "narrow",
            "recommended_execution_risk": "low",
            "recommended_execution_runner": "pytest",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=missing_native_extension_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "missing_native_extension"
    assert "binary is missing" in payload["failure_signal"].lower()
    assert "compiled native extension" in payload["diagnostic_summary"]
    assert any("native extension" in action for action in payload["next_actions"])


def test_repository_test_execution_result_classifies_missing_test_runner(tmp_path):
    def missing_runner_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            1,
            "",
            f"{sys.executable}: No module named pytest\n",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest tests",
            "recommended_execution_level": "full",
            "recommended_execution_risk": "medium",
            "recommended_execution_runner": "pytest",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=missing_runner_runner,
    )

    assert payload["status"] == "fail"
    assert payload["execution_runner"] == "pytest"
    assert payload["failure_category"] == "missing_test_runner"
    assert payload["failure_signal"] == "missing_runner:pytest"
    assert "test runner" in payload["diagnostic_summary"].lower()
    assert any("pytest" in action for action in payload["next_actions"])


def test_repository_test_execution_result_classifies_missing_pytest_fixture(tmp_path):
    def missing_fixture_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            1,
            "E       fixture 'client' not found\n>       available fixtures: tmp_path\n",
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "narrow",
            "recommended_execution_risk": "low",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=missing_fixture_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "missing_pytest_fixture"
    assert payload["failure_signal"] == "missing_fixture:client"
    assert "fixture" in payload["diagnostic_summary"].lower()
    assert any("client" in action for action in payload["next_actions"])


def test_repository_test_execution_result_suggests_known_pytest_fixture_plugin(
    tmp_path,
):
    def missing_fixture_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            1,
            "E       fixture 'mocker' not found\n>       available fixtures: tmp_path\n",
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "focused",
            "recommended_execution_risk": "low",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=missing_fixture_runner,
    )

    assert payload["failure_category"] == "missing_pytest_fixture"
    assert payload["failure_signal"] == "missing_fixture:mocker"
    assert any("pytest-mock" in action for action in payload["next_actions"])


def test_repository_test_execution_result_classifies_import_path_error(tmp_path):
    def import_path_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            2,
            "",
            (
                "ImportPathMismatchError: ('pkg.tests.test_api', "
                "'/repo/pkg/tests/test_api.py', '/repo/tests/test_api.py')\n"
            ),
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "full",
            "recommended_execution_risk": "high",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=import_path_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "import_path_error"
    assert "ImportPathMismatchError" in payload["failure_signal"]
    assert "package import path" in payload["diagnostic_summary"]
    assert any("target_prefix" in action for action in payload["next_actions"])


def test_repository_test_execution_result_classifies_framework_configuration_error(
    tmp_path,
):
    def framework_config_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            2,
            "",
            (
                "django.core.exceptions.ImproperlyConfigured: Requested setting "
                "INSTALLED_APPS, but settings are not configured. You must either "
                "define the environment variable DJANGO_SETTINGS_MODULE or call "
                "settings.configure() before accessing settings.\n"
            ),
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "narrow",
            "recommended_execution_risk": "low",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=framework_config_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "framework_configuration_error"
    assert "ImproperlyConfigured" in payload["failure_signal"]
    assert "framework test configuration" in payload["diagnostic_summary"]
    assert any("DJANGO_SETTINGS_MODULE" in action for action in payload["next_actions"])


def test_repository_test_execution_result_classifies_pytest_warning_as_error(tmp_path):
    def warning_error_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            2,
            (
                "ERROR collecting tests/test_basic.py\n"
                "E   pytest.PytestRemovedIn10Warning: Passing a non-Collection "
                "iterable to parametrize is deprecated.\n"
                "warnings.warn('deprecated')\n"
            ),
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q tests",
            "recommended_execution_level": "narrow",
            "recommended_execution_risk": "low",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=warning_error_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "pytest_warning_as_error"
    assert "PytestRemovedIn10Warning" in payload["failure_signal"]
    assert "warning policy" in payload["diagnostic_summary"]


def test_repository_test_execution_result_classifies_no_tests_collected(tmp_path):
    def no_tests_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            5,
            "collected 0 items\n\nno tests ran in 0.01s\n",
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "smoke",
            "recommended_execution_risk": "medium",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=no_tests_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "no_tests_collected"
    assert "did not discover runnable tests" in payload["diagnostic_summary"]


def test_repository_test_execution_result_classifies_inline_pytest_failed_nodeid(
    tmp_path,
):
    def inline_failed_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            1,
            (
                "tests/test_service.py::TestService::test_guard FAILED [100%]\n"
                "E   AssertionError: expected guard\n"
            ),
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": (
                "python -m pytest -q tests/test_service.py::TestService::test_guard"
            ),
            "recommended_execution_level": "focused",
            "recommended_execution_risk": "low",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=inline_failed_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "test_assertion_failure"
    assert payload["failure_signal"] == (
        "tests/test_service.py::TestService::test_guard FAILED [100%]"
    )
    assert "dynamic evidence" in " ".join(payload["next_actions"])


def test_repository_test_execution_result_classifies_parameterized_failed_nodeid_with_spaces(
    tmp_path,
):
    def parameterized_failed_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            1,
            (
                "FAILED tests/test_service.py::test_guard[empty value] - "
                "AssertionError\n"
            ),
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q tests/test_service.py",
            "recommended_execution_level": "focused",
            "recommended_execution_risk": "low",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=parameterized_failed_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "test_assertion_failure"
    assert payload["failure_signal"] == (
        "FAILED tests/test_service.py::test_guard[empty value] - AssertionError"
    )


def test_repository_test_execution_result_classifies_nodeid_scoped_truncated_failure(
    tmp_path,
):
    def truncated_nodeid_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            1,
            "E   AssertionError: expected guard\n",
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": (
                "python -m pytest -q --maxfail=1 "
                "tests/test_service.py::TestService::test_guard"
            ),
            "recommended_execution_level": "focused",
            "recommended_execution_risk": "low",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=truncated_nodeid_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "test_assertion_failure"
    assert payload["failure_signal"] == (
        "FAILED tests/test_service.py::TestService::test_guard "
        "(nodeid-scoped pytest command)"
    )
    assert "nodeid-scoped" in payload["diagnostic_summary"]


def test_repository_test_execution_result_classifies_unittest_failure(tmp_path):
    def unittest_failure_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "unittest", "discover"],
            1,
            (
                "F\n"
                "======================================================================\n"
                "FAIL: test_pick_short_values "
                "(tests.test_service.TestService.test_pick_short_values)\n"
                "----------------------------------------------------------------------\n"
                "Traceback (most recent call last):\n"
                "  File \"tests/test_service.py\", line 6, in test_pick_short_values\n"
                "    self.assertEqual(pick([1]), 1)\n"
                "AssertionError: 2 != 1\n"
                "\n"
                "----------------------------------------------------------------------\n"
                "Ran 1 test in 0.001s\n"
                "\n"
                "FAILED (failures=1)\n"
            ),
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m unittest discover",
            "recommended_execution_level": "full",
            "recommended_execution_risk": "medium",
            "recommended_execution_runner": "unittest",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=unittest_failure_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "test_assertion_failure"
    assert payload["failure_signal"] == (
        "FAIL: test_pick_short_values "
        "(tests.test_service.TestService.test_pick_short_values)"
    )
    assert "unittest" in payload["diagnostic_summary"]
    assert payload["test_count"] == 1
    assert payload["test_count_source"] == "unittest_summary"
    assert payload["passed"] == 0
    assert payload["failed"] == 1
    assert payload["errors"] == 0


def test_repository_test_execution_result_preserves_failure_context_beyond_preview(
    tmp_path,
):
    long_prefix = "\n".join(f"noise line {index}" for index in range(700))

    def long_failure_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            1,
            (
                f"{long_prefix}\n"
                "FAILED tests/test_service.py::test_guard - AssertionError\n"
                "Traceback (most recent call last):\n"
                "  File \"tests/test_service.py\", line 12, in test_guard\n"
                "    assert guard([]) == 1\n"
                "AssertionError: assert 0 == 1\n"
            ),
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m pytest -q",
            "recommended_execution_level": "smoke",
            "recommended_execution_risk": "medium",
            "recommended_execution_runner": "pytest",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=long_failure_runner,
    )
    markdown = render_repository_test_execution_result_markdown(payload)

    assert payload["failure_category"] == "test_assertion_failure"
    assert "...[truncated]" in payload["stdout_preview"]
    assert "FAILED tests/test_service.py::test_guard" not in payload["stdout_preview"]
    assert "FAILED tests/test_service.py::test_guard" in payload["failure_context"]
    assert "File \"tests/test_service.py\", line 12" in payload["failure_context"]
    assert payload["failure_context_line_count"] > 0
    assert "Failure Context" in markdown


def test_repository_test_execution_result_classifies_quoted_parameterized_nodeid_scoped_failure(
    tmp_path,
):
    def truncated_parameterized_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "pytest"],
            1,
            "E   AssertionError: expected guard\n",
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": (
                "python -m pytest -q --maxfail=1 "
                "'tests/test_service.py::TestService::test_guard[pkg::empty value]'"
            ),
            "recommended_execution_level": "focused",
            "recommended_execution_risk": "low",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=truncated_parameterized_runner,
    )

    assert payload["status"] == "fail"
    assert payload["failure_category"] == "test_assertion_failure"
    assert payload["failure_signal"] == (
        "FAILED tests/test_service.py::TestService::test_guard[pkg::empty value] "
        "(nodeid-scoped pytest command)"
    )


def test_repository_test_execution_result_classifies_tox_missing_python(tmp_path):
    def tox_runner(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["python", "-m", "tox"],
            1,
            (
                "py37: failed with could not find python interpreter matching "
                "any of the specs py37\n"
            ),
            "",
        )

    payload = execute_repository_test_plan(
        {
            "recommended_execution_command": "python -m tox",
            "recommended_execution_level": "full",
            "recommended_execution_risk": "high",
            "recommended_execution_runner": "tox",
            "executable_now": True,
        },
        repository_root=tmp_path,
        runner=tox_runner,
    )

    assert payload["status"] == "fail"
    assert payload["execution_runner"] == "tox"
    assert payload["failure_category"] == "tox_missing_python_interpreter"
    assert "py37" in payload["failure_signal"]
    assert any("pytest" in action for action in payload["next_actions"])
