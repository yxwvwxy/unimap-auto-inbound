from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

from . import config

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
QUEUE_SHEET = "入库队列"
QUEUE_SHEET_LEGACY = "_UnimapQueue"


@dataclass
class SheetOrder:
    row_number: int
    order_no: str


class BlankRowError(RuntimeError):
    """A-column has a blank row; run must stop."""

    def __init__(self, row_number: int):
        self.row_number = row_number
        super().__init__(f"A{row_number} 为空行，已停止。请补全单号或删除空行后再运行。")


def _column_letter_to_index(letter: str) -> int:
    index = 0
    for ch in letter.upper():
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def _is_checked(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and value == 1:
        return True
    if isinstance(value, str) and value.strip().upper() in {"TRUE", "1", "✓", "YES", "DONE"}:
        return True
    return False


def _extract_orders(rows: List[List[str]]) -> List[SheetOrder]:
    col = _column_letter_to_index(config.ORDER_COLUMN)
    orders: List[SheetOrder] = []
    for idx, row in enumerate(rows):
        row_number = idx + 1
        if row_number <= config.HEADER_ROWS:
            continue
        value = (row[col] if col < len(row) else "").strip()
        if not value:
            continue
        if re.match(r"^(order|tracking|单号|order\s*no)", value, re.I):
            continue
        orders.append(SheetOrder(row_number=row_number, order_no=value))
    return orders


class SheetsClient:
    def __init__(self) -> None:
        if not config.SERVICE_ACCOUNT_FILE.exists():
            raise FileNotFoundError(
                f"Google service account file not found: {config.SERVICE_ACCOUNT_FILE}\n"
                "1) Put JSON at credentials/unimap-put-in-storage-google-service-account.json\n"
                "2) Share the Google Sheet with the service account client_email as Editor"
            )
        creds = service_account.Credentials.from_service_account_file(
            str(config.SERVICE_ACCOUNT_FILE),
            scopes=SCOPES,
        )
        self.service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.sheet_id = config.SHEET_ID
        self._title: Optional[str] = None
        self._queue_title: Optional[str] = None

    def main_sheet_title(self) -> str:
        if self._title:
            return self._title
        meta = self.service.spreadsheets().get(spreadsheetId=self.sheet_id).execute()
        sheets = meta.get("sheets", [])
        sheet = next(
            (
                s
                for s in sheets
                if str(s.get("properties", {}).get("sheetId")) == config.SHEET_GID
            ),
            sheets[0] if sheets else None,
        )
        if not sheet:
            raise RuntimeError("Could not resolve Google Sheet tab")
        self._title = sheet["properties"]["title"]
        return self._title

    def _a1(self, tab: str, range_part: str) -> str:
        safe = tab.replace("'", "''")
        return f"'{safe}'!{range_part}"

    def read_order_done_rows(self) -> List[Tuple[int, str, bool]]:
        """Return (row_number, order_no, done) for data rows.

        Stops with BlankRowError if column A has an empty cell in the data range.
        """
        title = self.main_sheet_title()
        # A:B covers order + checkbox
        result = (
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.sheet_id,
                range=self._a1(title, "A:B"),
                valueRenderOption="UNFORMATTED_VALUE",
            )
            .execute()
        )
        values = result.get("values", [])
        rows: List[Tuple[int, str, bool]] = []
        for idx, row in enumerate(values):
            row_number = idx + 1
            if row_number <= config.HEADER_ROWS:
                continue
            order_no = str(row[0]).strip() if row else ""
            if not order_no:
                raise BlankRowError(row_number)
            if re.match(r"^(order|tracking|单号|order\s*no)", order_no, re.I):
                continue
            done_val = row[1] if len(row) > 1 else False
            rows.append((row_number, order_no, _is_checked(done_val)))
        return rows

    def find_next_unchecked(self) -> Optional[SheetOrder]:
        for row_number, order_no, done in self.read_order_done_rows():
            if not done:
                return SheetOrder(row_number=row_number, order_no=order_no)
        return None

    def find_next_unchecked_after(self, after_row: int) -> Optional[SheetOrder]:
        """Next unchecked order with row_number strictly greater than after_row.

        Trailing blank A cells are treated as end-of-list (do not crash the watcher).
        """
        title = self.main_sheet_title()
        result = (
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.sheet_id,
                range=self._a1(title, "A:B"),
                valueRenderOption="UNFORMATTED_VALUE",
            )
            .execute()
        )
        values = result.get("values", [])
        for idx, row in enumerate(values):
            row_number = idx + 1
            if row_number <= config.HEADER_ROWS or row_number <= after_row:
                continue
            order_no = str(row[0]).strip() if row else ""
            if not order_no:
                # Contiguous block ended — stop scanning
                return None
            if re.match(r"^(order|tracking|单号|order\s*no)", order_no, re.I):
                continue
            done_val = row[1] if len(row) > 1 else False
            if not _is_checked(done_val):
                return SheetOrder(row_number=row_number, order_no=order_no)
        return None

    def mark_done(self, row_number: int) -> None:
        title = self.main_sheet_title()
        self.service.spreadsheets().values().update(
            spreadsheetId=self.sheet_id,
            range=self._a1(title, f"B{row_number}"),
            valueInputOption="USER_ENTERED",
            body={"values": [[True]]},
        ).execute()

    def load_pending_orders(self) -> List[SheetOrder]:
        return [
            SheetOrder(row_number=r, order_no=o)
            for r, o, done in self.read_order_done_rows()
            if not done
        ]

    def _queue_tab(self) -> str:
        """Prefer 入库队列; fall back to legacy _UnimapQueue."""
        if self._queue_title:
            return self._queue_title
        meta = self.service.spreadsheets().get(spreadsheetId=self.sheet_id).execute()
        titles = {
            s.get("properties", {}).get("title")
            for s in meta.get("sheets", [])
        }
        if QUEUE_SHEET in titles:
            self._queue_title = QUEUE_SHEET
        elif QUEUE_SHEET_LEGACY in titles:
            self._queue_title = QUEUE_SHEET_LEGACY
        else:
            self._queue_title = QUEUE_SHEET
        return self._queue_title

    def get_batch_flag(self) -> str:
        tab = self._queue_tab()
        try:
            result = (
                self.service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=self.sheet_id,
                    range=self._a1(tab, "G1"),
                    valueRenderOption="UNFORMATTED_VALUE",
                )
                .execute()
            )
        except Exception as err:
            if "Unable to parse range" in str(err) or "not found" in str(err).lower():
                return ""
            raise
        values = result.get("values") or []
        if not values or not values[0]:
            return ""
        return str(values[0][0] or "").strip().lower()

    def set_batch_flag(self, flag: str, note: str = "") -> None:
        from datetime import datetime, timezone

        tab = self._queue_tab()
        stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        body_note = note or stamp
        self.service.spreadsheets().values().update(
            spreadsheetId=self.sheet_id,
            range=self._a1(tab, "G1:H1"),
            valueInputOption="USER_ENTERED",
            body={"values": [[flag, body_note]]},
        ).execute()

    def read_queue_entries(self) -> List[dict]:
        """All order rows in 入库队列 (A2:E)."""
        tab = self._queue_tab()
        try:
            result = (
                self.service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=self.sheet_id,
                    range=self._a1(tab, "A2:E"),
                    valueRenderOption="UNFORMATTED_VALUE",
                )
                .execute()
            )
        except Exception as err:
            if "Unable to parse range" in str(err) or "not found" in str(err).lower():
                return []
            raise

        entries: List[dict] = []
        for idx, row in enumerate(result.get("values") or []):
            row = row + [""] * 5
            order_no = str(row[0]).strip()
            if not order_no:
                continue
            try:
                sheet_row = int(row[1])
            except (TypeError, ValueError):
                sheet_row = 0
            entries.append(
                {
                    "queue_row": idx + 2,  # 1-based row in queue sheet
                    "order_no": order_no,
                    "row_number": sheet_row,
                    "status": str(row[2]).strip().lower(),
                    "note": str(row[4]).strip(),
                }
            )
        return entries

    def has_pending_batch(self) -> bool:
        """True when Apps Script queued a new batch (G1=pending) with pending lines."""
        if self.get_batch_flag() != "pending":
            return False
        return any(e["status"] == "pending" for e in self.read_queue_entries())

    def is_stop_requested(self) -> bool:
        return self.get_batch_flag() == "stop"

    def update_queue_entry(
        self,
        queue_row: int,
        status: str,
        note: str = "",
    ) -> None:
        from datetime import datetime, timezone

        tab = self._queue_tab()
        self.service.spreadsheets().values().update(
            spreadsheetId=self.sheet_id,
            range=self._a1(tab, f"C{queue_row}:E{queue_row}"),
            valueInputOption="USER_ENTERED",
            body={
                "values": [
                    [
                        status,
                        datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                        note,
                    ]
                ]
            },
        ).execute()

    def cancel_remaining_pending(self, note: str = "cancelled by stop") -> int:
        count = 0
        for entry in self.read_queue_entries():
            if entry["status"] == "pending":
                self.update_queue_entry(entry["queue_row"], "cancelled", note)
                count += 1
        return count


