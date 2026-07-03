from pathlib import Path
import tempfile

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.repo_parser import RepoParser


FIXTURE = Path(__file__).parent / "fixtures" / "buggy_sample.py"


def test_repo_parser_extracts_entities_and_imports():
    parsed = RepoParser().parse(FIXTURE)

    function_names = {
        function.metadata["qualified_name"] for function in parsed.functions
    }
    class_names = {class_entity.name for class_entity in parsed.classes}
    import_modules = {item.module for item in parsed.imports}

    assert "normalize" in function_names
    assert "uses_helper" in function_names
    assert "Calculator.add" in function_names
    assert "Calculator.total" in function_names
    assert "test_total" in function_names
    assert "Calculator" in class_names
    assert None in import_modules
    assert "os" in import_modules
    assert [test.name for test in parsed.tests] == ["test_total", "test_shift_left"]


def test_call_graph_resolves_local_functions_and_methods():
    parsed = RepoParser().parse(FIXTURE)
    graph = build_call_graph(parsed.functions, parsed.calls)

    edges = {
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
        )
        for edge in graph.edges
    }

    assert ("uses_helper", "normalize") in edges
    assert ("Calculator.total", "Calculator.add") in edges
    helper_call = next(
        edge
        for edge in graph.edges
        if graph.nodes[edge["source"]].metadata["qualified_name"] == "uses_helper"
        and graph.nodes[edge["target"]].metadata["qualified_name"] == "normalize"
    )
    assert helper_call["arg_names"] == [["value"]]
    assert helper_call["assigned_to"] == []
    method_call = next(
        edge
        for edge in graph.edges
        if graph.nodes[edge["source"]].metadata["qualified_name"] == "Calculator.total"
        and graph.nodes[edge["target"]].metadata["qualified_name"] == "Calculator.add"
    )
    assert method_call["arg_names"] == [["total"], ["value"]]
    assert method_call["assigned_to"] == ["total"]


def test_call_graph_resolves_method_receiver_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "service.py").write_text(
            "class Worker:\n"
            "    @classmethod\n"
            "    def run(cls, value):\n"
            "        return cls.compute(value)\n\n"
            "    @staticmethod\n"
            "    def compute(value):\n"
            "        return value + 1\n\n"
            "class Other:\n"
            "    @staticmethod\n"
            "    def compute(value):\n"
            "        return value - 1\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    method_edge = next(
        edge
        for edge in graph.edges
        if graph.nodes[edge["source"]].metadata["qualified_name"] == "Worker.run"
        and graph.nodes[edge["target"]].metadata["qualified_name"]
        == "Worker.compute"
    )

    assert method_edge["resolution"] == "method_receiver"
    assert method_edge["receiver_alias"] == "cls"
    assert method_edge["class_name"] == "Worker"


def test_call_graph_does_not_treat_staticmethod_arg_as_receiver():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "service.py").write_text(
            "class Worker:\n"
            "    @staticmethod\n"
            "    def run(value):\n"
            "        return value.compute()\n\n"
            "    @staticmethod\n"
            "    def compute():\n"
            "        return 1\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)

    assert not any(
        graph.nodes[edge["source"]].metadata["qualified_name"] == "Worker.run"
        and graph.nodes[edge["target"]].metadata["qualified_name"] == "Worker.compute"
        for edge in graph.edges
    )
    assert any(call.callee == "value.compute" for call in graph.unresolved_calls)


def test_call_graph_resolves_local_instance_method_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "service.py").write_text(
            "class Worker:\n"
            "    def run(self, value):\n"
            "        return value + 1\n\n"
            "class Other:\n"
            "    def run(self, value):\n"
            "        return value - 1\n\n"
            "def dispatch(value):\n"
            "    worker = Worker()\n"
            "    return worker.run(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    method_edge = next(
        edge
        for edge in graph.edges
        if graph.nodes[edge["source"]].metadata["qualified_name"] == "dispatch"
        and graph.nodes[edge["target"]].metadata["qualified_name"] == "Worker.run"
    )

    assert method_edge["resolution"] == "instance_method"
    assert method_edge["instance_alias"] == "worker"
    assert method_edge["class_name"] == "Worker"


def test_call_graph_resolves_self_attribute_instance_method_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "service.py").write_text(
            "class Worker:\n"
            "    def run(self, value):\n"
            "        return value + 1\n\n"
            "class Dispatcher:\n"
            "    def __init__(self):\n"
            "        self.worker = Worker()\n\n"
            "    def dispatch(self, value):\n"
            "        return self.worker.run(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    method_edge = next(
        edge
        for edge in graph.edges
        if graph.nodes[edge["source"]].metadata["qualified_name"]
        == "Dispatcher.dispatch"
        and graph.nodes[edge["target"]].metadata["qualified_name"] == "Worker.run"
    )

    assert method_edge["resolution"] == "instance_method"
    assert method_edge["instance_alias"] == "self.worker"
    assert method_edge["class_name"] == "Worker"


def test_call_graph_resolves_super_method_calls():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "service.py").write_text(
            "class Base:\n"
            "    def run(self, value):\n"
            "        return value + 1\n\n"
            "class Other:\n"
            "    def run(self, value):\n"
            "        return value - 1\n\n"
            "class Child(Base):\n"
            "    def run(self, value):\n"
            "        return super().run(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    super_edge = next(
        edge
        for edge in graph.edges
        if graph.nodes[edge["source"]].metadata["qualified_name"] == "Child.run"
        and graph.nodes[edge["target"]].metadata["qualified_name"] == "Base.run"
    )

    assert super_edge["resolution"] == "super_method"
    assert super_edge["class_name"] == "Child"
    assert super_edge["base_class"] == "Base"


def test_call_graph_resolves_with_context_manager_instance_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "service.py").write_text(
            "class Worker:\n"
            "    def __enter__(self):\n"
            "        return self\n\n"
            "    def __exit__(self, exc_type, exc, tb):\n"
            "        return False\n\n"
            "    def run(self, value):\n"
            "        return value + 1\n\n"
            "def dispatch(value):\n"
            "    with Worker() as worker:\n"
            "        return worker.run(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    constructor_call = next(
        call
        for call in parsed.calls
        if call.caller_name == "dispatch" and call.callee == "Worker"
    )
    method_edge = next(
        edge
        for edge in graph.edges
        if graph.nodes[edge["source"]].metadata["qualified_name"] == "dispatch"
        and graph.nodes[edge["target"]].metadata["qualified_name"] == "Worker.run"
    )

    assert constructor_call.assigned_to == ["worker"]
    assert method_edge["resolution"] == "instance_method"
    assert method_edge["instance_alias"] == "worker"


