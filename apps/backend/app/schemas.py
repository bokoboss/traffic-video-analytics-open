from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, model_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    location: str
    study_type: str = "mock-intersection"
    language: Literal["th", "en"] = "th"


class Project(ProjectCreate):
    id: str
    state: str
    stale: bool
    created_at: datetime
    updated_at: datetime | None = None
    version: int = 1


class ProjectUpdate(BaseModel):
    name: str = Field(min_length=1)
    location: str
    study_type: str
    language: Literal["th", "en"] = "th"
    expected_version: int = Field(ge=1)


class SourceCreate(BaseModel):
    project_id: str
    file_name: str
    fingerprint_sha256: str
    source_started_at: datetime
    timezone_name: str = "Asia/Bangkok"
    analysis_start_pts_ms: int = 0
    analysis_end_pts_ms: int = 3_600_000


class TimeConfigurationUpdate(BaseModel):
    source_started_at: datetime
    timezone_name: str = Field(min_length=1)
    analysis_start_pts_ms: int = Field(ge=0)
    analysis_end_pts_ms: int = Field(gt=0)
    source_offset_ms: int = Field(default=0, ge=0)


class SceneCreate(BaseModel):
    project_id: str
    template: Literal["road_segment", "intersection", "pedestrian_crossing"]


class ReferenceFrameRequest(BaseModel):
    mode: Literal["beginning", "analysis_start", "timestamp"] = "analysis_start"
    requested_pts_ms: int | None = Field(default=None, ge=0)
    force_regenerate: bool = False


