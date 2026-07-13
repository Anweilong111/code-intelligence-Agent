from __future__ import annotations

import ast
import textwrap
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SemanticPatchValidation:
    status: str
    blocked_reasons: list[str]
    warnings: list[str]
    risk_score: float
    old_summary: dict[str, Any]
    new_summary: dict[str, Any]

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_semantic_patch(
    old_source: str,
    new_source: str,
) -> SemanticPatchValidation:
    old_node = _parse_function(old_source)
    new_node = _parse_function(new_source)
    if old_node is None or new_node is None:
        return SemanticPatchValidation(
            status="blocked",
            blocked_reasons=["semantic_ast_unavailable"],
            warnings=[],
            risk_score=1.0,
            old_summary={},
            new_summary={},
        )

    old_summary = _behavior_summary(old_node)
    new_summary = _behavior_summary(new_node)
    blocked: list[str] = []
    warnings: list[str] = []

    old_parameter_reads = set(old_summary["parameter_reads"])
    new_parameter_reads = set(new_summary["parameter_reads"])
    if old_parameter_reads and not old_parameter_reads.intersection(
        new_parameter_reads
    ):
        blocked.append("input_dependency_removed")

    if (
        bool(new_summary["constant_return_only"])
        and not bool(old_summary["constant_return_only"])
        and old_parameter_reads
    ):
        blocked.append("hardcoded_constant_return_added")

    old_swallow = set(old_summary["exception_swallowing_patterns"])
    new_swallow = set(new_summary["exception_swallowing_patterns"])
    if new_swallow.difference(old_swallow):
        blocked.append("exception_swallowing_added")

    old_control = int(old_summary["control_flow_count"])
    new_control = int(new_summary["control_flow_count"])
    if old_control >= 2 and new_control == 0:
        warnings.append("control_flow_collapsed_requires_behavioral_evidence")

    old_contract = int(old_summary["raise_count"]) + int(
        old_summary["assert_count"]
    )
    new_contract = int(new_summary["raise_count"]) + int(
        new_summary["assert_count"]
    )
    if old_contract > 0 and new_contract < old_contract:
        warnings.append("error_contract_weakened")

    removed_reads = old_parameter_reads.difference(new_parameter_reads)
    if removed_reads and new_parameter_reads:
        warnings.append("some_parameter_dependencies_removed")

    blocked = sorted(set(blocked))
    warnings = sorted(set(warnings))
    risk_score = min(1.0, 0.55 * len(blocked) + 0.15 * len(warnings))
    status = "blocked" if blocked else "warning" if warnings else "pass"
    return SemanticPatchValidation(
        status=status,
        blocked_reasons=blocked,
        warnings=warnings,
        risk_score=round(risk_score, 4),
        old_summary=old_summary,
        new_summary=new_summary,
    )


def _parse_function(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(textwrap.dedent(source).strip("\n"))
    except (SyntaxError, ValueError):
        return None
    if len(tree.body) != 1:
        return None
    node = tree.body[0]
    return node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _behavior_summary(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, Any]:
    parameters = _parameter_names(node)
    loaded_names = {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }
    return {
        "parameters": sorted(parameters),
        "parameter_reads": sorted(parameters.intersection(loaded_names)),
        "control_flow_count": sum(
            isinstance(
                child,
                (
                    ast.If,
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.Try,
                    ast.IfExp,
                    ast.comprehension,
                    ast.BoolOp,
                    ast.Match,
                ),
            )
            for child in ast.walk(node)
        ),
        "return_count": sum(isinstance(child, ast.Return) for child in ast.walk(node)),
        "raise_count": sum(isinstance(child, ast.Raise) for child in ast.walk(node)),
        "assert_count": sum(isinstance(child, ast.Assert) for child in ast.walk(node)),
        "call_count": sum(isinstance(child, ast.Call) for child in ast.walk(node)),
        "constant_return_only": _constant_return_only(node),
        "exception_swallowing_patterns": sorted(_exception_swallowing(node)),
    }


def _parameter_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    arguments = node.args
    names = {
        argument.arg
        for argument in [
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        ]
    }
    if arguments.vararg is not None:
        names.add(arguments.vararg.arg)
    if arguments.kwarg is not None:
        names.add(arguments.kwarg.arg)
    names.discard("self")
    names.discard("cls")
    return names


def _constant_return_only(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(
        body[0].value,
        ast.Constant,
    ) and isinstance(body[0].value.value, str):
        body = body[1:]
    return (
        len(body) == 1
        and isinstance(body[0], ast.Return)
        and _is_constant_expression(body[0].value)
    )


def _is_constant_expression(node: ast.AST | None) -> bool:
    if node is None or isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_constant_expression(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            _is_constant_expression(key) and _is_constant_expression(value)
            for key, value in zip(node.keys, node.values)
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _is_constant_expression(node.operand)
    return False


def _exception_swallowing(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    patterns: set[str] = set()
    for handler in (
        child for child in ast.walk(node) if isinstance(child, ast.ExceptHandler)
    ):
        broad = handler.type is None or _exception_name(handler.type) in {
            "BaseException",
            "Exception",
        }
        if not broad:
            continue
        body = [
            item
            for item in handler.body
            if not (
                isinstance(item, ast.Expr)
                and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, str)
            )
        ]
        if not body or all(isinstance(item, ast.Pass) for item in body):
            patterns.add("broad_exception_pass")
        if body and all(
            isinstance(item, ast.Return)
            and _is_constant_expression(item.value)
            for item in body
        ):
            patterns.add("broad_exception_constant_return")
    return patterns


def _exception_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        current: ast.AST | None = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return ""
