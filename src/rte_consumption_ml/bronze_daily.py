"""Refresh published RTE Bronze data, preserving raw bytes and verified checkpoints.

GitLab supplies credentials through protected CI/CD variables.
--dry-run performs authenticated reads only; --self-test uses in-memory fakes.
"""

import argparse
import calendar
import hashlib
import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from rte_consumption_ml.bronze_backfill import API_URL, TOKEN_URL, records_from, valid_day

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc
DEFAULT_VOLUME = "/Volumes/workspace/bronze/rte_raw"
STATE_PATH = "_orchestration/state.json"
KNOWN_REVIEW_DATES = {
    "2020-10-25", "2021-10-31", "2022-10-30",
    "2023-10-29", "2024-10-27", "2025-10-26",
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def digest(body):
    return hashlib.sha256(body).hexdigest()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Timestamp has no timezone")
    return parsed


def structured_records(payload):
    if not isinstance(payload, dict):
        raise ValueError("Unexpected API response root")
    blocks = payload.get("consolidated_power_consumption")
    if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks):
        raise ValueError("Unexpected API response blocks")
    rows = records_from(payload)
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Unexpected API record")
    return rows


def published_end(raw, today):
    """CPC-RG01: derive availability from actual rows, never from a fixed lag."""
    rows = structured_records(json.loads(raw))
    if not rows:
        raise ValueError("Discovery returned no published rows")
    days = [timestamp(row["start_date"]).astimezone(PARIS).date() for row in rows]
    latest = max(days)
    if latest >= today or latest < date(1996, 1, 1):
        raise ValueError("Discovery returned an invalid publication horizon")
    return latest + timedelta(days=1)


def months_before(day, count):
    index = day.year * 12 + day.month - 1 - count
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def plan_window(cursor, end, months, first, max_days):
    if end < cursor:
        raise ValueError("Publication horizon moved backwards; inspect before continuing")
    start = max(first, min(cursor, months_before(end, months)))
    if start >= end or (end - start).days > max_days:
        raise ValueError("Invalid or excessive refresh range; inspect the proposed dates")
    return start, end


def inspect_day(raw, day):
    """Every received HTTP-200 body is retained, including malformed JSON."""
    following = day + timedelta(days=1)
    start = datetime.combine(day, datetime.min.time(), PARIS)
    end = datetime.combine(following, datetime.min.time(), PARIS)
    result = {
        "date": str(day),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "expected_physical_intervals": int(
            (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() / 1800
        ),
        "returned_records": None,
        "validated_intervals": None,
        "coverage_status": "needs_review",
        "coverage_error": None,
    }
    try:
        payload = json.loads(raw)
        rows = structured_records(payload)
        result["returned_records"] = len(rows)
        for row in rows:
            value = row["value"]
            if (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0
            ):
                raise ValueError("Invalid consumption value")
            if row["status"] not in ("FINAL", "PROVISORY"):
                raise ValueError("Invalid consumption status")
            timestamp(row["updated_date"])
            timestamp(row["start_date"])
            timestamp(row["end_date"])
        result["validated_intervals"] = valid_day(payload, start, end)
        result["coverage_status"] = "complete"
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        # Do not copy arbitrary payload contents into logs or quality errors.
        result["coverage_error"] = (
            str(error) if isinstance(error, ValueError)
            and not isinstance(error, json.JSONDecodeError)
            and not isinstance(error, UnicodeError)
            else type(error).__name__
        )
    return result


def make_session():
    session = requests.Session()
    retry = Retry(
        total=4, backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "PUT"],
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def checked(response, label, allowed=(200,)):
    if response.status_code not in allowed:
        # In particular, HTTP 403 is never treated as "not published".
        raise RuntimeError(f"{label}: HTTP {response.status_code}")
    return response


class RteClient:
    def __init__(self, session, client_id, client_secret):
        self.session = session
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = None
        self.deadline = 0.0

    def authenticate(self):
        response = checked(self.session.post(
            TOKEN_URL, auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            timeout=(10, 30), allow_redirects=False,
        ), "RTE authentication")
        result = response.json()
        self.token = result["access_token"]
        self.deadline = time.monotonic() + max(
            1.0, float(result.get("expires_in", 300)) - 60
        )

    def get(self, params=None):
        if self.token is None or time.monotonic() >= self.deadline:
            self.authenticate()
        def request():
            return self.session.get(
                API_URL, params=params,
                headers={"Authorization": "Bearer " + self.token},
                timeout=(10, 60), allow_redirects=False,
            )
        response = request()
        if response.status_code == 401:
            self.authenticate()
            response = request()
        return checked(response, "RTE data request").content

    def discover(self):
        return self.get()

    def fetch_day(self, day):
        start = datetime.combine(day, datetime.min.time(), PARIS)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), PARIS)
        return self.get({"start_date": start.isoformat(), "end_date": end.isoformat()})