class SceneGeometrySave(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    template: Literal["road_segment", "intersection", "pedestrian_crossing"] = "intersection"
    reference_frame_id: str
    geometry: dict[str, Any]


class AnalysisRun(BaseModel):
    id: str
    project_id: str
    state: str
    progress_percent: int
    result_version: str


class AutoCountEventOut(BaseModel):
    id: str
    run_id: str
    technical_key: str
    pts_ms: int
    classification: str
    movement: str
    confidence: float
    qc_state: str


class ReviewActionCreate(BaseModel):
    event_id: str
    action_type: Literal["approve", "change_class", "change_movement", "exclude", "reverse"]
    reviewer: str = "mock-reviewer"
    new_classification: str | None = None
    new_movement: str | None = None
    reason: str | None = None
    reverses_action_id: str | None = None


class ReviewSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processing_run_id: str = Field(min_length=1)
    engineering_result_revision_id: str | None = Field(default=None, min_length=1)
    review_scope_type: Literal["FULL_RESULT", "LINE", "TIME_INTERVAL", "DIAGNOSTIC_SUBSET"] = "FULL_RESULT"
    review_scope_filter: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(default="operator", min_length=1, max_length=120)
    new_revision: bool = False


class ReviewActionAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Literal[
        "CONFIRM_EVENT", "REJECT_FALSE_POSITIVE", "CHANGE_CLASS", "CHANGE_DIRECTION",
        "CHANGE_LINE", "ADJUST_TIMESTAMP", "MARK_DUPLICATE", "ADD_MISSED_EVENT",
        "MARK_UNSCORABLE", "ADD_NOTE", "REVERSE_ACTION",
    ]
    target_event_id: str | None = Field(default=None, min_length=1)
    target_review_event_id: str | None = Field(default=None, min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason_code: str | None = Field(default=None, max_length=120)
    comment: str | None = Field(default=None, max_length=2000)
    reviewer_id: str = Field(default="operator", min_length=1, max_length=120)
    expected_review_revision: int = Field(default=0, ge=0)
    client_request_id: str | None = Field(default=None, min_length=1, max_length=160)
    reverses_action_id: str | None = Field(default=None, min_length=1)
    source_event_revision: str | None = Field(default=None, max_length=160)


class ReviewCompletionRequest(BaseModel):
    completed_by: str = Field(default="operator", min_length=1, max_length=120)
    expected_review_revision: int | None = Field(default=None, ge=0)


class CertificationCreateRequest(BaseModel):
    certified_by: str = Field(min_length=1, max_length=120)
    benchmark_reference: dict[str, Any] = Field(default_factory=dict)
    qualification_disclosure: str = Field(default="Benchmark diagnostics are disclosed separately; human review does not prove detector accuracy.", max_length=2000)
    rights_disclosure: str = Field(default="Rights status is operator-supplied and is not inferred by this application.", max_length=2000)


class CertificationRevocationRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    revoked_by: str = Field(min_length=1, max_length=120)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["CSV", "XLSX", "JSON", "csv", "xlsx", "json"]
    language: Literal["th", "en"] = "th"
    options: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str | None = Field(default=None, min_length=1, max_length=160)
    created_by: str = Field(default="operator", min_length=1, max_length=120)


class ReviewSessionOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    project_id: str
    processing_run_id: str
    engineering_result_revision_id: str
    review_scope_type: str
    review_status: str
    review_revision: int
    stale_status: str


class ReviewActionAppendOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    action_type: str
    review_session_id: str
    content_hash: str


class CertificationRevisionOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    certification_revision_id: str
    review_session_id: str
    reviewed_projection_revision_id: str
    certification_hash: str
    certification_content_hash: str = ""
    certification_content_hash_status: str = "LEGACY_UNRESOLVED"
    status: str


class ExportRevisionOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    export_revision_id: str
    certification_revision_id: str
    format: str
    status: str
    artifact_generation_status: str = ""
    source_certification_status: str = ""
    effective_export_status: str = ""


class SyntheticFixtureRequest(BaseModel):
    fixture_id: str = "default-crossing-fixture"
    expected_scene_version: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    run_again: bool = False


class ProcessingJobCreate(BaseModel):
    fixture_id: str | None = Field(default=None, min_length=1, max_length=120)
    mode: Literal["SYNTHETIC", "REAL_VIDEO"] = "SYNTHETIC"
    configuration: dict[str, Any] | None = None
    expected_scene_version: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    run_again: bool = False
    auto_start: bool = True
    retry_of_run_id: str | None = None
    processing_configuration_revision_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_processing_input(self) -> "ProcessingJobCreate":
        if self.mode == "SYNTHETIC" and self.fixture_id is None:
            self.fixture_id = "default-crossing-fixture"
        if self.mode == "REAL_VIDEO" and self.fixture_id is not None:
            raise ValueError("real-video processing does not accept fixture_id")
        return self


class GuidedProcessingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_quality: Literal["STANDARD", "HIGH", "VERY_HIGH"] = "STANDARD"
    processing_preference: Literal["SPEED", "BALANCED", "DETAIL"] = "BALANCED"
    detection_sensitivity: Literal["CONSERVATIVE", "BALANCED", "SENSITIVE"] = "BALANCED"
    scene_type: Literal[
        "GENERAL_TRAFFIC",
        "DENSE_TRAFFIC",
        "MOTORCYCLE_HEAVY",
        "PEDESTRIAN",
        "MIXED_TRAFFIC",
        "SMALL_DISTANT_OBJECTS",
    ] = "GENERAL_TRAFFIC"
    occlusion: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    object_size: Literal["NORMAL", "SMALL", "VERY_SMALL"] = "NORMAL"


class ExpertProcessingOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_mode: Literal["AUTO", "CPU", "CUDA"] | None = None
    detector_id: Literal["detector.ultralytics-yolo11n-coco"] | None = None
    tracker_id: Literal["tracker.trackers-bytetrack"] | None = None
    confidence_threshold: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=1)
    iou_threshold: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=1)
    image_size: Literal[320, 480, 640, 960, 1280, 1536, 1920, 2560, 4096] | None = None
    frame_stride: StrictInt | None = Field(default=None, ge=1, le=8)
    class_allowlist: list[Literal["bicycle", "bus", "car", "motorcycle", "other", "person", "truck"]] | None = None
    track_activation_threshold: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=1)
    lost_track_buffer: StrictInt | None = Field(default=None, ge=1, le=300)
    minimum_iou_threshold: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=1)
    minimum_consecutive_frames: StrictInt | None = Field(default=None, ge=1, le=60)
    minimum_track_duration_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    minimum_track_observations: StrictInt | None = Field(default=None, ge=1, le=100)
    crossing_anchor: Literal["bottom_center"] | None = None
    crossing_tolerance: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=0.2)
    crossing_hysteresis: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=0.2)
    minimum_movement_distance: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=1)
    minimum_side_stability_frames: StrictInt | None = Field(default=None, ge=1, le=60)
    duplicate_crossing_cooldown_ms: StrictInt | None = Field(default=None, ge=0, le=10000)
    classification_min_observations: StrictInt | None = Field(default=None, ge=1, le=100)
    classification_min_winning_vote_share: StrictFloat | StrictInt | None = Field(default=None, ge=0.5, le=1)
    classification_min_weighted_share: StrictFloat | StrictInt | None = Field(default=None, ge=0.5, le=1)
    classification_near_tie_margin: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=0.5)


class ProcessingConfigurationResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_code: Literal[
        "BALANCED",
        "HIGH_ACCURACY",
        "FAST_PROCESSING",
        "SMALL_DISTANT_OBJECTS",
        "DENSE_TRAFFIC",
        "MOTORCYCLE_HEAVY",
        "PEDESTRIAN_COUNTING",
        "CUSTOM",
    ] = "BALANCED"
    guided_settings: GuidedProcessingSettings = Field(default_factory=GuidedProcessingSettings)
    expert_overrides: ExpertProcessingOverrides = Field(default_factory=ExpertProcessingOverrides)
    expected_scene_version: int | None = Field(default=None, ge=1)
    created_by: str = Field(default="operator", min_length=1, max_length=120)


class ProcessingConfigurationRevisionCreate(ProcessingConfigurationResolveRequest):
    pass


class PreviewRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["SYNTHETIC", "REAL_VIDEO"] = "REAL_VIDEO"
    processing_configuration_revision_id: str | None = Field(default=None, min_length=1)
    profile_code: Literal[
        "BALANCED",
        "HIGH_ACCURACY",
        "FAST_PROCESSING",
        "SMALL_DISTANT_OBJECTS",
        "DENSE_TRAFFIC",
        "MOTORCYCLE_HEAVY",
        "PEDESTRIAN_COUNTING",
        "CUSTOM",
    ] = "BALANCED"
    guided_settings: GuidedProcessingSettings = Field(default_factory=GuidedProcessingSettings)
    expert_overrides: ExpertProcessingOverrides = Field(default_factory=ExpertProcessingOverrides)
    start_pts_ms: StrictInt = Field(default=0, ge=0)
    end_pts_ms: StrictInt = Field(default=30_000, gt=0)
    expected_scene_version: int | None = Field(default=None, ge=1)
    fixture_id: str | None = Field(default=None, min_length=1, max_length=120)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    created_by: str = Field(default="operator", min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_segment(self) -> "PreviewRunCreate":
        duration = self.end_pts_ms - self.start_pts_ms
        if duration <= 0:
            raise ValueError("preview interval must use [start_pts_ms, end_pts_ms) with positive duration")
        if duration > 120_000:
            raise ValueError("preview interval cannot exceed 120 seconds")
        if self.mode == "SYNTHETIC" and not self.fixture_id:
            raise ValueError("synthetic preview requires fixture_id")
        if self.mode == "REAL_VIDEO" and self.fixture_id is not None:
            raise ValueError("real-video preview does not accept fixture_id")
        return self


class ConfigurationComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processing_configuration_revision_ids: list[str] = Field(min_length=2, max_length=8)
    preview_run_ids: list[str] = Field(default_factory=list, max_length=8)
    start_pts_ms: StrictInt = Field(default=0, ge=0)
    end_pts_ms: StrictInt = Field(default=30_000, gt=0)
    expected_scene_version: int | None = Field(default=None, ge=1)
    created_by: str = Field(default="operator", min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_segment(self) -> "ConfigurationComparisonCreate":
        if self.end_pts_ms <= self.start_pts_ms:
            raise ValueError("comparison interval must use a positive half-open interval")
        return self


class ConfigurationDiffCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_processing_configuration_revision_id: str = Field(min_length=1)
    second_processing_configuration_revision_id: str = Field(min_length=1)
    created_by: str = Field(default="operator", min_length=1, max_length=120)


class OperationalCandidateSelectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processing_configuration_revision_id: str = Field(min_length=1)
    selection_status: Literal[
        "PROFILE_DEFAULT",
        "OPERATOR_SELECTED",
        "BENCHMARK_SUPPORTED",
        "DIAGNOSTIC_ONLY",
    ]
    rationale: str = Field(min_length=1, max_length=1000)
    created_by: str = Field(default="operator", min_length=1, max_length=120)


class CapabilityValidationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processing_configuration_revision_id: str = Field(min_length=1)
    preview_run_id: str | None = Field(default=None, min_length=1)
    validation_type: Literal["RUNTIME", "SYNTHETIC", "REAL_MEDIA", "BENCHMARK"]
    created_by: str = Field(default="operator", min_length=1, max_length=120)


class ProcessingJobCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=240)


class CurrentProcessingRunSelection(BaseModel):
    selected_by: str = Field(default="operator", min_length=1, max_length=120)


class SyntheticValidationRequest(BaseModel):
    synthetic_input: dict[str, Any]
    expected_scene_version: int | None = Field(default=None, ge=1)


class ReviewActionOut(ReviewActionCreate):
    id: str
    created_at: datetime


class AggregateSnapshot(BaseModel):
    id: str
    run_id: str
    result_version: str
    fifteen_minute_counts: list[int]
    hourly_total: int
    phf: float | None
    stale: bool


class GateResponse(BaseModel):
    allowed: bool
    reason: str


class ReadinessComponent(BaseModel):
    component: str | None = None
    status: Literal["STARTING", "READY", "READY_WITH_WARNINGS", "BLOCKED", "NOT_CONFIGURED", "NOT_PROBED"] = "NOT_PROBED"
    required: bool = False
    version: str | None = None
    state: str = "not_probed"
    ready: bool = False
    detail: str | None = None
    remediation: str | None = None


class ReleaseIdentity(BaseModel):
    schema_version: str
    application_name: str
    release_version: str
    release_channel: str
    build_label: str
    git_commit_sha: str
    identity_warning: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["core_ready", "degraded", "not_ready"]
    liveness: dict[str, ReadinessComponent]
    core: dict[str, ReadinessComponent]
    processing: dict[str, Any]
    generated_at: datetime
    startup_phases_ms: dict[str, float] | None = None
    core_status: Literal["READY", "READY_WITH_WARNINGS", "BLOCKED"] = "READY"
    processing_status: Literal["READY", "READY_WITH_WARNINGS", "BLOCKED", "NOT_CONFIGURED"] = "NOT_CONFIGURED"
    application_status: Literal["READY", "READY_WITH_WARNINGS", "BLOCKED"] = "BLOCKED"
    application_ready: bool = False
    overall_status: Literal["READY", "READY_WITH_WARNINGS", "BLOCKED"] = "READY"
    components: dict[str, ReadinessComponent] = Field(default_factory=dict)
    release: ReleaseIdentity


class CertificationRecord(BaseModel):
    id: str
    project_id: str
    run_id: str
    result_version: str
    certified_by: str
    certified_at: datetime


class ExportManifest(BaseModel):
    id: str
    project_id: str
    run_id: str
    result_version: str
    format: Literal["xlsx", "csv"]
    status: Literal["manifest_created"]
    created_at: datetime
    provenance: dict[str, str]


class TaxonomyClassOut(BaseModel):
    id: str
    taxonomy_revision_id: str
    code: str
    display_name_en: str
    display_name_th: str
    object_domain: str
    capability_state: str
    target_reference: str | None = None
    display_order: int


class TaxonomyRevisionOut(BaseModel):
    id: str
    revision: str
    content_hash: str
    status: str
    classes: list[TaxonomyClassOut]
    target_taxonomy: dict[str, Any]


class ClassificationPolicyOut(BaseModel):
    id: str
    revision: str
    content_hash: str
    status: str
    policy: dict[str, Any]


class ProcessingParametersOut(BaseModel):
    detector_id: str
    tracker_id: str
    device_mode: Literal["AUTO", "CPU", "CUDA"]
    confidence_threshold: float
    iou_threshold: float
    image_size: int
    frame_stride: int
    class_allowlist: list[str]
    track_activation_threshold: float
    lost_track_buffer: int
    minimum_iou_threshold: float
    minimum_consecutive_frames: int
    minimum_track_duration_ms: int
    minimum_track_observations: int
    crossing_anchor: Literal["bottom_center"]
    crossing_tolerance: float
    crossing_hysteresis: float
    minimum_movement_distance: float
    minimum_side_stability_frames: int
    duplicate_crossing_cooldown_ms: int
    classification_min_observations: int
    classification_min_winning_vote_share: float
    classification_min_weighted_share: float
    classification_near_tie_margin: float
    sampling: Literal["every_decoded_frame"]


class ProcessingProfileOut(BaseModel):
    id: str
    profile_code: str
    profile_revision: str
    display_name_en: str
    display_name_th: str
    description_en: str
    description_th: str
    intended_use: str
    resolved_parameters: ProcessingParametersOut
    supported_domains: list[str]
    hardware_expectation: str
    known_tradeoffs: list[str]
    created_by: str
    created_at: str
    content_hash: str
    supersedes_revision: str | None = None
    status: str
    schema_revision: str


class ProcessingParameterSpecOut(BaseModel):
    name: str
    value_type: str
    default: Any
    supported: bool
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: list[Any] = Field(default_factory=list)
    unit: str | None = None
    dependency: str | None = None


class ProcessingParameterSchemaOut(BaseModel):
    revision: str
    parameters: list[ProcessingParameterSpecOut]


class ConfigurationValidationOut(BaseModel):
    valid: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    guided_settings: GuidedProcessingSettings
    requested_expert_overrides: ExpertProcessingOverrides
    requested_guided_settings: dict[str, Any] = Field(default_factory=dict)
    resolved_parameters: ProcessingParametersOut
    normalizations: list[dict[str, Any]] = Field(default_factory=list)
    estimated_resource_impact: dict[str, Any]
    configuration_hash: str
    runtime_configuration_hash: str | None = None
    request_provenance_hash: str | None = None
    runtime_provenance_status: str = "LEGACY_UNRESOLVED"
    runtime_provenance: dict[str, Any] = Field(default_factory=dict)
    model_revision: str | None = None
    weight_sha256: str | None = None
    tracker_revision: str
    crossing_policy_revision: str
    classification_policy_revision: str
    device_request: str
    parameter_schema_revision: str
    resolved_device: str
    adapter_support: dict[str, str]
    profile_code: str
    profile_revision: str
    profile_id: str
    configuration_schema_revision: str


class ProcessingConfigurationRevisionOut(ConfigurationValidationOut):
    id: str
    project_id: str | None = None
    created_by: str
    created_at: str
    content_hash: str
    status: str


class PreviewRunOut(BaseModel):
    id: str
    project_id: str
    source_id: str
    source_fingerprint_sha256: str
    scene_revision: str
    scene_semantic_hash: str
    start_pts_ms: int
    end_pts_ms: int
    processing_configuration_revision_id: str
    runtime_configuration_hash: str | None = None
    request_fingerprint: str | None = None
    run_type: Literal["PREVIEW_ONLY"]
    mode: Literal["SYNTHETIC", "REAL_VIDEO"]
    status: str
    created_by: str
    created_at: str
    completed_at: str | None = None
    promoted_full_run_id: str | None = None
    ownership_state: str
    attempt_number: int = 0
    heartbeat_at: str | None = None
    lease_expires_at: str | None = None
    cancellation_reason: str | None = None
    provenance_status: str = "LEGACY_UNRESOLVED"
    statistics: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=lambda: ["PREVIEW_ONLY", "NOT_PRODUCTION_RESULT"])


