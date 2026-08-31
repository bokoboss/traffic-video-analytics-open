from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone

from fastapi import FastAPI, File, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .diagnostics import install_diagnostics
from .benchmark_service import BenchmarkService
from .benchmarking import BenchmarkValidationError
from .media import MediaRuntimeError
from .scene_geometry import SceneValidationError
from .db import connect, migrate
from .release import release_identity
from .runtime_readiness import build_components, component
from .worker_heartbeat import DEFAULT_HEARTBEAT_TTL_SECONDS
from .schemas import (
    ProjectCreate,
    ProjectUpdate,
    CurrentProcessingRunSelection,
    ProcessingJobCancel,
    ProcessingJobCreate,
    ReferenceFrameRequest,
    ReadinessResponse,
    ClassificationPolicyOut,
    BenchmarkCorpusImportRequest,
    BenchmarkCorpusRevisionOut,
    BenchmarkEvaluationRequest,
    BenchmarkExperimentComparisonOut,
    BenchmarkGroundTruthImportRequest,
    BenchmarkMatchPageOut,
    BenchmarkMetricsOut,
    BenchmarkOverviewOut,
    BenchmarkQualificationOut,
    BenchmarkRunHeaderOut,
    BenchmarkRunOut,
    BenchmarkValidationOut,
    CapabilityValidationCreate,
    CapabilityValidationOut,
    ConfigurationComparisonCreate,
    ConfigurationComparisonOut,
    ConfigurationDiffCreate,
    ConfigurationDiffOut,
    ConfigurationValidationOut,
    ConflictEnvelopeOut,
    OperationalCandidateSelectionCreate,
    OperationalCandidateSelectionOut,
    PreviewRunCreate,
    PreviewRunOut,
    ProcessingConfigurationResolveRequest,
    ProcessingConfigurationRevisionCreate,
    ProcessingConfigurationRevisionOut,
    ProcessingParameterSchemaOut,
    ProcessingProfileOut,
    GroundTruthRevisionOut,
    EngineeringDirectionClassOut,
    EngineeringDirectionTotalOut,
    EngineeringDisclosuresOut,
    EngineeringEventOut,
    EngineeringIntervalOut,
    EngineeringIntervalRowOut,
    EngineeringLineTotalOut,
    EngineeringSummaryOut,
    ReconciliationReportOut,
    ReviewActionCreate,
    ReviewActionAppendRequest,
    ReviewActionAppendOut,
    ReviewCompletionRequest,
    ReviewSessionCreate,
    ReviewSessionOut,
    CertificationCreateRequest,
    CertificationRevocationRequest,
    CertificationRevisionOut,
    ExportRequest,
    ExportRevisionOut,
    SceneCreate,
    SceneGeometrySave,
    SourceCreate,
    SyntheticFixtureRequest,
    TaxonomyRevisionOut,
    TrackEvidenceOut,
    SyntheticValidationRequest,
    TimeConfigurationUpdate,
)
from .operational_profiles import PreviewConflict
from .services import FoundationService, MAX_UPLOAD_BYTES, StaleWriteError
from .diagnostics import diagnostics_enabled
from .review_service import ReviewConflictError, ReviewDomainError, ReviewStaleError


MODULE_IMPORT_COMPLETED = time.perf_counter()


class StartupPhases:
    def __init__(self) -> None:
        self._origin = time.perf_counter()
        self._last = self._origin
        self._phases: dict[str, float] = {}

    def mark(self, name: str) -> None:
        current = time.perf_counter()
        self._phases[name] = round((current - self._last) * 1000, 3)
        self._last = current

    def snapshot(self) -> dict[str, float]:
        return dict(self._phases)


