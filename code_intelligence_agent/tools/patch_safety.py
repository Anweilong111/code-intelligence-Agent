from __future__ import annotations

import ast
import configparser
import re
import sys
import textwrap
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.core.models import PatchCandidate
from code_intelligence_agent.search.candidate_diversity import (
    stable_source_fingerprint,
)
from code_intelligence_agent.tools.diff_utils import render_unified_diff
from code_intelligence_agent.tools.patch_validation import validate_function_patch
from code_intelligence_agent.tools.semantic_patch_validation import (
    validate_semantic_patch,
)


SENSITIVE_FILE_NAMES = frozenset(
    {
        ".env",
        ".pypirc",
        "credentials",
        "credentials.json",
        "secrets.json",
        "settings.json",
    }
)
SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx"})
DANGEROUS_CALLS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "os.system",
        "os.popen",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.removedirs",
        "os.rename",
        "os.replace",
        "pickle.load",
        "pickle.loads",
        "marshal.load",
        "marshal.loads",
        "shutil.rmtree",
        "shutil.move",
        "subprocess.Popen",
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "socket.socket",
        "urllib.request.urlopen",
        "requests.request",
        "requests.get",
        "requests.post",
        "httpx.request",
        "httpx.get",
        "httpx.post",
    }
)


@dataclass(frozen=True)
class PatchSafetyPolicy:
    allow_signature_change: bool = False
    allow_test_file_modification: bool = False
    authorized_files: tuple[str, ...] = ()
    allowed_new_dependencies: tuple[str, ...] = ()
    allowed_new_dangerous_calls: tuple[str, ...] = ()
    max_changed_lines: int = 80
    max_line_change_ratio: float = 3.0


