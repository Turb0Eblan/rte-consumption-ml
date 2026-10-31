# RTE API access check

Checked on: 2026-10-31 at 15:25 UTC.

## Access

- API: Consolidated Consumption v1
- Resource: consolidated_power_consumption
- Token request: HTTP 200
- Consumption request: HTTP 200
- No date parameters were supplied.
- According to control rule CPC-RG01, this request returns
  the consumption history for the last known month.

## Returned data

- Returned month: August 2026
- Record count: 1488
- Earliest interval start: 2026-08-01T00:00:00+02:00
- Latest interval start: 2026-08-31T23:30:00+02:00
- Latest interval end: 2026-09-01T00:00:00+02:00
- Observed interval duration: 30 minutes
- First and last record status: PROVISORY
- First and last record updated_date:
  2026-09-25T13:34:47+02:00

The record count matches 31 days at 48 intervals per day.
Duplicate and gap checks remain to be performed.

## Implications for forecasting

The default latest-month response returned August data
at the time of this check. Recent consumption lags must not
be assumed available for next-day forecasts.

Availability must be checked at each forecast issue time.
Evaluation against actual November consumption may be delayed
until those values are published by this resource.

Measurement units still need confirmation from the resource guide.

## Credentials and local output

Credentials remain in the ignored local .env file.
Tokens are neither printed nor saved.

The response is saved locally to:
data/api_check/consumption_response.json

Raw data is not committed to Git.