# Gates: run export and monthly upload fixes

OWNS: server/runs.py, server/app.py, ui/template.html, ui/onboarding.js, tests/test_run_export_regression.py, tests/e2e_new_month.py, tests/e2e_upload_layout.cjs, tests/serve_upload_regression.py, README.md, RUN_UPLOAD_GATES.md

Scope: deliver all seven requested changes; verify only end-to-end and targeted regression behavior. UI requires reviewed screenshots. No unit-test suite.

- [x] G1: HTTP Excel downloads use the persisted local run day and keep their name on later downloads and restart.
  CHECK: uv run python tests/test_run_export_regression.py naming
  EXPECT: NAMING_REGRESSION_PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=6e17e147bb0e/15 entries; output=NAMING_REGRESSION_PASSED

- [x] G2: Downloaded Excel has four ordered planning columns, exact left join to the previous report month, no row loss/multiplication, and blank current entry cells.
  CHECK: uv run python tests/test_run_export_regression.py export
  EXPECT: EXPORT_REGRESSION_PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=6e17e147bb0e/15 entries; output=EXPORT_REGRESSION_PASSED

- [x] G3: Browser verifies date guidance beside the date field and correct Start run enablement without a remark below the button.
  CHECK: node tests/e2e_upload_layout.cjs date
  EXPECT: DATE_BROWSER_PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=6e17e147bb0e/15 entries; output=DATE_BROWSER_PASSED

- [x] G4: HTTP deletion handles read-only files and history links, preserves other runs/shared history and IDs, retains retryable metadata after a locked-file failure, and rejects active-run deletion.
  CHECK: uv run python tests/test_run_export_regression.py deletion
  EXPECT: DELETION_REGRESSION_PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=6e17e147bb0e/15 entries; output=DELETION_REGRESSION_PASSED (Windows read-only rule emulated; native Windows still requires verification)

- [x] G5: Browser verifies single-row active/archive action controls at desktop and mobile sizes, with reachable horizontal scrolling.
  CHECK: node tests/e2e_upload_layout.cjs actions
  EXPECT: ACTIONS_BROWSER_PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=6e17e147bb0e/15 entries; output=ACTIONS_BROWSER_PASSED

- [x] G6: Browser measures equal desktop setup card bounds empty/selected/expanded, and natural mobile stacking.
  CHECK: node tests/e2e_upload_layout.cjs cards
  EXPECT: CARDS_BROWSER_PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=6e17e147bb0e/15 entries; output=CARDS_BROWSER_PASSED

- [x] G7: Browser performs real uploads/replacements and reload; short/long filenames and draft controls do not change upload row/card heights.
  CHECK: node tests/e2e_upload_layout.cjs uploads
  EXPECT: UPLOADS_BROWSER_PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=6e17e147bb0e/15 entries; output=UPLOADS_BROWSER_PASSED

- [x] G8: Real source uploads complete the full pipeline; actual Excel response reopens with correct planning data and baseline physical totals.
  CHECK: uv run python tests/e2e_new_month.py
  EXPECT: E2E PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=6e17e147bb0e/15 entries; output=Excel verified: 27891 rows; exact previous-month join; action_plan_to_tm_2026_11_11.xlsx | E2E PASSED RUN-0001: 27891 batches, 18594 carried, 9297 unlabelled

- [x] G9: Before/after desktop, selected-file, expanded, active/archive and mobile screenshots are visually reviewed for alignment, clipping, spacing and usability.
  EVIDENCE: Visually reviewed supplied before images and validation-artifacts/run-upload/desktop-before.png against fresh desktop-empty.png, desktop-selected.png, desktop-expanded.png, runs-1524.png, runs-1280.png, runs-390.png, archive-1280.png, archive-390.png, mobile-setup-top.png and mobile-setup-bottom.png. Desktop cards share top/bottom bounds with reserved filename/footer space; long filenames are ellipsized; actions remain one row and scroll without label overlap; mobile sidebar is settled and cards stack with readable controls. cards-measurements.json independently records equal 467.765625px empty card heights and no page overflow; browser groups report no page errors.

- [x] G10: Generated preview is rebuilt and published; static preview matches the generated artifact.
  CHECK: uv run python validation-artifacts/run-upload/verify_preview.py
  EXPECT: PREVIEW_VERIFIED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=6e17e147bb0e/15 entries; output=PREVIEW_VERIFIED https://acer.taildd3bd9.ts.net/fsne-ui/ sha256=304f00bf6181c465e2d5a21ad04db2a415bde23f0ad126a3675023cae6ab2b3d

- [ ] G11: Original Windows access-denied scenario is verified on Windows.
  EVIDENCE: Native Windows is unavailable: this host is Linux, Wine is absent, and hc has no configured remote machines. Targeted HTTP regressions exercise the Windows read-only rule and locked-file failures through injected OS errors, but do not prove the original machine's failure is resolved.

ABANDON: G11 Native Windows reproduction is unavailable in this environment; verify the reported delete action on the user's Windows installation. G4 covers the implemented handling and filesystem protections with targeted regressions.
