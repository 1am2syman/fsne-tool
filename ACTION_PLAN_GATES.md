# Gates: Action plan cycle

OWNS: server/**, ui/**, tests/e2e_action_plan*, docs/action-plan-cycle.md, README.md, ACTION_PLAN_GATES.md

Scope: Implement all acceptance criteria A1–A14 with E2E tests only.

- [x] G1: A1–A13 pass through HTTP against real pipeline outputs, Excel files and isolated local git repositories.
  CHECK: uv run python -m tests.e2e_action_plan_cycle
  EXPECT: ACTION_PLAN_E2E_PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=1179344f836b/12 entries; output=/home/acer_vps/GitHub/fsne-tool/tests/e2e_action_plan_cycle.py:354: FutureWarning: from_arrow(<ArrowStreamExportable>) will return a Series instead of a DataFrame in 2.0. To avoid this warning, pass the ArrowStreamExportable to either `pl.D

- [x] G2: A14 and browser interactions pass at widescreen laptop widths with screenshots of runs and dashboards in all three states.
  CHECK: node tests/e2e_action_plan_cycle.cjs
  EXPECT: ACTION_PLAN_BROWSER_PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=1179344f836b/12 entries; output=Checking browser state: settled | ACTION_PLAN_BROWSER_PASSED

- [x] G3: Widescreen screenshots are visually reviewed and the rebuilt static preview is published and reachable.
  EVIDENCE: Reviewed runs-{provisional,plan_uploaded,settled}.png and dashboard-{provisional,plan_uploaded,settled}.png at 1524×980, plus runs-settled-1440.png at 1440×900 under validation-artifacts/action-plan/: readable labels/actions, correct state flags, no page overflow. Browser checks all 7 dashboard tabs per state; browser-results.json records no uncaught errors and independent movement reconciliation. preview-results.json verifies https://acer.taildd3bd9.ts.net/fsne-ui/ against generated HTML (62,602,356 bytes, SHA-256 match); shared Tailscale routes unchanged.
