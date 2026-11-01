"""Build validated Silver snapshots from the latest daily Bronze responses.

Run inside Databricks serverless as a Python script task.
The standard-library self-test also runs locally with --self-test.
"""

import argparse
import json
import math
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
PARIS = ZoneInfo("Europe/Paris")
STEP = timedelta(minutes=30)
QUARANTINE_DATES = {
    date.fromisoformat(text)
    for text in (
        "2020-10-25", "2021-10-31", "2022-10-30",
        "2023-10-29", "2024-10-27", "2025-10-26",
    )
}
FILE_PATTERN = (
    r"/(\d{4})/(\d{2})/(\d{2})/"
    r"response_(\d{8}T\d{12}Z)_([0-9a-f]{8})\.json$"
)


def bounds(day):
    start = datetime.combine(day, time.min, PARIS).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, PARIS)
    return start, end.astimezone(UTC)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp_not_string")
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("timestamp_without_offset")
    return result.astimezone(UTC)


def analyse_day(day, source_file, body):
    """Reject an entire day if its selected response cannot be validated."""
    start, end = bounds(day)
    expected = int((end - start) / STEP)
    issues = set()
    candidates = []
    parsed = []
    outside = 0
    raw_count = 0
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("root_not_object")
        blocks = payload.get("consolidated_power_consumption")
        if not isinstance(blocks, list) or not blocks:
            raise ValueError("missing_consumption_blocks")
        rows = []
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("values"), list):
                raise ValueError("missing_values_array")
            rows.extend(block["values"])
    except (ValueError, TypeError, UnicodeError) as exc:
        issues.add("invalid_payload:" + str(exc))
        candidates.append((-1, body.decode("utf-8", errors="replace")))
        rows = []

    raw_count = len(rows)
    for index, row in enumerate(rows):
        raw = json.dumps(row, ensure_ascii=False)
        try:
            if not isinstance(row, dict):
                raise ValueError("record_not_object")
            left = timestamp(row["start_date"])
            right = timestamp(row["end_date"])
            if right <= left:
                raise ValueError("invalid_interval_order")
            if right <= start or left >= end:
                outside += 1
                continue
            candidates.append((index, raw))
            if left < start or right > end:
                raise ValueError("crosses_day_boundary")
            if right - left != STEP or (left - start) % STEP:
                raise ValueError("invalid_half_hour_interval")
            value = row["value"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("non_numeric_consumption")
            if not math.isfinite(value) or value <= 0:
                raise ValueError("invalid_consumption")
            status = row["status"]
            if status not in ("FINAL", "PROVISORY"):
                raise ValueError("invalid_status")
            updated = timestamp(row["updated_date"])
            parsed.append((left, right, float(value), status, updated))
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            if not candidates or candidates[-1][0] != index:
                candidates.append((index, raw))
            issues.add("invalid_record:" + type(exc).__name__ + ":" + str(exc))

    parsed.sort(key=lambda row: row[0])
    if not parsed:
        issues.add("no_valid_intervals")
    cursor = start
    for row in parsed:
        if row[0] != cursor:
            issues.add("missing_duplicate_or_overlapping_interval")
        cursor = row[1]
    if cursor != end or len(parsed) != expected:
        issues.add("incomplete_day")
    if day in QUARANTINE_DATES:
        issues.add("agreed_dst_quarantine")

    reason = ";".join(sorted(issues))
    accepted = [] if issues else parsed
    rejected = [
        (day, source_file, index, raw, reason)
        for index, raw in candidates
    ] if issues else []
    quality = (
        day, source_file, raw_count, expected, len(accepted),
        len(rejected), outside, reason or "complete",
    )
    return accepted, rejected, quality


def self_test():
    def fixture(day):
        start, end = bounds(day)
        rows = []
        cursor = start
        while cursor < end:
            rows.append({
                "start_date": cursor.astimezone(PARIS).isoformat(),
                "end_date": (cursor + STEP).astimezone(PARIS).isoformat(),
                "value": 50000,
                "status": "FINAL",
                "updated_date": "2026-10-31T00:00:00+00:00",
            })
            cursor += STEP
        return rows

    def evaluate(day, rows):
        body = json.dumps({"consolidated_power_consumption": [
            {"values": rows}
        ]}).encode()
        return analyse_day(day, "fixture.json", body)

    normal = date(2024, 1, 1)
    rows = fixture(normal)
    assert len(evaluate(normal, rows)[0]) == 48
    assert len(evaluate(date(2024, 3, 31), fixture(date(2024, 3, 31)))[0]) == 46
    fall = date(2024, 10, 27)
    accepted, rejected, quality = evaluate(fall, fixture(fall))
    assert not accepted and len(rejected) == 50
    assert quality[3] == 50 and "agreed_dst_quarantine" in quality[-1]
    assert not evaluate(normal, rows[:-1])[0]
    assert not evaluate(normal, rows + rows[:1])[0]
    damaged = [dict(row) for row in rows]
    del damaged[0]["updated_date"]
    assert len(evaluate(normal, damaged)[1]) == 48
    damaged = [dict(row) for row in rows]
    damaged[0]["start_date"] = "2024-01-01T00:00:00"
    assert not evaluate(normal, damaged)[0]
    extra = fixture(normal + timedelta(days=1))[:2]
    assert len(evaluate(normal, rows + extra)[0]) == 48
    assert evaluate(normal, rows + extra)[2][6] == 2
    assert not analyse_day(normal, "bad.json", b"{broken")[0]
    assert re.search(FILE_PATTERN,
        "/Volumes/workspace/bronze/rte_raw/2024/01/01/"
        "response_20261101T070000123456Z_1234abcd.json")
    print("Self-test passed: normal/spring/fall days, gaps, duplicates, "
          "malformed records, missing offsets, outside rows and invalid JSON.")


def read_latest(spark, root, first, last):
    """Use filename download time, never upload modification time."""
    from pyspark.sql import Window, functions as F

    files = spark.read.format("binaryFile").load(
        root.rstrip("/") + "/[0-9][0-9][0-9][0-9]/*/*/response_*.json"
    )
    names = files.select(
        "path", "content",
        F.concat_ws("-",
            F.regexp_extract("path", FILE_PATTERN, 1),
            F.regexp_extract("path", FILE_PATTERN, 2),
            F.regexp_extract("path", FILE_PATTERN, 3),
        ).alias("date_text"),
        F.regexp_extract("path", FILE_PATTERN, 4).alias("download_stamp"),
    )
    if names.filter(F.col("download_stamp") == "").limit(1).count():
        raise RuntimeError("Unrecognized Bronze filename; no tables updated.")
    names = names.withColumn("local_date", F.to_date("date_text"))
    if names.filter(F.col("local_date").isNull()).limit(1).count():
        raise RuntimeError("Invalid date directory; no tables updated.")
    names = names.filter(
        (F.col("local_date") >= F.lit(first))
        & (F.col("local_date") < F.lit(last))
    )
    ranked = names.withColumn("rn", F.row_number().over(
        Window.partitionBy("local_date").orderBy(
            F.col("download_stamp").desc(), F.col("path").desc()
        )
    ))
    selected = ranked.filter("rn = 1").select(
        "local_date", "path", "content"
    ).orderBy("local_date").collect()
    expected = {first + timedelta(days=i) for i in range((last - first).days)}
    missing = expected - {row.local_date for row in selected}
    if missing:
        sample = ", ".join(str(day) for day in sorted(missing)[:10])
        raise RuntimeError("Missing Bronze dates; no tables updated: " + sample)
    return selected


def merge_snapshot(spark, table, frame, keys, scope):
    """Synchronize only the explicitly requested interval."""
    view = "_silver_source_" + table.rsplit(".", 1)[-1]
    frame.createOrReplaceTempView(view)
    columns = frame.columns
    join = " AND ".join("t." + key + " = s." + key for key in keys)
    updates = ", ".join("t." + name + " = s." + name for name in columns)
    insert_columns = ", ".join(columns)
    insert_values = ", ".join("s." + name for name in columns)
    spark.sql(
        "MERGE INTO " + table + " t USING " + view + " s ON " + join
        + " WHEN MATCHED THEN UPDATE SET " + updates
        + " WHEN NOT MATCHED THEN INSERT (" + insert_columns + ") VALUES ("
        + insert_values + ") WHEN NOT MATCHED BY SOURCE AND " + scope
        + " THEN DELETE"
    )
    spark.catalog.dropTempView(view)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--input-dir", default="/Volumes/workspace/bronze/rte_raw")
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.end is None or args.start >= args.end:
        parser.error("--end is required and must be later than --start")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.catalog):
        parser.error("Invalid catalog identifier")

    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    selected = read_latest(spark, args.input_dir, args.start, args.end)
    clean, rejected, quality = [], [], []
    for index, item in enumerate(selected, 1):
        rows, bad, summary = analyse_day(
            item.local_date, item.path, bytes(item.content)
        )
        clean.extend(rows)
        rejected.extend(bad)
        quality.append(summary)
        if index % 250 == 0:
            print("Checked", index, "/", len(selected), "dates", flush=True)

    if not clean:
        raise RuntimeError("No accepted records; no tables updated.")
    if len({row[0] for row in clean}) != len(clean):
        raise RuntimeError("Duplicate accepted timestamps; no tables updated.")
    print("Selected dates:", len(selected))
    print("Accepted rows:", len(clean))
    print("Quarantined rows:", len(rejected))
    print("Days by outcome:", dict(Counter(row[-1] for row in quality)))
    for row in quality:
        if row[-1] != "complete":
            print("REVIEW", row[0], row[-1])
    if args.dry_run:
        print("DRY RUN finished: no tables updated.")
        return

    base = args.catalog + ".silver."
    schemas = {
        "consumption": (
            "ts TIMESTAMP, end_ts TIMESTAMP, consumption_mw DOUBLE, "
            "status STRING, updated_date TIMESTAMP"
        ),
        "consumption_quarantine": (
            "local_date DATE, source_file STRING, row_index INT, "
            "raw_record STRING, reason STRING"
        ),
        "consumption_quality": (
            "local_date DATE, source_file STRING, raw_records INT, "
            "expected_intervals INT, accepted_records INT, "
            "quarantined_records INT, outside_records INT, outcome STRING"
        ),
    }
    for name, schema in schemas.items():
        spark.sql("CREATE TABLE IF NOT EXISTS " + base + name
                  + " (" + schema + ") USING DELTA")
    day_scope = (
        "t.local_date >= DATE '" + str(args.start)
        + "' AND t.local_date < DATE '" + str(args.end) + "'"
    )
    for name, values, keys in (
        ("consumption_quality", quality, ["local_date"]),
        ("consumption_quarantine", rejected, ["local_date", "row_index"]),
    ):
        frame = spark.createDataFrame(values, schema=schemas[name])
        merge_snapshot(spark, base + name, frame, keys, day_scope)

    start_utc = bounds(args.start)[0].strftime("%Y-%m-%d %H:%M:%S")
    end_utc = bounds(args.end)[0].strftime("%Y-%m-%d %H:%M:%S")
    time_scope = (
        "t.ts >= TIMESTAMP '" + start_utc
        + "' AND t.ts < TIMESTAMP '" + end_utc + "'"
    )
    frame = spark.createDataFrame(clean, schema=schemas["consumption"])
    merge_snapshot(spark, base + "consumption", frame, ["ts"], time_scope)
    print("Finished: Silver interval synchronized. "
          "Quarantine and quality tables updated.")


if __name__ == "__main__":
    main()
# Silver transformation: run --self-test before using Databricks.
