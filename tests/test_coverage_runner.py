from pathlib import Path
import tempfile

import pytest

from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.tools.coverage_runner import CoverageRunner


def test_coverage_runner_maps_pytest_execution_to_functions():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "import asyncio\n\n"
            "import threading\n\n"
            "def buggy(value):\n"
            "    if value > 0:\n"
            "        return value + 1\n"
            "    return value - 1\n\n"
            "def wrapper(value):\n"
            "    return buggy(value)\n\n"
            "def raises_value():\n"
            "    raise ValueError('boom')\n\n"
            "def catches_value():\n"
            "    try:\n"
            "        raises_value()\n"
            "    except ValueError:\n"
            "        return 'caught'\n\n"
            "def loop_sum(values):\n"
            "    total = 0\n"
            "    for value in values:\n"
            "        total += value\n"
            "    return total\n\n"
            "async def async_leaf(value):\n"
            "    return value + 1\n\n"
            "async def async_wrapper(value):\n"
            "    return await async_leaf(value)\n\n"
            "def threaded_wrapper(value):\n"
            "    result = []\n"
            "    thread = threading.Thread(target=lambda: result.append(wrapper(value)))\n"
            "    thread.start()\n"
            "    thread.join()\n"
            "    return result[0]\n\n"
            "def clean(value):\n"
            "    return value * 2\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "import asyncio\n\n"
            "from sample import async_wrapper, buggy, catches_value, clean, loop_sum, threaded_wrapper, wrapper\n\n"
            "def helper(value):\n"
            "    return wrapper(value)\n\n"
            "def test_buggy():\n"
            "    assert helper(1) == 3\n\n"
            "def test_buggy_negative():\n"
            "    assert buggy(-1) == -2\n\n"
            "def test_catches_value():\n"
            "    assert catches_value() == 'caught'\n\n"
            "def test_loop_empty():\n"
            "    assert loop_sum([]) == 0\n\n"
            "def test_loop_single():\n"
            "    assert loop_sum([3]) == 3\n\n"
            "def test_loop_multi():\n"
            "    assert loop_sum([1, 2, 3]) == 6\n\n"
            "def test_async_path():\n"
            "    assert asyncio.run(async_wrapper(1)) == 2\n\n"
            "def test_threaded_path():\n"
            "    assert threaded_wrapper(1) == 2\n\n"
            "def test_clean():\n"
            "    assert clean(2) == 4\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(repo)
        by_name = {
            function.metadata["qualified_name"]: function for function in parsed.functions
        }
        result = CoverageRunner(timeout=10).run_test_coverage(
            repo,
            parsed.functions,
            "test_buggy",
        )
        exception_result = CoverageRunner(timeout=10).run_test_coverage(
            repo,
            parsed.functions,
            "test_catches_value",
        )
        loop_empty = CoverageRunner(timeout=10).run_test_coverage(
            repo,
            parsed.functions,
            "test_loop_empty",
        )
        loop_single = CoverageRunner(timeout=10).run_test_coverage(
            repo,
            parsed.functions,
            "test_loop_single",
        )
        loop_multi = CoverageRunner(timeout=10).run_test_coverage(
            repo,
            parsed.functions,
            "test_loop_multi",
        )
        async_result = CoverageRunner(timeout=10).run_test_coverage(
            repo,
            parsed.functions,
            "test_async_path",
        )
        threaded_result = CoverageRunner(timeout=10).run_test_coverage(
            repo,
            parsed.functions,
            "test_threaded_path",
        )
        summary = CoverageRunner(timeout=10).build_summary(
            repo,
            parsed.functions,
            failing_tests=["test_buggy"],
            passed_tests=["test_clean"],
        )

        assert result.success is False
        assert by_name["buggy"].id in result.covered_function_ids
        assert by_name["wrapper"].id in result.covered_function_ids
        assert result.covered_function_line_counts[by_name["buggy"].id] == 2
        assert result.covered_function_lines[by_name["buggy"].id] == {
            by_name["buggy"].start_line + 1,
            by_name["buggy"].start_line + 2,
        }
        assert result.function_line_coverage[by_name["buggy"].id] == 0.6667
        assert result.covered_branch_outcomes[by_name["buggy"].id] == {
            f"if:{by_name['buggy'].start_line + 1}:true"
        }
        assert any(
            fragment.endswith("test_buggy -> buggy")
            for fragment in result.covered_path_fragments[by_name["buggy"].id]
        )
        assert any(
            fragment == "pathseq:test_buggy -> wrapper -> buggy"
            for fragment in result.covered_path_fragments[by_name["buggy"].id]
        )
        assert any(
            fragment == "pathseq:test_buggy -> wrapper -> buggy"
            for fragment in result.covered_path_fragments[by_name["wrapper"].id]
        )
        assert exception_result.success is True
        assert any(
            fragment == "exception:test_catches_value -> raises_value:ValueError"
            for fragment in exception_result.covered_path_fragments[
                by_name["raises_value"].id
            ]
        )
        expected_exception_path = (
            "exception_path:test_catches_value -> catches_value -> "
            "raises_value:ValueError"
        )
        assert (
            expected_exception_path
            in exception_result.covered_path_fragments[by_name["raises_value"].id]
        )
        assert (
            expected_exception_path
            in exception_result.covered_path_fragments[by_name["catches_value"].id]
        )
        loop_line = by_name["loop_sum"].start_line + 2
        assert (
            f"loopseq:test_loop_empty -> loop_sum:{loop_line}:zero"
            in loop_empty.covered_path_fragments[by_name["loop_sum"].id]
        )
        assert (
            f"loopseq:test_loop_single -> loop_sum:{loop_line}:single"
            in loop_single.covered_path_fragments[by_name["loop_sum"].id]
        )
        assert (
            f"loopseq:test_loop_multi -> loop_sum:{loop_line}:multi"
            in loop_multi.covered_path_fragments[by_name["loop_sum"].id]
        )
        expected_async_path = "asyncseq:test_async_path -> async_wrapper -> async_leaf"
        assert (
            expected_async_path
            in async_result.covered_path_fragments[by_name["async_wrapper"].id]
        )
        assert (
            expected_async_path
            in async_result.covered_path_fragments[by_name["async_leaf"].id]
        )
        expected_threaded_path = (
            "callseq:test_threaded_path -> threaded_wrapper -> wrapper -> buggy"
        )
        assert (
            expected_threaded_path
            in threaded_result.covered_path_fragments[by_name["threaded_wrapper"].id]
        )
        assert (
            expected_threaded_path
            in threaded_result.covered_path_fragments[by_name["buggy"].id]
        )
        assert by_name["clean"].id not in result.covered_function_ids
        assert by_name["helper"].metadata["is_test_file"] is True
        assert by_name["helper"].id not in result.covered_function_ids
        assert by_name["test_buggy"].id in summary.failed_tests
        assert by_name["test_clean"].id in summary.passed_tests
        assert summary.dynamic_evidence_test_ids == {by_name["test_buggy"].id}
        assert summary.dynamic_traceback_function_ids == set()
        assert by_name["buggy"].id in summary.coverage[by_name["test_buggy"].id]
        assert (
            summary.line_coverage[by_name["test_buggy"].id][by_name["buggy"].id]
            == 0.6667
        )
        assert summary.covered_lines[by_name["test_buggy"].id][by_name["buggy"].id] == {
            by_name["buggy"].start_line + 1,
            by_name["buggy"].start_line + 2,
        }
        assert summary.branch_coverage[by_name["test_buggy"].id][by_name["buggy"].id] == {
            f"if:{by_name['buggy'].start_line + 1}:true"
        }
        assert any(
            fragment.endswith("test_buggy -> buggy")
            for fragment in summary.path_coverage[by_name["test_buggy"].id][
                by_name["buggy"].id
            ]
        )
        assert by_name["clean"].id in summary.coverage[by_name["test_clean"].id]
        assert summary.test_names[by_name["test_buggy"].id] == "test_buggy"
        assert "assert helper(1) == 3" in summary.failure_messages[by_name["test_buggy"].id]


