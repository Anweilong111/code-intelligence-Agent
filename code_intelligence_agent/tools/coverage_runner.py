from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from code_intelligence_agent.core.models import CodeEntity, TestExecutionSummary


@dataclass(frozen=True)
class TestCoverageResult:
    test_name: str
    test_id: str
    success: bool
    returncode: int
    covered_function_ids: set[str] = field(default_factory=set)
    covered_function_lines: dict[str, set[int]] = field(default_factory=dict)
    covered_branch_outcomes: dict[str, set[str]] = field(default_factory=dict)
    covered_path_fragments: dict[str, set[str]] = field(default_factory=dict)
    covered_function_line_counts: dict[str, int] = field(default_factory=dict)
    function_line_coverage: dict[str, float] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""


class CoverageRunner:
    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    def run_test_coverage(
        self,
        repo_path: str | Path,
        functions: list[CodeEntity],
        test_name: str,
    ) -> TestCoverageResult:
        repo = Path(repo_path).resolve()
        test_function = _find_test_function(functions, test_name)
        test_id = test_function.id if test_function is not None else test_name
        target = _pytest_target(repo, test_function, test_name)
        raw = self._run_trace(repo, target)
        covered = _covered_functions(raw.get("covered_lines", {}), functions, repo)
        covered_lines = _covered_function_lines(
            raw.get("covered_lines", {}), functions, repo
        )
        line_counts = {
            function_id: len(lines)
            for function_id, lines in covered_lines.items()
        }
        line_coverage = _function_line_coverage(line_counts, functions)
        branch_outcomes = _covered_branch_outcomes(functions, covered_lines)
        path_fragments = _covered_path_fragments(
            functions=functions,
            test_label=(test_function.name if test_function is not None else test_name),
            covered_lines_by_function=covered_lines,
            call_events=raw.get("call_events", []),
            line_events=raw.get("line_events", []),
        )
        return TestCoverageResult(
            test_name=test_name,
            test_id=test_id,
            success=raw.get("returncode") == 0,
            returncode=int(raw.get("returncode", -1)),
            covered_function_ids=covered,
            covered_function_lines=covered_lines,
            covered_branch_outcomes=branch_outcomes,
            covered_path_fragments=path_fragments,
            covered_function_line_counts=line_counts,
            function_line_coverage=line_coverage,
            stdout=str(raw.get("stdout", "")),
            stderr=str(raw.get("stderr", "")),
        )

    def build_summary(
        self,
        repo_path: str | Path,
        functions: list[CodeEntity],
        failing_tests: list[str],
        passed_tests: list[str],
    ) -> TestExecutionSummary:
        failed_ids: set[str] = set()
        passed_ids: set[str] = set()
        coverage: dict[str, set[str]] = {}
        line_coverage: dict[str, dict[str, float]] = {}
        covered_lines: dict[str, dict[str, set[int]]] = {}
        branch_coverage: dict[str, dict[str, set[str]]] = {}
        path_coverage: dict[str, dict[str, set[str]]] = {}
        traceback_function_ids: set[str] = set()
        test_names: dict[str, str] = {}
        failure_messages: dict[str, str] = {}

        for test_name in failing_tests:
            result = self.run_test_coverage(repo_path, functions, test_name)
            failed_ids.add(result.test_id)
            coverage[result.test_id] = set(result.covered_function_ids)
            line_coverage[result.test_id] = dict(result.function_line_coverage)
            covered_lines[result.test_id] = {
                function_id: set(lines)
                for function_id, lines in result.covered_function_lines.items()
            }
            branch_coverage[result.test_id] = {
                function_id: set(outcomes)
                for function_id, outcomes in result.covered_branch_outcomes.items()
            }
            path_coverage[result.test_id] = {
                function_id: set(fragments)
                for function_id, fragments in result.covered_path_fragments.items()
            }
            traceback_function_ids.update(result.covered_function_ids)
            test_names[result.test_id] = result.test_name
            failure_messages[result.test_id] = _failure_text(result.stdout, result.stderr)
        for test_name in passed_tests:
            result = self.run_test_coverage(repo_path, functions, test_name)
            passed_ids.add(result.test_id)
            coverage[result.test_id] = set(result.covered_function_ids)
            line_coverage[result.test_id] = dict(result.function_line_coverage)
            covered_lines[result.test_id] = {
                function_id: set(lines)
                for function_id, lines in result.covered_function_lines.items()
            }
            branch_coverage[result.test_id] = {
                function_id: set(outcomes)
                for function_id, outcomes in result.covered_branch_outcomes.items()
            }
            path_coverage[result.test_id] = {
                function_id: set(fragments)
                for function_id, fragments in result.covered_path_fragments.items()
            }
            test_names[result.test_id] = result.test_name

        return TestExecutionSummary(
            failed_tests=failed_ids,
            passed_tests=passed_ids,
            coverage=coverage,
            line_coverage=line_coverage,
            covered_lines=covered_lines,
            branch_coverage=branch_coverage,
            path_coverage=path_coverage,
            traceback_function_ids=traceback_function_ids,
            test_names=test_names,
            failure_messages=failure_messages,
        )

    def _run_trace(self, repo: Path, target: str) -> dict:
        script = _trace_script()
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".py",
            delete=False,
        ) as handle:
            handle.write(script)
            script_path = Path(handle.name)
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    str(repo),
                    target,
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "returncode": -1,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
                "covered_lines": {},
                "call_events": [],
                "line_events": [],
            }
        finally:
            script_path.unlink(missing_ok=True)

        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError:
            data = {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "covered_lines": {},
                "call_events": [],
                "line_events": [],
            }
        if completed.stderr and not data.get("stderr"):
            data["stderr"] = completed.stderr
        return data


