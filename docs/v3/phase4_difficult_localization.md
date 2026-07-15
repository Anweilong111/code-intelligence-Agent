# V3 Real-Bug Fault Localization Evaluation

- Status: `pass`
- Ready / Total Cases: 20 / 20
- Blockers: 0
- Split Counts: development=7, validation=8, test=5
- Weight Selection Scope: `validation_only`
- Selected Profile: `simplex-021`
- Selected Profile SHA256: `5e86aa8ffd55c93f9f862f5698376edee0ce8144ff0ce7d06fe170394b79e10a`
- Candidate Weight Profiles: 141
- Runtime Coverage Available: 20 / 20
- Failing Runtime Coverage Available: 20 / 20
- Artifact Audit: `pass`

## Frozen Test Metrics

| Variant | Function Cases | Top-1 | Top-3 | Top-5 | MRR | MAP | EXAM | File Top-1 | File MRR | Status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| rule_only | 5 | 0.0000 | 0.0000 | 0.0000 | 0.0190 | 0.0192 | 0.4699 | 0.6000 | 0.6605 | evaluated |
| graph_only | 5 | 0.0000 | 0.0000 | 0.2000 | 0.0750 | 0.0749 | 0.1011 | 0.2000 | 0.4250 | evaluated |
| dynamic_only | 5 | 0.0000 | 0.0000 | 0.4000 | 0.1601 | 0.1442 | 0.0154 | 0.4000 | 0.5500 | evaluated |
| semantic_only | 5 | 0.2000 | 0.2000 | 0.4000 | 0.2803 | 0.1945 | 0.0826 | 0.8000 | 0.9000 | evaluated |
| fusion | 5 | 0.6000 | 0.8000 | 1.0000 | 0.7067 | 0.6144 | 0.0036 | 0.8000 | 0.9000 | evaluated |
| without_rule | 5 | 0.6000 | 0.8000 | 1.0000 | 0.7067 | 0.6144 | 0.0036 | 0.8000 | 0.9000 | evaluated |
| without_graph | 5 | 0.6000 | 0.8000 | 1.0000 | 0.7067 | 0.6144 | 0.0036 | 0.8000 | 0.9000 | evaluated |
| without_dynamic | 5 | 0.2000 | 0.2000 | 0.4000 | 0.2828 | 0.2861 | 0.0665 | 0.4000 | 0.5317 | evaluated |
| without_semantic | 5 | 0.4000 | 0.6000 | 0.8000 | 0.5733 | 0.5273 | 0.0056 | 0.6000 | 0.8000 | evaluated |
| without_auxiliary | 5 | 0.4000 | 0.8000 | 0.8000 | 0.5420 | 0.4526 | 0.0096 | 1.0000 | 1.0000 | evaluated |
| llm_only | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | not_applicable |

## Fusion On Difficult Test Subsets

| Subset | Cases | Function Cases | Top-1 | Top-3 | MRR | MAP | File Top-1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| static_negative | 2 | 2 | 0.5000 | 1.0000 | 0.6667 | 0.4359 | 0.5000 |
| cross_function | 1 | 1 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| data_flow | 3 | 3 | 0.6667 | 0.6667 | 0.7333 | 0.7333 | 1.0000 |
| separated_failure_site | 1 | 1 | 1.0000 | 1.0000 | 1.0000 | 0.5385 | 1.0000 |
| high_similarity_candidates | 2 | 2 | 0.5000 | 0.5000 | 0.6000 | 0.6000 | 1.0000 |
| multi_file | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

## Per-Case Fusion Results

