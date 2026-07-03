from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_intelligence_agent.core.models import CallSite, CodeEntity, ImportInfo


@dataclass
class CallGraph:
    nodes: dict[str, CodeEntity] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)
    unresolved_calls: list[CallSite] = field(default_factory=list)

    def in_degree(self, function_id: str) -> int:
        return sum(1 for edge in self.edges if edge["target"] == function_id)

    def out_degree(self, function_id: str) -> int:
        return sum(1 for edge in self.edges if edge["source"] == function_id)

    def degree(self, function_id: str) -> int:
        return self.in_degree(function_id) + self.out_degree(function_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": self.edges,
            "unresolved_calls": [call.to_dict() for call in self.unresolved_calls],
        }


class CallGraphBuilder:
    def build(
        self,
        functions: list[CodeEntity],
        calls: list[CallSite],
        imports: list[ImportInfo] | None = None,
    ) -> CallGraph:
        graph = CallGraph(nodes={function.id: function for function in functions})
        resolver = _FunctionResolver(functions, imports or [], calls)
        for call in calls:
            resolution = resolver.resolve(call)
            if resolution is None:
                target_id = None
                metadata = {}
            else:
                target_id = resolution.target_id
                metadata = resolution.metadata
            if target_id is None:
                graph.unresolved_calls.append(call)
                continue
            graph.edges.append(
                {
                    "source": call.caller_id,
                    "target": target_id,
                    "type": "calls",
                    "callee": call.callee,
                    "line": call.line,
                    "arg_names": call.arg_names,
                    "assigned_to": call.assigned_to,
                    "is_awaited": call.is_awaited,
                    "async_kind": call.async_kind,
                    "weight": 1.0,
                    **metadata,
                }
            )
        return graph


@dataclass(frozen=True)
class _ResolvedCall:
    target_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _ImportContext:
    function_aliases: dict[str, str] = field(default_factory=dict)
    module_aliases: dict[str, str] = field(default_factory=dict)
    object_aliases: dict[str, tuple[str, str]] = field(default_factory=dict)
    class_aliases: dict[str, tuple[str, str]] = field(default_factory=dict)
    explicit_star_exports_by_module: dict[str, set[str]] = field(default_factory=dict)
    function_alias_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    module_alias_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    object_alias_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    class_alias_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class _ModuleExports:
    function_aliases: dict[str, str] = field(default_factory=dict)
    module_aliases: dict[str, str] = field(default_factory=dict)
    object_aliases: dict[str, tuple[str, str]] = field(default_factory=dict)
    class_aliases: dict[str, tuple[str, str]] = field(default_factory=dict)
    function_alias_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    module_alias_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    object_alias_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    class_alias_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class _InstanceAlias:
    module_name: str
    class_name: str
    line: int
    metadata: dict[str, Any] = field(default_factory=dict)


