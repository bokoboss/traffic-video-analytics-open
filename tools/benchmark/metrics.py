from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MatchResult:
    predicted_event_id: str
    truth_event_id: str
    timestamp_error_ms: int
    class_match: bool


def match_crossing_events(predicted: list[dict[str, Any]], truth: list[dict[str, Any]]) -> dict[str, Any]:
    unmatched_truth = set(range(len(truth)))
    matches: list[MatchResult] = []
    false_positive_ids: list[str] = []
    direction_mismatches = 0
    line_mismatches = 0
    for event in predicted:
        best_index: int | None = None
        best_error: int | None = None
        for index in sorted(unmatched_truth):
            target = truth[index]
            timestamp_error = abs(int(event["crossing_timestamp_ms"]) - int(target["crossing_timestamp_ms"]))
            tolerance = int(target.get("timestamp_tolerance_ms", 0))
            if timestamp_error > tolerance:
                continue
            if event.get("line_id") != target.get("line_id"):
                continue
            if event.get("direction") != target.get("direction"):
                continue
            if best_error is None or timestamp_error < best_error:
                best_error = timestamp_error
                best_index = index
        if best_index is None:
            false_positive_ids.append(str(event.get("event_id", "")))
            if any(event.get("line_id") != target.get("line_id") for target in truth):
                line_mismatches += 1
            if any(event.get("direction") != target.get("direction") for target in truth):
                direction_mismatches += 1
            continue
        target = truth[best_index]
        unmatched_truth.remove(best_index)
        matches.append(
            MatchResult(
                predicted_event_id=str(event.get("event_id", "")),
                truth_event_id=str(target.get("event_id", "")),
                timestamp_error_ms=best_error or 0,
                class_match=str(event.get("class", "")) == str(target.get("observable_class", "")),
            )
        )
    true_positive = len(matches)
    false_positive = len(false_positive_ids)
    false_negative = len(unmatched_truth)
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "sample_sizes": {"predicted": len(predicted), "ground_truth": len(truth), "matched": true_positive},
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "timestamp_error_ms": {
            "mean_absolute": _mean([match.timestamp_error_ms for match in matches]),
            "max_absolute": max((match.timestamp_error_ms for match in matches), default=None),
        },
        "class_errors": sum(1 for match in matches if not match.class_match),
        "wrong_direction_candidates": direction_mismatches,
        "wrong_line_candidates": line_mismatches,
        "false_positive_event_ids": false_positive_ids,
        "missed_truth_event_ids": [str(truth[index].get("event_id", "")) for index in sorted(unmatched_truth)],
    }


def aggregate_count_errors(predicted: list[dict[str, Any]], truth: list[dict[str, Any]]) -> dict[str, Any]:
    dimensions = {
        "by_line": "line_id",
        "by_direction": "direction",
        "by_class": "observable_class",
    }
    totals = {
        "predicted_total": len(predicted),
        "truth_total": len(truth),
        "absolute_error": abs(len(predicted) - len(truth)),
        "percentage_error": _ratio(abs(len(predicted) - len(truth)), len(truth)),
        "overcount": max(len(predicted) - len(truth), 0),
        "undercount": max(len(truth) - len(predicted), 0),
    }
    dimension_errors: dict[str, dict[str, Any]] = {}
    for name, key in dimensions.items():
        predicted_counts = Counter(str(event.get("class" if key == "observable_class" else key, "")) for event in predicted)
        truth_counts = Counter(str(event.get(key, "")) for event in truth)
        labels = sorted(set(predicted_counts) | set(truth_counts))
        dimension_errors[name] = {
            label: {
                "predicted": predicted_counts[label],
                "truth": truth_counts[label],
                "absolute_error": abs(predicted_counts[label] - truth_counts[label]),
            }
            for label in labels
        }
    return {"totals": totals, "dimensions": dimension_errors}


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _mean(values: list[int]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