class ConfigurationComparisonOut(BaseModel):
    id: str
    project_id: str
    source_fingerprint_sha256: str
    scene_revision: str
    segment_start_pts_ms: int
    segment_end_pts_ms: int
    taxonomy_revision: str
    processing_configuration_revision_ids: list[str]
    comparison: dict[str, Any]
    created_by: str
    created_at: str


class ConfigurationDiffOut(BaseModel):
    first_processing_configuration_revision_id: str
    second_processing_configuration_revision_id: str
    first_configuration_hash: str
    second_configuration_hash: str
    first_runtime_configuration_hash: str | None = None
    second_runtime_configuration_hash: str | None = None
    runtime_equivalent: bool = False
    runtime_equivalence_disclosure: str
    changed_parameters: dict[str, dict[str, Any]]
    unchanged_parameters: list[str]
    disclosure: str


class OperationalCandidateSelectionOut(BaseModel):
    id: str
    project_id: str
    processing_configuration_revision_id: str
    selection_status: str
    candidate_status: Literal["OPERATIONAL_CANDIDATE", "DIAGNOSTIC_ONLY"]
    rationale: str
    created_by: str
    created_at: str


class CapabilityValidationOut(BaseModel):
    id: str
    project_id: str
    processing_configuration_revision_id: str
    preview_run_id: str | None = None
    validation_type: str
    status: Literal["READY", "PENDING", "NOT_READY", "INCOMPLETE"]
    evidence: dict[str, Any]
    environment: dict[str, Any]
    content_hash: str
    created_by: str
    created_at: str


