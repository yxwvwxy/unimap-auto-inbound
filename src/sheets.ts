import fs from "node:fs";
import { google } from "googleapis";
import { config } from "./config.js";

export type SheetOrder = {
  rowNumber: number;
  orderNo: string;
};

function columnLetterToIndex(letter: string): number {
  let index = 0;
  for (const ch of letter.toUpperCase()) {
    index = index * 26 + (ch.charCodeAt(0) - 64);
  }
  return index - 1;
}

function parseCsv(content: string): string[][] {
  const rows: string[][] = [];
  let current: string[] = [];
  let cell = "";
  let inQuotes = false;

  for (let i = 0; i < content.length; i++) {
    const ch = content[i];
    const next = content[i + 1];

    if (inQuotes) {
      if (ch === '"' && next === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') {
        inQuotes = false;
      } else {
        cell += ch;
      }
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      current.push(cell);
      cell = "";
    } else if (ch === "\n") {
      current.push(cell);
      rows.push(current);
      current = [];
      cell = "";
    } else if (ch === "\r") {
      // skip
    } else {
      cell += ch;
    }
  }

  if (cell.length || current.length) {
    current.push(cell);
    rows.push(current);
  }

  return rows;
}

function extractOrders(rows: string[][]): SheetOrder[] {
  const col = columnLetterToIndex(config.orderColumn);
  const orders: SheetOrder[] = [];

  rows.forEach((row, idx) => {
    const rowNumber = idx + 1;
    if (rowNumber <= config.headerRows) return;
    const value = (row[col] ?? "").trim();
    if (!value) return;
    // Skip obvious non-order header leftovers
    if (/^(order|tracking|单号|order\s*no)/i.test(value)) return;
    orders.push({ rowNumber, orderNo: value });
  });

  return orders;
}

async function readViaServiceAccount(): Promise<SheetOrder[]> {
  if (!fs.existsSync(config.serviceAccountFile)) {
    throw new Error(
      `Google service account file not found: ${config.serviceAccountFile}\n` +
        "Either place credentials there, or set LOCAL_CSV_FILE, or share the sheet as Anyone with the link (Viewer).",
    );
  }

  const auth = new google.auth.GoogleAuth({
    keyFile: config.serviceAccountFile,
    scopes: ["https://www.googleapis.com/auth/spreadsheets.readonly"],
  });

  const sheets = google.sheets({ version: "v4", auth });

  // Resolve gid -> sheet title
  const meta = await sheets.spreadsheets.get({ spreadsheetId: config.sheetId });
  const sheet =
    meta.data.sheets?.find((s) => String(s.properties?.sheetId) === config.sheetGid) ??
    meta.data.sheets?.[0];

  const title = sheet?.properties?.title;
  if (!title) {
    throw new Error("Could not resolve Google Sheet tab title");
  }

  const range = `'${title.replace(/'/g, "''")}'!${config.orderColumn}:${config.orderColumn}`;
  const res = await sheets.spreadsheets.values.get({
    spreadsheetId: config.sheetId,
    range,
  });

  const values = res.data.values ?? [];
  const rows = values.map((row) => [String(row[0] ?? "")]);
  return extractOrders(rows);
}

async function readViaPublicCsv(): Promise<SheetOrder[]> {
  const url =
    `https://docs.google.com/spreadsheets/d/${config.sheetId}/export?format=csv&gid=${config.sheetGid}`;
  const res = await fetch(url);
  const text = await res.text();

  if (!res.ok || text.includes("Sign in to your Google Account") || text.includes("<!DOCTYPE html>")) {
    throw new Error(
      "Public CSV export is not available (sheet is private). " +
        "Share the sheet with your service account, or export a local CSV and set LOCAL_CSV_FILE.",
    );
  }

  return extractOrders(parseCsv(text));
}

function readLocalCsv(filePath: string): SheetOrder[] {
  const content = fs.readFileSync(filePath, "utf8");
  return extractOrders(parseCsv(content));
}

export async function loadOrders(): Promise<SheetOrder[]> {
  if (config.localCsvFile) {
    console.log(`Reading orders from local CSV: ${config.localCsvFile}`);
    return readLocalCsv(config.localCsvFile);
  }

  if (fs.existsSync(config.serviceAccountFile)) {
    console.log("Reading orders via Google service account...");
    return readViaServiceAccount();
  }

  console.log("Trying public Google Sheet CSV export...");
  return readViaPublicCsv();
}
