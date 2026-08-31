import type {
  ProjectCreate,
  ProjectUpdate,
  ReferenceFrameRequest,
  ReadinessComponent,
  ReadinessResponse as GeneratedReadinessResponse,
  TaxonomyRevisionOut,
  ClassificationPolicyOut,
  EngineeringEventOut,
  EngineeringSummaryOut,
  TrackEvidenceOut,
  ReviewActionCreate,
  SceneGeometrySave,
  SourceCreate,
  SyntheticFixtureRequest,
  SyntheticValidationRequest,
  TimeConfigurationUpdate,
  BenchmarkOverviewOut,
  BenchmarkMetricsOut,
  BenchmarkMatchPageOut,
  BenchmarkExperimentComparisonOut,
  CapabilityValidationCreate,
  CapabilityValidationOut,
  ConfigurationComparisonCreate,
  ConfigurationComparisonOut,
  ConfigurationDiffCreate,
  ConfigurationDiffOut,
  ConfigurationValidationOut,
  ExpertProcessingOverrides,
  GuidedProcessingSettings,
  OperationalCandidateSelectionCreate,
  OperationalCandidateSelectionOut,
  PreviewRunCreate,
  PreviewRunOut,
  ProcessingConfigurationRevisionCreate,
  ProcessingConfigurationRevisionOut,
  ProcessingParameterSchemaOut,
  ProcessingProfileOut
} from "./generated";

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const API_BASE = import.meta.env.VITE_TVA_API_BASE ?? import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: init.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init.headers },
      ...init
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(0, "request_cancelled");
    }
    throw new ApiError(0, "backend_unavailable");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => undefined) as
      | { detail?: string | { code?: string; detail?: string; resource_id?: string } }
      | undefined;
    const detail = payload?.detail;
    if (detail && typeof detail === "object") {
      throw new ApiError(response.status, detail.detail ?? `api_failure_${response.status}`, detail.code);
    }
    throw new ApiError(response.status, typeof detail === "string" ? detail : `api_failure_${response.status}`);
  }
  return response.json() as Promise<T>;
}

export type ProjectSnapshot = {
  project: Record<string, unknown>;
  source: Record<string, unknown> | null;
  scene: Record<string, unknown> | null;
  reference_frame: Record<string, unknown> | null;
  run: Record<string, unknown> | null;
  active_job: Record<string, unknown> | null;
  jobs: Record<string, unknown>[];
  review: { unresolved_mandatory_qc: number };
  certification: Record<string, unknown> | null;
  export: Record<string, unknown> | null;
  selected_run_id?: string | null;
  selected_run_explicit?: boolean;
  processing_runs?: Record<string, unknown>[];
  processing_configuration?: Record<string, unknown> | null;
  previews?: Record<string, unknown>[];
  stale_warnings?: string[];
};

export type ReadinessResponse = GeneratedReadinessResponse & {
  release?: {
    application_name?: string;
    release_version?: string;
    release_channel?: string;
    build_label?: string;
    git_commit_sha?: string;
  };
  core_status?: string;
  processing_status?: string;
  application_status?: string;
  application_ready?: boolean;
  overall_status?: string;
  components?: Record<string, ReadinessComponent>;
  liveness: Record<string, ReadinessComponent>;
  core: Record<string, ReadinessComponent>;
  processing: Record<string, unknown> & {
    media_runtime?: Record<string, unknown>;
    ready?: boolean;
    synthetic_ready?: boolean;
    status?: string;
  };
};

export type ReviewSession = Record<string, unknown> & {
  id: string;
  project_id: string;
  processing_run_id: string;
  review_scope_type: string;
  review_status: string;
  review_revision: number;
  stale_status: string;
};

export type ReviewQueue = {
  review_session_id: string;
  reviewed_projection_revision_id: string;
  review_revision?: number;
  projection_status?: string;
  items: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
};

export function exportArtifactDownloadUrl(artifactId: string): string {
  return `${API_BASE}/api/v1/export-artifacts/${encodeURIComponent(artifactId)}/download`;
}

