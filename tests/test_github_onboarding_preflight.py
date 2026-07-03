import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.github_onboarding_preflight import (
    _recover_auto_scoped_candidate_sample,
    preflight_from_discovery,
    preflight_tree,
    render_github_onboarding_preflight_markdown,
)


def test_preflight_from_discovery_recommends_smoke_runner_entry():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_mixed_discovery(root)
        output_dir = root / "preflight"

        report = preflight_from_discovery(
            json.loads(discovery.read_text(encoding="utf-8")),
            output_dir,
            source=str(discovery),
            sample_sources=5,
            max_candidates=7,
            run_name="mixed_repo",
        )
        markdown = render_github_onboarding_preflight_markdown(report)
        recommended_manifest = json.loads(
            Path(report.output_paths["recommended_manifest"]).read_text(
                encoding="utf-8"
            )
        )

        assert report.status == "pass"
        assert report.ready_for_smoke is True
        assert report.discovery_item_count == 3
        assert report.imported_source_count == 2
        assert report.skipped_source_count == 1
        assert report.sampled_source_count == 2
        assert report.generated_candidate_count >= 2
        assert "missing_len_zero_guard" in report.recommended_run["recipe"]
        assert "dict_missing_key_guard" in report.recommended_run["recipe"]
        assert report.recommended_run["mode"] == "from-discovery"
        assert report.recommended_run["preset"] == "smoke"
        assert report.recommended_run["max_sources"] == 5
        assert report.recommended_run["max_candidates"] == 7
        assert report.recommended_run["fallback"]["max_sources"] == 50
        assert recommended_manifest["runs"][0]["name"] == "mixed_repo"
        assert Path(report.output_paths["preflight_json"]).exists()
        assert Path(report.output_paths["source_mining_markdown"]).exists()
        assert "GitHub Onboarding Preflight" in markdown
        assert "github_onboarding_smoke_runner" in markdown


def test_preflight_repository_profile_detects_src_layout_and_test_command():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_src_layout_discovery(root)
        output_dir = root / "preflight"

        report = preflight_from_discovery(
            json.loads(discovery.read_text(encoding="utf-8")),
            output_dir,
            source=str(discovery),
            sample_sources=5,
            run_name="src_layout_repo",
        )
        markdown = render_github_onboarding_preflight_markdown(report)

        profile = report.repository_profile
        assert profile["src_layout_packages"] == ["demo"]
        assert profile["recommended_target_prefix"] == "demo"
        assert profile["recommended_test_command"] == "python -m pytest"
        assert profile["doctor_status"] == "pass"
        assert profile["doctor_blocker"] == "none"
        assert profile["test_source_count"] == 1
        assert profile["project_config_files"] == ["pyproject.toml"]
        assert "src-layout package detected" in profile["layout_hints"][0]
        assert report.recommended_run["target_prefix"] == "demo"
        assert report.recommended_run["project_profile"] == {
            "recommended_test_command": "python -m pytest",
            "recommended_target_prefix": "demo",
            "test_source_count": 1,
            "project_config_count": 1,
            "doctor_status": "pass",
            "doctor_blocker": "none",
            "doctor_score": 1.0,
            "doctor_next_action": (
                "Run preflight smoke or repository-test repair using the recommended command."
            ),
        }
        assert "Repository Doctor: status=pass; blocker=none; score=1.00" in markdown
        assert "Recommended Test Command" in markdown
        assert "Repository Profile" in markdown


