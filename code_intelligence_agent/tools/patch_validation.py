from __future__ import annotations

import ast
import difflib
import textwrap
import warnings
from dataclasses import asdict, dataclass


SIGNATURE_CHANGE_RULES = frozenset({"mutable_default_arg"})


@dataclass(frozen=True)
class PatchValidation:
    valid: bool
    reasons: list[str]
    ast_valid: bool
    scope_limited: bool
    signature_changed: bool
    signature_change_allowed: bool
    decorator_changed: bool
    changed_lines: int
    line_change_ratio: float
    ast_node_delta: int

    def to_dict(self) -> dict:
        return asdict(self)


def validate_function_patch(
    old_source: str,
    new_source: str,
    *,
    allow_signature_change: bool = False,
    max_changed_lines: int = 80,
    max_line_change_ratio: float = 3.0,
) -> PatchValidation:
    reasons: list[str] = []
    old_parse = _parse_single_function(old_source)
    new_parse = _parse_single_function(new_source)
    ast_valid = old_parse.node is not None and new_parse.node is not None
    if old_parse.error:
        reasons.append(f"old_source_parse_error:{old_parse.error}")
    if new_parse.error:
        reasons.append(f"new_source_parse_error:{new_parse.error}")

    scope_limited = False
    signature_changed = False
    decorator_changed = False
    ast_node_delta = 0
    if old_parse.node is not None and new_parse.node is not None:
        scope_limited = (
            old_parse.top_level_node_count == 1
            and new_parse.top_level_node_count == 1
            and type(old_parse.node) is type(new_parse.node)
            and old_parse.node.name == new_parse.node.name
            and _leading_indent(old_source) == _leading_indent(new_source)
        )
        signature_changed = _signature_dump(old_parse.node) != _signature_dump(
            new_parse.node
        )
        decorator_changed = _decorator_dump(old_parse.node) != _decorator_dump(
            new_parse.node
        )
        ast_node_delta = abs(_node_count(old_parse.node) - _node_count(new_parse.node))

    if not ast_valid:
        reasons.append("invalid_python_ast")
    if ast_valid and not scope_limited:
        reasons.append("scope_not_limited_to_original_function")
    if decorator_changed:
        reasons.append("decorator_changed")
    if signature_changed and not allow_signature_change:
        reasons.append("signature_changed")

    changed_lines = _changed_lines(old_source, new_source)
    old_line_count = max(1, len(old_source.splitlines()))
    line_change_ratio = round(changed_lines / old_line_count, 4)
    if changed_lines > max_changed_lines:
        reasons.append("patch_too_large")
    if line_change_ratio > max_line_change_ratio:
        reasons.append("patch_change_ratio_too_large")

    return PatchValidation(
        valid=not reasons,
        reasons=reasons,
        ast_valid=ast_valid,
        scope_limited=scope_limited,
        signature_changed=signature_changed,
        signature_change_allowed=allow_signature_change,
        decorator_changed=decorator_changed,
        changed_lines=changed_lines,
        line_change_ratio=line_change_ratio,
        ast_node_delta=ast_node_delta,
    )


def validate_module_patch(
    old_source: str,
    new_source: str,
    *,
    allow_signature_change: bool = False,
    max_changed_lines: int = 80,
    max_line_change_ratio: float = 3.0,
) -> PatchValidation:
    reasons: list[str] = []
    old_tree, old_error = _parse_module(old_source)
    new_tree, new_error = _parse_module(new_source)
    ast_valid = old_tree is not None and new_tree is not None
    if old_error:
        reasons.append(f"old_source_parse_error:{old_error}")
    if new_error:
        reasons.append(f"new_source_parse_error:{new_error}")
    if not ast_valid:
        reasons.append("invalid_python_ast")

    signature_changed = False
    decorator_changed = False
    if old_tree is not None and new_tree is not None:
        old_contracts = _module_definition_contracts(old_tree)
        new_contracts = _module_definition_contracts(new_tree)
        if set(old_contracts).difference(new_contracts):
            reasons.append("module_callable_removed")
        shared_names = set(old_contracts).intersection(new_contracts)
        signature_changed = any(
            old_contracts[name][0] != new_contracts[name][0]
            for name in shared_names
        )
        decorator_changed = any(
            old_contracts[name][1] != new_contracts[name][1]
            for name in shared_names
        )
    if signature_changed and not allow_signature_change:
        reasons.append("signature_changed")
    if decorator_changed:
        reasons.append("decorator_changed")

    changed_lines = _changed_lines(old_source, new_source)
    old_line_count = max(1, len(old_source.splitlines()))
    line_change_ratio = round(changed_lines / old_line_count, 4)
    if changed_lines > max_changed_lines:
        reasons.append("patch_too_large")
    if line_change_ratio > max_line_change_ratio:
        reasons.append("patch_change_ratio_too_large")
    ast_node_delta = (
        abs(_node_count(old_tree) - _node_count(new_tree))
        if old_tree is not None and new_tree is not None
        else 0
    )
    return PatchValidation(
        valid=not reasons,
        reasons=reasons,
        ast_valid=ast_valid,
        scope_limited=ast_valid,
        signature_changed=signature_changed,
        signature_change_allowed=allow_signature_change,
        decorator_changed=decorator_changed,
        changed_lines=changed_lines,
        line_change_ratio=line_change_ratio,
        ast_node_delta=ast_node_delta,
    )


