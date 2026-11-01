# Gold feature engineering

Entry point: src/rte_consumption_ml/gold_features.py
Output: workspace.gold.features

## Contract

Columns:
ts, consumption_mw, half_hour, day_of_week, month, day_of_year,
is_holiday, lag_364d, rolling_mean_7d_364.

- UTC timestamps; calendar features use Europe/Paris.
- half_hour: 0-47; day_of_week: Monday=0 through Sunday=6.
- French public holidays are supplied by the holidays package.
- lag_364d uses the same local half-hour 364 calendar days earlier.
- rolling_mean_7d_364 averages available days at offsets 364-370,
  requiring at least five distinct days.
- Repeated historical local slots are averaged within each day before
  calculating annual features. Physical target timestamps remain distinct.
- Known quarantine dates and non-complete Silver quality dates are excluded.
- Missing annual features remain NULL; no interpolation is applied.
- Historical targets exactly preserve accepted Silver consumption values.
- D+1 contains 46, 48 or 50 physical intervals with a NULL target.

Training must require non-null consumption_mw, lag_364d and
rolling_mean_7d_364. Serving must select the requested Paris forecast date
and check feature availability.

## Execution

Run Silver successfully before Gold; avoid concurrent source updates.
Requires holidays and tzdata; Spark is provided by Databricks.

Local check:
uv run python -m rte_consumption_ml.gold_features --self-test

Databricks dry-run arguments:
--as-of 2026-11-01 --dry-run

Remove --dry-run to write.
Omit --as-of for the current Paris date during daily execution.

Gold rebuilds its full snapshot from Silver using Delta MERGE.
Rows absent from the rebuilt snapshot are deleted, including obsolete
forecast placeholders. Prediction history must be stored separately.

The as-of argument sets the calendar cutoff; it does not reconstruct
which revisions were available at a historical point in time.

## Validation completed on 2026-11-01

- Local regression tests passed, including DST, quarantine and 5-of-7 rules.
- Databricks dry run and write passed.
- Historical rows: 116578; latest observation: 2026-08-31.
- Training-ready rows: 98914.
- Forecast date: 2026-11-02; 48 rows, all with complete annual features.
- Total Gold rows: 116626, with unique timestamps.
- Historical targets matched Silver using bidirectional EXCEPT ALL.
- Annual features matched independent SQL date-join calculations.
- Full rerun preserved every row and value using bidirectional EXCEPT ALL.

Databricks notebook validation used holidays 0.107.
