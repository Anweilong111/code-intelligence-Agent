from __future__ import annotations

import hashlib
import re
from pathlib import Path

from code_intelligence_agent.core.models import (
    BugFinding,
    CodeEntity,
    FaultLocalizationResult,
    PatchCandidate,
)
from code_intelligence_agent.search.confidence_calibration import (
    calibrate_patch_confidence,
)
from code_intelligence_agent.tools.diff_utils import render_unified_diff


class PatchGenerator:
    def generate(
        self,
        repo_path: str | Path,
        functions: list[CodeEntity],
        ranked: list[FaultLocalizationResult],
        limit: int = 5,
    ) -> list[PatchCandidate]:
        function_map = {function.id: function for function in functions}
        candidates: list[PatchCandidate] = []
        seen: set[tuple[str, str]] = set()
        for result in ranked:
            function = function_map.get(result.function_id)
            if function is None:
                continue
            for finding in result.findings:
                key = _finding_key(function, finding)
                if key in seen:
                    continue
                seen.add(key)
                for candidate in self._generate_for_rule(repo_path, function, finding):
                    candidates.append(candidate)
                    if len(candidates) >= limit:
                        return candidates
        return candidates

    def refine(
        self,
        repo_path: str | Path,
        previous_patch: PatchCandidate,
        execution_result,
        round_index: int,
    ) -> PatchCandidate | None:
        refined = self.refine_many(
            repo_path=repo_path,
            previous_patch=previous_patch,
            execution_result=execution_result,
            round_index=round_index,
            limit=1,
        )
        return refined[0] if refined else None

    def refine_many(
        self,
        repo_path: str | Path,
        previous_patch: PatchCandidate,
        execution_result,
        round_index: int,
        limit: int = 1,
    ) -> list[PatchCandidate]:
        del repo_path
        if limit <= 0 or execution_result.success:
            return []
        rewrite = _reflection_rewrite(previous_patch)
        if rewrite is None:
            return []
        variant, new_source = rewrite
        if (
            not new_source
            or new_source == previous_patch.old_source
            or new_source == previous_patch.new_source
        ):
            return []

        diff = render_unified_diff(
            previous_patch.old_source,
            new_source,
            previous_patch.relative_file_path,
        )
        metadata = {
            **previous_patch.metadata,
            "generator": "rule_based_reflection",
            "variant": variant,
            "variant_rank": 0,
            "reflection_round_index": round_index,
            "reflection_parent_variant": previous_patch.metadata.get("variant", ""),
            "reflection_parent_generator": previous_patch.metadata.get("generator", ""),
            "reflection_failure_stdout": execution_result.stdout[-500:],
            "reflection_failure_stderr": execution_result.stderr[-500:],
            "reflection_failure_traceback": execution_result.traceback[-500:],
            "search_profile_role": "reflection_refined_candidate",
        }
        confidence = max(float(metadata.get("confidence", 0.0)), 0.86)
        metadata["confidence"] = confidence
        metadata["rule_confidence"] = max(
            float(metadata.get("rule_confidence", 0.0)),
            confidence,
        )
        return [
            PatchCandidate(
                id=f"{previous_patch.id}::reflection_{round_index}_{variant}",
                target_file=previous_patch.target_file,
                relative_file_path=previous_patch.relative_file_path,
                target_function_id=previous_patch.target_function_id,
                target_function_name=previous_patch.target_function_name,
                rule_id=previous_patch.rule_id,
                description=(
                    f"Refine failed {previous_patch.rule_id} patch using "
                    "execution feedback."
                ),
                old_source=previous_patch.old_source,
                new_source=new_source,
                diff=diff,
                metadata=metadata,
            )
        ][:limit]

    def _generate_for_rule(
        self, repo_path: str | Path, function: CodeEntity, finding: BugFinding
    ) -> list[PatchCandidate]:
        rule_id = finding.rule_id
        old_source = function.source
        rewrites = _rewrite_sources(old_source, rule_id, finding.evidence)
        candidates = []
        relative = _relative_file(repo_path, function.file_path)
        for index, rewrite in enumerate(rewrites):
            new_source = rewrite["source"]
            if not new_source or new_source == old_source:
                continue
            diff = render_unified_diff(old_source, new_source, relative)
            variant = rewrite["variant"]
            evidence_fingerprint = _evidence_fingerprint(finding.evidence)
            calibration = calibrate_patch_confidence(
                finding,
                variant_rank=index,
                diff=diff,
            )
            candidates.append(
                PatchCandidate(
                    id=_candidate_id(
                        function=function,
                        rule_id=rule_id,
                        variant=variant,
                        evidence_fingerprint=evidence_fingerprint,
                    ),
                    target_file=function.file_path,
                    relative_file_path=relative,
                    target_function_id=function.id,
                    target_function_name=function.metadata.get(
                        "qualified_name", function.name
                    ),
                    rule_id=rule_id,
                    description=_description(rule_id),
                    old_source=old_source,
                    new_source=new_source,
                    diff=diff,
                    metadata={
                        "generator": "rule_based",
                        "variant": variant,
                        "variant_rank": index,
                        "confidence": calibration.score,
                        "rule_confidence": calibration.score,
                        "raw_rule_confidence": finding.confidence,
                        "confidence_calibration": calibration.to_dict(),
                        "finding_evidence": finding.evidence,
                        "finding_evidence_fingerprint": evidence_fingerprint,
                    },
                )
            )
        return candidates