def _trace_script() -> str:
    return r'''
import contextlib
import io
import json
import os
import sys

repo = os.path.abspath(sys.argv[1])
target = sys.argv[2]
sys.path.insert(0, repo)

stdout = io.StringIO()
stderr = io.StringIO()
covered = {}
call_events = []
line_events = []
active_stack = []

def in_repo(filename):
    filename = os.path.abspath(filename)
    if "<" in filename or ">" in filename:
        return False
    try:
        return os.path.commonpath([repo, filename]) == repo
    except ValueError:
        return False

def trace_func(frame, event, arg):
    filename = os.path.abspath(frame.f_code.co_filename)
    if not in_repo(filename):
        return None
    frame_key = {
        "filename": filename,
        "name": frame.f_code.co_name,
        "firstlineno": frame.f_code.co_firstlineno,
    }
    if event == "line":
        covered.setdefault(filename, set()).add(frame.f_lineno)
        if len(line_events) < 5000:
            line_events.append({
                "filename": filename,
                "lineno": frame.f_lineno,
            })
    elif event == "call":
        active_stack.append(frame_key)
        if len(call_events) < 2000:
            call_events.append({
                "event": "call",
                "filename": filename,
                "name": frame.f_code.co_name,
                "firstlineno": frame.f_code.co_firstlineno,
                "is_coroutine": bool(frame.f_code.co_flags & (0x80 | 0x200)),
                "stack": list(active_stack),
            })
    elif event == "return":
        for index in range(len(active_stack) - 1, -1, -1):
            current = active_stack[index]
            if (
                current.get("filename") == filename
                and current.get("name") == frame.f_code.co_name
                and current.get("firstlineno") == frame.f_code.co_firstlineno
            ):
                del active_stack[index:]
                break
    elif event == "exception":
        exc_type = ""
        if arg and len(arg) >= 1:
            exc_type = getattr(arg[0], "__name__", str(arg[0]))
        if len(call_events) < 2000:
            call_events.append({
                "event": "exception",
                "filename": filename,
                "name": frame.f_code.co_name,
                "firstlineno": frame.f_code.co_firstlineno,
                "exception": exc_type,
                "stack": list(active_stack),
            })
    return trace_func

with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    def run_pytest():
        import pytest
        return pytest.main(["-q", target])
    try:
        sys.settrace(trace_func)
        returncode = run_pytest()
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    except BaseException:
        import traceback
        traceback.print_exc()
        returncode = 1
    finally:
        sys.settrace(None)

print(json.dumps({
    "returncode": returncode,
    "stdout": stdout.getvalue(),
    "stderr": stderr.getvalue(),
    "covered_lines": {key: sorted(value) for key, value in covered.items()},
    "call_events": call_events,
    "line_events": line_events,
}))
'''