def allow_signature_change_for_rules(
    rule_ids: list[str] | set[str] | tuple[str, ...],
) -> bool:
    return bool(set(rule_ids) & SIGNATURE_CHANGE_RULES)


@dataclass(frozen=True)
class _FunctionParse:
    node: ast.FunctionDef | ast.AsyncFunctionDef | None
    top_level_node_count: int
    error: str


def _parse_single_function(source: str) -> _FunctionParse:
    normalized = textwrap.dedent(source).strip("\n")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(normalized)
    except SyntaxError as exc:
        return _FunctionParse(node=None, top_level_node_count=0, error=_syntax_error(exc))
    body = tree.body
    if len(body) != 1:
        return _FunctionParse(
            node=None,
            top_level_node_count=len(body),
            error=f"expected_one_top_level_function_got_{len(body)}",
        )
    node = body[0]
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _FunctionParse(
            node=None,
            top_level_node_count=len(body),
            error=f"expected_function_got_{type(node).__name__}",
        )
    return _FunctionParse(node=node, top_level_node_count=len(body), error="")


def _parse_module(source: str) -> tuple[ast.Module | None, str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            return ast.parse(source), ""
    except SyntaxError as exc:
        return None, _syntax_error(exc)


def _signature_dump(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return ast.dump(
        ast.Tuple(
            elts=[
                node.args,
                node.returns or ast.Constant(value=None),
                ast.Constant(value=node.type_comment),
                ast.List(elts=list(getattr(node, "type_params", [])), ctx=ast.Load()),
            ],
            ctx=ast.Load(),
        ),
        include_attributes=False,
    )


def _decorator_dump(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return repr(
        [ast.dump(decorator, include_attributes=False) for decorator in node.decorator_list]
    )


def _module_definition_contracts(
    tree: ast.Module,
) -> dict[str, tuple[str, str]]:
    contracts: dict[str, tuple[str, str]] = {}

    def collect(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if not isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                continue
            name = f"{prefix}.{node.name}" if prefix else node.name
            signature = (
                _signature_dump(node)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                else _class_signature_dump(node)
            )
            contracts[name] = (signature, _definition_decorator_dump(node))
            if isinstance(node, ast.ClassDef):
                collect(node.body, name)

    collect(tree.body)
    return contracts


def _class_signature_dump(node: ast.ClassDef) -> str:
    return repr(
        {
            "bases": [ast.dump(base, include_attributes=False) for base in node.bases],
            "keywords": [
                (keyword.arg, ast.dump(keyword.value, include_attributes=False))
                for keyword in node.keywords
            ],
            "type_params": [
                ast.dump(value, include_attributes=False)
                for value in getattr(node, "type_params", [])
            ],
        }
    )


def _definition_decorator_dump(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> str:
    return repr(
        [ast.dump(decorator, include_attributes=False) for decorator in node.decorator_list]
    )


def _node_count(node: ast.AST) -> int:
    return sum(1 for _ in ast.walk(node))


def _leading_indent(source: str) -> str:
    for line in source.splitlines():
        if line.strip():
            return line[: len(line) - len(line.lstrip())]
    return ""


def _changed_lines(old_source: str, new_source: str) -> int:
    diff = difflib.unified_diff(
        old_source.splitlines(),
        new_source.splitlines(),
        lineterm="",
    )
    count = 0
    for line in diff:
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            count += 1
    return count


def _syntax_error(exc: SyntaxError) -> str:
    line = exc.lineno or 0
    offset = exc.offset or 0
    return f"{exc.msg}@{line}:{offset}"