def _reflection_rewrite(candidate: PatchCandidate) -> tuple[str, str] | None:
    if candidate.metadata.get("generator") == "rule_based_reflection":
        return None
    variant = str(candidate.metadata.get("variant", ""))
    if candidate.rule_id == "possible_index_overrun" and variant in {
        "overly_conservative_range_bound",
        "diversity_duplicate_decoy",
    }:
        return (
            "reflection_shrink_range_upper_bound",
            _fix_index_overrun(candidate.old_source),
        )
    if (
        candidate.rule_id == "missing_len_zero_guard"
        and variant == "return_default_on_empty"
    ):
        return (
            "reflection_insert_len_zero_guard",
            _fix_missing_len_zero_guard(
                candidate.old_source,
                _dict(candidate.metadata.get("finding_evidence")),
            ),
        )
    if (
        candidate.rule_id == "missing_len_zero_guard"
        and variant == "insert_len_zero_guard"
    ):
        return (
            "reflection_return_default_on_empty",
            _fix_missing_len_zero_guard_return_default(
                candidate.old_source,
                _dict(candidate.metadata.get("finding_evidence")),
            ),
        )
    return None


def _rewrite_sources(
    source: str,
    rule_id: str,
    evidence: dict[str, object] | None = None,
) -> list[dict[str, str]]:
    evidence = _dict(evidence)
    if rule_id == "always_true_len_check":
        return [{"variant": "non_empty_check", "source": _fix_len_check(source)}]
    if rule_id == "possible_index_overrun":
        return [
            {"variant": "shrink_range_upper_bound", "source": _fix_index_overrun(source)},
            {
                "variant": "overly_conservative_range_bound",
                "source": _fix_index_overrun_conservative(source),
            },
        ]
    if rule_id == "broad_exception_pass":
        return [{"variant": "re_raise_exception", "source": _fix_broad_exception_pass(source)}]
    if rule_id == "mutable_default_arg":
        return [{"variant": "none_guard", "source": _fix_mutable_default_arg(source)}]
    if rule_id == "inplace_api_return_value":
        return [{"variant": "split_inplace_call_assignment", "source": _fix_inplace_api_assignment(source)}]
    if rule_id == "stringified_numeric_value":
        return [{"variant": "remove_str_numeric_wrapper", "source": _fix_stringified_numeric_value(source)}]
    if rule_id == "missing_len_zero_guard":
        return [
            {
                "variant": "insert_len_zero_guard",
                "source": _fix_missing_len_zero_guard(source, evidence),
            },
            {
                "variant": "return_default_on_empty",
                "source": _fix_missing_len_zero_guard_return_default(
                    source,
                    evidence,
                ),
            },
        ]
    if rule_id == "enumerate_start_zero_counter":
        return [{"variant": "one_based_enumerate_counter", "source": _fix_enumerate_start_zero_counter(source)}]
    if rule_id == "inverted_empty_guard":
        return [{"variant": "restore_empty_guard", "source": _fix_inverted_empty_guard(source)}]
    if rule_id == "identity_comparison_literal":
        return [{"variant": "equality_literal_comparison", "source": _fix_identity_comparison_literal(source)}]
    if rule_id == "iterator_double_consumption":
        return [{"variant": "materialize_iterator_before_consumption", "source": _fix_iterator_double_consumption(source)}]
    if rule_id == "dict_missing_key_guard":
        return [{"variant": "mapping_get_default", "source": _fix_dict_missing_key_guard(source)}]
    return []