class _FunctionResolver:
    def __init__(
        self,
        functions: list[CodeEntity],
        imports: list[ImportInfo],
        calls: list[CallSite] | None = None,
    ) -> None:
        self.by_qualified: dict[str, str] = {}
        self.by_simple: dict[str, list[str]] = defaultdict(list)
        self.by_file_and_simple: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.by_class_and_simple: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.by_module_and_qualified: dict[tuple[str, str], str] = {}
        self.by_module_and_simple: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.top_level_functions_by_module: dict[str, dict[str, str]] = defaultdict(dict)
        self.classes_by_module: dict[str, set[str]] = defaultdict(set)
        self.star_exports_by_module: dict[str, set[str]] = {}
        self.classes_by_module_and_name: set[tuple[str, str]] = set()
        self.class_bases_by_module_and_name: dict[tuple[str, str], list[str]] = {}
        self.method_receiver_by_function: dict[str, str] = {}
        self.imports_by_file: dict[str, list[ImportInfo]] = defaultdict(list)
        self.imports_by_module: dict[str, list[ImportInfo]] = defaultdict(list)
        self.import_context_by_file: dict[str, _ImportContext] = {}
        self.module_exports_cache: dict[str, _ModuleExports] = {}
        self.repo_root = _common_root(
            [Path(function.file_path).resolve().parent for function in functions]
        )
        self.module_by_file: dict[str, str] = {}
        self.module_by_function_id: dict[str, str] = {}
        self.known_modules: set[str] = set()
        for function in functions:
            file_path = _normalized_path(function.file_path)
            module_name = _module_name(file_path, self.repo_root)
            self.module_by_file[file_path] = module_name
            self.module_by_function_id[function.id] = module_name
            self.known_modules.add(module_name)
            qualified_name = function.metadata.get("qualified_name", function.name)
            self.by_qualified[qualified_name] = function.id
            self.by_simple[function.name].append(function.id)
            self.by_module_and_qualified[(module_name, qualified_name)] = function.id
            self.by_module_and_simple[(module_name, function.name)].append(function.id)
            file_key = (file_path, function.name)
            self.by_file_and_simple[file_key].append(function.id)
            class_name = function.metadata.get("class_name")
            if class_name:
                self.classes_by_module[module_name].add(class_name)
                self.by_class_and_simple[(class_name, function.name)].append(function.id)
                self.classes_by_module_and_name.add((module_name, class_name))
                self.class_bases_by_module_and_name.setdefault(
                    (module_name, class_name),
                    list(function.metadata.get("class_bases", [])),
                )
                args = list(function.metadata.get("args", []))
                decorators = set(function.metadata.get("decorators", []))
                if args and not _has_decorator(decorators, "staticmethod"):
                    receiver = str(args[0])
                    if receiver in {"self", "cls"} or _has_decorator(
                        decorators, "classmethod"
                    ):
                        self.method_receiver_by_function[function.id] = receiver
            else:
                self.top_level_functions_by_module[module_name][function.name] = function.id
        for item in imports:
            import_file = _normalized_path(item.file_path)
            self.imports_by_file[import_file].append(item)
            item_module = self.module_by_file.get(
                import_file,
                _module_name(import_file, self.repo_root),
            )
            self.module_by_file.setdefault(import_file, item_module)
            self.known_modules.add(item_module)
            self.imports_by_module[item_module].append(item)
            if item.kind == "module_all":
                self.star_exports_by_module[item_module] = set(item.names)
        (
            self.instance_aliases_by_caller,
            self.instance_aliases_by_class,
        ) = self._infer_instance_aliases(calls or [])

    def resolve(self, call: CallSite) -> _ResolvedCall | None:
        simple_name = call.callee.split(".")[-1]
        caller_file = _normalized_path(call.file_path)
        instance_resolution = self._resolve_instance_method(call)
        if instance_resolution is not None:
            return instance_resolution

        receiver_resolution = self._resolve_method_receiver_call(call)
        if receiver_resolution is not None:
            return receiver_resolution

        if call.local_symbol_alias_callee:
            import_resolution = self._resolve_imported_call(call)
            if import_resolution is not None:
                return import_resolution

        local_symbol_alias_resolution = self._resolve_local_symbol_alias_call(call)
        if local_symbol_alias_resolution is not None:
            return local_symbol_alias_resolution

        if "." not in call.callee:
            file_matches = self.by_file_and_simple.get((caller_file, simple_name), [])
            if len(file_matches) == 1:
                return _ResolvedCall(
                    file_matches[0],
                    {"resolution": "same_file"},
                )

        import_resolution = self._resolve_imported_call(call)
        if import_resolution is not None:
            return import_resolution

        if call.callee in self.by_qualified:
            target_id = self.by_qualified[call.callee]
            if self._is_explicit_star_blocked_target(call, target_id):
                return None
            return _ResolvedCall(
                target_id,
                {"resolution": "qualified_name"},
            )

        super_resolution = self._resolve_super_method(call)
        if super_resolution is not None:
            return super_resolution

        if "." not in call.callee:
            simple_matches = self._filter_explicit_star_blocked_matches(
                call,
                self.by_simple.get(simple_name, []),
            )
            if len(simple_matches) == 1:
                return _ResolvedCall(
                    simple_matches[0],
                    {"resolution": "unique_simple_name"},
                )
        return None

    def _resolve_local_symbol_alias_call(self, call: CallSite) -> _ResolvedCall | None:
        if not call.local_symbol_alias_callee:
            return None
        context = self._import_context(call.file_path)
        resolution = self._resolve_context_function_reference(
            call.local_symbol_alias_callee,
            context,
        )
        if resolution is None:
            return None
        return _ResolvedCall(
            resolution.target_id,
            _local_symbol_alias_metadata(resolution.metadata, call=call),
        )

    def _resolve_imported_call(self, call: CallSite) -> _ResolvedCall | None:
        context = self._import_context(call.file_path)
        direct_target = context.function_aliases.get(call.callee)
        if direct_target is not None:
            metadata = {
                "resolution": "from_import_alias",
                "import_alias": call.callee,
                **context.function_alias_metadata.get(call.callee, {}),
            }
            return _ResolvedCall(
                direct_target,
                metadata,
            )

        module_match = _longest_prefix_match(call.callee, context.module_aliases)
        if module_match is not None:
            alias, module_name, suffix = module_match
            target_id = self._resolve_module_member(module_name, suffix)
            if target_id is not None:
                metadata = {
                    "resolution": "module_import_alias",
                    "import_alias": alias,
                    "import_module": module_name,
                    **context.module_alias_metadata.get(alias, {}),
                }
                return _ResolvedCall(
                    target_id,
                    metadata,
                )

        object_match = _longest_prefix_match(call.callee, context.object_aliases)
        if object_match is not None:
            alias, imported, suffix = object_match
            module_name, imported_name = imported
            member = imported_name if not suffix else f"{imported_name}.{suffix}"
            target_id = self._resolve_module_member(module_name, member)
            if target_id is not None:
                metadata = {
                    "resolution": "from_import_object_alias",
                    "import_alias": alias,
                    "import_module": module_name,
                    "import_name": imported_name,
                    **context.object_alias_metadata.get(alias, {}),
                }
                return _ResolvedCall(
                    target_id,
                    metadata,
                )
        return None

    def _resolve_context_function_reference(
        self,
        callee: str,
        context: _ImportContext,
    ) -> _ResolvedCall | None:
        direct_target = context.function_aliases.get(callee)
        if direct_target is not None:
            return _ResolvedCall(
                direct_target,
                {
                    "resolution": "from_import_alias",
                    **context.function_alias_metadata.get(callee, {}),
                },
            )

        module_match = _longest_prefix_match(callee, context.module_aliases)
        if module_match is not None:
            source_alias, module_name, suffix = module_match
            member_ref = self._resolve_module_member_reference(module_name, suffix)
            if member_ref is not None:
                target_id, target_module, target_member = member_ref
                return _ResolvedCall(
                    target_id,
                    {
                        "resolution": "module_import_alias",
                        **context.module_alias_metadata.get(source_alias, {}),
                        "import_module": target_module,
                        "import_name": target_member,
                    },
                )

        object_match = _longest_prefix_match(callee, context.object_aliases)
        if object_match is not None:
            source_alias, imported, suffix = object_match
            module_name, imported_name = imported
            member = imported_name if not suffix else f"{imported_name}.{suffix}"
            member_ref = self._resolve_module_member_reference(module_name, member)
            if member_ref is not None:
                target_id, target_module, target_member = member_ref
                return _ResolvedCall(
                    target_id,
                    {
                        "resolution": "from_import_object_alias",
                        **context.object_alias_metadata.get(source_alias, {}),
                        "import_module": target_module,
                        "import_name": target_member,
                    },
                )
        return None

    def _infer_instance_aliases(
        self,
        calls: list[CallSite],
    ) -> tuple[
        dict[str, dict[str, _InstanceAlias]],
        dict[str, dict[str, _InstanceAlias]],
    ]:
        caller_aliases: dict[str, dict[str, _InstanceAlias]] = defaultdict(dict)
        class_aliases: dict[str, dict[str, _InstanceAlias]] = defaultdict(dict)
        for call in sorted(calls, key=lambda item: (item.caller_id, item.line, item.col)):
            if not call.assigned_to:
                continue
            constructor = self._resolve_class_constructor(call)
            caller_class_name = _caller_class_name(call.caller_name)
            if constructor is None:
                for assigned_name in call.assigned_to:
                    caller_aliases[call.caller_id].pop(assigned_name, None)
                    if assigned_name.startswith("self.") and caller_class_name:
                        class_aliases[caller_class_name].pop(assigned_name, None)
                continue
            module_name, class_name, metadata = constructor
            for assigned_name in call.assigned_to:
                alias = _InstanceAlias(
                    module_name=module_name,
                    class_name=class_name,
                    line=call.line,
                    metadata=metadata,
                )
                caller_aliases[call.caller_id][assigned_name] = alias
                if assigned_name.startswith("self.") and caller_class_name:
                    class_aliases[caller_class_name][assigned_name] = alias
        return (
            {caller_id: dict(items) for caller_id, items in caller_aliases.items()},
            {class_name: dict(items) for class_name, items in class_aliases.items()},
        )

    def _resolve_class_constructor(
        self,
        call: CallSite,
    ) -> tuple[str, str, dict[str, Any]] | None:
        caller_file = _normalized_path(call.file_path)
        caller_module = self.module_by_file.get(caller_file, "")
        context = self._import_context(call.file_path)

        class_alias = context.class_aliases.get(call.callee)
        if class_alias is not None:
            module_name, class_name = class_alias
            metadata = {
                "import_alias": call.callee,
                **context.class_alias_metadata.get(call.callee, {}),
            }
            return module_name, class_name, metadata

        local_class_alias = self._resolve_local_symbol_alias_class_constructor(
            call,
            context,
        )
        if local_class_alias is not None:
            return local_class_alias

        module_match = _longest_prefix_match(call.callee, context.module_aliases)
        if module_match is not None:
            alias, module_name, suffix = module_match
            if suffix and "." not in suffix:
                if (module_name, suffix) in self.classes_by_module_and_name:
                    metadata = {
                        "import_alias": alias,
                        **context.module_alias_metadata.get(alias, {}),
                    }
                    return module_name, suffix, metadata

        if "." not in call.callee:
            class_name = call.callee
            if (caller_module, class_name) in self.classes_by_module_and_name:
                return (
                    caller_module,
                    class_name,
                    {"class_module": caller_module},
                )
        return None

    def _resolve_local_symbol_alias_class_constructor(
        self,
        call: CallSite,
        context: _ImportContext,
    ) -> tuple[str, str, dict[str, Any]] | None:
        if not call.local_symbol_alias_callee:
            return None
        class_ref = self._resolve_context_class_reference(
            call.local_symbol_alias_callee,
            context,
        )
        if class_ref is None:
            return None
        module_name, class_name, metadata = class_ref
        return (
            module_name,
            class_name,
            _local_symbol_alias_metadata(metadata, call=call),
        )

    def _resolve_context_class_reference(
        self,
        callee: str,
        context: _ImportContext,
    ) -> tuple[str, str, dict[str, Any]] | None:
        class_alias = context.class_aliases.get(callee)
        if class_alias is not None:
            module_name, class_name = class_alias
            return (
                module_name,
                class_name,
                {
                    **context.class_alias_metadata.get(callee, {}),
                    "import_module": module_name,
                    "import_name": class_name,
                },
            )

        module_match = _longest_prefix_match(callee, context.module_aliases)
        if module_match is not None:
            source_alias, module_name, suffix = module_match
            class_ref = self._resolve_module_class_reference(module_name, suffix)
            if class_ref is not None:
                target_module, class_name = class_ref
                return (
                    target_module,
                    class_name,
                    {
                        **context.module_alias_metadata.get(source_alias, {}),
                        "import_module": target_module,
                        "import_name": class_name,
                    },
                )

        object_match = _longest_prefix_match(callee, context.object_aliases)
        if object_match is not None:
            source_alias, imported, suffix = object_match
            module_name, imported_name = imported
            member = imported_name if not suffix else f"{imported_name}.{suffix}"
            class_ref = self._resolve_module_class_reference(module_name, member)
            if class_ref is not None:
                target_module, class_name = class_ref
                return (
                    target_module,
                    class_name,
                    {
                        **context.object_alias_metadata.get(source_alias, {}),
                        "import_module": target_module,
                        "import_name": class_name,
                    },
                )
        return None

    def _resolve_instance_method(self, call: CallSite) -> _ResolvedCall | None:
        if "." not in call.callee:
            return None
        local_aliases = self.instance_aliases_by_caller.get(call.caller_id, {})
        alias_match = _longest_prefix_match(
            call.callee,
            local_aliases,
        )
        is_local_alias = alias_match is not None
        caller_class_name = _caller_class_name(call.caller_name)
        if caller_class_name:
            class_match = _longest_prefix_match(
                call.callee,
                self.instance_aliases_by_class.get(caller_class_name, {}),
            )
            if class_match is not None:
                if alias_match is None or len(class_match[0]) > len(alias_match[0]):
                    alias_match = class_match
                    is_local_alias = False
        if alias_match is None:
            return None
        receiver, instance_alias, method_name = alias_match
        if not receiver or not method_name:
            return None
        if is_local_alias and instance_alias.line > call.line:
            return None
        target_id = self.by_module_and_qualified.get(
            (
                instance_alias.module_name,
                f"{instance_alias.class_name}.{method_name}",
            )
        )
        if target_id is None and "." not in method_name:
            matches = self.by_class_and_simple.get(
                (instance_alias.class_name, method_name),
                [],
            )
            if len(matches) == 1:
                target_id = matches[0]
        if target_id is None:
            return None
        metadata = {
            **instance_alias.metadata,
            "resolution": "instance_method",
            "instance_alias": receiver,
            "class_name": instance_alias.class_name,
            "class_module": instance_alias.module_name,
        }
        return _ResolvedCall(target_id, metadata)

    def _resolve_method_receiver_call(self, call: CallSite) -> _ResolvedCall | None:
        if "." not in call.callee:
            return None
        receiver, method_name = call.callee.split(".", 1)
        if not receiver or not method_name:
            return None
        expected_receiver = self.method_receiver_by_function.get(call.caller_id)
        if receiver != expected_receiver:
            return None
        class_name = _caller_class_name(call.caller_name)
        if not class_name:
            return None
        caller_module = self.module_by_file.get(_normalized_path(call.file_path), "")
        target_id = self.by_module_and_qualified.get(
            (caller_module, f"{class_name}.{method_name}")
        )
        if target_id is None and "." not in method_name:
            class_matches = self.by_class_and_simple.get((class_name, method_name), [])
            if len(class_matches) == 1:
                target_id = class_matches[0]
        if target_id is None:
            return None
        return _ResolvedCall(
            target_id,
            {
                "resolution": "method_receiver",
                "receiver_alias": receiver,
                "class_name": class_name,
                "class_module": caller_module,
            },
        )

    def _resolve_super_method(self, call: CallSite) -> _ResolvedCall | None:
        if not call.callee.startswith("super."):
            return None
        method_name = call.callee.split(".", 1)[1]
        if not method_name:
            return None
        class_name = _caller_class_name(call.caller_name)
        if not class_name:
            return None
        caller_module = self.module_by_file.get(_normalized_path(call.file_path), "")
        bases = self.class_bases_by_module_and_name.get((caller_module, class_name), [])
        for base in bases:
            base_info = self._resolve_base_class(base, call.file_path)
            if base_info is None:
                continue
            base_module, base_class, base_metadata = base_info
            target_id = self.by_module_and_qualified.get(
                (base_module, f"{base_class}.{method_name}")
            )
            if target_id is None and "." not in method_name:
                matches = self.by_class_and_simple.get((base_class, method_name), [])
                if len(matches) == 1:
                    target_id = matches[0]
            if target_id is None:
                continue
            metadata = {
                "resolution": "super_method",
                "class_name": class_name,
                "class_module": caller_module,
                "base_class": base_class,
                "base_module": base_module,
                **base_metadata,
            }
            return _ResolvedCall(target_id, metadata)
        return None

    def _resolve_base_class(
        self,
        base: str,
        file_path: str,
    ) -> tuple[str, str, dict[str, Any]] | None:
        if not base:
            return None
        caller_file = _normalized_path(file_path)
        caller_module = self.module_by_file.get(caller_file, "")
        context = self._import_context(file_path)

        class_alias = context.class_aliases.get(base)
        if class_alias is not None:
            module_name, class_name = class_alias
            metadata = {
                "import_alias": base,
                **context.class_alias_metadata.get(base, {}),
            }
            return module_name, class_name, metadata

        module_match = _longest_prefix_match(base, context.module_aliases)
        if module_match is not None:
            alias, module_name, suffix = module_match
            if suffix and "." not in suffix:
                if (module_name, suffix) in self.classes_by_module_and_name:
                    metadata = {
                        "import_alias": alias,
                        **context.module_alias_metadata.get(alias, {}),
                    }
                    return module_name, suffix, metadata

        if "." not in base and (caller_module, base) in self.classes_by_module_and_name:
            return caller_module, base, {"class_module": caller_module}

        if "." in base:
            module_name, class_name = base.rsplit(".", 1)
            if (module_name, class_name) in self.classes_by_module_and_name:
                return module_name, class_name, {"class_module": module_name}
        return None

    def _import_context(self, file_path: str) -> _ImportContext:
        normalized = _normalized_path(file_path)
        if normalized in self.import_context_by_file:
            return self.import_context_by_file[normalized]
        function_aliases: dict[str, str] = {}
        module_aliases: dict[str, str] = {}
        object_aliases: dict[str, tuple[str, str]] = {}
        class_aliases: dict[str, tuple[str, str]] = {}
        function_alias_metadata: dict[str, dict[str, Any]] = {}
        module_alias_metadata: dict[str, dict[str, Any]] = {}
        object_alias_metadata: dict[str, dict[str, Any]] = {}
        class_alias_metadata: dict[str, dict[str, Any]] = {}
        explicit_star_exports_by_module: dict[str, set[str]] = {}
        for item in self.imports_by_file.get(normalized, []):
            if item.kind == "module_all":
                continue
            if item.kind == "symbol_alias":
                self._add_symbol_aliases(
                    item,
                    function_aliases=function_aliases,
                    module_aliases=module_aliases,
                    object_aliases=object_aliases,
                    class_aliases=class_aliases,
                    function_alias_metadata=function_alias_metadata,
                    module_alias_metadata=module_alias_metadata,
                    object_alias_metadata=object_alias_metadata,
                    class_alias_metadata=class_alias_metadata,
                )
                continue
            if item.module is None:
                relative_base = self._relative_import_base(item)
                if relative_base is not None:
                    if "*" in item.names:
                        self._record_explicit_star_exports(
                            relative_base,
                            explicit_star_exports_by_module,
                        )
                        self._add_star_import_aliases(
                            item,
                            module_name=relative_base,
                            function_aliases=function_aliases,
                            module_aliases=module_aliases,
                            object_aliases=object_aliases,
                            class_aliases=class_aliases,
                            function_alias_metadata=function_alias_metadata,
                            module_alias_metadata=module_alias_metadata,
                            object_alias_metadata=object_alias_metadata,
                            class_alias_metadata=class_alias_metadata,
                        )
                    for name in item.names:
                        if name == "*":
                            continue
                        alias = _bound_import_name(item, name)
                        module_name = _join_module(relative_base, name)
                        module_aliases[alias] = module_name
                        module_alias_metadata[alias] = _import_metadata(
                            item,
                            module_name=module_name,
                            import_name=name,
                        )
                        module_aliases.setdefault(name, module_name)
                        module_alias_metadata.setdefault(
                            name,
                            module_alias_metadata[alias],
                        )
                    continue
                if item.kind == "dynamic":
                    for alias, module_name in item.aliases.items():
                        module_aliases[alias] = module_name
                        module_alias_metadata[alias] = _import_metadata(
                            item,
                            module_name=module_name,
                        )
                    continue
                for name in item.names:
                    alias = _bound_import_name(item, name)
                    module_aliases[alias] = name
                    module_alias_metadata[alias] = _import_metadata(
                        item,
                        module_name=name,
                    )
                    module_aliases.setdefault(name, name)
                    module_alias_metadata.setdefault(
                        name,
                        module_alias_metadata[alias],
                    )
                continue
            module_name = self._absolute_import_module(item)
            if not module_name:
                continue
            for name in item.names:
                if name == "*":
                    self._record_explicit_star_exports(
                        module_name,
                        explicit_star_exports_by_module,
                    )
                    self._add_star_import_aliases(
                        item,
                        module_name=module_name,
                        function_aliases=function_aliases,
                        module_aliases=module_aliases,
                        object_aliases=object_aliases,
                        class_aliases=class_aliases,
                        function_alias_metadata=function_alias_metadata,
                        module_alias_metadata=module_alias_metadata,
                        object_alias_metadata=object_alias_metadata,
                        class_alias_metadata=class_alias_metadata,
                    )
                    continue
                alias = _bound_import_name(item, name)
                exports = self._module_exports(module_name)
                if self._add_exported_name_alias(
                    item,
                    exports=exports,
                    import_module=module_name,
                    import_name=name,
                    alias=alias,
                    function_aliases=function_aliases,
                    module_aliases=module_aliases,
                    object_aliases=object_aliases,
                    class_aliases=class_aliases,
                    function_alias_metadata=function_alias_metadata,
                    module_alias_metadata=module_alias_metadata,
                    object_alias_metadata=object_alias_metadata,
                    class_alias_metadata=class_alias_metadata,
                ):
                    continue
                submodule_name = _join_module(module_name, name)
                if self._module_exists(submodule_name):
                    module_aliases[alias] = submodule_name
                    module_alias_metadata[alias] = _import_metadata(
                        item,
                        module_name=submodule_name,
                        import_name=name,
                    )
                    continue
                object_aliases[alias] = (module_name, name)
                object_alias_metadata[alias] = _import_metadata(
                    item,
                    module_name=module_name,
                    import_name=name,
                )
        context = _ImportContext(
            function_aliases=function_aliases,
            module_aliases=module_aliases,
            object_aliases=object_aliases,
            class_aliases=class_aliases,
            explicit_star_exports_by_module=explicit_star_exports_by_module,
            function_alias_metadata=function_alias_metadata,
            module_alias_metadata=module_alias_metadata,
            object_alias_metadata=object_alias_metadata,
            class_alias_metadata=class_alias_metadata,
        )
        self.import_context_by_file[normalized] = context
        return context

    def _module_exists(self, module_name: str) -> bool:
        return module_name in self.known_modules

    def _record_explicit_star_exports(
        self,
        module_name: str,
        explicit_star_exports_by_module: dict[str, set[str]],
    ) -> None:
        exports = self.star_exports_by_module.get(module_name)
        if exports is not None:
            explicit_star_exports_by_module[module_name] = exports

    def _filter_explicit_star_blocked_matches(
        self,
        call: CallSite,
        matches: list[str],
    ) -> list[str]:
        if not matches or "." in call.callee:
            return matches
        context = self._import_context(call.file_path)
        if not context.explicit_star_exports_by_module:
            return matches
        filtered = []
        for target_id in matches:
            module_name = self.module_by_function_id.get(target_id)
            exports = context.explicit_star_exports_by_module.get(module_name or "")
            if exports is not None and call.callee not in exports:
                continue
            filtered.append(target_id)
        return filtered

    def _is_explicit_star_blocked_target(
        self,
        call: CallSite,
        target_id: str,
    ) -> bool:
        if "." in call.callee:
            return False
        context = self._import_context(call.file_path)
        if not context.explicit_star_exports_by_module:
            return False
        module_name = self.module_by_function_id.get(target_id)
        exports = context.explicit_star_exports_by_module.get(module_name or "")
        return exports is not None and call.callee not in exports

    def _module_exports(
        self,
        module_name: str,
        seen: set[str] | None = None,
    ) -> _ModuleExports:
        if module_name in self.module_exports_cache:
            return self.module_exports_cache[module_name]
        seen = set(seen or set())
        if module_name in seen:
            return _ModuleExports()
        seen.add(module_name)

        exports = _ModuleExports()
        for name, target_id in self.top_level_functions_by_module.get(
            module_name,
            {},
        ).items():
            exports.function_aliases[name] = target_id
            exports.object_aliases[name] = (module_name, name)
        for class_name in sorted(self.classes_by_module.get(module_name, set())):
            exports.class_aliases[class_name] = (module_name, class_name)
            exports.object_aliases[class_name] = (module_name, class_name)

        for item in self.imports_by_module.get(module_name, []):
            if item.kind in {"module_all", "dynamic", "dynamic_member"}:
                continue
            if item.kind == "symbol_alias":
                self._add_symbol_aliases(
                    item,
                    function_aliases=exports.function_aliases,
                    module_aliases=exports.module_aliases,
                    object_aliases=exports.object_aliases,
                    class_aliases=exports.class_aliases,
                    function_alias_metadata=exports.function_alias_metadata,
                    module_alias_metadata=exports.module_alias_metadata,
                    object_alias_metadata=exports.object_alias_metadata,
                    class_alias_metadata=exports.class_alias_metadata,
                )
                continue
            if item.module is None:
                relative_base = self._relative_import_base(item)
                for name in item.names:
                    if name == "*":
                        continue
                    alias = _bound_import_name(item, name)
                    target_module = _join_module(relative_base, name) if relative_base else name
                    metadata = _reexport_metadata(
                        target_module=target_module,
                        target_name="",
                    )
                    exports.module_aliases[alias] = target_module
                    exports.module_alias_metadata[alias] = metadata
                continue

            source_module = self._absolute_import_module(item)
            if not source_module:
                continue
            source_exports = self._module_exports(source_module, seen)
            for name in item.names:
                if name == "*":
                    for exported_name in self._exported_names_for_star(
                        source_module,
                        source_exports,
                    ):
                        self._copy_module_export(
                            exports,
                            source_exports,
                            import_name=exported_name,
                            alias=exported_name,
                            reexport_module=source_module,
                        )
                    continue
                alias = _bound_import_name(item, name)
                self._copy_module_export(
                    exports,
                    source_exports,
                    import_name=name,
                    alias=alias,
                    reexport_module=source_module,
                )

        self.module_exports_cache[module_name] = exports
        return exports

    def _copy_module_export(
        self,
        target_exports: _ModuleExports,
        source_exports: _ModuleExports,
        *,
        import_name: str,
        alias: str,
        reexport_module: str,
    ) -> bool:
        if import_name in source_exports.function_aliases:
            target_exports.function_aliases[alias] = source_exports.function_aliases[
                import_name
            ]
            target_exports.function_alias_metadata[alias] = _merge_reexport_metadata(
                source_exports.function_alias_metadata.get(import_name, {}),
                reexport_module=reexport_module,
                reexport_name=import_name,
            )
            if import_name in source_exports.object_aliases:
                target_exports.object_aliases[alias] = source_exports.object_aliases[
                    import_name
                ]
                target_exports.object_alias_metadata[alias] = dict(
                    target_exports.function_alias_metadata[alias]
                )
            return True
        if import_name in source_exports.class_aliases:
            target_exports.class_aliases[alias] = source_exports.class_aliases[
                import_name
            ]
            target_exports.class_alias_metadata[alias] = _merge_reexport_metadata(
                source_exports.class_alias_metadata.get(import_name, {}),
                reexport_module=reexport_module,
                reexport_name=import_name,
            )
            if import_name in source_exports.object_aliases:
                target_exports.object_aliases[alias] = source_exports.object_aliases[
                    import_name
                ]
                target_exports.object_alias_metadata[alias] = dict(
                    target_exports.class_alias_metadata[alias]
                )
            return True
        if import_name in source_exports.module_aliases:
            target_exports.module_aliases[alias] = source_exports.module_aliases[
                import_name
            ]
            target_exports.module_alias_metadata[alias] = _merge_reexport_metadata(
                source_exports.module_alias_metadata.get(import_name, {}),
                reexport_module=reexport_module,
                reexport_name=import_name,
            )
            return True
        return False

    def _exported_names_for_star(
        self,
        module_name: str,
        exports: _ModuleExports | None = None,
    ) -> list[str]:
        explicit_exports = self.star_exports_by_module.get(module_name)
        exports = exports or self._module_exports(module_name)
        names = {
            *exports.function_aliases,
            *exports.class_aliases,
            *exports.module_aliases,
        }
        if explicit_exports is not None:
            names = {name for name in names if name in explicit_exports}
        else:
            names = {name for name in names if not _is_private_export(name)}
        return sorted(names)

    def _add_exported_name_alias(
        self,
        item: ImportInfo,
        *,
        exports: _ModuleExports,
        import_module: str,
        import_name: str,
        alias: str,
        function_aliases: dict[str, str],
        module_aliases: dict[str, str],
        object_aliases: dict[str, tuple[str, str]],
        class_aliases: dict[str, tuple[str, str]],
        function_alias_metadata: dict[str, dict[str, Any]],
        module_alias_metadata: dict[str, dict[str, Any]],
        object_alias_metadata: dict[str, dict[str, Any]],
        class_alias_metadata: dict[str, dict[str, Any]],
        is_star_import: bool = False,
        star_import_uses_all: bool = False,
    ) -> bool:
        base_metadata = _import_metadata(
            item,
            module_name=import_module,
            import_name=import_name,
            is_star_import=is_star_import,
            star_import_uses_all=star_import_uses_all,
        )
        added = False
        if import_name in exports.function_aliases:
            function_aliases[alias] = exports.function_aliases[import_name]
            function_alias_metadata[alias] = {
                **base_metadata,
                **exports.function_alias_metadata.get(import_name, {}),
            }
            added = True
        if import_name in exports.class_aliases:
            class_aliases[alias] = exports.class_aliases[import_name]
            class_alias_metadata[alias] = {
                **base_metadata,
                **exports.class_alias_metadata.get(import_name, {}),
            }
            added = True
        if import_name in exports.module_aliases:
            module_aliases[alias] = exports.module_aliases[import_name]
            module_alias_metadata[alias] = {
                **base_metadata,
                **exports.module_alias_metadata.get(import_name, {}),
            }
            added = True
        if import_name in exports.object_aliases:
            object_aliases[alias] = exports.object_aliases[import_name]
            object_alias_metadata[alias] = {
                **base_metadata,
                **exports.object_alias_metadata.get(import_name, {}),
            }
            added = True
        return added

    def _add_symbol_aliases(
        self,
        item: ImportInfo,
        *,
        function_aliases: dict[str, str],
        module_aliases: dict[str, str],
        object_aliases: dict[str, tuple[str, str]],
        class_aliases: dict[str, tuple[str, str]],
        function_alias_metadata: dict[str, dict[str, Any]],
        module_alias_metadata: dict[str, dict[str, Any]],
        object_alias_metadata: dict[str, dict[str, Any]],
        class_alias_metadata: dict[str, dict[str, Any]],
    ) -> None:
        source_ref = item.names[0] if item.names else ""
        if not source_ref:
            return
        for alias, original in item.aliases.items():
            if original != source_ref or alias == source_ref:
                continue
            if self._copy_exact_symbol_alias(
                source_ref,
                alias=alias,
                function_aliases=function_aliases,
                module_aliases=module_aliases,
                object_aliases=object_aliases,
                class_aliases=class_aliases,
                function_alias_metadata=function_alias_metadata,
                module_alias_metadata=module_alias_metadata,
                object_alias_metadata=object_alias_metadata,
                class_alias_metadata=class_alias_metadata,
            ):
                continue
            self._copy_dotted_symbol_alias(
                source_ref,
                alias=alias,
                function_aliases=function_aliases,
                module_aliases=module_aliases,
                object_aliases=object_aliases,
                class_aliases=class_aliases,
                function_alias_metadata=function_alias_metadata,
                module_alias_metadata=module_alias_metadata,
                object_alias_metadata=object_alias_metadata,
                class_alias_metadata=class_alias_metadata,
            )

    def _copy_exact_symbol_alias(
        self,
        source_ref: str,
        *,
        alias: str,
        function_aliases: dict[str, str],
        module_aliases: dict[str, str],
        object_aliases: dict[str, tuple[str, str]],
        class_aliases: dict[str, tuple[str, str]],
        function_alias_metadata: dict[str, dict[str, Any]],
        module_alias_metadata: dict[str, dict[str, Any]],
        object_alias_metadata: dict[str, dict[str, Any]],
        class_alias_metadata: dict[str, dict[str, Any]],
    ) -> bool:
        added = False
        if source_ref in function_aliases:
            function_aliases[alias] = function_aliases[source_ref]
            function_alias_metadata[alias] = _symbol_alias_metadata(
                function_alias_metadata.get(source_ref, {}),
                source_ref=source_ref,
            )
            added = True
        if source_ref in module_aliases:
            module_aliases[alias] = module_aliases[source_ref]
            module_alias_metadata[alias] = _symbol_alias_metadata(
                module_alias_metadata.get(source_ref, {}),
                source_ref=source_ref,
            )
            added = True
        if source_ref in class_aliases:
            class_aliases[alias] = class_aliases[source_ref]
            class_alias_metadata[alias] = _symbol_alias_metadata(
                class_alias_metadata.get(source_ref, {}),
                source_ref=source_ref,
            )
            added = True
        if source_ref in object_aliases:
            object_aliases[alias] = object_aliases[source_ref]
            object_alias_metadata[alias] = _symbol_alias_metadata(
                object_alias_metadata.get(source_ref, {}),
                source_ref=source_ref,
            )
            added = True
        return added

    def _copy_dotted_symbol_alias(
        self,
        source_ref: str,
        *,
        alias: str,
        function_aliases: dict[str, str],
        module_aliases: dict[str, str],
        object_aliases: dict[str, tuple[str, str]],
        class_aliases: dict[str, tuple[str, str]],
        function_alias_metadata: dict[str, dict[str, Any]],
        module_alias_metadata: dict[str, dict[str, Any]],
        object_alias_metadata: dict[str, dict[str, Any]],
        class_alias_metadata: dict[str, dict[str, Any]],
    ) -> bool:
        module_match = _longest_prefix_match(source_ref, module_aliases)
        if module_match is not None:
            source_alias, module_name, member = module_match
            if member and self._add_member_symbol_alias(
                alias=alias,
                source_ref=source_ref,
                module_name=module_name,
                member=member,
                base_metadata=module_alias_metadata.get(source_alias, {}),
                function_aliases=function_aliases,
                object_aliases=object_aliases,
                class_aliases=class_aliases,
                function_alias_metadata=function_alias_metadata,
                object_alias_metadata=object_alias_metadata,
                class_alias_metadata=class_alias_metadata,
            ):
                return True

        object_match = _longest_prefix_match(source_ref, object_aliases)
        if object_match is not None:
            source_alias, imported, suffix = object_match
            module_name, imported_name = imported
            member = imported_name if not suffix else f"{imported_name}.{suffix}"
            if self._add_member_symbol_alias(
                alias=alias,
                source_ref=source_ref,
                module_name=module_name,
                member=member,
                base_metadata=object_alias_metadata.get(source_alias, {}),
                function_aliases=function_aliases,
                object_aliases=object_aliases,
                class_aliases=class_aliases,
                function_alias_metadata=function_alias_metadata,
                object_alias_metadata=object_alias_metadata,
                class_alias_metadata=class_alias_metadata,
            ):
                return True

        class_match = _longest_prefix_match(source_ref, class_aliases)
        if class_match is not None:
            source_alias, imported, suffix = class_match
            module_name, class_name = imported
            if suffix and self._add_member_symbol_alias(
                alias=alias,
                source_ref=source_ref,
                module_name=module_name,
                member=f"{class_name}.{suffix}",
                base_metadata=class_alias_metadata.get(source_alias, {}),
                function_aliases=function_aliases,
                object_aliases=object_aliases,
                class_aliases=class_aliases,
                function_alias_metadata=function_alias_metadata,
                object_alias_metadata=object_alias_metadata,
                class_alias_metadata=class_alias_metadata,
            ):
                return True
        return False

    def _add_member_symbol_alias(
        self,
        *,
        alias: str,
        source_ref: str,
        module_name: str,
        member: str,
        base_metadata: dict[str, Any],
        function_aliases: dict[str, str],
        object_aliases: dict[str, tuple[str, str]],
        class_aliases: dict[str, tuple[str, str]],
        function_alias_metadata: dict[str, dict[str, Any]],
        object_alias_metadata: dict[str, dict[str, Any]],
        class_alias_metadata: dict[str, dict[str, Any]],
    ) -> bool:
        class_ref = self._resolve_module_class_reference(module_name, member)
        if class_ref is not None:
            target_module, class_name = class_ref
            metadata = _symbol_alias_metadata(
                {
                    **base_metadata,
                    "import_module": target_module,
                    "import_name": class_name,
                },
                source_ref=source_ref,
            )
            class_aliases[alias] = (target_module, class_name)
            class_alias_metadata[alias] = metadata
            object_aliases[alias] = (target_module, class_name)
            object_alias_metadata[alias] = dict(metadata)
            return True

        member_ref = self._resolve_module_member_reference(module_name, member)
        if member_ref is not None:
            target_id, target_module, target_member = member_ref
            metadata = _symbol_alias_metadata(
                {
                    **base_metadata,
                    "import_module": target_module,
                    "import_name": target_member,
                },
                source_ref=source_ref,
            )
            function_aliases[alias] = target_id
            function_alias_metadata[alias] = metadata
            object_aliases[alias] = (target_module, target_member)
            object_alias_metadata[alias] = dict(metadata)
            return True

        return False

    def _add_star_import_aliases(
        self,
        item: ImportInfo,
        *,
        module_name: str,
        function_aliases: dict[str, str],
        module_aliases: dict[str, str],
        object_aliases: dict[str, tuple[str, str]],
        class_aliases: dict[str, tuple[str, str]],
        function_alias_metadata: dict[str, dict[str, Any]],
        module_alias_metadata: dict[str, dict[str, Any]],
        object_alias_metadata: dict[str, dict[str, Any]],
        class_alias_metadata: dict[str, dict[str, Any]],
    ) -> None:
        explicit_exports = self.star_exports_by_module.get(module_name)
        exports = self._module_exports(module_name)
        for name in sorted(
            {
                *exports.function_aliases,
                *exports.class_aliases,
                *exports.module_aliases,
            }
        ):
            if not _is_star_exported(name, explicit_exports):
                continue
            self._add_exported_name_alias(
                item,
                import_name=name,
                alias=name,
                exports=exports,
                import_module=module_name,
                function_aliases=function_aliases,
                module_aliases=module_aliases,
                object_aliases=object_aliases,
                class_aliases=class_aliases,
                function_alias_metadata=function_alias_metadata,
                module_alias_metadata=module_alias_metadata,
                object_alias_metadata=object_alias_metadata,
                class_alias_metadata=class_alias_metadata,
                is_star_import=True,
                star_import_uses_all=explicit_exports is not None,
            )

    def _absolute_import_module(self, item: ImportInfo) -> str | None:
        if item.level <= 0:
            return item.module
        base = self._relative_import_base(item)
        if base is None:
            return item.module
        return _join_module(base, item.module or "")

    def _relative_import_base(self, item: ImportInfo) -> str | None:
        if item.level <= 0:
            return None
        current_module = self.module_by_file.get(_normalized_path(item.file_path), "")
        if not current_module:
            return None
        if Path(item.file_path).name == "__init__.py":
            package_parts = current_module.split(".")
        else:
            package_parts = current_module.split(".")[:-1]
        trim_count = item.level - 1
        if trim_count:
            package_parts = package_parts[:-trim_count]
        return ".".join(part for part in package_parts if part)

    def _resolve_module_member(self, module_name: str, member: str) -> str | None:
        member_ref = self._resolve_module_member_reference(module_name, member)
        if member_ref is None:
            return None
        target_id, _, _ = member_ref
        return target_id

    def _resolve_module_member_reference(
        self,
        module_name: str,
        member: str,
    ) -> tuple[str, str, str] | None:
        if not member:
            return None
        for candidate_module, candidate_member in _module_member_candidates(
            module_name,
            member,
        ):
            direct = self.by_module_and_qualified.get(
                (candidate_module, candidate_member)
            )
            if direct is not None:
                return direct, candidate_module, candidate_member
            simple_matches = self.by_module_and_simple.get(
                (candidate_module, candidate_member),
                [],
            )
            if len(simple_matches) == 1:
                return simple_matches[0], candidate_module, candidate_member
        return None

    def _resolve_module_class_reference(
        self,
        module_name: str,
        member: str,
    ) -> tuple[str, str] | None:
        if not member:
            return None
        for candidate_module, candidate_member in _module_member_candidates(
            module_name,
            member,
        ):
            if "." in candidate_member:
                continue
            if (candidate_module, candidate_member) in self.classes_by_module_and_name:
                return candidate_module, candidate_member
        return None