def test_call_graph_resolves_unambiguous_cross_file_call():
    parsed = RepoParser().parse(Path("datasets/toy_bugs/cross_file_repo"))
    graph = build_call_graph(parsed.functions, parsed.calls)

    edges = {
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
        )
        for edge in graph.edges
    }

    assert ("normalize_window", "shift_left") in edges


def test_call_graph_resolves_cross_file_import_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "left.py").write_text(
            "def compute(value):\n"
            "    return value - 1\n",
            encoding="utf-8",
        )
        (repo / "right.py").write_text(
            "def compute(value):\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "import left as left_mod\n"
            "from right import compute as selected_compute\n\n"
            "def use_module_alias(value):\n"
            "    return left_mod.compute(value)\n\n"
            "def use_from_alias(value):\n"
            "    return selected_compute(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = {
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].file_path,
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
        )
        for edge in graph.edges
    }

    assert any(
        caller == "use_module_alias"
        and target_name == "compute"
        and target_path.endswith("left.py")
        and resolution == "module_import_alias"
        for caller, target_path, target_name, resolution in edges
    )
    assert any(
        caller == "use_from_alias"
        and target_name == "compute"
        and target_path.endswith("right.py")
        and resolution == "from_import_alias"
        for caller, target_path, target_name, resolution in edges
    )
    assert not graph.unresolved_calls


def test_call_graph_resolves_package_relative_import_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "worker.py").write_text(
            "def compute(value):\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
        (package / "service.py").write_text(
            "from .worker import compute as selected_compute\n"
            "from . import worker as worker_mod\n\n"
            "def use_from_relative(value):\n"
            "    return selected_compute(value)\n\n"
            "def use_module_relative(value):\n"
            "    return worker_mod.compute(value)\n",
            encoding="utf-8",
        )
        (repo / "test_service.py").write_text(
            "from pkg.service import use_from_relative, use_module_relative\n\n"
            "def test_use_relative():\n"
            "    assert use_from_relative(1) == use_module_relative(1)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = {
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].file_path,
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_module", ""),
        )
        for edge in graph.edges
    }

    assert any(
        caller == "use_from_relative"
        and target_name == "compute"
        and target_path.endswith("worker.py")
        and resolution == "from_import_alias"
        for caller, target_path, target_name, resolution, _ in edges
    )
    assert any(
        caller == "use_module_relative"
        and target_name == "compute"
        and target_path.endswith("worker.py")
        and resolution == "module_import_alias"
        and import_module == "pkg.worker"
        for caller, target_path, target_name, resolution, import_module in edges
    )


def test_call_graph_resolves_from_package_imported_submodules():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "def use_submodule_function(value):\n"
            "    return worker.run(value)\n\n"
            "def use_submodule_class(values):\n"
            "    instance = worker.Worker()\n"
            "    return instance.compute(values)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_name"),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "use_submodule_function",
        "run",
        "module_import_alias",
        "worker",
        "pkg.worker",
        "worker",
        "",
    ) in edges
    assert (
        "use_submodule_class",
        "Worker.compute",
        "instance_method",
        "worker",
        "pkg.worker",
        "worker",
        "instance",
    ) in edges


def test_call_graph_resolves_star_imported_functions_and_classes():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "worker.py").write_text(
            "def compute(value):\n"
            "    return value + 1\n\n"
            "def _private_helper(value):\n"
            "    return value - 1\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from worker import *\n\n"
            "def use_star_function(value):\n"
            "    return compute(value)\n\n"
            "def use_star_class(values):\n"
            "    worker = Worker()\n"
            "    return worker.compute(values)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].file_path,
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_name"),
            edge.get("import_kind"),
            edge.get("is_star_import", False),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "use_star_function",
        next(
            target_path
            for caller, target_path, target_name, *_ in edges
            if caller == "use_star_function" and target_name == "compute"
        ),
        "compute",
        "from_import_alias",
        "compute",
        "worker",
        "compute",
        "static",
        True,
        "",
    ) in edges
    assert any(
        caller == "use_star_class"
        and target_path.endswith("worker.py")
        and target_name == "Worker.compute"
        and resolution == "instance_method"
        and import_alias == "Worker"
        and import_module == "worker"
        and import_name == "Worker"
        and import_kind == "static"
        and is_star_import is True
        and instance_alias == "worker"
        for (
            caller,
            target_path,
            target_name,
            resolution,
            import_alias,
            import_module,
            import_name,
            import_kind,
            is_star_import,
            instance_alias,
        ) in edges
    )
    assert not any(
        edge["callee"] == "_private_helper" for edge in graph.edges
    )


def test_call_graph_respects_module_all_for_star_imports():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "worker.py").write_text(
            "PREFIX = 'Exported'\n"
            "__all__ = ['public_compute', '_private_allowed'] + [f'{PREFIX}Worker']\n\n"
            "def public_compute(value):\n"
            "    return value + 1\n\n"
            "def hidden_compute(value):\n"
            "    return value - 1\n\n"
            "def _private_allowed(value):\n"
            "    return value * 2\n\n"
            "class ExportedWorker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n\n"
            "class HiddenWorker:\n"
            "    def compute(self, values):\n"
            "        return values[0]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from worker import *\n\n"
            "def use_public(value):\n"
            "    return public_compute(value)\n\n"
            "def use_explicit_private(value):\n"
            "    return _private_allowed(value)\n\n"
            "def use_exported_class(values):\n"
            "    worker = ExportedWorker()\n"
            "    return worker.compute(values)\n\n"
            "def use_hidden_function(value):\n"
            "    return hidden_compute(value)\n\n"
            "def use_hidden_class(values):\n"
            "    worker = HiddenWorker()\n"
            "    return worker.compute(values)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    module_all = [
        item for item in parsed.imports if item.kind == "module_all"
    ]
    assert [(item.names, Path(item.file_path).name) for item in module_all] == [
        (["public_compute", "_private_allowed", "ExportedWorker"], "worker.py")
    ]

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_name"),
            edge.get("is_star_import", False),
            edge.get("star_import_uses_all", False),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "use_public",
        "public_compute",
        "from_import_alias",
        "public_compute",
        "public_compute",
        True,
        True,
        "",
    ) in edges
    assert (
        "use_explicit_private",
        "_private_allowed",
        "from_import_alias",
        "_private_allowed",
        "_private_allowed",
        True,
        True,
        "",
    ) in edges
    assert (
        "use_exported_class",
        "ExportedWorker.compute",
        "instance_method",
        "ExportedWorker",
        "ExportedWorker",
        True,
        True,
        "worker",
    ) in edges
    assert not any(target == "hidden_compute" for _, target, *_ in edges)
    assert not any(target == "HiddenWorker.compute" for _, target, *_ in edges)
    assert any(call.callee == "hidden_compute" for call in graph.unresolved_calls)
    assert any(call.callee == "worker.compute" for call in graph.unresolved_calls)