def _fix_len_check(source: str) -> str:
    source = re.sub(r"len\(([^)]+)\)\s*>=\s*0", r"bool(\1)", source, count=1)
    source = re.sub(r"0\s*<=\s*len\(([^)]+)\)", r"bool(\1)", source, count=1)
    return source


def _fix_index_overrun(source: str) -> str:
    return re.sub(
        r"range\(\s*len\(([^)]+)\)\s*\)",
        r"range(len(\1) - 1)",
        source,
        count=1,
    )


def _fix_index_overrun_conservative(source: str) -> str:
    return re.sub(
        r"range\(\s*len\(([^)]+)\)\s*\)",
        r"range(max(0, len(\1) - 2))",
        source,
        count=1,
    )


def _fix_broad_exception_pass(source: str) -> str:
    lines = source.splitlines()
    in_except = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("except ") or stripped == "except:":
            in_except = True
            continue
        if in_except and stripped == "pass":
            indent = line[: len(line) - len(line.lstrip())]
            lines[index] = f"{indent}raise"
            return "\n".join(lines)
        if in_except and stripped and not line.startswith((" ", "\t")):
            break
    return source


def _fix_mutable_default_arg(source: str) -> str:
    lines = source.splitlines()
    if not lines:
        return source
    first_line = lines[0]
    match = re.search(
        r"(\w+)\s*=\s*(\[\]|\{\}|set\(\)|list\(\)|dict\(\))",
        first_line,
    )
    if not match:
        return source
    arg_name = match.group(1)
    initializer = match.group(2)
    lines[0] = (
        first_line[: match.start(2)]
        + "None"
        + first_line[match.end(2) :]
    )
    body_indent = _body_indent(lines)
    guard = [
        f"{body_indent}if {arg_name} is None:",
        f"{body_indent}    {arg_name} = {initializer}",
    ]
    return "\n".join([lines[0], *guard, *lines[1:]])


def _fix_inplace_api_assignment(source: str) -> str:
    lines = source.splitlines()
    for index, line in enumerate(lines):
        fixed = _split_inplace_assignment_line(line)
        if fixed is not None:
            lines[index : index + 1] = fixed
            return "\n".join(lines)
    return source


def _fix_stringified_numeric_value(source: str) -> str:
    return re.sub(
        r"^(\s*[A-Za-z_]\w*\s*=\s*)str\((.+)\)\s*$",
        r"\1\2",
        source,
        count=1,
        flags=re.MULTILINE,
    )


def _fix_missing_len_zero_guard(
    source: str,
    evidence: dict[str, object] | None = None,
) -> str:
    evidence = _dict(evidence)
    lines = source.splitlines()
    exception_name = "StatisticsError" if "StatisticsError" in source else "ValueError"
    return _insert_len_zero_guard(
        lines,
        evidence=evidence,
        body_line=f"raise {exception_name}('empty input would divide by zero')",
    )


def _fix_missing_len_zero_guard_return_default(
    source: str,
    evidence: dict[str, object] | None = None,
) -> str:
    evidence = _dict(evidence)
    lines = source.splitlines()
    return _insert_len_zero_guard(
        lines,
        evidence=evidence,
        body_line="return 0",
    )


