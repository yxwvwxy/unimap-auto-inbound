import { chromium, type BrowserContext, type Locator, type Page } from "playwright";
import { config } from "./config.js";
import {
  TRANSITION_ALIASES,
  findStepForStatus,
  normalizeStatusText,
  requireKnownStatus,
  type TransitionStep,
} from "./transitions.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function firstVisible(locators: Locator[]): Promise<Locator | null> {
  for (const loc of locators) {
    const count = await loc.count();
    for (let i = 0; i < count; i++) {
      const item = loc.nth(i);
      if (await item.isVisible().catch(() => false)) return item;
    }
  }
  return null;
}

async function clickByText(page: Page, patterns: string | string[], timeout = 15000) {
  const list = Array.isArray(patterns) ? patterns : [patterns];
  const locators: Locator[] = [];

  for (const text of list) {
    locators.push(page.getByRole("tab", { name: new RegExp(text, "i") }));
    locators.push(page.getByRole("menuitem", { name: new RegExp(text, "i") }));
    locators.push(page.getByRole("button", { name: new RegExp(text, "i") }));
    locators.push(page.getByRole("link", { name: new RegExp(text, "i") }));
    locators.push(page.locator(`text=/${escapeRegex(text)}/i`));
  }

  const target = await firstVisible(locators);
  if (!target) {
    throw new Error(`Could not find clickable element matching: ${list.join(" | ")}`);
  }
  await target.click({ timeout });
}

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export async function launchBrowser(): Promise<BrowserContext> {
  return chromium.launchPersistentContext(config.browserProfileDir, {
    headless: false,
    viewport: { width: 1400, height: 900 },
    args: ["--disable-blink-features=AutomationControlled"],
  });
}

export async function openUnimap(context: BrowserContext): Promise<Page> {
  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(config.unimapUrl, { waitUntil: "domcontentloaded" });
  return page;
}

/**
 * Wait until the user finishes Microsoft login and reaches the main app.
 * Press Enter in the terminal when ready.
 */
export async function waitForManualLogin(page: Page): Promise<void> {
  console.log("\n=== Microsoft / UniUni login ===");
  console.log("1. Complete Microsoft login / MFA in the opened browser.");
  console.log("2. Wait until you can see the UniUni dispatch main page with the left menu.");
  console.log("3. Come back here and press Enter to continue.\n");

  await new Promise<void>((resolve) => {
    process.stdin.resume();
    process.stdin.once("data", () => resolve());
  });

  // Prefer staying on /main if redirected during login
  if (!page.url().includes("dispatch.uniuni.com")) {
    await page.goto(config.unimapUrl, { waitUntil: "domcontentloaded" });
  }
}

export async function goToEditOrder(page: Page): Promise<void> {
  // Left menu "Edit Order"
  await clickByText(page, ["Edit Order", "edit order"]);
  await page.waitForTimeout(800);
}

async function findOrderSearchInput(page: Page): Promise<Locator> {
  const candidates = [
    page.getByPlaceholder(/order|tracking|单号|search/i),
    page.locator('input[type="search"]'),
    page.locator('input[type="text"]').first(),
    page.locator(".ant-input").first(),
    page.locator('input:not([type="hidden"])').first(),
  ];

  const input = await firstVisible(candidates);
  if (!input) throw new Error("Could not find order search input on Edit Order page");
  return input;
}

async function clickSearch(page: Page): Promise<void> {
  const searchBtn = await firstVisible([
    page.getByRole("button", { name: /search|查询|搜/i }),
    page.locator("button:has-text('Search')"),
    page.locator(".ant-btn:has-text('Search')"),
  ]);

  if (searchBtn) {
    await searchBtn.click();
  } else {
    await page.keyboard.press("Enter");
  }
  await page.waitForTimeout(1500);
}

export async function searchOrder(page: Page, orderNo: string): Promise<void> {
  await goToEditOrder(page);
  const input = await findOrderSearchInput(page);
  await input.click({ clickCount: 3 });
  await input.fill(orderNo);
  await clickSearch(page);

  // Basic success check: order number appears somewhere on page
  const found = await page
    .locator(`text=/${escapeRegex(orderNo)}/i`)
    .first()
    .isVisible()
    .catch(() => false);

  if (!found) {
    // Still continue; some UIs only show fields without echoing the tracking # prominently
    console.warn(`  Warning: order ${orderNo} text not clearly visible after search; continuing...`);
  }
}

