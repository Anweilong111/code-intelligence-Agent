from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.core.models import PatchCandidate


_BOOTSTRAP = Path(__file__).with_name("boundary_probe_bootstrap.py")


@dataclass(frozen=True)
class BoundaryProbeResult:
    status: str
    reason: str
    rule_id: str = ""
    case_count: int = 0
    forbidden_exceptions: tuple[str, ...] = ()
    results: tuple[dict[str, Any], ...] = ()
    timeout: bool = False
    returncode: int = 0
    command: tuple[str, ...] = ()
    stdout_preview: str = ""
    stderr_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_boundary_probe(
    candidate: PatchCandidate,
    *,
    timeout_seconds: float = 2.0,
    python_executable: str | Path | None = None,
) -> BoundaryProbeResult:
    rule_id = _supported_rule_id(candidate)
    if not rule_id:
        return BoundaryProbeResult(
            status="not_run",
            reason="no_supported_boundary_probe_for_candidate",
        )
    function = _parse_function(candidate.new_source)
    if function is None or function.decorator_list:
        return BoundaryProbeResult(
            status="not_run",
            reason="candidate_signature_not_probeable",
            rule_id=rule_id,
        )
    probe = _probe_payload(rule_id, function)
    if not probe:
        return BoundaryProbeResult(
            status="not_run",
            reason="candidate_signature_not_probeable",
            rule_id=rule_id,
        )
    payload = {
        "source": textwrap.dedent(candidate.new_source).strip("\n"),
        "function_name": function.name,
        **probe,
    }
    with tempfile.TemporaryDirectory(prefix="cia_boundary_probe_") as tmp_dir:
        payload_path = Path(tmp_dir) / "payload.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        command = [
            str(python_executable or sys.executable),
            str(_BOOTSTRAP),
            str(payload_path),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=tmp_dir,
                capture_output=True,
                text=True,
                timeout=max(0.1, timeout_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return BoundaryProbeResult(
                status="fail",
                reason="boundary_probe_timeout",
                rule_id=rule_id,
                case_count=len(probe["cases"]),
                forbidden_exceptions=tuple(probe["forbidden_exceptions"]),
                timeout=True,
                returncode=-1,
                command=tuple(command),
                stdout_preview=str(exc.stdout or "")[-500:],
                stderr_preview=str(exc.stderr or "")[-500:],
            )
        except OSError as exc:
            return BoundaryProbeResult(
                status="blocker",
                reason="boundary_probe_process_start_failed",
                rule_id=rule_id,
                case_count=len(probe["cases"]),
                forbidden_exceptions=tuple(probe["forbidden_exceptions"]),
                timeout=False,
                returncode=-1,
                command=tuple(command),
                stderr_preview=type(exc).__name__,
            )
        output = _last_json_object(completed.stdout)
        status = str(output.get("status") or "error")
        if status == "unsupported":
            status = "not_run"
        reason = {
            "pass": "generated_boundary_cases_passed",
            "fail": "forbidden_boundary_exception_observed",
            "not_run": str(output.get("reason") or "boundary_probe_unsupported"),
        }.get(status, "boundary_probe_execution_error")
        return BoundaryProbeResult(
            status=status,
            reason=reason,
            rule_id=rule_id,
            case_count=int(output.get("case_count", len(probe["cases"]))),
            forbidden_exceptions=tuple(probe["forbidden_exceptions"]),
            results=tuple(
                item for item in output.get("results", []) if isinstance(item, dict)
            ),
            timeout=False,
            returncode=completed.returncode,
            command=tuple(command),
            stdout_preview=completed.stdout[-1000:],
            stderr_preview=completed.stderr[-1000:],
        )


def _supported_rule_id(candidate: PatchCandidate) -> str:
    supported = {
        "possible_index_overrun",
        "missing_len_zero_guard",
        "dict_missing_key_guard",
        "inverted_empty_guard",
    }
    values = [candidate.rule_id]
    static_rule_ids = candidate.metadata.get("static_rule_ids")
    if isinstance(static_rule_ids, list):
        values.extend(str(item) for item in static_rule_ids)
    return next((value for value in values if value in supported), "")


def _parse_function(
    source: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(textwrap.dedent(source).strip("\n"))
    except (SyntaxError, ValueError):
        return None
    if len(tree.body) != 1:
        return None
    node = tree.body[0]
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    if isinstance(node, ast.AsyncFunctionDef):
        return None
    return node


def _probe_payload(
    rule_id: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, Any]:
    positional = [*function.args.posonlyargs, *function.args.args]
    if len(positional) != 1 or function.args.vararg or function.args.kwarg:
        return {}
    if function.args.kwonlyargs:
        return {}
    if rule_id == "possible_index_overrun":
        return {
            "cases": [
                {"args": [[]], "kwargs": {}},
                {"args": [[1]], "kwargs": {}},
                {"args": [[1, 2]], "kwargs": {}},
            ],
            "forbidden_exceptions": ["IndexError"],
        }
    if rule_id == "missing_len_zero_guard":
        return {
            "cases": [{"args": [[]], "kwargs": {}}],
            "forbidden_exceptions": ["ZeroDivisionError"],
        }
    if rule_id == "dict_missing_key_guard":
        return {
            "cases": [{"args": [{}], "kwargs": {}}],
            "forbidden_exceptions": ["KeyError"],
        }
    if rule_id == "inverted_empty_guard":
        return {
            "cases": [{"args": [[]], "kwargs": {}}],
            "forbidden_exceptions": ["IndexError", "ZeroDivisionError"],
        }
    return {}


def _last_json_object(value: str) -> dict[str, Any]:
    for line in reversed(str(value or "").splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}
