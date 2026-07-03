from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.repository_test_execution_result import (
    execute_repository_test_plan,
    render_repository_test_execution_result_markdown,
)


def execute_repository_test_retry_plan(
    retry_plan: dict[str, Any],
    *,
    repository_root: str | Path | None = None,
    timeout: int = 20,
    enabled: bool = False,
    python_executable: str | Path | None = None,
    python_executable_source: str = "current_interpreter",
    repository_test_environment_setup_result: dict[str, Any] | None = None,
    runner=None,
) -> dict[str, Any]:
    retry_command = str(retry_plan.get("retry_command") or "").strip()
    retry_recommended = bool(retry_plan.get("retry_recommended", False))
    retry_strategy = str(retry_plan.get("retry_strategy") or "")
    setup_result = _dict(repository_test_environment_setup_result)
    metadata = _retry_metadata(
        retry_plan,
        enabled=enabled,
        python_executable=python_executable,
        python_executable_source=python_executable_source,
        repository_test_environment_setup_result=setup_result,
    )
    if not enabled:
        return _skipped(
            metadata,
            reason="execution_disabled",
            message="Repository test retry execution is disabled.",
        )
    if not retry_recommended:
        return _skipped(
            metadata,
            reason="retry_not_recommended",
            message="Repository test retry plan did not recommend an executable retry.",
        )
    if not retry_command:
        return _skipped(
            metadata,
            reason="no_retry_command",
            message="Repository test retry plan did not produce a retry command.",
        )
    if (
        retry_strategy == "run_environment_setup_then_retry"
        and str(setup_result.get("status") or "") != "pass"
    ):
        return _skipped(
            metadata,
            reason="prerequisites_pending",
            message=(
                "Retry requires successful repository test environment setup before execution."
            ),
        )

    execution_result = execute_repository_test_plan(
        {
            "recommended_execution_command": retry_command,
            "recommended_execution_level": str(retry_plan.get("retry_level") or ""),
            "recommended_execution_risk": str(retry_plan.get("retry_risk") or ""),
            "recommended_execution_scope": retry_strategy,
            "repository_root": str(repository_root or ""),
            "executable_now": True,
            "planned_environment_variables": _dict(
                retry_plan.get("planned_environment_variables")
            ),
        },
        repository_root=repository_root,
        timeout=timeout,
        python_executable=python_executable,
        python_executable_source=python_executable_source,
        runner=runner,
    )
    payload = dict(execution_result)
    payload.update(metadata)
    payload["retry_execution_status"] = payload.get("status")
    payload["retry_execution_reason"] = payload.get("reason")
    payload["message"] = (
        "Repository test retry command completed successfully."
        if payload.get("status") == "pass"
        else payload.get("message", "")
    )
    return payload


def render_repository_test_retry_execution_result_markdown(
    payload: dict[str, Any],
) -> str:
    lines = [
        "# Repository Test Retry Execution Result",
        "",
        f"- Retry Enabled: {str(bool(payload.get('retry_enabled', False))).lower()}",
        f"- Retry Recommended: {str(bool(payload.get('retry_recommended', False))).lower()}",
        f"- Retry Strategy: `{_markdown_cell(payload.get('retry_strategy') or 'none')}`",
        (
            "- Setup Prerequisite Required: "
            f"{str(bool(payload.get('retry_setup_prerequisite_required', False))).lower()}"
        ),
        (
            "- Setup Prerequisite Satisfied: "
            f"{str(bool(payload.get('retry_setup_prerequisite_satisfied', False))).lower()}"
        ),
        (
            "- Setup Prerequisite Status: "
            f"`{_markdown_cell(payload.get('retry_setup_prerequisite_status') or 'none')}`"
        ),
        f"- Original Command: `{_markdown_cell(payload.get('original_command') or 'none')}`",
        f"- Retry Command: `{_markdown_cell(payload.get('retry_command') or 'none')}`",
        "",
        render_repository_test_execution_result_markdown(payload),
    ]
    return "\n".join(lines)


def write_repository_test_retry_execution_result_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_test_retry_execution_result.json"
    markdown_path = root / "repository_test_retry_execution_result.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_test_retry_execution_result_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_test_retry_execution_result_json": str(json_path),
        "repository_test_retry_execution_result_markdown": str(markdown_path),
    }


def _retry_metadata(
    retry_plan: dict[str, Any],
    *,
    enabled: bool,
    python_executable: str | Path | None,
    python_executable_source: str,
    repository_test_environment_setup_result: dict[str, Any],
) -> dict[str, Any]:
    setup_result = _dict(repository_test_environment_setup_result)
    setup_required = (
        str(retry_plan.get("retry_strategy") or "")
        == "run_environment_setup_then_retry"
    )
    setup_status = str(setup_result.get("status") or "")
    return {
        "retry_enabled": enabled,
        "retry_recommended": bool(retry_plan.get("retry_recommended", False)),
        "retry_strategy": str(retry_plan.get("retry_strategy") or ""),
        "retry_plan_status": str(retry_plan.get("status") or ""),
        "retry_plan_reason": str(retry_plan.get("reason") or ""),
        "original_command": str(retry_plan.get("original_command") or ""),
        "retry_command": str(retry_plan.get("retry_command") or ""),
        "retry_level": str(retry_plan.get("retry_level") or ""),
        "retry_risk": str(retry_plan.get("retry_risk") or ""),
        "retry_python_executable": str(python_executable or ""),
        "retry_python_executable_source": str(
            python_executable_source or "current_interpreter"
        ),
        "retry_setup_prerequisite_required": setup_required,
        "retry_setup_prerequisite_status": setup_status,
        "retry_setup_prerequisite_reason": str(setup_result.get("reason") or ""),
        "retry_setup_prerequisite_satisfied": bool(
            not setup_required or setup_status == "pass"
        ),
        "retry_setup_prerequisite_triggered_by": str(
            setup_result.get("triggered_by") or ""
        ),
        "retry_setup_prerequisite_auto_executed": bool(
            setup_result.get("auto_retry_prerequisite", False)
        ),
    }


def _skipped(
    metadata: dict[str, Any],
    *,
    reason: str,
    message: str,
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "executed": False,
        "reason": reason,
        "message": message,
        "command": metadata.get("retry_command", ""),
        "command_args": [],
        "execution_level": metadata.get("retry_level", ""),
        "execution_risk": metadata.get("retry_risk", ""),
        "execution_scope": metadata.get("retry_strategy", ""),
        "cwd": "",
        "python_executable": metadata.get("retry_python_executable", ""),
        "python_executable_source": metadata.get(
            "retry_python_executable_source",
            "current_interpreter",
        ),
        "returncode": None,
        "timeout": False,
        "passed": 0,
        "failed": 0,
        "failure_category": "not_executed",
        "failure_signal": reason,
        "diagnostic_summary": message,
        "stdout_preview": "",
        "stderr_preview": "",
        "next_actions": _skipped_next_actions(reason),
        "retry_execution_status": "skipped",
        "retry_execution_reason": reason,
        **metadata,
    }


def _skipped_next_actions(reason: str) -> list[str]:
    if reason == "execution_disabled":
        return ["Enable repository test retry execution for this run."]
    if reason == "retry_not_recommended":
        return ["Inspect repository_test_retry_plan.md for manual next actions."]
    if reason == "prerequisites_pending":
        return ["Run repository test environment setup before retry execution."]
    if reason == "no_retry_command":
        return ["Inspect repository_test_retry_plan.json for retry planning gaps."]
    return []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