class ConflictOut(BaseModel):
    code: str
    detail: str
    resource_id: str | None = None


class ConflictEnvelopeOut(BaseModel):
    detail: ConflictOut


class EngineeringEventOut(BaseModel):
    id: str
    engineering_result_revision_id: str
    run_id: str
    source_event_id: str
    technical_key: str
    source_fingerprint_sha256: str
    scene_revision: str
    counting_line_id: str
    counting_line_name: str
    side_a_name: str
    side_b_name: str
    canonical_direction: str
    readable_direction_name: str
    track_id: str
    event_pts_ms: int
    source_frame_index: int | None = None
    source_frame_pts_ms: int | None = None
    absolute_event_time: str | None = None
    event_timezone_name: str | None = None
    event_time_status: str
    raw_detector_class_id: int | None = None
    raw_detector_class_name: str | None = None
    track_voted_raw_class_id: int | None = None
    track_voted_raw_class_name: str | None = None
    provisional_class: str
    engineering_class: str
    classification_status: str
    classification_reason: str
    taxonomy_revision: str
    mapping_revision: str
    classification_policy_revision: str
    detector_confidence_summary_json: str
    detector_confidence_summary: dict[str, Any] = Field(default_factory=dict)
    track_evidence_ref: str | None = None
    processing_provenance_json: str
    processing_provenance: dict[str, Any] = Field(default_factory=dict)
    qc_state: str
    stale: bool | int
    created_at: str


