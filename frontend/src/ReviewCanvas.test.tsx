import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ReviewCanvas from "./ReviewCanvas";
import type { Detection, Manifest, Rotation } from "./types";

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
  rotation: 0,
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

function renderCanvas({
  detections = suggestions,
  rotation = 0 as Rotation,
  canUndo = false,
  pageCount = 3,
} = {}) {
  const onChange = vi.fn();
  const onUndo = vi.fn();
  const onRotate = vi.fn();
  const view = render(
    <ReviewCanvas
      credentials={{ jobId: "job", token: "t" }}
      manifest={{ ...manifest, page_count: pageCount, detections: suggestions }}
      detections={detections}
      onChange={onChange}
      onUndo={onUndo}
      canUndo={canUndo}
      rotation={rotation}
      onRotate={onRotate}
    />,
  );
  const rerender = (next: { rotation?: Rotation; detections?: Detection[]; canUndo?: boolean }) =>
    view.rerender(
      <ReviewCanvas
        credentials={{ jobId: "job", token: "t" }}
        manifest={{ ...manifest, page_count: pageCount, detections: suggestions }}
        detections={next.detections ?? detections}
        onChange={onChange}
        onUndo={onUndo}
        canUndo={next.canUndo ?? canUndo}
        rotation={next.rotation ?? rotation}
        onRotate={onRotate}
      />,
    );
  return { onChange, onUndo, onRotate, rerender, unmount: view.unmount };
}

function stubStageRect() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    width: 1000,
    height: 1000,
    right: 1000,
    bottom: 1000,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
}

function drag(stage: HTMLElement, from: [number, number], to: [number, number]) {
  fireEvent.pointerDown(stage, { button: 0, pointerId: 1, clientX: from[0], clientY: from[1] });
  fireEvent.pointerMove(stage, { pointerId: 1, clientX: to[0], clientY: to[1] });
  fireEvent.pointerUp(stage, { pointerId: 1, clientX: to[0], clientY: to[1] });
}

