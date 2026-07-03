import io
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from code_intelligence_agent.evaluation import github_repository_checkout
from code_intelligence_agent.evaluation.github_repository_checkout import (
    checkout_github_repository,
    render_repository_checkout_markdown,
    write_repository_checkout_artifacts,
)


def test_checkout_github_repository_clones_into_output_dir(monkeypatch):
    monkeypatch.setattr(github_repository_checkout.shutil, "which", lambda name: "git")
    calls = []

    def fake_runner(command, cwd, capture_output, text, timeout, check):
        del cwd, capture_output, text, timeout, check
        calls.append(command)
        checkout_path = Path(command[-1])
        checkout_path.mkdir(parents=True)
        (checkout_path / ".git").mkdir()
        return subprocess.CompletedProcess(command, 0, "cloned", "")

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir) / "nested" / "run"
        payload = checkout_github_repository(
            owner="example",
            repo="project",
            ref="main",
            output_dir=output_dir,
            runner=fake_runner,
        )
        paths = write_repository_checkout_artifacts(payload, output_dir)
        markdown = render_repository_checkout_markdown(payload)

        assert payload["status"] == "pass"
        assert payload["reason"] == "checkout_created"
        assert payload["checkout_path"].endswith("repository_checkout")
        assert calls[0][1:5] == ["clone", "--depth", "1", "--branch"]
        assert Path(paths["repository_checkout_json"]).exists()
        assert "GitHub Repository Checkout" in markdown


def test_checkout_github_repository_skips_invalid_repo_identity():
    with tempfile.TemporaryDirectory() as tmp_dir:
        payload = checkout_github_repository(
            owner="../bad",
            repo="project",
            output_dir=tmp_dir,
        )

        assert payload["status"] == "skipped"
        assert payload["reason"] == "invalid_repo_identity"


def test_checkout_command_enforces_real_subprocess_timeout(tmp_path):
    started = time.monotonic()
    payload = github_repository_checkout._run_command(
        subprocess.run,
        [
            sys.executable,
            "-c",
            (
                "import subprocess, sys; "
                "subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(30)']).wait()"
            ),
        ],
        cwd=tmp_path,
        timeout=1,
    )

    assert time.monotonic() - started < 8
    assert payload["status"] == "fail"
    assert payload["reason"] == "timeout"
    assert payload["timeout"] is True


def test_checkout_github_repository_falls_back_to_archive(monkeypatch):
    monkeypatch.setattr(github_repository_checkout.shutil, "which", lambda name: "git")
    calls = []

    def fake_runner(command, cwd, capture_output, text, timeout, check):
        del cwd, capture_output, text, timeout, check
        calls.append(command)
        return subprocess.CompletedProcess(command, 128, "", "connection reset")

    archive_bytes = _zip_bytes(
        {
            "project-main/pyproject.toml": "[project]\nname='demo'\n",
            "project-main/tests/test_smoke.py": "def test_smoke():\n    assert True\n",
        }
    )

    def fake_opener(request, timeout):
        del timeout
        assert str(request.full_url).endswith("/zip/main")
        return _BytesResponse(archive_bytes)

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        payload = checkout_github_repository(
            owner="example",
            repo="project",
            ref="main",
            output_dir=output_dir,
            runner=fake_runner,
            archive_opener=fake_opener,
        )
        checkout_path = Path(payload["checkout_path"])

        assert payload["status"] == "pass"
        assert payload["reason"] == "archive_checkout_created"
        assert payload["checkout_method"] == "archive"
        assert payload["archive_status"] == "pass"
        assert payload["archive_reason"] == "archive_checkout_created"
        assert (checkout_path / "pyproject.toml").exists()
        assert (checkout_path / "tests" / "test_smoke.py").exists()
        assert calls