def test_call_graph_resolves_package_init_reexports():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text(
            "from .worker import run as exported_run\n"
            "from .worker import Worker as ExportedWorker\n"
            "from . import worker as worker_api\n"
            "__all__ = ['exported_run', 'ExportedWorker']\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from pkg import exported_run, ExportedWorker, worker_api\n"
            "\n"
            "def use_reexported_function(value):\n"
            "    return exported_run(value)\n\n"
            "def use_reexported_class(values):\n"
            "    worker = ExportedWorker()\n"
            "    return worker.compute(values)\n\n"
            "def use_reexported_module(value):\n"
            "    return worker_api.run(value)\n\n",
            encoding="utf-8",
        )
        (repo / "star_service.py").write_text(
            "from pkg import *\n\n"
            "def use_star_reexport(value):\n"
            "    return exported_run(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_name"),
            edge.get("is_reexport", False),
            edge.get("reexport_module", ""),
            edge.get("reexport_name", ""),
            edge.get("is_star_import", False),
            edge.get("star_import_uses_all", False),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "use_reexported_function",
        "run",
        "from_import_alias",
        "exported_run",
        "pkg",
        "exported_run",
        True,
        "pkg.worker",
        "run",
        False,
        False,
        "",
    ) in edges
    assert (
        "use_star_reexport",
        "run",
        "from_import_alias",
        "exported_run",
        "pkg",
        "exported_run",
        True,
        "pkg.worker",
        "run",
        True,
        True,
        "",
    ) in edges
    assert (
        "use_reexported_class",
        "Worker.compute",
        "instance_method",
        "ExportedWorker",
        "pkg",
        "ExportedWorker",
        True,
        "pkg.worker",
        "Worker",
        False,
        False,
        "worker",
    ) in edges
    assert (
        "use_reexported_module",
        "run",
        "module_import_alias",
        "worker_api",
        "pkg",
        "worker_api",
        True,
        "pkg.worker",
        "",
        False,
        False,
        "",
    ) in edges
    assert [
        (call.caller_name, call.callee, call.assigned_to)
        for call in graph.unresolved_calls
    ] == [("use_reexported_class", "ExportedWorker", ["worker"])]


def test_call_graph_resolves_imported_symbol_alias_assignments():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "api = worker\n"
            "handler = worker.run\n"
            "Handler = worker.Worker\n\n"
            "def use_module_alias(value):\n"
            "    return api.run(value)\n\n"
            "def use_function_alias(value):\n"
            "    return handler(value)\n\n"
            "def use_class_alias(values):\n"
            "    instance = Handler()\n"
            "    return instance.compute(values)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_name"),
            edge.get("is_symbol_alias", False),
            edge.get("symbol_alias_source", ""),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "use_module_alias",
        "run",
        "module_import_alias",
        "api",
        "pkg.worker",
        "worker",
        True,
        "worker",
        "",
    ) in edges
    assert (
        "use_function_alias",
        "run",
        "from_import_alias",
        "handler",
        "pkg.worker",
        "run",
        True,
        "worker.run",
        "",
    ) in edges
    assert (
        "use_class_alias",
        "Worker.compute",
        "instance_method",
        "Handler",
        "pkg.worker",
        "Worker",
        True,
        "worker.Worker",
        "instance",
    ) in edges


def test_call_graph_resolves_function_local_imported_symbol_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "def use_local_module_alias(value):\n"
            "    api = worker\n"
            "    return api.run(value)\n\n"
            "def use_local_function_alias(value):\n"
            "    handler = worker.run\n"
            "    return handler(value)\n\n"
            "def use_local_alias_chain(value):\n"
            "    handler = worker.run\n"
            "    runner = handler\n"
            "    return runner(value)\n\n"
            "def use_local_class_alias(values):\n"
            "    Handler = worker.Worker\n"
            "    instance = Handler()\n"
            "    return instance.compute(values)\n\n"
            "def use_shadowed_alias(value):\n"
            "    handler = worker.run\n"
            "    handler = lambda item: item\n"
            "    return handler(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_name"),
            edge.get("is_symbol_alias", False),
            edge.get("symbol_alias_scope", ""),
            edge.get("symbol_alias_source", ""),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "use_local_module_alias",
        "run",
        "local_symbol_alias",
        "api",
        "pkg.worker",
        "run",
        True,
        "local",
        "worker",
        "",
    ) in edges
    assert (
        "use_local_function_alias",
        "run",
        "local_symbol_alias",
        "handler",
        "pkg.worker",
        "run",
        True,
        "local",
        "worker.run",
        "",
    ) in edges
    assert (
        "use_local_alias_chain",
        "run",
        "local_symbol_alias",
        "runner",
        "pkg.worker",
        "run",
        True,
        "local",
        "worker.run",
        "",
    ) in edges
    assert (
        "use_local_class_alias",
        "Worker.compute",
        "instance_method",
        "Handler",
        "pkg.worker",
        "Worker",
        True,
        "local",
        "worker.Worker",
        "instance",
    ) in edges
    assert not any(
        source == "use_shadowed_alias"
        and target == "run"
        and import_alias == "handler"
        for (
            source,
            target,
            _resolution,
            import_alias,
            *_rest,
        ) in edges
    )