describe("review canvas", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    apiMocks.getPageBlob.mockResolvedValue(new Blob(["jpg"], { type: "image/jpeg" }));
    vi.stubGlobal("IntersectionObserver", undefined);
  });

  it("puts the controls in a toolbar above the pages and drops the rail", async () => {
    renderCanvas();
    expect(screen.getByRole("toolbar", { name: /review controls/i })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: /boxes on this page/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/new box type/i)).not.toBeInTheDocument();
    expect(await screen.findAllByRole("button", { name: /redaction$/i })).toHaveLength(3);
  });

  it("lists box counts per page and scrolls to the chosen page", async () => {
    renderCanvas();
    const select = screen.getByLabelText(/^page$/i);
    expect(select).toHaveTextContent(/page 1 · 2 boxes/i);
    expect(select).toHaveTextContent(/page 2 · no boxes/i);
    expect(select).toHaveTextContent(/page 3 · 1 box$/i);
    expect(screen.getByRole("button", { name: /previous page/i })).toBeDisabled();

    fireEvent.change(select, { target: { value: "1" } });

    const pages = await screen.findAllByTestId("review-page");
    expect(pages[1]).toHaveAttribute("aria-current", "page");
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /previous page/i })).toBeEnabled();
  });

  it("loads every page once when the browser has no intersection observer", async () => {
    renderCanvas();
    await waitFor(() => expect(apiMocks.getPageBlob).toHaveBeenCalledTimes(3));
    expect(apiMocks.getPageBlob.mock.calls.map((call) => call[1]).sort()).toEqual([0, 1, 2]);
    fireEvent.change(screen.getByLabelText(/zoom/i), { target: { value: "150" } });
    await waitFor(() => expect(screen.getAllByTestId("review-page")[0]).toHaveStyle({ width: "150%" }));
    expect(apiMocks.getPageBlob).toHaveBeenCalledTimes(3);
  });

  it("loads only visible pages and their neighbours when an observer exists", async () => {
    let callback: IntersectionObserverCallback | undefined;
    class FakeObserver {
      observed: Element[] = [];
      constructor(handler: IntersectionObserverCallback) {
        callback = handler;
      }
      observe(element: Element) {
        this.observed.push(element);
      }
      disconnect() {}
    }
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    renderCanvas({ pageCount: 5 });

    await waitFor(() => expect(apiMocks.getPageBlob).toHaveBeenCalledTimes(2));
    const pages = screen.getAllByTestId("review-page");
    callback!(
      [{ target: pages[3], isIntersecting: true, intersectionRatio: 0.8 } as unknown as IntersectionObserverEntry],
      {} as IntersectionObserver,
    );
    await waitFor(() => expect(apiMocks.getPageBlob).toHaveBeenCalledTimes(5));
    expect(screen.getByLabelText(/^page$/i)).toHaveValue("3");
    expect(pages[3]).toHaveAttribute("aria-current", "page");
  });

  it("previews the final look and turns the pages", async () => {
    const { onRotate, rerender } = renderCanvas();
    const stages = await screen.findAllByTestId("document-stage");
    fireEvent.click(screen.getByLabelText(/final look/i));
    stages.forEach((stage) => expect(stage).toHaveClass("is-final"));

    fireEvent.click(screen.getByRole("button", { name: /rotate/i }));
    expect(onRotate).toHaveBeenCalledWith(90);
    rerender({ rotation: 90 });
    screen.getAllByTestId("document-stage").forEach((stage) => {
      expect(stage).toHaveAttribute("data-rotation", "90");
    });
  });

  it("draws a plain user box in page space", async () => {
    stubStageRect();
    const { onChange } = renderCanvas();
    const [stage] = await screen.findAllByTestId("document-stage");
    drag(stage, [100, 100], [300, 140]);
    expect(onChange).toHaveBeenCalledTimes(1);
    const added = onChange.mock.calls[0][0].at(-1) as Detection;
    expect(added).toMatchObject({
      page_index: 0,
      category: "user_added",
      source: "user",
      confidence: 1,
      box: { x1: 100, y1: 100, x2: 300, y2: 140 },
    });
  });

  it("maps a box drawn on a turned page back to page space", async () => {
    stubStageRect();
    const { onChange } = renderCanvas({ rotation: 90 });
    const [stage] = await screen.findAllByTestId("document-stage");
    drag(stage, [100, 100], [300, 140]);
    const added = onChange.mock.calls[0][0].at(-1) as Detection;
    expect(added.box).toEqual({ x1: 100, y1: 700, x2: 140, y2: 900 });
  });

  it("removes the selected box from the keyboard and clears with Escape", async () => {
    const { onChange } = renderCanvas();
    const boxes = await screen.findAllByRole("button", { name: /redaction$/i });
    const remove = screen.getByRole("button", { name: /remove selected box/i });
    expect(remove).toBeDisabled();

    fireEvent.focus(boxes[0]);
    expect(remove).toBeEnabled();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(remove).toBeDisabled();

    fireEvent.focus(boxes[1]);
    fireEvent.keyDown(window, { key: "Delete" });
    expect(onChange).toHaveBeenCalledWith([suggestions[0], suggestions[2]]);
  });

  it("offers undo and restores the detector's suggestions", async () => {
    const { onChange, onUndo, rerender } = renderCanvas({ detections: [suggestions[0]] });
    const undo = await screen.findByRole("button", { name: /^undo/i });
    expect(undo).toBeDisabled();
    rerender({ canUndo: true });
    fireEvent.click(screen.getByRole("button", { name: /^undo/i }));
    expect(onUndo).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /restore suggestions/i }));
    expect(onChange).toHaveBeenCalledWith(suggestions);
    rerender({ detections: suggestions });
    expect(screen.getByRole("button", { name: /restore suggestions/i })).toBeDisabled();
  });

  it("releases every cached page image on unmount", async () => {
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    const { unmount } = renderCanvas();
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(3));
    unmount();
    expect(revoke).toHaveBeenCalledTimes(3);
  });
});
