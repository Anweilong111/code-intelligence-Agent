import json
import io
import tempfile
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from code_intelligence_agent.evaluation.github_discovery_fetcher import (
    GitHubAPIError,
    attach_import_report,
    build_code_search_query,
    fetch_code_search_discovery,
    fetch_tree_discovery,
    main,
    render_github_discovery_fetch_markdown,
)


def test_tree_discovery_fetcher_builds_payload_and_imports_sources():
    opener = FakeOpener(
        [
            {
                "sha": "abc123",
                "tree": [
                    {"path": "src/math.py", "type": "blob"},
                    {"path": "docs/readme.md", "type": "blob"},
                ],
            }
        ]
    )

    report = fetch_tree_discovery(
        owner="example",
        repo="project",
        ref="main",
        token="token-value",
        opener=opener,
    )
    imported = attach_import_report(report)

    assert report.mode == "tree"
    assert report.discovery_item_count == 2
    assert report.discovery_payload["owner"] == "example"
    assert report.discovery_payload["repo"] == "project"
    assert report.discovery_payload["ref"] == "main"
    assert opener.urls == [
        "https://api.github.com/repos/example/project/git/trees/main?recursive=1"
    ]
    assert opener.headers[0]["Authorization"] == "Bearer token-value"
    assert imported.import_report is not None
    assert imported.import_report.source_entries == [
        {
            "target_path": "math.py",
            "owner": "example",
            "repo": "project",
            "ref": "main",
            "source_path": "src/math.py",
        }
    ]


def test_tree_discovery_fetcher_resolves_default_branch_when_ref_is_missing():
    opener = FakeOpener(
        [
            {"default_branch": "develop"},
            {
                "sha": "def456",
                "tree": [
                    {"path": "src/service.py", "type": "blob"},
                ],
            },
        ]
    )

    report = fetch_tree_discovery(
        owner="example",
        repo="project",
        ref=None,
        opener=opener,
    )

    assert opener.urls == [
        "https://api.github.com/repos/example/project",
        "https://api.github.com/repos/example/project/git/trees/develop?recursive=1",
    ]
    assert report.discovery_payload["ref"] == "develop"
    assert report.discovery_payload["discovery"]["requested_ref"] is None
    assert report.discovery_payload["discovery"]["ref_source"] == "default_branch"


def test_tree_discovery_fetcher_passes_timeout_as_urlopen_keyword():
    opener = KeywordTimeoutFakeOpener(
        [
            {
                "sha": "abc123",
                "tree": [
                    {"path": "src/math.py", "type": "blob"},
                ],
            }
        ]
    )

    fetch_tree_discovery(
        owner="example",
        repo="project",
        ref="main",
        timeout=7,
        opener=opener,
    )

    assert opener.positional_args == [()]
    assert opener.timeouts == [7]


def test_tree_discovery_fetcher_wraps_rate_limit_http_error():
    opener = FailingHTTPErrorOpener(
        status=403,
        reason="rate limit exceeded",
        body={
            "message": "API rate limit exceeded",
            "documentation_url": "https://docs.github.com/rest",
        },
        headers={
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1729",
        },
    )

    with pytest.raises(GitHubAPIError) as exc_info:
        fetch_tree_discovery(
            owner="example",
            repo="project",
            ref="main",
            opener=opener,
        )

    message = str(exc_info.value)
    assert "HTTP 403" in message
    assert "rate_limit_remaining=0" in message
    assert "GITHUB_TOKEN" in message
    assert "from-discovery" in message
    assert exc_info.value.status_code == 403
    assert exc_info.value.rate_limit_reset == "1729"


