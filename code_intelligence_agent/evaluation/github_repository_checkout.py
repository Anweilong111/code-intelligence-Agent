from __future__ import annotations

import io
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

Runner = Callable[..., subprocess.CompletedProcess]
UrlOpen = Callable[..., Any]


def checkout_github_repository(
    *,
    owner: str,
    repo: str,
    output_dir: str | Path,
    ref: str | None = None,
    depth: int = 1,
    timeout: int = 120,
    runner: Runner | None = None,
    archive_opener: UrlOpen | None = None,
) -> dict[str, Any]:
    output_root = Path(output_dir).resolve()
    checkout_path = output_root / "repository_checkout"
    if not _safe_github_part(owner) or not _safe_github_part(repo):
        return _result(
            status="skipped",
            reason="invalid_repo_identity",
            message="Owner/repo values are not safe GitHub path components.",
            checkout_path=checkout_path,
            owner=owner,
            repo=repo,
            ref=ref,
        )
    if depth <= 0:
        return _result(
            status="skipped",
            reason="invalid_depth",
            message="Repository checkout depth must be positive.",
            checkout_path=checkout_path,
            owner=owner,
            repo=repo,
            ref=ref,
        )
    clone_url = f"https://github.com/{owner}/{repo}.git"
    archive_url = _archive_url(owner=owner, repo=repo, ref=ref)
    archive_checkout_path = output_root / "repository_checkout_archive"
    if (checkout_path / ".git").exists() and _directory_has_entries_excluding(
        checkout_path,
        {".git"},
    ):
        return _result(
            status="pass",
            reason="existing_checkout",
            message="Reusing existing repository_checkout git repository.",
            checkout_path=checkout_path,
            owner=owner,
            repo=repo,
            ref=ref,
            clone_url=clone_url,
            archive_url=archive_url,
            checkout_method="git",
        )
    if (
        archive_checkout_path.exists()
        and archive_checkout_path.is_dir()
        and _directory_has_entries(archive_checkout_path)
    ):
        return _result(
            status="pass",
            reason="existing_archive_checkout",
            message=(
                "Reusing existing repository_checkout_archive directory from a "
                "previous archive checkout."
            ),
            checkout_path=archive_checkout_path,
            owner=owner,
            repo=repo,
            ref=ref,
            clone_url=clone_url,
            archive_url=archive_url,
            checkout_method="archive",
        )
    if (
        checkout_path.exists()
        and checkout_path.is_dir()
        and not (checkout_path / ".git").exists()
        and _directory_has_entries(checkout_path)
    ):
        return _result(
            status="pass",
            reason="existing_archive_checkout",
            message=(
                "Reusing existing repository_checkout directory from a previous "
                "archive or materialized checkout."
            ),
            checkout_path=checkout_path,
            owner=owner,
            repo=repo,
            ref=ref,
            clone_url=clone_url,
            archive_url=archive_url,
            checkout_method="archive",
        )
    if checkout_path.exists():
        return _result(
            status="fail",
            reason="checkout_path_exists",
            message=(
                "Checkout path already exists but is not a reusable repository "
                "checkout."
            ),
            checkout_path=checkout_path,
            owner=owner,
            repo=repo,
            ref=ref,
            clone_url=clone_url,
            archive_url=archive_url,
            next_actions=[
                "Choose a clean output directory or remove the stale repository_checkout directory.",
            ],
        )
    git_path = shutil.which("git")
    if not git_path:
        return _result(
            status="skipped",
            reason="git_unavailable",
            message="git executable was not found on PATH.",
            checkout_path=checkout_path,
            owner=owner,
            repo=repo,
            ref=ref,
            next_actions=["Install git or provide --repository-test-root manually."],
        )
    run = runner or subprocess.run

    output_root.mkdir(parents=True, exist_ok=True)
    if ref and _is_probable_commit_sha(ref):
        archive = _checkout_from_archive(
            archive_url=archive_url,
            checkout_path=checkout_path,
            output_root=output_root,
            timeout=timeout,
            opener=archive_opener,
        )
        if archive.get("status") == "pass":
            materialized_checkout_path = Path(
                str(archive.get("checkout_path") or checkout_path)
            )
            return _result(
                status="pass",
                reason="archive_checkout_created",
                message=(
                    "Repository checkout completed from GitHub archive for "
                    "commit ref."
                ),
                checkout_path=materialized_checkout_path,
                owner=owner,
                repo=repo,
                ref=ref,
                clone_url=clone_url,
                archive_url=archive_url,
                checkout_method="archive",
                archive_status=str(archive.get("status") or ""),
                archive_reason=str(archive.get("reason") or ""),
                archive_message=str(archive.get("message") or ""),
            )

    clone_command = [
        git_path,
        "clone",
        "--depth",
        str(depth),
        *_branch_args(ref),
        clone_url,
        str(checkout_path),
    ]
    commands = [clone_command]
    completed = _run_command(run, clone_command, cwd=output_root, timeout=timeout)
    if completed["status"] != "pass":
        archive_checkout_path = _archive_fallback_checkout_path(
            output_root=output_root,
            primary_checkout_path=checkout_path,
        )
        archive = _checkout_from_archive(
            archive_url=archive_url,
            checkout_path=archive_checkout_path,
            output_root=output_root,
            timeout=timeout,
            opener=archive_opener,
        )
        if archive.get("status") == "pass":
            materialized_checkout_path = Path(
                str(archive.get("checkout_path") or archive_checkout_path)
            )
            return _result(
                status="pass",
                reason="archive_checkout_created",
                message=(
                    "git clone failed, but repository checkout completed from "
                    "GitHub archive fallback."
                ),
                checkout_path=materialized_checkout_path,
                owner=owner,
                repo=repo,
                ref=ref,
                clone_url=clone_url,
                archive_url=archive_url,
                checkout_method="archive",
                commands=commands,
                returncode=completed["returncode"],
                stdout_preview=completed["stdout_preview"],
                stderr_preview=completed["stderr_preview"],
                timeout=completed["timeout"],
                archive_status=str(archive.get("status") or ""),
                archive_reason=str(archive.get("reason") or ""),
                archive_message=str(archive.get("message") or ""),
            )
        return _result(
            status=completed["status"],
            reason=completed["reason"],
            message=completed["message"],
            checkout_path=checkout_path,
            owner=owner,
            repo=repo,
            ref=ref,
            clone_url=clone_url,
            archive_url=archive_url,
            checkout_method="git",
            commands=commands,
            returncode=completed["returncode"],
            stdout_preview=completed["stdout_preview"],
            stderr_preview=completed["stderr_preview"],
            timeout=completed["timeout"],
            archive_status=str(archive.get("status") or ""),
            archive_reason=str(archive.get("reason") or ""),
            archive_message=str(archive.get("message") or ""),
            next_actions=[
                "Verify GitHub network access, repository visibility, and ref value.",
                "If the ref is a commit SHA not reachable by shallow fetch, provide --repository-test-root manually.",
            ],
        )

    if ref and _is_probable_commit_sha(ref):
        fetch_command = [git_path, "-C", str(checkout_path), "fetch", "--depth", str(depth), "origin", ref]
        checkout_command = [git_path, "-C", str(checkout_path), "checkout", ref]
        commands.extend([fetch_command, checkout_command])
        for command in (fetch_command, checkout_command):
            completed = _run_command(run, command, cwd=output_root, timeout=timeout)
            if completed["status"] != "pass":
                return _result(
                    status=completed["status"],
                    reason=completed["reason"],
                    message=completed["message"],
                    checkout_path=checkout_path,
                    owner=owner,
                    repo=repo,
                    ref=ref,
                    clone_url=clone_url,
                    archive_url=archive_url,
                    checkout_method="git",
                    commands=commands,
                    returncode=completed["returncode"],
                    stdout_preview=completed["stdout_preview"],
                    stderr_preview=completed["stderr_preview"],
                    timeout=completed["timeout"],
                    next_actions=[
                        "Provide a branch/tag ref for shallow clone or pass a prepared full checkout with --repository-test-root.",
                    ],
                )

    return _result(
        status="pass",
        reason="checkout_created",
        message="Repository checkout completed.",
        checkout_path=checkout_path,
        owner=owner,
        repo=repo,
        ref=ref,
        clone_url=clone_url,
        archive_url=archive_url,
        checkout_method="git",
        commands=commands,
    )


