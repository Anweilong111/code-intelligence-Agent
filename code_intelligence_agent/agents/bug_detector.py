from __future__ import annotations

import ast
import textwrap
from collections import defaultdict
from pathlib import Path

from code_intelligence_agent.core.call_graph import CallGraph
from code_intelligence_agent.core.models import BugFinding, CodeEntity, SuspiciousFunction


class RuleBasedBugDetector:
    """AST rule detector for phase-1 bug signals."""

    def detect(self, functions: list[CodeEntity]) -> list[BugFinding]:
        findings: list[BugFinding] = []
        for function in functions:
            findings.extend(_FunctionRuleVisitor(function).run())
        return findings

    def rank(
        self,
        functions: list[CodeEntity],
        findings: list[BugFinding],
        call_graph: CallGraph | None = None,
    ) -> list[SuspiciousFunction]:
        by_function: dict[str, list[BugFinding]] = defaultdict(list)
        for finding in findings:
            by_function[finding.function_id].append(finding)

        max_degree = 1
        if call_graph is not None and call_graph.nodes:
            max_degree = max(call_graph.degree(function.id) for function in functions) or 1

        ranked: list[SuspiciousFunction] = []
        for function in functions:
            function_findings = by_function.get(function.id, [])
            static_score = _combine_confidence(
                finding.confidence for finding in function_findings
            )
            graph_score = 0.0
            if call_graph is not None:
                graph_score = call_graph.degree(function.id) / max_degree
            final_score = min(1.0, 0.80 * static_score + 0.20 * graph_score)
            ranked.append(
                SuspiciousFunction(
                    function_id=function.id,
                    function_name=function.metadata.get("qualified_name", function.name),
                    file_path=function.file_path,
                    start_line=function.start_line,
                    end_line=function.end_line,
                    static_rule_score=round(static_score, 4),
                    graph_score=round(graph_score, 4),
                    final_score=round(final_score, 4),
                    findings=function_findings,
                )
            )
        return sorted(ranked, key=lambda item: item.final_score, reverse=True)


