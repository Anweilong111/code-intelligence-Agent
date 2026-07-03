from __future__ import annotations

import ast
from pathlib import Path

from code_intelligence_agent.core.models import (
    CallSite,
    CodeEntity,
    FileAnalysis,
    ImportInfo,
)


class ASTAnalyzer:
    """Extract code entities and call sites from Python source."""

    def analyze_file(self, file_path: str | Path, source: str) -> FileAnalysis:
        path = str(file_path)
        tree = ast.parse(source, filename=path)
        lines = source.splitlines()
        visitor = _EntityVisitor(path, lines)
        visitor.visit(tree)
        chunks = visitor.classes + visitor.functions
        return FileAnalysis(
            file_path=path,
            source=source,
            functions=visitor.functions,
            classes=visitor.classes,
            imports=visitor.imports,
            calls=visitor.calls,
            chunks=chunks,
        )


class _EntityVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, lines: list[str]) -> None:
        self.file_path = file_path
        self.lines = lines
        self.is_test_file = _is_test_file(file_path)
        self.functions: list[CodeEntity] = []
        self.classes: list[CodeEntity] = []
        self.imports: list[ImportInfo] = []
        self.calls: list[CallSite] = []
        self._class_stack: list[str] = []
        self._class_base_stack: list[list[str]] = []
        self._function_stack: list[str] = []
        self._constant_scopes: list[dict[str, str]] = [{}]
        self._dynamic_import_aliases_seen: set[tuple[int, str, str]] = set()

    def visit_Import(self, node: ast.Import) -> None:
        names = [alias.name for alias in node.names]
        self.imports.append(
            ImportInfo(
                module=None,
                names=names,
                level=0,
                file_path=self.file_path,
                line=node.lineno,
                aliases={
                    alias.asname: alias.name
                    for alias in node.names
                    if alias.asname is not None
                },
            )
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = [alias.name for alias in node.names]
        self.imports.append(
            ImportInfo(
                module=node.module,
                names=names,
                level=node.level,
                file_path=self.file_path,
                line=node.lineno,
                aliases={
                    alias.asname: alias.name
                    for alias in node.names
                    if alias.asname is not None
                },
            )
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        assigned_to = sorted(_assigned_names(node.targets))
        self._record_module_all_assignment(node.value, node.targets, node.lineno)
        self._record_dynamic_import_member_assignment(node.value, assigned_to)
        self._record_dynamic_import(node.value, sorted(_assigned_names(node.targets)))
        self._record_symbol_alias_assignment(node.value, node.targets, node.lineno)
        self._record_string_assignments(node.value, node.targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        assigned_to = sorted(_assigned_names([node.target]))
        if node.value is not None:
            self._record_module_all_assignment(
                node.value,
                [node.target],
                node.lineno,
            )
            self._record_dynamic_import_member_assignment(node.value, assigned_to)
            self._record_dynamic_import(node.value, assigned_to)
            self._record_symbol_alias_assignment(node.value, [node.target], node.lineno)
            self._record_string_assignments(node.value, [node.target])
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        self._record_dynamic_import(node.value, [])
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._record_dynamic_import_member_alias(node)
        self.generic_visit(node)

    def _record_dynamic_import(self, node: ast.AST, assigned_to: list[str]) -> None:
        if not isinstance(node, ast.Call):
            return
        module_name = _dynamic_import_module_name(
            node,
            importlib_aliases=self._importlib_module_aliases(),
            import_module_aliases=self._import_module_function_aliases(),
            constants=self._lookup_constant,
        )
        if module_name is None:
            return
        self.imports.append(
            ImportInfo(
                module=None,
                names=[module_name],
                level=0,
                file_path=self.file_path,
                line=node.lineno,
                aliases={alias: module_name for alias in assigned_to},
                kind="dynamic",
            )
        )

    def _record_dynamic_import_member_alias(self, node: ast.Call) -> None:
        alias, module_name = _dynamic_import_member_alias(
            node,
            importlib_aliases=self._importlib_module_aliases(),
            import_module_aliases=self._import_module_function_aliases(),
            constants=self._lookup_constant,
        )
        if not alias or module_name is None:
            return
        key = (node.lineno, alias, module_name)
        if key in self._dynamic_import_aliases_seen:
            return
        self._dynamic_import_aliases_seen.add(key)
        self.imports.append(
            ImportInfo(
                module=None,
                names=[module_name],
                level=0,
                file_path=self.file_path,
                line=node.lineno,
                aliases={alias: module_name},
                kind="dynamic",
            )
        )

    def _record_dynamic_import_member_assignment(
        self,
        node: ast.AST,
        assigned_to: list[str],
    ) -> None:
        if not assigned_to:
            return
        member = _dynamic_import_member_reference(
            node,
            importlib_aliases=self._importlib_module_aliases(),
            import_module_aliases=self._import_module_function_aliases(),
            module_aliases=self._dynamic_module_aliases(),
            constants=self._lookup_constant,
        )
        if member is None:
            return
        module_name, member_name = member
        self.imports.append(
            ImportInfo(
                module=module_name,
                names=[member_name],
                level=0,
                file_path=self.file_path,
                line=getattr(node, "lineno", 0),
                aliases={alias: member_name for alias in assigned_to},
                kind="dynamic_member",
            )
        )

    def _record_symbol_alias_assignment(
        self,
        node: ast.AST,
        targets: list[ast.AST],
        line: int,
    ) -> None:
        if self._class_stack or self._function_stack:
            return
        if not isinstance(node, (ast.Name, ast.Attribute)):
            return
        source_ref = _callable_name(node)
        if not source_ref or source_ref.startswith("__"):
            return
        assigned_to = sorted(_symbol_alias_targets(targets))
        aliases = {
            alias: source_ref
            for alias in assigned_to
            if alias != source_ref and not alias.startswith("__")
        }
        if not aliases:
            return
        self.imports.append(
            ImportInfo(
                module=None,
                names=[source_ref],
                level=0,
                file_path=self.file_path,
                line=line,
                aliases=aliases,
                kind="symbol_alias",
            )
        )

    def _record_module_all_assignment(
        self,
        value: ast.AST,
        targets: list[ast.AST],
        line: int,
    ) -> None:
        if self._class_stack or self._function_stack:
            return
        if "__all__" not in _assigned_names(targets):
            return
        exports = _static_string_sequence(value, self._lookup_constant)
        if exports is None:
            return
        self.imports.append(
            ImportInfo(
                module=None,
                names=exports,
                level=0,
                file_path=self.file_path,
                line=line,
                kind="module_all",
            )
        )

    def _record_string_assignments(
        self,
        value: ast.AST,
        targets: list[ast.AST],
    ) -> None:
        constant = _static_string_value(value, self._lookup_constant)
        current_scope = self._constant_scopes[-1]
        for name in _assigned_names(targets):
            if constant is None:
                current_scope.pop(name, None)
            else:
                current_scope[name] = constant

    def _lookup_constant(self, name: str) -> str | None:
        for scope in reversed(self._constant_scopes):
            if name in scope:
                return scope[name]
        return None

    def _importlib_module_aliases(self) -> set[str]:
        aliases = {"importlib"}
        for item in self.imports:
            if item.module is not None:
                continue
            for name in item.names:
                if name == "importlib":
                    aliases.add(_bound_static_import_name(item, name))
        return aliases

    def _import_module_function_aliases(self) -> set[str]:
        aliases: set[str] = set()
        for item in self.imports:
            if item.module != "importlib":
                continue
            for name in item.names:
                if name == "import_module":
                    aliases.add(_bound_static_import_name(item, name))
        return aliases

    def _dynamic_module_aliases(self) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for item in self.imports:
            if item.module is not None or item.kind != "dynamic":
                continue
            for alias, module_name in item.aliases.items():
                aliases[alias] = module_name
        return aliases

    def _asyncio_module_aliases(self) -> set[str]:
        aliases = {"asyncio"}
        for item in self.imports:
            if item.module is not None:
                continue
            for name in item.names:
                if name == "asyncio":
                    aliases.add(_bound_static_import_name(item, name))
        return aliases

    def _asyncio_function_aliases(self, names: set[str]) -> set[str]:
        aliases: set[str] = set()
        for item in self.imports:
            if item.module != "asyncio":
                continue
            for name in item.names:
                if name in names:
                    aliases.add(_bound_static_import_name(item, name))
        return aliases

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified_name = ".".join(self._class_stack + [node.name])
        bases = [_callable_name(base) for base in node.bases]
        entity = self._make_entity(
            node=node,
            entity_type="class",
            name=node.name,
            qualified_name=qualified_name,
            metadata={"bases": bases},
        )
        self.classes.append(entity)
        self._class_stack.append(node.name)
        self._class_base_stack.append(bases)
        self._constant_scopes.append({})
        self.generic_visit(node)
        self._constant_scopes.pop()
        self._class_base_stack.pop()
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_async=True)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool
    ) -> None:
        qualified_name = ".".join(
            self._class_stack + self._function_stack + [node.name]
        )
        function_id = _entity_id(self.file_path, qualified_name)
        is_method = bool(self._class_stack)
        is_test = node.name.startswith("test_") or any(
            class_name.startswith("Test") for class_name in self._class_stack
        )
        entity = self._make_entity(
            node=node,
            entity_type="function",
            name=node.name,
            qualified_name=qualified_name,
            metadata={
                "qualified_name": qualified_name,
                "is_async": is_async,
                "is_method": is_method,
                "decorators": [
                    _callable_name(decorator)
                    for decorator in node.decorator_list
                    if _callable_name(decorator)
                ],
                "class_name": self._class_stack[-1] if self._class_stack else None,
                "class_bases": self._class_base_stack[-1]
                if self._class_base_stack
                else [],
                "is_test": is_test,
                "is_test_file": self.is_test_file,
                "args": [arg.arg for arg in node.args.args],
            },
        )
        self.functions.append(entity)
        collector = _CallCollector(
            caller_id=function_id,
            caller_name=qualified_name,
            file_path=self.file_path,
            constants=self._visible_constants(),
            asyncio_module_aliases=self._asyncio_module_aliases(),
            asyncio_task_function_aliases=self._asyncio_function_aliases(
                {"create_task", "ensure_future"}
            ),
            asyncio_gather_function_aliases=self._asyncio_function_aliases({"gather"}),
            asyncio_loop_function_aliases=self._asyncio_function_aliases(
                {"get_event_loop", "get_running_loop"}
            ),
            asyncio_task_group_aliases=self._asyncio_function_aliases({"TaskGroup"}),
        )
        collector.collect(node)
        self.calls.extend(collector.calls)

        self._function_stack.append(node.name)
        self._constant_scopes.append({})
        self.generic_visit(node)
        self._constant_scopes.pop()
        self._function_stack.pop()

    def _visible_constants(self) -> dict[str, str]:
        constants: dict[str, str] = {}
        for scope in self._constant_scopes:
            constants.update(scope)
        return constants

    def _make_entity(
        self,
        node: ast.AST,
        entity_type: str,
        name: str,
        qualified_name: str,
        metadata: dict,
    ) -> CodeEntity:
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)
        source = "\n".join(self.lines[start_line - 1 : end_line])
        metadata = {**metadata, "qualified_name": qualified_name}
        return CodeEntity(
            id=_entity_id(self.file_path, qualified_name),
            type=entity_type,
            name=name,
            file_path=self.file_path,
            start_line=start_line,
            end_line=end_line,
            source=source,
            metadata=metadata,
        )