def _caller_class_name(caller_name: str) -> str | None:
    parts = caller_name.split(".")
    if len(parts) >= 2:
        return parts[-2]
    return None


def _has_decorator(decorators: set[str], name: str) -> bool:
    return name in decorators or any(
        decorator.endswith(f".{name}") for decorator in decorators
    )


def _normalized_path(file_path: str | Path) -> str:
    return Path(file_path).resolve().as_posix()


def _common_root(file_paths: list[Path]) -> Path:
    if not file_paths:
        return Path(".").resolve()
    resolved = [path.resolve() for path in file_paths]
    if len(resolved) == 1:
        return resolved[0].parent
    common = Path(*Path(resolved[0]).parts[:1])
    for index in range(1, min(len(path.parts) for path in resolved) + 1):
        candidate = Path(*resolved[0].parts[:index])
        if all(path.parts[:index] == candidate.parts for path in resolved):
            common = candidate
    return common


def _module_name(file_path: str, repo_root: Path) -> str:
    path = Path(file_path).resolve()
    try:
        relative = path.relative_to(repo_root)
    except ValueError:
        relative = Path(path.name)
    if str(relative) in {"", "."}:
        relative = Path(path.name)
    if relative.name == "__init__.py":
        parts = relative.parent.parts
    else:
        parts = relative.with_suffix("").parts
    return ".".join(part for part in parts if part)


