# V3 Phase 5 Semantic Validation Calibration

- Status: `pass`
- Human-fix cases: `2`
- Semantic passes: `2`
- False rejections: `0`
- Blockers: `0`
- Reverse mutations killed: `3/3`

## Case Results

| Case | Repository | Source files | Semantic | Result |
| --- | --- | ---: | --- | --- |
| `bugsinpy-pysnooper-1` | cool-RR/PySnooper | 2 | `pass` | `pass` |
| `bugsinpy-pysnooper-3` | cool-RR/PySnooper | 1 | `pass` | `pass` |

## Claim Boundary

Human fix content is used only to calibrate post-generation semantic validation. These results are not Agent-generated repairs, pass@k, or evidence available to localization, planning, or an LLM.

The fix-side source is an oracle used only after generation to measure validator false rejection. It is never model context and these cases do not count as Agent repair successes.