class VolumeStore:
    """Files API only: no account APIs, Spark or broad token scopes needed."""
    def __init__(self, session, host, token, volume):
        self.session = session
        self.host = host.rstrip("/")
        self.volume = volume.rstrip("/")
        self.headers = {"Authorization": "Bearer " + token}

    def path(self, relative):
        if (
            not relative or relative.startswith("/") or "\\" in relative
            or any(part in ("", ".", "..") for part in relative.split("/"))
        ):
            raise ValueError("Invalid relative Bronze path")
        return self.volume + "/" + relative

    def get(self, relative):
        response = self.session.get(
            self.host + "/api/2.0/fs/files" + self.path(relative),
            headers=self.headers, timeout=(10, 60), allow_redirects=False,
        )
        if response.status_code == 404:
            return None
        return checked(response, "Bronze download").content

    def put(self, relative, body, overwrite=False):
        path = self.path(relative)
        parent = path.rsplit("/", 1)[0]
        checked(self.session.put(
            self.host + "/api/2.0/fs/directories" + parent,
            headers=self.headers, timeout=(10, 30), allow_redirects=False,
        ), "Bronze directory creation", (200, 201, 204))
        response = self.session.put(
            self.host + "/api/2.0/fs/files" + path,
            params={"overwrite": str(overwrite).lower()},
            headers={**self.headers, "Content-Type": "application/octet-stream"},
            data=body, timeout=(10, 60), allow_redirects=False,
        )
        # A retry after an acknowledged-late PUT may find the same unique file.
        if response.status_code == 409 and not overwrite:
            if self.get(relative) != body:
                raise RuntimeError("Existing immutable Bronze path has different bytes")
        else:
            checked(response, "Bronze upload", (200, 201, 204))
        if self.get(relative) != body:
            raise RuntimeError("Bronze upload read-back verification failed")


def load_state(store, volume):
    raw = store.get(STATE_PATH)
    if raw is None:
        return None
    state = json.loads(raw)
    if (
        not isinstance(state, dict) or state.get("schema_version") != 1
        or state.get("volume") != volume or not isinstance(state.get("days"), dict)
    ):
        raise ValueError("Unexpected orchestration checkpoint")
    date.fromisoformat(state["last_end"])
    return state


def validate_entry(day, entry):
    relative = entry.get("raw_file", "")
    pattern = (
        re.escape(day.strftime("%Y/%m/%d"))
        + r"/response_\d{8}T\d{12}Z_[0-9a-f]{8}\.json"
    )
    if (
        not re.fullmatch(pattern, relative)
        or entry.get("quality_file") != "_quality/" + relative
        or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", ""))
    ):
        raise ValueError("Invalid per-day checkpoint entry")


