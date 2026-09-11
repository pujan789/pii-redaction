import type { Detection, PiiCategory } from "./types";

export const CATEGORY_LABELS: Record<PiiCategory, string> = {
  person_name: "Person name",
  organization_name: "Private organization",
  street_address: "Street-address line",
  email: "Email",
  phone: "Phone",
  ssn: "SSN",
  itin: "ITIN",
  ein: "EIN",
  ptin: "PTIN",
  efin: "EFIN",
  ip_pin: "IP PIN",
  caf_number: "CAF number",
  bank_account: "Bank account (manual)",
  routing_number: "Routing number",
  payment_card: "Payment card",
  state_tax_id: "State tax ID",
  employee_id: "Employee ID",
  health_insurance_id: "Health insurance ID",
  other_private_id: "Other private ID",
  date_of_birth: "Date of birth",
  date_of_death: "Date of death",
  driver_license: "Driver license",
  passport: "Passport",
  ip_address: "IP address",
  signature: "Signature",
  user_added: "Other sensitive data",
};

export const SOURCE_LABELS: Record<Detection["source"], string> = {
  model: "Suggested",
  ocr: "Suggested",
  regex: "SSN pattern",
  user: "Added by you",
};

const SUMMARY_NAMES: Record<PiiCategory, [string, string]> = {
  person_name: ["person name", "person names"],
  organization_name: ["private organization", "private organizations"],
  street_address: ["street-address line", "street-address lines"],
  email: ["email", "emails"],
  phone: ["phone number", "phone numbers"],
  ssn: ["SSN", "SSNs"],
  itin: ["ITIN", "ITINs"],
  ein: ["EIN", "EINs"],
  ptin: ["PTIN", "PTINs"],
  efin: ["EFIN", "EFINs"],
  ip_pin: ["IP PIN", "IP PINs"],
  caf_number: ["CAF number", "CAF numbers"],
  bank_account: ["bank account", "bank accounts"],
  routing_number: ["routing number", "routing numbers"],
  payment_card: ["payment card", "payment cards"],
  state_tax_id: ["state tax ID", "state tax IDs"],
  employee_id: ["employee ID", "employee IDs"],
  health_insurance_id: ["health insurance ID", "health insurance IDs"],
  other_private_id: ["other private ID", "other private IDs"],
  date_of_birth: ["date of birth", "dates of birth"],
  date_of_death: ["date of death", "dates of death"],
  driver_license: ["driver license", "driver licenses"],
  passport: ["passport", "passports"],
  ip_address: ["IP address", "IP addresses"],
  signature: ["signature", "signatures"],
  user_added: ["added by you", "added by you"],
};

/** "3 SSNs, 2 person names, 1 street-address line" for the approval bar. */
export function summarizeDetections(detections: Detection[]): string {
  const counts = new Map<PiiCategory, number>();
  for (const detection of detections) {
    counts.set(detection.category, (counts.get(detection.category) ?? 0) + 1);
  }
  if (!counts.size) return "No redactions yet";
  return [...counts.entries()]
    .sort((left, right) => right[1] - left[1])
    .map(([category, count]) => `${count} ${SUMMARY_NAMES[category][count === 1 ? 0 : 1]}`)
    .join(", ");
}

export function pageBoxCounts(detections: Detection[], pageCount: number): number[] {
  const counts = new Array<number>(pageCount).fill(0);
  for (const detection of detections) {
    if (detection.page_index < pageCount) counts[detection.page_index] += 1;
  }
  return counts;
}

export function sameDetections(left: Detection[], right: Detection[]): boolean {
  if (left.length !== right.length) return false;
  const ids = new Set(left.map((item) => item.id));
  return right.every((item) => ids.has(item.id));
}
