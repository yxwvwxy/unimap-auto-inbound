import fs from "node:fs";
import path from "node:path";
import { config } from "./config.js";
import { loadOrders } from "./sheets.js";
import {
  advanceOrderToTarget,
  launchBrowser,
  openUnimap,
  waitForManualLogin,
} from "./unimap.js";

type CliOptions = {
  loginOnly: boolean;
  dryRun: boolean;
  limit?: number;
  order?: string;
};

function parseArgs(argv: string[]): CliOptions {
  const opts: CliOptions = {
    loginOnly: argv.includes("--login-only"),
    dryRun: argv.includes("--dry-run"),
  };

  const limitIdx = argv.indexOf("--limit");
  if (limitIdx >= 0 && argv[limitIdx + 1]) {
    opts.limit = Number(argv[limitIdx + 1]);
  }

  const orderIdx = argv.indexOf("--order");
  if (orderIdx >= 0 && argv[orderIdx + 1]) {
    opts.order = argv[orderIdx + 1];
  }

  return opts;
}

function printHelp() {
  console.log(`
UniMap auto-inbound tool

Usage:
  npm start                 # login assist + process all orders from Google Sheet
  npm run login             # open browser and save Microsoft login session only
  npm run dry-run           # read sheet + print planned transitions (no submit)
  npm start -- --order XXX  # process one order
  npm start -- --limit 5    # process first 5 orders

Google Sheet setup (pick one):
  1) Service account: put JSON at credentials/unimap-put-in-storage-google-service-account.json
     and share the sheet with that client_email (Viewer).
  2) Local CSV: export the sheet and set LOCAL_CSV_FILE=/path/to/orders.csv
  3) Public link: share sheet as Anyone with the link (Viewer)

Env file: copy .env.example to .env
`);
}

async function main() {
  const argv = process.argv.slice(2);
  if (argv.includes("--help") || argv.includes("-h")) {
    printHelp();
    return;
  }

  const opts = parseArgs(argv);

  // Ensure dirs exist
  fs.mkdirSync(path.dirname(config.serviceAccountFile), { recursive: true });
  fs.mkdirSync(config.browserProfileDir, { recursive: true });

  if (!opts.loginOnly) {
    // Validate we can load orders before opening browser (unless single --order)
    if (!opts.order) {
      const preview = await loadOrders();
      console.log(`Loaded ${preview.length} order(s) from sheet/CSV.`);
      if (!preview.length) {
        console.error("No order numbers found. Check ORDER_COLUMN / sheet contents.");
        process.exitCode = 1;
        return;
      }
    }
  }

  const context = await launchBrowser();
  const page = await openUnimap(context);

  await waitForManualLogin(page);

  if (opts.loginOnly) {
    console.log("Login session saved in browser profile. You can run npm start next time.");
    await context.close();
    return;
  }

  const orders = opts.order
    ? [{ rowNumber: 0, orderNo: opts.order }]
    : await loadOrders();

  const selected = typeof opts.limit === "number" ? orders.slice(0, opts.limit) : orders;

  console.log(
    `\nProcessing ${selected.length} order(s)${opts.dryRun ? " [DRY RUN]" : ""}...\n`,
  );

  const results: Array<{
    orderNo: string;
    ok: boolean;
    finalStatus: number | null;
    error?: string;
  }> = [];

  for (const [idx, item] of selected.entries()) {
    console.log(`[${idx + 1}/${selected.length}] ${item.orderNo}`);
    const result = await advanceOrderToTarget(page, item.orderNo, {
      dryRun: opts.dryRun,
    });
    results.push({
      orderNo: item.orderNo,
      ok: result.ok,
      finalStatus: result.finalStatus,
      error: result.error,
    });

    if (result.ok) {
      console.log(`  OK -> ${result.finalStatus}`);
    } else {
      console.error(`  FAIL: ${result.error}`);
    }
  }

  const okCount = results.filter((r) => r.ok).length;
  const failCount = results.length - okCount;
  console.log(`\nDone. success=${okCount} failed=${failCount}`);

  const reportPath = path.resolve(`run-report-${Date.now()}.json`);
  fs.writeFileSync(reportPath, JSON.stringify(results, null, 2));
  console.log(`Report written: ${reportPath}`);

  // Keep browser open briefly so user can inspect last page; then close
  await context.close();
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