def refresh(client, store, state, start, end, volume, discovery, quiet=False):
    """Commit the cursor only after every raw body and quality report is verified."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_id = stamp + "_" + uuid4().hex[:8]
    run_root = "_orchestration/runs/" + run_id
    store.put(run_root + "/discovery.json", discovery)
    entries = dict(state["days"]) if state else {}
    report = {
        "run_id": run_id, "start": str(start), "end": str(end),
        "new_responses": 0, "unchanged_responses": 0,
        "needs_review": [], "unexpected_review": [],
    }
    current = start
    while current < end:
        raw = client.fetch_day(current)
        previous = entries.get(str(current))
        sha = digest(raw)
        if previous:
            validate_entry(current, previous)
        if previous and previous["sha256"] == sha:
            # Do not trust checkpoint metadata alone; verify the retained bytes.
            if store.get(previous["raw_file"]) != raw:
                raise RuntimeError(f"{current}: checkpoint/raw integrity mismatch")
            saved_quality = store.get(previous["quality_file"])
            if saved_quality is None:
                raise RuntimeError(f"{current}: checkpoint quality report is missing")
            quality = inspect_day(raw, current)
            old_quality = json.loads(saved_quality)
            compared = ("date", "coverage_status", "validated_intervals",
                        "returned_records", "expected_physical_intervals")
            if any(old_quality.get(key) != quality.get(key) for key in compared):
                raise RuntimeError(f"{current}: quality changed; review checkpoint")
            report["unchanged_responses"] += 1
        else:
            fresh_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            filename = "response_" + fresh_stamp + "_" + uuid4().hex[:8] + ".json"
            relative = current.strftime("%Y/%m/%d/") + filename
            # Raw bytes first; malformed content still gets a sidecar report.
            store.put(relative, raw)
            quality = inspect_day(raw, current)
            quality["raw_file"] = relative
            quality["sha256"] = sha
            quality_file = "_quality/" + relative
            store.put(quality_file, encoded(quality))
            entries[str(current)] = {
                "sha256": sha, "raw_file": relative, "quality_file": quality_file,
            }
            report["new_responses"] += 1
        if quality["coverage_status"] != "complete":
            report["needs_review"].append(str(current))
            if str(current) not in KNOWN_REVIEW_DATES:
                report["unexpected_review"].append(str(current))
        if not quiet:
            print(f"CHECKED {current}: {quality['coverage_status']}; "
                  f"new={report['new_responses']}, "
                  f"unchanged={report['unchanged_responses']}", flush=True)
        current += timedelta(days=1)
        if not quiet:
            time.sleep(1)
    report["status"] = (
        "completed_with_review" if report["needs_review"] else "complete"
    )
    store.put(run_root + "/report.json", encoded(report))
    # Checkpoint records successful fetching, including explicitly reported review days.
    # Readers must run after this job finishes, not during raw/quality pair uploads.
    checkpoint = {
        "schema_version": 1, "volume": volume, "last_end": str(end),
        "last_run": run_id, "days": entries,
    }
    store.put(STATE_PATH, encoded(checkpoint), overwrite=True)
    return report


def self_test():
    class FakeStore:
        def __init__(self):
            self.files = {}
            self.fail_quality = False
        def get(self, path):
            return self.files.get(path)
        def put(self, path, body, overwrite=False):
            if self.fail_quality and path.startswith("_quality/"):
                raise RuntimeError("Simulated interrupted quality upload")
            if path in self.files and not overwrite:
                raise RuntimeError("Attempted immutable overwrite")
            self.files[path] = body

    def body(day, missing=False):
        start = datetime.combine(day, datetime.min.time(), PARIS).astimezone(UTC)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), PARIS)
        rows = []
        while start < end.astimezone(UTC):
            right = start + timedelta(minutes=30)
            rows.append({
                "start_date": start.isoformat(), "end_date": right.isoformat(),
                "value": 50000, "status": "FINAL",
                "updated_date": "2026-01-01T00:00:00+00:00",
            })
            start = right
        if missing:
            rows.pop()
        return encoded({"consolidated_power_consumption": [{"values": rows}]})

    class FakeClient:
        def __init__(self, bodies):
            self.bodies = bodies
        def fetch_day(self, day):
            return self.bodies[day]

    assert months_before(date(2024, 5, 31), 3) == date(2024, 2, 29)
    assert months_before(date(2026, 1, 1), 3) == date(2025, 10, 1)
    first = date(2020, 1, 1)
    assert plan_window(date(2026, 9, 1), date(2026, 10, 1), 3, first, 400)[0] == date(2026, 7, 1)
    assert plan_window(date(2026, 1, 1), date(2026, 10, 1), 3, first, 400)[0] == date(2026, 1, 1)
    try:
        plan_window(date(2026, 10, 1), date(2026, 9, 1), 3, first, 400)
    except ValueError:
        pass
    else:
        raise AssertionError("Backward availability was accepted")

    for day, expected in (
        (date(2026, 2, 1), 48), (date(2026, 3, 29), 46),
        (date(2026, 10, 25), 50),
    ):
        result = inspect_day(body(day), day)
        assert result["coverage_status"] == "complete"
        assert result["validated_intervals"] == expected
    day = date(2026, 8, 31)
    end = day + timedelta(days=1)
    raw = body(day)
    assert published_end(raw, date(2026, 11, 1)) == end
    for bad in (b"not-json", b"[]", b'{"consolidated_power_consumption":[null]}'):
        assert inspect_day(bad, day)["coverage_status"] == "needs_review"

    store = FakeStore()
    client = FakeClient({day: raw})
    first_run = refresh(client, store, None, day, end, DEFAULT_VOLUME, raw, quiet=True)
    assert first_run["new_responses"] == 1
    checkpoint = load_state(store, DEFAULT_VOLUME)
    raw_path = checkpoint["days"][str(day)]["raw_file"]
    second = refresh(client, store, checkpoint, day, end, DEFAULT_VOLUME, raw, quiet=True)
    assert second["unchanged_responses"] == 1 and second["new_responses"] == 0
    assert len([p for p in store.files if p.startswith("2026/")]) == 1

    # A revised raw body must get a new immutable response path.
    changed = json.loads(raw)
    changed["consolidated_power_consumption"][0]["values"][0]["value"] = 50001
    client.bodies[day] = encoded(changed)
    revised = refresh(client, store, checkpoint, day, end, DEFAULT_VOLUME, raw, quiet=True)
    assert revised["new_responses"] == 1
    assert store.get(raw_path) == raw
    checkpoint = load_state(store, DEFAULT_VOLUME)
    cursor_before = store.get(STATE_PATH)
    client.bodies[day] = body(day, missing=True)
    store.fail_quality = True
    try:
        refresh(client, store, checkpoint, day, end, DEFAULT_VOLUME, raw, quiet=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Simulated upload failure was ignored")
    assert store.get(STATE_PATH) == cursor_before

    # Malformed responses are saved and reported rather than dropped.
    store = FakeStore()
    bad = b"not-json"
    result = refresh(FakeClient({day: bad}), store, None, day, end,
                     DEFAULT_VOLUME, raw, quiet=True)
    assert result["unexpected_review"] == [str(day)]
    entry = load_state(store, DEFAULT_VOLUME)["days"][str(day)]
    assert store.get(entry["raw_file"]) == bad
    assert json.loads(store.get(entry["quality_file"]))["coverage_status"] == "needs_review"

    # Stored-data tampering must not be concealed as an unchanged successful refresh.
    store.files[entry["raw_file"]] = b"tampered"
    checkpoint = load_state(store, DEFAULT_VOLUME)
    cursor_before = store.get(STATE_PATH)
    try:
        refresh(FakeClient({day: bad}), store, checkpoint, day, end,
                DEFAULT_VOLUME, raw, quiet=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Integrity mismatch was ignored")
    assert store.get(STATE_PATH) == cursor_before
    # Authentication failures are never mistaken for an unpublished period.
    class FakeResponse:
        def __init__(self, status, content=b"", result=None):
            self.status_code = status
            self.content = content
            self.result = result
        def json(self):
            return self.result

    class FakeSession:
        def __init__(self, statuses):
            self.statuses = list(statuses)
            self.auth_calls = 0
            self.used_headers = []
        def post(self, url, **kwargs):
            self.auth_calls += 1
            return FakeResponse(200, result={
                "access_token": "fake-token-" + str(self.auth_calls),
                "expires_in": 3600,
            })
        def get(self, url, **kwargs):
            self.used_headers.append(kwargs["headers"]["Authorization"])
            return FakeResponse(self.statuses.pop(0), content=raw)

    session = FakeSession([401, 200])
    assert RteClient(session, "fake-id", "fake-secret").discover() == raw
    assert session.auth_calls == 2
    assert session.used_headers == ["Bearer fake-token-1", "Bearer fake-token-2"]
    try:
        RteClient(FakeSession([403]), "fake-id", "fake-secret").discover()
    except RuntimeError as error:
        assert "HTTP 403" in str(error)
    else:
        raise AssertionError("HTTP 403 was incorrectly accepted")

    print("Self-test passed: publication discovery, calendar windows, DST, "
          "revisions, unchanged-response deduplication, raw-first reports, "
          "interrupted uploads, checkpoint integrity and 401/403 handling.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--initial-end", type=date.fromisoformat,
                        help="Exclusive end of the already verified historical backfill")
    parser.add_argument("--refresh-months", type=int, default=3)
    parser.add_argument("--history-start", type=date.fromisoformat,
                        default=date(2020, 1, 1))
    parser.add_argument("--max-days", type=int, default=400)
    parser.add_argument("--volume", default=DEFAULT_VOLUME)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not 1 <= args.refresh_months <= 12 or args.max_days < 1:
        parser.error("Use 1-12 refresh months and a positive --max-days")
    if not re.fullmatch(r"/Volumes/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", args.volume):
        parser.error("Invalid Volume path")
    required = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "RTE_CLIENT_ID", "RTE_CLIENT_SECRET")
    if any(not os.environ.get(key) for key in required):
        raise RuntimeError("Required ingestion environment variables are missing")
    host = os.environ["DATABRICKS_HOST"].strip().rstrip("/")
    if host != "https://dbc-ef009879-5f31.cloud.databricks.com":
        raise RuntimeError("Unexpected Databricks workspace host")
    with make_session() as session:
        client = RteClient(session, os.environ["RTE_CLIENT_ID"], os.environ["RTE_CLIENT_SECRET"])
        store = VolumeStore(session, host, os.environ["DATABRICKS_TOKEN"], args.volume)
        state = load_state(store, args.volume)
        cursor = date.fromisoformat(state["last_end"]) if state else args.initial_end
        if cursor is None or cursor < args.history_start:
            raise ValueError("First run requires a valid --initial-end bootstrap boundary")
        discovery = client.discover()
        end = published_end(discovery, datetime.now(PARIS).date())
        start, end = plan_window(cursor, end, args.refresh_months,
                                 args.history_start, args.max_days)
        print(f"Latest published exclusive end: {end}", flush=True)
        print(f"Verified backfill/checkpoint exclusive end: {cursor}", flush=True)
        print(f"Planned refresh: {start} inclusive -> {end} exclusive", flush=True)
        print(f"Days to fetch: {(end - start).days}", flush=True)
        if args.dry_run:
            print("DRY RUN finished: no files uploaded and no checkpoint changed.")
            return
        report = refresh(client, store, state, start, end, args.volume, discovery)
        print(encoded(report).decode("utf-8"), flush=True)
        if report["unexpected_review"]:
            print("REVIEW: unexpected incomplete/malformed days were preserved. "
                  "Inspect quality reports before running downstream tasks.")
            raise SystemExit(2)
        print("PASS: daily Bronze refresh uploaded and verified.")


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, OSError, ValueError, KeyError, TypeError,
            OverflowError, RuntimeError) as error:
        if isinstance(error, RuntimeError):
            print(f"Stopped: {error}")
        else:
            print(f"Stopped: {type(error).__name__}")
        raise SystemExit(1)
