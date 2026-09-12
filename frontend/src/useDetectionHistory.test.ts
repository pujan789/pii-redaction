import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Detection } from "./types";
import { useDetectionHistory } from "./useDetectionHistory";

function box(id: string): Detection {
  return {
    id,
    page_index: 0,
    category: "user_added",
    box: { x1: 10, y1: 10, x2: 20, y2: 20 },
    confidence: 1,
    source: "user",
  };
}

describe("detection history", () => {
  it("records each update so it can be undone in order", () => {
    const { result } = renderHook(() => useDetectionHistory([box("a")]));
    expect(result.current.canUndo).toBe(false);

    act(() => result.current.update([box("a"), box("b")]));
    act(() => result.current.update([box("b")]));
    expect(result.current.detections.map((item) => item.id)).toEqual(["b"]);
    expect(result.current.canUndo).toBe(true);

    act(() => result.current.undo());
    expect(result.current.detections.map((item) => item.id)).toEqual(["a", "b"]);
    act(() => result.current.undo());
    expect(result.current.detections.map((item) => item.id)).toEqual(["a"]);
    expect(result.current.canUndo).toBe(false);
  });

  it("keeps at most fifty steps", () => {
    const { result } = renderHook(() => useDetectionHistory([]));
    for (let index = 0; index < 60; index++) {
      act(() => result.current.update([box(`step-${index}`)]));
    }
    let undos = 0;
    while (result.current.canUndo) {
      act(() => result.current.undo());
      undos += 1;
    }
    expect(undos).toBe(50);
  });

  it("reset replaces the boxes and forgets the history", () => {
    const { result } = renderHook(() => useDetectionHistory([box("a")]));
    act(() => result.current.update([]));
    act(() => result.current.reset([box("z")]));
    expect(result.current.detections.map((item) => item.id)).toEqual(["z"]);
    expect(result.current.canUndo).toBe(false);
  });
});