@dataclass(frozen=True)
class PatchSafetyDecision:
    status: str
    reasons: list[str]
    warnings: list[str]
    ast_valid: bool
    scope_limited: bool
    minimal_diff: bool
    signature_change_allowed: bool
    path_authorized: bool
    test_files_unchanged: bool
    sensitive_files_unchanged: bool
    dangerous_api_guard: bool
    dependency_guard: bool
    unified_diff_complete: bool
    duplicate_failed_patch: bool
    diff_fingerprint: str
    fixed_source_fingerprint: str
    new_dependencies: list[str] = field(default_factory=list)
    new_dangerous_calls: list[str] = field(default_factory=list)
    unauthorized_dangerous_calls: list[str] = field(default_factory=list)
    function_validation: dict[str, Any] = field(default_factory=dict)
    semantic_validation: dict[str, Any] = field(default_factory=dict)
    source: str = "unified_patch_safety_gate_v2"

    @property
    def allowed(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def apply_patch_safety_gate(
    candidate: PatchCandidate,
    *,
    repository_root: str | Path | None = None,
    policy: PatchSafetyPolicy | None = None,
    failed_diff_fingerprints: list[str] | set[str] | tuple[str, ...] = (),
    failed_source_fingerprints: list[str] | set[str] | tuple[str, ...] = (),
    source: str = "unified_patch_safety_gate_v2",
) -> PatchCandidate:
    decision = evaluate_patch_safety(
        candidate,
        repository_root=repository_root,
        policy=policy,
        failed_diff_fingerprints=failed_diff_fingerprints,
        failed_source_fingerprints=failed_source_fingerprints,
        source=source,
    )
    return replace(
        candidate,
        metadata={
            **candidate.metadata,
            "validation": decision.function_validation,
            "semantic_validation": decision.semantic_validation,
            "safety_gate": decision.to_dict(),
        },
    )


def evaluate_patch_safety(
    candidate: PatchCandidate,
    *,
    repository_root: str | Path | None = None,
    policy: PatchSafetyPolicy | None = None,
    failed_diff_fingerprints: list[str] | set[str] | tuple[str, ...] = (),
    failed_source_fingerprints: list[str] | set[str] | tuple[str, ...] = (),
    source: str = "unified_patch_safety_gate_v2",
) -> PatchSafetyDecision:
    active_policy = policy or PatchSafetyPolicy()
    function_validation = validate_function_patch(
        candidate.old_source,
        candidate.new_source,
        allow_signature_change=active_policy.allow_signature_change,
        max_changed_lines=active_policy.max_changed_lines,
        max_line_change_ratio=active_policy.max_line_change_ratio,
    )
    semantic_validation = validate_semantic_patch(
        candidate.old_source,
        candidate.new_source,
    )
    relative_path = _normalized_relative_path(candidate.relative_file_path)
    path_authorized = _path_authorized(
        relative_path,
        candidate.target_file,
        repository_root=repository_root,
        authorized_files=active_policy.authorized_files,
    )
    test_files_unchanged = active_policy.allow_test_file_modification or not _is_test_path(
        relative_path
    )
    sensitive_files_unchanged = not _is_sensitive_path(relative_path)
    new_dangerous_calls = sorted(
        _dangerous_calls(candidate.new_source).difference(
            _dangerous_calls(candidate.old_source)
        )
    )
    allowed_dangerous_calls = {
        str(item).strip()
        for item in active_policy.allowed_new_dangerous_calls
        if str(item).strip()
    }
    unauthorized_dangerous_calls = [
        call for call in new_dangerous_calls if call not in allowed_dangerous_calls
    ]
    dangerous_api_guard = not unauthorized_dangerous_calls
    new_dependencies = sorted(
        _third_party_imports(candidate.new_source).difference(
            _third_party_imports(candidate.old_source)
        )
    )
    allowed_dependencies = {
        item.split(".", 1)[0]
        for item in active_policy.allowed_new_dependencies
        if str(item).strip()
    }
    if repository_root is not None:
        allowed_dependencies.update(
            _repository_authorized_imports(
                Path(repository_root),
                relative_path=relative_path,
            )
        )
    unauthorized_dependencies = [
        dependency
        for dependency in new_dependencies
        if dependency not in allowed_dependencies
    ]
    dependency_guard = not unauthorized_dependencies
    expected_diff = render_unified_diff(
        candidate.old_source,
        candidate.new_source,
        relative_path,
    )
    unified_diff_complete = bool(
        candidate.diff.strip()
        and "@@" in candidate.diff
        and _normalize_text(candidate.diff) == _normalize_text(expected_diff)
    )
    diff_fingerprint = stable_source_fingerprint(candidate.diff)
    fixed_source_fingerprint = stable_source_fingerprint(candidate.new_source)
    duplicate_failed_patch = (
        diff_fingerprint in {str(item) for item in failed_diff_fingerprints}
        or fixed_source_fingerprint
        in {str(item) for item in failed_source_fingerprints}
    )
    minimal_diff = function_validation.changed_lines > 0 and not {
        "patch_too_large",
        "patch_change_ratio_too_large",
    }.intersection(function_validation.reasons)

    reasons = list(function_validation.reasons)
    if not path_authorized:
        reasons.append("target_path_not_authorized")
    if not test_files_unchanged:
        reasons.append("test_file_modification_forbidden")
    if not sensitive_files_unchanged:
        reasons.append("sensitive_file_modification_forbidden")
    if unauthorized_dangerous_calls:
        reasons.extend(
            f"dangerous_api_added:{item}" for item in unauthorized_dangerous_calls
        )
    if unauthorized_dependencies:
        reasons.extend(
            f"unauthorized_dependency_added:{item}"
            for item in unauthorized_dependencies
        )
    if not unified_diff_complete:
        reasons.append("unified_diff_mismatch")
    if duplicate_failed_patch:
        reasons.append("duplicate_failed_patch")
    reasons.extend(semantic_validation.blocked_reasons)
    if not minimal_diff and not function_validation.reasons:
        reasons.append("empty_patch")
    reasons = sorted(set(reasons))
    warnings = sorted(set(semantic_validation.warnings))
    return PatchSafetyDecision(
        status="blocked" if reasons else "pass",
        reasons=reasons,
        warnings=warnings,
        ast_valid=function_validation.ast_valid,
        scope_limited=function_validation.scope_limited,
        minimal_diff=minimal_diff,
        signature_change_allowed=function_validation.signature_change_allowed,
        path_authorized=path_authorized,
        test_files_unchanged=test_files_unchanged,
        sensitive_files_unchanged=sensitive_files_unchanged,
        dangerous_api_guard=dangerous_api_guard,
        dependency_guard=dependency_guard,
        unified_diff_complete=unified_diff_complete,
        duplicate_failed_patch=duplicate_failed_patch,
        diff_fingerprint=diff_fingerprint,
        fixed_source_fingerprint=fixed_source_fingerprint,
        new_dependencies=new_dependencies,
        new_dangerous_calls=new_dangerous_calls,
        unauthorized_dangerous_calls=unauthorized_dangerous_calls,
        function_validation=function_validation.to_dict(),
        semantic_validation=semantic_validation.to_dict(),
        source=source,
    )


def _normalized_relative_path(value: str) -> str:
    return PurePosixPath(str(value or "").replace("\\", "/")).as_posix()


def _path_authorized(
    relative_path: str,
    target_file: str,
    *,
    repository_root: str | Path | None,
    authorized_files: tuple[str, ...],
) -> bool:
    pure = PurePosixPath(relative_path)
    if not relative_path or pure.is_absolute() or ".." in pure.parts:
        return False
    if authorized_files:
        normalized = {
            _normalized_relative_path(item)
            for item in authorized_files
            if str(item).strip()
        }
        if relative_path not in normalized:
            return False
    if repository_root is None:
        return True
    root = Path(repository_root).resolve()
    expected = (root / Path(*pure.parts)).resolve()
    try:
        expected.relative_to(root)
    except ValueError:
        return False
    target = Path(target_file)
    target = target.resolve() if target.is_absolute() else (root / target).resolve()
    return target == expected


def _is_test_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    lowered_parts = {part.lower() for part in path.parts[:-1]}
    name = path.name.lower()
    return bool(
        {"test", "tests"}.intersection(lowered_parts)
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _is_sensitive_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    name = path.name.lower()
    return name in SENSITIVE_FILE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES


def _dangerous_calls(source: str) -> set[str]:
    node = _parse(source)
    if node is None:
        return set()
    calls: set[str] = set()
    for call in (item for item in ast.walk(node) if isinstance(item, ast.Call)):
        name = _call_name(call.func)
        if name in DANGEROUS_CALLS:
            calls.add(name)
        if name.startswith("subprocess.") and any(
            keyword.arg == "shell"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in call.keywords
        ):
            calls.add("subprocess.shell_true")
        if name == "yaml.load" and not any(
            keyword.arg in {"Loader", "loader"} for keyword in call.keywords
        ):
            calls.add("yaml.load_without_loader")
    return calls


def _third_party_imports(source: str) -> set[str]:
    node = _parse(source)
    if node is None:
        return set()
    modules: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Import):
            modules.update(alias.name.split(".", 1)[0] for alias in item.names)
        elif isinstance(item, ast.ImportFrom) and item.level == 0 and item.module:
            modules.add(item.module.split(".", 1)[0])
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    return {module for module in modules if module not in stdlib}


def _repository_authorized_imports(
    repository_root: Path,
    *,
    relative_path: str,
) -> set[str]:
    root = repository_root.resolve()
    return {
        *_repository_local_imports(root, relative_path=relative_path),
        *_declared_dependency_imports(root),
    }


def _repository_local_imports(root: Path, *, relative_path: str) -> set[str]:
    source_roots = {root, root / "src"}
    target = root / Path(*PurePosixPath(relative_path).parts)
    for parent in target.parents:
        try:
            parent.relative_to(root)
        except ValueError:
            continue
        source_roots.add(parent)
    modules: set[str] = set()
    for source_root in source_roots:
        if not source_root.is_dir():
            continue
        try:
            children = list(source_root.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_file() and child.suffix == ".py" and child.stem != "__init__":
                modules.add(child.stem)
            elif child.is_dir() and (child / "__init__.py").is_file():
                modules.add(child.name)
    return modules


def _declared_dependency_imports(root: Path) -> set[str]:
    dependencies = set()
    dependencies.update(_pyproject_dependency_names(root / "pyproject.toml"))
    dependencies.update(_setup_cfg_dependency_names(root / "setup.cfg"))
    for path in root.glob("requirements*.txt"):
        dependencies.update(_requirements_dependency_names(path))
    return {_distribution_to_import_name(item) for item in dependencies if item}


def _pyproject_dependency_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        import tomllib

        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return set()
    project = payload.get("project", {})
    dependencies = {
        _requirement_name(item)
        for item in project.get("dependencies", [])
        if isinstance(item, str)
    }
    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        dependencies.update(
            _requirement_name(item)
            for values in optional.values()
            if isinstance(values, list)
            for item in values
            if isinstance(item, str)
        )
    poetry = payload.get("tool", {}).get("poetry", {}).get("dependencies", {})
    if isinstance(poetry, dict):
        dependencies.update(str(item) for item in poetry if str(item).lower() != "python")
    return {item for item in dependencies if item}


def _setup_cfg_dependency_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return set()
    raw = parser.get("options", "install_requires", fallback="")
    return {
        name
        for line in raw.splitlines()
        if (name := _requirement_name(line))
    }


def _requirements_dependency_names(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return set()
    return {
        name
        for line in lines
        if not line.lstrip().startswith(("#", "-"))
        if (name := _requirement_name(line))
    }


def _requirement_name(value: str) -> str:
    text = str(value or "").strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)", text)
    return match.group(1) if match else ""


def _distribution_to_import_name(value: str) -> str:
    return re.sub(r"[-.]+", "_", value).lower()


def _parse(source: str) -> ast.AST | None:
    try:
        return ast.parse(textwrap.dedent(source).strip("\n"))
    except (SyntaxError, ValueError):
        return None


def _call_name(node: ast.AST) -> str:
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


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").strip()