class _CallCollector(ast.NodeVisitor):
    def __init__(
        self,
        caller_id: str,
        caller_name: str,
        file_path: str,
        *,
        constants: dict[str, str] | None = None,
        asyncio_module_aliases: set[str],
        asyncio_task_function_aliases: set[str],
        asyncio_gather_function_aliases: set[str],
        asyncio_loop_function_aliases: set[str],
        asyncio_task_group_aliases: set[str],
    ) -> None:
        self.caller_id = caller_id
        self.caller_name = caller_name
        self.file_path = file_path
        self.asyncio_module_aliases = asyncio_module_aliases
        self.asyncio_task_function_aliases = asyncio_task_function_aliases
        self.asyncio_gather_function_aliases = asyncio_gather_function_aliases
        self.asyncio_loop_function_aliases = asyncio_loop_function_aliases
        self.asyncio_task_group_aliases = asyncio_task_group_aliases
        self.async_task_scheduler_aliases: set[str] = set()
        self._constants: dict[str, str] = dict(constants or {})
        self._local_symbol_aliases: dict[str, str] = {}
        self.calls: list[CallSite] = []

    def collect(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for stmt in node.body:
            self.visit(stmt)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        self._update_async_scheduler_aliases(node.value, node.targets)
        self._record_string_assignments(node.value, node.targets)
        awaited_call = _awaited_call(node.value)
        if awaited_call is not None:
            self._record_call(
                awaited_call,
                assigned_to=sorted(_assigned_call_targets(node.targets)),
                is_awaited=True,
                async_kind="await",
            )
            self._visit_call_arguments_and_targets(
                awaited_call,
                node.targets,
                async_kind=self._async_scheduling_kind(awaited_call),
            )
            self._update_local_symbol_aliases(node.value, node.targets)
            return
        if isinstance(node.value, ast.Call):
            self._record_call(
                node.value,
                assigned_to=sorted(_assigned_call_targets(node.targets)),
            )
            self._visit_call_arguments_and_targets(
                node.value,
                node.targets,
                async_kind=self._async_scheduling_kind(node.value),
            )
            self._update_local_symbol_aliases(node.value, node.targets)
            return
        self.generic_visit(node)
        self._update_local_symbol_aliases(node.value, node.targets)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._update_async_scheduler_aliases(node.value, [node.target])
            self._record_string_assignments(node.value, [node.target])
        awaited_call = _awaited_call(node.value)
        if awaited_call is not None:
            self._record_call(
                awaited_call,
                assigned_to=sorted(_assigned_call_targets([node.target])),
                is_awaited=True,
                async_kind="await",
            )
            self._visit_call_arguments_and_targets(
                awaited_call,
                [node.target],
                async_kind=self._async_scheduling_kind(awaited_call),
            )
            self._update_local_symbol_aliases(node.value, [node.target])
            return
        if isinstance(node.value, ast.Call):
            self._record_call(
                node.value,
                assigned_to=sorted(_assigned_call_targets([node.target])),
            )
            self._visit_call_arguments_and_targets(
                node.value,
                [node.target],
                async_kind=self._async_scheduling_kind(node.value),
            )
            self._update_local_symbol_aliases(node.value, [node.target])
            return
        self.generic_visit(node)
        if node.value is not None:
            self._update_local_symbol_aliases(node.value, [node.target])

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.generic_visit(node)
        self._drop_local_symbol_aliases([node.target])

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        before = self._call_collector_state()

        self._restore_call_collector_state(before)
        for statement in node.body:
            self.visit(statement)
        body_state = self._call_collector_state()

        self._restore_call_collector_state(before)
        for statement in node.orelse:
            self.visit(statement)
        else_state = self._call_collector_state()

        possible_states = [body_state, else_state] if node.orelse else [before, body_state]
        self._restore_call_collector_state(
            _merge_call_collector_states(possible_states)
        )

    def visit_Try(self, node: ast.Try) -> None:
        before = self._call_collector_state()

        self._restore_call_collector_state(before)
        for statement in node.body:
            self.visit(statement)
        normal_state = self._call_collector_state()

        if node.orelse:
            self._restore_call_collector_state(normal_state)
            for statement in node.orelse:
                self.visit(statement)
            normal_state = self._call_collector_state()

        possible_states = [normal_state]
        for handler in node.handlers:
            self._restore_call_collector_state(before)
            if handler.type is not None:
                self.visit(handler.type)
            if handler.name:
                self._drop_local_state_names({handler.name})
            for statement in handler.body:
                self.visit(statement)
            if handler.name:
                self._drop_local_state_names({handler.name})
            possible_states.append(self._call_collector_state())

        merged_state = _merge_call_collector_states(possible_states)
        self._restore_call_collector_state(merged_state)
        for statement in node.finalbody:
            self.visit(statement)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(
            test_or_iter=node.iter,
            targets=[node.target],
            body=node.body,
            orelse=node.orelse,
        )

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(
            test_or_iter=node.iter,
            targets=[node.target],
            body=node.body,
            orelse=node.orelse,
        )

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(
            test_or_iter=node.test,
            targets=[],
            body=node.body,
            orelse=node.orelse,
        )

    def visit_Await(self, node: ast.Await) -> None:
        awaited_call = _awaited_call(node)
        if awaited_call is None:
            self.generic_visit(node)
            return
        self._record_call(awaited_call, is_awaited=True, async_kind="await")
        self._visit_call_arguments_and_targets(
            awaited_call,
            [],
            async_kind=self._async_scheduling_kind(awaited_call),
        )

    def visit_With(self, node: ast.With) -> None:
        scheduler_aliases = self._visit_with_items(node.items, is_async=False)
        self._visit_with_body(node.body, scheduler_aliases)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        scheduler_aliases = self._visit_with_items(node.items, is_async=True)
        self._visit_with_body(node.body, scheduler_aliases)

    def _visit_with_body(
        self,
        body: list[ast.stmt],
        scheduler_aliases: set[str],
    ) -> None:
        original_aliases = set(self.async_task_scheduler_aliases)
        self.async_task_scheduler_aliases.update(scheduler_aliases)
        try:
            for statement in body:
                self.visit(statement)
        finally:
            context_only_aliases = scheduler_aliases - original_aliases
            self.async_task_scheduler_aliases.difference_update(context_only_aliases)

    def visit_Call(self, node: ast.Call) -> None:
        self._record_call(node)
        self._visit_call_arguments_and_targets(
            node,
            [],
            async_kind=self._async_scheduling_kind(node),
        )

    def _record_call(
        self,
        node: ast.Call,
        assigned_to: list[str] | None = None,
        is_awaited: bool = False,
        async_kind: str = "",
    ) -> None:
        callee = _dynamic_getattr_callable_name(
            node.func,
            self._lookup_constant,
        ) or _callable_name(node.func)
        if callee:
            symbol_alias = self._local_symbol_alias_for_callee(callee)
            self.calls.append(
                CallSite(
                    caller_id=self.caller_id,
                    caller_name=self.caller_name,
                    callee=callee,
                    file_path=self.file_path,
                    line=node.lineno,
                    col=node.col_offset,
                    arg_names=[sorted(_loaded_names(argument)) for argument in node.args],
                    assigned_to=assigned_to or [],
                    is_awaited=is_awaited,
                    async_kind=async_kind,
                    local_symbol_alias=symbol_alias[0] if symbol_alias else "",
                    local_symbol_alias_source=symbol_alias[1] if symbol_alias else "",
                    local_symbol_alias_callee=symbol_alias[2] if symbol_alias else "",
                )
            )

    def _visit_call_arguments_and_targets(
        self,
        call: ast.Call,
        targets: list[ast.AST],
        *,
        async_kind: str = "",
    ) -> None:
        self._visit_call_function_expression(call.func)
        for argument in _call_arguments(call):
            if async_kind and isinstance(argument, ast.Call):
                self._record_call(argument, async_kind=async_kind)
                self._visit_call_arguments_and_targets(argument, [])
                continue
            self.visit(argument)
        for target in targets:
            self.visit(target)

    def _record_string_assignments(
        self,
        value: ast.AST,
        targets: list[ast.AST],
    ) -> None:
        constant = _static_string_value(value, self._lookup_constant)
        for name in _assigned_names(targets):
            if constant is None:
                self._constants.pop(name, None)
            else:
                self._constants[name] = constant

    def _lookup_constant(self, name: str) -> str | None:
        return self._constants.get(name)

    def _call_collector_state(self) -> dict[str, object]:
        return {
            "constants": dict(self._constants),
            "local_symbol_aliases": dict(self._local_symbol_aliases),
            "async_task_scheduler_aliases": set(self.async_task_scheduler_aliases),
        }

    def _restore_call_collector_state(self, state: dict[str, object]) -> None:
        self._constants = dict(state.get("constants", {}))
        self._local_symbol_aliases = dict(state.get("local_symbol_aliases", {}))
        self.async_task_scheduler_aliases = set(
            state.get("async_task_scheduler_aliases", set())
        )

    def _visit_loop(
        self,
        *,
        test_or_iter: ast.AST,
        targets: list[ast.AST],
        body: list[ast.stmt],
        orelse: list[ast.stmt],
    ) -> None:
        self.visit(test_or_iter)
        before = self._call_collector_state()

        self._restore_call_collector_state(before)
        self._drop_local_state_names(_assigned_names(targets))
        for statement in body:
            self.visit(statement)
        body_state = self._call_collector_state()

        loop_exit_state = _merge_call_collector_states([before, body_state])
        if not orelse:
            self._restore_call_collector_state(loop_exit_state)
            return

        self._restore_call_collector_state(loop_exit_state)
        for statement in orelse:
            self.visit(statement)
        else_state = self._call_collector_state()
        self._restore_call_collector_state(
            _merge_call_collector_states([body_state, else_state])
        )

    def _update_local_symbol_aliases(
        self,
        value: ast.AST | None,
        targets: list[ast.AST],
    ) -> None:
        target_names = sorted(_symbol_alias_targets(targets))
        if not target_names:
            return
        if not isinstance(value, (ast.Name, ast.Attribute)):
            for name in target_names:
                self._local_symbol_aliases.pop(name, None)
            return
        source_ref = _callable_name(value)
        if not source_ref or source_ref.startswith("__"):
            for name in target_names:
                self._local_symbol_aliases.pop(name, None)
            return
        source_ref = self._expanded_local_symbol_alias_callee(source_ref)
        for name in target_names:
            if name == source_ref or name.startswith("__"):
                self._local_symbol_aliases.pop(name, None)
                continue
            self._local_symbol_aliases[name] = source_ref

    def _drop_local_symbol_aliases(self, targets: list[ast.AST]) -> None:
        for name in _assigned_names(targets):
            self._local_symbol_aliases.pop(name, None)

    def _drop_local_state_names(self, names: set[str]) -> None:
        for name in names:
            self._local_symbol_aliases.pop(name, None)
            self._constants.pop(name, None)
            self.async_task_scheduler_aliases.discard(name)

    def _local_symbol_alias_for_callee(
        self,
        callee: str,
    ) -> tuple[str, str, str] | None:
        for alias in sorted(self._local_symbol_aliases, key=len, reverse=True):
            source_ref = self._local_symbol_aliases[alias]
            if callee == alias:
                return alias, source_ref, source_ref
            prefix = f"{alias}."
            if callee.startswith(prefix):
                suffix = callee[len(alias) :]
                return alias, source_ref, f"{source_ref}{suffix}"
        return None

    def _expanded_local_symbol_alias_callee(self, callee: str) -> str:
        symbol_alias = self._local_symbol_alias_for_callee(callee)
        if symbol_alias is None:
            return callee
        return symbol_alias[2]

    def _visit_call_function_expression(self, node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            self.visit(node)
            return
        if isinstance(node, ast.Attribute):
            self._visit_call_function_expression(node.value)
            return
        if isinstance(node, ast.Subscript):
            self._visit_call_function_expression(node.value)

    def _update_async_scheduler_aliases(
        self,
        value: ast.AST,
        targets: list[ast.AST],
    ) -> None:
        assigned_targets = _assigned_call_targets(targets)
        if not assigned_targets:
            return
        is_scheduler = (
            isinstance(value, ast.Call)
            and self._async_scheduler_factory_kind(value) != ""
        )
        for target in assigned_targets:
            if is_scheduler:
                self.async_task_scheduler_aliases.add(target)
            else:
                self.async_task_scheduler_aliases.discard(target)

    def _async_scheduler_factory_kind(self, call: ast.Call) -> str:
        callee = _callable_name(call.func)
        if callee in self.asyncio_loop_function_aliases:
            return "loop"
        if callee in self.asyncio_task_group_aliases:
            return "task_group"
        if "." not in callee:
            return ""
        receiver, method_name = callee.rsplit(".", 1)
        if receiver not in self.asyncio_module_aliases:
            return ""
        if method_name in {"get_event_loop", "get_running_loop"}:
            return "loop"
        if method_name == "TaskGroup":
            return "task_group"
        return ""

    def _async_scheduling_kind(self, call: ast.Call) -> str:
        callee = _callable_name(call.func)
        if callee in self.asyncio_task_function_aliases:
            return "task"
        if callee in self.asyncio_gather_function_aliases:
            return "gather"
        if "." not in callee:
            return ""
        receiver, method_name = callee.rsplit(".", 1)
        if (
            receiver in self.async_task_scheduler_aliases
            and method_name == "create_task"
        ):
            return "task"
        if receiver not in self.asyncio_module_aliases:
            return ""
        if method_name in {"create_task", "ensure_future"}:
            return "task"
        if method_name == "gather":
            return "gather"
        return ""

    def _visit_with_items(
        self,
        items: list[ast.withitem],
        *,
        is_async: bool,
    ) -> set[str]:
        scheduler_aliases: set[str] = set()
        for item in items:
            targets = [item.optional_vars] if item.optional_vars is not None else []
            context_expr = item.context_expr
            awaited_call = _awaited_call(context_expr)
            if awaited_call is not None:
                self._record_call(
                    awaited_call,
                    assigned_to=sorted(_assigned_call_targets(targets)),
                    is_awaited=True,
                    async_kind="await",
                )
                self._visit_call_arguments_and_targets(
                    awaited_call,
                    targets,
                    async_kind=self._async_scheduling_kind(awaited_call),
                )
                continue
            if isinstance(context_expr, ast.Call):
                if self._async_scheduler_factory_kind(context_expr):
                    scheduler_aliases.update(_assigned_call_targets(targets))
                self._record_call(
                    context_expr,
                    assigned_to=sorted(_assigned_call_targets(targets)),
                    is_awaited=is_async,
                    async_kind="await" if is_async else "",
                )
                self._visit_call_arguments_and_targets(
                    context_expr,
                    targets,
                    async_kind=self._async_scheduling_kind(context_expr),
                )
                continue
            self.visit(context_expr)
            for target in targets:
                self.visit(target)
        return scheduler_aliases


def _merge_call_collector_states(
    states: list[dict[str, object]],
) -> dict[str, object]:
    if not states:
        return {
            "constants": {},
            "local_symbol_aliases": {},
            "async_task_scheduler_aliases": set(),
        }
    return {
        "constants": _common_mapping_entries(
            [state.get("constants", {}) for state in states]
        ),
        "local_symbol_aliases": _common_mapping_entries(
            [state.get("local_symbol_aliases", {}) for state in states]
        ),
        "async_task_scheduler_aliases": _common_set_entries(
            [state.get("async_task_scheduler_aliases", set()) for state in states]
        ),
    }


def _common_mapping_entries(maps: list[object]) -> dict[str, str]:
    normalized = [dict(mapping) for mapping in maps]
    if not normalized:
        return {}
    common_keys = set(normalized[0])
    for mapping in normalized[1:]:
        common_keys.intersection_update(mapping)
    return {
        key: normalized[0][key]
        for key in common_keys
        if all(mapping[key] == normalized[0][key] for mapping in normalized[1:])
    }


def _common_set_entries(sets: list[object]) -> set[str]:
    normalized = [set(items) for items in sets]
    if not normalized:
        return set()
    common = set(normalized[0])
    for items in normalized[1:]:
        common.intersection_update(items)
    return common


def _entity_id(file_path: str, qualified_name: str) -> str:
    return f"{Path(file_path).as_posix()}::{qualified_name}"


def _callable_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _callable_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _callable_name(node.func)
    if isinstance(node, ast.Subscript):
        return _callable_name(node.value)
    return ""


def _awaited_call(node: ast.AST | None) -> ast.Call | None:
    if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
        return node.value
    return None


def _call_arguments(node: ast.Call) -> list[ast.AST]:
    return [*node.args, *[keyword.value for keyword in node.keywords]]


def _dynamic_import_module_name(
    node: ast.Call,
    importlib_aliases: set[str],
    import_module_aliases: set[str],
    constants,
) -> str | None:
    if not node.args:
        return None
    module_name = _static_string_value(node.args[0], constants)
    if module_name is None:
        return None
    callee = _callable_name(node.func)
    if callee == "__import__":
        return _builtin_import_returned_module_name(node, module_name)
    if callee in import_module_aliases:
        return module_name
    if "." in callee:
        receiver, method_name = callee.rsplit(".", 1)
        if method_name == "import_module" and receiver in importlib_aliases:
            return module_name
    return None


def _builtin_import_returned_module_name(node: ast.Call, module_name: str) -> str:
    if "." not in module_name:
        return module_name
    if _has_static_truthy_fromlist(node):
        return module_name
    return module_name.split(".", 1)[0]


def _has_static_truthy_fromlist(node: ast.Call) -> bool:
    fromlist_node: ast.AST | None = None
    if len(node.args) >= 4:
        fromlist_node = node.args[3]
    for keyword in node.keywords:
        if keyword.arg == "fromlist":
            fromlist_node = keyword.value
            break
    if fromlist_node is None:
        return False
    if isinstance(fromlist_node, (ast.List, ast.Tuple, ast.Set)):
        return bool(fromlist_node.elts)
    if isinstance(fromlist_node, ast.Constant):
        return bool(fromlist_node.value)
    return False


def _dynamic_import_member_alias(
    node: ast.Call,
    importlib_aliases: set[str],
    import_module_aliases: set[str],
    constants,
) -> tuple[str, str | None]:
    dynamic_call: ast.Call | None = None
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
        dynamic_call = node.func.value
    elif _is_getattr_call(node.func):
        receiver = node.func.args[0]
        if isinstance(receiver, ast.Call):
            dynamic_call = receiver
    if dynamic_call is None:
        return "", None
    module_name = _dynamic_import_module_name(
        dynamic_call,
        importlib_aliases=importlib_aliases,
        import_module_aliases=import_module_aliases,
        constants=constants,
    )
    if module_name is None:
        return "", None
    return _callable_name(dynamic_call.func), module_name


def _dynamic_import_member_reference(
    node: ast.AST,
    importlib_aliases: set[str],
    import_module_aliases: set[str],
    module_aliases: dict[str, str],
    constants,
) -> tuple[str, str] | None:
    if isinstance(node, ast.Attribute):
        module_name = _dynamic_import_receiver_module(
            node.value,
            importlib_aliases=importlib_aliases,
            import_module_aliases=import_module_aliases,
            module_aliases=module_aliases,
            constants=constants,
        )
        if module_name is not None:
            return module_name, node.attr
    if _is_getattr_call(node):
        member_name = _static_string_value(node.args[1], constants)
        if member_name is None:
            return None
        module_name = _dynamic_import_receiver_module(
            node.args[0],
            importlib_aliases=importlib_aliases,
            import_module_aliases=import_module_aliases,
            module_aliases=module_aliases,
            constants=constants,
        )
        if module_name is not None:
            return module_name, member_name
    return None


def _dynamic_import_receiver_module(
    node: ast.AST,
    importlib_aliases: set[str],
    import_module_aliases: set[str],
    module_aliases: dict[str, str],
    constants,
) -> str | None:
    if isinstance(node, ast.Call):
        return _dynamic_import_module_name(
            node,
            importlib_aliases=importlib_aliases,
            import_module_aliases=import_module_aliases,
            constants=constants,
        )
    receiver_name = _callable_name(node)
    if receiver_name:
        return module_aliases.get(receiver_name)
    return None


def _dynamic_getattr_callable_name(node: ast.AST, constants) -> str:
    if not _is_getattr_call(node):
        return ""
    receiver_name = _callable_name(node.args[0])
    member_name = _static_string_value(node.args[1], constants)
    if not receiver_name or member_name is None:
        return ""
    return f"{receiver_name}.{member_name}"


def _is_getattr_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and _callable_name(node.func) == "getattr"
        and len(node.args) >= 2
    )


def _static_string_value(node: ast.AST, constants) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string_value(node.left, constants)
        right = _static_string_value(node.right, constants)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue):
                formatted = _static_string_value(value.value, constants)
                if formatted is None:
                    return None
                parts.append(formatted)
                continue
            return None
        return "".join(parts)
    return None


