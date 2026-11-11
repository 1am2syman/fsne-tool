# Action plan cycle: upload, settle, final view

Status: implemented and verified against A1–A14 with E2E checks only. Evidence: [acceptance gates](../ACTION_PLAN_GATES.md) and `validation-artifacts/action-plan/`.

## Why

Each month the run's Excel goes to the sales team, who fill `action_plan` and `mitigation_status` beside the `lm_` columns and send it back. Today that file reaches the tool only by hand: someone deletes the `lm_` columns, drops the file into `history/` and re-runs. Until then the dashboard shows last month's statuses carried forward by batch key, with nothing on screen saying so.

This change makes the round trip a first-class part of a run and makes it visible which view a reader is looking at.

## Run lifecycle

| State | Trigger | "Open results" label | Tab flag | History |
| --- | --- | --- | --- | --- |
| Provisional | Run completes | Pre-action plan view | Pre-action plan view: statuses carried over from the previous month, N new batches unlabelled | Nothing written to `history/` |
| Plan uploaded | Sales file uploaded | Action plan view (not settled) | Action plan view (not settled) | Nothing written to `history/` |
| Settled | "Settle month" confirmed | Final view | Final view | Written to `history/`, committed locally |

The state is stored in the run's metadata (state, upload time, uploader, file name, check counts, settle time, settler, commit result). Every label and flag is derived from it.

## Runs page

- The "Excel" action is renamed "Action plan input file".
- "Open results" takes the label for the run's state.
- "Upload action plan" is available on a completed run that is not settled. Uploading again replaces the previous upload.
- "Settle month" is available on a completed run that is not settled.
- "Reopen" is available on a settled run.
- Anyone using the app may upload, settle and reopen.

## Action plan input file

- Same rows and columns as today: the report, `lm_action_plan`, `lm_mitigation_status`, `action_plan`, `mitigation_status`.
- `mitigation_status` has a dropdown. Values seen in saved history: `Actionable`, `Not actionable`, `Incorrect Stock`, `RSL>10M_Good stock`.
- Every column except `action_plan` and `mitigation_status` is locked.

## Upload

- One file, as returned by sales. Nobody removes the `lm_` columns by hand.
- Only `batch_index`, `action_plan` and `mitigation_status` are read. They are joined to the run's own frozen report by `batch_index`. Every other column in the uploaded file is ignored, so sorting, filtering or altered numbers cannot reach history.
- The file must hold exactly the run's batches: the set of `batch_index` values equals the run's, with none missing, none extra and none repeated. Row order does not matter.
- Before the upload is accepted the user sees a check: batches matched, action plan remarks blank, statuses blank, unrecognised status values. A refused file names the reason and lists the missing or extra batches.
- Blocking: missing `batch_index` or either entry column, duplicate `batch_index`, any missing or extra batch, a report date that differs from the run's.
- Warning only: blank action plan remarks, blank statuses, unrecognised status values. A partly filled plan is acceptable.
- Accepting the upload rebuilds only the snapshot, movement and dashboard steps. The report is not recalculated.

## Settle month

- Confirmation shows the month, batch count and number of batches without a status.
- Settling with no plan uploaded is allowed behind a stronger confirmation, so a month where sales never responds does not block the next one. The settled file then holds the carried-over statuses.
- One settled run per calendar month. Settling a run archives the month's other runs.
- Settling writes the run's snapshot to `history/<report date>.xlsx` and commits that file only, locally. It does not push.
- A failed commit (no git, no identity, any other error) leaves the run settled and shows "settled, not committed".
- The run's snapshot is the authority; the file in `history/` is its export.

## Reopen

- Needs a confirmation.
- Returns the run to "plan uploaded" (or "provisional" if it was settled without a plan), removes its settled history file in a new local commit, and marks later runs built on that month as out of date.

## Next month

- Start run stops when the previous calendar month has no settled history.
- An explicit override starts the run anyway. Its `lm_` columns are empty and no statuses are carried over.
- Files already in `history/` when this ships (February to September 2026) count as settled.

## Out of scope

- Pushing to a remote.
- Several returned files per month, or merging files.
- Editing statuses inside the app. (Rescue plan notes only pre-fill the input file's entry cells; the uploaded file stays the only way plans reach history. See [rescue.md](rescue.md).)
- Roles or permissions.

## Acceptance gates

- [x] A1: A completed run shows "Action plan input file" and "Pre-action plan view"; every dashboard tab carries the pre-action plan flag with the carried and unlabelled counts.
- [x] A2: The input file has a dropdown on `mitigation_status` and rejects edits to every other column except `action_plan`.
- [x] A3: Uploading the returned file with rows sorted, numbers altered and `lm_` columns present yields a snapshot whose report columns equal the run's own report and whose two entry columns equal the file's.
- [x] A4: The pre-accept check reports correct counts for matched batches, blank remarks, blank statuses and unrecognised statuses on a prepared file, and a file with blank remarks is accepted.
- [x] A5: A file with one batch removed, one batch added, a duplicate `batch_index`, a missing entry column or a different report date is refused, names the offending batches, and leaves the run unchanged.
- [x] A6: After upload the run shows "Action plan view (not settled)" on the button and every tab, the movement view reflects the uploaded statuses, and `history/` is unchanged.
- [x] A7: Settle writes exactly one file to `history/`, creates one local commit containing only that file, pushes nothing, and the run shows "Final view" everywhere.
- [x] A8: Settle with git unavailable leaves the run settled, the history file written, and shows "settled, not committed".
- [x] A9: Settling a second run of the same month archives the first and leaves one history file for that month.
- [x] A10: Settling with no upload requires the stronger confirmation and produces a history file with the carried-over statuses.
- [x] A11: Reopen restores the earlier state, removes the history file in a local commit, and flags a later run built on that month as out of date.
- [x] A12: Start run is refused when the previous month is unsettled; the override runs with empty `lm_` columns and no carried statuses.
- [x] A13: With the existing `history/` files and no settled flags, a new run for the following month starts without the override.
- [x] A14: Widescreen screenshots of the runs page in all three states and of one dashboard tab in each state are reviewed.
