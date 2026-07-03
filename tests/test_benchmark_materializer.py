from pathlib import Path
import hashlib
import json
import tempfile

import pytest

from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner


def test_materializer_generates_manifest_and_runnable_benchmark():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "raw_sample.py"
        raw_source.write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n",
            encoding="utf-8",
        )
        template = root / "template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "materialized_shift_left",
                            "repo_path": "materialized_shift_left_repo",
                            "sources": [
                                {
                                    "raw_url": str(raw_source),
                                    "target_path": "sample.py",
                                }
                            ],
                            "files": [
                                {
                                    "target_path": "test_sample.py",
                                    "content": (
                                        "from sample import shift_left\n\n"
                                        "def test_shift_left():\n"
                                        "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n"
                                    ),
                                }
                            ],
                            "benchmark": {
                                "buggy_functions": ["shift_left"],
                                "expected_rule_ids": ["possible_index_overrun"],
                                "failing_tests": ["test_shift_left"],
                                "passed_tests": [],
                                "test_args": [],
                                "metadata": {"source": "local_raw_source"},
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        output = root / "generated"

        manifest = BenchmarkMaterializer().materialize_template(template, output)
        report = BenchmarkRunner().run_manifest(manifest)

        assert manifest == output / "manifest.json"
        assert (output / "materialized_shift_left_repo" / "sample.py").exists()
        assert report.top1 == 1.0
        assert report.patch_success_rate == 1.0
        assert report.cases[0].best_patch_rule_id == "possible_index_overrun"


def test_materializer_applies_mutations_to_raw_sources_before_benchmarking():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "raw_sample.py"
        raw_source.write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values) - 1):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n",
            encoding="utf-8",
        )
        template = root / "template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "mutated_raw_shift_left",
                            "repo_path": "mutated_raw_shift_left_repo",
                            "sources": [
                                {
                                    "raw_url": str(raw_source),
                                    "target_path": "sample.py",
                                }
                            ],
                            "mutations": [
                                {
                                    "target_path": "sample.py",
                                    "find": "range(len(values) - 1)",
                                    "replace": "range(len(values))",
                                    "description": "Inject off-by-one boundary bug.",
                                }
                            ],
                            "files": [
                                {
                                    "target_path": "test_sample.py",
                                    "content": (
                                        "from sample import shift_left\n\n"
                                        "def test_shift_left():\n"
                                        "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n"
                                    ),
                                }
                            ],
                            "benchmark": {
                                "buggy_functions": ["shift_left"],
                                "expected_rule_ids": ["possible_index_overrun"],
                                "failing_tests": ["test_shift_left"],
                                "passed_tests": [],
                                "test_args": [],
                                "metadata": {"source": "local_raw_source_mutation"},
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        output = root / "generated"

        manifest = BenchmarkMaterializer().materialize_template(template, output)
        mutated_source = output / "mutated_raw_shift_left_repo" / "sample.py"
        report = BenchmarkRunner().run_manifest(manifest)
        manifest_data = json.loads(manifest.read_text(encoding="utf-8"))

        assert "range(len(values))" in mutated_source.read_text(encoding="utf-8")
        assert manifest_data["cases"][0]["metadata"]["materialized_mutations"][0][
            "description"
        ] == "Inject off-by-one boundary bug."
        assert report.top1 == 1.0
        assert report.patch_success_rate == 1.0
        assert report.cases[0].best_patch_rule_id == "possible_index_overrun"


def test_materializer_rejects_unsafe_paths():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template = root / "template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "unsafe",
                            "repo_path": "../escape",
                            "sources": [],
                            "files": [],
                            "benchmark": {},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="Unsafe relative path"):
            BenchmarkMaterializer().materialize_template(template, root / "out")


def test_materializer_rejects_missing_mutation_pattern():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "raw_sample.py"
        raw_source.write_text("VALUE = 1\n", encoding="utf-8")
        template = root / "template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "missing_mutation",
                            "repo_path": "repo",
                            "sources": [
                                {
                                    "raw_url": str(raw_source),
                                    "target_path": "sample.py",
                                }
                            ],
                            "mutations": [
                                {
                                    "target_path": "sample.py",
                                    "find": "VALUE = 2",
                                    "replace": "VALUE = 3",
                                }
                            ],
                            "files": [],
                            "benchmark": {},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="Mutation pattern not found"):
            BenchmarkMaterializer().materialize_template(template, root / "out")


def test_materializer_rejects_unsafe_source_target_paths():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "raw_sample.py"
        raw_source.write_text("VALUE = 1\n", encoding="utf-8")
        template = root / "template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "unsafe_source",
                            "repo_path": "repo",
                            "sources": [
                                {
                                    "raw_url": str(raw_source),
                                    "target_path": "../escape.py",
                                }
                            ],
                            "files": [],
                            "benchmark": {},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="Unsafe relative path"):
            BenchmarkMaterializer().materialize_template(template, root / "out")


def test_materializer_reuses_root_source_cache_without_polluting_case_repo():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "raw_sample.py"
        raw_source.write_text("VALUE = 1\n", encoding="utf-8")
        digest = hashlib.sha256(raw_source.read_bytes()).hexdigest()
        template = root / "template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "cached_source",
                            "repo_path": "repo",
                            "sources": [
                                {
                                    "raw_url": str(raw_source),
                                    "target_path": "sample.py",
                                    "sha256": digest,
                                }
                            ],
                            "files": [],
                            "benchmark": {
                                "buggy_functions": ["value"],
                                "expected_rule_ids": ["rule"],
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        output = root / "generated"

        manifest = BenchmarkMaterializer().materialize_template(template, output)
        raw_source.write_text("VALUE = 2\n", encoding="utf-8")
        (output / "repo" / "sample.py").unlink()
        manifest_again = BenchmarkMaterializer().materialize_template(template, output)

        assert manifest_again == manifest
        assert (output / ".source_cache" / f"{digest}.py").exists()
        assert not (output / "repo" / ".source_cache").exists()
        assert (output / "repo" / "sample.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_materializer_reuses_external_source_cache_across_output_dirs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "raw_sample.py"
        raw_source.write_text("VALUE = 1\n", encoding="utf-8")
        digest = hashlib.sha256(raw_source.read_bytes()).hexdigest()
        shared_cache = root / "shared_cache"
        template = root / "template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "external_cached_source",
                            "repo_path": "repo",
                            "sources": [
                                {
                                    "raw_url": str(raw_source),
                                    "target_path": "sample.py",
                                    "sha256": digest,
                                }
                            ],
                            "files": [],
                            "benchmark": {
                                "buggy_functions": ["value"],
                                "expected_rule_ids": ["rule"],
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        BenchmarkMaterializer().materialize_template(
            template,
            root / "generated_one",
            source_cache_dir=shared_cache,
        )
        raw_source.write_text("VALUE = 2\n", encoding="utf-8")
        BenchmarkMaterializer().materialize_template(
            template,
            root / "generated_two",
            source_cache_dir=shared_cache,
        )

        assert (shared_cache / f"{digest}.py").exists()
        assert not (root / "generated_one" / ".source_cache").exists()
        assert not (root / "generated_two" / ".source_cache").exists()
        assert (
            root / "generated_two" / "repo" / "sample.py"
        ).read_text(encoding="utf-8") == "VALUE = 1\n"
