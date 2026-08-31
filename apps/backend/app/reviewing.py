"""Deterministic Milestone 6E review overlay semantics.

This module deliberately knows nothing about SQLite, FastAPI or a detector. It
turns immutable engineering events plus append-only actions into effective
reviewed events. The persistence adapter is responsible for validating the
scope/provenance and storing the resulting revision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .engineering_outputs import EngineeringClass, VALID_CANONICAL_DIRECTIONS


REVIEW_SCOPE_TYPES = frozenset({"FULL_RESULT", "LINE", "TIME_INTERVAL", "DIAGNOSTIC_SUBSET"})
REVIEW_STATUSES = frozenset({"NOT_STARTED", "IN_REVIEW", "REVIEW_COMPLETE", "BLOCKED", "STALE", "SUPERSEDED"})
REVIEW_ACTION_TYPES = frozenset(
    {
        "CONFIRM_EVENT",
        "REJECT_FALSE_POSITIVE",
        "CHANGE_CLASS",
        "CHANGE_DIRECTION",
        "CHANGE_LINE",
        "ADJUST_TIMESTAMP",
        "MARK_DUPLICATE",
        "ADD_MISSED_EVENT",
        "MARK_UNSCORABLE",
        "ADD_NOTE",
        "REVERSE_ACTION",
    }
)
LEGACY_ACTION_TYPES = {
    "approve": "CONFIRM_EVENT",
    "exclude": "REJECT_FALSE_POSITIVE",
    "change_class": "CHANGE_CLASS",
    "change_movement": "CHANGE_DIRECTION",
    "add_manual": "ADD_MISSED_EVENT",
    "reverse": "REVERSE_ACTION",
}
MATERIAL_ACTION_TYPES = frozenset(
    {
        "REJECT_FALSE_POSITIVE",
        "CHANGE_CLASS",
        "CHANGE_DIRECTION",
        "CHANGE_LINE",
        "ADJUST_TIMESTAMP",
        "MARK_DUPLICATE",
        "ADD_MISSED_EVENT",
        "MARK_UNSCORABLE",
    }
)
EXCLUDED_REVIEW_STATUSES = frozenset({"REJECTED_FALSE_POSITIVE", "DUPLICATE_SUPPRESSED", "UNSCORABLE", "STALE"})
# Status is a projection of the active overlay, not a last-write-wins field.
# Exclusion and quality states therefore take precedence over confirmations or
# scalar corrections until the specific action is explicitly reversed.
REVIEW_STATUS_PRECEDENCE = (
    "STALE",
    "UNSCORABLE",
    "REJECTED_FALSE_POSITIVE",
    "DUPLICATE_SUPPRESSED",
    "CORRECTED",
    "CONFIRMED",
    "UNREVIEWED",
)


class ReviewDomainError(ValueError):
    """Typed validation error with a stable machine-readable code."""

    def __init__(self, code: str, detail: str, *, field: str | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.field = field


@dataclass(frozen=True)
class ReversalState:
    active_by_id: dict[str, bool]
    active_ids: tuple[str, ...]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_action_type(value: str) -> str:
    normalized = LEGACY_ACTION_TYPES.get(value, value.upper())
    if normalized not in REVIEW_ACTION_TYPES:
        raise ReviewDomainError("INVALID_ACTION_TYPE", f"Unsupported review action: {value}", field="action_type")
    return normalized


def action_payload(action: Mapping[str, Any]) -> dict[str, Any]:
    payload = action.get("payload")
    if payload is None:
        payload = action.get("payload_json")
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            decoded = {}
        payload = decoded if isinstance(decoded, dict) else {}
    result = dict(payload) if isinstance(payload, Mapping) else {}
    aliases = {
        "engineering_class": ("engineering_class", "class", "new_classification"),
        "direction": ("direction", "new_direction", "new_movement"),
        "counting_line_id": ("counting_line_id", "line_id", "new_line_id"),
        "crossing_pts_ms": ("crossing_pts_ms", "pts_ms", "new_crossing_pts_ms"),
        # `target_event_id` identifies the event being reviewed. It is not the
        # duplicate target; conflating those identities would make a confirm
        # action look like a self-duplicate in audit payloads.
        "duplicate_of": ("duplicate_of",),
        "reason": ("reason", "reason_text"),
        "evidence_reference": ("evidence_reference", "evidence_ref"),
        "manual_observation_note": ("manual_observation_note", "observation_note"),
    }
    for canonical, names in aliases.items():
        if canonical in result:
            continue
        for name in names:
            if action.get(name) is not None:
                result[canonical] = action[name]
                break
    return result


def action_target_event_id(action: Mapping[str, Any]) -> str | None:
    value = action.get("target_event_id") or action.get("event_id")
    return str(value) if value else None


def action_order_key(action: Mapping[str, Any]) -> tuple[int, str, str]:
    try:
        order = int(action.get("action_order") or 0)
    except (TypeError, ValueError):
        order = 0
    return order, str(action.get("created_at") or ""), str(action.get("id") or action.get("review_action_id") or "")


def reversal_state(actions: Iterable[Mapping[str, Any]]) -> ReversalState:
    """Resolve reversal chains without mutating any action row.

    The newest direct reversal controls a target. A reversal of that reversal
    therefore restores the target, while a reversal aimed at an unrelated
    action has no effect. Cycles are rejected by the persistence layer; the
    cycle guard here fails closed so projection cannot become nondeterministic.
    """

    ordered = sorted(actions, key=action_order_key)
    by_id = {str(action.get("id") or action.get("review_action_id")): action for action in ordered}
    reverse_by_target: dict[str, list[Mapping[str, Any]]] = {}
    for action in ordered:
        if normalize_action_type(str(action.get("action_type"))) != "REVERSE_ACTION":
            continue
        target = action.get("reverses_action_id") or action_payload(action).get("reverses_action_id")
        if target:
            reverse_by_target.setdefault(str(target), []).append(action)

    memo: dict[str, bool] = {}

    def is_active(action_id: str, stack: set[str]) -> bool:
        if action_id in memo:
            return memo[action_id]
        if action_id in stack:
            return False
        direct = reverse_by_target.get(action_id, [])
        if not direct:
            memo[action_id] = True
            return True
        latest = max(direct, key=action_order_key)
        reverse_id = str(latest.get("id") or latest.get("review_action_id"))
        result = not is_active(reverse_id, stack | {action_id})
        memo[action_id] = result
        return result

    active = {action_id: is_active(action_id, set()) for action_id in by_id}
    return ReversalState(active_by_id=active, active_ids=tuple(action_id for action_id in by_id if active[action_id]))


def validate_reversal_target(actions: Iterable[Mapping[str, Any]], target_id: str, new_action_id: str | None = None) -> None:
    action_list = list(actions)
    by_id = {str(action.get("id") or action.get("review_action_id")): action for action in action_list}
    if target_id not in by_id:
        raise ReviewDomainError("REVERSAL_TARGET_NOT_FOUND", "The reversal target does not exist.", field="reverses_action_id")
    if new_action_id and target_id == new_action_id:
        raise ReviewDomainError("REVERSAL_CYCLE", "An action cannot reverse itself.", field="reverses_action_id")
    cursor = target_id
    seen: set[str] = set()
    while cursor:
        if cursor in seen or cursor == new_action_id:
            raise ReviewDomainError("REVERSAL_CYCLE", "The reversal chain would contain a cycle.", field="reverses_action_id")
        seen.add(cursor)
        node = by_id.get(cursor)
        if node is None:
            break
        cursor = str(node.get("reverses_action_id") or action_payload(node).get("reverses_action_id") or "")


def active_actions_for_target(actions: Iterable[Mapping[str, Any]], target_event_id: str) -> list[Mapping[str, Any]]:
    action_list = list(actions)
    state = reversal_state(action_list)
    direct_ids = {
        str(action.get("id") or action.get("review_action_id"))
        for action in action_list
        if action_target_event_id(action) == target_event_id
    }
    changed = True
    while changed:
        changed = False
        for action in action_list:
            if normalize_action_type(str(action.get("action_type"))) != "REVERSE_ACTION":
                continue
            target = str(action.get("reverses_action_id") or action_payload(action).get("reverses_action_id") or "")
            if target in direct_ids:
                action_id = str(action.get("id") or action.get("review_action_id"))
                if action_id not in direct_ids:
                    direct_ids.add(action_id)
                    changed = True
    return [
        action
        for action in action_list
        if str(action.get("id") or action.get("review_action_id")) in direct_ids
        and state.active_by_id.get(str(action.get("id") or action.get("review_action_id")), False)
    ]


def _latest_action(actions: Iterable[Mapping[str, Any]], action_types: set[str]) -> Mapping[str, Any] | None:
    candidates = [
        action
        for action in actions
        if normalize_action_type(str(action.get("action_type"))) in action_types
    ]
    return max(candidates, key=action_order_key) if candidates else None


def _value(action: Mapping[str, Any] | None, key: str) -> Any:
    if action is None:
        return None
    payload = action_payload(action)
    if key in payload:
        return payload[key]
    aliases = {
        "engineering_class": action.get("new_classification"),
        "direction": action.get("new_movement"),
        "counting_line_id": action.get("new_line_id"),
        "crossing_pts_ms": action.get("new_crossing_pts_ms"),
    }
    return aliases.get(key)


def _notes(actions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for action in sorted(actions, key=action_order_key):
        if normalize_action_type(str(action.get("action_type"))) != "ADD_NOTE":
            continue
        payload = action_payload(action)
        comment = str(action.get("comment") or action.get("reason") or payload.get("comment") or payload.get("note") or "")
        if comment:
            notes.append({"action_id": action.get("id") or action.get("review_action_id"), "comment": comment})
    return notes


def project_review_event(
    base_event: Mapping[str, Any],
    actions: Iterable[Mapping[str, Any]],
    *,
    session_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply active actions to one immutable automatic engineering event.

    Confirmation is an affirmative observation, not an implicit reversal.
    Every exclusion remains active until a matching ``REVERSE_ACTION`` is
    itself active.  The status precedence below is evaluated only after the
    reversal graph has been resolved by :func:`active_actions_for_target`.
    """

    event_id = str(base_event.get("source_event_id") or base_event.get("id") or "")
    target_actions = active_actions_for_target(actions, event_id)
    ordered = sorted(target_actions, key=action_order_key)
    original = {
        "line_id": str(base_event.get("counting_line_id") or base_event.get("effective_line_id") or ""),
        "line_name": str(base_event.get("counting_line_name") or base_event.get("effective_line_name") or ""),
        "direction": str(base_event.get("canonical_direction") or base_event.get("effective_direction") or ""),
        "crossing_pts_ms": int(base_event.get("event_pts_ms") or base_event.get("effective_crossing_pts_ms") or 0),
        "class": str(base_event.get("engineering_class") or base_event.get("effective_class") or EngineeringClass.UNKNOWN.value),
        "classification_status": str(base_event.get("classification_status") or "UNKNOWN"),
    }
    result = dict(original)
    corrected: list[str] = []
    notes = _notes(ordered)
    for field, action_type, payload_key in (
        ("class", "CHANGE_CLASS", "engineering_class"),
        ("direction", "CHANGE_DIRECTION", "direction"),
        ("line_id", "CHANGE_LINE", "counting_line_id"),
        ("crossing_pts_ms", "ADJUST_TIMESTAMP", "crossing_pts_ms"),
    ):
        action = _latest_action(ordered, {action_type})
        value = _value(action, payload_key)
        if value is not None:
            if field == "crossing_pts_ms":
                result[field] = int(value)
            else:
                result[field] = str(value)
            if result[field] != original[field]:
                corrected.append(field)
    line_action = _latest_action(ordered, {"CHANGE_LINE"})
    if line_action is not None:
        line_name = _value(line_action, "counting_line_name")
        if line_name is not None:
            result["line_name"] = str(line_name)
    duplicate_action = _latest_action(ordered, {"MARK_DUPLICATE"})
    duplicate_of = str(_value(duplicate_action, "duplicate_of")) if duplicate_action and _value(duplicate_action, "duplicate_of") else None
    reject_action = _latest_action(ordered, {"REJECT_FALSE_POSITIVE"})
    confirm_action = _latest_action(ordered, {"CONFIRM_EVENT"})
    unscorable_action = _latest_action(ordered, {"MARK_UNSCORABLE"})
    rejected = reject_action is not None
    unscorable = unscorable_action is not None
    status = "UNREVIEWED"
    # Keep this order synchronized with REVIEW_STATUS_PRECEDENCE.  These are
    # mutually prioritized disclosure states, while scalar corrections and
    # notes remain independent overlays.
    if unscorable:
        status = "UNSCORABLE"
    elif rejected:
        status = "REJECTED_FALSE_POSITIVE"
    elif duplicate_of:
        status = "DUPLICATE_SUPPRESSED"
    elif corrected:
        status = "CORRECTED"
    elif confirm_action is not None:
        status = "CONFIRMED"
    if not ordered and base_event.get("review_status"):
        status = str(base_event["review_status"])
    active_ids = [str(action.get("id") or action.get("review_action_id")) for action in ordered]
    context = session_context or {}
    evidence = dict(base_event.get("evidence") or {})
    for key in (
        "side_a_name", "side_b_name", "absolute_event_time", "event_time_status",
        "source_fingerprint_sha256", "source_frame_index", "source_frame_pts_ms",
        "benchmark_error_category",
    ):
        if base_event.get(key) is not None:
            evidence[key] = base_event[key]
    return {
        "reviewed_event_id": str(base_event.get("reviewed_event_id") or ""),
        "origin": "AUTOMATIC",
        "source_event_id": event_id,
        "human_add_action_id": None,
        "effective_line_id": result["line_id"],
        "effective_line_name": result["line_name"],
        "effective_direction": result["direction"],
        "effective_crossing_pts_ms": result["crossing_pts_ms"],
        "effective_class": result["class"],
        "classification_status": result["classification_status"],
        "review_status": status,
        "duplicate_of": duplicate_of,
        "corrected_fields": sorted(set(corrected)),
        "effective_action_ids": active_ids,
        "notes": notes,
        "evidence": evidence,
        "runtime_configuration_hash": base_event.get("runtime_configuration_hash") or context.get("runtime_configuration_hash"),
        "scene_revision": str(base_event.get("scene_revision") or context.get("scene_revision") or ""),
        "taxonomy_revision": str(base_event.get("taxonomy_revision") or context.get("taxonomy_revision") or ""),
        "stale_status": "CURRENT",
    }


