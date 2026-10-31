"""Check RTE API access and save the consumption response."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from rte_consumption_ml.config import load_settings


def main():
    settings = load_settings()

    client_id = settings["rte_client_id"].strip()
    client_secret = settings["rte_client_secret"].strip()
    token_url = os.getenv("RTE_TOKEN_URL", "").strip()
    consumption_url = os.getenv("RTE_CONSUMPTION_URL", "").strip()

    if not all([client_id, client_secret, token_url, consumption_url]):
        raise SystemExit("Missing configuration. Check your .env file.")

    expected_token_url = (
        "https://digital.iservices.rte-france.com/token/oauth/"
    )
    expected_consumption_url = (
        "https://digital.iservices.rte-france.com/open_api/"
        "consolidated_consumption/v1/consolidated_power_consumption"
    )

    if token_url != expected_token_url:
        raise SystemExit("Incorrect RTE_TOKEN_URL.")

    if consumption_url != expected_consumption_url:
        raise SystemExit("Incorrect RTE_CONSUMPTION_URL.")

    try:
        # Step 1: Obtain an access token.
        token_response = requests.post(
            token_url,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            timeout=30,
            allow_redirects=False,
        )

        print("Token HTTP status:", token_response.status_code)

        if token_response.status_code != 200:
            raise SystemExit(
                "Authentication failed. Check credentials and API subscription."
            )

        token = token_response.json().get("access_token")

        if not token:
            raise SystemExit("No access token returned.")

        print("Authentication successful. Token is not displayed.")

        # Step 2: Retrieve the last known month, without date parameters.
        response = requests.get(
            consumption_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=60,
            allow_redirects=False,
        )

        print("Consumption HTTP status:", response.status_code)

        if response.status_code != 200:
            raise SystemExit(
                "Consumption request did not return HTTP 200."
            )

        payload = response.json()

    except requests.RequestException as exc:
        raise SystemExit(
            f"Network error: {type(exc).__name__}"
        ) from None
    except ValueError:
        raise SystemExit("The response was not valid JSON.") from None

    # Step 3: Save consumption data locally.
    output_dir = Path(settings["data_dir"]) / "api_check"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "consumption_response.json"
    output_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Checked at UTC:", datetime.now(timezone.utc).isoformat())
    print("Data saved to:", output_file)

    if isinstance(payload, dict):
        print("JSON keys:", list(payload.keys()))

    print("Open the JSON file to inspect the returned records.")


if __name__ == "__main__":
    main()