def test_preflight_auto_scoped_include_uses_sampled_source_paths():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        average_source = root / "average_mean.py"
        average_source.write_text(
            "def mean(nums):\n"
            "    if not nums:\n"
            "        raise ValueError(\"List is empty\")\n"
            "    return sum(nums) / len(nums)\n",
            encoding="utf-8",
        )
        playfair_source = root / "playfair_cipher.py"
        playfair_source.write_text(
            "def encode_pair(table, row1, col1, row2, col2):\n"
            "    if row1 == row2:\n"
            "        return table[row1 * 5 + (col1 + 1) % 5]\n"
            "    return table[row2 * 5 + col1]\n",
            encoding="utf-8",
        )
        running_key_source = root / "running_key_cipher.py"
        running_key_source.write_text(
            "def running_key_encrypt(key, plaintext):\n"
            "    key_length = len(key)\n"
            "    ciphertext = []\n"
            "    for i, char in enumerate(plaintext):\n"
            "        ciphertext.append(key[i % key_length])\n"
            "    return ''.join(ciphertext)\n\n"
            "def test_running_key_encrypt():\n"
            "    pass\n",
            encoding="utf-8",
        )
        cipher_source = root / "gronsfeld_cipher.py"
        cipher_source.write_text(
            "from string import ascii_uppercase\n\n"
            "def gronsfeld(text, key):\n"
            "    \"\"\"\n"
            "    >>> gronsfeld('hello', '')\n"
            "    Traceback (most recent call last):\n"
            "      ...\n"
            "    ZeroDivisionError: division by zero\n"
            "    \"\"\"\n"
            "    if not key:\n"
            "        raise ValueError(\"empty key\")\n"
            "    ascii_len = len(ascii_uppercase)\n"
            "    key_len = len(key)\n"
            "    keys = [int(char) for char in key]\n"
            "    encrypted = ''\n"
            "    for i, char in enumerate(text.upper()):\n"
            "        encrypted += ascii_uppercase[(ascii_uppercase.index(char) + keys[i % key_len]) % ascii_len]\n"
            "    return encrypted\n",
            encoding="utf-8",
        )
        discovery = root / "repair_rank.discovery.json"
        discovery.write_text(
            json.dumps(
                {
                    "files": [
                        _source_item(
                            average_source,
                            "maths/average_mean.py",
                            "average_mean.py",
                        ),
                        _source_item(
                            playfair_source,
                            "ciphers/playfair_cipher.py",
                            "playfair_cipher.py",
                        ),
                        _source_item(
                            running_key_source,
                            "ciphers/running_key_cipher.py",
                            "running_key_cipher.py",
                        ),
                        _source_item(
                            cipher_source,
                            "ciphers/gronsfeld_cipher.py",
                            "gronsfeld_cipher.py",
                        ),
                    ]
                }
            ),
            encoding="utf-8",
        )
        output_dir = root / "preflight"

        report = preflight_from_discovery(
            json.loads(discovery.read_text(encoding="utf-8")),
            output_dir,
            source=str(discovery),
            sample_sources=1,
            max_candidates=3,
            auto_scoped_include=True,
            run_name="mixed_repo",
        )

        assert report.sampled_source_count == 1
        assert report.recommended_run["auto_scoped_include"] is True
        assert report.recommended_run["auto_scoped_include_count"] == 1
        assert report.recommended_run["auto_scoped_include_source"] == (
            "preflight_sampled_sources"
        )
        assert report.recommended_run["include"] == ["maths/average_mean.py"]


def test_preflight_auto_scoped_include_prefers_generated_recipe_source():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        hooks_source = root / "_hooks.py"
        hooks_source.write_text(
            "class HookCaller:\n"
            "    def __init__(self):\n"
            "        self._name2hook = {}\n\n"
            "    def get_hook(self, key):\n"
            "        if key in self._name2hook:\n"
            "            return self._name2hook[key]\n"
            "        return None\n",
            encoding="utf-8",
        )
        tracing_source = root / "_tracing.py"
        tracing_source.write_text(
            "class TagTracerSub:\n"
            "    def __init__(self, root, tags):\n"
            "        self.root = root\n"
            "        self.tags = tags\n\n"
            "    def get(self, name: str):\n"
            "        return self.__class__(self.root, self.tags + (name,))\n\n\n"
            "class TagTracer:\n"
            "    def get(self, name: str):\n"
            "        return TagTracerSub(self, (name,))\n",
            encoding="utf-8",
        )
        discovery = root / "pluggy.discovery.json"
        discovery.write_text(
            json.dumps(
                {
                    "files": [
                        _source_item(
                            hooks_source,
                            "src/pluggy/_hooks.py",
                            "_hooks.py",
                        ),
                        _source_item(
                            tracing_source,
                            "src/pluggy/_tracing.py",
                            "_tracing.py",
                        ),
                    ]
                }
            ),
            encoding="utf-8",
        )

        report = preflight_from_discovery(
            json.loads(discovery.read_text(encoding="utf-8")),
            root / "preflight",
            source=str(discovery),
            recipes=["dict_missing_key_guard", "mutable_default_arg"],
            sample_sources=1,
            max_candidates=3,
            auto_scoped_include=True,
            run_name="pluggy_like",
        )

        assert report.ready_for_smoke is True
        assert report.generated_candidate_count == 1
        assert report.recommended_run["include"] == ["src/pluggy/_tracing.py"]


