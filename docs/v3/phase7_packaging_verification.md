# V3 Phase 7 Packaging and Clean-Archive Verification

## Result

V3 architecture, demonstration, and Chinese resume/interview material now use
the completed 120-trial live evaluation. The clean source archive gate verifies
release-focused tests, release hygiene, unified evaluation CLI behavior, and
Agent CLI discovery without packaging ignored paid-run workspaces.

The final worktree regression passed `1412` tests with `2` explicit Windows
symlink fixture skips in `676.95` seconds. The final Git candidate set passed
all `5/5` hygiene checks across `527` files.

The complete V3 aggregate status is `pass`: LLM pass@1/pass@3 is 0.40/0.50 and
Hybrid is 0.30/0.45. Those numbers come from the validated external live
artifact and its RunRecord hash, not from the archive or documentation checks.

## Presentation Material

| Deliverable | Status | Scope |
| --- | --- | --- |
| V3 architecture and Agent design | pass | Three Mermaid diagrams, execution planes, FinalScore, patch, memory, security, and evidence map |
| 10-minute demonstration guide | pass | Offline main path, optional repository Agent, optional paid smoke, and failure fallback |
| Chinese resume/interview pack | pass | Frozen live metrics, formulas, Hybrid analysis, Q&A, limitations, and evidence links |
| README entry points | pass | Direct links to all V3 packaging documents |

The material uses the measured 20-case benchmark, 19/20 startup, localization
Top-1/3/5 of 0.60/0.80/1.00, Rule pass@1 of 0/20, and controlled
memory/security evidence, plus the completed 120/120 live trials and 423/423
RunRecord audit.

## Clean Archive

| Check | Result |
| --- | --- |
| Tested commit | `408d8f1383bf77bdab121c04a308e19e7ae6306c` |
| ZIP SHA-256 | `292ad4ca1bed186f76e35a3cde0f7dd9202b10c0e29a5c4e6ebe96e6d8fc2f02` |
| Git metadata | absent |
| Top-level outputs | 0 |
| Source files | 520 |
| Candidate source | `filesystem_snapshot` |
| Release hygiene | 5/5 pass over 520 candidates |
| Release-focused tests | 33 passed in 5.09 s |
| Unified release CLI | offline pass, complete pending |
| Agent CLI | help/route pass |

The focused test set covers V3 packaging documents, unified release evaluation,
Phase 6 memory/security evaluation, Git/snapshot candidate discovery, README
consistency, and top-level CLI routing.

## Resolved Findings

The clean-archive run found and fixed three release-only issues:

1. `git ls-files` could discover an outer repository while the nested archive
   path was ignored, producing an empty candidate set and a false hygiene pass.
2. Importing the audit module generated `__pycache__` before filesystem
   discovery, causing a false cache-contamination failure.
3. The original hygiene test required `candidate_source=git`, so the test
   contract itself could not run in a source archive.

The final implementation uses Git candidates only when Git top-level exactly
matches the audit root. Otherwise it scans the snapshot, excludes runtime cache
directories, and still rejects packaged `.pyc` files outside those directories,
outputs, coverage, secrets, binary documents, and unsafe public claims.

## Reproduction

Create the archive from the tested commit:

```powershell
git archive --format=zip `
  --output=source.zip `
  408d8f1383bf77bdab121c04a308e19e7ae6306c
```

After extracting into a directory without `.git`, run:

```powershell
python -m code_intelligence_agent.evaluation.release_hygiene_audit `
  ../hygiene `
  --root . `
  --require-pass `
  --format json

python -m pytest -q `
  tests/test_v3_release_packaging_docs.py `
  tests/test_v3_release_evaluation.py `
  tests/test_v3_phase6_evaluation.py `
  tests/test_release_hygiene_audit.py `
  tests/test_project_packaging_docs.py `
  tests/test_readme_showcase_consistency.py `
  tests/test_main_cli.py

python -m code_intelligence_agent v3-release-eval `
  ../release `
  --root . `
  --require-offline-pass `
  --format markdown
```

## Boundary

The archive verifies the source commit before these verification artifacts are
added. The Git candidate set is also audited in the main worktree. Neither
clean-archive success nor documentation quality substitutes for the validated
120-trial live artifact. Conversely, live repair success does not substitute
for source tests, release hygiene, or archive reproducibility.
