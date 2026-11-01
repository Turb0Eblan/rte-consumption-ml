"""Build the agreed Gold feature snapshot from validated Silver tables.

Run --self-test locally first; run --dry-run in Databricks before writing.
This entry point builds data only. It does not train or run a model.
"""

import argparse
import math
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

UTC = timezone.utc
PARIS = ZoneInfo("Europe/Paris")
STEP = timedelta(minutes=30)
KNOWN_QUARANTINE = {
    date.fromisoformat(value) for value in (
        "2020-10-25", "2021-10-31", "2022-10-30",
        "2023-10-29", "2024-10-27", "2025-10-26",
    )
}
COLUMNS = [
    "ts", "consumption_mw", "half_hour", "day_of_week", "month",
    "day_of_year", "is_holiday", "lag_364d", "rolling_mean_7d_364",
]
SCHEMA = (
    "ts TIMESTAMP, consumption_mw DOUBLE, half_hour INT, day_of_week INT, "
    "month INT, day_of_year INT, is_holiday BOOLEAN, lag_364d DOUBLE, "
    "rolling_mean_7d_364 DOUBLE"
)


def day_bounds(day):
    return (
        datetime.combine(day, time.min, PARIS).astimezone(UTC),
        datetime.combine(day + timedelta(days=1), time.min, PARIS).astimezone(UTC),
    )


def day_slots(day):
    cursor, end = day_bounds(day)
    result = []
    while cursor < end:
        result.append(cursor)
        cursor += STEP
    return result


def parse_timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Expected a timezone-aware timestamp")
    return value.astimezone(UTC)


def slot_key(ts):
    local = ts.astimezone(PARIS)
    return local.date(), local.hour * 2 + local.minute // 30


def annual_features(lookup, day, half_hour):
    lag = lookup.get((day - timedelta(days=364), half_hour))
    values = [
        lookup[(day - timedelta(days=offset), half_hour)]
        for offset in range(364, 371)
        if (day - timedelta(days=offset), half_hour) in lookup
    ]
    rolling = math.fsum(values) / len(values) if len(values) >= 5 else None
    return lag, rolling


def build_features(intervals, outcomes, as_of):
    """Pure Python core shared by production and local regression tests.

    intervals: iterable of (aware start, aware end, MW).
    outcomes: one Silver quality outcome per Paris date.
    Outputs keep physical UTC timestamps; only historical lookups average
    duplicate local slots. No forward filling or interpolation is used.
    """
    import holidays

    forecast_day = as_of + timedelta(days=1)
    blocked = KNOWN_QUARANTINE | {
        day for day, outcome in outcomes.items() if outcome != "complete"
    }
    if forecast_day in blocked:
        raise ValueError("Forecast date is quarantined; inspect the requested as-of date")
    cutoff = day_bounds(forecast_day)[0]
    by_day = defaultdict(list)
    excluded = 0
    seen = set()
    for left, right, value in intervals:
        left, right = parse_timestamp(left), parse_timestamp(right)
        day, _ = slot_key(left)
        if left >= cutoff:
            continue
        if day not in outcomes:
            raise ValueError(f"Silver date has no quality result: {day}")
        if day in blocked:
            excluded += 1
            continue
        if (
            isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or value <= 0
        ):
            raise ValueError(f"Invalid Silver consumption at {left}")
        if right - left != STEP or left in seen:
            raise ValueError(f"Duplicate or irregular Silver interval at {left}")
        seen.add(left)
        by_day[day].append((left, right, float(value)))

    # Validate every complete quality date, including dates with zero Silver rows.
    for day, outcome in outcomes.items():
        if day > as_of or day in blocked or outcome != "complete":
            continue
        rows = sorted(by_day.get(day, []))
        expected = day_slots(day)
        if (
            [row[0] for row in rows] != expected
            or any(right != left + STEP for left, right, _ in rows)
        ):
            raise ValueError(f"Silver coverage disagrees with quality table: {day}")

    observed = sorted(row for rows in by_day.values() for row in rows)
    if not observed:
        raise ValueError("No validated historical Silver rows; no Gold write allowed")

    buckets = defaultdict(list)
    for left, _, value in observed:
        buckets[slot_key(left)].append(value)
    lookup = {
        key: math.fsum(values) / len(values) for key, values in buckets.items()
    }
    years = range(min(by_day).year, forecast_day.year + 1)
    public_holidays = holidays.France(years=years)
    targets = [(left, value) for left, _, value in observed]
    targets.extend((ts, None) for ts in day_slots(forecast_day))
    features = []
    for ts, value in targets:
        local = ts.astimezone(PARIS)
        day, half_hour = slot_key(ts)
        lag, rolling = annual_features(lookup, day, half_hour)
        features.append((
            ts, value, half_hour, day.weekday(), day.month,
            local.timetuple().tm_yday, day in public_holidays, lag, rolling,
        ))
    summary = {
        "historical_rows": len(observed),
        "latest_observation_date": str(max(by_day)),
        "forecast_date": str(forecast_day),
        "forecast_rows": len(day_slots(forecast_day)),
        "excluded_silver_rows": excluded,
        "training_ready_rows": sum(
            row[1] is not None and row[7] is not None and row[8] is not None
            for row in features
        ),
        "forecast_ready_rows": sum(
            row[1] is None and row[7] is not None and row[8] is not None
            for row in features
        ),
    }
    return features, summary