export async function expandOperation(page: Page): Promise<void> {
  const alreadyOpen = await page
    .locator("text=/next transition/i")
    .first()
    .isVisible()
    .catch(() => false);

  if (alreadyOpen) return;

  await clickByText(page, ["Operation", "operation"]);
  await page.waitForTimeout(800);

  const visible = await page
    .locator("text=/next transition/i")
    .first()
    .isVisible()
    .catch(() => false);

  if (!visible) {
    // Try collapsing/expanding panel headers common in ant design
    const panel = page.locator(".ant-collapse-header", { hasText: /operation/i }).first();
    if (await panel.isVisible().catch(() => false)) {
      await panel.click();
      await page.waitForTimeout(500);
    }
  }
}

export async function readCurrentStatus(page: Page): Promise<number | null> {
  // Prefer status near common labels
  const labeled = page.locator(
    "xpath=//*[contains(translate(., 'STATUS', 'status'), 'status')]/following::*[1]",
  );

  const blobs: string[] = [];
  const labeledCount = await labeled.count().catch(() => 0);
  for (let i = 0; i < Math.min(labeledCount, 8); i++) {
    blobs.push((await labeled.nth(i).innerText().catch(() => "")) || "");
  }

  // Also scan body text for known codes
  const body = await page.locator("body").innerText();
  blobs.unshift(body);

  const known = [215, 213, 212, 211, 200, 199, 195, 255, 190, 1910];
  for (const code of known) {
    const re = new RegExp(`\\b${code}\\b`);
    if (blobs.some((b) => re.test(b))) {
      // Prefer more specific nearby phrasing if multiple match — take the first known in priority order
      const { code: parsed } = normalizeStatusText(
        blobs.find((b) => re.test(b)) ?? "",
      );
      if (parsed != null) return code;
    }
  }

  // Fallback: first 3–4 digit status-looking number near "status"
  const statusLine = body.split("\n").find((line) => /status/i.test(line));
  if (statusLine) {
    return normalizeStatusText(statusLine).code;
  }

  return null;
}

async function selectDropdownOption(
  page: Page,
  fieldLabel: string | RegExp,
  optionText: string,
  aliases: string[] = [],
): Promise<void> {
  const labelRe = typeof fieldLabel === "string" ? new RegExp(fieldLabel, "i") : fieldLabel;

  // Click the form item / select near the label
  const label = page.locator(`text=${labelRe}`).first();
  if (await label.isVisible().catch(() => false)) {
    const container = label.locator(
      "xpath=ancestor::*[contains(@class,'ant-form-item') or contains(@class,'form-item') or self::div][1]",
    );
    const select = container.locator(".ant-select, select, [role='combobox']").first();
    if (await select.isVisible().catch(() => false)) {
      await select.click();
    } else {
      await label.click();
    }
  } else {
    // Fallback: open any visible select that might be next transition
    const select = page.locator(".ant-select").first();
    await select.click();
  }

  await page.waitForTimeout(400);

  const options = [optionText, ...aliases];
  for (const opt of options) {
    const option = page.locator(".ant-select-item-option-content, .ant-select-item, [role='option']", {
      hasText: new RegExp(escapeRegex(opt), "i"),
    }).first();

    if (await option.isVisible().catch(() => false)) {
      await option.click();
      return;
    }

    // Type-ahead filter
    await page.keyboard.type(opt.slice(0, 12), { delay: 40 });
    await page.waitForTimeout(300);
    const filtered = page.locator(".ant-select-item-option-content, [role='option']", {
      hasText: new RegExp(escapeRegex(opt), "i"),
    }).first();
    if (await filtered.isVisible().catch(() => false)) {
      await filtered.click();
      return;
    }
  }

  throw new Error(`Could not select dropdown option "${optionText}" for ${fieldLabel}`);
}

