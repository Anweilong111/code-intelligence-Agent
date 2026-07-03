from pathlib import Path
import tempfile

from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.search.beam_patch_search import BeamPatchSearch
from code_intelligence_agent.search.refinement_context import annotate_refinement_context


def test_refinement_context_collects_cross_file_graph_neighbors():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo, target, caller, program_graph = _cross_file_repo(Path(tmp_dir))
        candidate = _candidate_for(target, repo)

        annotated = annotate_refinement_context(candidate, program_graph)

        context = annotated.metadata["refinement_context"]
        assert context["available"] is True
        assert context["target"]["function_id"] == target.id
        assert context["callers"][0]["function_id"] == caller.id
        assert context["callers"][0]["is_cross_file"] is True
        assert "normalize(value)" in context["callers"][0]["source_excerpt"]
        assert any(
            item["function_id"] == caller.id and item["relation"] == "incoming"
            for item in context["module_dependencies"]
        )
        assert any(
            item["function_id"] == caller.id
            and item["edge_type"] == "arg_flows_to_param"
            for item in context["data_flow_neighbors"]
        )


def test_refinement_context_collects_local_subscript_key_flow():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def score_for(scores, name):\n"
            "    return scores[name]\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)
        call_graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, call_graph)
        target = parsed.functions[0]
        candidate = _candidate_for(
            target,
            repo,
            new_source="def score_for(scores, name):\n    return scores.get(name, 0)\n",
        )

        annotated = annotate_refinement_context(candidate, program_graph)

        context = annotated.metadata["refinement_context"]
        key_flow_neighbors = [
            item
            for item in context["data_flow_neighbors"]
            if item["edge_type"] == "key_flows_to_subscript"
        ]
        assert key_flow_neighbors
        assert key_flow_neighbors[0]["relation"] == "local"
        assert key_flow_neighbors[0]["is_cross_file"] is False
        assert {
            "key_variable": "name",
            "mapping_variable": "scores",
            "line": 2,
        } in key_flow_neighbors[0]["flows"]


def test_beam_search_passes_refinement_context_to_refiner():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo, target, caller, program_graph = _cross_file_repo(Path(tmp_dir))
        candidate = _candidate_for(target, repo)
        refiner = CapturingRefiner()

        BeamPatchSearch(
            sandbox=AlwaysFailingSandbox(),
            refiner=refiner,
            beam_width=1,
            max_depth=1,
            use_prior_ranking=False,
        ).search(repo, [candidate], program_graph=program_graph)

        assert refiner.previous_patch is not None
        context = refiner.previous_patch.metadata["refinement_context"]
        assert context["callers"][0]["function_id"] == caller.id
        assert context["callers"][0]["is_cross_file"] is True


class AlwaysFailingSandbox:
    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        del repo_path, candidate, test_args
        return ExecutionResult(
            success=False,
            returncode=1,
            stdout="F",
            stderr="AssertionError",
            traceback="",
            passed=0,
            failed=1,
            timeout=False,
            command=[],
        )


class CapturingRefiner:
    def __init__(self) -> None:
        self.previous_patch: PatchCandidate | None = None

    def refine_many(
        self,
        repo_path,
        previous_patch: PatchCandidate,
        execution_result: ExecutionResult,
        round_index: int,
        limit: int = 1,
    ) -> list[PatchCandidate]:
        del repo_path, execution_result, round_index, limit
        self.previous_patch = previous_patch
        return []


def _cross_file_repo(repo: Path):
    package = repo / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "helper.py").write_text(
        "def normalize(value):\n"
        "    return value + 1\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        "from .helper import normalize\n\n"
        "def run(value):\n"
        "    adjusted = normalize(value)\n"
        "    return adjusted * 2\n",
        encoding="utf-8",
    )
    parsed = RepoParser().parse(repo)
    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    program_graph = build_program_graph(parsed, call_graph)
    by_name = {function.name: function for function in parsed.functions}
    return repo, by_name["normalize"], by_name["run"], program_graph


def _candidate_for(
    function,
    repo: Path,
    *,
    new_source: str = "def normalize(value):\n    return value + 2\n",
) -> PatchCandidate:
    relative = Path(function.file_path).relative_to(repo).as_posix()
    return PatchCandidate(
        id="normalize_patch",
        target_file=function.file_path,
        relative_file_path=relative,
        target_function_id=function.id,
        target_function_name=function.metadata.get("qualified_name", function.name),
        rule_id="test_rule",
        description="test refinement context candidate",
        old_source=function.source,
        new_source=new_source,
        diff=(
            f"--- a/{relative}\n"
            f"+++ b/{relative}\n"
            "-    return value + 1\n"
            "+    return value + 2\n"
        ),
    )