class TrackEvidenceOut(BaseModel):
    id: str
    engineering_result_revision_id: str
    run_id: str
    track_id: str
    evidence_schema_version: str
    evidence: dict[str, Any]
    provenance: dict[str, Any]
    stale: bool | int
    created_at: str


class EngineeringLineTotalOut(BaseModel):
    line_id: str
    line_name: str
    total: int
    a_to_b: int
    b_to_a: int


class EngineeringLineClassTotalOut(BaseModel):
    line_id: str
    line_name: str
    engineering_class: str
    total: int


class EngineeringDirectionClassOut(BaseModel):
    line_id: str
    line_name: str
    direction: str
    engineering_class: str
    total: int


class EngineeringDirectionTotalOut(BaseModel):
    line_id: str
    line_name: str
    direction: str
    total: int


class EngineeringIntervalOut(BaseModel):
    id: str | None = None
    engineering_result_revision_id: str | None = None
    run_id: str | None = None
    interval_index: int
    source_relative_start_pts_ms: int
    source_relative_end_pts_ms: int
    absolute_start: str | None = None
    absolute_end: str | None = None
    timezone_name: str | None = None
    display_label: str
    time_status: str
    partial: bool | int


class EngineeringIntervalRowOut(BaseModel):
    interval_index: int
    line_id: str
    line_name: str
    direction: str
    engineering_class: str
    count: int


