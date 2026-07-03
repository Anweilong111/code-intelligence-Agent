import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_source_miner import (
    mine_recipe_sources,
)
from code_intelligence_agent.evaluation.github_source_importer import (
    import_github_sources,
    render_github_source_import_markdown,
)


def test_importer_converts_tree_payload_with_filters():
    payload = {
        "tree": [
            {"path": "src/math.py", "type": "blob"},
            {"path": "docs/readme.md", "type": "blob"},
            {"path": "tests/test_math.py", "type": "blob"},
            {"path": "src/package", "type": "tree"},
        ]
    }

    report = import_github_sources(
        payload,
        owner="example",
        repo="project",
        ref="abc123",
        exclude=["tests/*"],
    )
    source = report.source_entries[0]

    assert report.input_count == 4
    assert report.source_count == 1
    assert report.skipped_count == 3
    assert source == {
        "target_path": "math.py",
        "owner": "example",
        "repo": "project",
        "ref": "abc123",
        "source_path": "src/math.py",
    }
    assert {row.reason for row in report.rows if row.status == "skipped"} == {
        "excluded",
        "not_blob",
        "not_included",
    }


def test_importer_converts_search_payload_and_uses_ref_override():
    payload = {
        "items": [
            {
                "path": "maths/average_mean.py",
                "repository": {
                    "full_name": "TheAlgorithms/Python",
                    "default_branch": "master",
                },
            }
        ]
    }

    report = import_github_sources(payload, ref="6c0462")

    assert report.source_entries == [
        {
            "target_path": "average_mean.py",
            "owner": "TheAlgorithms",
            "repo": "Python",
            "ref": "6c0462",
            "source_path": "maths/average_mean.py",
        }
    ]
    assert "TheAlgorithms/Python" in render_github_source_import_markdown(report)


def test_importer_converts_repository_listing_and_dedupes_targets():
    payload = {
        "repositories": [
            {
                "full_name": "org/tools",
                "ref": "deadbeef",
                "license": "MIT",
                "paths": [
                    {
                        "path": "src/utils.py",
                        "sha256": "a" * 64,
                    },
                    {
                        "path": "lib/utils.py",
                        "sha256": "b" * 64,
                    },
                ],
            }
        ]
    }

    report = import_github_sources(payload)

    assert report.source_count == 2
    assert report.source_entries == [
        {
            "target_path": "lib_utils.py",
            "owner": "org",
            "repo": "tools",
            "ref": "deadbeef",
            "source_path": "lib/utils.py",
            "sha256": "b" * 64,
            "license": "MIT",
        },
        {
            "target_path": "src_utils.py",
            "owner": "org",
            "repo": "tools",
            "ref": "deadbeef",
            "source_path": "src/utils.py",
            "sha256": "a" * 64,
            "license": "MIT",
        },
    ]


def test_importer_preserves_paths_and_adds_target_prefix():
    payload = {
        "files": [
            {"path": "src/alpha.py", "raw_url": "file:///tmp/alpha.py"},
            {"path": "src/beta.py", "raw_url": "file:///tmp/beta.py"},
        ]
    }

    report = import_github_sources(
        payload,
        preserve_paths=True,
        target_prefix="third_party",
    )

    assert [source["target_path"] for source in report.source_entries] == [
        "third_party/src/alpha.py",
        "third_party/src/beta.py",
    ]
    assert report.source_entries[0]["raw_url"] == "file:///tmp/alpha.py"


def test_importer_keeps_package_relative_path_with_target_prefix():
    payload = {
        "files": [
            {
                "path": "src/werkzeug/debug/tbtools.py",
                "raw_url": "file:///tmp/tbtools.py",
            },
            {
                "path": "src/werkzeug/utils.py",
                "raw_url": "file:///tmp/utils.py",
            },
        ]
    }

    report = import_github_sources(payload, target_prefix="werkzeug")

    assert [source["target_path"] for source in report.source_entries] == [
        "werkzeug/debug/tbtools.py",
        "werkzeug/utils.py",
    ]


def test_importer_preserves_auxiliary_path_outside_target_prefix():
    payload = {
        "files": [
            {
                "path": "examples/couchy/views.py",
                "raw_url": "file:///tmp/views.py",
            },
            {
                "path": "src/werkzeug/utils.py",
                "raw_url": "file:///tmp/utils.py",
            },
        ]
    }

    report = import_github_sources(payload, target_prefix="werkzeug")

    assert [source["target_path"] for source in report.source_entries] == [
        "examples/couchy/views.py",
        "werkzeug/utils.py",
    ]


def test_importer_output_feeds_source_miner():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        raw_source = root / "average_mean.py"
        raw_source.write_text(
            "def mean(nums):\n"
            "    if not nums:\n"
            "        raise ValueError(\"List is empty\")\n"
            "    return sum(nums) / len(nums)\n",
            encoding="utf-8",
        )
        report = import_github_sources(
            {
                "files": [
                    {
                        "path": "maths/average_mean.py",
                        "raw_url": str(raw_source),
                        "target_path": "average_mean.py",
                    }
                ]
            }
        )
        mining_report = mine_recipe_sources(
            {"sources": report.source_entries},
            recipes=["missing_len_zero_guard"],
        )

        assert mining_report.source_count == 1
        assert mining_report.generated_count == 1


def test_importer_cli_writes_report_and_sources():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = root / "discovery.json"
        output_json = root / "import_report.json"
        output_markdown = root / "import_report.md"
        output_sources = root / "sources.json"
        discovery.write_text(
            json.dumps(
                {
                    "tree": [
                        {"path": "src/math.py", "type": "blob"},
                        {"path": "README.md", "type": "blob"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_source_importer",
                str(discovery),
                "--owner",
                "example",
                "--repo",
                "project",
                "--ref",
                "abc123",
                "--format",
                "markdown",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--output-sources",
                str(output_sources),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        report_payload = json.loads(output_json.read_text(encoding="utf-8"))
        sources_payload = json.loads(output_sources.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert "# GitHub Source Import" in completed.stdout
        assert report_payload["source_count"] == 1
        assert sources_payload == {
            "sources": [
                {
                    "target_path": "math.py",
                    "owner": "example",
                    "repo": "project",
                    "ref": "abc123",
                    "source_path": "src/math.py",
                }
            ]
        }
        assert "not_included" in output_markdown.read_text(encoding="utf-8")
