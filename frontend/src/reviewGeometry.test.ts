import { describe, expect, it } from "vitest";

import { nextRotation, orderedBox, unrotatePoint } from "./reviewGeometry";

describe("review geometry", () => {
  it("orders a dragged rectangle and rejects slivers", () => {
    expect(orderedBox({ x: 300, y: 140 }, { x: 100, y: 100 })).toEqual({
      x1: 100,
      y1: 100,
      x2: 300,
      y2: 140,
    });
    expect(orderedBox({ x: 100, y: 100 }, { x: 102, y: 140 })).toBeNull();
  });

  it("cycles the rotation clockwise in quarter turns", () => {
    expect(nextRotation(0)).toBe(90);
    expect(nextRotation(90)).toBe(180);
    expect(nextRotation(180)).toBe(270);
    expect(nextRotation(270)).toBe(0);
  });

  it("maps a pointer on the turned page back to page space", () => {
    // u, v are fractions of the element as displayed; the result is page space.
    expect(unrotatePoint(0.25, 0.5, 0)).toEqual({ x: 0.25, y: 0.5 });
    // Turned 90 degrees clockwise, the displayed top-right corner is the page's top-left.
    expect(unrotatePoint(1, 0, 90)).toEqual({ x: 0, y: 0 });
    expect(unrotatePoint(0, 0, 90)).toEqual({ x: 0, y: 1 });
    expect(unrotatePoint(1, 1, 180)).toEqual({ x: 0, y: 0 });
    // Turned 270 degrees, the displayed bottom-left corner is the page's top-left.
    expect(unrotatePoint(0, 1, 270)).toEqual({ x: 0, y: 0 });
  });
});
