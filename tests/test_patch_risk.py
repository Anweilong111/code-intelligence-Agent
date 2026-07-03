from pathlib import Path
import tempfile

from code_intelligence_agent.agents.patch_generator import PatchGenerator
from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.core.models import PatchCandidate
from code_intelligence_agent.search.patch_risk import PatchRiskAnalyzer, annotate_patch_risk
from code_intelligence_agent.tools.diff_utils import render_unified_diff


def test_patch_risk_analyzer_counts_callers_and_diff_size():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n\n"
            "def caller(values):\n"
            "    return shift_left(values)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)
        call_graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, call_graph)
        detector = RuleBasedBugDetector()
        findings = detector.detect(parsed.functions)
        ranked = FaultLocalizer().rank(program_graph, findings)
        candidate = PatchGenerator().generate(repo, parsed.functions, ranked)[0]

        risk = PatchRiskAnalyzer().analyze(candidate, program_graph)
        annotated = annotate_patch_risk(candidate, risk)

        assert risk.diff_size > 0
        assert risk.affected_callers == 1
        assert risk.score > 0
        assert annotated.metadata["risk"]["affected_callers"] == 1


def test_patch_risk_analyzer_reports_data_dependency_fanout():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        source = (
            "def compute(values):\n"
            "    count = len(values)\n"
            "    scale = count + 1\n"
            "    adjusted = scale * 2\n"
            "    return adjusted\n"
        )
        fixed = (
            "def compute(values):\n"
            "    count = max(1, len(values))\n"
            "    scale = count + 1\n"
            "    adjusted = scale * 2\n"
            "    return adjusted\n"
        )
        source_path = repo / "sample.py"
        source_path.write_text(source, encoding="utf-8")
        parsed = RepoParser().parse(repo)
        call_graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, call_graph)
        function = parsed.functions[0]
        candidate = _patch_candidate(repo, function, fixed)

        risk = PatchRiskAnalyzer().analyze(candidate, program_graph)

        assert {"count", "values"}.issubset(set(risk.changed_variables))
        assert risk.data_dependency_fanout >= 2
        assert f"data_dependency_fanout={risk.data_dependency_fanout}" in risk.risk_reasons
        assert risk.score > 0.0


def test_patch_risk_analyzer_reports_subscript_key_flow_fanout():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        source = (
            "def score_for(scores, name):\n"
            "    return scores[name]\n"
        )
        fixed = (
            "def score_for(scores, name):\n"
            "    return scores.get(name, 0)\n"
        )
        source_path = repo / "sample.py"
        source_path.write_text(source, encoding="utf-8")
        parsed = RepoParser().parse(repo)
        call_graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, call_graph)
        function = parsed.functions[0]
        candidate = _patch_candidate(repo, function, fixed)

        risk = PatchRiskAnalyzer().analyze(candidate, program_graph)

        assert {"scores", "name"}.issubset(set(risk.changed_variables))
        assert risk.data_dependency_fanout >= 1
        assert f"data_dependency_fanout={risk.data_dependency_fanout}" in risk.risk_reasons
        assert risk.score > 0.0


def test_patch_risk_analyzer_flags_return_or_control_changes():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        source = (
            "def classify(value):\n"
            "    if value > 0:\n"
            "        return 1\n"
            "    return 0\n"
        )
        fixed = (
            "def classify(value):\n"
            "    if value >= 0:\n"
            "        return 1\n"
            "    return 0\n"
        )
        source_path = repo / "sample.py"
        source_path.write_text(source, encoding="utf-8")
        parsed = RepoParser().parse(repo)
        function = parsed.functions[0]
        candidate = _patch_candidate(repo, function, fixed)

        risk = PatchRiskAnalyzer().analyze(candidate)

        assert risk.return_or_control_changed is True
        assert "return_or_control_changed" in risk.risk_reasons
        assert "value" in risk.changed_variables


def _patch_candidate(repo: Path, function, fixed_source: str) -> PatchCandidate:
    relative = Path(function.file_path).relative_to(repo).as_posix()
    return PatchCandidate(
        id=f"{function.id}::manual",
        target_file=function.file_path,
        relative_file_path=relative,
        target_function_id=function.id,
        target_function_name=function.metadata.get("qualified_name", function.name),
        rule_id="manual",
        description="Manual patch for risk analysis.",
        old_source=function.source,
        new_source=fixed_source,
        diff=render_unified_diff(function.source, fixed_source, relative),
        metadata={},
    )
