import hashlib
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.github_repository_checkout_sources import (
    build_repository_checkout_discovery,
    render_repository_checkout_discovery_markdown,
    write_repository_checkout_discovery_artifacts,
)


def test_build_repository_checkout_discovery_lists_safe_local_sources():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        package = root / "pkg"
        package.mkdir()
        source = package / "average_mean.py"
        source.write_text(
            "def mean(nums):\n"
            "    if not nums:\n"
            "        raise ValueError('empty')\n"
            "    return sum(nums) / len(nums)\n",
            encoding="utf-8",
        )
        (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

        payload = build_repository_checkout_discovery(
            root,
            owner="example",
            repo="project",
            ref="main",
        )
        paths = write_repository_checkout_discovery_artifacts(payload, root / "out")
        markdown = render_repository_checkout_discovery_markdown(payload)

        files = payload["files"]
        assert payload["discovery"]["reason"] == "ok"
        assert payload["discovery"]["included_file_count"] == 2
        assert payload["discovery"]["skipped_dir_count"] == 1
        assert {item["path"] for item in files} == {
            "pkg/average_mean.py",
            "pyproject.toml",
        }
        source_entry = next(item for item in files if item["path"].endswith(".py"))
        assert Path(source_entry["raw_url"]).resolve() == source.resolve()
        assert source_entry["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert source_entry["owner"] == "example"
        assert source_entry["repo"] == "project"
        assert source_entry["ref"] == "main"
        assert "Repository Checkout Source Discovery" in markdown
        assert Path(paths["repository_checkout_sources_json"]).exists()
        assert Path(paths["repository_checkout_sources_markdown"]).exists()


def test_build_repository_checkout_discovery_reports_missing_checkout():
    payload = build_repository_checkout_discovery(
        Path("missing_checkout_for_test"),
        owner="example",
        repo="project",
    )

    assert payload["discovery"]["reason"] == "checkout_path_missing"
    assert payload["files"] == []


def test_checkout_discovery_keeps_package_named_build_under_src(tmp_path):
    package = tmp_path / "src" / "build"
    package.mkdir(parents=True)
    (package / "core.py").write_text("def build_project():\n    return 1\n", encoding="utf-8")
    generated = tmp_path / "build"
    generated.mkdir()
    (generated / "generated.py").write_text("GENERATED = True\n", encoding="utf-8")

    payload = build_repository_checkout_discovery(tmp_path)
    paths = {str(item["path"]) for item in payload["files"]}

    assert "src/build/core.py" in paths
    assert "build/generated.py" not in paths
    assert payload["discovery"]["skipped_dir_count"] == 1
