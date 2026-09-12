import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  describeFailure,
  friendlyError,
  isPermanentFailure,
  messageForCode,
} from "./errorMessages";

describe("error messages", () => {
  it.each([
    ["too_many_pages", /300 pages/i],
    ["pdf_password_protected", /password/i],
    ["pdf_unreadable", /could not be opened/i],
    ["document_empty", /no pages/i],
    ["ocr_unavailable", /scanned|OCR/i],
    ["model_output_truncated", /not released/i],
    ["detection_unanchored", /not released/i],
    ["residual_identifier_detected", /not released/i],
    ["job_not_found", /1-hour|deleted/i],
    ["manifest_invalid", /on our side/i],
    ["invalid_request", /on our side/i],
    ["active_job_abuse_limit", /office network/i],
    ["hourly_abuse_limit", /100 documents/i],
    ["request_blocked", /blocked/i],
  ])("has a plain-language message for %s", (code, expected) => {
    expect(messageForCode(code)).toMatch(expected);
  });

  it("never shows a raw error code to the user", () => {
    const message = friendlyError(new ApiError("something_new", 500));
    expect(message).not.toMatch(/something_new/);
    expect(message).toMatch(/on our side/i);
  });

  it("keeps a generic sentence for network failures", () => {
    expect(friendlyError(new TypeError("Failed to fetch"))).toMatch(/connection|try again/i);
  });

  it("does not suggest retrying a document that will fail again", () => {
    expect(isPermanentFailure("too_many_pages")).toBe(true);
    expect(isPermanentFailure("service_busy")).toBe(false);
    expect(describeFailure(new ApiError("too_many_pages", 409))).not.toMatch(/retry/i);
    expect(describeFailure(new ApiError("service_busy", 503))).toMatch(/retry|resume/i);
    expect(describeFailure(new Error("offline"))).toMatch(/retry this file/i);
  });
});
