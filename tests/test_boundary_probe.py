from __future__ import annotations

from code_intelligence_agent.core.models import PatchCandidate
from code_intelligence_agent.tools.boundary_probe import run_boundary_probe
from code_intelligence_agent.tools.diff_utils import render_unified_diff


def test_boundary_probe_passes_fixed_index_candidate():
    result = run_boundary_probe(
        _candidate(
            rule_id="possible_index_overrun",
            new_source=(
                "def pairwise(values):\n"
                "    return [values[index + 1] - values[index] "
                "for index in range(len(values) - 1)]\n"
            ),
        )
    )

    assert result.status == "pass"
    assert result.case_count == 3
    assert result.reason == "generated_boundary_cases_passed"


def test_boundary_probe_rejects_remaining_index_overrun():
    result = run_boundary_probe(
        _candidate(
            rule_id="possible_index_overrun",
            new_source=(
                "def pairwise(values):\n"
                "    return [values[index + 1] - values[index] "
                "for index in range(len(values))]\n"
            ),
        )
    )

    assert result.status == "fail"
    assert any(item.get("exception_type") == "IndexError" for item in result.results)


def test_boundary_probe_reports_unsupported_semantic_candidate():
    result = run_boundary_probe(
        _candidate(
            rule_id="llm_patch",
            new_source="def pairwise(values):\n    return values\n",
        )
    )

    assert result.status == "not_run"
    assert result.reason == "no_supported_boundary_probe_for_candidate"


def _candidate(*, rule_id: str, new_source: str) -> PatchCandidate:
    old_source = (
        "def pairwise(values):\n"
        "    return [values[index + 1] - values[index] "
        "for index in range(len(values))]\n"
    )
    return PatchCandidate(
        id="candidate",
        target_file="sample.py",
        relative_file_path="sample.py",
        target_function_id="sample.py::pairwise",
        target_function_name="pairwise",
        rule_id=rule_id,
        description="candidate",
        old_source=old_source,
        new_source=new_source,
        diff=render_unified_diff(old_source, new_source, "sample.py"),
        metadata={},
    )
