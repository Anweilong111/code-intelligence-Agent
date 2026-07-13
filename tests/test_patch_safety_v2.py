from __future__ import annotations

from dataclasses import replace

from code_intelligence_agent.core.models import PatchCandidate
from code_intelligence_agent.search.candidate_diversity import (
    stable_source_fingerprint,
)
from code_intelligence_agent.tools.diff_utils import render_unified_diff
from code_intelligence_agent.tools.patch_safety import (
    PatchSafetyPolicy,
    evaluate_patch_safety,
)
from code_intelligence_agent.tools.semantic_patch_validation import (
    validate_semantic_patch,
)


def test_patch_safety_accepts_scoped_auditable_function_patch(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text("def f(value):\n    return value + 1\n", encoding="utf-8")
    candidate = _candidate(
        path,
        old_source="def f(value):\n    return value + 1\n",
        new_source="def f(value):\n    return value + 2\n",
    )

    decision = evaluate_patch_safety(
        candidate,
        repository_root=tmp_path,
        policy=PatchSafetyPolicy(authorized_files=("sample.py",)),
    )

    assert decision.status == "pass"
    assert decision.reasons == []
    assert decision.unified_diff_complete is True
    assert decision.path_authorized is True
    assert decision.semantic_validation["status"] == "pass"


def test_patch_safety_blocks_path_escape_test_file_and_diff_mismatch(tmp_path):
    candidate = _candidate(
        tmp_path / "tests" / "test_sample.py",
        relative_file_path="../tests/test_sample.py",
        old_source="def test_f():\n    assert False\n",
        new_source="def test_f():\n    return None\n",
    )
    candidate = replace(candidate, diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n+y\n")

    decision = evaluate_patch_safety(candidate, repository_root=tmp_path)

    assert decision.status == "blocked"
    assert "target_path_not_authorized" in decision.reasons
    assert "test_file_modification_forbidden" in decision.reasons
    assert "unified_diff_mismatch" in decision.reasons


def test_patch_safety_blocks_new_dangerous_api_and_dependency(tmp_path):
    path = tmp_path / "sample.py"
    old_source = "def f(value):\n    return value\n"
    new_source = (
        "def f(value):\n"
        "    import external_magic\n"
        "    return eval(value)\n"
    )
    candidate = _candidate(path, old_source=old_source, new_source=new_source)

    decision = evaluate_patch_safety(candidate, repository_root=tmp_path)

    assert decision.status == "blocked"
    assert "dangerous_api_added:eval" in decision.reasons
    assert "unauthorized_dependency_added:external_magic" in decision.reasons
    assert decision.dangerous_api_guard is False
    assert decision.dependency_guard is False


def test_patch_safety_blocks_process_file_and_network_side_effects(tmp_path):
    path = tmp_path / "sample.py"
    old_source = "def f(value):\n    return value\n"
    new_source = (
        "def f(value):\n"
        "    open(\"result.txt\", \"w\").write(value)\n"
        "    subprocess.run([\"tool\", value])\n"
        "    return urllib.request.urlopen(value).read()\n"
    )

    decision = evaluate_patch_safety(
        _candidate(path, old_source=old_source, new_source=new_source),
        repository_root=tmp_path,
    )

    assert decision.status == "blocked"
    assert decision.unauthorized_dangerous_calls == [
        "open",
        "subprocess.run",
        "urllib.request.urlopen",
    ]
    assert "dangerous_api_added:open" in decision.reasons
    assert "dangerous_api_added:subprocess.run" in decision.reasons
    assert "dangerous_api_added:urllib.request.urlopen" in decision.reasons


def test_patch_safety_allows_explicitly_authorized_dangerous_call(tmp_path):
    path = tmp_path / "sample.py"
    old_source = "def f(value):\n    return value\n"
    new_source = "def f(value):\n    return open(value).read()\n"

    decision = evaluate_patch_safety(
        _candidate(path, old_source=old_source, new_source=new_source),
        repository_root=tmp_path,
        policy=PatchSafetyPolicy(allowed_new_dangerous_calls=("open",)),
    )

    assert decision.status == "pass"
    assert decision.new_dangerous_calls == ["open"]
    assert decision.unauthorized_dangerous_calls == []
    assert decision.dangerous_api_guard is True


def test_patch_safety_allows_repository_local_module_import(tmp_path):
    package = tmp_path / "src" / "local_tools"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    path = package / "worker.py"
    old_source = "def f(value):\n    return value\n"
    new_source = (
        "def f(value):\n"
        "    import local_tools\n"
        "    return local_tools.normalize(value)\n"
    )

    decision = evaluate_patch_safety(
        _candidate(
            path,
            relative_file_path="src/local_tools/worker.py",
            old_source=old_source,
            new_source=new_source,
        ),
        repository_root=tmp_path,
    )

    assert decision.status == "pass"
    assert decision.new_dependencies == ["local_tools"]
    assert decision.dependency_guard is True


def test_patch_safety_allows_dependency_declared_by_repository(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["httpx>=0.27"]\n',
        encoding="utf-8",
    )
    path = tmp_path / "sample.py"
    old_source = "def f(value):\n    return value\n"
    new_source = (
        "def f(value):\n"
        "    import httpx\n"
        "    return httpx.URL(value).host\n"
    )

    decision = evaluate_patch_safety(
        _candidate(path, old_source=old_source, new_source=new_source),
        repository_root=tmp_path,
    )

    assert decision.status == "pass"
    assert decision.new_dependencies == ["httpx"]
    assert decision.dependency_guard is True


def test_patch_safety_blocks_previous_failed_patch_fingerprints(tmp_path):
    path = tmp_path / "sample.py"
    candidate = _candidate(
        path,
        old_source="def f(value):\n    return value\n",
        new_source="def f(value):\n    return value + 1\n",
    )

    decision = evaluate_patch_safety(
        candidate,
        repository_root=tmp_path,
        failed_diff_fingerprints=[stable_source_fingerprint(candidate.diff)],
    )

    assert decision.status == "blocked"
    assert decision.duplicate_failed_patch is True
    assert "duplicate_failed_patch" in decision.reasons


def test_semantic_validation_blocks_input_independent_hardcoded_return():
    result = validate_semantic_patch(
        "def classify(value):\n    return value > 0\n",
        "def classify(value):\n    return True\n",
    )

    assert result.status == "blocked"
    assert "input_dependency_removed" in result.blocked_reasons
    assert "hardcoded_constant_return_added" in result.blocked_reasons


def test_semantic_validation_warns_but_does_not_block_equivalent_collapse():
    result = validate_semantic_patch(
        (
            "def shift(values):\n"
            "    output = []\n"
            "    for index in range(1, len(values)):\n"
            "        if values[index]:\n"
            "            output.append(values[index])\n"
            "    return output\n"
        ),
        "def shift(values):\n    return [value for value in values[1:] if value]\n",
    )

    assert result.status in {"pass", "warning"}
    assert result.blocked_reasons == []


def _candidate(
    target_file,
    *,
    old_source: str,
    new_source: str,
    relative_file_path: str = "sample.py",
) -> PatchCandidate:
    return PatchCandidate(
        id="candidate",
        target_file=str(target_file),
        relative_file_path=relative_file_path,
        target_function_id="sample.py::f",
        target_function_name="f",
        rule_id="llm_patch",
        description="candidate",
        old_source=old_source,
        new_source=new_source,
        diff=render_unified_diff(old_source, new_source, relative_file_path),
        metadata={"generator": "llm"},
    )