class _FunctionRuleVisitor(ast.NodeVisitor):
    def __init__(self, function: CodeEntity) -> None:
        self.function = function
        self.findings: list[BugFinding] = []
        self._tree = ast.parse(textwrap.dedent(function.source))

    def run(self) -> list[BugFinding]:
        self._check_mutable_defaults()
        self._check_stringified_numeric_values()
        self._check_len_denominator_guards()
        self._check_inverted_empty_guards()
        self._check_iterator_double_consumption()
        self._check_dict_missing_key_guards()
        root = self._tree.body[0] if self._tree.body else None
        if isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for statement in root.body:
                self.visit(statement)
        return self.findings

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_Compare(self, node: ast.Compare) -> None:
        if _is_len_non_negative_check(node):
            self._add(
                rule_id="always_true_len_check",
                bug_type="condition error",
                message="len(x) >= 0 is always true for valid sequences.",
                node=node,
                confidence=0.80,
            )
        identity_literal = _identity_literal_comparison(node)
        if identity_literal is not None:
            self._add(
                rule_id="identity_comparison_literal",
                bug_type="comparison semantics error",
                message=(
                    "Literal values are compared with identity instead of "
                    "equality semantics."
                ),
                node=node,
                confidence=0.79,
                evidence=identity_literal,
            )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        catches_broad_exception = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}
        )
        body_is_only_pass = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
        if catches_broad_exception and body_is_only_pass:
            self._add(
                rule_id="broad_exception_pass",
                bug_type="exception handling error",
                message="Broad exception handler silently suppresses errors.",
                node=node,
                confidence=0.75,
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if _is_inplace_api_return_assignment(node):
            call = node.value
            assert isinstance(call, ast.Call)
            assert isinstance(call.func, ast.Attribute)
            self._add(
                rule_id="inplace_api_return_value",
                bug_type="api misuse",
                message="Result of in-place mutating API is assigned as if it returned a value.",
                node=node,
                confidence=0.78,
                evidence={
                    "method": call.func.attr,
                    "receiver": _expr_source(call.func.value),
                },
            )
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        enumerate_counter = _enumerate_start_zero_counter(node)
        if enumerate_counter and _loop_body_yields(node.body):
            self._add(
                rule_id="enumerate_start_zero_counter",
                bug_type="off-by-one counting error",
                message=(
                    "enumerate(..., start=0) is used as an item counter in a "
                    "yielding loop; one-item iterators can be counted as zero."
                ),
                node=node,
                confidence=0.72,
                evidence={"counter": enumerate_counter},
            )
        range_target = _range_len_target(node.iter)
        if range_target and isinstance(node.target, ast.Name):
            risky_lines = _find_positive_offset_index_reads(
                body=node.body,
                array_name=range_target,
                index_name=node.target.id,
            )
            for line in risky_lines:
                self._add(
                    rule_id="possible_index_overrun",
                    bug_type="boundary error",
                    message="Loop iterates range(len(x)) while reading x[i + k].",
                    node=node,
                    confidence=0.85,
                    evidence={"index_line": self.function.start_line + line - 1},
                )
        self.generic_visit(node)

    def _check_mutable_defaults(self) -> None:
        root = self._tree.body[0] if self._tree.body else None
        if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        mutable_defaults = [
            default
            for default in root.args.defaults
            if isinstance(default, (ast.List, ast.Dict, ast.Set))
            or _is_empty_mutable_constructor(default)
        ]
        for default in mutable_defaults:
            self._add(
                rule_id="mutable_default_arg",
                bug_type="state leakage",
                message="Mutable default argument can leak state between calls.",
                node=default,
                confidence=0.65,
            )

    def _check_stringified_numeric_values(self) -> None:
        root = self._tree.body[0] if self._tree.body else None
        if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        candidates: dict[str, ast.Assign] = {}
        for node in ast.walk(root):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            if _is_str_wrapped_numeric_expression(node.value):
                candidates[node.targets[0].id] = node
        if not candidates:
            return
        numeric_uses = _NumericUseVisitor(set(candidates))
        numeric_uses.visit(root)
        for name in sorted(numeric_uses.names):
            assignment = candidates[name]
            self._add(
                rule_id="stringified_numeric_value",
                bug_type="type error",
                message="Numeric value is converted to str and later used in numeric context.",
                node=assignment,
                confidence=0.76,
                evidence={"variable": name},
            )

    def _check_len_denominator_guards(self) -> None:
        root = self._tree.body[0] if self._tree.body else None
        if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        len_assignments: dict[str, tuple[ast.Assign, str | None]] = {}
        for node in ast.walk(root):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            if _is_len_call(node.value):
                len_assignments[node.targets[0].id] = (
                    node,
                    _len_call_source_name(node.value),
                )
        if not len_assignments:
            return

        denominator_uses = _LenDenominatorUseVisitor(set(len_assignments))
        denominator_uses.visit(root)
        for name, use_line in sorted(denominator_uses.names.items()):
            assignment, len_source = len_assignments[name]
            if use_line <= getattr(assignment, "lineno", 0):
                continue
            if _has_zero_guard_before(root, name, use_line):
                continue
            if len_source and _has_zero_guard_before(root, len_source, use_line):
                continue
            self._add(
                rule_id="missing_len_zero_guard",
                bug_type="zero division error",
                message="len-derived denominator is used without an empty-input guard.",
                node=assignment,
                confidence=0.74,
                evidence={
                    "variable": name,
                    "len_source": len_source,
                    "denominator_line": use_line,
                },
            )

    def _check_inverted_empty_guards(self) -> None:
        root = self._tree.body[0] if self._tree.body else None
        if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        for statement in root.body:
            if not isinstance(statement, ast.If):
                continue
            guard_name = _positive_guard_name(statement.test)
            if not guard_name:
                continue
            exception_name = _raised_exception_name(statement)
            if exception_name not in {"ValueError", "StatisticsError"}:
                continue
            self._add(
                rule_id="inverted_empty_guard",
                bug_type="condition error",
                message=(
                    "Guard raises on non-empty input; this looks like an "
                    "inverted empty-input check."
                ),
                node=statement,
                confidence=0.77,
                evidence={
                    "guard_name": guard_name,
                    "exception": exception_name,
                },
            )

    def _check_iterator_double_consumption(self) -> None:
        root = self._tree.body[0] if self._tree.body else None
        if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        parameter_names = {argument.arg for argument in root.args.args}
        materialized: set[str] = set()
        consumed: dict[str, tuple[ast.Call, str]] = {}
        for statement in root.body:
            materialized_name = _materialized_parameter_assignment(
                statement,
                parameter_names,
            )
            if materialized_name:
                materialized.add(materialized_name)
                consumed.pop(materialized_name, None)
            active_parameters = parameter_names - materialized
            for name, len_call in _len_list_parameter_calls(statement, active_parameters):
                prior = consumed.get(name)
                if prior is None:
                    continue
                consumer_call, consumer_name = prior
                self._add(
                    rule_id="iterator_double_consumption",
                    bug_type="iterator state error",
                    message=(
                        "Iterator-like parameter is consumed before "
                        "len(list(...)) recomputes its length."
                    ),
                    node=len_call,
                    confidence=0.73,
                    evidence={
                        "iterable": name,
                        "consumer": consumer_name,
                        "consumer_line": (
                            self.function.start_line
                            + getattr(consumer_call, "lineno", 1)
                            - 1
                        ),
                        "length_line": (
                            self.function.start_line
                            + getattr(len_call, "lineno", 1)
                            - 1
                        ),
                    },
                )
                return
            for name, call, consumer_name in _iterator_consuming_calls(
                statement,
                active_parameters,
            ):
                consumed.setdefault(name, (call, consumer_name))

    def _check_dict_missing_key_guards(self) -> None:
        root = self._tree.body[0] if self._tree.body else None
        if not isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        parameter_names = {argument.arg for argument in root.args.args}
        for statement in root.body:
            for node in ast.walk(statement):
                access = _mapping_subscript_access(node)
                if access is None:
                    continue
                mapping_name, key_expr = access
                if key_expr not in parameter_names:
                    continue
                if _has_mapping_key_guard(root, node, mapping_name, key_expr):
                    continue
                self._add(
                    rule_id="dict_missing_key_guard",
                    bug_type="key error",
                    message="Mapping subscript access has no key guard or default.",
                    node=node,
                    confidence=0.72,
                    evidence={
                        "mapping": mapping_name,
                        "key": key_expr,
                    },
                )
                return

    def _add(
        self,
        rule_id: str,
        bug_type: str,
        message: str,
        node: ast.AST,
        confidence: float,
        evidence: dict | None = None,
    ) -> None:
        local_line = getattr(node, "lineno", 1)
        absolute_line = self.function.start_line + local_line - 1
        self.findings.append(
            BugFinding(
                rule_id=rule_id,
                bug_type=bug_type,
                message=message,
                file_path=Path(self.function.file_path).as_posix(),
                function_id=self.function.id,
                function_name=self.function.metadata.get(
                    "qualified_name", self.function.name
                ),
                line=absolute_line,
                confidence=confidence,
                evidence=evidence or {},
            )
        )


def _is_len_non_negative_check(node: ast.Compare) -> bool:
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    left = node.left
    op = node.ops[0]
    right = node.comparators[0]
    if _is_len_call(left) and isinstance(op, (ast.GtE, ast.Gt)) and _number_value(right) == 0:
        return True
    if _number_value(left) == 0 and isinstance(op, (ast.LtE, ast.Lt)) and _is_len_call(right):
        return True
    return False


def _identity_literal_comparison(node: ast.Compare) -> dict | None:
    values = [node.left, *node.comparators]
    for index, op in enumerate(node.ops):
        if not isinstance(op, (ast.Is, ast.IsNot)):
            continue
        left = values[index]
        right = values[index + 1]
        literal_node = left if _is_identity_literal(left) else right
        compared_node = right if literal_node is left else left
        if not _is_identity_literal(literal_node):
            continue
        return {
            "operator": "is not" if isinstance(op, ast.IsNot) else "is",
            "literal": _literal_repr(literal_node),
            "compared_expression": _expr_source(compared_node),
        }
    return None


def _is_identity_literal(node: ast.AST) -> bool:
    if not isinstance(node, ast.Constant):
        return False
    value = node.value
    if isinstance(value, bool) or value is None or value is Ellipsis:
        return False
    return isinstance(value, (str, bytes, int, float, complex))


def _literal_repr(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return _expr_source(node)


def _materialized_parameter_assignment(
    node: ast.stmt,
    parameter_names: set[str],
) -> str:
    if not isinstance(node, ast.Assign):
        return ""
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return ""
    target = node.targets[0]
    if target.id not in parameter_names:
        return ""
    if _is_list_call_of_name(node.value, target.id):
        return target.id
    return ""


def _len_list_parameter_calls(
    node: ast.AST,
    parameter_names: set[str],
) -> list[tuple[str, ast.Call]]:
    calls: list[tuple[str, ast.Call]] = []
    for child in ast.walk(node):
        if not (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "len"
            and len(child.args) == 1
        ):
            continue
        argument = child.args[0]
        for name in parameter_names:
            if _is_list_call_of_name(argument, name):
                calls.append((name, child))
                break
    return calls


def _iterator_consuming_calls(
    node: ast.AST,
    parameter_names: set[str],
) -> list[tuple[str, ast.Call, str]]:
    consumers: list[tuple[str, ast.Call, str]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if not isinstance(child.func, ast.Name):
            continue
        if child.func.id not in {"sum", "min", "max", "sorted", "tuple", "list"}:
            continue
        if len(child.args) != 1 or not isinstance(child.args[0], ast.Name):
            continue
        name = child.args[0].id
        if name in parameter_names:
            consumers.append((name, child, child.func.id))
    return consumers


def _is_list_call_of_name(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "list"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == name
    )


def _mapping_subscript_access(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.Subscript) or not isinstance(node.ctx, ast.Load):
        return None
    if not isinstance(node.value, ast.Name):
        return None
    mapping_name = node.value.id
    if not _looks_mapping_name(mapping_name):
        return None
    key_expr = _expr_source(node.slice)
    if not key_expr:
        return None
    return mapping_name, key_expr


def _has_mapping_key_guard(
    root: ast.FunctionDef | ast.AsyncFunctionDef,
    subscript: ast.Subscript,
    mapping_name: str,
    key_expr: str,
) -> bool:
    subscript_line = getattr(subscript, "lineno", 0)
    for node in ast.walk(root):
        if not isinstance(node, ast.If):
            continue
        if getattr(node, "lineno", 0) > subscript_line:
            continue
        guard = _mapping_key_guard(node.test, mapping_name, key_expr)
        if guard == "present" and _line_in_statements(subscript_line, node.body):
            return True
        if (
            guard == "missing"
            and getattr(node, "lineno", 0) < subscript_line
            and not _line_in_statements(subscript_line, node.body)
            and _body_exits(node.body)
        ):
            return True
    return False


def _mapping_key_guard(
    node: ast.AST,
    mapping_name: str,
    key_expr: str,
) -> str:
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            guard = _mapping_key_guard(value, mapping_name, key_expr)
            if guard:
                return guard
        return ""
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return ""
    left = _expr_source(node.left)
    right = _expr_source(node.comparators[0])
    if right != mapping_name or left != key_expr:
        return ""
    if isinstance(node.ops[0], ast.In):
        return "present"
    if isinstance(node.ops[0], ast.NotIn):
        return "missing"
    return ""


def _line_in_statements(line: int, statements: list[ast.stmt]) -> bool:
    for statement in statements:
        start = getattr(statement, "lineno", 0)
        end = getattr(statement, "end_lineno", start)
        if start <= line <= end:
            return True
    return False


def _body_exits(statements: list[ast.stmt]) -> bool:
    return any(isinstance(statement, (ast.Return, ast.Raise)) for statement in statements)


def _looks_mapping_name(name: str) -> bool:
    lowered = name.lower()
    return any(
        token in lowered
        for token in (
            "dict",
            "map",
            "mapping",
            "lookup",
            "table",
            "score",
            "weight",
            "count",
            "cache",
        )
    )


def _is_len_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
        and len(node.args) == 1
    )


def _len_call_source_name(node: ast.AST) -> str | None:
    if not _is_len_call(node):
        return None
    assert isinstance(node, ast.Call)
    source = node.args[0]
    if isinstance(source, ast.Name):
        return source.id
    return None


_INPLACE_RETURN_NONE_METHODS = {
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "remove",
    "reverse",
    "sort",
    "update",
}


def _is_inplace_api_return_assignment(node: ast.Assign) -> bool:
    if len(node.targets) != 1:
        return False
    if not isinstance(node.targets[0], ast.Name):
        return False
    call = node.value
    if (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Attribute)
        and _attribute_root_name(call.func.value) in {"self", "cls"}
    ):
        return False
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in _INPLACE_RETURN_NONE_METHODS
        and isinstance(call.func.value, (ast.Name, ast.Attribute))
    )


