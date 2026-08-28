from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class InboundHub:
    """In-memory queue so SI page (userscript) can trigger the local Playwright worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.flag = "idle"
        self.note = ""
        self.entries: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counts: dict[str, int] = {}
            for e in self.entries:
                status = str(e.get("status") or "")
                counts[status] = counts.get(status, 0) + 1
            return {
                "ok": True,
                "flag": self.flag,
                "note": self.note,
                "counts": counts,
                "entries": [dict(e) for e in self.entries],
            }

    def has_pending_batch(self) -> bool:
        with self._lock:
            return self.flag == "pending" and any(
                e.get("status") == "pending" for e in self.entries
            )

    def is_stop_requested(self) -> bool:
        with self._lock:
            return self.flag == "stop"

    def enqueue(self, orders: list[str], force: bool = False) -> dict[str, Any]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in orders:
            order_no = str(raw or "").strip()
            if not order_no or order_no in seen:
                continue
            seen.add(order_no)
            cleaned.append(order_no)
        if not cleaned:
            return {"ok": False, "error": "没有有效单号"}

        with self._lock:
            if self.flag in {"pending", "running"} and not force:
                return {
                    "ok": False,
                    "error": "队列占用中（pending/running）。确认覆盖请传 force=true。",
                    "flag": self.flag,
                }
            stamp = _now()
            self.entries = [
                {
                    "queue_row": i + 1,
                    "order_no": order_no,
                    "row_number": 0,
                    "status": "pending",
                    "note": "from SI",
                    "updated_at": stamp,
                }
                for i, order_no in enumerate(cleaned)
            ]
            self.flag = "pending"
            self.note = f"si enqueue count={len(cleaned)} at {stamp}"
            return {
                "ok": True,
                "count": len(cleaned),
                "orders": cleaned,
                "flag": self.flag,
            }

    def request_stop(self) -> dict[str, Any]:
        with self._lock:
            if self.flag not in {"pending", "running"}:
                return {
                    "ok": False,
                    "error": f"当前没有等待/执行中的批次（flag={self.flag}）",
                    "flag": self.flag,
                }
            cancelled = 0
            stamp = _now()
            for entry in self.entries:
                if entry.get("status") == "pending":
                    entry["status"] = "cancelled"
                    entry["note"] = "cancelled by stop"
                    entry["updated_at"] = stamp
                    cancelled += 1
            self.flag = "stop"
            self.note = f"stop requested, cancelled={cancelled}"
            return {"ok": True, "cancelled": cancelled, "flag": self.flag}

    def pending_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self.entries if e.get("status") == "pending"]

    def set_flag(self, flag: str, note: str = "") -> None:
        with self._lock:
            self.flag = flag
            self.note = note or _now()

    def set_batch_flag(self, flag: str, note: str = "") -> None:
        self.set_flag(flag, note)

    def update_entry(self, queue_row: int, status: str, note: str = "") -> None:
        with self._lock:
            for entry in self.entries:
                if entry.get("queue_row") == queue_row:
                    entry["status"] = status
                    entry["note"] = note
                    entry["updated_at"] = _now()
                    return

    def update_queue_entry(self, queue_row: int, status: str, note: str = "") -> None:
        self.update_entry(queue_row, status, note)

    def cancel_remaining_pending(self, note: str = "cancelled after error") -> int:
        with self._lock:
            count = 0
            stamp = _now()
            for entry in self.entries:
                if entry.get("status") == "pending":
                    entry["status"] = "cancelled"
                    entry["note"] = note
                    entry["updated_at"] = stamp
                    count += 1
            return count