def project_human_added_event(action: Mapping[str, Any], *, session_context: Mapping[str, Any]) -> dict[str, Any]:
    payload = action_payload(action)
    action_id = str(action.get("id") or action.get("review_action_id") or "")
    active = str(action_id) in set(reversal_state([action]).active_ids)
    status = "CONFIRMED" if active else "REJECTED_FALSE_POSITIVE"
    return {
        "reviewed_event_id": f"revt_{action_id}",
        "origin": "HUMAN_ADDED",
        "source_event_id": None,
        "human_add_action_id": action_id,
        "effective_line_id": str(payload.get("counting_line_id") or ""),
        "effective_line_name": str(payload.get("counting_line_name") or payload.get("line_name") or payload.get("counting_line_id") or ""),
        "effective_direction": str(payload.get("direction") or ""),
        "effective_crossing_pts_ms": int(payload.get("crossing_pts_ms") or 0),
        "effective_class": str(payload.get("engineering_class") or EngineeringClass.UNKNOWN.value),
        "classification_status": "MANUAL_REVIEW",
        "review_status": status,
        "duplicate_of": None,
        "corrected_fields": ["origin", "line_id", "direction", "crossing_pts_ms", "class"],
        "effective_action_ids": [action_id] if active else [],
        "notes": [{"action_id": action_id, "comment": str(payload.get("manual_observation_note") or payload.get("reason") or "")}],
        "evidence": {"evidence_reference": payload.get("evidence_reference"), "manual": True},
        "runtime_configuration_hash": session_context.get("runtime_configuration_hash"),
        "scene_revision": str(session_context.get("scene_revision") or ""),
        "taxonomy_revision": str(session_context.get("taxonomy_revision") or ""),
        "stale_status": "CURRENT",
    }