def _is_str_wrapped_numeric_expression(node: ast.AST) -> bool:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "str"
        and len(node.args) == 1
        and not node.keywords
    ):
        return False
    return _looks_numeric_expression(node.args[0])


def _looks_numeric_expression(node: ast.AST) -> bool:
    if _is_len_call(node):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return True
    if isinstance(node, ast.BinOp) and isinstance(
        node.op,
        (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod),
    ):
        return True
    return False


class _NumericUseVisitor(ast.NodeVisitor):
    def __init__(self, candidate_names: set[str]) -> None:
        self.candidate_names = candidate_names
        self.names: set[str] = set()

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod),
        ):
            self._record_name(node.left)
            self._record_name(node.right)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        values = [node.left, *node.comparators]
        if any(_is_numeric_constant(value) for value in values):
            for value in values:
                self._record_name(value)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if not _is_mapping_like_base(node.value):
            self._record_name(node.slice)
        self.generic_visit(node)

    def _record_name(self, node: ast.AST) -> None:
        if isinstance(node, ast.Name) and node.id in self.candidate_names:
            self.names.add(node.id)


def _is_numeric_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float))


class _LenDenominatorUseVisitor(ast.NodeVisitor):
    def __init__(self, candidate_names: set[str]) -> None:
        self.candidate_names = candidate_names
        self.names: dict[str, int] = {}

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            if isinstance(node.right, ast.Name) and node.right.id in self.candidate_names:
                self.names.setdefault(node.right.id, getattr(node.right, "lineno", 0))
        self.generic_visit(node)


