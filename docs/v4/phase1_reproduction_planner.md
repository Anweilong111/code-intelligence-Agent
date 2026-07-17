# V4 Phase 1 Reproduction Planner

## Purpose

The reproduction planner converts the frozen V4 seed catalog into an executable
case order without treating environment setup as successful reproduction. It
reuses the V3 fixed-SHA checkout and three-gate executor, while adding V4 schema
adaptation, exact-runtime mapping, bounded command adapters, dependency probes,
and explicit blocker accounting.

Planning never executes repository code, benchmark setup scripts, tests, package
installation, or model calls. It may launch a mapped Python executable only to
verify its exact patch version and inspect whether required modules are already
installed.

## Execution Contract

For each candidate, the planner preserves the project and bug order from
`selection_plan.json`, then checks:

1. The catalog and reproduction profile schemas pass validation.
2. The targeted and regression commands are bounded `{python} -m ...` argv.
3. Any runner rewrite is pre-registered with a reason.
4. Test overlays are safe repository-relative paths copied from fix SHA to bug SHA.
5. An exact Python patch version is mapped and present.
6. The runtime contains the project's declared test dependencies.
7. Cases requiring native compilation remain blocked until a dedicated adapter exists.

Only an item with no blocker receives `readiness=ready`. The `run` command refuses
to checkout or execute a blocked item and writes a blocker evidence record instead.

The only initial command rewrite is Cookiecutter's BugsInPy `tox <pytest-node>`
form to a direct bounded `pytest <pytest-node>` invocation. This avoids tox
environment fan-out while preserving the exact target node. The rewrite itself
does not prove reproduction; all three runtime gates must still pass.

## Real Seed Result

The planner evaluated all 50 frozen candidates against the local pinned runtime
directory:

| Result | Cases |
| --- | ---: |
| Ready | 0 |
| Blocked | 50 |
| Exact runtime not mapped | 15 |
| Native build adapter required | 16 |
| Runtime modules missing | 35 |

Blocker categories overlap. For example, a Keras case can require both Python
3.7.3 provisioning and a native numerical backend adapter. The zero-ready result
is therefore an environment baseline, not a benchmark failure and not a repair
success claim.

The plan fingerprint is
`531f794a43c0e5ef73250eed72fbbd2eae33fd0c6315fa1945fc4c883f04b9a2`.
The generated plan remains under ignored `outputs_v4/`; the committed profiles
and planner code are sufficient to regenerate it.

## Commands

```powershell
python -m code_intelligence_agent v4-reproduce plan `
  datasets\v4_agent_effectiveness\real_bug_seed_catalog.json `
  datasets\v4_agent_effectiveness\selection_plan.json `
  datasets\v4_agent_effectiveness\reproduction_profiles.json `
  outputs_v4\phase1_reproduction_plan.json `
  --runtime-root outputs_v3\runtimes
```

Once an item becomes ready, one case can be executed with:

```powershell
python -m code_intelligence_agent v4-reproduce run `
  datasets\v4_agent_effectiveness\real_bug_seed_catalog.json `
  datasets\v4_agent_effectiveness\selection_plan.json `
  datasets\v4_agent_effectiveness\reproduction_profiles.json `
  outputs_v4\reproduction\<case-id> `
  --runtime-root <isolated-runtime-root> `
  --case-id <case-id> `
  --require-pass
```

## Next Gate

Dependency installation is intentionally not part of `plan`. The next step is to
create project-isolated environments from exact base interpreters and install only
pre-registered dependencies after explicit authorization. Development cases are
bootstrapped first. Validation and blind-test repositories remain unavailable for
prompt, threshold, and policy tuning even after their environments are prepared.
