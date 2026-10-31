-- Run in Databricks SQL.
-- Uses the existing workspace catalog.

CREATE SCHEMA IF NOT EXISTS workspace.bronze
COMMENT 'Raw RTE consumption JSON files';

CREATE SCHEMA IF NOT EXISTS workspace.silver
COMMENT 'Cleaned half-hourly RTE consumption data';

CREATE SCHEMA IF NOT EXISTS workspace.gold
COMMENT 'Model features and prediction results';

CREATE VOLUME IF NOT EXISTS workspace.bronze.rte_raw
COMMENT 'Raw API responses stored as JSON files';


CREATE TABLE IF NOT EXISTS workspace.silver.consumption (
    ts TIMESTAMP,
    end_ts TIMESTAMP,
    consumption_mw DOUBLE,
    status STRING,
    updated_date TIMESTAMP
)
USING DELTA
COMMENT 'Cleaned half-hourly RTE consumption data';


CREATE TABLE IF NOT EXISTS workspace.gold.features (
    ts TIMESTAMP,
    consumption_mw DOUBLE,
    half_hour INT,
    day_of_week INT,
    month INT,
    day_of_year INT,
    is_holiday BOOLEAN,
    lag_364d DOUBLE,
    rolling_mean_7d_364 DOUBLE
)
USING DELTA
COMMENT 'Consumption target and model features following the revised team contract';