def self_test():
    def sample(day, value=1000.0):
        return [
            (ts, ts + STEP, value + index)
            for index, ts in enumerate(day_slots(day))
        ]

    assert len(day_slots(date(2026, 2, 2))) == 48
    assert len(day_slots(date(2026, 3, 29))) == 46
    assert len(day_slots(date(2026, 10, 25))) == 50

    first = date(2024, 1, 1)
    days = [first + timedelta(days=index) for index in range(7)]
    outcomes = dict.fromkeys(days, "complete")
    history = [row for index, day in enumerate(days)
               for row in sample(day, 1000.0 + index * 100)]
    target_day = days[-1] + timedelta(days=364)
    as_of = target_day - timedelta(days=1)

    def midnight_forecast(result):
        return next(row for row in result
                    if row[1] is None and row[2] == 0)

    result, summary = build_features(history, outcomes, as_of)
    row = midnight_forecast(result)
    assert row[7] == 1600.0 and row[8] == 1300.0
    assert summary["historical_rows"] == 7 * 48
    assert summary["forecast_ready_rows"] == 48
    assert row[3] == target_day.weekday()

    five = outcomes | {days[0]: "quarantine", days[1]: "quarantine"}
    result, _ = build_features(history, five, as_of)
    row = midnight_forecast(result)
    assert row[7] == 1600.0 and row[8] == 1400.0
    assert all(slot_key(row[0])[0] not in days[:2] for row in result)

    four = five | {days[2]: "quarantine"}
    result, _ = build_features(history, four, as_of)
    assert midnight_forecast(result)[8] is None

    no_lag = outcomes | {days[-1]: "quarantine"}
    result, _ = build_features(history, no_lag, as_of)
    row = midnight_forecast(result)
    assert row[7] is None and row[8] == 1250.0

    # A complete 25-hour day outside the six agreed quarantine dates.
    fall = date(2026, 10, 25)
    result, summary = build_features(
        sample(fall), {fall: "complete"}, fall + timedelta(days=363)
    )
    target = next(row for row in result if row[1] is None and row[2] == 4)
    assert target[7] == 1005.0  # mean of both physical 02:00 slots
    assert summary["historical_rows"] == 50

    for forecast, expected in (
        (date(2026, 3, 29), 46), (date(2026, 10, 25), 50)
    ):
        result, summary = build_features(
            history, outcomes, forecast - timedelta(days=1)
        )
        assert summary["forecast_rows"] == expected
        future = [row for row in result if row[1] is None]
        assert len({row[0] for row in future}) == expected
        assert all(slot_key(row[0])[0] == forecast for row in future)
        assert all(row[0].utcoffset() == timedelta(0) for row in future)

    # Paris holiday/day boundary and leap-year calendar features.
    result, _ = build_features(
        sample(date(2024, 2, 29)), {date(2024, 2, 29): "complete"},
        date(2024, 4, 30),
    )
    future = [row for row in result if row[1] is None]
    assert future[0][0] == datetime(2024, 4, 30, 22, tzinfo=UTC)
    assert all(row[6] is True and row[3] == 2 for row in future)
    assert next(row for row in result if row[1] is not None)[5] == 60

    known = date(2024, 10, 27)
    result, _ = build_features(
        history + sample(known), outcomes | {known: "complete"},
        date(2026, 11, 1),
    )
    assert all(slot_key(row[0])[0] != known for row in result)

    for bad in (
        history + [history[0]], history[1:],
        [(history[0][0].replace(tzinfo=None), history[0][1], 1000.0)]
        + history[1:],
        [(history[0][0], history[0][1], float("nan"))] + history[1:],
    ):
        try:
            build_features(bad, outcomes, as_of)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid Silver input was accepted")
    print("Self-test passed: calendar, UTC/Paris boundaries, 46/48/50 slots, "
          "364-370 day lookups, 5-of-7 rule, duplicate-slot averaging, "
          "quarantine, null propagation and invalid inputs.")