def test_call_graph_merges_function_local_aliases_across_branches():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n\n"
            "def fallback(value):\n"
            "    return value - 1\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "def use_alias_inside_branch(flag, value):\n"
            "    if flag:\n"
            "        handler = worker.run\n"
            "        return handler(value)\n"
            "    return value\n\n"
            "def use_alias_after_matching_branches(flag, value):\n"
            "    if flag:\n"
            "        handler = worker.run\n"
            "    else:\n"
            "        handler = worker.run\n"
            "    return handler(value)\n\n"
            "def use_class_alias_after_matching_branches(flag, values):\n"
            "    if flag:\n"
            "        Handler = worker.Worker\n"
            "    else:\n"
            "        Handler = worker.Worker\n"
            "    instance = Handler()\n"
            "    return instance.compute(values)\n\n"
            "def avoid_single_branch_leak(flag, value):\n"
            "    if flag:\n"
            "        handler = worker.run\n"
            "    return handler(value)\n\n"
            "def avoid_conflicting_branch_leak(flag, value):\n"
            "    if flag:\n"
            "        handler = worker.run\n"
            "    else:\n"
            "        handler = worker.fallback\n"
            "    return handler(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_name"),
            edge.get("is_symbol_alias", False),
            edge.get("symbol_alias_scope", ""),
            edge.get("symbol_alias_source", ""),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "use_alias_inside_branch",
        "run",
        "local_symbol_alias",
        "handler",
        "pkg.worker",
        "run",
        True,
        "local",
        "worker.run",
        "",
    ) in edges
    assert (
        "use_alias_after_matching_branches",
        "run",
        "local_symbol_alias",
        "handler",
        "pkg.worker",
        "run",
        True,
        "local",
        "worker.run",
        "",
    ) in edges
    assert (
        "use_class_alias_after_matching_branches",
        "Worker.compute",
        "instance_method",
        "Handler",
        "pkg.worker",
        "Worker",
        True,
        "local",
        "worker.Worker",
        "instance",
    ) in edges
    assert not any(
        source == "avoid_single_branch_leak" and target == "run"
        for source, target, *_rest in edges
    )
    assert not any(
        source == "avoid_conflicting_branch_leak"
        and target in {"run", "fallback"}
        for source, target, *_rest in edges
    )


def test_call_graph_merges_function_local_aliases_across_try_handlers():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n\n"
            "def fallback(value):\n"
            "    return value - 1\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "def use_alias_inside_try(value):\n"
            "    try:\n"
            "        handler = worker.run\n"
            "        return handler(value)\n"
            "    except ValueError:\n"
            "        return value\n\n"
            "def use_alias_after_matching_try_except(value):\n"
            "    try:\n"
            "        handler = worker.run\n"
            "    except ValueError:\n"
            "        handler = worker.run\n"
            "    return handler(value)\n\n"
            "def use_class_alias_after_matching_try_except(values):\n"
            "    try:\n"
            "        Handler = worker.Worker\n"
            "    except ValueError:\n"
            "        Handler = worker.Worker\n"
            "    instance = Handler()\n"
            "    return instance.compute(values)\n\n"
            "def use_finally_alias(value):\n"
            "    try:\n"
            "        value = value\n"
            "    except ValueError:\n"
            "        value = value\n"
            "    finally:\n"
            "        handler = worker.run\n"
            "    return handler(value)\n\n"
            "def avoid_try_only_leak(value):\n"
            "    try:\n"
            "        handler = worker.run\n"
            "    except ValueError:\n"
            "        value = value\n"
            "    return handler(value)\n\n"
            "def avoid_conflicting_try_except_leak(value):\n"
            "    try:\n"
            "        handler = worker.run\n"
            "    except ValueError:\n"
            "        handler = worker.fallback\n"
            "    return handler(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_name"),
            edge.get("is_symbol_alias", False),
            edge.get("symbol_alias_scope", ""),
            edge.get("symbol_alias_source", ""),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "use_alias_inside_try",
        "run",
        "local_symbol_alias",
        "handler",
        "pkg.worker",
        "run",
        True,
        "local",
        "worker.run",
        "",
    ) in edges
    assert (
        "use_alias_after_matching_try_except",
        "run",
        "local_symbol_alias",
        "handler",
        "pkg.worker",
        "run",
        True,
        "local",
        "worker.run",
        "",
    ) in edges
    assert (
        "use_class_alias_after_matching_try_except",
        "Worker.compute",
        "instance_method",
        "Handler",
        "pkg.worker",
        "Worker",
        True,
        "local",
        "worker.Worker",
        "instance",
    ) in edges
    assert (
        "use_finally_alias",
        "run",
        "local_symbol_alias",
        "handler",
        "pkg.worker",
        "run",
        True,
        "local",
        "worker.run",
        "",
    ) in edges
    assert not any(
        source == "avoid_try_only_leak" and target == "run"
        for source, target, *_rest in edges
    )
    assert not any(
        source == "avoid_conflicting_try_except_leak"
        and target in {"run", "fallback"}
        for source, target, *_rest in edges
    )


def test_call_graph_merges_function_local_aliases_across_loops():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (repo / "service.py").write_text(
            "from pkg import worker\n\n"
            "def use_alias_inside_loop(values):\n"
            "    for value in values:\n"
            "        handler = worker.run\n"
            "        return handler(value)\n"
            "    return 0\n\n"
            "def use_alias_after_loop_preserved(values, value):\n"
            "    handler = worker.run\n"
            "    for item in values:\n"
            "        item = item\n"
            "    return handler(value)\n\n"
            "def use_class_alias_after_loop_preserved(values):\n"
            "    Handler = worker.Worker\n"
            "    for item in values:\n"
            "        item = item\n"
            "    instance = Handler()\n"
            "    return instance.compute(values)\n\n"
            "def avoid_loop_body_leak(values, value):\n"
            "    for item in values:\n"
            "        handler = worker.run\n"
            "    return handler(value)\n\n"
            "def avoid_loop_target_shadow(values, value):\n"
            "    handler = worker.run\n"
            "    for handler in values:\n"
            "        value = value\n"
            "    return handler(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_name"),
            edge.get("is_symbol_alias", False),
            edge.get("symbol_alias_scope", ""),
            edge.get("symbol_alias_source", ""),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "use_alias_inside_loop",
        "run",
        "local_symbol_alias",
        "handler",
        "pkg.worker",
        "run",
        True,
        "local",
        "worker.run",
        "",
    ) in edges
    assert (
        "use_alias_after_loop_preserved",
        "run",
        "local_symbol_alias",
        "handler",
        "pkg.worker",
        "run",
        True,
        "local",
        "worker.run",
        "",
    ) in edges
    assert (
        "use_class_alias_after_loop_preserved",
        "Worker.compute",
        "instance_method",
        "Handler",
        "pkg.worker",
        "Worker",
        True,
        "local",
        "worker.Worker",
        "instance",
    ) in edges
    assert not any(
        source == "avoid_loop_body_leak" and target == "run"
        for source, target, *_rest in edges
    )
    assert not any(
        source == "avoid_loop_target_shadow" and target == "run"
        for source, target, *_rest in edges
    )


