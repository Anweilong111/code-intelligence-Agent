# Phase 7 Verification

- Evaluation commit: `a47e37b564b7f9271faac981e4b6437e2fcbd17f`
- Focused regression: `12 passed in 44.69s`
- Full regression: `1226 passed in 1186.59s`
- Required comparisons: `8/8`
- Absolute workspace paths in published artifacts: `0`
- API keys in published artifacts: `0`

The full suite was run with `python -m pytest -q`. The focused suite covered
planner, memory, localization, patch strategy, budget ablation, and aggregate
system-report contracts. Generated work files remain under `outputs_v2/` and are
not part of the published source tree.
