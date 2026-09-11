import { describe, expect, it } from "vitest";

import { pageBoxCounts, summarizeDetections } from "./reviewSummary";
import type { Detection } from "./types";

function detection(
  id: string,
  category: Detection["category"],
  page = 0,
  source: Detection["source"] = "model",
): Detection {
  return {
    id,
    page_index: page,
    category,
    box: { x1: 10, y1: 10, x2: 20, y2: 20 },
    confidence: 0.9,
    source,
  };
}

describe("review summary", () => {
  it("tallies categories in plain language, largest first", () => {
    const summary = summarizeDetections([
      detection("a", "ssn"),
      detection("b", "ssn"),
      detection("c", "ssn", 1, "regex"),
      detection("d", "person_name"),
      detection("e", "person_name"),
      detection("f", "street_address"),
      detection("g", "user_added", 0, "user"),
    ]);
    expect(summary).toBe("3 SSNs, 2 person names, 1 street-address line, 1 added by you");
  });

  it("describes an empty document", () => {
    expect(summarizeDetections([])).toBe("No redactions yet");
  });

  it("counts boxes per page so empty pages are visible", () => {
    expect(pageBoxCounts([detection("a", "ssn", 0), detection("b", "ssn", 2)], 3)).toEqual([
      1, 0, 1,
    ]);
  });
});
