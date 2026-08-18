from __future__ import annotations

import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright

from . import config
from .transitions import (
    KNOWN_STATUSES,
    TRANSITION_ALIASES,
    PathMismatchError,
    TransitionStep,
    find_step_for_status,
    require_known_status,
)

DEBUG_DIR = Path(__file__).resolve().parents[1] / "debug"


def _escape_regex(text: str) -> str:
    return re.escape(text)


def _first_visible(locators: List[Locator]) -> Optional[Locator]:
    for loc in locators:
        try:
            count = loc.count()
        except Exception:
            continue
        for i in range(count):
            item = loc.nth(i)
            try:
                if item.is_visible():
                    return item
            except Exception:
                continue
    return None


def _click_by_text(page: Page, patterns: List[str], timeout: int = 15000) -> None:
    locators: List[Locator] = []
    for text in patterns:
        locators.append(page.get_by_role("tab", name=re.compile(text, re.I)))
        locators.append(page.get_by_role("menuitem", name=re.compile(text, re.I)))
        locators.append(page.get_by_role("button", name=re.compile(text, re.I)))
        locators.append(page.get_by_role("link", name=re.compile(text, re.I)))
        locators.append(page.locator(f"text=/{_escape_regex(text)}/i"))

    target = _first_visible(locators)
    if not target:
        raise RuntimeError(f"Could not find clickable element matching: {' | '.join(patterns)}")
    target.click(timeout=timeout)


