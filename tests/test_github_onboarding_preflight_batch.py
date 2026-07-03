import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.github_onboarding_preflight_batch import (
    render_github_onboarding_preflight_batch_markdown,
    run_github_onboarding_preflight_batch,
)
from code_intelligence_agent.evaluation.github_onboarding_smoke_runner import (
    run_onboarding_smoke_suite,
)


def test_preflight_batch_generates_smoke_manifest_for_ready_runs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        ready_discovery = _write_average_discovery(root)
        docs_discovery = _write_docs_discovery(root)
        manifest = root / "preflight_batch.json"
        output_dir = root / "batch_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "preflight_batch_smoke",
                    "defaults": {
                        "mode": "from-discovery",
                        "sample_sources": 5,
                        "max_candidates": 6,
                    },
                    "thresholds": {
                        "min_generated_candidates": 1,
                        "require_quality_gate": True,
                    },
                    "runs": [
                        {
                            "name": "average_ready",
                            "discovery": ready_discovery.name,
                        },
                        {
                            "name": "docs_only",
                            "discovery": docs_discovery.name,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_github_onboarding_preflight_batch(manifest, output_dir)
        markdown = render_github_onboarding_preflight_batch_markdown(report)
        smoke_manifest = json.loads(
            Path(report.output_paths["smoke_manifest"]).read_text(encoding="utf-8")
        )
        offline_manifest = json.loads(
            Path(report.output_paths["offline_preflight_manifest"]).read_text(
                encoding="utf-8"
            )
        )
        skipped = json.loads(
            Path(report.output_paths["skipped_json"]).read_text(encoding="utf-8")
        )

        assert report.passed is True
        assert report.summary["run_count"] == 2
        assert report.summary["ready_count"] == 1
        assert report.summary["fail_count"] == 1
        assert report.summary["readiness_rate"] == 0.5
        assert report.summary["ready_run_names"] == ["average_ready"]
        assert report.summary["skipped_run_names"] == ["docs_only"]
        assert report.summary["error_run_names"] == []
        assert report.summary["top_issue_code"] == "no_python_sources"
        assert report.summary["profile_doctor_status_counts"] == {
            "fail": 1,
            "warn": 1,
        }
        assert report.summary["profile_doctor_blocker_counts"] == {
            "python_sources": 1,
            "test_or_config_signal": 1,
        }
        assert report.summary["top_profile_doctor_blocker"] == "python_sources"
        assert report.summary["generated_candidates"] >= 1
        assert report.runs[0].profile_doctor_status == "warn"
        assert report.runs[0].profile_doctor_blocker == "test_or_config_signal"
        assert report.runs[1].profile_doctor_status == "fail"
        assert report.runs[1].profile_doctor_blocker == "python_sources"
        assert smoke_manifest["suite_name"] == "preflight_batch_smoke_preflight_smoke"
        assert smoke_manifest["thresholds"]["min_generated_candidates"] == 1
        assert [run["name"] for run in smoke_manifest["runs"]] == ["average_ready"]
        assert smoke_manifest["runs"][0]["mode"] == "from-discovery"
        assert smoke_manifest["runs"][0]["discovery"].endswith(
            "preflight_discovery.json"
        )
        assert offline_manifest["suite_name"] == (
            "preflight_batch_smoke_offline_preflight"
        )
        assert offline_manifest["thresholds"]["min_generated_candidates"] == 1
        assert [run["name"] for run in offline_manifest["runs"]] == [
            "average_ready",
            "docs_only",
        ]
        assert {run["mode"] for run in offline_manifest["runs"]} == {
            "from-discovery"
        }
        assert all(
            run["discovery"].endswith("preflight_discovery.json")
            for run in offline_manifest["runs"]
        )
        assert skipped["runs"][0]["name"] == "docs_only"
        assert skipped["runs"][0]["issue_codes"] == ["no_python_sources"]
        assert "Readiness Summary" in markdown
        assert "Ready Runs: average_ready" in markdown
        assert "Skipped Runs: docs_only" in markdown
        assert "Top Issue: `no_python_sources`" in markdown
        assert "Repository Doctor Statuses: fail=1, warn=1" in markdown
        assert "Repository Doctor Top Blocker: `python_sources`" in markdown
        assert "Offline Preflight Manifest" in markdown
        assert "average_ready" in markdown
        assert "docs_only" in markdown


def test_preflight_batch_tree_smoke_manifest_reuses_saved_discovery():
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
        opener = FakeOpener(
            [
                {
                    "sha": "abc123",
                    "tree": [
                        {
                            "path": "maths/average_mean.py",
                            "type": "blob",
                            "raw_url": str(raw_source),
                            "target_path": "average_mean.py",
                            "sha256": hashlib.sha256(
                                raw_source.read_bytes()
                            ).hexdigest(),
                            "license": "MIT",
                        }
                    ],
                }
            ]
        )
        manifest = root / "preflight_batch.json"
        output_dir = root / "batch_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "tree_preflight_to_offline_smoke",
                    "runs": [
                        {
                            "name": "average_tree",
                            "mode": "tree",
                            "owner": "example",
                            "repo": "algorithms",
                            "ref": "main",
                            "sample_sources": 5,
                            "source_cache_dir": "outputs_smoke/source_cache",
                            "dependency_max_depth": 4,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_github_onboarding_preflight_batch(
            manifest,
            output_dir,
            opener=opener,
        )
        smoke_manifest = json.loads(
            Path(report.output_paths["smoke_manifest"]).read_text(encoding="utf-8")
        )
        offline_manifest = json.loads(
            Path(report.output_paths["offline_preflight_manifest"]).read_text(
                encoding="utf-8"
            )
        )
        smoke_run = smoke_manifest["runs"][0]
        offline_run = offline_manifest["runs"][0]

        assert opener.urls == [
            "https://api.github.com/repos/example/algorithms/git/trees/main?recursive=1"
        ]
        assert report.passed is True
        assert report.runs[0].recommended_run["mode"] == "from-discovery"
        assert smoke_run["mode"] == "from-discovery"
        assert smoke_run["discovery"].replace("\\", "/").endswith(
            "preflight_runs/average_tree/preflight_discovery.json"
        )
        assert smoke_run["owner"] == "example"
        assert smoke_run["repo"] == "algorithms"
        assert smoke_run["ref"] == "main"
        assert smoke_run["source_cache_dir"] == "outputs_smoke/source_cache"
        assert smoke_run["dependency_max_depth"] == 4
        assert offline_run["mode"] == "from-discovery"
        assert offline_run["discovery"].replace("\\", "/").endswith(
            "preflight_runs/average_tree/preflight_discovery.json"
        )
        assert offline_run["sample_sources"] == 5
        assert offline_run["source_cache_dir"] == "outputs_smoke/source_cache"
        assert offline_run["dependency_max_depth"] == 4


def test_preflight_batch_smoke_manifest_feeds_existing_runner():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        ready_discovery = _write_average_discovery(root)
        manifest = root / "preflight_batch.json"
        preflight_output = root / "batch_output"
        smoke_output = root / "smoke_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "preflight_to_smoke",
                    "runs": [
                        {
                            "name": "average_ready",
                            "mode": "from-discovery",
                            "discovery": ready_discovery.name,
                            "sample_sources": 5,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        preflight_report = run_github_onboarding_preflight_batch(
            manifest,
            preflight_output,
        )
        smoke_report = run_onboarding_smoke_suite(
            preflight_report.output_paths["smoke_manifest"],
            smoke_output,
        )

        assert preflight_report.passed is True
        assert smoke_report.passed is True
        assert smoke_report.summary["run_count"] == 1
        assert smoke_report.summary["generated_candidates"] >= 1
        assert smoke_report.gap_summary["headline"]["status"] == "pass"


def test_preflight_batch_allows_repository_test_smoke_without_candidates():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_no_candidate_discovery(root)
        manifest = root / "preflight_batch.json"
        output_dir = root / "batch_output"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "preflight_repository_test_smoke",
                    "runs": [
                        {
                            "name": "repo_test_overlay_candidate",
                            "mode": "from-discovery",
                            "discovery": discovery.name,
                            "sample_sources": 5,
                            "checkout_repository_tests": True,
                            "repository_test_timeout": 3,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = run_github_onboarding_preflight_batch(manifest, output_dir)
        smoke_manifest = json.loads(
            Path(report.output_paths["smoke_manifest"]).read_text(encoding="utf-8")
        )

        assert report.passed is True
        assert report.summary["ready_count"] == 1
        assert report.summary["generated_candidates"] == 0
        assert report.runs[0].ready_for_smoke is True
        assert "no_preflight_candidates" in report.runs[0].issue_codes
        assert "repository_test_smoke_fallback" in report.runs[0].issue_codes
        assert smoke_manifest["runs"][0]["name"] == "repo_test_overlay_candidate"
        assert smoke_manifest["runs"][0]["checkout_repository_tests"] is True
        assert smoke_manifest["runs"][0]["repository_test_timeout"] == 3


def test_preflight_batch_cli_writes_machine_readable_report():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        ready_discovery = _write_average_discovery(root)
        manifest = root / "preflight_batch.json"
        output_dir = root / "batch_output"
        output_json = root / "batch.json"
        output_markdown = root / "batch.md"
        manifest.write_text(
            json.dumps(
                {
                    "suite_name": "preflight_batch_cli",
                    "runs": [
                        {
                            "name": "average_ready",
                            "mode": "from-discovery",
                            "discovery": ready_discovery.name,
                            "sample_sources": 5,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_onboarding_preflight_batch",
                str(manifest),
                str(output_dir),
                "--format",
                "json",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        saved = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert saved["summary"]["ready_count"] == 1
        assert saved["smoke_manifest"]["runs"][0]["name"] == "average_ready"
        assert "preflight_batch_smoke_manifest.json" in completed.stdout
        assert output_markdown.exists()


def _write_average_discovery(root: Path) -> Path:
    raw_source = root / "average_mean.py"
    raw_source.write_text(
        "def mean(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    discovery = root / "average.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "maths/average_mean.py",
                        "raw_url": str(raw_source),
                        "target_path": "average_mean.py",
                        "owner": "example",
                        "repo": "algorithms",
                        "ref": "v1.0.0",
                        "sha256": hashlib.sha256(
                            raw_source.read_bytes()
                        ).hexdigest(),
                        "license": "MIT",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery


def _write_docs_discovery(root: Path) -> Path:
    readme = root / "README.md"
    readme.write_text("# docs\n", encoding="utf-8")
    discovery = root / "docs.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "README.md",
                        "raw_url": str(readme),
                        "target_path": "README.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery


def _write_no_candidate_discovery(root: Path) -> Path:
    source = root / "plain.py"
    source.write_text(
        "def add(left, right):\n"
        "    return left + right\n",
        encoding="utf-8",
    )
    discovery = root / "plain.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "plain.py",
                        "raw_url": str(source),
                        "target_path": "plain.py",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.urls = []

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        return FakeResponse(self.payloads.pop(0))