def test_call_graph_resolves_package_init_assignment_reexports():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "pkg"
        package.mkdir()
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text(
            "from . import worker\n"
            "exported_run = worker.run\n"
            "ExportedWorker = worker.Worker\n"
            "worker_api = worker\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from pkg import exported_run, ExportedWorker, worker_api\n\n"
            "def use_reexported_function(value):\n"
            "    return exported_run(value)\n\n"
            "def use_reexported_class(values):\n"
            "    worker = ExportedWorker()\n"
            "    return worker.compute(values)\n\n"
            "def use_reexported_module(value):\n"
            "    return worker_api.run(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_name"),
            edge.get("is_reexport", False),
            edge.get("reexport_module", ""),
            edge.get("is_symbol_alias", False),
            edge.get("symbol_alias_source", ""),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "use_reexported_function",
        "run",
        "from_import_alias",
        "exported_run",
        "pkg.worker",
        "run",
        True,
        "pkg.worker",
        True,
        "worker.run",
        "",
    ) in edges
    assert (
        "use_reexported_class",
        "Worker.compute",
        "instance_method",
        "ExportedWorker",
        "pkg.worker",
        "Worker",
        True,
        "pkg.worker",
        True,
        "worker.Worker",
        "worker",
    ) in edges
    assert (
        "use_reexported_module",
        "run",
        "module_import_alias",
        "worker_api",
        "pkg",
        "worker_api",
        True,
        "pkg.worker",
        True,
        "worker",
        "",
    ) in edges


def test_call_graph_resolves_string_literal_dynamic_import_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "import importlib as loader\n\n"
            "def use_dynamic_import(value):\n"
            "    mod = loader.import_module('worker')\n"
            "    return mod.run(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    dynamic_imports = [item for item in parsed.imports if item.kind == "dynamic"]
    assert len(dynamic_imports) == 1
    assert dynamic_imports[0].names == ["worker"]
    assert dynamic_imports[0].aliases == {"mod": "worker"}

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    dynamic_edge = next(
        edge
        for edge in graph.edges
        if graph.nodes[edge["source"]].metadata["qualified_name"]
        == "use_dynamic_import"
        and graph.nodes[edge["target"]].metadata["qualified_name"] == "run"
    )

    assert dynamic_edge["resolution"] == "module_import_alias"
    assert dynamic_edge["import_alias"] == "mod"
    assert dynamic_edge["import_module"] == "worker"


def test_call_graph_resolves_static_expression_dynamic_import_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "plugins"
        package.mkdir()
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from importlib import import_module as load_module\n\n"
            "PACKAGE = 'plugins'\n\n"
            "def use_dynamic_import(value):\n"
            "    leaf = 'worker'\n"
            "    module_name = f'{PACKAGE}.{leaf}'\n"
            "    mod = load_module(module_name)\n"
            "    return mod.run(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    dynamic_imports = [item for item in parsed.imports if item.kind == "dynamic"]
    assert len(dynamic_imports) == 1
    assert dynamic_imports[0].names == ["plugins.worker"]
    assert dynamic_imports[0].aliases == {"mod": "plugins.worker"}

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    dynamic_edge = next(
        edge
        for edge in graph.edges
        if graph.nodes[edge["source"]].metadata["qualified_name"]
        == "use_dynamic_import"
        and graph.nodes[edge["target"]].metadata["qualified_name"] == "run"
    )

    assert dynamic_edge["resolution"] == "module_import_alias"
    assert dynamic_edge["import_alias"] == "mod"
    assert dynamic_edge["import_module"] == "plugins.worker"


def test_call_graph_resolves_direct_dynamic_import_member_calls():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "plugins"
        package.mkdir()
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from importlib import import_module as load_module\n\n"
            "PACKAGE = 'plugins'\n\n"
            "def use_dynamic_import(value):\n"
            "    leaf = 'worker'\n"
            "    module_name = f'{PACKAGE}.{leaf}'\n"
            "    return load_module(module_name).run(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    dynamic_imports = [item for item in parsed.imports if item.kind == "dynamic"]
    assert len(dynamic_imports) == 1
    assert dynamic_imports[0].names == ["plugins.worker"]
    assert dynamic_imports[0].aliases == {"load_module": "plugins.worker"}

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    dynamic_edge = next(
        edge
        for edge in graph.edges
        if graph.nodes[edge["source"]].metadata["qualified_name"]
        == "use_dynamic_import"
        and graph.nodes[edge["target"]].metadata["qualified_name"] == "run"
    )

    assert dynamic_edge["resolution"] == "module_import_alias"
    assert dynamic_edge["import_alias"] == "load_module"
    assert dynamic_edge["import_module"] == "plugins.worker"
    assert dynamic_edge["import_kind"] == "dynamic"


