/**
 * Status codes and next-transition labels for the inbound abandon path.
 *
 * 190 order received
 * 199 gateway transit
 * 200 parcel scanned
 * 212 wrong address from receive
 * 211 return office from transit
 * 213 storage 30 days from office
 * 215 parcel abandoned
 *
 * Alternate start:
 * 1910 warehouse inbound -> parcel scanned -> 200, then same path
 */

export type TransitionStep = {
  fromStatuses: number[];
  /** Text to match in the Next Transition dropdown */
  nextTransition: string;
  /** Extra fields that must be filled before Submit */
  extras?: Array<"failReason" | "warehouse">;
  toStatus: number;
  toLabel: string;
};

export const TARGET_STATUS = 215;

/** Ordered steps; tool picks the next step from the order's current status. */
export const TRANSITION_STEPS: TransitionStep[] = [
  {
    // 190 / 255 -> gateway processing -> 199
    fromStatuses: [190, 255],
    nextTransition: "gateway processing",
    toStatus: 199,
    toLabel: "gateway transit",
  },
  {
    // 199 / 195 / 1910 -> parcel scan -> 200
    fromStatuses: [199, 195, 1910],
    nextTransition: "parcel scan",
    // UI may show "parcel scanned" from warehouse inbound
    toStatus: 200,
    toLabel: "parcel scanned",
  },
  {
    fromStatuses: [200],
    nextTransition: "wrong address cfm in dispatch",
    toStatus: 212,
    toLabel: "wrong address from receive",
  },
  {
    // 212 / 202 IN_TRANSIT -> deliver parcel apt -> 211
    fromStatuses: [212, 202],
    nextTransition: "deliver parcel apt",
    extras: ["failReason"],
    toStatus: 211,
    toLabel: "return office from transit",
  },
  {
    fromStatuses: [211],
    nextTransition: "send parcel to storage",
    extras: ["warehouse"],
    toStatus: 213,
    toLabel: "storage 30 days from office",
  },
  {
    fromStatuses: [213],
    nextTransition: "parcel abandon",
    toStatus: 215,
    toLabel: "parcel abandoned",
  },
];

/** Alternate transition labels that may appear in the UI for the same step. */
export const TRANSITION_ALIASES: Record<string, string[]> = {
  "gateway processing": ["gateway processing", "gateway proccessing"],
  "parcel scan": ["parcel scan", "parcel scanned"],
  "wrong address cfm in dispatch": [
    "wrong address cfm in dispatch",
    "wrong address confirm in dispatch",
  ],
  "deliver parcel apt": ["deliver parcel apt", "deliver parcel apt("],
  "send parcel to storage": ["send parcel to storage"],
  "parcel abandon": ["parcel abandon", "parcel abandoned"],
};

export const KNOWN_STATUSES = new Set<number>([TARGET_STATUS]);
for (const step of TRANSITION_STEPS) {
  for (const s of step.fromStatuses) KNOWN_STATUSES.add(s);
  KNOWN_STATUSES.add(step.toStatus);
}

export function findStepForStatus(status: number): TransitionStep | null {
  if (status === TARGET_STATUS) return null;
  return TRANSITION_STEPS.find((step) => step.fromStatuses.includes(status)) ?? null;
}

export function requireKnownStatus(status: number | null, context = ""): number {
  const prefix = context ? `${context}: ` : "";
  if (status == null) {
    throw new Error(`${prefix}无法识别当前状态，已停止（仅处理已知节点）`);
  }
  if (!KNOWN_STATUSES.has(status)) {
    throw new Error(
      `${prefix}遇到未讲解过的状态 ${status}，已停止。已知状态: ${[...KNOWN_STATUSES].sort((a, b) => a - b).join(", ")}`,
    );
  }
  return status;
}

export function normalizeStatusText(text: string): { code: number | null; raw: string } {
  const raw = text.replace(/\s+/g, " ").trim();
  // Match patterns like "190: order received", "Status: 200", "1910 warehouse inbound"
  const match = raw.match(/\b(\d{3,4})\b/);
  return { code: match ? Number(match[1]) : null, raw };
}