def test_preflight_auto_scoped_include_prefers_deterministic_api_candidate():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        compat_source = root / "_compat.py"
        compat_source.write_text(
            "def strip_ansi(value):\n"
            "    try:\n"
            "        return value.encode('ascii').decode('ascii')\n"
            "    except Exception:\n"
            "        pass\n"
            "    return value\n\n"
            "def term_len(value):\n"
            "    try:\n"
            "        return len(value)\n"
            "    except Exception:\n"
            "        pass\n"
            "    return 0\n",
            encoding="utf-8",
        )
        formatting_source = root / "formatting.py"
        formatting_source.write_text(
            "def join_options(options):\n"
            "    rv = []\n"
            "    any_prefix_is_slash = False\n"
            "    for opt in options:\n"
            "        prefix = '--' if opt.startswith('--') else opt[:1]\n"
            "        rv.append((len(prefix), opt))\n"
            "    rv.sort(key=lambda x: x[0])\n"
            "    return ', '.join(x[1] for x in rv), any_prefix_is_slash\n",
            encoding="utf-8",
        )
        discovery = root / "click.discovery.json"
        discovery.write_text(
            json.dumps(
                {
                    "files": [
                        _source_item(
                            compat_source,
                            "src/click/_compat.py",
                            "_compat.py",
                        ),
                        _source_item(
                            formatting_source,
                            "src/click/formatting.py",
                            "formatting.py",
                        ),
                    ]
                }
            ),
            encoding="utf-8",
        )

        report = preflight_from_discovery(
            json.loads(discovery.read_text(encoding="utf-8")),
            root / "preflight",
            source=str(discovery),
            sample_sources=1,
            max_candidates=3,
            auto_scoped_include=True,
            run_name="click_like",
        )

        assert report.ready_for_smoke is True
        assert report.generated_candidate_count >= 1
        assert (
            report.mining_summary["rule_counts"]["inplace_api_return_value"] >= 1
        )
        assert report.recommended_run["include"] == ["src/click/formatting.py"]


def test_preflight_auto_scoped_include_recovers_when_first_sample_has_no_candidates():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        utils_source = root / "utils.py"
        utils_source.write_text(
            "def utility_text(values):\n"
            "    \"\"\"mentions len(values) / 2 and mapping.get(key, 0).\"\"\"\n"
            "    return tuple(values)\n",
            encoding="utf-8",
        )
        tbtools_source = root / "tbtools.py"
        tbtools_source.write_text(
            "def middle(values):\n"
            "    index = len(values) // 2\n"
            "    return values[index]\n",
            encoding="utf-8",
        )
        discovery = root / "werkzeug_like.discovery.json"
        discovery.write_text(
            json.dumps(
                {
                    "files": [
                        _source_item(
                            utils_source,
                            "src/werkzeug/utils.py",
                            "utils.py",
                        ),
                        _source_item(
                            tbtools_source,
                            "src/werkzeug/debug/tbtools.py",
                            "tbtools.py",
                        ),
                    ]
                }
            ),
            encoding="utf-8",
        )

        report = preflight_from_discovery(
            json.loads(discovery.read_text(encoding="utf-8")),
            root / "preflight",
            source=str(discovery),
            sample_sources=1,
            max_candidates=3,
            auto_scoped_include=True,
            run_name="werkzeug_like",
        )

        assert report.ready_for_smoke is True
        assert report.recommended_run["include"] == [
            "src/werkzeug/debug/tbtools.py"
        ]
        assert (
            report.recommended_run["auto_scoped_include_source"]
            == "preflight_candidate_recovery"
        )
        assert report.recipe_selection["mode"] == "auto_candidate_recovery"
        assert report.mining_summary["rule_counts"] == {
            "stringified_numeric_value": 1
        }


def test_preflight_candidate_recovery_runs_when_first_sample_only_has_static_overlay_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        risky_source = root / "index_tools.py"
        risky_source.write_text(
            "def shift(values):\n"
            "    shifted = []\n"
            "    for index in range(len(values)):\n"
            "        shifted.append(values[index + 1])\n"
            "    return shifted\n",
            encoding="utf-8",
        )
        guard_source = root / "core.py"
        guard_source.write_text(
            "def display(url):\n"
            "    if not url:\n"
            "        raise ValueError(\"empty\")\n"
            "    return url[0]\n",
            encoding="utf-8",
        )
        discovery = root / "overlay_recovery.discovery.json"
        discovery.write_text(
            json.dumps(
                {
                    "files": [
                        _source_item(
                            risky_source,
                            "src/pkg/index_tools.py",
                            "pkg_index_tools.py",
                        ),
                        _source_item(
                            guard_source,
                            "src/pkg/core.py",
                            "pkg_core.py",
                        ),
                    ]
                }
            ),
            encoding="utf-8",
        )

        report = preflight_from_discovery(
            json.loads(discovery.read_text(encoding="utf-8")),
            root / "preflight",
            source=str(discovery),
            sample_sources=1,
            max_candidates=3,
            auto_scoped_include=True,
            run_name="overlay_recovery",
        )

        assert report.ready_for_smoke is True
        assert report.recommended_run["include"] == ["src/pkg/core.py"]
        assert (
            report.recommended_run["auto_scoped_include_source"]
            == "preflight_candidate_recovery"
        )
        assert report.recipe_selection["mode"] == "auto_candidate_recovery"
        assert report.mining_summary["rule_counts"] == {
            "always_true_len_check": 1
        }