def test_call_graph_resolves_getattr_dynamic_import_member_calls():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "plugins"
        package.mkdir()
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from importlib import import_module as load_module\n\n"
            "PACKAGE = 'plugins'\n\n"
            "MEMBER = 'run'\n\n"
            "def use_direct_getattr(value):\n"
            "    leaf = 'worker'\n"
            "    module_name = f'{PACKAGE}.{leaf}'\n"
            "    return getattr(load_module(module_name), 'run')(value)\n\n"
            "def use_assigned_getattr(value):\n"
            "    leaf = 'worker'\n"
            "    module_name = f'{PACKAGE}.{leaf}'\n"
            "    mod = load_module(module_name)\n"
            "    return getattr(mod, 'run')(value)\n\n"
            "def use_module_constant_getattr(value):\n"
            "    leaf = 'worker'\n"
            "    module_name = f'{PACKAGE}.{leaf}'\n"
            "    return getattr(load_module(module_name), MEMBER)(value)\n\n"
            "def use_local_constant_getattr(value):\n"
            "    leaf = 'worker'\n"
            "    module_name = f'{PACKAGE}.{leaf}'\n"
            "    member = f'{MEMBER}'\n"
            "    mod = load_module(module_name)\n"
            "    return getattr(mod, member)(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    dynamic_imports = [item for item in parsed.imports if item.kind == "dynamic"]
    assert len(dynamic_imports) == 4
    assert {tuple(item.names) for item in dynamic_imports} == {
        ("plugins.worker",),
    }
    assert {tuple(item.aliases.items()) for item in dynamic_imports} == {
        (("load_module", "plugins.worker"),),
        (("mod", "plugins.worker"),),
    }

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_kind"),
        )
        for edge in graph.edges
    ]

    assert (
        "use_direct_getattr",
        "run",
        "module_import_alias",
        "load_module",
        "plugins.worker",
        "dynamic",
    ) in edges
    assert (
        "use_assigned_getattr",
        "run",
        "module_import_alias",
        "mod",
        "plugins.worker",
        "dynamic",
    ) in edges
    assert (
        "use_module_constant_getattr",
        "run",
        "module_import_alias",
        "load_module",
        "plugins.worker",
        "dynamic",
    ) in edges
    assert (
        "use_local_constant_getattr",
        "run",
        "module_import_alias",
        "mod",
        "plugins.worker",
        "dynamic",
    ) in edges


def test_call_graph_respects_builtin_import_fromlist_semantics():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        package = repo / "plugins"
        package.mkdir()
        (package / "__init__.py").write_text(
            "def run(value):\n"
            "    return value + 10\n",
            encoding="utf-8",
        )
        (package / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
        (repo / "service_top.py").write_text(
            "def use_top_level_import(value):\n"
            "    return __import__('plugins.worker').run(value)\n",
            encoding="utf-8",
        )
        (repo / "service_leaf.py").write_text(
            "def use_leaf_import(value):\n"
            "    return __import__('plugins.worker', fromlist=['run']).run(value)\n\n"
            "def use_assigned_leaf_import(value):\n"
            "    mod = __import__('plugins.worker', None, None, ('run',))\n"
            "    return mod.run(value)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    dynamic_imports = [
        (
            Path(item.file_path).name,
            tuple(item.names),
            tuple(item.aliases.items()),
        )
        for item in parsed.imports
        if item.kind == "dynamic"
    ]
    assert (
        "service_top.py",
        ("plugins",),
        (("__import__", "plugins"),),
    ) in dynamic_imports
    assert (
        "service_leaf.py",
        ("plugins.worker",),
        (("__import__", "plugins.worker"),),
    ) in dynamic_imports
    assert (
        "service_leaf.py",
        ("plugins.worker",),
        (("mod", "plugins.worker"),),
    ) in dynamic_imports

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            Path(graph.nodes[edge["target"]].file_path).as_posix(),
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_kind"),
        )
        for edge in graph.edges
    ]

    assert any(
        caller == "use_top_level_import"
        and target_path.endswith("plugins/__init__.py")
        and target_name == "run"
        and resolution == "module_import_alias"
        and import_alias == "__import__"
        and import_module == "plugins"
        and import_kind == "dynamic"
        for (
            caller,
            target_path,
            target_name,
            resolution,
            import_alias,
            import_module,
            import_kind,
        ) in edges
    )
    assert any(
        caller == "use_leaf_import"
        and target_path.endswith("plugins/worker.py")
        and target_name == "run"
        and resolution == "module_import_alias"
        and import_alias == "__import__"
        and import_module == "plugins.worker"
        and import_kind == "dynamic"
        for (
            caller,
            target_path,
            target_name,
            resolution,
            import_alias,
            import_module,
            import_kind,
        ) in edges
    )
    assert any(
        caller == "use_assigned_leaf_import"
        and target_path.endswith("plugins/worker.py")
        and target_name == "run"
        and resolution == "module_import_alias"
        and import_alias == "mod"
        and import_module == "plugins.worker"
        and import_kind == "dynamic"
        for (
            caller,
            target_path,
            target_name,
            resolution,
            import_alias,
            import_module,
            import_kind,
        ) in edges
    )


def test_call_graph_resolves_dynamic_imported_member_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "worker.py").write_text(
            "def run(value):\n"
            "    return value + 1\n\n"
            "class Worker:\n"
            "    def compute(self, values):\n"
            "        return values[1]\n",
            encoding="utf-8",
        )
        (repo / "service.py").write_text(
            "from importlib import import_module as load_module\n\n"
            "MODULE = 'worker'\n"
            "FUNCTION = 'run'\n"
            "CLASS_NAME = 'Worker'\n\n"
            "def call_function_alias(value):\n"
            "    handler = getattr(load_module(MODULE), FUNCTION)\n"
            "    return handler(value)\n\n"
            "def call_module_member_alias(value):\n"
            "    mod = load_module(MODULE)\n"
            "    handler = mod.run\n"
            "    return handler(value)\n\n"
            "def call_class_alias(values):\n"
            "    WorkerAlias = getattr(load_module(MODULE), CLASS_NAME)\n"
            "    worker = WorkerAlias()\n"
            "    return worker.compute(values)\n\n"
            "def call_module_class_alias(values):\n"
            "    mod = load_module(MODULE)\n"
            "    WorkerAlias = mod.Worker\n"
            "    worker = WorkerAlias()\n"
            "    return worker.compute(values)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    dynamic_members = [
        item for item in parsed.imports if item.kind == "dynamic_member"
    ]
    assert {
        (item.module, tuple(item.names), tuple(item.aliases.items()))
        for item in dynamic_members
    } == {
        ("worker", ("run",), (("handler", "run"),)),
        ("worker", ("Worker",), (("WorkerAlias", "Worker"),)),
    }

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    edges = [
        (
            graph.nodes[edge["source"]].metadata["qualified_name"],
            graph.nodes[edge["target"]].metadata["qualified_name"],
            edge.get("resolution"),
            edge.get("import_alias"),
            edge.get("import_module"),
            edge.get("import_name"),
            edge.get("import_kind"),
            edge.get("instance_alias", ""),
        )
        for edge in graph.edges
    ]

    assert (
        "call_function_alias",
        "run",
        "from_import_alias",
        "handler",
        "worker",
        "run",
        "dynamic_member",
        "",
    ) in edges
    assert (
        "call_module_member_alias",
        "run",
        "from_import_alias",
        "handler",
        "worker",
        "run",
        "dynamic_member",
        "",
    ) in edges
    assert (
        "call_class_alias",
        "Worker.compute",
        "instance_method",
        "WorkerAlias",
        "worker",
        "Worker",
        "dynamic_member",
        "worker",
    ) in edges
    assert (
        "call_module_class_alias",
        "Worker.compute",
        "instance_method",
        "WorkerAlias",
        "worker",
        "Worker",
        "dynamic_member",
        "worker",
    ) in edges