def _has_zero_guard_before(
    root: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    line: int,
) -> bool:
    for node in ast.walk(root):
        if not isinstance(node, ast.If):
            continue
        if getattr(node, "lineno", 0) >= line:
            continue
        if _is_zero_guard(node.test, name):
            return True
    return False


def _is_zero_guard(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _is_name(node.operand, name) or _is_len_of_name(node.operand, name)
    if isinstance(node, ast.BoolOp):
        return any(_is_zero_guard(value, name) for value in node.values)
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    left = node.left
    right = node.comparators[0]
    op = node.ops[0]
    if _is_name(left, name):
        threshold = _number_value(right)
        if threshold is None:
            return False
        if isinstance(op, (ast.Eq, ast.LtE)) and threshold >= 0:
            return True
        if isinstance(op, ast.Lt) and threshold > 0:
            return True
    if _is_len_of_name(left, name):
        threshold = _number_value(right)
        if threshold is None:
            return False
        if isinstance(op, (ast.Eq, ast.LtE)) and threshold >= 0:
            return True
        if isinstance(op, ast.Lt) and threshold > 0:
            return True
    if _is_name(right, name):
        threshold = _number_value(left)
        if threshold is None:
            return False
        if isinstance(op, (ast.Eq, ast.GtE)) and threshold >= 0:
            return True
        if isinstance(op, ast.Gt) and threshold > 0:
            return True
    if _is_len_of_name(right, name):
        threshold = _number_value(left)
        if threshold is None:
            return False
        if isinstance(op, (ast.Eq, ast.GtE)) and threshold >= 0:
            return True
        if isinstance(op, ast.Gt) and threshold > 0:
            return True
    return False


def _positive_guard_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    left = node.left
    right = node.comparators[0]
    op = node.ops[0]
    if _is_len_call(left) and isinstance(op, (ast.Gt, ast.GtE)) and _number_value(right) == 0:
        assert isinstance(left, ast.Call)
        source = left.args[0]
        return source.id if isinstance(source, ast.Name) else None
    if _number_value(left) == 0 and isinstance(op, (ast.Lt, ast.LtE)) and _is_len_call(right):
        assert isinstance(right, ast.Call)
        source = right.args[0]
        return source.id if isinstance(source, ast.Name) else None
    if isinstance(op, ast.NotEq):
        if _number_value(right) == 0:
            return _name_or_len_name(left)
        if _number_value(left) == 0:
            return _name_or_len_name(right)
    return None


def _raised_exception_name(node: ast.If) -> str:
    for statement in node.body:
        if not isinstance(statement, ast.Raise) or statement.exc is None:
            continue
        exc = statement.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        if isinstance(exc, ast.Name):
            return exc.id
    return ""


def _expr_source(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _number_value(node: ast.AST) -> int | float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    return None


def _attribute_root_name(node: ast.AST) -> str:
    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


def _is_mapping_like_base(node: ast.AST) -> bool:
    if isinstance(node, ast.Dict):
        return True
    if isinstance(node, ast.Name):
        lowered = node.id.lower()
        return any(token in lowered for token in ("dict", "map", "mapping", "lookup"))
    return False


def _is_empty_mutable_constructor(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"dict", "list", "set"}
        and not node.args
        and not node.keywords
    )


def _is_len_of_name(node: ast.AST, name: str) -> bool:
    return (
        _is_len_call(node)
        and isinstance(node, ast.Call)
        and _is_name(node.args[0], name)
    )


def _name_or_len_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if _is_len_call(node):
        assert isinstance(node, ast.Call)
        if isinstance(node.args[0], ast.Name):
            return node.args[0].id
    return None


def _range_len_target(node: ast.AST) -> str | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "range"
        and len(node.args) == 1
    ):
        return None
    len_call = node.args[0]
    if not _is_len_call(len_call):
        return None
    target = len_call.args[0]
    if isinstance(target, ast.Name):
        return target.id
    return None


