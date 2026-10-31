"""Load local project configuration without exposing credentials."""

import os
from pathlib import Path

from dotenv import load_dotenv


def load_settings() -> dict[str, str]:
    """Load .env defaults, preserving existing environment variables."""
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=False)

    return {
        "rte_client_id": os.getenv("RTE_CLIENT_ID", ""),
        "rte_client_secret": os.getenv("RTE_CLIENT_SECRET", ""),
        "data_dir": os.getenv("DATA_DIR", "data"),
    }
