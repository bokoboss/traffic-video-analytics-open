from __future__ import annotations

from enum import StrEnum


class ProcessingMode(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    REAL_VIDEO = "REAL_VIDEO"


class DeviceMode(StrEnum):
    AUTO = "AUTO"
    CPU = "CPU"
    CUDA = "CUDA"


class JobState(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ProgressPhase(StrEnum):
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    PREPARING_MEDIA = "PREPARING_MEDIA"
    INITIALIZING_RUNTIME = "INITIALIZING_RUNTIME"
    LOADING_MODEL = "LOADING_MODEL"
    DECODING = "DECODING"
    PROCESSING = "PROCESSING"
    FINALIZING_EVENTS = "FINALIZING_EVENTS"
    AGGREGATING = "AGGREGATING"
    PERSISTING_RESULTS = "PERSISTING_RESULTS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = {JobState.CANCELLED, JobState.COMPLETED, JobState.FAILED}
ACTIVE_STATES = {
    JobState.CREATED,
    JobState.QUEUED,
    JobState.STARTING,
    JobState.RUNNING,
    JobState.CANCELLATION_REQUESTED,
}

VALID_TRANSITIONS: dict[JobState, set[JobState]] = {
    JobState.CREATED: {JobState.QUEUED, JobState.CANCELLATION_REQUESTED, JobState.FAILED},
    JobState.QUEUED: {JobState.STARTING, JobState.CANCELLATION_REQUESTED, JobState.FAILED},
    JobState.STARTING: {JobState.RUNNING, JobState.CANCELLATION_REQUESTED, JobState.FAILED},
    JobState.RUNNING: {JobState.CANCELLATION_REQUESTED, JobState.COMPLETED, JobState.FAILED},
    JobState.CANCELLATION_REQUESTED: {JobState.CANCELLED, JobState.COMPLETED, JobState.FAILED},
    JobState.CANCELLED: set(),
    JobState.COMPLETED: set(),
    JobState.FAILED: set(),
}


def is_terminal(value: str | JobState) -> bool:
    return JobState(value) in TERMINAL_STATES


def is_active(value: str | JobState) -> bool:
    return JobState(value) in ACTIVE_STATES


def validate_transition(current: str | JobState, next_state: str | JobState) -> None:
    current_state = JobState(current)
    target_state = JobState(next_state)
    if current_state == target_state:
        return
    if target_state not in VALID_TRANSITIONS[current_state]:
        raise ValueError(f"invalid job transition: {current_state.value} -> {target_state.value}")