def _enumerate_start_zero_counter(node: ast.For) -> str | None:
    if not isinstance(node.target, ast.Tuple) or not node.target.elts:
        return None
    counter = node.target.elts[0]
    if not isinstance(counter, ast.Name):
        return None
    call = node.iter
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "enumerate"
    ):
        return None
    for keyword in call.keywords:
        if keyword.arg == "start" and _number_value(keyword.value) == 0:
            return counter.id
    if len(call.args) >= 2 and _number_value(call.args[1]) == 0:
        return counter.id
    return None


def _loop_body_yields(body: list[ast.stmt]) -> bool:
    return any(
        isinstance(node, (ast.Yield, ast.YieldFrom))
        for stmt in body
        for node in ast.walk(stmt)
    )


def _find_positive_offset_index_reads(
    body: list[ast.stmt], array_name: str, index_name: str
) -> list[int]:
    finder = _PositiveOffsetIndexVisitor(array_name=array_name, index_name=index_name)
    for stmt in body:
        finder.visit(stmt)
    return finder.lines


class _PositiveOffsetIndexVisitor(ast.NodeVisitor):
    def __init__(self, array_name: str, index_name: str) -> None:
        self.array_name = array_name
        self.index_name = index_name
        self.lines: list[int] = []

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_name(node.value, self.array_name) and _is_positive_offset_index(
            node.slice, self.index_name
        ):
            self.lines.append(node.lineno)
        self.generic_visit(node)


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _is_positive_offset_index(node: ast.AST, index_name: str) -> bool:
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Add)
        and _is_name(node.left, index_name)
        and isinstance(node.right, ast.Constant)
        and isinstance(node.right.value, int)
        and node.right.value > 0
    )


def _combine_confidence(confidences) -> float:
    score = 0.0
    for confidence in confidences:
        score = 1 - (1 - score) * (1 - confidence)
    return score


def detect_bugs(functions: list[CodeEntity]) -> list[BugFinding]:
    return RuleBasedBugDetector().detect(functions)
