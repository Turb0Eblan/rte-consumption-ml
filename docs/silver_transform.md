# Silver consumption transformation

Entry point: src/rte_consumption_ml/silver_transform.py

## Behaviour

- Reads Bronze JSON from /Volumes/workspace/bronze/rte_raw.
- Selects the latest downloaded response per Paris calendar date.
- Validates timezone-aware timestamps, 30-minute intervals, values and coverage.
- Uses UTC timestamps and Europe/Paris calendar-day boundaries.
- Quarantines an entire selected day if validation fails.
- Preserves the original Bronze files.
- Synchronizes the requested date range using Delta MERGE.
- The three table updates are separate transactions.

## Tables

- workspace.silver.consumption: accepted intervals.
- workspace.silver.consumption_quality: selected source and daily validation results.
- workspace.silver.consumption_quarantine: rejected records and reasons.

Known DST dates remain quarantined:
2020-10-25, 2021-10-31, 2022-10-30,
2023-10-29, 2024-10-27, 2025-10-26.

These gaps remain unresolved; no values or timestamps were fabricated.

## Validation completed on 2026-11-01

Period: 2020-01-01 inclusive to 2026-09-01 exclusive.

- Local self-test passed.
- Databricks dry run: 2435 dates, 2429 complete, 6 quarantined.
- Saved consumption: 116578 rows with unique timestamps.
- Required-field and interval-duration checks passed.
- Quarantine: 288 records across the six known dates.
- Full rerun: bidirectional EXCEPT ALL comparisons confirmed identical
  rows and values in all three tables.

## Local self-test

uv run python -m rte_consumption_ml.silver_transform --self-test

## Databricks script arguments

--start 2020-01-01 --end 2026-09-01 --dry-run

Remove --dry-run to synchronize the tables.
