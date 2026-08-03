import path from "node:path";
import { config as loadEnv } from "dotenv";

loadEnv();

function env(name: string, fallback?: string): string {
  const value = process.env[name] ?? fallback;
  if (!value) {
    throw new Error(`Missing required env: ${name}`);
  }
  return value;
}

export const config = {
  sheetId: env("GOOGLE_SHEET_ID", "1yrR83W15kKevye87ksYnELUY68i_j4_kIIY_gu0bAWU"),
  sheetGid: env("GOOGLE_SHEET_GID", "0"),
  orderColumn: env("ORDER_COLUMN", "A").toUpperCase(),
  headerRows: Number(env("HEADER_ROWS", "1")),
  serviceAccountFile: path.resolve(
    env(
      "GOOGLE_SERVICE_ACCOUNT_FILE",
      "./credentials/unimap-put-in-storage-google-service-account.json",
    ),
  ),
  localCsvFile: process.env.LOCAL_CSV_FILE
    ? path.resolve(process.env.LOCAL_CSV_FILE)
    : undefined,
  unimapUrl: env("UNIMAP_URL", "https://dispatch.uniuni.com/main"),
  browserProfileDir: path.resolve(
    env("BROWSER_PROFILE_DIR", "./.browser-profile"),
  ),
  targetStatus: 215,
  warehouse: {
    operationWarehouse: "NJ warehouse",
    networkNote: "WH- JFK-005",
  },
  failReason: "parcel damaged",
};