| Case | Split | Tags | Function Rankable | Function First Rank | File First Rank | Real Failing Coverage |
| --- | --- | --- | --- | ---: | ---: | --- |
| bugsinpy-black-10 | development | static_negative | true | 30 | 3 | true |
| bugsinpy-black-2 | development | data_flow, static_negative | true | 17 | 2 | true |
| bugsinpy-black-3 | development | static_negative | true | 3 | 2 | true |
| bugsinpy-black-5 | development | high_similarity_candidates, data_flow | true | 12 | 1 | true |
| bugsinpy-fastapi-1 | validation | multi_file, high_similarity_candidates | true | 1 | 1 | true |
| bugsinpy-fastapi-4 | validation | data_flow, separated_failure_site | true | 1 | 1 | true |
| bugsinpy-fastapi-5 | validation | cross_function, data_flow | true | 11 | 5 | true |
| bugsinpy-fastapi-7 | validation | separated_failure_site, static_negative | true | 3 | 3 | true |
| bugsinpy-pysnooper-1 | development | multi_file, cross_function | true | 10 | 1 | true |
| bugsinpy-pysnooper-2 | development | cross_function, high_similarity_candidates | true | 6 | 4 | true |
| bugsinpy-pysnooper-3 | development | static_negative | true | 18 | 1 | true |
| bugsinpy-tqdm-2 | validation | multi_file, cross_function | true | 1 | 1 | true |
| bugsinpy-tqdm-3 | validation | static_negative | false | n/a | 2 | true |
| bugsinpy-tqdm-4 | validation | data_flow | true | 1 | 1 | true |
| bugsinpy-tqdm-5 | validation | high_similarity_candidates, static_negative | true | 21 | 2 | true |
| bugsinpy-youtube-dl-2 | test | data_flow, high_similarity_candidates | true | 1 | 1 | true |
| bugsinpy-youtube-dl-3 | test | static_negative | true | 3 | 2 | true |
| bugsinpy-youtube-dl-4 | test | cross_function, data_flow | true | 1 | 1 | true |
| bugsinpy-youtube-dl-6 | test | separated_failure_site, static_negative | true | 1 | 1 | true |
| bugsinpy-youtube-dl-8 | test | data_flow, high_similarity_candidates | true | 5 | 1 | true |

## Ablation Interpretation

| Signal Family | Selected Weight | Top-1 Delta | Top-3 Delta | MRR Delta | MAP Delta | EXAM Improvement | Conclusion |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| rule | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | inactive_by_validation_weight_selection |
| graph | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | inactive_by_validation_weight_selection |
| dynamic | 0.5000 | 0.4000 | 0.6000 | 0.4238 | 0.3283 | 0.0629 | positive_test_contribution |
| semantic | 0.2500 | 0.2000 | 0.2000 | 0.1333 | 0.0871 | 0.0020 | positive_test_contribution |
| auxiliary | 0.2500 | 0.2000 | 0.0000 | 0.1646 | 0.1618 | 0.0060 | positive_test_contribution |

## Failure Analysis

- Test Top-1 Misses: 2
- Test Top-5 Misses: 0
- Inactive Signal Families: `rule, graph`
- Empty Test Difficulty Subsets: `multi_file`
- Test Repository Count: 1
- `bugsinpy-youtube-dl-3` first appears at function rank 3.
- `bugsinpy-youtube-dl-8` first appears at function rank 5.

## Protocol And Attribution Contract

- Candidate scope and all raw signals are frozen before fix-side ground truth is read.
- Weight search receives validation cases only; the selected profile hash is frozen before test evaluation.
- Every stored Top-k row contains raw signals, active weights, per-signal contributions, clamp adjustment, and a reconstructed score.
- Function-unrankable cases remain in file-level metrics and are listed explicitly instead of being counted as artificial function misses.
- Runtime line coverage is collected with the case-pinned Python interpreter. Branch/path evidence is inferred from line and call events, not claimed as native branch coverage.
- `semantic_only` is deterministic lexical similarity. `llm_only` is not applicable until a real localization scorer is configured.
- No global uplift is assumed; zero or negative ablation differences remain in the artifact.

## Boundary

This evaluation measures fault localization only. Semantic-only is a deterministic lexical signal and is not reported as an LLM result.

## Reproduction Evidence

- Command: `python -m code_intelligence_agent v3-localization-eval outputs_v3/localization_phase4 --coverage-timeout 180 --release-docs-dir docs/v3`
- Local evaluation SHA-256: `e33479bc2cf938c4c750c546113edfdc678063662e60c16d2eacac7a53138cd0`
- Committed Top-5 attribution SHA-256: `26d1aa331b194bde706446ecee57b9f89ec132f49f698a54a27f8ad1c9212f85`
- Machine-readable metrics: `docs/v3/phase4_localization_metrics.json`
- Test Top-5 attribution: `docs/v3/phase4_test_top5_attribution.json`