def _static_string_sequence(node: ast.AST, constants) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[str] = []
        for element in node.elts:
            value = _static_string_value(element, constants)
            if value is None:
                return None
            values.append(value)
        return values
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string_sequence(node.left, constants)
        right = _static_string_sequence(node.right, constants)
        if left is not None and right is not None:
            return [*left, *right]
    return None


def _bound_static_import_name(item: ImportInfo, name: str) -> str:
    for alias, original in item.aliases.items():
        if original == name:
            return alias
    if item.module is None:
        return name.split(".")[0]
    return name


def _assigned_names(nodes: list[ast.AST]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            names.update(_assigned_names(list(node.elts)))
        elif isinstance(node, ast.Subscript):
            names.update(_assigned_names([node.value]))
        elif isinstance(node, ast.Attribute):
            root = _assignment_root_name(node)
            if root:
                names.add(root)
    return names


def _symbol_alias_targets(nodes: list[ast.AST]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        if not isinstance(node, ast.Name):
            return set()
        names.add(node.id)
    return names


def _assigned_call_targets(nodes: list[ast.AST]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            names.update(_assigned_call_targets(list(node.elts)))
        elif isinstance(node, ast.Subscript):
            names.update(_assigned_call_targets([node.value]))
        elif isinstance(node, ast.Attribute):
            target = _callable_name(node)
            if target:
                names.add(target)
    return names


def _assignment_root_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _assignment_root_name(node.value)
    if isinstance(node, ast.Subscript):
        return _assignment_root_name(node.value)
    return ""


def _loaded_names(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _is_test_file(file_path: str) -> bool:
    path = Path(file_path)
    return (
        path.name.startswith("test_")
        or path.name.endswith("_test.py")
    )