class ReconciliationInvariantOut(BaseModel):
    identifier: str
    expected: int
    actual: int
    difference: int
    passed: bool


class InvalidDirectionExclusionOut(BaseModel):
    event_id: str | None = None
    technical_key: str | None = None
    original_direction: Any = None
    expected_domain: list[str]
    expected_count: int
    resulting_count: int
    resulting_count_difference: int
    reason: str


class ClassificationExclusionOut(BaseModel):
    event_id: str | None = None
    technical_key: str | None = None
    classification_status: str | None = None
    engineering_class: str | None = None
    allowed_classes: list[str]
    resulting_count_difference: int
    reason: str


class ReconciliationReportOut(BaseModel):
    status: str
    engineering_ready: bool
    invariants: list[ReconciliationInvariantOut]
    invalid_direction_exclusions: list[InvalidDirectionExclusionOut]
    classification_exclusions: list[ClassificationExclusionOut]
    result_revision: str | None = None


class EngineeringDisclosuresOut(BaseModel):
    run_id: str
    disclosures: list[str]
    source_time_status: str
    engineering_ready: bool
    stale: bool | int


class EngineeringSummaryOut(BaseModel):
    id: str
    run_id: str
    result_version: str
    result_status: str
    engineering_ready: bool
    stale: bool | int
    taxonomy_revision: str
    mapping_revision: str
    classification_policy_revision: str
    source_time_status: str
    source_timezone_name: str | None = None
    disclosures: list[str]
    line_totals: list[EngineeringLineTotalOut]
    direction_totals: list[EngineeringDirectionTotalOut]
    line_class_totals: list[EngineeringLineClassTotalOut]
    direction_class_matrix: list[EngineeringDirectionClassOut]
    intervals: list[EngineeringIntervalOut]
    interval_rows: list[EngineeringIntervalRowOut]
    overall_event_total: int
    events_count: int
    reconciliation: ReconciliationReportOut
    provenance: dict[str, Any]


class BenchmarkValidationOut(BaseModel):
    valid: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)
    content_hash: str | None = None
    manifest: dict[str, Any] | None = None
    ground_truth: dict[str, Any] | None = None
    dry_run: bool | None = None


class BenchmarkCorpusImportRequest(BaseModel):
    manifest: dict[str, Any]
    dry_run: bool = False


class BenchmarkGroundTruthImportRequest(BaseModel):
    manifest: dict[str, Any]
    dry_run: bool = False


class BenchmarkQualificationThreshold(BaseModel):
    metric_path: str = Field(min_length=1)
    operator: Literal["GTE", "LTE", "GT", "LT", "EQ"]
    required_value: StrictInt | StrictFloat
    unit: str = Field(min_length=1)
    required: StrictBool = True
    undefined_behavior: Literal["FAIL_CLOSED", "ALLOW_UNDEFINED"] = "FAIL_CLOSED"


class BenchmarkQualificationPolicy(BaseModel):
    schema_version: Literal["qualification-threshold-v1"] = "qualification-threshold-v1"
    policy_revision: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    approved_at: str = Field(min_length=1)
    applicable_corpus_revision: str = Field(min_length=1)
    required_split: Literal["CALIBRATION", "HOLDOUT", "DIAGNOSTIC_ONLY"]
    minimum_source_count: StrictInt = Field(ge=1)
    minimum_ground_truth_event_count: StrictInt = Field(ge=1)
    minimum_condition_coverage: list[str] = Field(default_factory=list)
    thresholds: list[BenchmarkQualificationThreshold] = Field(min_length=1)


