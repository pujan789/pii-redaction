import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import App from "./App";

describe("PII redaction desk", () => {
  beforeEach(() => sessionStorage.clear());

  it("explains the auditable workflow and deletion window", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: /redact a tax document/i })).toBeVisible();
    expect(screen.getByText(/deleted after download or within 1 hour/i)).toBeVisible();
    expect(screen.getByText(/free to use · no account/i)).toBeVisible();
  });

  it("offers a keyboard-accessible private file picker", () => {
    render(<App />);
    expect(screen.getByRole("button", { name: /drop a tax document here/i })).toBeEnabled();
  });
});