def render_repository_checkout_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# GitHub Repository Checkout",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Owner/Repo: `{_markdown_cell(payload.get('owner', ''))}/{_markdown_cell(payload.get('repo', ''))}`",
        f"- Ref: `{_markdown_cell(payload.get('ref', ''))}`",
        f"- Checkout Path: `{_markdown_cell(payload.get('checkout_path', ''))}`",
        f"- Checkout Method: `{_markdown_cell(payload.get('checkout_method', ''))}`",
        f"- Clone URL: `{_markdown_cell(payload.get('clone_url', ''))}`",
        f"- Archive URL: `{_markdown_cell(payload.get('archive_url', ''))}`",
        f"- Return Code: {_markdown_cell(payload.get('returncode'))}",
        f"- Timeout: {str(bool(payload.get('timeout', False))).lower()}",
        f"- Archive Status: `{_markdown_cell(payload.get('archive_status', ''))}`",
        f"- Archive Reason: `{_markdown_cell(payload.get('archive_reason', ''))}`",
        "",
        "## Message",
        "",
        _markdown_cell(payload.get("message", "")) or "none",
        "",
        "## Commands",
        "",
    ]
    for command in _list(payload.get("commands")):
        lines.append(f"- `{_markdown_cell(' '.join(str(part) for part in _list(command)))}`")
    if not _list(payload.get("commands")):
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {_markdown_cell(action)}")
    if not _list(payload.get("next_actions")):
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Stdout Preview",
            "",
            "```text",
            str(payload.get("stdout_preview") or ""),
            "```",
            "",
            "## Stderr Preview",
            "",
            "```text",
            str(payload.get("stderr_preview") or ""),
            "```",
        ]
    )
    return "\n".join(lines)