def _insert_len_zero_guard(
    lines: list[str],
    *,
    evidence: dict[str, object],
    body_line: str,
) -> str:
    target_variable = str(evidence.get("variable") or "").strip()
    target_len_source = str(evidence.get("len_source") or "").strip()
    matches = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)([A-Za-z_]\w*)\s*=\s*len\((.+)\)\s*$", line)
        if not match:
            continue
        indent, variable, len_source = match.groups()
        score = 0
        if target_variable and variable == target_variable:
            score += 2
        if target_len_source and len_source.strip() == target_len_source:
            score += 2
        matches.append((score, index, indent, variable))
    if not matches:
        return "\n".join(lines)
    matches.sort(key=lambda item: (-item[0], item[1]))
    _, index, indent, variable = matches[0]
    guard_indent = f"{indent}    "
    guard = [
        f"{indent}if not {variable}:",
        f"{guard_indent}{body_line}",
    ]
    lines[index + 1 : index + 1] = guard
    return "\n".join(lines)


def _fix_enumerate_start_zero_counter(source: str) -> str:
    source = re.sub(
        r"enumerate\(([^)]*?),\s*start\s*=\s*0\)",
        r"enumerate(\1, start=1)",
        source,
        count=1,
    )
    source = re.sub(
        r"enumerate\(([^)]*?),\s*0\)",
        r"enumerate(\1, 1)",
        source,
        count=1,
    )
    return source


def _fix_inverted_empty_guard(source: str) -> str:
    lines = source.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)if\s+([A-Za-z_]\w*)\s*:\s*$", line)
        if match:
            indent, name = match.groups()
            lines[index] = f"{indent}if not {name}:"
            return "\n".join(lines)
        fixed = re.sub(
            r"^(\s*)if\s+len\(([^)]+)\)\s*>\s*0\s*:\s*$",
            r"\1if len(\2) == 0:",
            line,
            count=1,
        )
        if fixed != line:
            lines[index] = fixed
            return "\n".join(lines)
        fixed = re.sub(
            r"^(\s*)if\s+0\s*<\s*len\(([^)]+)\)\s*:\s*$",
            r"\1if len(\2) == 0:",
            line,
            count=1,
        )
        if fixed != line:
            lines[index] = fixed
            return "\n".join(lines)
        fixed = re.sub(
            r"^(\s*)if\s+([A-Za-z_]\w*)\s*!=\s*0\s*:\s*$",
            r"\1if \2 == 0:",
            line,
            count=1,
        )
        if fixed != line:
            lines[index] = fixed
            return "\n".join(lines)
        fixed = re.sub(
            r"^(\s*)if\s+len\(([^)]+)\)\s*!=\s*0\s*:\s*$",
            r"\1if len(\2) == 0:",
            line,
            count=1,
        )
        if fixed != line:
            lines[index] = fixed
            return "\n".join(lines)
    return source


def _fix_identity_comparison_literal(source: str) -> str:
    literal = (
        r"(?:[rubfRUBF]*)"
        r"(?:'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\""
        r"|-?\d+(?:\.\d+)?)"
    )
    source = re.sub(
        rf"\bis\s+not\s+({literal})",
        r"!= \1",
        source,
        count=1,
    )
    source = re.sub(
        rf"\bis\s+({literal})",
        r"== \1",
        source,
        count=1,
    )
    source = re.sub(
        rf"({literal})\s+is\s+not\b",
        r"\1 !=",
        source,
        count=1,
    )
    source = re.sub(
        rf"({literal})\s+is\b",
        r"\1 ==",
        source,
        count=1,
    )
    return source


def _fix_iterator_double_consumption(source: str) -> str:
    lines = source.splitlines()
    iterable_name = ""
    first_consumer_index = -1
    for index, line in enumerate(lines):
        match = re.search(
            r"\b(?:sum|min|max|sorted|tuple|list)\(\s*([A-Za-z_]\w*)\s*\)",
            line,
        )
        if match and "len(" not in line[: match.start()]:
            iterable_name = match.group(1)
            first_consumer_index = index
            break
    if not iterable_name or first_consumer_index < 0:
        return source

    materialized_pattern = re.compile(
        rf"^\s*{re.escape(iterable_name)}\s*=\s*list\(\s*{re.escape(iterable_name)}\s*\)\s*$"
    )
    if not any(materialized_pattern.match(line) for line in lines[:first_consumer_index]):
        indent = lines[first_consumer_index][
            : len(lines[first_consumer_index]) - len(lines[first_consumer_index].lstrip())
        ]
        lines.insert(first_consumer_index, f"{indent}{iterable_name} = list({iterable_name})")

    source = "\n".join(lines)
    return re.sub(
        rf"len\(\s*list\(\s*{re.escape(iterable_name)}\s*\)\s*\)",
        f"len({iterable_name})",
        source,
        count=1,
    )


