from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    repo_path: str
    buggy_functions: list[str]
    expected_rule_ids: list[str]
    failing_tests: list[str]
    passed_tests: list[str]
    test_args: list[str]
    metadata: dict[str, Any]


class BenchmarkLoader:
    def load_manifest(self, manifest_path: str | Path) -> list[BenchmarkCase]:
        path = Path(manifest_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        cases = []
        for item in data.get("cases", []):
            repo_path = Path(item["repo_path"])
            if not repo_path.is_absolute():
                repo_path = (path.parent / repo_path).resolve()
            cases.append(
                BenchmarkCase(
                    name=item["name"],
                    repo_path=str(repo_path),
                    buggy_functions=list(item.get("buggy_functions", [])),
                    expected_rule_ids=list(item.get("expected_rule_ids", [])),
                    failing_tests=list(item.get("failing_tests", [])),
                    passed_tests=list(item.get("passed_tests", [])),
                    test_args=list(item.get("test_args", [])),
                    metadata=dict(item.get("metadata", {})),
                )
            )
        return cases
