import { useCallback, useState } from "react";

import type { Detection } from "./types";

const UNDO_LIMIT = 50;

/** Reviewer edits with a bounded undo stack; shared by the page and the batch modal. */
export function useDetectionHistory(initial: Detection[] = []) {
  const [detections, setDetections] = useState<Detection[]>(initial);
  const [history, setHistory] = useState<Detection[][]>([]);

  const update = useCallback((next: Detection[]) => {
    setDetections((current) => {
      setHistory((stack) => [...stack.slice(-(UNDO_LIMIT - 1)), current]);
      return next;
    });
  }, []);

  const undo = useCallback(() => {
    setHistory((stack) => {
      const previous = stack.at(-1);
      if (!previous) return stack;
      setDetections(previous);
      return stack.slice(0, -1);
    });
  }, []);

  const reset = useCallback((next: Detection[]) => {
    setDetections(next);
    setHistory([]);
  }, []);

  return { detections, canUndo: history.length > 0, update, undo, reset };
}
