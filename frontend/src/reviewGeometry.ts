import type { BoundingBox, Rotation } from "./types";

export interface Point {
  x: number;
  y: number;
}

/** A dragged rectangle in page thousandths, or null when it is too small to mean anything. */
export function orderedBox(first: Point, second: Point): BoundingBox | null {
  const box = {
    x1: Math.round(Math.min(first.x, second.x)),
    y1: Math.round(Math.min(first.y, second.y)),
    x2: Math.round(Math.max(first.x, second.x)),
    y2: Math.round(Math.max(first.y, second.y)),
  };
  return box.x2 - box.x1 >= 3 && box.y2 - box.y1 >= 3 ? box : null;
}

export function nextRotation(rotation: Rotation): Rotation {
  return ((rotation + 90) % 360) as Rotation;
}

/**
 * Map a pointer position inside the page as displayed (u, v as fractions of the
 * turned element) back to the unrotated page. The server turns pages clockwise
 * by the same rotation, so this is the exact inverse of that transform.
 */
export function unrotatePoint(u: number, v: number, rotation: Rotation): Point {
  switch (rotation) {
    case 90:
      return { x: v, y: 1 - u };
    case 180:
      return { x: 1 - u, y: 1 - v };
    case 270:
      return { x: 1 - v, y: u };
    default:
      return { x: u, y: v };
  }
}