def _read_local_csv(path) -> List[SheetOrder]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    return _extract_orders(rows)


def _read_public_csv() -> List[SheetOrder]:
    url = (
        f"https://docs.google.com/spreadsheets/d/{config.SHEET_ID}"
        f"/export?format=csv&gid={config.SHEET_GID}"
    )
    res = requests.get(url, timeout=30)
    text = res.text
    if (
        not res.ok
        or "Sign in to your Google Account" in text
        or text.lstrip().startswith("<!DOCTYPE html>")
    ):
        raise RuntimeError(
            "Public CSV export unavailable (sheet is private). "
            "Use a service account (Editor) for checkbox sync."
        )
    rows = list(csv.reader(io.StringIO(text)))
    return _extract_orders(rows)


def load_orders() -> List[SheetOrder]:
    if config.LOCAL_CSV_PATH:
        print(f"Reading orders from local CSV: {config.LOCAL_CSV_PATH}")
        return _read_local_csv(config.LOCAL_CSV_PATH)

    if config.SERVICE_ACCOUNT_FILE.exists():
        print("Reading unchecked orders via Google service account...")
        return SheetsClient().load_pending_orders()

    print("Trying public Google Sheet CSV export...")
    return _read_public_csv()


def get_sheets_client() -> SheetsClient:
    return SheetsClient()