async function clickSubmit(page: Page): Promise<void> {
  const btn = await firstVisible([
    page.getByRole("button", { name: /^submit$/i }),
    page.locator("button:has-text('Submit')"),
    page.locator(".ant-btn-primary:has-text('Submit')"),
  ]);

  if (!btn) throw new Error("Submit button not found");
  await btn.click();
  await page.waitForTimeout(1800);
}

async function selectOperationLocation(page: Page): Promise<void> {
  await selectDropdownOption(
    page,
    /operation location/i,
    config.warehouse.operationWarehouse,
    ["NJ Warehouse", "NJ warehouse"],
  );
}

async function fillExtras(page: Page, step: TransitionStep): Promise<void> {
  if (!step.extras?.length) return;

  for (const extra of step.extras) {
    if (extra === "failReason") {
      await selectDropdownOption(page, /fail reason|failure reason|reason/i, config.failReason, [
        "parcel damaged",
        "Parcel Damaged",
      ]);
    }

    if (extra === "warehouse") {
      // Operation Location already selected for every step
      await selectDropdownOption(
        page,
        /network node|network note|network/i,
        config.warehouse.networkNote,
        ["WH- JFK-005", "WH-JFK-005", "WH JFK-005"],
      );
    }
  }
}

export async function applyOneTransition(page: Page, step: TransitionStep): Promise<void> {
  await expandOperation(page);

  const aliases = TRANSITION_ALIASES[step.nextTransition] ?? [step.nextTransition];
  // 1) Next Transition  2) Operation Location  3) extras  4) Submit
  await selectDropdownOption(page, /next transition/i, aliases[0], aliases.slice(1));
  await selectOperationLocation(page);
  await fillExtras(page, step);
  await clickSubmit(page);
}

export async function advanceOrderToTarget(
  page: Page,
  orderNo: string,
  options: { dryRun?: boolean; maxSteps?: number } = {},
): Promise<{
  ok: boolean;
  finalStatus: number | null;
  steps: string[];
  error?: string;
  stopRun?: boolean;
}> {
  const stepsLog: string[] = [];
  const maxSteps = options.maxSteps ?? 12;
  let finalStatus: number | null = null;

  try {
    await searchOrder(page, orderNo);
    await expandOperation(page);

    let status = requireKnownStatus(await readCurrentStatus(page), "开始时");
    finalStatus = status;
    stepsLog.push(`start=${status}`);

    if (status === config.targetStatus) {
      return { ok: true, finalStatus: status, steps: stepsLog, stopRun: false };
    }

    for (let i = 0; i < maxSteps; i++) {
      if (status === config.targetStatus) break;

      const step = findStepForStatus(status);
      if (!step) {
        throw new Error(`状态 ${status} 虽在已知列表但没有对应下一步，已停止`);
      }

      const msg = `${status} -> ${step.nextTransition} -> ${step.toStatus}`;
      console.log(`  ${msg}`);
      stepsLog.push(msg);

      if (options.dryRun) {
        status = step.toStatus;
        finalStatus = status;
        continue;
      }

      try {
        await applyOneTransition(page, step);
      } catch (err) {
        throw new Error(
          `可选路径与讲解不一致或操作失败（在 "${step.nextTransition}"）：${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
      await sleep(1000);

      let next = await readCurrentStatus(page);
      if (next === status) {
        await page.waitForTimeout(1500);
        next = await readCurrentStatus(page);
      }
      next = requireKnownStatus(next, `执行 "${step.nextTransition}" 之后`);
      if (next === status) {
        throw new Error(
          `执行 "${step.nextTransition}" 后状态未变化（仍为 ${status}），已停止`,
        );
      }
      if (next !== step.toStatus) {
        throw new Error(
          `路径不一致：执行 "${step.nextTransition}" 后期望状态 ${step.toStatus}（${step.toLabel}），实际为 ${next}，已停止`,
        );
      }
      status = next;
      finalStatus = status;
    }

    if (status !== config.targetStatus) {
      throw new Error(`未到达目标 215，停在 ${status}，已停止`);
    }
    return { ok: true, finalStatus: status, steps: stepsLog, stopRun: false };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.log(`  STOP: ${message}`);
    return {
      ok: false,
      finalStatus,
      steps: stepsLog,
      error: message,
      stopRun: true,
    };
  }
}