def test_checkout_github_repository_uses_archive_first_for_commit_ref(monkeypatch):
    monkeypatch.setattr(github_repository_checkout.shutil, "which", lambda name: "git")
    calls = []
    commit_ref = "6c0462028f547fc905a4d9a8cc956daed8a00cd8"
    archive_bytes = _zip_bytes(
        {
            "project-commit/pyproject.toml": "[project]\nname='demo'\n",
            "project-commit/tests/test_smoke.py": "def test_smoke():\n    assert True\n",
        }
    )

    def fake_runner(command, cwd, capture_output, text, timeout, check):
        del cwd, capture_output, text, timeout, check
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "cloned", "")

    def fake_opener(request, timeout):
        del timeout
        assert str(request.full_url).endswith(f"/zip/{commit_ref}")
        return _BytesResponse(archive_bytes)

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        payload = checkout_github_repository(
            owner="example",
            repo="project",
            ref=commit_ref,
            output_dir=output_dir,
            runner=fake_runner,
            archive_opener=fake_opener,
        )
        checkout_path = Path(payload["checkout_path"])

        assert payload["status"] == "pass"
        assert payload["reason"] == "archive_checkout_created"
        assert payload["checkout_method"] == "archive"
        assert payload["archive_status"] == "pass"
        assert calls == []
        assert checkout_path == (output_dir / "repository_checkout").resolve()
        assert (checkout_path / "pyproject.toml").exists()
        assert (checkout_path / "tests" / "test_smoke.py").exists()


def test_checkout_github_repository_uses_sibling_archive_after_partial_git_checkout(
    monkeypatch,
):
    monkeypatch.setattr(github_repository_checkout.shutil, "which", lambda name: "git")

    def fake_runner(command, cwd, capture_output, text, timeout, check):
        del cwd, capture_output, text, timeout, check
        checkout_path = Path(command[-1])
        (checkout_path / ".git" / "objects" / "pack").mkdir(parents=True)
        (checkout_path / ".git" / "objects" / "pack" / "partial.idx").write_text(
            "left by failed clone\n"
        )
        return subprocess.CompletedProcess(command, 128, "", "clone timed out")

    archive_bytes = _zip_bytes(
        {
            "project-main/pyproject.toml": "[project]\nname='demo'\n",
            "project-main/tests/test_smoke.py": "def test_smoke():\n    assert True\n",
        }
    )

    def fake_opener(request, timeout):
        del request, timeout
        return _BytesResponse(archive_bytes)

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        payload = checkout_github_repository(
            owner="example",
            repo="project",
            ref="main",
            output_dir=output_dir,
            runner=fake_runner,
            archive_opener=fake_opener,
        )
        primary_checkout = output_dir / "repository_checkout"
        archive_checkout = output_dir / "repository_checkout_archive"

        assert payload["status"] == "pass"
        assert payload["reason"] == "archive_checkout_created"
        assert payload["checkout_method"] == "archive"
        assert Path(payload["checkout_path"]) == archive_checkout.resolve()
        assert (primary_checkout / ".git" / "objects" / "pack" / "partial.idx").exists()
        assert (archive_checkout / "pyproject.toml").exists()
        assert (archive_checkout / "tests" / "test_smoke.py").exists()