def _find_test_function(
    functions: list[CodeEntity],
    test_name: str,
) -> CodeEntity | None:
    for function in functions:
        qualified = function.metadata.get("qualified_name", function.name)
        if function.metadata.get("is_test") and test_name in {function.name, qualified}:
            return function
    return None


def _pytest_target(repo: Path, test_function: CodeEntity | None, test_name: str) -> str:
    if "::" in test_name or test_name.endswith(".py"):
        return test_name
    if test_function is None:
        return test_name
    relative = Path(test_function.file_path).resolve().relative_to(repo).as_posix()
    class_name = test_function.metadata.get("class_name")
    if class_name:
        return f"{relative}::{class_name}::{test_function.name}"
    return f"{relative}::{test_function.name}"


def _covered_functions(
    covered_lines: dict[str, list[int]],
    functions: list[CodeEntity],
    repo: Path,
) -> set[str]:
    covered_ids = set()
    line_sets = {
        Path(path).resolve(): set(lines) for path, lines in covered_lines.items()
    }
    for function in functions:
        if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
            continue
        function_path = Path(function.file_path).resolve()
        lines = line_sets.get(function_path, set())
        if any(function.start_line < line <= function.end_line for line in lines):
            covered_ids.add(function.id)
    return covered_ids


def _covered_function_lines(
    covered_lines: dict[str, list[int]],
    functions: list[CodeEntity],
    repo: Path,
) -> dict[str, set[int]]:
    output: dict[str, set[int]] = {}
    line_sets = {
        Path(path).resolve(): set(lines) for path, lines in covered_lines.items()
    }
    for function in functions:
        if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
            continue
        function_path = Path(function.file_path).resolve()
        lines = line_sets.get(function_path, set())
        executable_lines = _function_executable_lines(function)
        covered = executable_lines.intersection(lines)
        if covered:
            output[function.id] = covered
    return output


def _function_line_coverage(
    covered_line_counts: dict[str, int],
    functions: list[CodeEntity],
) -> dict[str, float]:
    ratios: dict[str, float] = {}
    by_id = {function.id: function for function in functions}
    for function_id, covered_count in covered_line_counts.items():
        executable_count = len(_function_executable_lines(by_id[function_id]))
        if executable_count:
            ratios[function_id] = round(covered_count / executable_count, 4)
    return ratios


def _covered_branch_outcomes(
    functions: list[CodeEntity],
    covered_lines_by_function: dict[str, set[int]],
) -> dict[str, set[str]]:
    outcomes: dict[str, set[str]] = {}
    for function in functions:
        lines = covered_lines_by_function.get(function.id, set())
        if not lines:
            continue
        function_outcomes = _function_branch_outcomes(function, lines)
        if function_outcomes:
            outcomes[function.id] = function_outcomes
    return outcomes


