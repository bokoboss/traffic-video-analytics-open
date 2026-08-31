from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReviewState = Literal["accepted", "unknown", "ambiguous", "needs_review"]


@dataclass(frozen=True)
class ClassEvidence:
    pts_ms: int
    native_class: str
    confidence: float
    source: str = "detector"


@dataclass(frozen=True)
class FusedClass:
    label: str
    confidence: float
    review_state: ReviewState
    evidence_count: int
    warnings: tuple[str, ...]


def fuse_track_class(
    evidence: tuple[ClassEvidence, ...],
    *,
    min_confidence: float = 0.45,
    ambiguity_margin: float = 0.12,
) -> FusedClass:
    if not evidence:
        return FusedClass("unknown", 0.0, "unknown", 0, ("no_class_evidence",))

    scores: dict[str, float] = {}
    counts: dict[str, int] = {}
    warnings: list[str] = []
    for item in evidence:
        if item.confidence < 0 or item.confidence > 1:
            warnings.append("invalid_confidence_ignored")
            continue
        scores[item.native_class] = scores.get(item.native_class, 0.0) + item.confidence
        counts[item.native_class] = counts.get(item.native_class, 0) + 1

    if not scores:
        return FusedClass("unknown", 0.0, "unknown", len(evidence), tuple(sorted(set(warnings))))

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    top_label, top_score = ranked[0]
    total_score = sum(scores.values())
    confidence = top_score / total_score if total_score else 0.0
    average_confidence = top_score / counts[top_label]

    if average_confidence < min_confidence:
        return FusedClass(top_label, confidence, "needs_review", len(evidence), tuple(sorted(set(warnings))))
    if len(ranked) > 1 and confidence - (ranked[1][1] / total_score) < ambiguity_margin:
        return FusedClass(top_label, confidence, "ambiguous", len(evidence), tuple(sorted(set(warnings))))
    if len(counts) > 1:
        warnings.append("conflicting_frame_classes")
    return FusedClass(top_label, confidence, "accepted", len(evidence), tuple(sorted(set(warnings))))