def _fix_dict_missing_key_guard(source: str) -> str:
    mapping_name = (
        r"[A-Za-z_]\w*(?:dict|map|mapping|lookup|table|score|weight|count|cache)"
        r"[A-Za-z_]\w*"
        r"|(?:dict|map|mapping|lookup|table|score|weight|count|cache)[A-Za-z_]\w*"
    )
    return re.sub(
        rf"\b({mapping_name})\s*\[\s*([^\]\n]+?)\s*\]",
        r"\1.get(\2, 0)",
        source,
        count=1,
        flags=re.IGNORECASE,
    )


def _split_inplace_assignment_line(line: str) -> list[str] | None:
    match = re.match(
        r"^(\s*)([A-Za-z_]\w*)\s*=\s*"
        r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"
        r"\.(add|append|clear|discard|extend|insert|remove|reverse|sort|update)"
        r"\((.*)\)\s*$",
        line,
    )
    if not match:
        return None
    indent, target, receiver, method, args = match.groups()
    return [
        f"{indent}{receiver}.{method}({args})",
        f"{indent}{target} = {receiver}",
    ]


def _body_indent(lines: list[str]) -> str:
    for line in lines[1:]:
        if line.strip():
            return line[: len(line) - len(line.lstrip())]
    return "    "


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _finding_key(function: CodeEntity, finding: BugFinding) -> tuple[str, str, int, str]:
    evidence_key = "|".join(
        f"{key}={value}" for key, value in sorted(finding.evidence.items())
    )
    return (function.id, finding.rule_id, finding.line, evidence_key)


def _candidate_id(
    *,
    function: CodeEntity,
    rule_id: str,
    variant: str,
    evidence_fingerprint: str,
) -> str:
    base = f"{function.id}::{rule_id}::{variant}"
    if not evidence_fingerprint:
        return base
    return f"{base}::{evidence_fingerprint}"


def _evidence_fingerprint(evidence: dict[str, object]) -> str:
    if not evidence:
        return ""
    evidence_key = "|".join(
        f"{key}={value}" for key, value in sorted(evidence.items())
    )
    digest = hashlib.sha1(evidence_key.encode("utf-8")).hexdigest()[:12]
    label_source = str(
        evidence.get("variable")
        or evidence.get("len_source")
        or evidence.get("function")
        or "evidence"
    )
    label = re.sub(r"[^A-Za-z0-9_]+", "_", label_source).strip("_").lower()
    return f"ev_{(label or 'evidence')[:24]}_{digest}"


def _relative_file(repo_path: str | Path, file_path: str) -> str:
    repo = Path(repo_path).resolve()
    if repo.is_file():
        repo = repo.parent
    target = Path(file_path).resolve()
    try:
        return target.relative_to(repo).as_posix()
    except ValueError:
        return Path(file_path).as_posix()


def _description(rule_id: str) -> str:
    descriptions = {
        "always_true_len_check": "Tighten non-negative len check into non-empty check.",
        "possible_index_overrun": "Reduce range upper bound to avoid i + k overflow.",
        "broad_exception_pass": "Avoid silently swallowing broad exceptions.",
        "mutable_default_arg": "Replace mutable default with None guard.",
        "inplace_api_return_value": "Split in-place API call from result assignment.",
        "stringified_numeric_value": "Remove str() wrapper from numeric value.",
        "missing_len_zero_guard": "Insert guard before len-derived division.",
        "enumerate_start_zero_counter": "Use one-based enumerate counter.",
        "inverted_empty_guard": "Restore inverted non-empty guard to an empty-input guard.",
        "identity_comparison_literal": "Replace literal identity comparison with equality semantics.",
        "iterator_double_consumption": "Materialize iterator before repeated consumption.",
        "dict_missing_key_guard": "Use mapping.get default instead of unguarded key access.",
    }
    return descriptions.get(rule_id, "Rule-based patch.")
