from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required env: {name}")
    return value


SHEET_ID = env("GOOGLE_SHEET_ID", "1yrR83W15kKevye87ksYnELUY68i_j4_kIIY_gu0bAWU")
SHEET_GID = env("GOOGLE_SHEET_GID", "0")
ORDER_COLUMN = env("ORDER_COLUMN", "A").upper()
HEADER_ROWS = int(env("HEADER_ROWS", "1"))
SERVICE_ACCOUNT_FILE = Path(
    env(
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        str(ROOT / "credentials" / "unimap-put-in-storage-google-service-account.json"),
    )
).expanduser()
if not SERVICE_ACCOUNT_FILE.is_absolute():
    SERVICE_ACCOUNT_FILE = (ROOT / SERVICE_ACCOUNT_FILE).resolve()

LOCAL_CSV_FILE = os.getenv("LOCAL_CSV_FILE")
if LOCAL_CSV_FILE:
    LOCAL_CSV_PATH = Path(LOCAL_CSV_FILE).expanduser()
    if not LOCAL_CSV_PATH.is_absolute():
        LOCAL_CSV_PATH = (ROOT / LOCAL_CSV_PATH).resolve()
else:
    LOCAL_CSV_PATH = None

UNIMAP_URL = env("UNIMAP_URL", "https://dispatch.uniuni.com/main")
BROWSER_PROFILE_DIR = Path(
    env("BROWSER_PROFILE_DIR", str(ROOT / ".browser-profile"))
).expanduser()
if not BROWSER_PROFILE_DIR.is_absolute():
    BROWSER_PROFILE_DIR = (ROOT / BROWSER_PROFILE_DIR).resolve()

TARGET_STATUS = 215
OPERATION_LOCATION = "NJ Warehouse"
OPERATION_LOCATION_ALIASES = [
    "NJ Warehouse",
    "NJ warehouse",
    "NJ WAREHOUSE",
]
NETWORK_NODE = "WH- JFK-005"
NETWORK_NODE_ALIASES = [
    "WH- JFK-005",
    "WH-JFK-005",
    "WH JFK-005",
    "WH_JFK_005",
]
FAIL_REASON = "parcel damaged"
FAIL_REASON_ALIASES = [
    "PARCEL_DAMAGED",
    "parcel damaged",
    "Parcel Damaged",
    "PARCEL DAMAGED",
]