def _covered_path_fragments(
    *,
    functions: list[CodeEntity],
    test_label: str,
    covered_lines_by_function: dict[str, set[int]],
    call_events: list[dict] | None = None,
    line_events: list[dict] | None = None,
) -> dict[str, set[str]]:
    fragments_by_function: dict[str, set[str]] = {}
    covered_ids = set(covered_lines_by_function)
    line_sequence = [
        function.id
        for function in sorted(
            functions,
            key=lambda item: (
                min(covered_lines_by_function.get(item.id, {item.start_line})),
                item.start_line,
                item.end_line,
            ),
        )
        if function.id in covered_ids
        and not function.metadata.get("is_test")
        and not function.metadata.get("is_test_file")
    ]
    labels = _function_labels(functions)
    for function_id in line_sequence:
        fragments_by_function.setdefault(function_id, set()).add(
            f"{test_label} -> {labels.get(function_id, function_id)}"
        )
    _add_sequence_windows(
        fragments_by_function=fragments_by_function,
        sequence=line_sequence,
        labels=labels,
        prefix="",
    )
    call_sequence, exception_events, call_records = _call_sequence_from_events(
        functions,
        call_events or [],
    )
    execution_sequence = _execution_sequence_from_line_events(
        functions,
        line_events or [],
    )
    if execution_sequence:
        _add_sequence_windows(
            fragments_by_function=fragments_by_function,
            sequence=execution_sequence,
            labels=labels,
            prefix=f"pathseq:{test_label} -> ",
        )
    if call_sequence:
        for function_id in call_sequence:
            fragments_by_function.setdefault(function_id, set()).add(
                f"callseq:{test_label} -> {labels.get(function_id, function_id)}"
            )
        _add_sequence_windows(
            fragments_by_function=fragments_by_function,
            sequence=call_sequence,
            labels=labels,
            prefix=f"callseq:{test_label} -> ",
        )
        _add_async_sequence_windows(
            fragments_by_function=fragments_by_function,
            call_records=call_records,
            labels=labels,
            test_label=test_label,
        )
    for event in exception_events:
        function_id = event["function_id"]
        exception_name = event["exception"]
        label = labels.get(function_id, function_id)
        fragments_by_function.setdefault(function_id, set()).add(
            f"exception:{test_label} -> {label}:{exception_name}"
        )
        stack_ids = event.get("stack", [])
        if len(stack_ids) >= 2:
            stack_labels = [labels.get(item, item) for item in stack_ids]
            fragment = (
                f"exception_path:{test_label} -> "
                + " -> ".join(stack_labels)
                + f":{exception_name}"
            )
            for stack_id in set(stack_ids):
                fragments_by_function.setdefault(stack_id, set()).add(fragment)
    _add_loop_boundary_fragments(
        fragments_by_function=fragments_by_function,
        functions=functions,
        labels=labels,
        test_label=test_label,
        line_events=line_events or [],
    )
    return fragments_by_function


def _add_sequence_windows(
    *,
    fragments_by_function: dict[str, set[str]],
    sequence: list[str],
    labels: dict[str, str],
    prefix: str,
) -> None:
    for size in (2, 3):
        if len(sequence) < size:
            continue
        for index in range(0, len(sequence) - size + 1):
            window = sequence[index : index + size]
            if len(set(window)) < 2:
                continue
            fragment = prefix + " -> ".join(
                labels.get(function_id, function_id) for function_id in window
            )
            for function_id in set(window):
                fragments_by_function.setdefault(function_id, set()).add(fragment)


def _call_sequence_from_events(
    functions: list[CodeEntity],
    call_events: list[dict],
) -> tuple[list[str], list[dict], list[tuple[str, bool]]]:
    lookup = {
        (Path(function.file_path).resolve(), function.start_line): function
        for function in functions
    }
    sequence: list[str] = []
    call_records: list[tuple[str, bool]] = []
    exception_events: list[dict] = []
    for event in call_events:
        function = lookup.get(
            (
                Path(str(event.get("filename", ""))).resolve(),
                int(event.get("firstlineno", 0)),
            )
        )
        if function is None:
            continue
        if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
            continue
        function_id = function.id
        if event.get("event") == "call":
            if not sequence or sequence[-1] != function_id:
                sequence.append(function_id)
                call_records.append(
                    (
                        function_id,
                        bool(event.get("is_coroutine"))
                        or bool(function.metadata.get("is_async")),
                    )
                )
        elif event.get("event") == "exception":
            exception_name = str(event.get("exception") or "Exception")
            stack_ids = _stack_function_ids(
                stack=event.get("stack", []),
                lookup=lookup,
            )
            if function_id not in stack_ids:
                stack_ids.append(function_id)
            exception_events.append(
                {
                    "function_id": function_id,
                    "exception": exception_name,
                    "stack": stack_ids,
                }
            )
    return sequence, exception_events, call_records