def test_checkout_github_repository_reuses_existing_checkout(monkeypatch):
    monkeypatch.setattr(github_repository_checkout.shutil, "which", lambda name: "git")

    with tempfile.TemporaryDirectory() as tmp_dir:
        checkout_path = Path(tmp_dir) / "repository_checkout"
        (checkout_path / ".git").mkdir(parents=True)
        (checkout_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
        payload = checkout_github_repository(
            owner="example",
            repo="project",
            output_dir=tmp_dir,
        )

        assert payload["status"] == "pass"
        assert payload["reason"] == "existing_checkout"


def test_checkout_github_repository_reuses_existing_sibling_archive_checkout(monkeypatch):
    monkeypatch.setattr(github_repository_checkout.shutil, "which", lambda name: "git")

    with tempfile.TemporaryDirectory() as tmp_dir:
        primary_checkout = Path(tmp_dir) / "repository_checkout"
        archive_checkout = Path(tmp_dir) / "repository_checkout_archive"
        (primary_checkout / ".git").mkdir(parents=True)
        archive_checkout.mkdir()
        (archive_checkout / "pyproject.toml").write_text("[project]\nname='demo'\n")

        payload = checkout_github_repository(
            owner="example",
            repo="project",
            ref="main",
            output_dir=tmp_dir,
        )

        assert payload["status"] == "pass"
        assert payload["reason"] == "existing_archive_checkout"
        assert payload["checkout_method"] == "archive"
        assert Path(payload["checkout_path"]) == archive_checkout.resolve()


def test_checkout_github_repository_reuses_existing_archive_checkout(monkeypatch):
    monkeypatch.setattr(github_repository_checkout.shutil, "which", lambda name: None)

    with tempfile.TemporaryDirectory() as tmp_dir:
        checkout_path = Path(tmp_dir) / "repository_checkout"
        checkout_path.mkdir()
        (checkout_path / "pyproject.toml").write_text("[project]\nname='demo'\n")

        payload = checkout_github_repository(
            owner="example",
            repo="project",
            ref="main",
            output_dir=tmp_dir,
        )

        assert payload["status"] == "pass"
        assert payload["reason"] == "existing_archive_checkout"
        assert payload["checkout_method"] == "archive"
        assert payload["archive_url"].endswith("/zip/main")


def test_checkout_github_repository_rejects_empty_existing_checkout(monkeypatch):
    monkeypatch.setattr(github_repository_checkout.shutil, "which", lambda name: "git")

    with tempfile.TemporaryDirectory() as tmp_dir:
        checkout_path = Path(tmp_dir) / "repository_checkout"
        checkout_path.mkdir()

        payload = checkout_github_repository(
            owner="example",
            repo="project",
            output_dir=tmp_dir,
        )

        assert payload["status"] == "fail"
        assert payload["reason"] == "checkout_path_exists"


def test_checkout_github_repository_resolves_relative_output_dir(monkeypatch):
    monkeypatch.setattr(github_repository_checkout.shutil, "which", lambda name: "git")

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        expected_output = root / "relative_run"
        expected_checkout = expected_output / "repository_checkout"

        with monkeypatch.context() as scoped_monkeypatch:
            scoped_monkeypatch.chdir(root)

            def fake_runner(command, cwd, capture_output, text, timeout, check):
                del capture_output, text, timeout, check
                checkout_path = Path(command[-1])
                assert cwd == expected_output.resolve()
                assert checkout_path == expected_checkout.resolve()
                checkout_path.mkdir(parents=True)
                (checkout_path / ".git").mkdir()
                return subprocess.CompletedProcess(command, 0, "cloned", "")

            payload = checkout_github_repository(
                owner="example",
                repo="project",
                output_dir="relative_run",
                runner=fake_runner,
            )

        assert payload["status"] == "pass"
        assert payload["checkout_path"] == str(expected_checkout.resolve())
        assert not (expected_output / "relative_run").exists()


def test_archive_fetch_enforces_total_timeout(monkeypatch):
    ticks = iter([0.0, 0.1, 0.2, 2.0])
    monkeypatch.setattr(
        github_repository_checkout.time,
        "monotonic",
        lambda: next(ticks),
    )

    def fake_opener(request, timeout):
        del request, timeout
        return _BytesResponse(b"a" * 10)

    data, reason = github_repository_checkout._fetch_archive_bytes(
        "https://codeload.github.com/example/project/zip/main",
        timeout=1,
        opener=fake_opener,
        max_bytes=100,
        chunk_size=1,
    )

    assert data == b""
    assert reason == "archive_fetch_timeout"


class _BytesResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        start = self._offset
        end = min(len(self._payload), start + size)
        self._offset = end
        return self._payload[start:end]


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return buffer.getvalue()
