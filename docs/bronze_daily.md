# Daily Bronze ingestion (#12)

Entry point: src/rte_consumption_ml/bronze_daily.py.
This task fetches and stores Bronze data only. It does not invoke Silver,
Gold, training or predictions. Coordinate downstream execution under issue #6.

## Availability and refresh window

The authenticated API request without date parameters discovers the last known
published data, as described by RTE guide v2.1, pages 7-10 (CPC-RG01).
The exclusive horizon is the day after the latest returned interval start date.
No fixed publication delay is assumed. HTTP 403 and other unexpected HTTP
errors fail the run; they are never interpreted as a publication delay.

The daily range starts at the earlier of:
- the last successfully fetched exclusive end;
- three calendar months before the latest available exclusive end.

It is bounded below by 2020-01-01. This refreshes revisions and catches up after
outages. First-run --initial-end 2026-09-01 is based on the separately verified
historical upload. It does not download or reverify the full old backfill.
The default 400-day cap prevents an unexpectedly large run.

## Data and checkpoint handling

- Requests use Europe/Paris midnight boundaries and UTC coverage checks.
- Every daily HTTP-200 response is saved byte-for-byte before validation.
- Malformed JSON, missing keys, bad records and DST gaps receive quality reports.
- New/revised bodies use unique response filenames; previous bodies remain.
- Unchanged bodies reuse the previous response only after read-back verification.
- Raw files and reports are verified against downloaded bytes.
- State metadata is published only after all daily files and the run report
  have been uploaded and verified.
- Known six autumn DST dates remain needs_review; no values are reconstructed.
- Other review dates are retained and reported; the job exits with code 2.
- Transport/authentication/storage failures exit with code 1.
- A checkpoint records successfully fetched dates, including review dates;
  it does not assert those dates are suitable for Silver.
- Unexpected malformed discovery responses fail before checkpoint updates.

Raw: YYYY/MM/DD/response_*.json
Quality: _quality/YYYY/MM/DD/response_*.json
Checkpoint: _orchestration/state.json
Run audit: _orchestration/runs/<run_id>/{discovery,report}.json
Probe bodies: _orchestration/probes/

Only one writer may run at a time. All ingestion jobs use the same GitLab
resource_group. Run downstream processing after ingestion succeeds. Individual
file writes and the checkpoint are not a global transaction.

## Credentials and execution

Protected CI/CD variables:
DATABRICKS_HOST, DATABRICKS_TOKEN (files scope),
RTE_CLIENT_ID, RTE_CLIENT_SECRET.

Secrets are configured in GitLab UI, never in source or artifacts.
Requires requests, python-dotenv and tzdata. No ML dependencies are required.
The Databricks token expires after 30 days and must be rotated before expiry.

Local fake-only tests:
uv run python -m rte_consumption_ml.bronze_daily --self-test

Manual read-only plan:
bronze_daily_plan

Manual integration run:
bronze_daily_refresh

Production schedule must target protected main. Scheduled refresh also requires
BRONZE_DAILY_ENABLED=true. Keep it disabled until review and integration checks
are complete. Manual jobs are optional; inspect their logs rather than relying
only on the aggregate green pipeline status.

## Validation status

Completed before implementation:
- GitLab runner reached RTE and Databricks.
- RTE authentication and 2026-08-31 request passed.
- Isolated probe upload/read-back passed SHA-256 verification.
- Probe coverage passed with 48 physical half-hour intervals.

The new daily module's tests, plan, full integration run, retry/resume checks
and production schedule activation remain to be verified.

## Limits

This is a Bronze scheduler, not an end-to-end Databricks workflow.
Review days are not silently repaired.
The three-month revision window does not cover annual finalization of older
years; plan separate historical re-fetches when required.
Refreshing Bronze does not automatically rebuild Silver or Gold.

## Integration validation — 2026-11-01

- Local and GitLab CI self-tests passed.
- Dry run discovered the published exclusive end: 2026-09-01.
- Refresh covered 2026-06-01 inclusive to 2026-09-01 exclusive.
- First run: 92 new responses uploaded and verified; no days required review.
- Repeat run: 0 new responses and 92 unchanged responses; no days required review.
- Silver and Gold were not modified.
- Automatic daily scheduling remains disabled pending review and merge.