def test_call_graph_marks_awaited_calls_without_duplicate_call_sites():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "service.py").write_text(
            "async def fetch(value):\n"
            "    return value + 1\n\n"
            "async def run(value):\n"
            "    result = await fetch(value)\n"
            "    return result\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    run = next(
        function
        for function in parsed.functions
        if function.metadata["qualified_name"] == "run"
    )
    run_calls = [call for call in parsed.calls if call.caller_id == run.id]
    assert len(run_calls) == 1
    assert run_calls[0].callee == "fetch"
    assert run_calls[0].is_awaited is True
    assert run_calls[0].assigned_to == ["result"]

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    async_edge = next(edge for edge in graph.edges if edge["callee"] == "fetch")
    assert async_edge["is_awaited"] is True


def test_call_graph_marks_asyncio_task_and_gather_calls():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "service.py").write_text(
            "import asyncio as aio\n"
            "from asyncio import gather as gather_tasks\n\n"
            "async def fetch(value):\n"
            "    return value + 1\n\n"
            "async def other(value):\n"
            "    return value - 1\n\n"
            "async def run(value):\n"
            "    task = aio.create_task(fetch(value))\n"
            "    results = await gather_tasks(task, other(value))\n"
            "    return results\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    run = next(
        function
        for function in parsed.functions
        if function.metadata["qualified_name"] == "run"
    )
    run_calls = {call.callee: call for call in parsed.calls if call.caller_id == run.id}

    assert run_calls["fetch"].async_kind == "task"
    assert run_calls["fetch"].is_awaited is False
    assert run_calls["other"].async_kind == "gather"
    assert run_calls["other"].is_awaited is False
    assert run_calls["gather_tasks"].is_awaited is True
    assert run_calls["gather_tasks"].async_kind == "await"

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    fetch_edge = next(edge for edge in graph.edges if edge["callee"] == "fetch")
    other_edge = next(edge for edge in graph.edges if edge["callee"] == "other")
    assert fetch_edge["async_kind"] == "task"
    assert other_edge["async_kind"] == "gather"


