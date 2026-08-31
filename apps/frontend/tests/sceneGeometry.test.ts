import { describe, expect, it } from "vitest";
import {
  enforceMinimumLineLength,
  lineLength,
  mediaBoxForViewport,
  normalizedToViewportPoint,
  viewportPointToNormalized
} from "../src/sceneGeometry";

describe("scene coordinate conversion", () => {
  it("maps letterboxed landscape media without mutating normalized coordinates", () => {
    const mediaBox = mediaBoxForViewport({ width: 1000, height: 500 }, { width: 400, height: 400 });

    expect(mediaBox).toEqual({ left: 250, top: 0, width: 500, height: 500 });
    expect(viewportPointToNormalized({ x: 500, y: 250 }, { left: 0, top: 0, width: 1000, height: 500 }, { width: 400, height: 400 })).toEqual({ x: 0.5, y: 0.5 });
    expect(normalizedToViewportPoint({ x: 0.5, y: 0.5 }, mediaBox)).toEqual({ x: 500, y: 250 });
  });

  it("maps portrait media and clamps pointers outside the media area", () => {
    const normalized = viewportPointToNormalized(
      { x: -40, y: 700 },
      { left: 0, top: 0, width: 600, height: 800 },
      { width: 300, height: 600 }
    );

    expect(normalized).toEqual({ x: 0, y: 0.875 });
  });

  it("preserves endpoint identity while preventing zero-length lines", () => {
    const fixed = { x: 0.5, y: 0.5 };
    const adjusted = enforceMinimumLineLength(fixed, fixed);

    expect(lineLength(fixed, adjusted)).toBeGreaterThanOrEqual(0.01);
    expect(adjusted.x).toBeGreaterThan(fixed.x);
    expect(adjusted.y).toBe(fixed.y);
  });
});