def test_preflight_candidate_recovery_prefers_main_package_over_examples():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        parser_source = root / "parser.py"
        parser_source.write_text(
            "def parse(value):\n"
            "    return str(value).strip()\n",
            encoding="utf-8",
        )
        example_source = root / "example_views.py"
        example_source.write_text(
            "class NotFound(Exception):\n"
            "    pass\n\n\n"
            "def display(url):\n"
            "    if not url:\n"
            "        raise NotFound()\n"
            "    return url[0]\n",
            encoding="utf-8",
        )
        package_source = root / "core.py"
        package_source.write_text(
            "def display(url):\n"
            "    if not url:\n"
            "        raise ValueError(\"empty\")\n"
            "    return url[0]\n",
            encoding="utf-8",
        )
        discovery = root / "main_package.discovery.json"
        discovery.write_text(
            json.dumps(
                {
                    "files": [
                        _source_item(
                            parser_source,
                            "src/pkg/parser.py",
                            "pkg_parser.py",
                        ),
                        _source_item(
                            example_source,
                            "examples/demo/views.py",
                            "examples_demo_views.py",
                        ),
                        _source_item(
                            package_source,
                            "src/pkg/core.py",
                            "pkg_core.py",
                        ),
                    ]
                }
            ),
            encoding="utf-8",
        )

        report = preflight_from_discovery(
            json.loads(discovery.read_text(encoding="utf-8")),
            root / "preflight",
            source=str(discovery),
            sample_sources=1,
            max_candidates=3,
            auto_scoped_include=True,
            run_name="main_package",
        )

        assert report.ready_for_smoke is True
        assert report.recommended_run["include"] == ["src/pkg/core.py"]
        assert (
            report.recommended_run["auto_scoped_include_source"]
            == "preflight_candidate_recovery"
        )
        assert report.recipe_selection["mode"] == "auto_candidate_recovery"
        assert report.mining_summary["rule_counts"] == {
            "always_true_len_check": 1
        }


def test_preflight_candidate_recovery_prefers_deterministic_rule_over_safer_broad_source():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        api_source = root / "api.py"
        api_source.write_text(
            "def fetch(url):\n"
            "    if url == []:\n"
            "        raise ValueError(\"bad url\")\n"
            "    return \"ok\"\n",
            encoding="utf-8",
        )
        models_source = root / "models.py"
        models_source.write_text(
            "from .compat import urlparse\n"
            "from .exceptions import MissingSchema\n"
            "from .hooks import default_hooks\n"
            "from .structures import CaseInsensitiveDict\n"
            "from .utils import requote_uri\n\n\n"
            "class PreparedRequest:\n"
            "    def prepare_url(self, url, params):\n"
            "        scheme = url\n"
            "        if not scheme:\n"
            "            raise MissingSchema()\n"
            "        return params\n",
            encoding="utf-8",
        )
        api_item = _source_item(api_source, "requests/api.py", "api.py")
        api_item["source_path"] = "requests/api.py"
        models_item = _source_item(models_source, "requests/models.py", "models.py")
        models_item["source_path"] = "requests/models.py"

        recovery = _recover_auto_scoped_candidate_sample(
            [api_item, models_item],
            requested_recipes=None,
            source_cache_dir=root / "cache",
            sample_sources=1,
            max_auto_recipes=3,
            enabled=True,
        )

        assert recovery is not None
        selected_sources, selected_recipes, recipe_selection, mining_report = recovery
        assert [source["source_path"] for source in selected_sources] == [
            "requests/models.py"
        ]
        assert selected_recipes == ["always_true_len_check"]
        assert recipe_selection["candidate_recovery"]["rule_counts"] == {
            "always_true_len_check": 1
        }
        assert mining_report.rule_counts == {"always_true_len_check": 1}


