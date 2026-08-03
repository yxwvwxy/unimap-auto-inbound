#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from py import config
from py.sheets import BlankRowError, SheetOrder, get_sheets_client, load_orders
from py.unimap import (
    advance_order_to_target,
    launch_browser,
    open_unimap,
    wait_for_manual_login,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="UniMap auto-inbound to status 215")
    p.add_argument("--login-only", action="store_true", help="Save Microsoft login session only")
    p.add_argument("--dry-run", action="store_true", help="Plan transitions without submitting")
    p.add_argument("--order", help="Process a single order number")
    p.add_argument("--limit", type=int, help="Process only the first N orders")
    p.add_argument(
        "--watch",
        action="store_true",
        help="Watch Google Sheet Apps Script queue and process confirmed jobs",
    )
    p.add_argument(
        "--next",
        action="store_true",
        help="Process the smallest-row unchecked order (terminal confirm)",
    )
    p.add_argument(
        "--poll-seconds",
        type=float,
        default=3.0,
        help="Queue poll interval for --watch (default 3s)",
    )
    return p.parse_args()


def confirm_start(order_no: str) -> bool:
    msg = f"从{order_no}单号开始进行入库闭环"
    print(f"\n{msg}")
    answer = input("按 Enter 确定开始，或输入 n 取消: ").strip().lower()
    return answer not in {"n", "no", "cancel", "q"}


def process_one(page, item: SheetOrder, dry_run: bool, sheets=None) -> dict:
    print(f"Processing {item.order_no} (row {item.row_number})")
    result = advance_order_to_target(page, item.order_no, dry_run=dry_run)
    if result["ok"]:
        print(f"  OK -> {result['final_status']}")
        if sheets and item.row_number > 0 and not dry_run:
            sheets.mark_done(item.row_number)
            print(f"  Checked B{item.row_number}")
    else:
        print(f"  FAIL: {result.get('error')}")
    return {
        "order_no": item.order_no,
        "row_number": item.row_number,
        "ok": result["ok"],
        "final_status": result["final_status"],
        "error": result.get("error"),
        "steps": result.get("steps"),
        "stop_run": bool(result.get("stop_run")),
    }


def run_watch(page, dry_run: bool, poll_seconds: float) -> int:
    sheets = get_sheets_client()
    print(
        "Watching Sheet queue「入库队列」.\n"
        "选中 A 列起始单号 → 一键入库 → 从选中单号开始\n"
        "会把该行起直到空行的所有单号写入队列，再按序搜索入库。\n"
        "停止：菜单「停止连续执行」。浏览器保持打开，继续搜下一单。\n"
        "本批结束后 terminal 不用关。Ctrl+C 退出监听。\n"
    )
    while True:
        try:
            if not sheets.has_pending_batch():
                time.sleep(poll_seconds)
                continue

            entries = [
                e for e in sheets.read_queue_entries() if e["status"] == "pending"
            ]
            if not entries:
                sheets.set_batch_flag("idle", "no pending rows")
                time.sleep(poll_seconds)
                continue

            print(f"\n======= 新批次：{len(entries)} 单待执行 =======")
            for i, e in enumerate(entries, start=1):
                print(f"  {i}. {e['order_no']} (sheet row {e['row_number']})")
            sheets.set_batch_flag("running", f"batch size={len(entries)}")

            done_count = 0
            stopped = False

            for idx, entry in enumerate(entries, start=1):
                if sheets.is_stop_requested():
                    cancelled = sheets.cancel_remaining_pending()
                    sheets.set_batch_flag(
                        "stopped",
                        f"stopped after {done_count} done, cancelled={cancelled}",
                    )
                    print(
                        f"\n已停止连续执行（完成 {done_count} 单，取消剩余 {cancelled} 单）。"
                        "\n浏览器保持打开。选中新单号再点菜单即可继续。"
                    )
                    stopped = True
                    break

                item = SheetOrder(
                    row_number=entry["row_number"], order_no=entry["order_no"]
                )
                print(
                    f"\n--- [{idx}/{len(entries)}] {item.order_no} "
                    f"(sheet row {item.row_number}) ---"
                )
                sheets.update_queue_entry(
                    entry["queue_row"], "running", f"batch {idx}/{len(entries)}"
                )

                try:
                    result = process_one(page, item, dry_run=dry_run, sheets=sheets)
                except Exception as err:
                    # 单单异常：标记 error，浏览器不关，整批停下等你重选
                    sheets.update_queue_entry(entry["queue_row"], "error", str(err))
                    sheets.cancel_remaining_pending("cancelled after error")
                    sheets.set_batch_flag("error", str(err))
                    print(f"\n处理异常：{err}")
                    print("浏览器保持打开。terminal 仍在监听，可重新选单号开始。")
                    stopped = True
                    break

                if not result["ok"]:
                    err = result.get("error") or "failed"
                    sheets.update_queue_entry(entry["queue_row"], "error", err)
                    sheets.cancel_remaining_pending("cancelled after error")
                    sheets.set_batch_flag("error", err)
                    print(f"\n本单失败，已停止本批。原因：{err}")
                    print("浏览器保持打开，将直接可用于下一批评列（不用重启）。")
                    stopped = True
                    break

                sheets.update_queue_entry(
                    entry["queue_row"],
                    "done",
                    f"final={result.get('final_status')}",
                )
                done_count += 1
                print(f"  队列进度 {done_count}/{len(entries)}，继续下一单号搜索…")

            if not stopped:
                sheets.set_batch_flag(
                    "done", f"batch complete done={done_count}/{len(entries)}"
                )
                print(
                    f"\n本批完成：{done_count}/{len(entries)} 单。"
                    "\n浏览器保持打开。可再选中单号点「从选中单号开始」。"
                )

        except Exception as err:
            print(f"\n监听循环出错（浏览器不关闭）：{err}")
            try:
                sheets.set_batch_flag("error", str(err))
            except Exception:
                pass

        time.sleep(poll_seconds)


