# Bronze historical backfill

## Download and storage

Entry point: src/rte_consumption_ml/bronze_backfill.py

Period: 2020-01-01 inclusive to 2026-09-01 exclusive.

Run from the repository root:

    uv run python -m rte_consumption_ml.bronze_backfill --start 2020-01-01 --end 2026-09-01 --output-dir data/bronze_test

Credentials come from the ignored project .env file or Databricks
secrets when --secret-scope is supplied.

Original response bodies are preserved under YYYY/MM/DD.
Existing files passing the coverage check are skipped.
Responses failing coverage checks are saved with needs_review reports;
subsequent runs may request those dates again.

Local ingestion was used after repeated DNS failures reaching RTE
from Databricks serverless compute.

Destination: /Volumes/workspace/bronze/rte_raw

## Verification

All 2,435 requested calendar days have raw files.
Physical interval coverage passes for 2,429 days.

Six autumn DST dates require review:
- 2020-10-25
- 2021-10-31
- 2022-10-30
- 2023-10-29
- 2024-10-27
- 2025-10-26

For each flagged date, timestamps leave the intervals starting at
23:00 and 23:30 Europe/Paris uncovered.

Daily, extended-window and monthly requests did not resolve coverage.
The source DST convention remains under investigation. These findings
do not establish which underlying measurements, if any, are missing.

ODRE data was not merged because overlapping values did not
consistently match.

The upload included 2,447 raw files plus quality reports.
All 4,807 files were verified against local contents using SHA-256.
Multiple raw responses can exist for one date.

Coverage reports in the destination:
- _quality/bronze_coverage.csv
- _quality/dst_coverage_details.json
- Individual reports under _quality/YYYY/MM/DD/

Passing coverage does not independently validate consumption values.

## Agreed downstream policy

These rules are agreed with Artur and remain to be implemented
in Silver/Gold:

- Preserve all Bronze responses.
- Quarantine the six flagged local dates from training targets
  and historical feature lookups.
- Keep lag_364d null when its historical slot is unavailable.
- For rolling_mean_7d_364, use the same local half-hour slot
  at calendar-day offsets 364 through 370.
- Average duplicate local slots within each historical day first.
- Require at least five available historical days out of seven;
  otherwise return null.
- Training excludes rows with null targets or required features.

Quarantine is temporary and does not recover missing data.