def test_call_graph_marks_asyncio_loop_and_task_group_aliases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "service.py").write_text(
            "import asyncio as aio\n"
            "from asyncio import TaskGroup as Group\n\n"
            "async def fetch(value):\n"
            "    return value + 1\n\n"
            "async def other(value):\n"
            "    return value - 1\n\n"
            "async def run(value):\n"
            "    loop = aio.get_running_loop()\n"
            "    task = loop.create_task(fetch(value))\n"
            "    async with Group() as group:\n"
            "        group.create_task(other(value))\n"
            "    return await task\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)

    run = next(
        function
        for function in parsed.functions
        if function.metadata["qualified_name"] == "run"
    )
    run_calls = {call.callee: call for call in parsed.calls if call.caller_id == run.id}

    assert run_calls["fetch"].async_kind == "task"
    assert run_calls["fetch"].is_awaited is False
    assert run_calls["other"].async_kind == "task"
    assert run_calls["other"].is_awaited is False
    assert run_calls["aio.get_running_loop"].assigned_to == ["loop"]
    assert run_calls["Group"].assigned_to == ["group"]

    graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    fetch_edge = next(edge for edge in graph.edges if edge["callee"] == "fetch")
    other_edge = next(edge for edge in graph.edges if edge["callee"] == "other")
    assert fetch_edge["async_kind"] == "task"
    assert other_edge["async_kind"] == "task"


def test_repo_parser_ignores_source_cache_directory():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "sample.py").write_text(
            "def real_function():\n"
            "    return 1\n",
            encoding="utf-8",
        )
        cache_dir = root / ".source_cache"
        cache_dir.mkdir()
        (cache_dir / "cached.py").write_text(
            "def cached_function():\n"
            "    return 2\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(root)

    names = {function.name for function in parsed.functions}
    assert names == {"real_function"}


def test_repo_parser_skips_unparseable_files_when_parsing_directory():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "sample.py").write_text(
            "def real_function():\n"
            "    return 1\n",
            encoding="utf-8",
        )
        (root / "legacy_python2.py").write_text(
            "try:\n"
            "    pass\n"
            "except ValueError, exc:\n"
            "    pass\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(root)

    names = {function.name for function in parsed.functions}
    assert names == {"real_function"}


def test_rule_based_bug_detector_finds_phase1_rules():
    parsed = RepoParser().parse(FIXTURE)
    detector = RuleBasedBugDetector()
    findings = detector.detect(parsed.functions)

    rule_ids = {finding.rule_id for finding in findings}
    assert "always_true_len_check" in rule_ids
    assert "possible_index_overrun" in rule_ids
    assert "broad_exception_pass" in rule_ids
    assert "mutable_default_arg" in rule_ids
    assert "inplace_api_return_value" in rule_ids
    assert "stringified_numeric_value" in rule_ids
    assert "missing_len_zero_guard" in rule_ids
    assert "enumerate_start_zero_counter" in rule_ids


def test_rule_based_bug_detector_finds_inverted_empty_guard():
    with tempfile.TemporaryDirectory() as tmp_dir:
        source = Path(tmp_dir) / "sample.py"
        source.write_text(
            "def mean(values):\n"
            "    if values:\n"
            "        raise ValueError('empty')\n"
            "    return sum(values) / len(values)\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(source)
        findings = RuleBasedBugDetector().detect(parsed.functions)

    finding = next(
        item for item in findings if item.rule_id == "inverted_empty_guard"
    )

    assert finding.bug_type == "condition error"
    assert finding.evidence["guard_name"] == "values"


def test_rule_based_bug_detector_finds_identity_comparison_literal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        source = Path(tmp_dir) / "sample.py"
        source.write_text(
            "def is_admin(token):\n"
            "    return token is 'admin'\n\n"
            "def is_missing(value):\n"
            "    return value is None\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(source)
        findings = RuleBasedBugDetector().detect(parsed.functions)

    finding = next(
        item for item in findings if item.rule_id == "identity_comparison_literal"
    )

    assert finding.function_name == "is_admin"
    assert finding.bug_type == "comparison semantics error"
    assert finding.evidence["operator"] == "is"
    assert finding.evidence["literal"] == "'admin'"
    assert all(
        item.rule_id != "identity_comparison_literal"
        for item in findings
        if item.function_name == "is_missing"
    )


def test_rule_based_bug_detector_finds_iterator_double_consumption():
    with tempfile.TemporaryDirectory() as tmp_dir:
        source = Path(tmp_dir) / "sample.py"
        source.write_text(
            "def average_iterable(values):\n"
            "    total = sum(values)\n"
            "    count = len(list(values))\n"
            "    return total / count\n\n"
            "def safe_average(values):\n"
            "    values = list(values)\n"
            "    total = sum(values)\n"
            "    count = len(values)\n"
            "    return total / count\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(source)
        findings = RuleBasedBugDetector().detect(parsed.functions)

    finding = next(
        item for item in findings if item.rule_id == "iterator_double_consumption"
    )

    assert finding.function_name == "average_iterable"
    assert finding.bug_type == "iterator state error"
    assert finding.evidence["iterable"] == "values"
    assert finding.evidence["consumer"] == "sum"
    assert all(
        item.rule_id != "iterator_double_consumption"
        for item in findings
        if item.function_name == "safe_average"
    )


def test_rule_based_bug_detector_finds_dict_missing_key_guard():
    with tempfile.TemporaryDirectory() as tmp_dir:
        source = Path(tmp_dir) / "sample.py"
        source.write_text(
            "def score_for(scores, name):\n"
            "    return scores[name]\n\n"
            "def guarded_score(scores, name):\n"
            "    if name not in scores:\n"
            "        return 0\n"
            "    return scores[name]\n\n"
            "def present_guard_score(scores, name):\n"
            "    if name in scores:\n"
            "        return scores[name]\n"
            "    return 0\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(source)
        findings = RuleBasedBugDetector().detect(parsed.functions)

    finding = next(
        item for item in findings if item.rule_id == "dict_missing_key_guard"
    )

    assert finding.function_name == "score_for"
    assert finding.bug_type == "key error"
    assert finding.evidence["mapping"] == "scores"
    assert finding.evidence["key"] == "name"
    assert all(
        item.rule_id != "dict_missing_key_guard"
        for item in findings
        if item.function_name in {"guarded_score", "present_guard_score"}
    )


def test_len_denominator_guard_accepts_positive_threshold_checks():
    with tempfile.TemporaryDirectory() as tmp_dir:
        source = Path(tmp_dir) / "sample.py"
        source.write_text(
            "def guarded_mean(values):\n"
            "    n = len(values)\n"
            "    if n < 1:\n"
            "        raise ValueError('empty')\n"
            "    return sum(values) / n\n\n"
            "def guarded_pair_mean(values):\n"
            "    n = len(values)\n"
            "    if n < 2:\n"
            "        raise ValueError('need two')\n"
            "    return sum(values) / n\n\n"
            "def unguarded_mean(values):\n"
            "    n = len(values)\n"
            "    return sum(values) / n\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(source)
        findings = RuleBasedBugDetector().detect(parsed.functions)

    missing_guard_by_function = {
        finding.function_name
        for finding in findings
        if finding.rule_id == "missing_len_zero_guard"
    }
    assert "unguarded_mean" in missing_guard_by_function
    assert "guarded_mean" not in missing_guard_by_function
    assert "guarded_pair_mean" not in missing_guard_by_function


def test_rule_precision_filters_static_negative_samples():
    with tempfile.TemporaryDirectory() as tmp_dir:
        source = Path(tmp_dir) / "sample.py"
        source.write_text(
            "def guarded_by_source(values):\n"
            "    n = len(values)\n"
            "    if not values:\n"
            "        raise ValueError('empty')\n"
            "    return sum(values) / n\n\n"
            "def guarded_by_len_source(values):\n"
            "    n = len(values)\n"
            "    if len(values) == 0:\n"
            "        raise ValueError('empty')\n"
            "    return sum(values) / n\n\n"
            "def mapping_lookup(values, mapping):\n"
            "    index = str(len(values) // 2)\n"
            "    return mapping[index]\n\n"
            "class Recorder:\n"
            "    def add(self, item):\n"
            "        result = self.builder.append(item)\n"
            "        return result\n",
            encoding="utf-8",
        )

        parsed = RepoParser().parse(source)
        findings = RuleBasedBugDetector().detect(parsed.functions)

    by_function = {}
    for finding in findings:
        by_function.setdefault(finding.function_name, set()).add(finding.rule_id)

    assert "missing_len_zero_guard" not in by_function.get("guarded_by_source", set())
    assert "missing_len_zero_guard" not in by_function.get(
        "guarded_by_len_source",
        set(),
    )
    assert "stringified_numeric_value" not in by_function.get("mapping_lookup", set())
    assert "dict_missing_key_guard" not in by_function.get("mapping_lookup", set())
    assert "inplace_api_return_value" not in by_function.get("Recorder.add", set())


def test_detector_ranks_buggy_functions_above_clean_helpers():
    parsed = RepoParser().parse(FIXTURE)
    graph = build_call_graph(parsed.functions, parsed.calls)
    detector = RuleBasedBugDetector()
    findings = detector.detect(parsed.functions)

    ranked = detector.rank(parsed.functions, findings, graph)
    clean_helper = next(item for item in ranked if item.function_name == "normalize")
    by_name = {item.function_name: item for item in ranked}

    assert by_name["shift_left"].final_score > clean_helper.final_score
    assert by_name["has_items"].final_score > clean_helper.final_score
    assert by_name["hidden_error"].final_score > clean_helper.final_score
    assert by_name["sorted_values"].final_score > clean_helper.final_score
    assert by_name["middle_value"].final_score > clean_helper.final_score
    assert by_name["average_value"].final_score > clean_helper.final_score
    assert by_name["iterator_average.count_items"].final_score > clean_helper.final_score
    assert clean_helper.final_score < ranked[0].final_score