def load_silver(spark, catalog, as_of, max_rows):
    base = catalog + ".silver."
    versions = {
        name: int(spark.sql(f"DESCRIBE HISTORY {base}{name} LIMIT 1").first().version)
        for name in ("consumption", "consumption_quality")
    }
    print("Silver source versions:", versions, flush=True)
    frames = {
        name: spark.read.option("versionAsOf", version).table(base + name)
        for name, version in versions.items()
    }
    outcomes = {}
    for row in frames["consumption_quality"].select(
        "local_date", "outcome"
    ).collect():
        if row.local_date is None or row.outcome is None:
            raise ValueError("Missing Silver quality date or outcome")
        if row.local_date in outcomes:
            raise ValueError("Duplicate Silver quality date")
        outcomes[row.local_date] = row.outcome
    end_text = day_bounds(as_of + timedelta(days=1))[0].strftime("%Y-%m-%d %H:%M:%S")
    # UTC-formatted strings avoid dependence on the Python driver's local timezone
    # when Spark converts TimestampType values into naive datetime objects.
    rows = frames["consumption"].filter(
        f"ts IS NULL OR ts < TIMESTAMP '{end_text}'"
    ).selectExpr(
        "date_format(ts, \"yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'\") AS start_utc",
        "date_format(end_ts, \"yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'\") AS end_utc",
        "consumption_mw",
    ).limit(max_rows + 1).collect()
    if len(rows) > max_rows:
        raise ValueError("Driver input limit exceeded; increase --max-input-rows "
                         "after checking available memory")
    return [(row.start_utc, row.end_utc, row.consumption_mw) for row in rows], outcomes


def write_snapshot(spark, table, rows, as_of):
    from pyspark.sql import functions as F

    if spark.catalog.tableExists(table):
        existing = spark.table(table)
        if existing.groupBy("ts").count().filter("count > 1").limit(1).count():
            raise ValueError("Existing Gold has duplicate timestamps; inspect before merging")
        # Use SQL for conversion, not the driver's naive datetime representation.
        latest = existing.filter("consumption_mw IS NOT NULL").selectExpr(
            "to_date(from_utc_timestamp(ts, 'Europe/Paris')) AS day"
        ).agg(F.max("day")).first()[0]
        if latest is not None and latest > as_of:
            raise ValueError("As-of date predates existing observed Gold data")
    source = spark.createDataFrame(rows, schema=SCHEMA)
    spark.sql(f"CREATE TABLE IF NOT EXISTS {table} ({SCHEMA}) USING DELTA")
    actual = spark.table(table).schema
    if [(f.name, f.dataType) for f in actual] != [
        (f.name, f.dataType) for f in source.schema
    ]:
        raise ValueError("Existing Gold schema differs from the agreed nine columns")
    view = "_gold_snapshot_" + uuid4().hex
    source.createOrReplaceTempView(view)
    try:
        spark.sql(f"""
            MERGE INTO {table} AS t USING {view} AS s ON t.ts = s.ts
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
            WHEN NOT MATCHED BY SOURCE THEN DELETE
        """)
    finally:
        spark.catalog.dropTempView(view)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-input-rows", type=int, default=300000)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.catalog):
        parser.error("Invalid catalog identifier")
    if args.max_input_rows < 1:
        parser.error("--max-input-rows must be positive")
    as_of = args.as_of or datetime.now(PARIS).date()

    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    intervals, outcomes = load_silver(
        spark, args.catalog, as_of, args.max_input_rows
    )
    rows, summary = build_features(intervals, outcomes, as_of)
    if len(rows) != len({row[0] for row in rows}):
        raise ValueError("Duplicate output timestamps; no Gold write allowed")
    for key, value in summary.items():
        print(f"{key}: {value}", flush=True)
    print("Gold rows:", len(rows), flush=True)
    print("Historical rows with unavailable annual features are retained; "
          "training must require non-null target, lag and rolling mean.")
    if summary["forecast_ready_rows"] != summary["forecast_rows"]:
        print("REVIEW: some D+1 features are unavailable; no imputation was applied.")
    if args.dry_run:
        print("DRY RUN finished: no tables updated.")
        return
    write_snapshot(spark, args.catalog + ".gold.features", rows, as_of)
    print("Finished: Gold feature snapshot synchronized.")


if __name__ == "__main__":
    main()