def main() -> int:
    args = parse_args()

    config.SERVICE_ACCOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    needs_sheet = args.watch or args.next or (not args.login_only and not args.order)
    sheets = None
    if needs_sheet and not config.LOCAL_CSV_PATH:
        if not config.SERVICE_ACCOUNT_FILE.exists() and (args.watch or args.next):
            print(
                "ERROR: --watch / --next need Google service account Editor access.\n"
                f"Place JSON at: {config.SERVICE_ACCOUNT_FILE}\n"
                "Share the Sheet with client_email as Editor."
            )
            return 1

    if not args.login_only and not args.order and not args.watch and not args.next:
        try:
            preview = load_orders()
        except BlankRowError as err:
            print(f"STOP: {err}")
            return 2
        print(f"Loaded {len(preview)} unchecked order(s).")
        if not preview:
            print("No unchecked order numbers found.")
            return 1

    playwright, context = launch_browser()
    exit_code = 0
    try:
        page = open_unimap(context)
        wait_for_manual_login(page)

        if args.login_only:
            print("Login session saved in .browser-profile/.")
            return 0

        if args.watch:
            try:
                exit_code = run_watch(page, dry_run=args.dry_run, poll_seconds=args.poll_seconds) or 0
            except KeyboardInterrupt:
                print("\nStopped watching.")
                exit_code = 0
            print("\n按 Enter 关闭浏览器窗口...")
            try:
                input()
            except EOFError:
                pass
            return exit_code

        if args.next:
            sheets = get_sheets_client()
            try:
                item = sheets.find_next_unchecked()
            except BlankRowError as err:
                print(f"STOP: {err}")
                return 2
            if not item:
                print("No unchecked orders left.")
                return 0
            if not confirm_start(item.order_no):
                print("Cancelled.")
                return 0
            result = process_one(page, item, dry_run=args.dry_run, sheets=sheets)
            exit_code = 0 if result["ok"] else 2
            if exit_code != 0:
                print("\n浏览器保持打开。按 Enter 关闭...")
                try:
                    input()
                except EOFError:
                    pass
            return exit_code

        if args.order:
            orders = [SheetOrder(row_number=0, order_no=args.order)]
            # Try to resolve row for checkbox if sheet available
            if config.SERVICE_ACCOUNT_FILE.exists():
                try:
                    sheets = get_sheets_client()
                    for r, o, done in sheets.read_order_done_rows():
                        if o == args.order and not done:
                            orders = [SheetOrder(row_number=r, order_no=o)]
                            break
                except Exception:
                    sheets = None
        else:
            try:
                if config.SERVICE_ACCOUNT_FILE.exists() and not config.LOCAL_CSV_PATH:
                    sheets = get_sheets_client()
                    orders = sheets.load_pending_orders()
                else:
                    orders = load_orders()
            except BlankRowError as err:
                print(f"STOP: {err}")
                return 2

        selected = orders[: args.limit] if args.limit else orders
        print(
            f"\nProcessing {len(selected)} order(s){' [DRY RUN]' if args.dry_run else ''}...\n"
        )

        results = []
        stopped_early = False
        for idx, item in enumerate(selected, start=1):
            print(f"[{idx}/{len(selected)}] {item.order_no}")
            if not confirm_start(item.order_no):
                print("  Skipped by user.")
                results.append(
                    {
                        "order_no": item.order_no,
                        "ok": False,
                        "error": "skipped",
                        "stop_run": True,
                    }
                )
                stopped_early = True
                break
            one = process_one(page, item, dry_run=args.dry_run, sheets=sheets)
            results.append(one)
            if one.get("stop_run") or not one.get("ok"):
                print("\n已停止运行（未知状态或路径不一致），不再处理后续单号。")
                stopped_early = True
                break

        ok_count = sum(1 for r in results if r.get("ok"))
        fail_count = len(results) - ok_count
        print(f"\nDone. success={ok_count} failed={fail_count}" + (" (stopped early)" if stopped_early else ""))

        report_path = ROOT / f"run-report-{int(time.time())}.json"
        report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Report written: {report_path}")
        exit_code = 0 if fail_count == 0 else 2
        if exit_code != 0:
            print("\n浏览器保持打开。按 Enter 关闭...")
            try:
                input()
            except EOFError:
                pass
        return exit_code
    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            playwright.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
