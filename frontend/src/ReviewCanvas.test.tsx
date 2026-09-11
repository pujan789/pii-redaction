import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ReviewCanvas from "./ReviewCanvas";
import type { Detection, Manifest } from "./types";

const apiMocks = vi.hoisted(() => ({ getPageBlob: vi.fn() }));
vi.mock("./api", async () => ({
  ...(await vi.importActual<typeof import("./api")>("./api")),
  ...apiMocks,
}));

const manifest: Manifest = {
  schema_version: 1,
  job_id: "job",
  page_count: 3,
  detections: [],
  detector_version: "t",
  prompt_version: "t",
  model_id: "t",
  created_at: "2099-01-01T00:00:00Z",
};

function box(id: string, category: Detection["category"], page = 0): Detection {
  return {
    id,
    page_index: page,
    category,
    box: { x1: 100, y1: 100, x2: 300, y2: 140 },
    confidence: 0.9,
    source: category === "ssn" ? "regex" : "model",
  };
}

const suggestions = [box("ssn-1", "ssn"), box("name-1", "person_name"), box("name-2", "person_name", 2)];

function renderCanvas(detections = suggestions) {
  const onChange = vi.fn();
  render(
    <ReviewCanvas
      credentials={{ jobId: "job", token: "t" }}
      manifest={{ ...manifest, detections: suggestions }}
      detections={detections}
      onChange={onChange}
    />,
  );
  return onChange;
}

describe("review canvas", () => {
  beforeEach(() => {
    apiMocks.getPageBlob.mockResolvedValue(new Blob(["jpg"], { type: "image/jpeg" }));
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:page"),
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  });

  it("selects a box from the keyboard and clears the selection with Escape", async () => {
    renderCanvas();
    const boxes = await screen.findAllByRole("button", { name: /redaction$/i });
    const remove = screen.getByRole("button", { name: /remove selected box/i });
    expect(remove).toBeDisabled();

    fireEvent.focus(boxes[0]);
    expect(remove).toBeEnabled();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(remove).toBeDisabled();
  });

  it("lists the boxes on this page with their category and source", async () => {
    renderCanvas();
    const list = await screen.findByRole("list", { name: /boxes on this page/i });
    expect(list).toHaveTextContent(/SSN/);
    expect(list).toHaveTextContent(/SSN pattern/);
    expect(list).toHaveTextContent(/Person name/);
    expect(list).toHaveTextContent(/Suggested/);
    fireEvent.click(screen.getByRole("button", { name: /select person name/i }));
    expect(screen.getByRole("button", { name: /remove selected box/i })).toBeEnabled();
  });

  it("shows box counts per page and jumps to a page", async () => {
    renderCanvas();
    const pages = await screen.findByLabelText(/^page$/i);
    expect(pages).toHaveTextContent(/page 1 · 2 boxes/i);
    expect(pages).toHaveTextContent(/page 2 · no boxes/i);
    fireEvent.change(pages, { target: { value: "2" } });
    await waitFor(() => expect(apiMocks.getPageBlob).toHaveBeenLastCalledWith(expect.anything(), 2));
  });

  it("zooms the page and previews the final look", async () => {
    renderCanvas();
    const stage = await screen.findByTestId("document-stage");
    fireEvent.change(screen.getByLabelText(/zoom/i), { target: { value: "200" } });
    expect(stage).toHaveStyle({ width: "1800px" });
    expect(stage).not.toHaveClass("is-final");
    fireEvent.click(screen.getByLabelText(/final look/i));
    expect(stage).toHaveClass("is-final");
  });

  it("restores the detector's suggestions after boxes were removed", async () => {
    const onChange = renderCanvas([suggestions[0]]);
    const restore = await screen.findByRole("button", { name: /restore suggestions/i });
    fireEvent.click(restore);
    expect(onChange).toHaveBeenCalledWith(suggestions);
  });
});
