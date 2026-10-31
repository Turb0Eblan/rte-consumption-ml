# Databricks data architecture

## Environment

The initial setup was created in Fady's Databricks Free Edition workspace.
Shared access with Artur has not yet been verified.

Catalog: `workspace`

## Storage structure

| Layer | Object | Purpose |
|---|---|---|
| Bronze | `workspace.bronze.rte_raw` | Managed volume for raw RTE JSON responses |
| Silver | `workspace.silver.consumption` | Delta table for cleaned half-hourly consumption |
| Gold | `workspace.gold.features` | Delta table for the target and model features |

Bronze volume path:

`/Volumes/workspace/bronze/rte_raw/`

Raw files will be organized into year/month/day directories.
Each ingestion should retain a distinct file so API revisions can be traced.

## Reproduce the structure

Open `sql/001_create_data_architecture.sql` in Databricks SQL
and run all statements using a SQL warehouse.

The script uses the existing `workspace` catalog.
For another catalog, update the catalog references before execution.

The statements use `IF NOT EXISTS`. They do not modify the schema
of an existing table.

## Silver table

| Column | Type | Meaning |
|---|---|---|
| ts | TIMESTAMP | Consumption interval start |
| end_ts | TIMESTAMP | Consumption interval end |
| consumption_mw | DOUBLE | Consumption value; source unit must be verified before ingestion |
| status | STRING | Source record status |
| updated_date | TIMESTAMP | Source record update timestamp |

## Revised Gold contract

This structure follows Artur's update after issue #10.

| Column | Type | Meaning |
|---|---|---|
| ts | TIMESTAMP | Forecast or observation timestamp |
| consumption_mw | DOUBLE | Target; unknown for future timestamps |
| half_hour | INT | Local half-hour slot, 0–47 |
| day_of_week | INT | Weekday; numbering convention to confirm |
| month | INT | Calendar month |
| day_of_year | INT | Calendar day of year |
| is_holiday | BOOLEAN | Holiday indicator |
| lag_364d | DOUBLE | Consumption at the matching timestamp 364 days earlier |
| rolling_mean_7d_364 | DOUBLE | Same-slot mean over seven days ending 364 days earlier |

Feature calculations will be implemented in issue #14.
Missing historical values must not be filled using future observations.

## Data availability and revisions

The API check on 2026-10-31 returned August 2026 as the latest
available month through the request without date parameters.

Because consolidated consumption is delayed, recent features such as
`lag_d1`, `lag_d7`, and `rolling_mean_7d` are excluded from the revised
D+1 forecasting contract.

Following Artur's update, the daily Bronze job should re-fetch
the last 2–3 months to capture revisions to provisional records.
Publication availability must be checked when requesting those periods.

## Time handling

Lag calculations must use timestamp joins rather than row shifts.
Days affected by daylight-saving changes can contain 46 or 50
half-hour intervals.

The exact timezone and DST matching convention must be confirmed
with Artur before implementing Silver and Gold transformations.

## Validation and scope

The Silver and Gold CREATE TABLE statements completed successfully.
DESCRIBE TABLE confirmed the nine Gold column names and types.

The tables are initially empty.
Data ingestion and transformations belong to issues #11–#14.
Prediction tables and model storage will be coordinated with Artur.