def _import_metadata(
    item: ImportInfo,
    *,
    module_name: str,
    import_name: str = "",
    is_star_import: bool = False,
    star_import_uses_all: bool = False,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "import_module": module_name,
        "import_level": item.level,
        "import_kind": item.kind,
        "is_relative_import": item.level > 0,
    }
    if import_name:
        metadata["import_name"] = import_name
    if is_star_import:
        metadata["is_star_import"] = True
    if star_import_uses_all:
        metadata["star_import_uses_all"] = True
    return metadata


def _symbol_alias_metadata(
    metadata: dict[str, Any],
    *,
    source_ref: str,
) -> dict[str, Any]:
    return {
        **metadata,
        "is_symbol_alias": True,
        "symbol_alias_scope": "module",
        "symbol_alias_source": source_ref,
    }


def _local_symbol_alias_metadata(
    metadata: dict[str, Any],
    *,
    call: CallSite,
) -> dict[str, Any]:
    return {
        **metadata,
        "import_alias": call.local_symbol_alias or call.callee,
        "resolution": "local_symbol_alias",
        "is_symbol_alias": True,
        "symbol_alias_scope": "local",
        "symbol_alias_source": call.local_symbol_alias_source,
    }


def _reexport_metadata(
    *,
    target_module: str,
    target_name: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "is_reexport": True,
        "reexport_module": target_module,
    }
    if target_name:
        metadata["reexport_name"] = target_name
    return metadata


