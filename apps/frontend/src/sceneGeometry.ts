export type Point = { x: number; y: number };
export type MediaBox = { left: number; top: number; width: number; height: number };
export type ViewportBox = { width: number; height: number };

export const MIN_NORMALIZED_LINE_LENGTH = 0.01;

export function clampNormalized(value: number) {
  return Math.max(0, Math.min(1, value));
}

export function mediaBoxForViewport(viewport: ViewportBox, media: ViewportBox): MediaBox {
  if (viewport.width <= 0 || viewport.height <= 0 || media.width <= 0 || media.height <= 0) {
    return { left: 0, top: 0, width: 0, height: 0 };
  }
  const viewportRatio = viewport.width / viewport.height;
  const mediaRatio = media.width / media.height;
  if (viewportRatio > mediaRatio) {
    const width = viewport.height * mediaRatio;
    return { left: (viewport.width - width) / 2, top: 0, width, height: viewport.height };
  }
  const height = viewport.width / mediaRatio;
  return { left: 0, top: (viewport.height - height) / 2, width: viewport.width, height };
}

export function viewportPointToNormalized(client: Point, bounds: DOMRect | MediaBox, media?: ViewportBox): Point {
  const mediaBox = media ? mediaBoxForViewport({ width: bounds.width, height: bounds.height }, media) : { left: 0, top: 0, width: bounds.width, height: bounds.height };
  const localX = client.x - bounds.left - mediaBox.left;
  const localY = client.y - bounds.top - mediaBox.top;
  return {
    x: clampNormalized(mediaBox.width > 0 ? localX / mediaBox.width : 0),
    y: clampNormalized(mediaBox.height > 0 ? localY / mediaBox.height : 0)
  };
}

export function normalizedToViewportPoint(point: Point, bounds: MediaBox): Point {
  return {
    x: bounds.left + point.x * bounds.width,
    y: bounds.top + point.y * bounds.height
  };
}

export function lineLength(start: Point, end: Point) {
  return Math.hypot(end.x - start.x, end.y - start.y);
}

export function enforceMinimumLineLength(fixed: Point, candidate: Point, minimum = MIN_NORMALIZED_LINE_LENGTH): Point {
  const dx = candidate.x - fixed.x;
  const dy = candidate.y - fixed.y;
  const length = Math.hypot(dx, dy);
  if (length >= minimum) return candidate;
  if (length === 0) {
    return { x: clampNormalized(fixed.x + minimum), y: fixed.y };
  }
  return {
    x: clampNormalized(fixed.x + (dx / length) * minimum),
    y: clampNormalized(fixed.y + (dy / length) * minimum)
  };
}