class BenchmarkEvaluationRequest(BaseModel):
    automatic_run_id: str
    benchmark_source_id: str
    ground_truth_revision: str | None = None
    evaluation_configuration: dict[str, Any] = Field(default_factory=dict)
    code_commit_sha: str | None = None
    approved_policy: BenchmarkQualificationPolicy | None = None
    created_by: str = "benchmark-api"


class BenchmarkSourceOut(BaseModel):
    id: str
    benchmark_source_id: str
    corpus_revision_id: str
    source_fingerprint_sha256: str
    file_name_or_external_reference: str
    media_duration_ms: int
    source_width: int
    source_height: int
    nominal_fps_if_known: float | None = None
    recording_start_status: str
    timezone_name: str | None = None
    rights_status: str
    rights_basis: str
    permission_reference: str | None = None
    redistribution_status: str
    storage_status: str
    checksum_verified: bool
    scene_revision: str
    annotation_revision: str | None = None
    benchmark_split: str
    condition_tags: list[str] = Field(default_factory=list)
    notes: dict[str, Any] = Field(default_factory=dict)
    rights_qualifying: bool


class BenchmarkCorpusRevisionOut(BaseModel):
    id: str
    revision: str
    content_hash: str
    manifest: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: str
    created_by: str
    source_count: int = 0
    sources: list[BenchmarkSourceOut] = Field(default_factory=list)


class BenchmarkRunHeaderOut(BaseModel):
    id: str
    benchmark_source_id: str
    benchmark_split: str
    rights_status: str
    ground_truth_revision: str
    evaluation_configuration_revision: str
    status: str
    qualification_status: str
    source_fingerprint_sha256: str
    scene_revision: str
    code_commit_sha: str
    configuration_hash: str
    runtime_configuration_hash: str | None = None
    provenance_status: str = "LEGACY_UNRESOLVED"
    started_at: str
    completed_at: str | None = None
    environment: dict[str, Any] = Field(default_factory=dict)
    run_configuration: dict[str, Any] = Field(default_factory=dict)


class BenchmarkOverviewOut(BaseModel):
    status: str
    corpus_revisions: list[BenchmarkCorpusRevisionOut] = Field(default_factory=list)
    source_count: int
    rights_coverage: dict[str, int] = Field(default_factory=dict)
    split_counts: dict[str, int] = Field(default_factory=dict)
    latest_run: BenchmarkRunHeaderOut | None = None
    benchmark_runs: list[BenchmarkRunHeaderOut] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class GroundTruthRevisionOut(BaseModel):
    id: str
    benchmark_source_id: str
    revision: str
    content_hash: str
    status: str
    annotation_schema_version: str
    reviewer_agreement: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    reviewer_annotations: list[dict[str, Any]] = Field(default_factory=list)
    source_fingerprint_sha256: str
    scene_revision: str
    benchmark_split: str


class BenchmarkRunOut(BenchmarkRunHeaderOut):
    report: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    match_result: dict[str, Any] = Field(default_factory=dict)
    qualification: dict[str, Any] = Field(default_factory=dict)


class BenchmarkMatchPageOut(BaseModel):
    benchmark_run_id: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    limit: int
    offset: int
    category: str | None = None


class BenchmarkMetricsOut(BaseModel):
    benchmark_run_id: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    qualification: dict[str, Any] = Field(default_factory=dict)


class BenchmarkQualificationOut(BaseModel):
    benchmark_run_id: str
    status: str
    gates: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    threshold_evaluation: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)
    report_markdown: str


class BenchmarkExperimentComparisonOut(BaseModel):
    suite_id: str
    suite_revision: str
    corpus_revision: str
    calibration_split: str
    holdout_split: str
    baseline_configuration_id: str | None = None
    status: str
    candidate_configurations: list[dict[str, Any]] = Field(default_factory=list)
    runs: list[dict[str, Any]] = Field(default_factory=list)
    pareto: dict[str, Any] = Field(default_factory=dict)
    qualification: dict[str, Any] = Field(default_factory=dict)