def _merge_reexport_metadata(
    metadata: dict[str, Any],
    *,
    reexport_module: str,
    reexport_name: str,
) -> dict[str, Any]:
    return {
        **metadata,
        "is_reexport": True,
        "reexport_module": reexport_module,
        "reexport_name": reexport_name,
    }


def _is_star_exported(name: str, explicit_exports: set[str] | None) -> bool:
    if explicit_exports is not None:
        return name in explicit_exports
    return not _is_private_export(name)


def _is_private_export(name: str) -> bool:
    return name.startswith("_")


def _join_module(prefix: str, suffix: str) -> str:
    parts = [part for part in [prefix, suffix] if part]
    return ".".join(parts)


def _bound_import_name(item: ImportInfo, name: str) -> str:
    for alias, original in item.aliases.items():
        if original == name:
            return alias
    if item.module is None:
        return name.split(".")[0]
    return name


def _longest_prefix_match(callee: str, aliases: dict):
    for alias in sorted(aliases, key=len, reverse=True):
        if callee == alias:
            return alias, aliases[alias], ""
        prefix = f"{alias}."
        if callee.startswith(prefix):
            return alias, aliases[alias], callee[len(prefix) :]
    return None


def _module_member_candidates(module_name: str, member: str) -> list[tuple[str, str]]:
    parts = member.split(".")
    candidates = [(module_name, member)]
    for index in range(1, len(parts)):
        candidate_module = ".".join([module_name, *parts[:index]])
        candidate_member = ".".join(parts[index:])
        candidates.append((candidate_module, candidate_member))
    return candidates


def build_call_graph(
    functions: list[CodeEntity],
    calls: list[CallSite],
    imports: list[ImportInfo] | None = None,
) -> CallGraph:
    return CallGraphBuilder().build(
        functions=functions,
        calls=calls,
        imports=imports,
    )
