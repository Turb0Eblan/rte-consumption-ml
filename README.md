# rte-consumption-ml

ENSE3 Machine Learning project, group 4 (Artur Nam, Fady Abuisaac).

We forecast French electricity consumption for the next day (D+1) at 30 minute
resolution, using RTE open data. Everything runs on Databricks Free Edition:
a data pipeline builds the Bronze, Silver and Gold layers, a daily ML job picks
the best model and makes the forecast, and a dashboard shows the results.

## How it works

```
RTE API
  |
Bronze   raw JSON files in a volume, one folder per day
  |
Silver   cleaned 30 min consumption table, bad days go to quarantine
  |
Gold     features for the model + 48 empty rows for tomorrow
  |
ML job   search -> select -> predict
  |
Dashboard
```

The catalog is `workspace` with one schema per layer:

| Layer | Object | What is inside |
|---|---|---|
| Bronze | `/Volumes/workspace/bronze/rte_raw/YYYY/MM/DD` | raw API responses |
| Silver | `workspace.silver.consumption` | one row per 30 min interval (UTC) |
| Gold | `workspace.gold.features` | target and features |
| Gold | `/Volumes/workspace/gold/models` | saved best model |
| Gold | `workspace.gold.best_models` | best estimator and its MAPE for each day |
| Gold | `workspace.gold.predictions` | D+1 forecasts |

More details on the data side are in `docs/databricks_architecture.md`,
`docs/silver_transform.md` and `docs/gold_features.md`.

## Case study

Target: `consumption_mw`, the consolidated consumption of France in MW,
for every half hour of tomorrow.

RTE publishes consolidated data with a delay of about two months (when we
checked on 2026-10-31 the latest month was August 2026). So yesterday or last
week is simply not available when we forecast tomorrow, and lags like D-1 or
D-7 were not an option. We use the same day one year back instead:

| Feature | Meaning |
|---|---|
| `half_hour` | slot of the day, 0 to 47 (Paris time) |
| `day_of_week` | Monday = 0 |
| `month`, `day_of_year` | season |
| `is_holiday` | French public holiday |
| `lag_364d` | consumption 364 days earlier, same half hour |
| `rolling_mean_7d_364` | mean of the same half hour over days 364 to 370 back, needs at least 5 of the 7 days |

364 is a multiple of 7, so a Monday is compared with a Monday.

All times are in Europe/Paris for the calendar features. On the autumn DST day
there are 50 half hours, in spring 46. The six autumn DST days from 2020 to
2025 had inconsistent data and are quarantined in Silver.

Metric: MAPE is used to choose the best model, MAE and RMSE are logged too.
The last 30 days of history are the test set, no shuffling.

## ML pipeline

Code is in `src/rte_consumption_ml/ml/`.

1. `train_search.py` takes every regressor from `sklearn.utils.all_estimators()`
   plus XGBoost and LightGBM, trains each one in a pipeline with
   `StandardScaler` and logs the metrics to MLflow (experiment
   `/Shared/rte-model-search`). A few models are excluded because they need
   n x n memory or extra arguments, the list with reasons is in the file.
   Every search gets a `search_id` tag.
2. `select_best.py` reads the runs of the latest search, takes the lowest
   MAPE, retrains that model on all the data and saves it with joblib to the
   models volume. It also appends a row to `workspace.gold.best_models`.
3. `predict.py` loads the model, reads tomorrow's rows from Gold
   (`consumption_mw IS NULL`) and writes 48 predictions to
   `workspace.gold.predictions`. If Gold still has the rows of another day it
   stops with an error instead of predicting the wrong date.

Training uses only rows where `consumption_mw`, `lag_364d` and
`rolling_mean_7d_364` are all present (98,914 rows on 2026-11-01).

When the code runs outside Databricks it uses generated fake data with the
same columns and writes CSV files to `models/`, so the scripts can be tested
without a cluster. `ml/settings.py` checks `DATABRICKS_RUNTIME_VERSION` to
decide which mode to use.

## Databricks job

Job name: `rte-ml-daily`. It runs `jobs/run_step.py` three times, each task
depends on the previous one:

| Task | Parameter |
|---|---|
| search | `["search"]` |
| select | `["select"]` |
| predict | `["predict"]` |

Settings we used:

- Source: Git provider (this repository), so every run takes the latest commit
- Compute: serverless
- Environment: Standard v6 (Python 3.12) with the extra packages
  `xgboost-cpu`, `lightgbm`, `holidays`. We did not use the pyproject.toml
  option because it would replace the pandas and MLflow versions that come
  with Databricks.
- A full run takes about 11 minutes, most of it is the model search.

The Gold table has to be rebuilt each morning before this job, because that
is what creates tomorrow's rows.

## Dashboard

"RTE consumption forecast" in Databricks, built from two SQL datasets:

- `best_models_15d`: last row of each day from `best_models`, last 15 days
- `predictions_15d`: forecasts of the last 15 days, converted to Paris time

It has a table with the best estimator per day, a line chart of its MAPE, and
the D+1 forecast curve.

## Running locally

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env    # fill in your RTE credentials, never commit this file
```

ML scripts on fake data:

```bash
uv run python jobs/run_step.py search
uv run python jobs/run_step.py select
uv run python jobs/run_step.py predict
```

MLflow results can be viewed with `uv run mlflow ui`.

Data scripts have self tests that do not need Spark:

```bash
uv run python -m rte_consumption_ml.silver_transform --self-test
uv run python -m rte_consumption_ml.gold_features --self-test
```

## Results

First run on real data (2026-11-01): the best model was
OrthogonalMatchingPursuit with MAPE 3.14%.

Keep in mind that the test period is August 2026, when consumption is low and
stable. We expect a higher error in November with heating. We also have no
weather data, which is the main thing that would improve the forecast.

## Known limitations

- Consolidated data arrives about two months late, so the model only knows the
  past through yearly features.
- No temperature or weather features.
- Six autumn DST days are missing from the history.
- Databricks serverless could not reach the RTE API during our tests (DNS
  errors), so daily Bronze ingestion is still being worked on.

## Team

| | |
|---|---|
| Fady Abuisaac | project setup, RTE API, Bronze, Silver, Gold, data architecture |
| Artur Nam | case study, model search, model selection, serving, Databricks job, dashboard |