export const api = {
  health: () => request<Record<string, unknown>>("/api/v1/health"),
  readiness: () => request<ReadinessResponse>("/api/v1/readiness"),
  release: () => request<Record<string, unknown>>("/api/v1/release"),
  listProjects: () => request<Record<string, unknown>[]>("/api/v1/projects"),
  createProject: (payload: ProjectCreate) =>
    request<Record<string, unknown>>("/api/v1/projects", { method: "POST", body: JSON.stringify(payload) }),
  getProject: (projectId: string) => request<ProjectSnapshot>(`/api/v1/projects/${projectId}`),
  selectCurrentProcessingRun: (projectId: string, runId: string, selectedBy = "operator") =>
    request<ProjectSnapshot>(`/api/v1/projects/${projectId}/processing-runs/${runId}/select`, {
      method: "POST",
      body: JSON.stringify({ selected_by: selectedBy })
    }),
  updateProject: (projectId: string, payload: ProjectUpdate) =>
    request<Record<string, unknown>>(`/api/v1/projects/${projectId}`, {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  registerSource: (payload: SourceCreate) =>
    request<Record<string, unknown>>("/api/v1/sources", { method: "POST", body: JSON.stringify(payload) }),
  uploadSource: (projectId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<Record<string, unknown>>(`/api/v1/projects/${projectId}/sources/upload`, {
      method: "POST",
      body
    });
  },
  updateTimeConfiguration: (projectId: string, payload: TimeConfigurationUpdate) =>
    request<ProjectSnapshot>(`/api/v1/projects/${projectId}/time-configuration`, {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  mediaRuntime: () => request<Record<string, unknown>>("/api/v1/media-runtime"),
  taxonomy: () => request<TaxonomyRevisionOut>("/api/v1/taxonomy"),
  classificationPolicy: () => request<ClassificationPolicyOut>("/api/v1/classification-policy"),
  extractReferenceFrame: (projectId: string, payload: ReferenceFrameRequest, init: RequestInit = {}) =>
    request<Record<string, unknown>>(`/api/v1/projects/${projectId}/reference-frames`, {
      method: "POST",
      body: JSON.stringify(payload),
      ...init
    }),
  referenceFramePreviewUrl: (projectId: string, frameId: string) =>
    `${API_BASE}/api/v1/projects/${projectId}/reference-frames/${frameId}/preview`,
  sourceVideoUrl: (projectId: string) => `${API_BASE}/api/v1/projects/${projectId}/source-video`,
  saveSceneConfiguration: (projectId: string, payload: SceneGeometrySave) =>
    request<Record<string, unknown>>(`/api/v1/projects/${projectId}/scene-configuration`, {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  validateSceneConfiguration: (projectId: string, payload: SceneGeometrySave) =>
    request<{ valid: boolean; errors: string[] }>(`/api/v1/projects/${projectId}/scene-configuration/validate`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  createScene: (projectId: string, template: "road_segment" | "intersection" | "pedestrian_crossing") =>
    request<Record<string, unknown>>("/api/v1/scenes", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, template })
    }),
  runMockAnalysis: (projectId: string) =>
    request<Record<string, unknown>>(`/api/v1/projects/${projectId}/mock-analysis`, { method: "POST" }),
  validateSyntheticInput: (projectId: string, payload: SyntheticValidationRequest) =>
    request<Record<string, unknown>>(`/api/v1/projects/${projectId}/synthetic-input/validate`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  runSyntheticFixture: (projectId: string, payload: SyntheticFixtureRequest) =>
    request<Record<string, unknown>>(`/api/v1/projects/${projectId}/synthetic-runs`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  createProcessingJob: (projectId: string, payload: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/v1/projects/${projectId}/processing-jobs`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getProcessingJobProgress: (jobId: string) => request<Record<string, unknown>>(`/api/v1/processing-jobs/${jobId}/progress`),
  cancelProcessingJob: (jobId: string, reason: string) =>
    request<Record<string, unknown>>(`/api/v1/processing-jobs/${jobId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason })
    }),
  listEvents: (runId: string) => request<Record<string, unknown>[]>(`/api/v1/runs/${runId}/events`),
  listCrossingEvents: (runId: string) => request<Record<string, unknown>[]>(`/api/v1/runs/${runId}/crossing-events`),
  getAggregates: (runId: string) => request<Record<string, unknown>>(`/api/v1/runs/${runId}/aggregates`),
  getReviewedTotals: (runId: string) => request<Record<string, unknown>>(`/api/v1/runs/${runId}/reviewed-totals`),
  getEngineeringSummary: (runId: string) => request<EngineeringSummaryOut>(`/api/v1/runs/${runId}/engineering-summary`),
  listEngineeringEvents: (runId: string) => request<EngineeringEventOut[]>(`/api/v1/runs/${runId}/engineering-events`),
  getTrackEvidence: (runId: string, trackId: string) => request<TrackEvidenceOut[]>(`/api/v1/runs/${runId}/tracks/${trackId}/evidence`),
  addReviewAction: (payload: ReviewActionCreate) =>
    request<Record<string, unknown>>("/api/v1/review-actions", { method: "POST", body: JSON.stringify(payload) }),
  markReviewComplete: (projectId: string) =>
    request<Record<string, unknown>>(`/api/v1/projects/${projectId}/review-complete`, { method: "POST" }),
  certify: (projectId: string, runId: string) =>
    request<Record<string, unknown>>(`/api/v1/projects/${projectId}/runs/${runId}/certifications`, { method: "POST" }),
  exportManifest: (projectId: string, runId: string, format: "csv" | "xlsx") =>
    request<Record<string, unknown>>(`/api/v1/projects/${projectId}/runs/${runId}/exports/${format}`, {
      method: "POST"
    }),
  benchmarkOverview: () => request<BenchmarkOverviewOut>("/api/v1/benchmarks/overview"),
  benchmarkExperiments: () => request<BenchmarkExperimentComparisonOut[]>("/api/v1/benchmarks/experiments"),
  benchmarkMetrics: (benchmarkRunId: string) =>
    request<BenchmarkMetricsOut>(`/api/v1/benchmarks/runs/${benchmarkRunId}/metrics`),
  benchmarkMatches: (benchmarkRunId: string, category?: string) => {
    const query = category ? `?category=${encodeURIComponent(category)}` : "";
    return request<BenchmarkMatchPageOut>(`/api/v1/benchmarks/runs/${benchmarkRunId}/matches${query}`);
  },
  processingProfiles: () => request<ProcessingProfileOut[]>("/api/v1/processing-profiles"),
  processingParameterSchema: () => request<ProcessingParameterSchemaOut>("/api/v1/processing-parameter-schema"),
  resolveProcessingConfiguration: (projectId: string, payload: {
    profile_code: ProcessingProfileOut["profile_code"];
    guided_settings?: GuidedProcessingSettings;
    expert_overrides?: ExpertProcessingOverrides;
    expected_scene_version?: number;
    created_by?: string;
  }) => request<ConfigurationValidationOut>(`/api/v1/projects/${projectId}/processing-configurations/resolve`, {
    method: "POST",
    body: JSON.stringify(payload)
  }),
  createProcessingConfiguration: (projectId: string, payload: ProcessingConfigurationRevisionCreate) =>
    request<ProcessingConfigurationRevisionOut>(`/api/v1/projects/${projectId}/processing-configurations`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getProcessingConfiguration: (revisionId: string) =>
    request<ProcessingConfigurationRevisionOut>(`/api/v1/processing-configurations/${revisionId}`),
  createPreviewRun: (projectId: string, payload: PreviewRunCreate) =>
    request<PreviewRunOut>(`/api/v1/projects/${projectId}/previews`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  listPreviewRuns: (projectId: string) => request<PreviewRunOut[]>(`/api/v1/projects/${projectId}/previews`),
  getPreviewRun: (previewId: string) => request<PreviewRunOut>(`/api/v1/previews/${previewId}`),
  cancelPreviewRun: (previewId: string, reason?: string) =>
    request<PreviewRunOut>(`/api/v1/previews/${previewId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason })
    }),
  promotePreviewRun: (previewId: string) =>
    request<Record<string, unknown>>(`/api/v1/previews/${previewId}/promote`, { method: "POST" }),
  compareProcessingConfigurations: (projectId: string, payload: ConfigurationComparisonCreate) =>
    request<ConfigurationComparisonOut>(`/api/v1/projects/${projectId}/configuration-comparisons`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  diffProcessingConfigurations: (projectId: string, payload: ConfigurationDiffCreate) =>
    request<ConfigurationDiffOut>(`/api/v1/projects/${projectId}/processing-configurations/diff`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  selectOperationalCandidate: (projectId: string, payload: OperationalCandidateSelectionCreate) =>
    request<OperationalCandidateSelectionOut>(`/api/v1/projects/${projectId}/operational-candidates`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  createCapabilityValidation: (projectId: string, payload: CapabilityValidationCreate) =>
    request<CapabilityValidationOut>(`/api/v1/projects/${projectId}/capability-validations`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  listCapabilityValidations: (projectId: string) =>
    request<CapabilityValidationOut[]>(`/api/v1/projects/${projectId}/capability-validations`),
  listReviewSessions: (projectId: string) =>
    request<ReviewSession[]>(`/api/v1/projects/${projectId}/review-sessions`),
  createReviewSession: (projectId: string, payload: {
    processing_run_id: string;
    engineering_result_revision_id?: string;
    review_scope_type?: "FULL_RESULT" | "LINE" | "TIME_INTERVAL" | "DIAGNOSTIC_SUBSET";
    review_scope_filter?: Record<string, unknown>;
    created_by?: string;
    new_revision?: boolean;
  }) => request<ReviewSession>(`/api/v1/projects/${projectId}/review-sessions`, {
    method: "POST",
    body: JSON.stringify(payload)
  }),
  getReviewSession: (sessionId: string) => request<ReviewSession>(`/api/v1/review-sessions/${sessionId}`),
  listReviewActions: (sessionId: string) =>
    request<Record<string, unknown>[]>(`/api/v1/review-sessions/${sessionId}/actions`),
  getReviewQueue: (sessionId: string, params: Record<string, string | number | boolean | undefined> = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => { if (value !== undefined) query.set(key, String(value)); });
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request<ReviewQueue>(`/api/v1/review-sessions/${sessionId}/queue${suffix}`);
  },
  appendScopedReviewAction: (sessionId: string, payload: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/v1/review-sessions/${sessionId}/actions`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getReviewEvidence: (sessionId: string, eventId: string) =>
    request<Record<string, unknown>>(`/api/v1/review-sessions/${sessionId}/events/${encodeURIComponent(eventId)}/evidence`),
  getReviewProgress: (sessionId: string) => request<Record<string, unknown>>(`/api/v1/review-sessions/${sessionId}/progress`),
  buildReviewedProjection: (sessionId: string) => request<Record<string, unknown>>(`/api/v1/review-sessions/${sessionId}/projections`, { method: "POST" }),
  completeReviewSession: (sessionId: string, payload: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/v1/review-sessions/${sessionId}/complete`, { method: "POST", body: JSON.stringify(payload) }),
  createReviewCertification: (sessionId: string, payload: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/v1/review-sessions/${sessionId}/certification`, { method: "POST", body: JSON.stringify(payload) }),
  listReviewCertifications: (projectId: string) =>
    request<Record<string, unknown>[]>(`/api/v1/projects/${projectId}/certifications`),
  listCertifiedExports: (certificationId: string) =>
    request<Record<string, unknown>[]>(`/api/v1/certifications/${certificationId}/exports`),
  requestCertifiedExport: (certificationId: string, payload: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/v1/certifications/${certificationId}/exports`, { method: "POST", body: JSON.stringify(payload) }),
  exportArtifactDownloadUrl
};
