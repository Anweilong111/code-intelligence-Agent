from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Any


def build_repository_test_dynamic_evidence(
    execution_result: dict[str, Any] | None,
    retry_execution_result: dict[str, Any] | None = None,
    *,
    execution_plan: dict[str, Any] | None = None,
    retry_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    planned = _dict(execution_result)
    retry = _dict(retry_execution_result)
    plan = _dict(execution_plan)
    retry_plan_payload = _dict(retry_plan)
    primary_command = str(
        planned.get("command")
        or plan.get("recommended_execution_command")
        or retry_plan_payload.get("original_command")
        or ""
    )
    retry_command = str(
        retry.get("command")
        or retry.get("retry_command")
        or retry_plan_payload.get("retry_command")
        or ""
    )
    planned_summary = _execution_summary(planned, source="planned_execution_result")
    retry_summary = _execution_summary(retry, source="retry_execution_result")
    selected = _select_evidence_execution(planned_summary, retry_summary)
    selected_category = str(selected.get("failure_category") or "")
    failing_tests = []
    for failure_text in _failure_texts_for_nodeid_extraction(selected):
        failing_tests.extend(_extract_failing_tests(failure_text))
    failing_tests = _dedupe_failing_tests(failing_tests)
    traceback_frames = []
    for failure_text in _failure_texts_for_nodeid_extraction(selected):
        traceback_frames.extend(_extract_traceback_frames(failure_text))
    traceback_frames = _dedupe_traceback_frames(traceback_frames)
    if not failing_tests and selected_category == "test_assertion_failure":
        failing_tests = _failing_tests_from_pytest_command(
            str(selected.get("command") or "")
        )
    selected_status = str(selected.get("status") or "")
    level, reason = _evidence_level(selected)
    if level == "failing_tests" and not failing_tests:
        if traceback_frames:
            level = "traceback"
            reason = "test_assertion_failure_with_traceback"
        else:
            level = "unknown_failure"
            reason = "test_assertion_failure_without_nodeid"
    usable_for_localization = bool(
        selected_category == "test_assertion_failure"
        and bool(selected.get("executed", False))
        and (bool(failing_tests) or bool(traceback_frames))
    )
    usable_for_regression_validation = bool(
        selected_status == "pass"
        and bool(selected.get("executed", False))
        and bool(selected.get("command", ""))
    )
    usable_for_patch_validation = bool(
        level in {"failing_tests", "traceback"}
        and bool(selected.get("executed", False))
        and bool(selected.get("command", ""))
        and selected_category == "test_assertion_failure"
        and (bool(failing_tests) or bool(traceback_frames))
    )
    recommended_command = _recommended_validation_command(
        selected=selected,
        primary_command=primary_command,
        retry_command=retry_command,
    )
    next_actions = _next_actions(
        evidence_level=level,
        usable_for_localization=usable_for_localization,
        usable_for_patch_validation=usable_for_patch_validation,
        command=recommended_command,
    )
    failed_count = _int(selected.get("failed", 0))
    if failing_tests and failed_count == 0:
        failed_count = len(failing_tests)
    status = _status_for_level(level)
    return {
        "status": status,
        "reason": reason,
        "evidence_level": level,
        "source": str(selected.get("source") or "none"),
        "selected_execution": selected,
        "planned_execution": planned_summary,
        "retry_execution": retry_summary,
        "primary_validation_command": primary_command,
        "retry_validation_command": retry_command,
        "recommended_validation_command": recommended_command,
        "failure_category": selected_category,
        "failure_signal": str(selected.get("failure_signal") or ""),
        "diagnostic_summary": str(selected.get("diagnostic_summary") or ""),
        "passed_test_count": _int(selected.get("passed", 0)),
        "failed_test_count": failed_count,
        "failing_test_count": len(failing_tests),
        "failing_tests": failing_tests,
        "traceback_frame_count": len(traceback_frames),
        "traceback_frames": traceback_frames,
        "usable_for_localization": usable_for_localization,
        "usable_for_regression_validation": usable_for_regression_validation,
        "usable_for_patch_validation": usable_for_patch_validation,
        "next_actions": next_actions,
    }


def render_repository_test_dynamic_evidence_markdown(payload: dict[str, Any]) -> str:
    selected = _dict(payload.get("selected_execution"))
    lines = [
        "# Repository Test Dynamic Evidence",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Evidence Level: `{_markdown_cell(payload.get('evidence_level', ''))}`",
        f"- Source: `{_markdown_cell(payload.get('source', ''))}`",
        (
            "- Usable For Localization: "
            f"{str(bool(payload.get('usable_for_localization', False))).lower()}"
        ),
        (
            "- Usable For Patch Validation: "
            f"{str(bool(payload.get('usable_for_patch_validation', False))).lower()}"
        ),
        (
            "- Usable For Regression Validation: "
            f"{str(bool(payload.get('usable_for_regression_validation', False))).lower()}"
        ),
        (
            "- Recommended Validation Command: "
            f"`{_markdown_cell(payload.get('recommended_validation_command') or 'none')}`"
        ),
        f"- Failure Category: `{_markdown_cell(payload.get('failure_category') or 'none')}`",
        f"- Failure Signal: `{_markdown_cell(payload.get('failure_signal') or 'none')}`",
        f"- Passed Tests: {_int(payload.get('passed_test_count', 0))}",
        f"- Failed Tests: {_int(payload.get('failed_test_count', 0))}",
        f"- Parsed Failing Tests: {_int(payload.get('failing_test_count', 0))}",
        f"- Parsed Traceback Frames: {_int(payload.get('traceback_frame_count', 0))}",
        "",
        "## Selected Execution",
        "",
        f"- Status: `{_markdown_cell(selected.get('status') or 'none')}`",
        f"- Executed: {str(bool(selected.get('executed', False))).lower()}",
        f"- Command: `{_markdown_cell(selected.get('command') or 'none')}`",
        f"- Return Code: {_markdown_cell(selected.get('returncode'))}",
        f"- Failure Context Lines: {_int(selected.get('failure_context_line_count', 0))}",
        "",
        "```text",
        str(selected.get("failure_context") or ""),
        "```",
        "",
        "## Failing Tests",
        "",
        "| Nodeid | Path | Test |",
        "| --- | --- | --- |",
    ]
    failing_tests = _list(payload.get("failing_tests"))
    for item in failing_tests:
        row = _dict(item)
        lines.append(
            "| "
            f"`{_markdown_cell(row.get('nodeid', ''))}` | "
            f"`{_markdown_cell(row.get('path', ''))}` | "
            f"`{_markdown_cell(row.get('test_name', ''))}` |"
        )
    if not failing_tests:
        lines.append("| none | none | none |")
    lines.extend(
        [
            "",
            "## Traceback Frames",
            "",
            "| Path | Line | Function |",
            "| --- | ---: | --- |",
        ]
    )
    traceback_frames = _list(payload.get("traceback_frames"))
    for item in traceback_frames:
        row = _dict(item)
        lines.append(
            "| "
            f"`{_markdown_cell(row.get('path', ''))}` | "
            f"{_int(row.get('line', 0))} | "
            f"`{_markdown_cell(row.get('function_name', ''))}` |"
        )
    if not traceback_frames:
        lines.append("| none | 0 | none |")
    lines.extend(["", "## Diagnostic Summary", ""])
    lines.append(_markdown_cell(payload.get("diagnostic_summary", "")) or "none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    return "\n".join(lines)


def write_repository_test_dynamic_evidence_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_dynamic_evidence.json"
    markdown_path = root / "repository_test_dynamic_evidence.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_dynamic_evidence_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_dynamic_evidence_json": str(json_path),
        "repository_test_dynamic_evidence_markdown": str(markdown_path),
    }


def _execution_summary(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
    if not payload:
        return {
            "source": source,
            "present": False,
            "status": "",
            "executed": False,
            "reason": "",
            "command": "",
            "returncode": None,
            "passed": 0,
            "failed": 0,
            "failure_category": "",
            "failure_signal": "",
            "diagnostic_summary": "",
            "failure_context": "",
            "failure_context_line_count": 0,
            "stdout_preview": "",
            "stderr_preview": "",
        }
    return {
        "source": source,
        "present": True,
        "status": str(payload.get("status") or ""),
        "executed": bool(payload.get("executed", False)),
        "reason": str(payload.get("reason") or ""),
        "command": str(payload.get("command") or payload.get("retry_command") or ""),
        "returncode": payload.get("returncode"),
        "passed": _int(payload.get("passed", 0)),
        "failed": _int(payload.get("failed", 0)),
        "failure_category": str(payload.get("failure_category") or ""),
        "failure_signal": str(payload.get("failure_signal") or ""),
        "diagnostic_summary": str(payload.get("diagnostic_summary") or ""),
        "failure_context": str(payload.get("failure_context") or ""),
        "failure_context_line_count": _int(
            payload.get("failure_context_line_count", 0)
        ),
        "stdout_preview": str(payload.get("stdout_preview") or ""),
        "stderr_preview": str(payload.get("stderr_preview") or ""),
    }


def _select_evidence_execution(
    planned: dict[str, Any],
    retry: dict[str, Any],
) -> dict[str, Any]:
    candidates = [retry, planned]
    for candidate in candidates:
        if (
            bool(candidate.get("present", False))
            and bool(candidate.get("executed", False))
            and candidate.get("failure_category") == "test_assertion_failure"
        ):
            return candidate
    for candidate in candidates:
        if bool(candidate.get("present", False)) and bool(candidate.get("executed", False)):
            return candidate
    for candidate in (planned, retry):
        if bool(candidate.get("present", False)):
            return candidate
    return _execution_summary({}, source="none")


def _evidence_level(selected: dict[str, Any]) -> tuple[str, str]:
    if not selected.get("present"):
        return "none", "no_execution_result"
    if not bool(selected.get("executed", False)):
        return "not_executed", str(selected.get("reason") or "not_executed")
    status = str(selected.get("status") or "")
    category = str(selected.get("failure_category") or "")
    if status == "pass":
        return "passing_tests", "repository_tests_passed"
    if category == "test_assertion_failure":
        return "failing_tests", "test_assertion_failure"
    if category in {
        "missing_dependency",
        "missing_pytest_fixture",
        "command_usage_error",
        "pytest_warning_as_error",
    }:
        return "environment_failure", category
    if category in {"pytest_collection_error", "syntax_error", "no_tests_collected"}:
        return "collection_failure", category
    if category == "timeout":
        return "timeout", "timeout"
    return "unknown_failure", category or "command_failed"


def _recommended_validation_command(
    *,
    selected: dict[str, Any],
    primary_command: str,
    retry_command: str,
) -> str:
    selected_command = str(selected.get("command") or "")
    if selected_command:
        return selected_command
    if retry_command:
        return retry_command
    return primary_command


def _next_actions(
    *,
    evidence_level: str,
    usable_for_localization: bool,
    usable_for_patch_validation: bool,
    command: str,
) -> list[str]:
    if evidence_level == "failing_tests":
        actions = [
            "Use parsed failing test identifiers as dynamic evidence for Phase 2 fault localization.",
        ]
        if command:
            actions.append(
                f"Validate generated patches with the same failing command: {command}"
            )
        return actions
    if evidence_level == "traceback":
        actions = [
            "Use traceback frames as dynamic evidence for Phase 2 fault localization.",
        ]
        if command:
            actions.append(
                f"Validate generated patches with the same failing command: {command}"
            )
        return actions
    if evidence_level == "passing_tests":
        return [
            "Use the passing command as a regression check after patch generation.",
            "Provide a failing test, mutation ground truth, bug report, or controlled failure overlay before localization.",
        ]
    if evidence_level == "environment_failure":
        return [
            "Resolve repository dependency or command environment failures before localization.",
            "Rerun repository test execution after environment setup succeeds.",
        ]
    if evidence_level == "collection_failure":
        return [
            "Fix pytest collection/import/syntax issues before using dynamic test evidence.",
            "Retry with a narrower planned test command if available.",
        ]
    if evidence_level == "timeout":
        return [
            "Use a narrower planned command or increase repository_test_timeout.",
        ]
    if evidence_level == "not_executed":
        return [
            "Materialize a repository checkout and execute the planned test command.",
        ]
    if usable_for_localization or usable_for_patch_validation:
        return []
    return ["Inspect repository test stdout/stderr previews before patch generation."]


def _status_for_level(level: str) -> str:
    if level in {"failing_tests", "traceback", "passing_tests"}:
        return "pass"
    if level == "none":
        return "skipped"
    return "warning"


def _extract_failing_tests(text: str) -> list[dict[str, str]]:
    tests: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = _strip_log_source_prefix(line.strip())
        if not stripped:
            continue
        nodeid = (
            _failed_prefix_nodeid(stripped)
            or _inline_failed_nodeid(stripped)
            or _unittest_failure_nodeid(stripped)
        )
        if not nodeid:
            continue
        path, test_name = _split_nodeid(nodeid)
        tests.append(
            {
                "nodeid": nodeid,
                "path": path,
                "test_name": test_name,
                "source_line": stripped,
            }
        )
    return tests


def _extract_traceback_frames(text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = _strip_log_source_prefix(line.strip())
        if not stripped:
            continue
        frame = _python_traceback_frame(stripped) or _pytest_traceback_frame(
            stripped
        )
        if frame:
            frames.append(frame)
    return frames


def _python_traceback_frame(line: str) -> dict[str, Any]:
    match = re.match(r'^File "([^"]+\.py)", line (\d+), in ([A-Za-z_][\w]*)', line)
    if not match:
        return {}
    path = _safe_traceback_path(match.group(1))
    if not path:
        return {}
    return {
        "path": path,
        "line": _int(match.group(2)),
        "function_name": match.group(3),
        "source_line": line,
        "format": "python_traceback",
    }


def _pytest_traceback_frame(line: str) -> dict[str, Any]:
    match = re.match(
        r"^([^:\s][^:]*\.py):(\d+):\s+in\s+([A-Za-z_][\w]*)",
        line,
    )
    if not match:
        return {}
    path = _safe_traceback_path(match.group(1))
    if not path:
        return {}
    return {
        "path": path,
        "line": _int(match.group(2)),
        "function_name": match.group(3),
        "source_line": line,
        "format": "pytest_traceback",
    }


def _safe_traceback_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text.endswith(".py"):
        return ""
    if any(char in text for char in ("\x00", "\n", "\r")):
        return ""
    return text


def _failure_texts_for_nodeid_extraction(selected: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for key in ("failure_context", "stdout_preview", "stderr_preview", "failure_signal"):
        value = str(selected.get(key) or "")
        if value:
            texts.append(value)
    return texts


def _strip_log_source_prefix(line: str) -> str:
    return re.sub(r"^\[(stdout|stderr)\]\s+", "", str(line or ""))


def _failing_tests_from_pytest_command(command: str) -> list[dict[str, str]]:
    tests: list[dict[str, str]] = []
    for nodeid in _pytest_nodeids_from_command(command):
        path, test_name = _split_nodeid(nodeid)
        tests.append(
            {
                "nodeid": nodeid,
                "path": path,
                "test_name": test_name,
                "source_line": f"pytest command target: {nodeid}",
            }
        )
    return tests


def _pytest_nodeids_from_command(command: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    if not parts:
        return []
    args = _pytest_argument_parts(parts)
    if not args:
        return []
    nodeids: list[str] = []
    skip_next = False
    options_with_values = {
        "--basetemp",
        "--cache-clear",
        "--color",
        "--confcutdir",
        "--cov",
        "--cov-append",
        "--cov-branch",
        "--cov-config",
        "--cov-report",
        "--ignore",
        "--ignore-glob",
        "--import-mode",
        "--junit-prefix",
        "--junitxml",
        "--maxfail",
        "--override-ini",
        "--rootdir",
        "--tb",
        "-c",
        "-k",
        "-m",
        "-o",
        "-p",
    }
    for part in args:
        if skip_next:
            skip_next = False
            continue
        if part in options_with_values:
            skip_next = True
            continue
        if part.startswith("-"):
            continue
        if "=" in part and part.split("=", 1)[0] in options_with_values:
            continue
        nodeid = _safe_pytest_nodeid(part)
        if nodeid and nodeid not in nodeids:
            nodeids.append(nodeid)
    return nodeids


def _pytest_argument_parts(parts: list[str]) -> list[str]:
    executable = Path(parts[0]).name.lower()
    if (
        len(parts) >= 3
        and executable in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
        and parts[1:3] == ["-m", "pytest"]
    ):
        return parts[3:]
    if executable in {"pytest", "pytest.exe", "py.test", "py.test.exe"}:
        return parts[1:]
    return []


def _safe_pytest_nodeid(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or "::" not in text:
        return ""
    if any(char in text for char in ("\x00", "\n", "\r")):
        return ""
    path_text, separator, suffix = text.partition("::")
    if not separator or not path_text.endswith(".py"):
        return ""
    pure = PurePosixPath(path_text)
    if pure.is_absolute() or ".." in pure.parts:
        return ""
    node_parts = [part.strip() for part in _split_nodeid_parts(suffix)]
    if not node_parts or any(
        not part or any(char in part for char in ("\x00", "\n", "\r"))
        for part in node_parts
    ):
        return ""
    return f"{pure.as_posix()}::{'::'.join(node_parts)}"


def _split_nodeid_parts(value: str) -> list[str]:
    text = str(value or "")
    parts: list[str] = []
    start = 0
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]" and depth > 0:
            depth -= 1
        if depth == 0 and text.startswith("::", index):
            parts.append(text[start:index])
            index += 2
            start = index
            continue
        index += 1
    parts.append(text[start:])
    return parts


def _failed_prefix_nodeid(line: str) -> str:
    if not line.startswith("FAILED "):
        return ""
    candidate = _strip_pytest_nodeid_suffix(line[len("FAILED ") :].strip())
    return _safe_pytest_nodeid(candidate)


def _inline_failed_nodeid(line: str) -> str:
    candidate = _split_outside_brackets(line, " FAILED")
    return _safe_pytest_nodeid(candidate)


def _unittest_failure_nodeid(line: str) -> str:
    match = re.match(
        r"^(FAIL|ERROR):\s+([A-Za-z_][\w]*)\s+\(([A-Za-z_][\w.]+)\)",
        line,
    )
    if not match:
        return ""
    method_name = match.group(2)
    dotted = match.group(3)
    parts = dotted.split(".")
    if len(parts) < 2 or any(not _safe_python_identifier(part) for part in parts):
        return ""
    test_method = parts[-1]
    if test_method != method_name:
        return ""
    if len(parts) >= 3 and parts[-2][0].isupper():
        class_name = parts[-2]
        module_parts = parts[:-2]
        return f"{'/'.join(module_parts)}.py::{class_name}::{test_method}"
    module_parts = parts[:-1]
    return f"{'/'.join(module_parts)}.py::{test_method}"


def _safe_python_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value or "")))


def _strip_pytest_nodeid_suffix(value: str) -> str:
    text = str(value or "").strip()
    if text.endswith(" (nodeid-scoped pytest command)"):
        text = text[: -len(" (nodeid-scoped pytest command)")].strip()
    return _split_outside_brackets(text, " - ")


def _split_outside_brackets(value: str, delimiter: str) -> str:
    text = str(value or "")
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]" and depth > 0:
            depth -= 1
        if depth == 0 and text.startswith(delimiter, index):
            return text[:index].strip()
        index += 1
    return text.strip()


def _split_nodeid(nodeid: str) -> tuple[str, str]:
    if "::" not in nodeid:
        return nodeid, ""
    path, _, test_name = nodeid.partition("::")
    return path, test_name


def _dedupe_failing_tests(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for row in rows:
        nodeid = row.get("nodeid", "")
        if not nodeid or nodeid in seen:
            continue
        seen.add(nodeid)
        deduped.append(row)
    return deduped


def _dedupe_traceback_frames(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        path = str(row.get("path") or "")
        line = _int(row.get("line", 0))
        function_name = str(row.get("function_name") or "")
        key = (path, line, function_name)
        if not path or line <= 0 or not function_name or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