def test_code_search_discovery_paginates_and_builds_query():
    opener = FakeOpener(
        [
            {
                "total_count": 3,
                "incomplete_results": False,
                "items": [
                    {
                        "path": "maths/average_mean.py",
                        "repository": {
                            "full_name": "TheAlgorithms/Python",
                            "default_branch": "master",
                        },
                    },
                    {
                        "path": "sorts/bubble_sort.py",
                        "repository": {
                            "full_name": "TheAlgorithms/Python",
                            "default_branch": "master",
                        },
                    },
                ],
            },
            {
                "total_count": 3,
                "incomplete_results": False,
                "items": [
                    {
                        "path": "README.md",
                        "repository": {
                            "full_name": "TheAlgorithms/Python",
                            "default_branch": "master",
                        },
                    }
                ],
            },
        ]
    )

    report = fetch_code_search_discovery(
        query="len(",
        owner="TheAlgorithms",
        repo="Python",
        ref="6c0462",
        per_page=2,
        max_pages=2,
        opener=opener,
    )
    imported = attach_import_report(report)
    first_query = parse_qs(urlparse(opener.urls[0]).query)["q"][0]
    second_page = parse_qs(urlparse(opener.urls[1]).query)["page"][0]

    assert first_query == "len( repo:TheAlgorithms/Python extension:py"
    assert second_page == "2"
    assert report.discovery_item_count == 3
    assert report.discovery_payload["ref"] == "6c0462"
    assert imported.import_report is not None
    assert [source["source_path"] for source in imported.import_report.source_entries] == [
        "maths/average_mean.py",
        "sorts/bubble_sort.py",
    ]


def test_code_search_query_does_not_duplicate_qualifiers():
    query = build_code_search_query(
        "mean repo:python/cpython extension:py",
        owner="TheAlgorithms",
        repo="Python",
        extension="py",
    )

    assert query == "mean repo:python/cpython extension:py"


def test_discovery_cli_writes_tree_outputs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        output_json = root / "fetch_report.json"
        output_markdown = root / "fetch_report.md"
        output_discovery = root / "discovery.json"
        output_sources = root / "sources.json"
        opener = FakeOpener(
            [
                {
                    "sha": "abc123",
                    "tree": [
                        {"path": "src/math.py", "type": "blob"},
                        {"path": "README.md", "type": "blob"},
                    ],
                }
            ]
        )

        main(
            [
                "tree",
                "example",
                "project",
                "--ref",
                "main",
                "--format",
                "markdown",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--output-discovery",
                str(output_discovery),
                "--output-sources",
                str(output_sources),
            ],
            opener=opener,
        )

        fetch_payload = json.loads(output_json.read_text(encoding="utf-8"))
        sources_payload = json.loads(output_sources.read_text(encoding="utf-8"))

        assert fetch_payload["mode"] == "tree"
        assert fetch_payload["discovery_item_count"] == 2
        assert sources_payload == {
            "sources": [
                {
                    "target_path": "math.py",
                    "owner": "example",
                    "repo": "project",
                    "ref": "main",
                    "source_path": "src/math.py",
                }
            ]
        }
        assert "# GitHub Discovery Fetch" in output_markdown.read_text(
            encoding="utf-8"
        )
        assert json.loads(output_discovery.read_text(encoding="utf-8"))["tree"][0][
            "path"
        ] == "src/math.py"


def test_discovery_markdown_embeds_import_summary():
    opener = FakeOpener(
        [
            {
                "tree": [
                    {"path": "src/math.py", "type": "blob"},
                ]
            }
        ]
    )

    report = attach_import_report(
        fetch_tree_discovery(
            owner="example",
            repo="project",
            ref="main",
            opener=opener,
        )
    )
    markdown = render_github_discovery_fetch_markdown(report)

    assert "Imported Sources: 1" in markdown
    assert "# GitHub Source Import" in markdown


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
        self.headers = []

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        self.headers.append(dict(request.header_items()))
        return FakeResponse(self.payloads.pop(0))


class KeywordTimeoutFakeOpener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.urls = []
        self.positional_args = []
        self.timeouts = []

    def __call__(self, request, *args, **kwargs):
        self.urls.append(request.full_url)
        self.positional_args.append(args)
        self.timeouts.append(kwargs.get("timeout"))
        return FakeResponse(self.payloads.pop(0))


class FailingHTTPErrorOpener:
    def __init__(self, *, status, reason, body, headers=None):
        self.status = status
        self.reason = reason
        self.body = body
        self.headers = headers or {}
        self.urls = []

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        body_bytes = json.dumps(self.body).encode("utf-8")
        raise urllib.error.HTTPError(
            request.full_url,
            self.status,
            self.reason,
            self.headers,
            io.BytesIO(body_bytes),
        )