def test_preflight_reports_failure_when_no_python_sources_are_imported():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        readme = root / "README.md"
        readme.write_text("# docs\n", encoding="utf-8")
        output_dir = root / "preflight"

        report = preflight_from_discovery(
            {
                "files": [
                    {
                        "path": "README.md",
                        "raw_url": str(readme),
                        "target_path": "README.md",
                    }
                ]
            },
            output_dir,
            source="docs_only",
        )

        assert report.status == "fail"
        assert report.ready_for_smoke is False
        assert report.imported_source_count == 0
        assert report.generated_candidate_count == 0
        assert [issue["code"] for issue in report.issues] == ["no_python_sources"]
        assert "Fix discovery/import filters" in report.next_actions[0]


def test_preflight_cli_writes_json_and_markdown_reports():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        discovery = _write_mixed_discovery(root)
        output_dir = root / "preflight"
        output_json = root / "preflight.json"
        output_markdown = root / "preflight.md"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.github_onboarding_preflight",
                "from-discovery",
                str(discovery),
                str(output_dir),
                "--sample-sources",
                "5",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        saved = json.loads(output_json.read_text(encoding="utf-8"))
        markdown = output_markdown.read_text(encoding="utf-8")

        assert completed.returncode == 0
        assert saved["ready_for_smoke"] is True
        assert saved["recommended_manifest"]["runs"][0]["mode"] == "from-discovery"
        assert "preflight_recommended_manifest.json" in saved["recommended_commands"][0]
        assert "mixed_repo" not in saved["recommended_manifest"]["runs"][0]["name"]
        assert "GitHub Onboarding Preflight" in markdown
        assert "generated_candidate_count" not in completed.stderr


def test_preflight_tree_uses_github_discovery_and_emits_tree_run():
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

        report = preflight_tree(
            "example",
            "algorithms",
            root / "preflight_tree",
            ref="main",
            opener=opener,
            sample_sources=3,
        )

        assert opener.urls == [
            "https://api.github.com/repos/example/algorithms/git/trees/main?recursive=1"
        ]
        assert report.status == "pass"
        assert report.mode == "tree"
        assert report.recommended_run["mode"] == "tree"
        assert report.recommended_run["owner"] == "example"
        assert report.recommended_run["repo"] == "algorithms"
        assert report.recommended_run["ref"] == "main"
        assert report.generated_candidate_count >= 1


def _write_mixed_discovery(root: Path) -> Path:
    average_source = root / "average_mean.py"
    average_source.write_text(
        "def mean(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    score_source = root / "score_lookup.py"
    score_source.write_text(
        "def score_for(scores, name):\n"
        "    return scores.get(name, 0)\n",
        encoding="utf-8",
    )
    readme = root / "README.md"
    readme.write_text("# docs\n", encoding="utf-8")
    discovery = root / "mixed.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    _source_item(
                        average_source,
                        "maths/average_mean.py",
                        "average_mean.py",
                    ),
                    _source_item(
                        score_source,
                        "metrics/score_lookup.py",
                        "score_lookup.py",
                    ),
                    {
                        "path": "README.md",
                        "raw_url": str(readme),
                        "target_path": "README.md",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery


def _write_src_layout_discovery(root: Path) -> Path:
    package_init = root / "__init__.py"
    package_init.write_text("", encoding="utf-8")
    average_source = root / "average.py"
    average_source.write_text(
        "def mean(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    test_source = root / "test_average.py"
    test_source.write_text(
        "from demo.average import mean\n\n"
        "\n"
        "def test_mean_empty_raises():\n"
        "    try:\n"
        "        mean([])\n"
        "    except ValueError:\n"
        "        return\n"
        "    raise AssertionError('expected ValueError')\n",
        encoding="utf-8",
    )
    discovery = root / "src_layout.discovery.json"
    discovery.write_text(
        json.dumps(
            {
                "files": [
                    _source_item(
                        package_init,
                        "src/demo/__init__.py",
                        "src/demo/__init__.py",
                    ),
                    _source_item(
                        average_source,
                        "src/demo/average.py",
                        "src/demo/average.py",
                    ),
                    _source_item(
                        test_source,
                        "tests/test_average.py",
                        "tests/test_average.py",
                    ),
                    {
                        "path": "pyproject.toml",
                        "owner": "example",
                        "repo": "demo",
                        "ref": "main",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return discovery


def _source_item(raw_source: Path, source_path: str, target_path: str) -> dict:
    return {
        "path": source_path,
        "raw_url": str(raw_source),
        "target_path": target_path,
        "owner": "example",
        "repo": "mixed",
        "ref": "v1.0.0",
        "sha256": hashlib.sha256(raw_source.read_bytes()).hexdigest(),
        "license": "MIT",
    }


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
