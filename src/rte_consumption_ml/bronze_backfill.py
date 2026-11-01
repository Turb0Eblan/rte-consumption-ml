"""Download historical RTE responses into daily Bronze JSON files."""

import argparse
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


TOKEN_URL = "https://digital.iservices.rte-france.com/token/oauth/"
API_URL = (
    "https://digital.iservices.rte-france.com/open_api/"
    "consolidated_consumption/v1/consolidated_power_consumption"
)
PARIS = ZoneInfo("Europe/Paris")


def records_from(payload):
    blocks = payload.get("consolidated_power_consumption")
    if not isinstance(blocks, list):
        raise ValueError("Unexpected API response structure")

    records = []
    for block in blocks:
        values = block.get("values")
        if not isinstance(values, list):
            raise ValueError("Missing values array")
        records.extend(values)
    return records


def valid_day(payload, start, end):
    records = records_from(payload)
    if not records:
        raise ValueError("No published records for this day")

    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    intervals = []
    outside = 0

    for record in records:
        left = datetime.fromisoformat(record["start_date"])
        right = datetime.fromisoformat(record["end_date"])

        if left.utcoffset() is None or right.utcoffset() is None:
            raise ValueError("Timestamp has no timezone")

        left = left.astimezone(timezone.utc)
        right = right.astimezone(timezone.utc)

        if right <= left:
            raise ValueError("Invalid interval duration")

        if right <= start_utc or left >= end_utc:
            outside += 1
            continue

        if left < start_utc or right > end_utc:
            raise ValueError("Interval crosses the requested day boundary")

        intervals.append((left, right))

    intervals.sort()
    cursor = start_utc

    for left, right in intervals:
        if left != cursor or right - left != timedelta(minutes=30):
            raise ValueError("Missing, duplicate, or irregular interval")
        cursor = right

    if cursor != end_utc:
        raise ValueError("Incomplete day")

    if outside:
        print(
            f"INFO: {outside} extra intervals retained in raw JSON; "
            "excluded from this day's coverage check"
        )

    return len(intervals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        required=True,
        help="Exclusive end date",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--secret-scope", default=None)
    args = parser.parse_args()

    if args.start >= args.end:
        parser.error("--start must be earlier than --end")




    if args.secret_scope:
        from databricks.sdk import WorkspaceClient

        secrets = WorkspaceClient().dbutils.secrets
        client_id = secrets.get(
            scope=args.secret_scope,
            key="rte-client-id",
        )
        client_secret = secrets.get(
            scope=args.secret_scope,
            key="rte-client-secret",
        )
    else:
        project_root = Path(__file__).resolve().parents[2]
        load_dotenv(project_root / ".env", override=False)
        client_id = os.environ["RTE_CLIENT_ID"]
        client_secret = os.environ["RTE_CLIENT_SECRET"]

    if not client_id or not client_secret:
        raise RuntimeError("RTE credentials are missing")



    retries = Retry(
        total=4,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )

    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=retries))
        token = None
        token_deadline = 0.0

        def authenticate():
            nonlocal token, token_deadline
            response = session.post(
                TOKEN_URL,
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                timeout=30,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Authentication HTTP {response.status_code}"
                )
            result = response.json()
            token = result["access_token"]
            lifetime = float(result.get("expires_in", 300))
            token_deadline = time.monotonic() + max(1, lifetime - 60)

        current = args.start
        saved = 0
        skipped = 0

        while current < args.end:
            following = current + timedelta(days=1)
            start = datetime.combine(current, datetime.min.time(), PARIS)
            end = datetime.combine(following, datetime.min.time(), PARIS)
            folder = args.output_dir / current.strftime("%Y/%m/%d")

            existing = False
            for previous in sorted(folder.glob("response_*.json")):
                try:
                    valid_day(
                        json.loads(previous.read_text(encoding="utf-8")),
                        start,
                        end,
                    )
                    existing = True
                    break
                except (ValueError, KeyError, TypeError):
                    continue

            if existing:
                print(f"SKIP {current}: validated file already exists")
                skipped += 1
                current = following
                continue

            if token is None or time.monotonic() >= token_deadline:
                authenticate()

            def fetch():
                return session.get(
                    API_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "start_date": start.isoformat(),
                        "end_date": end.isoformat(),
                    },
                    timeout=60,
                    allow_redirects=False,
                )

            response = fetch()
            if response.status_code == 401:
                authenticate()
                response = fetch()

            if response.status_code != 200:
                raise RuntimeError(
                    f"{current}: API HTTP {response.status_code}"
                )

            # Save the original response before checking day coverage.
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            filename = f"response_{stamp}_{uuid4().hex[:8]}.json"
            destination = folder / filename

            with destination.open("xb") as output:
                output.write(response.content)

            payload = response.json()

            # Coverage is measured against the actual Europe/Paris day.
            # A failed check is recorded; it does not imply missing values
            # can be reconstructed or that the source response is complete.
            try:
                count = valid_day(payload, start, end)
                coverage_status = "complete"
                coverage_error = None
            except ValueError as exc:
                count = None
                coverage_status = "needs_review"
                coverage_error = str(exc)

            quality = {
                "date": current.isoformat(),
                "raw_file": destination.relative_to(
                    args.output_dir
                ).as_posix(),
                "requested_start": start.isoformat(),
                "requested_end": end.isoformat(),
                "returned_records": len(records_from(payload)),
                "expected_physical_intervals": int(
                    (
                        end.astimezone(timezone.utc)
                        - start.astimezone(timezone.utc)
                    ).total_seconds() / 1800
                ),
                "validated_intervals": count,
                "coverage_status": coverage_status,
                "coverage_error": coverage_error,
            }

            quality_folder = (
                args.output_dir
                / "_quality"
                / current.strftime("%Y/%m/%d")
            )
            quality_folder.mkdir(parents=True, exist_ok=True)
            report = quality_folder / filename

            with report.open("x", encoding="utf-8") as output:
                json.dump(quality, output, ensure_ascii=False, indent=2)

            print(
                f"SAVED {current}: "
                f"{quality['returned_records']} raw records -> "
                f"{destination}"
            )

            if coverage_status == "needs_review":
                print(
                    f"WARNING {current}: coverage needs review: "
                    f"{coverage_error}. Report: {report}"
                )
            else:
                print(f"VALIDATED {current}: {count} intervals")

            saved += 1
            current = following
            time.sleep(1)

        print(f"Finished: saved={saved}, skipped={skipped}")


if __name__ == "__main__":
    try:
        main()
    except (
        requests.RequestException,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
    ) as exc:
        # Avoid printing request objects, headers, or credentials.
        if isinstance(exc, RuntimeError):
            print(f"Stopped: {exc}")
        else:
            print(f"Stopped: {type(exc).__name__}")
        raise SystemExit(1)