def write_repository_checkout_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_checkout.json"
    markdown_path = root / "repository_checkout.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_checkout_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_checkout_json": str(json_path),
        "repository_checkout_markdown": str(markdown_path),
    }


def _run_command(
    runner: Runner,
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    run = _run_subprocess_with_tree_timeout if runner is subprocess.run else runner
    try:
        completed = run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "fail",
            "reason": "timeout",
            "message": f"git command exceeded {timeout}s timeout.",
            "returncode": -1,
            "timeout": True,
            "stdout_preview": _preview(exc.stdout or ""),
            "stderr_preview": _preview(exc.stderr or ""),
        }
    except OSError as exc:
        return {
            "status": "fail",
            "reason": "git_execution_error",
            "message": str(exc),
            "returncode": -1,
            "timeout": False,
            "stdout_preview": "",
            "stderr_preview": str(exc),
        }
    return {
        "status": "pass" if completed.returncode == 0 else "fail",
        "reason": "git_returncode",
        "message": "git command completed." if completed.returncode == 0 else "git command failed.",
        "returncode": completed.returncode,
        "timeout": False,
        "stdout_preview": _preview(completed.stdout or ""),
        "stderr_preview": _preview(completed.stderr or ""),
    }


def _run_subprocess_with_tree_timeout(
    command: list[str],
    *,
    cwd: Path,
    capture_output: bool,
    text: bool,
    timeout: int,
    check: bool,
) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE if capture_output else None,
        "stderr": subprocess.PIPE if capture_output else None,
        "text": text,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _kill_process_tree_and_collect_output(process)
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout if stdout is not None else exc.stdout,
            stderr=stderr if stderr is not None else exc.stderr,
        ) from exc

    completed = subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )
    if check and completed.returncode:
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=stdout,
            stderr=stderr,
        )
    return completed


def _kill_process_tree_and_collect_output(
    process: subprocess.Popen,
) -> tuple[str | bytes | None, str | bytes | None]:
    if process.poll() is None:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate()


def _checkout_from_archive(
    *,
    archive_url: str,
    checkout_path: Path,
    output_root: Path,
    timeout: int,
    opener: UrlOpen | None,
) -> dict[str, Any]:
    if not archive_url:
        return {
            "status": "skipped",
            "reason": "missing_archive_url",
            "message": "Archive fallback URL could not be built.",
        }
    try:
        output_root_resolved = output_root.resolve()
        checkout_resolved = checkout_path.resolve()
        checkout_resolved.relative_to(output_root_resolved)
    except ValueError:
        return {
            "status": "fail",
            "reason": "unsafe_checkout_path",
            "message": "Checkout path is outside the output directory.",
        }
    if checkout_path.name not in {"repository_checkout", "repository_checkout_archive"}:
        return {
            "status": "fail",
            "reason": "unsafe_checkout_path",
            "message": (
                "Archive fallback only writes to repository_checkout or "
                "repository_checkout_archive."
            ),
        }
    archive_bytes, fetch_reason = _fetch_archive_bytes(
        archive_url,
        timeout=timeout,
        opener=opener,
    )
    if not archive_bytes:
        return {
            "status": "fail",
            "reason": fetch_reason,
            "message": "GitHub archive fallback could not be downloaded.",
        }
    try:
        with tempfile.TemporaryDirectory(dir=output_root) as temp_name:
            temp_root = Path(temp_name)
            extract_root = temp_root / "extracted"
            extract_root.mkdir()
            _extract_zip_safe(archive_bytes, extract_root)
            source_root = _archive_source_root(extract_root)
            if checkout_path.exists():
                shutil.rmtree(checkout_path)
            shutil.copytree(source_root, checkout_path)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        return {
            "status": "fail",
            "reason": f"archive_extract_failed:{type(exc).__name__}",
            "message": str(exc),
        }
    return {
        "status": "pass",
        "reason": "archive_checkout_created",
        "message": "Repository checkout completed from GitHub archive fallback.",
        "checkout_path": str(checkout_path),
    }


