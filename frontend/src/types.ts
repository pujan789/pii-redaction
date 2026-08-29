export type JobStatus =
  | "awaiting_upload"
  | "queued_detection"
  | "detecting"
  | "review_required"
  | "queued_redaction"
  | "redacting"
  | "complete"
  | "failed"
  | "deleted"
  | "expired";

export type PiiCategory =
  | "person_name"
  | "organization_name"
  | "street_address"
  | "email"
  | "phone"
  | "ssn"
  | "itin"
  | "ein"
  | "ptin"
  | "efin"
  | "ip_pin"
  | "caf_number"
  | "bank_account"
  | "routing_number"
  | "payment_card"
  | "state_tax_id"
  | "employee_id"
  | "health_insurance_id"
  | "other_private_id"
  | "date_of_birth"
  | "date_of_death"
  | "driver_license"
  | "passport"
  | "ip_address"
  | "signature"
  | "user_added";

export interface BoundingBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface Detection {
  id: string;
  page_index: number;
  category: PiiCategory;
  box: BoundingBox;
  confidence: number;
  source: "model" | "regex" | "ocr" | "user";
  fingerprint?: string | null;
}

export interface Manifest {
  schema_version: 1;
  job_id: string;
  page_count: number;
  detections: Detection[];
  detector_version: string;
  prompt_version: string;
  model_id: string;
  created_at: string;
}

export interface Job {
  job_id: string;
  status: JobStatus;
  expires_at: string;
  page_count: number | null;
  pages_completed: number;
  finding_count: number;
  error_code: string | null;
}

export interface UploadPlan {
  method: "PUT" | "POST";
  url: string;
  fields: Record<string, string>;
  headers: Record<string, string>;
  expires_in_seconds: number;
}

export interface CreatedJob {
  job_id: string;
  access_token: string;
  expires_at: string;
  upload: UploadPlan;
}

export interface JobCredentials {
  jobId: string;
  token: string;
}
