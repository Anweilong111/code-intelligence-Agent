from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from code_intelligence_agent.evaluation.github_source_importer import (
    GitHubSourceImportReport,
    import_github_sources,
    render_github_source_import_markdown,
)

UrlOpen = Callable[[urllib.request.Request, int], Any]


class GitHubAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str = "",
        rate_limit_remaining: str | None = None,
        rate_limit_reset: str | None = None,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.rate_limit_remaining = rate_limit_remaining
        self.rate_limit_reset = rate_limit_reset
        self.response_body = response_body


@dataclass(frozen=True)
class GitHubDiscoveryFetchReport:
    mode: str
    requested_urls: list[str]
    discovery_payload: dict[str, Any]
    import_report: GitHubSourceImportReport | None = None

    @property
    def discovery_item_count(self) -> int:
        if self.mode == "tree":
            return len(self.discovery_payload.get("tree", []))
        if self.mode == "search":
            return len(self.discovery_payload.get("items", []))
        return 0

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "mode": self.mode,
            "requested_urls": self.requested_urls,
            "discovery_item_count": self.discovery_item_count,
            "discovery_payload": self.discovery_payload,
        }
        if self.import_report is not None:
            payload["import_report"] = self.import_report.to_dict()
            payload["sources_payload"] = self.import_report.to_dict()[
                "sources_payload"
            ]
        return payload