def _archive_fallback_checkout_path(
    *,
    output_root: Path,
    primary_checkout_path: Path,
) -> Path:
    if primary_checkout_path.exists():
        return output_root / "repository_checkout_archive"
    return primary_checkout_path


def _fetch_archive_bytes(
    archive_url: str,
    *,
    timeout: int,
    opener: UrlOpen | None,
    max_bytes: int = 250_000_000,
    chunk_size: int = 1_048_576,
) -> tuple[bytes, str]:
    if timeout <= 0:
        return b"", "archive_fetch_timeout"
    request = urllib.request.Request(
        archive_url,
        headers={"User-Agent": "code-intelligence-agent"},
    )
    open_url = opener or urllib.request.urlopen
    started = time.monotonic()
    deadline = started + timeout
    try:
        with open_url(request, timeout=timeout) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                if time.monotonic() > deadline:
                    return b"", "archive_fetch_timeout"
                remaining = max_bytes + 1 - total
                data = response.read(min(chunk_size, remaining))
                if not data:
                    break
                chunks.append(data)
                total += len(data)
                if total > max_bytes:
                    return b"", "archive_too_large"
                if time.monotonic() > deadline:
                    return b"", "archive_fetch_timeout"
    except (OSError, urllib.error.URLError) as exc:
        return b"", f"archive_fetch_failed:{type(exc).__name__}"
    return b"".join(chunks), "archive_fetched"


def _extract_zip_safe(archive_bytes: bytes, extract_root: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            target = (extract_root / member.filename).resolve()
            try:
                target.relative_to(extract_root.resolve())
            except ValueError as exc:
                raise ValueError("archive contains unsafe path") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _archive_source_root(extract_root: Path) -> Path:
    children = [path for path in extract_root.iterdir()]
    directories = [path for path in children if path.is_dir()]
    files = [path for path in children if path.is_file()]
    if len(directories) == 1 and not files:
        return directories[0]
    return extract_root


def _archive_url(*, owner: str, repo: str, ref: str | None) -> str:
    archive_ref = ref or "HEAD"
    return (
        f"https://codeload.github.com/{owner}/{repo}/zip/"
        f"{urllib.parse.quote(archive_ref, safe='')}"
    )


def _directory_has_entries(path: Path) -> bool:
    try:
        next(path.iterdir())
    except StopIteration:
        return False
    except OSError:
        return False
    return True


def _directory_has_entries_excluding(path: Path, excluded_names: set[str]) -> bool:
    try:
        for child in path.iterdir():
            if child.name not in excluded_names:
                return True
    except OSError:
        return False
    return False


def _result(
    *,
    status: str,
    reason: str,
    message: str,
    checkout_path: Path,
    owner: str,
    repo: str,
    ref: str | None,
    clone_url: str = "",
    archive_url: str = "",
    checkout_method: str = "",
    commands: list[list[str]] | None = None,
    returncode: int | None = None,
    stdout_preview: str = "",
    stderr_preview: str = "",
    timeout: bool = False,
    archive_status: str = "",
    archive_reason: str = "",
    archive_message: str = "",
    next_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "message": message,
        "checkout_path": str(checkout_path),
        "owner": owner,
        "repo": repo,
        "ref": ref or "",
        "clone_url": clone_url,
        "archive_url": archive_url,
        "checkout_method": checkout_method,
        "commands": commands or [],
        "returncode": returncode,
        "timeout": timeout,
        "stdout_preview": stdout_preview,
        "stderr_preview": stderr_preview,
        "archive_status": archive_status,
        "archive_reason": archive_reason,
        "archive_message": archive_message,
        "next_actions": next_actions or [],
    }


def _branch_args(ref: str | None) -> list[str]:
    if not ref or _is_probable_commit_sha(ref):
        return []
    return ["--branch", ref]


def _is_probable_commit_sha(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", value.strip()))


def _safe_github_part(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", value or ""))


def _preview(value: str, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
