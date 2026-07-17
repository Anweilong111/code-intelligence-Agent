# V4 Phase 1 Seed Benchmark

## Scope

This checkpoint freezes the candidate inventory and the repository-disjoint
selection plan for the V4 real-bug benchmark. It does not claim that the 50 new
candidates are reproducible or accepted. A new case can become `accepted` only
after the buggy revision fails the targeted test, the fixed revision passes the
targeted test, and the fixed revision passes the applicable regression command.

No repository setup script was executed while building the inventory. The
builder only parsed BugsInPy metadata, normalized bounded test argv, summarized
the hidden gold patch, and recorded setup risks for later adapter work.

## Frozen Inputs

- BugsInPy commit: `11c5f1eea954a42132cfd06bf257766a7963e0fd`
- Inventory: `datasets/v4_agent_effectiveness/bugsinpy_candidate_inventory.json`
- Inventory SHA-256: `5537380e978ce9560a64558c0389b47309bc464e406d4e377e74b626803eaaf1`
- V3 catalog: `docs/v3/phase1_real_bug_catalog.json`
- Selection plan: `datasets/v4_agent_effectiveness/selection_plan.json`
- Selection plan SHA-256: `b78dd398fcd1ead492ea4abd73a33951c70ed91c33696d762825d556863f524c`

## Inventory Result

The offline scan imported all 501 BugsInPy cases from 17 projects with zero
metadata parsing errors.

| Inventory class | Cases |
| --- | ---: |
| Eligible without a setup adapter | 280 |
| Requires an adapter or safe setup replacement | 51 |
| Blocked | 170 |
| Total | 501 |

The blocked set contains 168 pandas cases whose benchmark metadata uses short
Git revisions, one case whose gold patch contains no source file, and one case
whose test script cannot be represented as bounded argv. Short revisions remain
visible in the inventory but cannot enter a V4 catalog until both revisions are
resolved to full 40-character SHAs.

## Selection Contract

The frozen V3 baseline contains 20 accepted cases with split counts `7/8/5`.
The V4 plan pre-registers 50 new candidates and targets 30 accepted additions:

| Split | V3 accepted | Planned addition | Final target | New candidates |
| --- | ---: | ---: | ---: | ---: |
| Development | 7 | 3 | 10 | 5 |
| Validation | 8 | 7 | 15 | 10 |
| Blind test | 5 | 20 | 25 | 35 |

Candidate order is frozen per repository. Reproduction proceeds in that order
and stops when the repository target is met. Pre-registered unused backfills are
reported explicitly; they are not silently removed after outcomes are known.
Repositories are assigned to exactly one split.

The ten new target repositories are `thefuck`, `httpie`, `sanic`, `ansible`,
`cookiecutter`, `keras`, `luigi`, `matplotlib`, `scrapy`, and `spacy`. Together
with the five repositories already accepted in V3, the plan can reach the
required minimum of 15 accepted repositories only if every planned repository
contributes at least one reproducible case.

## Candidate Difficulty

The 50 candidates include 18 multi-file patches, 30 patches touching multiple
named symbols, 18 cases requiring a test/setup adapter, and 15 cases requiring
runtime provisioning. These are candidate-side structural signals only. Final
labels such as `static_negative`, `dataflow`, `root_error_separated`, and
`high_similarity_candidates` require reproduction evidence and manual causal
review before acceptance.

## License Evidence

Each new repository has a historical license URL anchored to a representative
bug SHA. GitHub's license endpoint was used where it recognized the file. Keras
was normalized to MIT from the exact historical grant and warranty clauses
because the endpoint returned `NOASSERTION`. Matplotlib uses the project-specific
`LicenseRef-Matplotlib-1.3` identifier and the historical `LICENSE/LICENSE` file.
Every generated case URL substitutes its own bug SHA into the verified path.

## Commands

```powershell
python -m code_intelligence_agent v4-benchmark-catalog inventory `
  outputs_v3\catalog_sources\BugsInPy `
  datasets\v4_agent_effectiveness\bugsinpy_candidate_inventory.json `
  --source-commit 11c5f1eea954a42132cfd06bf257766a7963e0fd `
  --available-python 3.6.9 `
  --available-python 3.7.0 `
  --available-python 3.8.1 `
  --available-python 3.8.3

python -m code_intelligence_agent v4-benchmark-catalog seed `
  datasets\v4_agent_effectiveness\bugsinpy_candidate_inventory.json `
  datasets\v4_agent_effectiveness\selection_plan.json `
  docs\v3\phase1_real_bug_catalog.json `
  datasets\v4_agent_effectiveness\real_bug_seed_catalog

python -m code_intelligence_agent v4-benchmark-catalog audit `
  datasets\v4_agent_effectiveness\real_bug_seed_catalog.json `
  --format json
```

## Verification

- Seed catalog audit: pass, 0 errors, 0 warnings.
- Seed catalog manifest: `38c03f7ea1f73e2f3c0f3498b8b2c45457829e1d2ef6e24833768eed8f76c527`.
- Catalog state: 20 accepted, 50 candidates, 5 rejected, 16 repositories.
- Focused tests: 21 passed.
- Model calls: none.

The next Phase 1 checkpoint implements safe environment adapters and records
bug/fix reproduction evidence. The catalog remains unlocked until exactly 50
cases satisfy all acceptance gates and no candidate status remains.