def test_coverage_runner_traces_exact_unittest_module_command(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def normalize(value):\n"
        "    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (tmp_path / "test_sample.py").write_text(
        "import unittest\n\n"
        "from sample import normalize\n\n"
        "class NormalizeTest(unittest.TestCase):\n"
        "    def test_value(self):\n"
        "        self.assertEqual(normalize(' X '), 'wrong')\n",
        encoding="utf-8",
    )
    parsed = RepoParser().parse(tmp_path)
    normalize = next(
        function for function in parsed.functions if function.name == "normalize"
    )

    result = CoverageRunner(timeout=10).run_command_coverage(
        tmp_path,
        parsed.functions,
        [
            "{python}",
            "-m",
            "unittest",
            "-q",
            "test_sample.NormalizeTest.test_value",
        ],
        test_name="test_sample.NormalizeTest.test_value",
    )

    assert result.success is False
    assert result.returncode == 1
    assert normalize.id in result.covered_function_ids
    assert normalize.id in result.covered_function_lines
    assert "'x' != 'wrong'" in result.stderr


def test_coverage_runner_rejects_non_test_python_modules(tmp_path):
    with pytest.raises(ValueError, match="Unsupported coverage test module"):
        CoverageRunner(timeout=10).run_command_coverage(
            tmp_path,
            [],
            ["{python}", "-m", "http.server"],
            test_name="unsafe-module",
        )