def _add_async_sequence_windows(
    *,
    fragments_by_function: dict[str, set[str]],
    call_records: list[tuple[str, bool]],
    labels: dict[str, str],
    test_label: str,
) -> None:
    for size in (2, 3):
        if len(call_records) < size:
            continue
        for index in range(0, len(call_records) - size + 1):
            window = call_records[index : index + size]
            function_ids = [function_id for function_id, _ in window]
            if len(set(function_ids)) < 2 or not any(is_async for _, is_async in window):
                continue
            fragment = f"asyncseq:{test_label} -> " + " -> ".join(
                labels.get(function_id, function_id) for function_id in function_ids
            )
            for function_id in set(function_ids):
                fragments_by_function.setdefault(function_id, set()).add(fragment)


def _stack_function_ids(
    *,
    stack,
    lookup: dict[tuple[Path, int], CodeEntity],
) -> list[str]:
    output: list[str] = []
    if not isinstance(stack, list):
        return output
    for item in stack:
        if not isinstance(item, dict):
            continue
        function = lookup.get(
            (
                Path(str(item.get("filename", ""))).resolve(),
                int(item.get("firstlineno", 0)),
            )
        )
        if function is None:
            continue
        if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
            continue
        if output and output[-1] == function.id:
            continue
        output.append(function.id)
    return output


def _execution_sequence_from_line_events(
    functions: list[CodeEntity],
    line_events: list[dict],
) -> list[str]:
    by_file: dict[Path, list[CodeEntity]] = {}
    for function in functions:
        if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
            continue
        by_file.setdefault(Path(function.file_path).resolve(), []).append(function)
    for items in by_file.values():
        items.sort(key=lambda function: (function.start_line, function.end_line))

    sequence: list[str] = []
    for event in line_events:
        path = Path(str(event.get("filename", ""))).resolve()
        line = int(event.get("lineno", 0))
        function = _function_for_line(by_file.get(path, []), line)
        if function is None:
            continue
        if not sequence or sequence[-1] != function.id:
            sequence.append(function.id)
    return sequence


def _add_loop_boundary_fragments(
    *,
    fragments_by_function: dict[str, set[str]],
    functions: list[CodeEntity],
    labels: dict[str, str],
    test_label: str,
    line_events: list[dict],
) -> None:
    line_counts = _line_event_counts(line_events)
    for function in functions:
        if function.metadata.get("is_test") or function.metadata.get("is_test_file"):
            continue
        counts = line_counts.get(Path(function.file_path).resolve(), {})
        if not counts:
            continue
        for line, state in _function_loop_boundary_states(function, counts):
            label = labels.get(function.id, function.id)
            fragments_by_function.setdefault(function.id, set()).add(
                f"loopseq:{test_label} -> {label}:{line}:{state}"
            )


def _function_loop_boundary_states(
    function: CodeEntity,
    line_counts: dict[int, int],
) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(textwrap.dedent(function.source))
    except SyntaxError:
        return []
    root = tree.body[0] if tree.body else None
    if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []

    states = []
    for node in ast.walk(root):
        if not isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            continue
        line = _absolute_line(function, node)
        if line_counts.get(line, 0) == 0:
            continue
        body_hits = [
            line_counts.get(body_line, 0)
            for body_line in _node_body_lines(function, node.body)
        ]
        max_body_hits = max(body_hits, default=0)
        if max_body_hits == 0:
            state = "zero"
        elif max_body_hits == 1:
            state = "single"
        else:
            state = "multi"
        states.append((line, state))
    return states


def _line_event_counts(line_events: list[dict]) -> dict[Path, dict[int, int]]:
    counts: dict[Path, dict[int, int]] = {}
    for event in line_events:
        path = Path(str(event.get("filename", ""))).resolve()
        line = int(event.get("lineno", 0))
        if line <= 0:
            continue
        per_file = counts.setdefault(path, {})
        per_file[line] = per_file.get(line, 0) + 1
    return counts


def _function_for_line(functions: list[CodeEntity], line: int) -> CodeEntity | None:
    containing = [
        function
        for function in functions
        if function.start_line < line <= function.end_line
    ]
    if not containing:
        return None
    return max(containing, key=lambda function: function.start_line)


def _function_labels(functions: list[CodeEntity]) -> dict[str, str]:
    return {
        function.id: function.metadata.get("qualified_name", function.name)
        for function in functions
    }