def is_active_review_event(event: Mapping[str, Any]) -> bool:
    return str(event.get("review_status")) not in EXCLUDED_REVIEW_STATUSES


def interval_index_for_event(event: Mapping[str, Any], context: Mapping[str, Any]) -> int | None:
    pts = int(event.get("effective_crossing_pts_ms") or 0)
    start = int(context.get("analysis_start_pts_ms") or 0)
    end = int(context.get("analysis_end_pts_ms") or 0)
    origin = int(context.get("interval_origin_pts_ms") if context.get("interval_origin_pts_ms") is not None else start)
    if pts < start or pts >= end:
        return None
    return (pts - origin) // (15 * 60 * 1000)


def reconcile_reviewed_events(
    events: Iterable[Mapping[str, Any]],
    *,
    automatic_total: int,
    context: Mapping[str, Any],
    expected_event_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    rows = list(events)
    automatic_rows = [row for row in rows if row.get("origin") == "AUTOMATIC"]
    human_rows = [row for row in rows if row.get("origin") == "HUMAN_ADDED"]
    rejected = [row for row in rows if row.get("review_status") == "REJECTED_FALSE_POSITIVE"]
    duplicates = [row for row in rows if row.get("review_status") == "DUPLICATE_SUPPRESSED"]
    unscorable = [row for row in rows if row.get("review_status") == "UNSCORABLE"]
    automatic_rejected = [row for row in automatic_rows if row.get("review_status") == "REJECTED_FALSE_POSITIVE"]
    automatic_duplicates = [row for row in automatic_rows if row.get("review_status") == "DUPLICATE_SUPPRESSED"]
    automatic_unscorable = [row for row in automatic_rows if row.get("review_status") == "UNSCORABLE"]
    human_rejected = [row for row in human_rows if row.get("review_status") == "REJECTED_FALSE_POSITIVE"]
    active = [row for row in rows if is_active_review_event(row)]
    diagnostics: list[dict[str, Any]] = []
    geometry = context.get("geometry") if isinstance(context.get("geometry"), Mapping) else {}
    valid_line_ids = {
        str(item.get("id"))
        for item in (geometry.get("counting_lines") or [])
        if isinstance(item, Mapping) and item.get("active", True)
    }
    if len({str(row.get("reviewed_event_id")) for row in rows}) != len(rows):
        diagnostics.append({"code": "DUPLICATE_REVIEWED_EVENT_ID", "detail": "reviewed event identity is not unique"})
    source_ids = [str(row.get("source_event_id")) for row in automatic_rows if row.get("source_event_id")]
    if len(set(source_ids)) != len(source_ids):
        diagnostics.append({"code": "DUPLICATE_ACTIVE_SOURCE_EVENT", "detail": "an automatic source event appears more than once"})
    if len(automatic_rows) != automatic_total:
        diagnostics.append({"code": "AUTOMATIC_EVENT_LEDGER_MISMATCH", "expected": automatic_total, "actual": len(automatic_rows)})
    if expected_event_ids is not None:
        expected = {str(value) for value in expected_event_ids}
        actual = set(source_ids)
        if expected != actual:
            diagnostics.append(
                {
                    "code": "MISSING_OR_EXTRA_AUTOMATIC_EVENT",
                    "missing": sorted(expected - actual),
                    "extra": sorted(actual - expected),
                }
            )
    for row in rows:
        if valid_line_ids and str(row.get("effective_line_id") or "") not in valid_line_ids:
            diagnostics.append({"code": "INVALID_COUNTING_LINE", "reviewed_event_id": row.get("reviewed_event_id")})
        if row.get("duplicate_of") and str(row["duplicate_of"]) == str(row.get("source_event_id") or row.get("reviewed_event_id")):
            diagnostics.append({"code": "SELF_DUPLICATE", "reviewed_event_id": row.get("reviewed_event_id")})
        if row.get("duplicate_of") and str(row["duplicate_of"]) not in {str(item.get("source_event_id")) for item in rows if item.get("source_event_id")} | {str(item.get("reviewed_event_id")) for item in rows}:
            diagnostics.append({"code": "DUPLICATE_TARGET_NOT_FOUND", "reviewed_event_id": row.get("reviewed_event_id")})
        if row.get("effective_direction") not in VALID_CANONICAL_DIRECTIONS:
            diagnostics.append({"code": "INVALID_DIRECTION", "reviewed_event_id": row.get("reviewed_event_id")})
        if row.get("effective_class") not in {item.value for item in EngineeringClass}:
            diagnostics.append({"code": "UNSUPPORTED_CLASS", "reviewed_event_id": row.get("reviewed_event_id")})
        if interval_index_for_event(row, context) is None:
            diagnostics.append({"code": "EVENT_OUTSIDE_ANALYSIS_INTERVAL", "reviewed_event_id": row.get("reviewed_event_id")})
        if row.get("origin") == "HUMAN_ADDED":
            evidence = row.get("evidence") if isinstance(row.get("evidence"), Mapping) else {}
            notes = evidence.get("notes") if isinstance(evidence.get("notes"), list) else []
            has_note = any(isinstance(note, Mapping) and str(note.get("comment") or "").strip() for note in notes)
            if row.get("source_event_id") is not None or not row.get("human_add_action_id"):
                diagnostics.append({"code": "INVALID_HUMAN_ADDED_PROVENANCE", "reviewed_event_id": row.get("reviewed_event_id")})
            if not evidence.get("evidence_reference") and not has_note:
                diagnostics.append({"code": "HUMAN_ADDED_EVIDENCE_MISSING", "reviewed_event_id": row.get("reviewed_event_id")})
            if row.get("review_status") not in {"CONFIRMED", "REJECTED_FALSE_POSITIVE"}:
                diagnostics.append({"code": "INVALID_HUMAN_ADDED_STATUS", "reviewed_event_id": row.get("reviewed_event_id")})
    equation = {
        "automatic_events": automatic_total,
        "active_human_added_events": len([row for row in human_rows if is_active_review_event(row)]),
        "rejected_false_positives": len(automatic_rejected),
        "duplicate_suppressed_events": len(automatic_duplicates),
        "unscorable_excluded_events": len(automatic_unscorable),
        "rejected_human_added_events": len(human_rejected),
        "final_active_reviewed_events": len(active),
    }
    calculated = (
        automatic_total
        + equation["active_human_added_events"]
        - len(automatic_rejected)
        - len(automatic_duplicates)
        - len(automatic_unscorable)
    )
    equation["calculated_active_reviewed_events"] = calculated
    if calculated != len(active):
        diagnostics.append({"code": "ACTIVE_COUNT_EQUATION_MISMATCH", "expected": calculated, "actual": len(active)})

    def aggregate(key: str) -> dict[str, int]:
        totals: dict[str, int] = {}
        for row in active:
            value = str(row.get(key) or "")
            totals[value] = totals.get(value, 0) + 1
        return dict(sorted(totals.items()))

    by_interval: dict[str, int] = {}
    for row in active:
        interval = interval_index_for_event(row, context)
        if interval is not None:
            key = str(interval)
            by_interval[key] = by_interval.get(key, 0) + 1
    by_line = aggregate("effective_line_id")
    by_direction = aggregate("effective_direction")
    by_class = aggregate("effective_class")
    return {
        "status": "PASSED" if not diagnostics else "FAILED",
        "engineering_ready": not diagnostics,
        "equation": equation,
        "by_line": by_line,
        "by_direction": by_direction,
        "by_class": by_class,
        "by_interval": dict(sorted(by_interval.items(), key=lambda item: int(item[0]))),
        "diagnostics": diagnostics,
        "scope_total": len(automatic_rows),
        "reviewed_event_count": len(active),
        "confirmed_count": len([row for row in active if row.get("review_status") == "CONFIRMED"]),
        "corrected_count": len([row for row in active if row.get("review_status") == "CORRECTED"]),
        "rejected_count": len(rejected),
        "duplicate_count": len(duplicates),
        "human_added_count": len(human_rows),
        "unscorable_count": len(unscorable),
        "unreviewed_count": len([row for row in rows if row.get("review_status") == "UNREVIEWED"]),
        "review_coverage_percent": round((len(rows) - len([row for row in rows if row.get("review_status") == "UNREVIEWED"])) / len(rows) * 100, 2) if rows else 100.0,
    }