def create_app(database_path: str | None = None) -> FastAPI:
    phases = StartupPhases()
    phases._phases["module_import_to_factory_ms"] = round((time.perf_counter() - MODULE_IMPORT_COMPLETED) * 1000, 3)
    db_path = database_path or os.getenv("TVA_DB_PATH", ":memory:")
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    phases.mark("settings_and_storage_path")
    connection = connect(db_path)
    phases.mark("database_connect")
    migrate(connection)
    phases.mark("database_migrate")
    service = FoundationService(connection)
    benchmark_service = BenchmarkService(connection)
    release = release_identity()
    phases.mark("service_construct")

    app = FastAPI(
        title="Traffic Video Analytics API",
        version=str(release["release_version"]),
        description="Local-first Traffic Video Analytics API with synthetic validation and optional real-video worker processing.",
    )
    phases.mark("app_factory")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    phases.mark("middleware_registration")
    install_diagnostics(app)
    app.state.foundation_service = service
    app.state.benchmark_service = benchmark_service
    app.state.release_identity = release
    app.state.startup_phases_ms = phases.snapshot() if diagnostics_enabled() else None

    @app.get("/api/v1/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "application": release["application_name"],
            "release": release["release_version"],
            "version": release["release_version"],
            "git_sha": release["git_commit_sha"],
            "database": "sqlite",
            "worker_boundary": "separate_process",
            "tims_taxonomy": "pending_official_pdf_verification",
            "license": "provisional",
        }

    @app.get("/api/v1/media-runtime")
    def get_media_runtime() -> dict:
        return service.media_runtime_status()

    @app.get("/api/v1/release")
    def get_release_identity() -> dict[str, object]:
        return dict(release)

    @app.get("/api/v1/taxonomy", response_model=TaxonomyRevisionOut)
    def get_taxonomy() -> dict:
        with service.lock:
            return service.get_taxonomy()

    @app.get("/api/v1/taxonomy/{revision}", response_model=TaxonomyRevisionOut)
    def get_taxonomy_revision(revision: str) -> dict:
        try:
            with service.lock:
                return service.get_taxonomy(revision)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/classification-policy", response_model=ClassificationPolicyOut)
    def get_classification_policy() -> dict:
        with service.lock:
            return service.get_classification_policy()

    @app.get("/api/v1/benchmarks/overview", response_model=BenchmarkOverviewOut)
    def get_benchmark_overview() -> dict:
        with benchmark_service.lock:
            return benchmark_service.overview()

    @app.get("/api/v1/benchmarks/experiments", response_model=list[BenchmarkExperimentComparisonOut])
    def list_benchmark_experiments(limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> list[dict]:
        with benchmark_service.lock:
            return benchmark_service.list_experiment_comparisons(limit=limit, offset=offset)

    @app.get("/api/v1/benchmarks/corpora", response_model=list[BenchmarkCorpusRevisionOut])
    def list_benchmark_corpora() -> list[dict]:
        with benchmark_service.lock:
            return benchmark_service.list_corpora()

    @app.get("/api/v1/benchmarks/corpora/{revision}", response_model=BenchmarkCorpusRevisionOut)
    def get_benchmark_corpus(revision: str) -> dict:
        try:
            with benchmark_service.lock:
                return benchmark_service.get_corpus(revision)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/benchmarks/corpora/validate", response_model=BenchmarkValidationOut)
    def validate_benchmark_corpus(payload: BenchmarkCorpusImportRequest) -> dict:
        return benchmark_service.validate_manifest(payload.manifest)

    @app.post("/api/v1/benchmarks/corpora/import", response_model=BenchmarkCorpusRevisionOut | BenchmarkValidationOut)
    def import_benchmark_corpus(payload: BenchmarkCorpusImportRequest) -> dict:
        try:
            with benchmark_service.lock:
                return benchmark_service.import_manifest(payload.manifest, dry_run=payload.dry_run)
        except BenchmarkValidationError as exc:
            raise HTTPException(status_code=422, detail={"message": str(exc), "errors": list(exc.errors)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/benchmarks/ground-truth/validate", response_model=BenchmarkValidationOut)
    def validate_benchmark_ground_truth(payload: BenchmarkGroundTruthImportRequest) -> dict:
        with benchmark_service.lock:
            return benchmark_service.validate_ground_truth(payload.manifest)

    @app.post("/api/v1/benchmarks/ground-truth/import", response_model=GroundTruthRevisionOut | BenchmarkValidationOut)
    def import_benchmark_ground_truth(payload: BenchmarkGroundTruthImportRequest) -> dict:
        try:
            with benchmark_service.lock:
                return benchmark_service.import_ground_truth(payload.manifest, dry_run=payload.dry_run)
        except BenchmarkValidationError as exc:
            raise HTTPException(status_code=422, detail={"message": str(exc), "errors": list(exc.errors)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/benchmarks/ground-truth/{benchmark_source_id}", response_model=GroundTruthRevisionOut)
    def get_benchmark_ground_truth(benchmark_source_id: str, revision: str | None = Query(default=None)) -> dict:
        try:
            with benchmark_service.lock:
                return benchmark_service.get_ground_truth(benchmark_source_id, revision)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/benchmarks/runs/evaluate", response_model=BenchmarkRunOut)
    def evaluate_benchmark(payload: BenchmarkEvaluationRequest) -> dict:
        try:
            with benchmark_service.lock:
                return benchmark_service.evaluate(
                    automatic_run_id=payload.automatic_run_id,
                    benchmark_source_id=payload.benchmark_source_id,
                    ground_truth_revision=payload.ground_truth_revision,
                    evaluation_configuration=payload.evaluation_configuration,
                    code_commit_sha=payload.code_commit_sha,
                    approved_policy=payload.approved_policy.model_dump() if payload.approved_policy is not None else None,
                    created_by=payload.created_by,
                )
        except BenchmarkValidationError as exc:
            raise HTTPException(status_code=422, detail={"message": str(exc), "errors": list(exc.errors)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/benchmarks/runs", response_model=list[BenchmarkRunHeaderOut])
    def list_benchmark_runs(limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> list[dict]:
        with benchmark_service.lock:
            return benchmark_service.list_runs(limit=limit, offset=offset)

    @app.get("/api/v1/benchmarks/runs/{benchmark_run_id}", response_model=BenchmarkRunOut)
    def get_benchmark_run(benchmark_run_id: str) -> dict:
        try:
            with benchmark_service.lock:
                return benchmark_service.get_run(benchmark_run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/benchmarks/runs/{benchmark_run_id}/matches", response_model=BenchmarkMatchPageOut)
    def list_benchmark_matches(
        benchmark_run_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        category: str | None = Query(default=None),
    ) -> dict:
        try:
            with benchmark_service.lock:
                return benchmark_service.list_matches(benchmark_run_id, limit=limit, offset=offset, category=category)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/benchmarks/runs/{benchmark_run_id}/metrics", response_model=BenchmarkMetricsOut)
    def get_benchmark_metrics(benchmark_run_id: str) -> dict:
        try:
            with benchmark_service.lock:
                return benchmark_service.metrics(benchmark_run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/benchmarks/runs/{benchmark_run_id}/qualification", response_model=BenchmarkQualificationOut)
    def get_benchmark_qualification(benchmark_run_id: str) -> dict:
        try:
            with benchmark_service.lock:
                return benchmark_service.qualification(benchmark_run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/readiness", response_model=ReadinessResponse)
    def readiness() -> ReadinessResponse:
        media_runtime = service.media_runtime_status()
        worker_readiness = service.processing_readiness()
        try:
            heartbeat_ttl_seconds = max(
                0.1,
                float(os.getenv("TVA_WORKER_HEARTBEAT_TTL_SECONDS", str(DEFAULT_HEARTBEAT_TTL_SECONDS))),
            )
        except ValueError:
            heartbeat_ttl_seconds = DEFAULT_HEARTBEAT_TTL_SECONDS
        components = build_components(
            database=connection,
            media_runtime=media_runtime,
            processing=worker_readiness,
            worker_heartbeat_ttl_seconds=heartbeat_ttl_seconds,
        )
        core_components = [
            item["ready"]
            for item in components.values()
            if item["required"] and item["component"] in {"backend", "database", "migrations", "artifact_storage", "disk_space"}
        ]
        core_ready = all(core_components)
        core_has_warning = any(
            item["status"] == "READY_WITH_WARNINGS"
            for item in components.values()
            if item["required"] and item["component"] in {"backend", "database", "migrations", "artifact_storage", "disk_space"}
        )
        core_status = "READY_WITH_WARNINGS" if core_ready and core_has_warning else ("READY" if core_ready else "BLOCKED")
        worker_ready = bool(components["worker"]["ready"])
        synthetic_ready = bool(components["synthetic_capability"]["ready"])
        real_processing_ready = bool(components["real_inference"]["ready"])
        frontend_ready = bool(components["frontend"]["ready"])
        processing_status = "READY" if real_processing_ready else ("READY_WITH_WARNINGS" if synthetic_ready else "BLOCKED")
        application_ready = core_ready and frontend_ready and worker_ready
        application_status = (
            "READY"
            if application_ready and real_processing_ready
            else "READY_WITH_WARNINGS"
            if application_ready and synthetic_ready
            else "BLOCKED"
        )
        status = "core_ready" if core_ready and application_ready else ("degraded" if core_ready else "not_ready")
        processing_real_inference = {
            **worker_readiness["real_inference"],
            "ready": real_processing_ready,
            "state": components["real_inference"]["state"],
            "detail": components["real_inference"]["detail"],
        }
        return ReadinessResponse(
            status=status,
            liveness={
                "backend": component("backend", status="READY", required=True, ready=True, version=str(release["release_version"]), state="alive", detail="FastAPI process is responding."),
                "database": component("database", status="READY", required=True, ready=True, version="sqlite", state="available", detail="SQLite connection is available."),
                "frontend": components["frontend"],
                "worker": components["worker"],
            },
            core={
                "api": component("api", status="READY", required=True, ready=True, state="ready", detail="Core API contract is available."),
                "project_storage": component("project_storage", status="READY", required=True, ready=True, state="ready", detail="Project storage is available."),
                "scene_configuration": component("scene_configuration", status="READY", required=True, ready=True, state="ready", detail="Scene configuration contract is available."),
            },
            processing={
                "worker": {
                    "status": components["worker"]["status"],
                    "state": components["worker"]["state"],
                    "ready": worker_ready,
                    "detail": components["worker"]["detail"],
                    "active_jobs": worker_readiness["active_jobs"],
                },
                "ffmpeg": {
                    "state": media_runtime["ffmpeg"]["error_code"] or ("available" if media_runtime["ffmpeg"]["available"] else "unavailable"),
                    "ready": bool(media_runtime["ffmpeg"]["available"]),
                    "detail": media_runtime["ffmpeg"].get("path_source"),
                },
                "ffprobe": {
                    "state": media_runtime["ffprobe"]["error_code"] or ("available" if media_runtime["ffprobe"]["available"] else "unavailable"),
                    "ready": bool(media_runtime["ffprobe"]["available"]),
                    "detail": media_runtime["ffprobe"].get("path_source"),
                },
                "model_runtime": {
                    **worker_readiness["model_runtime"],
                },
                "model_weights": {
                    **worker_readiness["model_weights"],
                },
                "detector": worker_readiness["detector"],
                "tracker": worker_readiness["tracker"],
                "device": worker_readiness["device"],
                "real_inference": processing_real_inference,
                "synthetic_capability": components["synthetic_capability"],
                "media_runtime": media_runtime,
                "ready": real_processing_ready,
                "synthetic_ready": synthetic_ready,
                "status": processing_status,
                "application": {
                    "ready": application_ready,
                    "status": application_status,
                },
            },
            generated_at=datetime.now(timezone.utc),
            startup_phases_ms=app.state.startup_phases_ms,
            core_status=core_status,
            processing_status=processing_status,
            application_status=application_status,
            application_ready=application_ready,
            overall_status=application_status,
            components=components,
            release=release,
        )

    @app.post("/api/v1/projects")
    def create_project(payload: ProjectCreate) -> dict:
        with service.lock:
            return service.create_project(payload.name, payload.location, payload.study_type, payload.language)

    @app.get("/api/v1/projects")
    def list_projects() -> list[dict]:
        with service.lock:
            return service.list_projects()

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        try:
            with service.lock:
                return service.get_project_snapshot(project_id)
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail == "project not found" else 422
            raise HTTPException(status_code=status_code, detail=detail) from exc

    @app.post("/api/v1/projects/{project_id}/processing-runs/{run_id}/select")
    def select_current_processing_run(project_id: str, run_id: str, payload: CurrentProcessingRunSelection) -> dict:
        try:
            with service.lock:
                return service.select_current_processing_run(project_id, run_id, payload.selected_by)
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail in {"project not found", "processing run not found"} else 409
            raise HTTPException(status_code=status_code, detail=detail) from exc

    @app.put("/api/v1/projects/{project_id}")
    def update_project(project_id: str, payload: ProjectUpdate) -> dict:
        try:
            with service.lock:
                return service.update_project(project_id, payload.model_dump())
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail == "project not found" else 422
            raise HTTPException(status_code=status_code, detail=detail) from exc

    @app.post("/api/v1/sources")
    def register_source(payload: SourceCreate) -> dict:
        with service.lock:
            return service.register_source(payload)

    @app.post("/api/v1/projects/{project_id}/sources/upload")
    async def upload_source(project_id: str, file: UploadFile = File(...)) -> dict:
        local_data_dir = Path(os.getenv("TVA_LOCAL_DATA_DIR", ".local-data")).resolve()
        local_data_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(file.filename or "source.bin").suffix
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_path = Path(temp_file.name)
                total_bytes = 0
                while chunk := await file.read(1024 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="oversized_file")
                    temp_file.write(chunk)
            with service.lock:
                return service.register_uploaded_source(
                    project_id,
                    temp_path,
                    file.filename or "source.bin",
                    local_data_dir,
                )
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail == "project not found" else 422
            raise HTTPException(status_code=status_code, detail=detail) from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @app.post("/api/v1/projects/{project_id}/reference-frames")
    def extract_reference_frame(project_id: str, payload: ReferenceFrameRequest) -> dict:
        local_data_dir = Path(os.getenv("TVA_LOCAL_DATA_DIR", ".local-data")).resolve()
        try:
            with service.lock:
                return service.extract_project_reference_frame(
                    project_id,
                    payload.mode,
                    payload.requested_pts_ms,
                    local_data_dir,
                    payload.force_regenerate,
                )
        except MediaRuntimeError as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/reference-frames/{frame_id}/preview")
    def get_reference_frame_preview(project_id: str, frame_id: str) -> FileResponse:
        try:
            with service.lock:
                path = service.reference_frame_preview_path(project_id, frame_id)
            return FileResponse(
                path,
                media_type="image/png",
                headers={
                    "Cache-Control": "private, max-age=31536000, immutable",
                    "ETag": f'"{frame_id}"',
                },
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="reference frame preview missing") from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/source-video")
    def get_project_source_video(project_id: str, range_header: str | None = Header(default=None, alias="Range")) -> Response:
        try:
            with service.lock:
                path, media_type, size = service.source_video_file(project_id)
            headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=0, must-revalidate"}
            if range_header:
                start, end = _parse_range_header(range_header, size)
                chunk = _read_file_range(path, start, end)
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
                headers["Content-Length"] = str(len(chunk))
                return Response(content=chunk, status_code=206, media_type=media_type, headers=headers)
            headers["Content-Length"] = str(size)
            return FileResponse(path, media_type=media_type, headers=headers)
        except MediaRuntimeError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from exc
        except ValueError as exc:
            raise HTTPException(status_code=416, detail=str(exc)) from exc

    @app.put("/api/v1/projects/{project_id}/time-configuration")
    def update_time_configuration(project_id: str, payload: TimeConfigurationUpdate) -> dict:
        try:
            with service.lock:
                return service.update_time_configuration(project_id, payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/scenes")
    def create_scene(payload: SceneCreate) -> dict:
        with service.lock:
            return service.create_scene(payload.project_id, payload.template)

    @app.get("/api/v1/projects/{project_id}/scene-configuration")
    def get_scene_configuration(project_id: str) -> dict:
        try:
            with service.lock:
                return service.get_project_snapshot(project_id)["scene"] or {}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/processing-profiles", response_model=list[ProcessingProfileOut])
    def list_processing_profiles() -> list[dict]:
        with service.lock:
            return service.list_processing_profiles()

    @app.get("/api/v1/processing-profiles/{profile_code}", response_model=ProcessingProfileOut)
    def get_processing_profile(profile_code: str, profile_revision: str | None = Query(default=None)) -> dict:
        try:
            with service.lock:
                return service.get_processing_profile(profile_code, profile_revision)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/processing-parameter-schema", response_model=ProcessingParameterSchemaOut)
    def get_processing_parameter_schema() -> dict:
        with service.lock:
            return service.get_processing_parameter_schema()

    @app.post(
        "/api/v1/projects/{project_id}/processing-configurations/resolve",
        response_model=ConfigurationValidationOut,
    )
    def resolve_processing_configuration(
        project_id: str,
        payload: ProcessingConfigurationResolveRequest,
    ) -> dict:
        try:
            with service.lock:
                return service.resolve_processing_configuration(project_id, payload.model_dump(exclude_unset=True))
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail in {"project not found", "source is required", "scene is required"} else 422
            raise HTTPException(status_code=status_code, detail=detail) from exc

    @app.post(
        "/api/v1/projects/{project_id}/processing-configurations",
        response_model=ProcessingConfigurationRevisionOut,
    )
    def create_processing_configuration_revision(
        project_id: str,
        payload: ProcessingConfigurationRevisionCreate,
    ) -> dict:
        try:
            with service.lock:
                return service.create_processing_configuration_revision(project_id, payload.model_dump(exclude_unset=True))
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail in {"project not found", "source is required", "scene is required"} else 422
            raise HTTPException(status_code=status_code, detail=detail) from exc

    @app.get(
        "/api/v1/processing-configurations/{revision_id}",
        response_model=ProcessingConfigurationRevisionOut,
    )
    def get_processing_configuration_revision(revision_id: str) -> dict:
        try:
            with service.lock:
                return service.get_processing_configuration_revision(revision_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/projects/{project_id}/previews",
        response_model=PreviewRunOut,
        responses={409: {"model": ConflictEnvelopeOut}},
    )
    def create_preview_run(project_id: str, payload: PreviewRunCreate) -> dict:
        try:
            with service.lock:
                return service.create_preview_run(project_id, payload.model_dump())
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PreviewConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "detail": exc.detail, "resource_id": exc.preview_id},
            ) from exc
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail in {"project not found", "source is required", "scene is required"} else 422
            raise HTTPException(status_code=status_code, detail=detail) from exc

    @app.get("/api/v1/projects/{project_id}/previews", response_model=list[PreviewRunOut])
    def list_preview_runs(project_id: str, limit: int = Query(default=25, ge=1, le=100)) -> list[dict]:
        try:
            with service.lock:
                return service.list_preview_runs(project_id, limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/previews/{preview_id}", response_model=PreviewRunOut)
    def get_preview_run(preview_id: str) -> dict:
        try:
            with service.lock:
                return service.get_preview_run(preview_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/previews/{preview_id}/promote", response_model=dict, responses={409: {"model": ConflictEnvelopeOut}})
    def promote_preview_run(preview_id: str) -> dict:
        try:
            with service.lock:
                return service.promote_preview_run(preview_id)
        except PreviewConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "detail": exc.detail, "resource_id": exc.preview_id},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/previews/{preview_id}/cancel",
        response_model=PreviewRunOut,
        responses={409: {"model": ConflictEnvelopeOut}},
    )
    def cancel_preview_run(preview_id: str, payload: ProcessingJobCancel | None = None) -> dict:
        try:
            with service.lock:
                return service.cancel_preview_run(preview_id, (payload.reason if payload else None))
        except PreviewConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "detail": exc.detail, "resource_id": exc.preview_id},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/projects/{project_id}/processing-configurations/diff",
        response_model=ConfigurationDiffOut,
    )
    def diff_processing_configurations(project_id: str, payload: ConfigurationDiffCreate) -> dict:
        try:
            with service.lock:
                return service.diff_processing_configurations(project_id, payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/v1/projects/{project_id}/configuration-comparisons",
        response_model=ConfigurationComparisonOut,
    )
    def compare_processing_configurations(project_id: str, payload: ConfigurationComparisonCreate) -> dict:
        try:
            with service.lock:
                return service.compare_processing_configurations(project_id, payload.model_dump())
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/v1/projects/{project_id}/operational-candidates",
        response_model=OperationalCandidateSelectionOut,
    )
    def select_operational_candidate(
        project_id: str,
        payload: OperationalCandidateSelectionCreate,
    ) -> dict:
        try:
            with service.lock:
                return service.select_operational_candidate(project_id, payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/v1/projects/{project_id}/capability-validations",
        response_model=CapabilityValidationOut,
    )
    def create_capability_validation(project_id: str, payload: CapabilityValidationCreate) -> dict:
        try:
            with service.lock:
                return service.create_capability_validation(project_id, payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/v1/projects/{project_id}/capability-validations",
        response_model=list[CapabilityValidationOut],
    )
    def list_capability_validations(project_id: str, limit: int = Query(default=25, ge=1, le=100)) -> list[dict]:
        try:
            with service.lock:
                return service.list_capability_validations(project_id, limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/projects/{project_id}/scene-configuration/validate")
    def validate_scene_configuration(project_id: str, payload: SceneGeometrySave) -> dict:
        try:
            with service.lock:
                service._project_or_raise(project_id)
                return service.validate_scene_payload(payload)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/v1/projects/{project_id}/scene-configuration")
    def save_scene_configuration(project_id: str, payload: SceneGeometrySave) -> dict:
        try:
            with service.lock:
                return service.save_scene_configuration(project_id, payload)
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SceneValidationError as exc:
            raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/projects/{project_id}/mock-analysis")
    def run_mock_analysis(project_id: str) -> dict:
        try:
            with service.lock:
                return service.run_mock_analysis(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/projects/{project_id}/synthetic-input/validate")
    def validate_synthetic_input(project_id: str, payload: SyntheticValidationRequest) -> dict:
        try:
            with service.lock:
                return service.validate_synthetic_input(
                    project_id,
                    payload.synthetic_input,
                    payload.expected_scene_version,
                )
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/projects/{project_id}/synthetic-runs")
    def run_synthetic_fixture(project_id: str, payload: SyntheticFixtureRequest) -> dict:
        try:
            with service.lock:
                if payload.idempotency_key or payload.run_again is False:
                    return service.submit_processing_job(
                        project_id,
                        fixture_id=payload.fixture_id,
                        expected_scene_version=payload.expected_scene_version,
                        idempotency_key=payload.idempotency_key,
                        run_again=payload.run_again,
                        auto_start=True,
                    )
                return service.run_synthetic_fixture(project_id, payload.fixture_id, payload.expected_scene_version)
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/projects/{project_id}/processing-jobs")
    def submit_processing_job(project_id: str, payload: ProcessingJobCreate) -> dict:
        try:
            with service.lock:
                return service.submit_processing_job(
                    project_id,
                    fixture_id=payload.fixture_id,
                    mode=payload.mode,
                    configuration=payload.configuration,
                    expected_scene_version=payload.expected_scene_version,
                    idempotency_key=payload.idempotency_key,
                    run_again=payload.run_again,
                    auto_start=payload.auto_start,
                    retry_of_run_id=payload.retry_of_run_id,
                    processing_configuration_revision_id=payload.processing_configuration_revision_id,
                )
        except StaleWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/processing-jobs")
    def list_processing_jobs(project_id: str, limit: int = Query(default=25, ge=1, le=100)) -> list[dict]:
        with service.lock:
            return service.list_processing_jobs(project_id, limit=limit)

    @app.get("/api/v1/projects/{project_id}/processing-jobs/active")
    def get_active_processing_job(project_id: str) -> dict:
        with service.lock:
            jobs = service.list_processing_jobs(project_id, limit=25)
        for job in jobs:
            if job["job_state"] in {"CREATED", "QUEUED", "STARTING", "RUNNING", "CANCELLATION_REQUESTED"}:
                return job
        return {}

    @app.get("/api/v1/processing-jobs/{job_id}")
    def get_processing_job(job_id: str) -> dict:
        try:
            with service.lock:
                return service.get_processing_job(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/processing-jobs/{job_id}/progress")
    def get_processing_job_progress(job_id: str) -> dict:
        try:
            with service.lock:
                return service.get_processing_job(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/processing-jobs/{job_id}/cancel")
    def cancel_processing_job(job_id: str, payload: ProcessingJobCancel | None = None) -> dict:
        try:
            with service.lock:
                return service.cancel_processing_job(job_id, payload.reason if payload else None)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/processing-jobs/{job_id}/retry")
    def retry_processing_job(job_id: str) -> dict:
        try:
            with service.lock:
                return service.retry_processing_job(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/runs/{run_id}/segments/{segment_index}/retry")
    def retry_segment(run_id: str, segment_index: int) -> dict:
        with service.lock:
            return service.retry_segment(run_id, segment_index)

    @app.get("/api/v1/runs/{run_id}/events")
    def list_events(
        run_id: str,
        limit: int = Query(default=1000, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict]:
        with service.lock:
            return service.list_events(run_id, limit=limit, offset=offset)

    @app.get("/api/v1/runs/{run_id}/crossing-events")
    def list_crossing_events(
        run_id: str,
        limit: int = Query(default=1000, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict]:
        with service.lock:
            return service.list_crossing_events(run_id, limit=limit, offset=offset)

    @app.get("/api/v1/runs/{run_id}/aggregates")
    def get_aggregates(run_id: str) -> dict:
        try:
            with service.lock:
                return service.get_aggregate_snapshot(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/engineering-summary", response_model=EngineeringSummaryOut)
    def get_engineering_summary(run_id: str) -> dict:
        try:
            with service.lock:
                return service.get_engineering_summary(run_id)
        except ValueError as exc:
            detail = str(exc)
            raise HTTPException(status_code=404 if "not found" in detail else 409, detail=detail) from exc

    @app.get("/api/v1/runs/{run_id}/engineering-events", response_model=list[EngineeringEventOut])
    def list_engineering_events(
        run_id: str,
        limit: int = Query(default=1000, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict]:
        try:
            with service.lock:
                return service.list_engineering_events(run_id, limit=limit, offset=offset)
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/tracks/{track_id}/evidence", response_model=list[TrackEvidenceOut])
    def get_track_evidence(run_id: str, track_id: str) -> list[dict]:
        try:
            with service.lock:
                return service.get_track_evidence(run_id, track_id)
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/engineering/line-summary", response_model=list[EngineeringLineTotalOut])
    def get_engineering_line_summary(run_id: str) -> list[dict]:
        try:
            with service.lock:
                return service.get_engineering_summary(run_id)["line_totals"]
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/engineering/direction-summary", response_model=list[EngineeringDirectionTotalOut])
    def get_engineering_direction_summary(run_id: str) -> list[dict]:
        try:
            with service.lock:
                return service.get_engineering_summary(run_id)["direction_totals"]
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/engineering/class-direction-matrix", response_model=list[EngineeringDirectionClassOut])
    def get_engineering_class_direction_matrix(run_id: str) -> list[dict]:
        try:
            with service.lock:
                return service.get_engineering_summary(run_id)["direction_class_matrix"]
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/engineering/intervals", response_model=list[EngineeringIntervalOut])
    def get_engineering_intervals(run_id: str) -> list[dict]:
        try:
            with service.lock:
                return service.get_engineering_summary(run_id)["intervals"]
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/engineering/interval-rows", response_model=list[EngineeringIntervalRowOut])
    def get_engineering_interval_rows(run_id: str) -> list[dict]:
        try:
            with service.lock:
                return service.get_engineering_summary(run_id)["interval_rows"]
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/engineering/reconciliation", response_model=ReconciliationReportOut)
    def get_engineering_reconciliation(run_id: str) -> dict:
        try:
            with service.lock:
                return service.get_engineering_reconciliation(run_id)["report"]
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/engineering/disclosures", response_model=EngineeringDisclosuresOut)
    def get_engineering_disclosures(run_id: str) -> dict[str, object]:
        try:
            with service.lock:
                summary = service.get_engineering_summary(run_id)
                return {"run_id": run_id, "disclosures": summary["disclosures"], "source_time_status": summary["source_time_status"], "engineering_ready": summary["engineering_ready"], "stale": summary["stale"]}
        except ValueError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/reviewed-totals")
    def get_reviewed_totals(run_id: str) -> dict:
        with service.lock:
            return service.reviewed_totals(run_id)

    @app.post("/api/v1/review-actions")
    def add_review_action(payload: ReviewActionCreate) -> dict:
        with service.lock:
            return service.add_review_action(payload.event_id, payload.model_dump())

    @app.post("/api/v1/projects/{project_id}/review-complete")
    def mark_review_complete(project_id: str) -> dict:
        try:
            with service.lock:
                return service.mark_review_complete(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/projects/{project_id}/runs/{run_id}/certifications")
    def certify(project_id: str, run_id: str, certified_by: str = "mock-certifier") -> dict:
        try:
            with service.lock:
                return service.certify(project_id, run_id, certified_by)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/projects/{project_id}/runs/{run_id}/exports/{export_format}")
    def export_manifest(project_id: str, run_id: str, export_format: str) -> dict:
        if export_format not in {"xlsx", "csv"}:
            raise HTTPException(status_code=422, detail="format must be xlsx or csv")
        try:
            with service.lock:
                return service.create_export_manifest(project_id, run_id, export_format)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Milestone 6E review/certification/export API. The legacy routes above
    # remain available for foundation fixtures; these routes are the scoped,
    # versioned production contract.
    @app.post("/api/v1/projects/{project_id}/review-sessions", response_model=ReviewSessionOut)
    def create_review_session(project_id: str, payload: ReviewSessionCreate) -> dict:
        try:
            with service.lock:
                return service.review_service.create_session(project_id, payload.model_dump())
        except ReviewDomainError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail, "field": exc.field}) from exc
        except ReviewStaleError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail, "resource_id": exc.session_id}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/review-sessions", response_model=list[ReviewSessionOut])
    def list_review_sessions(project_id: str, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> list[dict]:
        with service.lock:
            return service.review_service.list_sessions(project_id, limit, offset)

    @app.get("/api/v1/review-sessions/{session_id}", response_model=ReviewSessionOut)
    def get_review_session(session_id: str) -> dict:
        try:
            with service.lock:
                return service.review_service.get_session(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/review-sessions/{session_id}/actions")
    def list_scoped_review_actions(session_id: str, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> list[dict]:
        try:
            with service.lock:
                return service.review_service.list_actions(session_id, limit, offset)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/review-sessions/{session_id}/actions", response_model=ReviewActionAppendOut, responses={409: {"model": ConflictEnvelopeOut}})
    def append_scoped_review_action(session_id: str, payload: ReviewActionAppendRequest) -> dict:
        try:
            with service.lock:
                return service.review_service.append_action(session_id, payload.model_dump(exclude_none=True))
        except ReviewConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": exc.code,
                    "detail": exc.detail,
                    "resource_id": exc.session_id,
                    "expected_review_revision": exc.expected_revision,
                    "current_review_revision": exc.current_revision,
                    "actions_since_expected": exc.actions_since,
                    "retry_guidance": "Refresh the review session and retry with the current review revision.",
                },
            ) from exc
        except ReviewStaleError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail, "resource_id": exc.session_id}) from exc
        except ReviewDomainError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail, "field": exc.field}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/review-sessions/{session_id}/actions/reverse", response_model=ReviewActionAppendOut, responses={409: {"model": ConflictEnvelopeOut}})
    def reverse_scoped_review_action(session_id: str, payload: ReviewActionAppendRequest) -> dict:
        if payload.action_type != "REVERSE_ACTION":
            raise HTTPException(status_code=422, detail={"code": "INVALID_ACTION_TYPE", "detail": "action_type must be REVERSE_ACTION"})
        return append_scoped_review_action(session_id, payload)

    @app.get("/api/v1/review-sessions/{session_id}/queue")
    def review_queue(
        session_id: str,
        review_status: str | None = None,
        line: str | None = None,
        direction: str | None = None,
        engineering_class: str | None = None,
        origin: str | None = None,
        classification_status: str | None = None,
        corrected: bool | None = None,
        duplicate: bool | None = None,
        automatic_or_human: str | None = None,
        start_pts_ms: int | None = Query(default=None, ge=0),
        end_pts_ms: int | None = Query(default=None, gt=0),
        confidence_min: float | None = Query(default=None, ge=0, le=1),
        confidence_max: float | None = Query(default=None, ge=0, le=1),
        benchmark_error_category: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        order_by: str = Query(default="timestamp"),
    ) -> dict:
        if (start_pts_ms is None) != (end_pts_ms is None):
            raise HTTPException(status_code=422, detail={"code": "INVALID_TIME_INTERVAL", "detail": "start_pts_ms and end_pts_ms must be supplied together."})
        filters = {key: value for key, value in {
            "review_status": review_status,
            "line": line,
            "direction": direction,
            "class": engineering_class,
            "origin": origin,
            "classification_status": classification_status,
            "corrected": corrected,
            "duplicate": duplicate,
            "automatic_or_human": automatic_or_human,
            "time_interval": (start_pts_ms, end_pts_ms) if start_pts_ms is not None and end_pts_ms is not None else None,
            "confidence_min": confidence_min,
            "confidence_max": confidence_max,
            "benchmark_error_category": benchmark_error_category,
        }.items() if value is not None}
        if start_pts_ms is not None and end_pts_ms is not None and end_pts_ms <= start_pts_ms:
            raise HTTPException(status_code=422, detail={"code": "INVALID_TIME_INTERVAL", "detail": "end_pts_ms must be greater than start_pts_ms."})
        try:
            with service.lock:
                return service.review_service.queue(session_id, limit=limit, offset=offset, filters=filters, order_by=order_by)
        except ReviewDomainError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail, "field": exc.field}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/review-sessions/{session_id}/events/{event_id}/evidence")
    def review_event_evidence(session_id: str, event_id: str) -> dict:
        try:
            with service.lock:
                return service.review_service.event_evidence(session_id, event_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/review-sessions/{session_id}/progress")
    def review_progress(session_id: str) -> dict:
        try:
            with service.lock:
                return service.review_service.get_progress(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/review-sessions/{session_id}/projections", response_model=dict)
    def build_reviewed_projection(session_id: str) -> dict:
        try:
            with service.lock:
                return service.review_service.build_projection(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/review-sessions/{session_id}/projections")
    def projection_history(session_id: str, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> list[dict]:
        try:
            with service.lock:
                return service.review_service.get_projection_history(session_id, limit, offset)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/review-sessions/{session_id}/reconciliation")
    def run_review_reconciliation(session_id: str) -> dict:
        try:
            with service.lock:
                return service.review_service.reconcile(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/review-sessions/{session_id}/complete", response_model=ReviewSessionOut, responses={409: {"model": ConflictEnvelopeOut}})
    def complete_review_session(session_id: str, payload: ReviewCompletionRequest) -> dict:
        try:
            with service.lock:
                return service.review_service.complete_review(session_id, completed_by=payload.completed_by, expected_review_revision=payload.expected_review_revision)
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail, "resource_id": exc.session_id, "expected_review_revision": exc.expected_revision, "current_review_revision": exc.current_revision, "actions_since_expected": exc.actions_since, "retry_guidance": "Refresh and retry completion."}) from exc
        except ReviewStaleError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail, "resource_id": exc.session_id}) from exc
        except ReviewDomainError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/review-sessions/{session_id}/supersede", response_model=ReviewSessionOut)
    def supersede_review_session(session_id: str, superseded_by: str, actor_id: str = "operator") -> dict:
        try:
            with service.lock:
                return service.review_service.supersede(session_id, superseded_by=superseded_by, actor_id=actor_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/review-sessions/{session_id}/certification", response_model=CertificationRevisionOut, responses={409: {"model": ConflictEnvelopeOut}})
    def create_review_certification(session_id: str, payload: CertificationCreateRequest) -> dict:
        try:
            with service.lock:
                return service.review_service.create_certification(session_id, payload.model_dump())
        except ReviewStaleError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail, "resource_id": exc.session_id}) from exc
        except ReviewDomainError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/certifications", response_model=list[CertificationRevisionOut])
    def list_review_certifications(project_id: str, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> list[dict]:
        with service.lock:
            return service.review_service.list_certifications(project_id, limit, offset)

    @app.get("/api/v1/certifications/{certification_id}", response_model=CertificationRevisionOut)
    def get_review_certification(certification_id: str) -> dict:
        try:
            with service.lock:
                return service.review_service.get_certification(certification_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/certifications/{certification_id}/revoke", response_model=CertificationRevisionOut)
    def revoke_review_certification(certification_id: str, payload: CertificationRevocationRequest) -> dict:
        try:
            with service.lock:
                return service.review_service.revoke_certification(certification_id, reason=payload.reason, revoked_by=payload.revoked_by)
        except ReviewDomainError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/certifications/{certification_id}/exports", response_model=ExportRevisionOut, responses={409: {"model": ConflictEnvelopeOut}})
    def request_certified_export(certification_id: str, payload: ExportRequest) -> dict:
        try:
            with service.lock:
                return service.review_service.request_export(certification_id, payload.model_dump())
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail, "resource_id": exc.session_id, "retry_guidance": "Use a new client request ID or replay the original request."}) from exc
        except ReviewStaleError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail, "resource_id": exc.session_id}) from exc
        except ReviewDomainError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail, "field": exc.field}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/certifications/{certification_id}/exports")
    def list_certified_exports(certification_id: str, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> list[dict]:
        try:
            with service.lock:
                return service.review_service.list_exports(certification_id, limit, offset)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/exports/{export_revision_id}", response_model=ExportRevisionOut)
    def get_certified_export(export_revision_id: str) -> dict:
        try:
            with service.lock:
                return service.review_service.get_export(export_revision_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/export-artifacts/{artifact_id}/download")
    def download_export_artifact(artifact_id: str) -> FileResponse:
        try:
            with service.lock:
                path, artifact = service.review_service.artifact_path(artifact_id)
            return FileResponse(path, media_type=str(artifact["mime_type"]), filename=str(artifact["safe_filename"]))
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc

    phases.mark("route_registration")
    if diagnostics_enabled():
        app.state.startup_phases_ms = phases.snapshot()
    return app


app = create_app()


def _parse_range_header(value: str, size: int) -> tuple[int, int]:
    if not value.startswith("bytes="):
        raise ValueError("invalid_range")
    raw_start, _, raw_end = value.removeprefix("bytes=").partition("-")
    if raw_start == "":
        length = int(raw_end)
        if length <= 0:
            raise ValueError("invalid_range")
        return max(size - length, 0), size - 1
    start = int(raw_start)
    end = int(raw_end) if raw_end else size - 1
    if start < 0 or end < start or start >= size:
        raise ValueError("invalid_range")
    return start, min(end, size - 1)


def _read_file_range(path: Path, start: int, end: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(start)
        return handle.read(end - start + 1)