def _function_branch_outcomes(
    function: CodeEntity,
    covered_lines: set[int],
) -> set[str]:
    try:
        tree = ast.parse(textwrap.dedent(function.source))
    except SyntaxError:
        return set()
    root = tree.body[0] if tree.body else None
    if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()

    outcomes: set[str] = set()
    for node in ast.walk(root):
        if isinstance(node, ast.If):
            _add_conditional_branch_outcomes(
                outcomes=outcomes,
                prefix="if",
                function=function,
                node=node,
                covered_lines=covered_lines,
            )
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            _add_loop_branch_outcomes(
                outcomes=outcomes,
                function=function,
                node=node,
                covered_lines=covered_lines,
            )
        elif isinstance(node, ast.Try):
            _add_try_branch_outcomes(
                outcomes=outcomes,
                function=function,
                node=node,
                covered_lines=covered_lines,
            )
    return outcomes


def _add_conditional_branch_outcomes(
    *,
    outcomes: set[str],
    prefix: str,
    function: CodeEntity,
    node: ast.If,
    covered_lines: set[int],
) -> None:
    line = _absolute_line(function, node)
    if line not in covered_lines:
        return
    body_lines = _node_body_lines(function, node.body)
    orelse_lines = _node_body_lines(function, node.orelse)
    body_covered = bool(body_lines.intersection(covered_lines))
    orelse_covered = bool(orelse_lines.intersection(covered_lines))
    if body_covered:
        outcomes.add(f"{prefix}:{line}:true")
    if orelse_covered or (not body_covered and not orelse_lines):
        outcomes.add(f"{prefix}:{line}:false")


def _add_loop_branch_outcomes(
    *,
    outcomes: set[str],
    function: CodeEntity,
    node: ast.For | ast.AsyncFor | ast.While,
    covered_lines: set[int],
) -> None:
    line = _absolute_line(function, node)
    if line not in covered_lines:
        return
    body_covered = bool(_node_body_lines(function, node.body).intersection(covered_lines))
    outcomes.add(f"loop:{line}:taken" if body_covered else f"loop:{line}:skipped")


def _add_try_branch_outcomes(
    *,
    outcomes: set[str],
    function: CodeEntity,
    node: ast.Try,
    covered_lines: set[int],
) -> None:
    line = _absolute_line(function, node)
    body_covered = bool(_node_body_lines(function, node.body).intersection(covered_lines))
    if body_covered:
        outcomes.add(f"try:{line}:body")
    for handler in node.handlers:
        handler_line = _absolute_line(function, handler)
        handler_covered = bool(
            _node_body_lines(function, handler.body).intersection(covered_lines)
            or handler_line in covered_lines
        )
        if handler_covered:
            outcomes.add(f"try:{line}:except:{handler_line}")
    if _node_body_lines(function, node.orelse).intersection(covered_lines):
        outcomes.add(f"try:{line}:else")
    if _node_body_lines(function, node.finalbody).intersection(covered_lines):
        outcomes.add(f"try:{line}:finally")


def _node_body_lines(function: CodeEntity, nodes: list[ast.stmt]) -> set[int]:
    lines: set[int] = set()
    for node in nodes:
        for child in ast.walk(node):
            lineno = getattr(child, "lineno", None)
            if lineno is not None:
                lines.add(function.start_line + lineno - 1)
    return lines


def _absolute_line(function: CodeEntity, node: ast.AST) -> int:
    return function.start_line + int(getattr(node, "lineno", 1)) - 1


def _function_executable_lines(function: CodeEntity) -> set[int]:
    lines = set()
    for offset, line in enumerate(function.source.splitlines()):
        absolute_line = function.start_line + offset
        stripped = line.strip()
        if absolute_line <= function.start_line:
            continue
        if not stripped or stripped.startswith("#"):
            continue
        lines.add(absolute_line)
    return lines


def _failure_text(stdout: str, stderr: str, limit: int = 12000) -> str:
    text = "\n".join(part for part in [stdout, stderr] if part)
    if len(text) <= limit:
        return text
    return text[-limit:]