def launch_browser() -> Tuple[object, BrowserContext]:
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(config.BROWSER_PROFILE_DIR),
        headless=False,
        viewport={"width": 1400, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    return playwright, context


def open_unimap(context: BrowserContext) -> Page:
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(config.UNIMAP_URL, wait_until="domcontentloaded")
    return page


def wait_for_manual_login(page: Page) -> None:
    print("\n=== Microsoft / UniUni login ===")
    print("1. Complete Microsoft login / MFA in the opened browser.")
    print("2. Wait until you see the UniUni dispatch main page with the left menu.")
    print("3. Come back here and press Enter to continue.\n")
    input()
    if "dispatch.uniuni.com" not in page.url:
        page.goto(config.UNIMAP_URL, wait_until="domcontentloaded")


def _edit_order_modal(page: Page) -> Optional[Locator]:
    """Visible Edit Order overlay/modal, if any (may not be Ant Design)."""
    candidates = [
        page.locator(
            ".ant-modal:visible, .ant-modal-wrap:visible, [role='dialog']:visible"
        ).filter(has_text=re.compile(r"edit\s*order", re.I)),
        # Custom UniMap overlay: has Edit Order title + order search label
        page.locator("div, section, aside").filter(
            has_text=re.compile(r"Order No\s*/\s*unit No\s*/\s*parcel No", re.I)
        ),
        page.locator("div, section, aside").filter(
            has=page.get_by_text(re.compile(r"Tracking Info", re.I))
        ).filter(has_text=re.compile(r"edit\s*order", re.I)),
    ]
    for loc in candidates:
        try:
            if loc.count() > 0 and loc.last.is_visible():
                return loc.last
        except Exception:
            continue
    return None


def _edit_order_root(page: Page) -> Locator:
    """Prefer the open Edit Order modal; fall back to the whole page."""
    modal = _edit_order_modal(page)
    return modal if modal is not None else page.locator("body")


def _is_edit_order_open(page: Page) -> bool:
    # Strong signals from the Edit Order overlay (not the Menu tile)
    markers = [
        page.get_by_text(re.compile(r"Order No\s*/\s*unit No\s*/\s*parcel No", re.I)),
        page.get_by_text(re.compile(r"^\s*Tracking Info\s*$", re.I)),
        page.get_by_text(re.compile(r"^\s*Order Information\s*$", re.I)),
        page.get_by_text(re.compile(r"^\s*Next Transition\s*$", re.I)),
    ]
    for marker in markers:
        try:
            if marker.count() > 0 and marker.first.is_visible():
                return True
        except Exception:
            continue
    if _edit_order_modal(page) is not None:
        return True
    return _find_order_search_input(page, required=False) is not None


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _locate_edit_order_click_point(page: Page) -> Optional[dict]:
    """Find the orange EDIT ORDER card and return its center {x,y,w,h,tag}."""
    return page.evaluate(
        """() => {
          const norm = (t) => (t || '').replace(/\\s+/g, ' ').trim();
          const nodes = Array.from(
            document.querySelectorAll('div,button,a,li,span,p,section,article')
          );

          // Candidates whose own text is exactly EDIT ORDER (the label or the card)
          const exact = [];
          for (const el of nodes) {
            const text = norm(el.innerText);
            if (!/^EDIT ORDER$/i.test(text)) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 8 || r.height < 8) continue;
            if (r.bottom < 0 || r.top > window.innerHeight) continue;
            if (r.right < 0 || r.left > window.innerWidth) continue;
            exact.push({ el, r });
          }
          if (!exact.length) return null;

          // Prefer a card-sized ancestor around the label (the orange tile).
          // Typical tile ~80-220px; avoid the whole sidebar.
          let best = null;
          for (const { el, r } of exact) {
            let card = el;
            let cardR = r;
            let p = el.parentElement;
            for (let depth = 0; p && depth < 6; depth++, p = p.parentElement) {
              const pr = p.getBoundingClientRect();
              const pText = norm(p.innerText);
              // Parent still only this tile (maybe icon + label), not whole menu
              if (!/^EDIT ORDER$/i.test(pText) && pText.length > 24) break;
              if (pr.width > 320 || pr.height > 320) break;
              if (pr.width >= 60 && pr.height >= 50) {
                card = p;
                cardR = pr;
                // keep walking a bit to reach the full orange tile if label is nested
              }
            }
            const area = cardR.width * cardR.height;
            // Prefer mid-size tiles over tiny text spans
            const score = area;
            if (!best || score > best.score) {
              // but reject huge panels
              if (cardR.width <= 320 && cardR.height <= 320 && cardR.width >= 50) {
                best = {
                  score,
                  x: cardR.left + cardR.width / 2,
                  y: cardR.top + cardR.height / 2,
                  w: Math.round(cardR.width),
                  h: Math.round(cardR.height),
                  tag: card.tagName,
                  text: norm(card.innerText).slice(0, 40),
                };
              }
            }
          }

          // Fallback: click the smallest exact text box itself
          if (!best) {
            exact.sort(
              (a, b) => a.r.width * a.r.height - b.r.width * b.r.height
            );
            const { el, r } = exact[0];
            best = {
              score: r.width * r.height,
              x: r.left + r.width / 2,
              y: r.top + r.height / 2,
              w: Math.round(r.width),
              h: Math.round(r.height),
              tag: el.tagName,
              text: norm(el.innerText).slice(0, 40),
            };
          }
          return best;
        }"""
    )


def _click_point(page: Page, x: float, y: float) -> None:
    """Hard mouse click at viewport coords — no auto-scroll."""
    page.mouse.click(x, y)


def go_to_edit_order(page: Page) -> None:
    page = ensure_live_page(page)
    if _is_edit_order_open(page):
        print("  Edit Order already open.")
        return

    if "dispatch.uniuni.com" in page.url and "/main" not in page.url:
        page.goto(config.UNIMAP_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(800)

    # Only switch to Menu rail if EDIT ORDER card is not already on screen
    point = _locate_edit_order_click_point(page)
    if not point:
        try:
            menu_tab = page.get_by_text(re.compile(r"^\s*Menu\s*$", re.I)).first
            if menu_tab.count() > 0 and menu_tab.is_visible():
                box = menu_tab.bounding_box()
                if box:
                    _click_point(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    page.wait_for_timeout(500)
        except Exception:
            pass
        point = _locate_edit_order_click_point(page)

    if not point:
        raise RuntimeError(
            "Could not locate the orange EDIT ORDER card on the Menu grid. "
            "Leave the Menu page open so EDIT ORDER is visible."
        )

    x, y = float(point["x"]), float(point["y"])
    print(
        f"  Clicking EDIT ORDER card at ({x:.0f},{y:.0f}) "
        f"size={point.get('w')}x{point.get('h')} tag={point.get('tag')}"
    )
    # Direct mouse click — do NOT scrollIntoView / Playwright auto-scroll
    _click_point(page, x, y)
    page.wait_for_timeout(1200)

    for _ in range(20):
        if _is_edit_order_open(page):
            print("  Edit Order overlay open.")
            return
        page.wait_for_timeout(400)

    if _find_order_search_input(page, required=False):
        print("  Edit Order search box found.")
        return

    # One retry click (still no scrolling)
    print("  Overlay not open yet — clicking EDIT ORDER once more...")
    point2 = _locate_edit_order_click_point(page) or point
    _click_point(page, float(point2["x"]), float(point2["y"]))
    page.wait_for_timeout(1500)
    if _is_edit_order_open(page) or _find_order_search_input(page, required=False):
        print("  Edit Order overlay open.")
        return

    raise RuntimeError(
        "Clicked EDIT ORDER but the overlay did not open. "
        "Click the orange EDIT ORDER card once manually, then re-queue."
    )


def _find_order_search_input(page: Page, required: bool = True) -> Optional[Locator]:
    root = _edit_order_root(page)
    candidates = [
        # Label from screenshot: "Order No/unit No/parcel No"
        root.get_by_placeholder(
            re.compile(r"order|tracking|单号|parcel|unit|uus", re.I)
        ),
        root.locator("input[placeholder*='Order' i]"),
        root.locator("input[placeholder*='order' i]"),
        root.locator("input[placeholder*='parcel' i]"),
        root.locator("input[placeholder*='unit' i]"),
        root.locator('input[type="search"]'),
        # Ant Design text inputs in modal / page (skip checkboxes etc.)
        root.locator(
            "input.ant-input:not([type='hidden']):not([type='checkbox']):not([type='radio'])"
        ),
        root.locator(
            "input:not([type='hidden']):not([type='checkbox']):not([type='radio']):not([type='password'])"
        ),
    ]
    input_el = _first_visible(candidates)
    if not input_el and required:
        raise RuntimeError("Could not find order search input on Edit Order page")
    return input_el


def _click_search(page: Page) -> None:
    root = _edit_order_root(page)
    search_btn = _first_visible(
        [
            root.get_by_role("button", name=re.compile(r"search|查询|搜", re.I)),
            root.locator("button:has-text('Search')"),
            root.locator(".ant-btn:has-text('Search')"),
            root.locator("[aria-label*='search' i]"),
            root.locator(".anticon-search").locator("xpath=ancestor::button[1]"),
            root.locator(".anticon-search").locator(
                "xpath=ancestor::*[self::button or self::span][1]"
            ),
        ]
    )
    if search_btn:
        search_btn.click()
    else:
        page.keyboard.press("Enter")
    page.wait_for_timeout(2000)


def search_order(page: Page, order_no: str) -> None:
    page = ensure_live_page(page)
    go_to_edit_order(page)
    inp = _find_order_search_input(page)
    assert inp is not None
    print(f"  Searching order {order_no}...")
    inp.click(click_count=3)
    inp.fill(order_no)
    _click_search(page)
    # Wait for tracking / order panel (searches can be slow)
    try:
        page.locator(
            "text=/Tracking Info|ORDER_RECEIVED|Order Information|Batch Info/i"
        ).first.wait_for(state="visible", timeout=15000)
    except Exception:
        pass
    # Wait out loading spinner if present
    try:
        page.locator(".ant-spin-spinning").first.wait_for(state="hidden", timeout=15000)
    except Exception:
        pass
    try:
        found = page.locator(f"text=/{_escape_regex(order_no)}/i").first.is_visible()
    except Exception:
        found = False
    if not found:
        print(f"  Warning: order {order_no} text not clearly visible after search; continuing...")


def ensure_live_page(page: Page) -> Page:
    """Return a live page in the same browser context (never close the browser)."""
    try:
        if not page.is_closed():
            return page
    except Exception:
        pass
    ctx = page.context
    for p in ctx.pages:
        try:
            if not p.is_closed():
                p.bring_to_front()
                return p
        except Exception:
            continue
    return ctx.new_page()


def expand_operation(page: Page) -> Page:
    """Open the Operation panel without clicking unrelated 'operation' text elsewhere.

    Returns the (possibly recovered) live page — caller must keep using the return value.
    """
    page = ensure_live_page(page)
    try:
        if page.locator("text=/next transition/i").first.is_visible():
            return page
    except Exception:
        pass

    # Prefer collapse/panel headers — avoid broad page-wide text clicks that can
    # hit wrong controls and feel like the page "closed".
    candidates = [
        page.locator(".ant-collapse-header", has_text=re.compile(r"^\s*operation\s*$", re.I)),
        page.locator(".ant-collapse-header", has_text=re.compile(r"operation", re.I)),
        page.get_by_role("button", name=re.compile(r"^\s*operation\s*$", re.I)),
        page.get_by_role("tab", name=re.compile(r"^\s*operation\s*$", re.I)),
        page.locator("div, span, a", has_text=re.compile(r"^\s*operation\s*$", re.I)),
    ]
    target = _first_visible(candidates)
    if not target:
        raise RuntimeError("找不到 Operation 面板入口")

    print("  Opening Operation panel...")
    target.click()
    page.wait_for_timeout(1200)
    page = ensure_live_page(page)
    try:
        page.bring_to_front()
    except Exception:
        pass

    try:
        page.locator("text=/next transition/i").first.wait_for(state="visible", timeout=8000)
    except Exception as err:
        raise RuntimeError(
            "已点击 Operation，但找不到 Next Transition。"
            "浏览器会保持打开，请手动看当前页面。"
        ) from err
    return page


def _extract_status_codes(text: str) -> List[int]:
    """Extract status codes from lines like '199: GATEWAY_TRANSIT' (order preserved)."""
    codes: List[int] = []
    for code_str in re.findall(
        r"\b(\d{3,4})\s*:\s*[A-Z][A-Z0-9_ ]+",
        text,
        flags=re.I,
    ):
        code = int(code_str)
        if code in KNOWN_STATUSES:
            codes.append(code)
    return codes


def read_current_status(page: Page) -> Optional[int]:
    """Read the *latest* Tracking Info status (not the oldest historical one)."""
    tracking_text = ""

    # Prefer the Tracking Info sidebar — timeline lists oldest→newest, take LAST.
    try:
        header = page.get_by_text(re.compile(r"Tracking Info", re.I)).first
        if header.count() and header.is_visible():
            box = header.locator(
                "xpath=ancestor::*[contains(@class,'ant-') or self::aside or self::section or self::div][1]"
            )
            # Walk up a couple levels to include the full timeline list
            for _ in range(3):
                try:
                    candidate = box.inner_text(timeout=1000)
                    if re.search(r"\d{3,4}\s*:\s*[A-Z]", candidate, re.I):
                        tracking_text = candidate
                        break
                    box = box.locator("xpath=..")
                except Exception:
                    break
    except Exception:
        pass

    if not tracking_text:
        try:
            tracking_text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            tracking_text = ""

    codes = _extract_status_codes(tracking_text)
    if codes:
        latest = codes[-1]
        print(f"  Status from Tracking Info (latest): {latest}  history={codes}")
        return latest

    return None


def _save_debug(page: Page, tag: str) -> None:
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        path = DEBUG_DIR / f"{tag}-{int(time.time())}.png"
        page.screenshot(path=str(path), full_page=True)
        print(f"  Debug screenshot: {path}")
    except Exception as err:
        print(f"  Could not save screenshot: {err}")


def _open_labeled_dropdown(page: Page, field_label: re.Pattern) -> None:
    """Open dropdown by floating label text (e.g. Next Transition)."""
    # Important: use get_by_text(pattern), NOT f"text={pattern_object}"
    label = page.get_by_text(field_label).first
    try:
        label_visible = label.count() > 0 and label.is_visible()
    except Exception:
        label_visible = False

    select_candidates: List[Locator] = []
    if label_visible:
        select_candidates.extend(
            [
                label.locator(
                    "xpath=ancestor::*[contains(@class,'ant-form-item') or contains(@class,'FormControl') or contains(@class,'form-item') or contains(@class,'MuiFormControl')][1]"
                ).locator(
                    ".ant-select-selector, .ant-select, [role='combobox'], .MuiSelect-select, .MuiOutlinedInput-root"
                ),
                label.locator(
                    "xpath=following::*[contains(@class,'ant-select-selector') or contains(@class,'ant-select') or @role='combobox' or contains(@class,'MuiSelect') or contains(@class,'MuiOutlinedInput')][1]"
                ),
            ]
        )

    select_candidates.append(
        page.locator(".ant-form-item, .MuiFormControl-root, [class*='FormControl']")
        .filter(has_text=field_label)
        .locator(
            ".ant-select-selector, .ant-select, [role='combobox'], .MuiSelect-select, .MuiOutlinedInput-root"
        )
    )

    target = _first_visible(select_candidates)
    if not target:
        if label_visible:
            label.click()
            page.wait_for_timeout(400)
            return
        raise RuntimeError(f"找不到字段下拉框: {field_label.pattern}")

    print(f"  Opening dropdown: {field_label.pattern}")
    target.click()
    page.wait_for_timeout(500)


def _select_dropdown_option(
    page: Page,
    field_label: re.Pattern,
    option_text: str,
    aliases: Optional[List[str]] = None,
) -> None:
    aliases = aliases or []
    options: List[str] = []
    for opt in [option_text, *aliases]:
        if opt not in options:
            options.append(opt)

    _open_labeled_dropdown(page, field_label)

    for opt in options:
        option_locators = [
            page.get_by_role("option", name=re.compile(rf"^{_escape_regex(opt)}$", re.I)),
            page.locator(
                ".ant-select-item-option-content, .ant-select-item, [role='option'], li",
                has_text=re.compile(rf"^{_escape_regex(opt)}$", re.I),
            ),
            page.locator(
                ".ant-select-item-option-content, .ant-select-item, [role='option'], li",
                has_text=re.compile(_escape_regex(opt), re.I),
            ),
            page.get_by_text(re.compile(rf"^{_escape_regex(opt)}$", re.I)),
        ]
        option = _first_visible(option_locators)
        if option:
            print(f"  Selecting: {opt}")
            option.click()
            page.wait_for_timeout(400)
            return

        page.keyboard.type(opt, delay=20)
        page.wait_for_timeout(300)
        filtered = _first_visible(option_locators)
        if filtered:
            print(f"  Selecting (filtered): {opt}")
            filtered.click()
            page.wait_for_timeout(400)
            return
        for _ in range(len(opt)):
            page.keyboard.press("Backspace")

    raise RuntimeError(
        f'在 "{field_label.pattern}" 下找不到选项 "{option_text}" '
        f"（也试过: {', '.join(options[:4])}）"
    )


def _dialog_root(page: Page) -> Locator:
    """Visible modal/dialog after first Submit (fail-reason popup etc.)."""
    return page.locator(
        ".ant-modal:visible, .ant-modal-wrap:visible, [role='dialog']:visible, "
        ".MuiModal-root:visible, .MuiDialog-root:visible"
    ).last


def _click_submit(page: Page, *, within_dialog: bool = False) -> None:
    root: Page | Locator = _dialog_root(page) if within_dialog else page
    btn = _first_visible(
        [
            root.get_by_role("button", name=re.compile(r"^submit$", re.I)),
            root.locator("button:has-text('Submit')"),
            root.locator(".ant-btn-primary:has-text('Submit')"),
            root.locator("button:has-text('SUBMIT')"),
            root.locator("button:has-text('OK')"),
            root.locator("button:has-text('Confirm')"),
        ]
    )
    if not btn:
        raise RuntimeError(
            "Submit button not found" + (" in dialog" if within_dialog else "")
        )
    print("  Clicking SUBMIT" + (" (dialog)" if within_dialog else ""))
    btn.click()
    page.wait_for_timeout(1800)


def _select_option_in_dialog(page: Page, option_text: str, aliases: List[str]) -> None:
    """Open dropdown inside popup and choose fail reason (e.g. parcel damaged)."""
    dialog = _dialog_root(page)
    try:
        dialog.wait_for(state="visible", timeout=8000)
    except Exception as err:
        raise RuntimeError("Submit 后未出现弹窗（需要选择 fail reason）") from err

    print("  Dialog opened — selecting fail reason...")
    # Open any combobox/select inside the dialog
    dropdown = _first_visible(
        [
            dialog.locator(".ant-select-selector"),
            dialog.locator(".ant-select"),
            dialog.locator("[role='combobox']"),
            dialog.locator(".MuiSelect-select"),
            dialog.locator(".MuiOutlinedInput-root"),
            dialog.get_by_text(
                re.compile(r"fail reason|failure reason|reason|select", re.I)
            ),
        ]
    )
    if not dropdown:
        raise RuntimeError("弹窗内找不到下拉箭头/选择框")
    dropdown.click()
    page.wait_for_timeout(400)

    options: List[str] = []
    for opt in [option_text, *aliases]:
        if opt not in options:
            options.append(opt)

    for opt in options:
        option_locators = [
            page.get_by_role("option", name=re.compile(rf"^{_escape_regex(opt)}$", re.I)),
            page.locator(
                ".ant-select-item-option-content, .ant-select-item, [role='option'], li",
                has_text=re.compile(rf"^{_escape_regex(opt)}$", re.I),
            ),
            page.locator(
                ".ant-select-item-option-content, .ant-select-item, [role='option'], li",
                has_text=re.compile(_escape_regex(opt), re.I),
            ),
            dialog.get_by_text(re.compile(rf"^{_escape_regex(opt)}$", re.I)),
            page.get_by_text(re.compile(rf"^{_escape_regex(opt)}$", re.I)),
        ]
        option = _first_visible(option_locators)
        if option:
            print(f"  Selecting fail reason: {opt}")
            option.click()
            page.wait_for_timeout(400)
            return
        page.keyboard.type(opt, delay=20)
        page.wait_for_timeout(300)
        filtered = _first_visible(option_locators)
        if filtered:
            print(f"  Selecting fail reason (filtered): {opt}")
            filtered.click()
            page.wait_for_timeout(400)
            return
        for _ in range(len(opt)):
            page.keyboard.press("Backspace")

    raise RuntimeError(
        f'弹窗下拉中找不到 "{option_text}"（也试过: {", ".join(options[:4])}）'
    )


def _select_operation_location(page: Page) -> None:
    """Every transition step: Operation Location = NJ Warehouse before Submit."""
    _select_dropdown_option(
        page,
        re.compile(r"Operation Location", re.I),
        config.OPERATION_LOCATION,
        list(config.OPERATION_LOCATION_ALIASES),
    )


def _fill_pre_submit_extras(page: Page, step: TransitionStep) -> None:
    """Step-specific fields on the main Operation form before Submit."""
    for extra in step.extras:
        if extra == "warehouse":
            # SEND_PARCEL_TO_STORAGE also needs Network Node
            # (Operation Location already selected for every step)
            _select_dropdown_option(
                page,
                re.compile(r"Network Node", re.I),
                config.NETWORK_NODE,
                list(config.NETWORK_NODE_ALIASES),
            )


def apply_one_transition(page: Page, step: TransitionStep) -> None:
    expand_operation(page)
    aliases = TRANSITION_ALIASES.get(step.next_transition, [step.next_transition])
    # 1) Next Transition
    _select_dropdown_option(
        page,
        re.compile(r"Next Transition", re.I),
        aliases[0],
        aliases[1:] + [step.next_transition],
    )
    # 2) Operation Location = NJ Warehouse (every node)
    _select_operation_location(page)
    # 3) Any step-specific extras (e.g. Network Node on storage step)
    _fill_pre_submit_extras(page, step)
    # 4) Submit
    _click_submit(page, within_dialog=False)

    # deliver parcel apt: fail reason is chosen in a popup AFTER first Submit
    if "failReason" in step.extras:
        _select_option_in_dialog(
            page,
            config.FAIL_REASON,
            list(getattr(config, "FAIL_REASON_ALIASES", [])),
        )
        _click_submit(page, within_dialog=True)


def advance_order_to_target(
    page: Page,
    order_no: str,
    dry_run: bool = False,
    max_steps: int = 12,
) -> dict:
    steps_log: List[str] = []
    stop_run = False
    final_status: Optional[int] = None
    try:
        search_order(page, order_no)
        expand_operation(page)

        status = require_known_status(read_current_status(page), "开始时")
        final_status = status
        steps_log.append(f"start={status}")

        if status == config.TARGET_STATUS:
            return {
                "ok": True,
                "final_status": status,
                "steps": steps_log,
                "stop_run": False,
            }

        for _ in range(max_steps):
            if status == config.TARGET_STATUS:
                break

            step = find_step_for_status(status)
            if not step:
                raise PathMismatchError(
                    f"状态 {status} 虽在已知列表但没有对应下一步，已停止"
                )

            msg = f"{status} -> {step.next_transition} -> {step.to_status}"
            print(f"  {msg}")
            steps_log.append(msg)

            if dry_run:
                status = step.to_status
                final_status = status
                continue

            try:
                apply_one_transition(page, step)
            except Exception as err:
                raise PathMismatchError(
                    f'可选路径与讲解不一致或操作失败（在 "{step.next_transition}"）：{err}'
                ) from err

            # Tracking Info often updates a moment after Submit — poll for change
            nxt = None
            for attempt in range(8):
                page.wait_for_timeout(800 if attempt else 500)
                nxt = read_current_status(page)
                if nxt is not None and nxt != status:
                    break

            nxt = require_known_status(nxt, f'执行 "{step.next_transition}" 之后')
            if nxt == status:
                raise PathMismatchError(
                    f'执行 "{step.next_transition}" 后状态未变化（仍为 {status}），已停止'
                )
            if nxt != step.to_status:
                raise PathMismatchError(
                    f'路径不一致：执行 "{step.next_transition}" 后期望状态 '
                    f"{step.to_status}（{step.to_label}），实际为 {nxt}，已停止"
                )

            status = nxt
            final_status = status

        ok = status == config.TARGET_STATUS
        if not ok:
            raise PathMismatchError(
                f"未到达目标 215，停在 {status}，已停止"
            )
        return {
            "ok": True,
            "final_status": status,
            "steps": steps_log,
            "stop_run": False,
        }
    except PathMismatchError as err:
        print(f"  STOP: {err}")
        _save_debug(page, "path-mismatch")
        return {
            "ok": False,
            "final_status": final_status,
            "steps": steps_log,
            "error": str(err),
            "stop_run": True,
        }
    except Exception as err:
        print(f"  STOP: {err}")
        _save_debug(page, "error")
        return {
            "ok": False,
            "final_status": final_status,
            "steps": steps_log,
            "error": str(err),
            "stop_run": True,
        }