class GitHubAPIClient:
    def __init__(
        self,
        token: str | None = None,
        api_base_url: str = "https://api.github.com",
        timeout: int = 20,
        opener: UrlOpen | None = None,
    ) -> None:
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
        self.requested_urls: list[str] = []

    def fetch_tree(
        self,
        owner: str,
        repo: str,
        ref: str | None,
        recursive: bool = True,
    ) -> dict[str, Any]:
        requested_ref = ref
        ref_source = "explicit"
        if not ref:
            ref = self.fetch_default_branch(owner, repo)
            ref_source = "default_branch"
        query = {"recursive": "1"} if recursive else {}
        payload = self.fetch_json(
            f"/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(ref, safe='')}",
            query=query,
        )
        payload["owner"] = owner
        payload["repo"] = repo
        payload["ref"] = ref
        payload.setdefault("tree", [])
        payload["discovery"] = {
            "mode": "tree",
            "owner": owner,
            "repo": repo,
            "ref": ref,
            "requested_ref": requested_ref,
            "ref_source": ref_source,
            "recursive": recursive,
        }
        return payload

    def fetch_default_branch(self, owner: str, repo: str) -> str:
        payload = self.fetch_json(f"/repos/{owner}/{repo}")
        default_branch = payload.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch.strip():
            raise ValueError(f"Repository {owner}/{repo} did not return default_branch")
        return default_branch.strip()

    def fetch_code_search(
        self,
        query: str,
        owner: str | None = None,
        repo: str | None = None,
        ref: str | None = None,
        extension: str | None = "py",
        per_page: int = 100,
        max_pages: int = 1,
    ) -> dict[str, Any]:
        search_query = build_code_search_query(
            query,
            owner=owner,
            repo=repo,
            extension=extension,
        )
        collected_items: list[dict[str, Any]] = []
        total_count = 0
        incomplete_results = False
        for page in range(1, max_pages + 1):
            payload = self.fetch_json(
                "/search/code",
                query={
                    "q": search_query,
                    "per_page": str(per_page),
                    "page": str(page),
                },
            )
            page_items = payload.get("items", [])
            if isinstance(page_items, list):
                collected_items.extend(
                    item for item in page_items if isinstance(item, dict)
                )
            total_count = int(payload.get("total_count", total_count) or 0)
            incomplete_results = bool(
                payload.get("incomplete_results", incomplete_results)
            )
            if len(page_items) < per_page:
                break
        discovery_payload: dict[str, Any] = {
            "items": collected_items,
            "total_count": total_count,
            "incomplete_results": incomplete_results,
            "query": search_query,
            "discovery": {
                "mode": "search",
                "query": search_query,
                "owner": owner,
                "repo": repo,
                "ref": ref,
                "extension": extension,
                "per_page": per_page,
                "max_pages": max_pages,
            },
        }
        if ref:
            discovery_payload["ref"] = ref
        return discovery_payload

    def fetch_json(
        self,
        path: str,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = self._url(path, query=query)
        self.requested_urls.append(url)
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with self.opener(request, timeout=self.timeout) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            raise _github_api_error(exc, url) from exc
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object from {url}")
        return payload

    def _url(self, path: str, query: dict[str, str] | None = None) -> str:
        encoded = urllib.parse.urlencode(query or {})
        url = f"{self.api_base_url}{path}"
        return f"{url}?{encoded}" if encoded else url

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "code-intelligence-agent",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def fetch_tree_discovery(
    owner: str,
    repo: str,
    ref: str | None,
    token: str | None = None,
    recursive: bool = True,
    api_base_url: str = "https://api.github.com",
    timeout: int = 20,
    opener: UrlOpen | None = None,
) -> GitHubDiscoveryFetchReport:
    client = GitHubAPIClient(
        token=token,
        api_base_url=api_base_url,
        timeout=timeout,
        opener=opener,
    )
    payload = client.fetch_tree(owner=owner, repo=repo, ref=ref, recursive=recursive)
    return GitHubDiscoveryFetchReport(
        mode="tree",
        requested_urls=client.requested_urls,
        discovery_payload=payload,
    )


def fetch_code_search_discovery(
    query: str,
    owner: str | None = None,
    repo: str | None = None,
    ref: str | None = None,
    token: str | None = None,
    extension: str | None = "py",
    per_page: int = 100,
    max_pages: int = 1,
    api_base_url: str = "https://api.github.com",
    timeout: int = 20,
    opener: UrlOpen | None = None,
) -> GitHubDiscoveryFetchReport:
    client = GitHubAPIClient(
        token=token,
        api_base_url=api_base_url,
        timeout=timeout,
        opener=opener,
    )
    payload = client.fetch_code_search(
        query=query,
        owner=owner,
        repo=repo,
        ref=ref,
        extension=extension,
        per_page=per_page,
        max_pages=max_pages,
    )
    return GitHubDiscoveryFetchReport(
        mode="search",
        requested_urls=client.requested_urls,
        discovery_payload=payload,
    )


def build_code_search_query(
    query: str,
    owner: str | None = None,
    repo: str | None = None,
    extension: str | None = "py",
) -> str:
    parts = [part for part in query.split() if part]
    if owner and repo and not any(part.startswith("repo:") for part in parts):
        parts.append(f"repo:{owner}/{repo}")
    if extension and not any(part.startswith("extension:") for part in parts):
        parts.append(f"extension:{extension.lstrip('.')}")
    return " ".join(parts)


def _github_api_error(exc: urllib.error.HTTPError, url: str) -> GitHubAPIError:
    body = _read_http_error_body(exc)
    remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
    reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
    message_parts = [
        f"GitHub API request failed with HTTP {exc.code}: {exc.reason}",
        f"url={url}",
    ]
    if remaining is not None:
        message_parts.append(f"rate_limit_remaining={remaining}")
    if reset is not None:
        message_parts.append(f"rate_limit_reset={reset}")
    if exc.code in {403, 429}:
        message_parts.append(
            "If this is a rate limit, set GITHUB_TOKEN or pass --token-env "
            "with a token environment variable; use from-discovery with a "
            "saved discovery JSON for reproducible/offline reruns."
        )
    if body:
        message_parts.append(f"body={body[:500]}")
    return GitHubAPIError(
        "; ".join(message_parts),
        status_code=exc.code,
        url=url,
        rate_limit_remaining=remaining,
        rate_limit_reset=reset,
        response_body=body,
    )


def _read_http_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        data = exc.read()
    except Exception:
        return ""
    if not data:
        return ""
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return data.decode("utf-8", errors="replace").strip()
    if isinstance(payload, dict):
        message = payload.get("message")
        documentation_url = payload.get("documentation_url")
        parts = [str(message)] if message else []
        if documentation_url:
            parts.append(f"documentation_url={documentation_url}")
        return "; ".join(parts)
    return str(payload)


def attach_import_report(
    report: GitHubDiscoveryFetchReport,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    preserve_paths: bool = False,
    target_prefix: str = "",
) -> GitHubDiscoveryFetchReport:
    import_report = import_github_sources(
        report.discovery_payload,
        source_path=f"github_{report.mode}_discovery",
        include=include,
        exclude=exclude,
        preserve_paths=preserve_paths,
        target_prefix=target_prefix,
    )
    return GitHubDiscoveryFetchReport(
        mode=report.mode,
        requested_urls=report.requested_urls,
        discovery_payload=report.discovery_payload,
        import_report=import_report,
    )


def render_github_discovery_fetch_markdown(
    report: GitHubDiscoveryFetchReport,
) -> str:
    lines = [
        "# GitHub Discovery Fetch",
        "",
        f"- Mode: `{report.mode}`",
        f"- Requested URLs: {len(report.requested_urls)}",
        f"- Discovery Items: {report.discovery_item_count}",
    ]
    if report.import_report is not None:
        lines.extend(
            [
                f"- Imported Sources: {report.import_report.source_count}",
                f"- Skipped Items: {report.import_report.skipped_count}",
            ]
        )
    lines.extend(["", "| URL |", "| --- |"])
    for url in report.requested_urls:
        lines.append(f"| {_markdown_cell(url)} |")
    if report.import_report is not None:
        lines.extend(["", render_github_source_import_markdown(report.import_report)])
    return "\n".join(lines)


def _token_from_env(env_name: str | None) -> str | None:
    if not env_name:
        return None
    return os.environ.get(env_name)


def _write_outputs(
    report: GitHubDiscoveryFetchReport,
    format_name: str,
    output_json: str | None = None,
    output_markdown: str | None = None,
    output_discovery: str | None = None,
    output_import_json: str | None = None,
    output_import_markdown: str | None = None,
    output_sources: str | None = None,
) -> None:
    payload = report.to_dict()
    json_report = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown_report = render_github_discovery_fetch_markdown(report)
    if output_json:
        Path(output_json).write_text(json_report, encoding="utf-8")
    if output_markdown:
        Path(output_markdown).write_text(markdown_report, encoding="utf-8")
    if output_discovery:
        Path(output_discovery).write_text(
            json.dumps(report.discovery_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if report.import_report is not None:
        import_payload = report.import_report.to_dict()
        if output_import_json:
            Path(output_import_json).write_text(
                json.dumps(import_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        if output_import_markdown:
            Path(output_import_markdown).write_text(
                render_github_source_import_markdown(report.import_report),
                encoding="utf-8",
            )
        if output_sources:
            Path(output_sources).write_text(
                json.dumps(
                    import_payload["sources_payload"],
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
    if format_name == "markdown":
        print(markdown_report)
    else:
        print(json_report)


def _add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="Environment variable containing a GitHub token.",
    )
    parser.add_argument(
        "--api-base-url",
        default="https://api.github.com",
        help="GitHub API base URL.",
    )
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout.")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    parser.add_argument("--output-json", help="Optional full report JSON path.")
    parser.add_argument("--output-markdown", help="Optional markdown report path.")
    parser.add_argument(
        "--output-discovery",
        help="Optional raw discovery payload JSON path.",
    )
    parser.add_argument(
        "--output-import-json",
        help="Optional importer report JSON path.",
    )
    parser.add_argument(
        "--output-import-markdown",
        help="Optional importer markdown report path.",
    )
    parser.add_argument(
        "--output-sources",
        help="Optional imported sources JSON path.",
    )
    parser.add_argument(
        "--include",
        action="append",
        help="Importer include glob. Defaults to '*.py'. May be repeated.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        help="Importer exclude glob. May be repeated.",
    )
    parser.add_argument(
        "--preserve-paths",
        action="store_true",
        help="Keep repository-relative paths as target paths.",
    )
    parser.add_argument(
        "--target-prefix",
        default="",
        help="Optional prefix added to imported target paths.",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch GitHub tree/search discovery payloads and optionally convert "
            "them into benchmark source manifests."
        )
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    tree = subparsers.add_parser("tree", help="Fetch a repository tree payload.")
    tree.add_argument("owner")
    tree.add_argument("repo")
    tree.add_argument("--ref", required=True, help="Commit, tag, or branch.")
    tree.add_argument(
        "--no-recursive",
        action="store_true",
        help="Disable recursive tree fetch.",
    )
    _add_shared_args(tree)

    search = subparsers.add_parser("search", help="Fetch GitHub code search payload.")
    search.add_argument("query", help="GitHub code search query terms.")
    search.add_argument("--owner", help="Repo owner for a repo qualifier.")
    search.add_argument("--repo", help="Repo name for a repo qualifier.")
    search.add_argument("--ref", help="Ref to attach for downstream raw fetches.")
    search.add_argument(
        "--extension",
        default="py",
        help="Extension qualifier to add unless the query already has one.",
    )
    search.add_argument("--per-page", type=int, default=100)
    search.add_argument("--max-pages", type=int, default=1)
    _add_shared_args(search)
    return parser


def main(argv: list[str] | None = None, opener: UrlOpen | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    token = _token_from_env(args.token_env)
    try:
        if args.mode == "tree":
            report = fetch_tree_discovery(
                owner=args.owner,
                repo=args.repo,
                ref=args.ref,
                token=token,
                recursive=not args.no_recursive,
                api_base_url=args.api_base_url,
                timeout=args.timeout,
                opener=opener,
            )
        else:
            report = fetch_code_search_discovery(
                query=args.query,
                owner=args.owner,
                repo=args.repo,
                ref=args.ref,
                token=token,
                extension=args.extension,
                per_page=args.per_page,
                max_pages=args.max_pages,
                api_base_url=args.api_base_url,
                timeout=args.timeout,
                opener=opener,
            )
    except GitHubAPIError as exc:
        parser.exit(1, f"error: {exc}\n")
    if (
        args.output_sources
        or args.output_import_json
        or args.output_import_markdown
    ):
        report = attach_import_report(
            report,
            include=args.include,
            exclude=args.exclude,
            preserve_paths=args.preserve_paths,
            target_prefix=args.target_prefix,
        )
    _write_outputs(
        report,
        args.format,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
        output_discovery=args.output_discovery,
        output_import_json=args.output_import_json,
        output_import_markdown=args.output_import_markdown,
        output_sources=args.output_sources,
    )


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


if __name__ == "__main__":
    main()
