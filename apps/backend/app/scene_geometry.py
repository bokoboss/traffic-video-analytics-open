from __future__ import annotations

import hashlib
import json
import math
from typing import Any


SCENE_SCHEMA_VERSION = "scene-geometry-v2"
MIN_LINE_LENGTH = 0.01
MIN_POLYGON_AREA = 0.0001
DIRECTION_MODES = {"A_TO_B", "B_TO_A", "BIDIRECTIONAL"}


class SceneValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_scene_geometry(geometry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if geometry.get("schema_version") != SCENE_SCHEMA_VERSION:
        errors.append("schema_version must be scene-geometry-v2")
    _validate_unique_ids("counting_lines", geometry.get("counting_lines"), errors)
    _validate_unique_ids("rois", geometry.get("rois"), errors)
    rois = geometry.get("rois") or []
    if isinstance(rois, list) and len(rois) > 1:
        errors.append("rois supports one pilot ROI in this milestone")
    seen_line_geometry: set[tuple[float, float, float, float]] = set()
    for index, line in enumerate(geometry.get("counting_lines") or []):
        prefix = f"counting_lines[{index}]"
        if not str(line.get("name", "")).strip():
            errors.append(f"{prefix}.name is required")
        side_a_name = str(line.get("side_a_name") or line.get("direction_a_label") or "").strip()
        side_b_name = str(line.get("side_b_name") or line.get("direction_b_label") or "").strip()
        if side_a_name and side_b_name and side_a_name == side_b_name:
            errors.append(f"{prefix}.side_a_name and side_b_name must be distinguishable when provided")
        start = line.get("start")
        end = line.get("end")
        if not _valid_point(start):
            errors.append(f"{prefix}.start must be a finite normalized point")
            continue
        if not _valid_point(end):
            errors.append(f"{prefix}.end must be a finite normalized point")
            continue
        length = math.dist((start["x"], start["y"]), (end["x"], end["y"]))
        if length < MIN_LINE_LENGTH:
            errors.append(f"{prefix} endpoints must be separated by at least {MIN_LINE_LENGTH}")
        geometry_key = (
            round(float(start["x"]), 6),
            round(float(start["y"]), 6),
            round(float(end["x"]), 6),
            round(float(end["y"]), 6),
        )
        reverse_key = (geometry_key[2], geometry_key[3], geometry_key[0], geometry_key[1])
        if geometry_key in seen_line_geometry or reverse_key in seen_line_geometry:
            errors.append(f"{prefix} duplicates another counting line geometry")
        seen_line_geometry.add(geometry_key)
        if line.get("direction_mode") not in DIRECTION_MODES:
            errors.append(f"{prefix}.direction_mode must be A_TO_B, B_TO_A, or BIDIRECTIONAL")
    for index, roi in enumerate(rois):
        prefix = f"rois[{index}]"
        vertices = roi.get("vertices")
        if not isinstance(vertices, list) or len(vertices) < 3:
            errors.append(f"{prefix}.vertices must include at least three points")
            continue
        if not all(_valid_point(point) for point in vertices):
            errors.append(f"{prefix}.vertices must be finite normalized points")
            continue
        if _has_consecutive_duplicate(vertices):
            errors.append(f"{prefix}.vertices must not contain consecutive duplicate points")
        area = abs(_polygon_area(vertices))
        if area < MIN_POLYGON_AREA:
            errors.append(f"{prefix} area must be at least {MIN_POLYGON_AREA}")
        if _self_intersects(vertices):
            errors.append(f"{prefix} must not self-intersect")
    return errors


def scene_semantic_hash(geometry: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_semantic_payload(geometry), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _semantic_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _semantic_payload(item) for key, item in value.items() if key != "analyst_note"}
    if isinstance(value, list):
        return [_semantic_payload(item) for item in value]
    return value


def _validate_unique_ids(label: str, items: object, errors: list[str]) -> None:
    if not isinstance(items, list):
        errors.append(f"{label} must be a list")
        return
    seen: set[str] = set()
    for index, item in enumerate(items):
        item_id = str(item.get("id", "") if isinstance(item, dict) else "")
        if not item_id:
            errors.append(f"{label}[{index}].id is required")
        elif item_id in seen:
            errors.append(f"{label}[{index}].id must be unique")
        seen.add(item_id)


def _valid_point(point: object) -> bool:
    if not isinstance(point, dict):
        return False
    x = point.get("x")
    y = point.get("y")
    return (
        isinstance(x, int | float)
        and isinstance(y, int | float)
        and math.isfinite(x)
        and math.isfinite(y)
        and 0 <= x <= 1
        and 0 <= y <= 1
    )


def _has_consecutive_duplicate(points: list[dict[str, float]]) -> bool:
    return any(points[index] == points[index - 1] for index in range(1, len(points)))


def _polygon_area(points: list[dict[str, float]]) -> float:
    total = 0.0
    for index, point in enumerate(points):
        other = points[(index + 1) % len(points)]
        total += point["x"] * other["y"] - other["x"] * point["y"]
    return total / 2


def _self_intersects(points: list[dict[str, float]]) -> bool:
    segments = [(points[index], points[(index + 1) % len(points)]) for index in range(len(points))]
    for left_index, left in enumerate(segments):
        for right_index, right in enumerate(segments):
            if abs(left_index - right_index) <= 1 or {left_index, right_index} == {0, len(segments) - 1}:
                continue
            if _segments_intersect(left[0], left[1], right[0], right[1]):
                return True
    return False


def _segments_intersect(a: dict[str, float], b: dict[str, float], c: dict[str, float], d: dict[str, float]) -> bool:
    def orient(p: dict[str, float], q: dict[str, float], r: dict[str, float]) -> float:
        return (q["x"] - p["x"]) * (r["y"] - p["y"]) - (q["y"] - p["y"]) * (r["x"] - p["x"])

    return orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0
