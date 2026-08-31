import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App, takeSelectedUpload } from "../src/App";
import { api } from "../src/api/client";

const runtime = {
  reference_frame_extraction_available: false,
  ffmpeg: { name: "ffmpeg" },
  ffprobe: { name: "ffprobe" }
};
const readiness = {
  status: "degraded",
  liveness: { backend: { state: "alive", ready: true }, database: { state: "available", ready: true } },
  core: {
    api: { state: "ready", ready: true },
    project_storage: { state: "ready", ready: true },
    scene_configuration: { state: "ready", ready: true }
  },
  processing: {
    worker: { state: "not_started", ready: false, detail: "Processing worker is a separate mock process." },
    model_weights: { state: "unavailable", ready: false },
    media_runtime: runtime,
    ready: false
  },
  generated_at: "2026-07-27T00:00:00Z",
  startup_phases_ms: null
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("API-backed shell", () => {
  it("clears the upload input before asynchronous work so the same file can be selected again", async () => {
    const input = document.createElement("input");
    input.type = "file";
    const file = new File(["safe fixture"], "e2e-legal-upload.mp4", { type: "video/mp4" });

    await userEvent.upload(input, file);
    expect(takeSelectedUpload(input)).toBe(file);
    expect(input.files).toHaveLength(0);

    await userEvent.upload(input, file);
    expect(takeSelectedUpload(input)).toBe(file);
    expect(input.files).toHaveLength(0);
  });

  it("renders startup shell while waiting for backend readiness", async () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise<Response>(() => undefined));
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(screen.getByText("Opening the application shell")).toBeInTheDocument();
    expect(screen.getByText("Frontend shell")).toBeInTheDocument();
    expect(screen.getByText("Backend API")).toBeInTheDocument();
  });

  it("renders empty state when the backend has no projects", async () => {
    mockFetch([json(readiness), json([])]);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "EN" }));
    expect(screen.getByText("Create or open a project before loading video")).toBeInTheDocument();
    expect(screen.getByText("Create a project first")).toBeInTheDocument();
  });

  it("creates a project only after the backend confirms persistence", async () => {
    mockFetch([
      json(readiness),
      json([]),
      json({
        id: "prj_1",
        name: "Rama IX traffic study",
        location: "Bangkok",
        study_type: "intersection",
        language: "th",
        state: "draft",
        stale: 0,
        created_at: "now",
        version: 1
      }),
      json(readiness),
      json([{ id: "prj_1" }]),
      json({
        project: {
          id: "prj_1",
          name: "Rama IX traffic study",
          location: "Bangkok",
          study_type: "intersection",
          language: "th",
          state: "draft",
          stale: 0,
          created_at: "now",
          version: 1
        },
        source: null,
        scene: null,
        reference_frame: null,
        run: null,
        review: { unresolved_mandatory_qc: 0 },
        certification: null,
        export: null
      })
    ]);
    render(<App />);
    await screen.findByLabelText("ชื่อโครงการ");
    await userEvent.click(screen.getAllByRole("button", { name: "สร้างโครงการ" }).at(-1)!);
    await waitFor(() => expect(window.localStorage.getItem("tva.projectId")).toBe("prj_1"));
  });

  it("shows a controlled backend unavailable error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    render(<App />);
    expect((await screen.findAllByText("backend_unavailable")).length).toBeGreaterThan(0);
  });

  it("submits a processing job without labeling it as a backend failure", async () => {
    const queuedJob = {
      id: "run_job",
      job_state: "QUEUED",
      progress_percent: 0,
      progress_phase: "QUEUED",
      progress_message_code: "job_queued"
    };
    const snapshot = {
      project: {
        id: "prj_1",
        name: "Ready scene",
        location: "Bangkok",
        study_type: "intersection",
        language: "en",
        state: "scene_configured",
        stale: 0,
        created_at: "now",
        version: 1
      },
      source: {
        id: "src_1",
        file_name: "source.mp4",
        fingerprint_sha256: "abc",
        timezone_name: "Asia/Bangkok",
        analysis_start_pts_ms: 0,
        analysis_end_pts_ms: 1000
      },
      scene: { id: "scn_1", version: 1, geometry_json: JSON.stringify(defaultSceneGeometry()) },
      reference_frame: { id: "frm_1" },
      run: null,
      active_job: null,
      jobs: [],
      review: { unresolved_mandatory_qc: 0 },
      certification: null,
      export: null
    };
    mockFetch([
      json({
        ...readiness,
        status: "core_ready",
        processing: { ...readiness.processing, media_runtime: { ...runtime, reference_frame_extraction_available: true }, ready: true }
      }),
      json([{ id: "prj_1" }]),
      json(snapshot),
      json(queuedJob),
      json({ ...snapshot, active_job: queuedJob, jobs: [queuedJob] })
    ]);
    render(<App />);
    await screen.findAllByText("Ready scene");
    await userEvent.click(screen.getByRole("button", { name: "EN" }));
    await userEvent.click(screen.getByRole("button", { name: "Start processing job" }));
    expect(screen.queryByText("Backend unavailable")).not.toBeInTheDocument();
    expect(screen.getAllByText("Processing job").length).toBeGreaterThan(0);
  });

  it("sends the saved configuration revision for a real-video full run", async () => {
    const realReadiness = {
      ...readiness,
      status: "core_ready",
      processing: {
        ...readiness.processing,
        real_inference: { ready: true, state: "real_ready", detail: "" },
        ready: true
      }
    };
    const snapshot = {
      project: {
        id: "prj_real",
        name: "Ready scene",
        location: "Bangkok",
        study_type: "intersection",
        language: "en",
        state: "scene_configured",
        stale: 0,
        created_at: "now",
        version: 1
      },
      source: {
        id: "src_real",
        source_type: "local_upload",
        readiness_state: "media_ready",
        file_name: "source.mp4",
        fingerprint_sha256: "abc",
        timezone_name: "Asia/Bangkok",
        analysis_start_pts_ms: 0,
        analysis_end_pts_ms: 30000,
        duration_ms: 30000
      },
      scene: { id: "scn_real", version: 1, geometry_json: JSON.stringify(defaultSceneGeometry()) },
      reference_frame: { id: "frm_real", preview_width: 640, preview_height: 384 },
      run: null,
      active_job: null,
      jobs: [],
      review: { unresolved_mandatory_qc: 0 },
      certification: null,
      export: null
    };
    const balancedProfile = {
      profile_code: "BALANCED",
      profile_revision: "balanced-r1",
      display_name_en: "Balanced",
      display_name_th: "สมดุล",
      description_en: "Mixed traffic starting point.",
      description_th: "ค่าตั้งต้นสำหรับการจราจรผสม",
      intended_use: "preview",
      resolved_parameters: { image_size: 640, frame_stride: 1 },
      supported_domains: ["vehicle", "pedestrian"],
      hardware_expectation: "CPU or GPU",
      known_tradeoffs: [],
      created_by: "system",
      created_at: "now",
      content_hash: "profile-hash",
      status: "ACTIVE",
      schema_revision: "processing-profile-v1"
    };
    const configuration = {
      id: "config-real",
      project_id: "prj_real",
      profile_code: "BALANCED",
      profile_revision: "balanced-r1",
      guided_settings: {},
      requested_expert_overrides: {},
      resolved_parameters: { image_size: 640, frame_stride: 1 },
      valid: true,
      errors: [],
      warnings: [],
      normalizations: [],
      estimated_resource_impact: { estimated_memory_class: "LOW" },
      adapter_support: {},
      parameter_schema_revision: "processing-parameters-v1",
      device_mode: "AUTO",
      resolved_device: "cpu",
      configuration_hash: "config-hash",
      request_provenance_hash: "request-hash",
      runtime_configuration_hash: null,
      runtime_provenance_status: "AWAITING_RUNTIME_RESOLUTION",
      runtime_provenance: {}
    };
    const queuedJob = {
      id: "run-real",
      project_id: "prj_real",
      job_state: "QUEUED",
      progress_percent: 0,
      progress_phase: "QUEUED",
      progress_message_code: "job_queued",
      processing_mode: "REAL_VIDEO",
      result_ready: false,
      fixture_id: null
    };

    vi.spyOn(api, "readiness").mockResolvedValue(realReadiness as never);
    vi.spyOn(api, "listProjects").mockResolvedValue([{ id: "prj_real", name: "Ready scene" }] as never);
    vi.spyOn(api, "getProject").mockResolvedValue(snapshot as never);
    vi.spyOn(api, "processingProfiles").mockResolvedValue([balancedProfile] as never);
    vi.spyOn(api, "listPreviewRuns").mockResolvedValue([]);
    const createConfiguration = vi.spyOn(api, "createProcessingConfiguration").mockResolvedValue(configuration as never);
    const createJob = vi.spyOn(api, "createProcessingJob").mockResolvedValue(queuedJob as never);

    render(<App />);
    await screen.findAllByText("Ready scene");
    await userEvent.click(screen.getByRole("button", { name: "EN" }));
    await userEvent.click(screen.getByRole("button", { name: "Operational settings" }));
    await screen.findByRole("heading", { name: "Processing profiles and capability check" });
    await userEvent.click(screen.getByRole("button", { name: "Save immutable revision" }));
    await waitFor(() => expect(createConfiguration).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "Back to workspace" }));
    await userEvent.selectOptions(screen.getByLabelText("Processing mode"), "REAL_VIDEO");
    await userEvent.click(screen.getByRole("button", { name: "Start processing job" }));

    await waitFor(() => expect(createJob).toHaveBeenCalled());
    const payload = createJob.mock.calls[0][1] as Record<string, unknown>;
    expect(payload).toEqual(expect.objectContaining({
      mode: "REAL_VIDEO",
      processing_configuration_revision_id: "config-real"
    }));
    expect(payload).not.toHaveProperty("fixture_id");
    expect(payload).not.toHaveProperty("configuration");
  });

  it("renders Thai labels without falling back to English", async () => {
    mockFetch([json(readiness), json([])]);
    render(<App />);
    expect(await screen.findByRole("button", { name: "สร้างโครงการ" })).toBeInTheDocument();
    expect(screen.getByText("แหล่งวิดีโอและเวลา")).toBeInTheDocument();
    expect(screen.queryByText("Source and time")).not.toBeInTheDocument();
  });

  it("opens the benchmark workspace with an explicit empty corpus state", async () => {
    mockFetch([
      json(readiness),
      json([]),
      json({
        status: "EMPTY",
        corpus_revisions: [],
        source_count: 0,
        rights_coverage: {},
        split_counts: {},
        latest_run: null,
        benchmark_runs: [],
        limitations: ["No accuracy claim is made without reviewed data."]
      })
    ]);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "EN" }));
    await userEvent.click(screen.getByRole("button", { name: "Benchmark workspace" }));
    expect(await screen.findByRole("heading", { name: "No benchmark corpus is loaded" })).toBeInTheDocument();
    expect(
      within(screen.getByRole("complementary", { name: "Benchmark workspace" })).getByText(
        "No owner-approved threshold policy is configured."
      )
    ).toBeInTheDocument();
  });

  it("renders persisted threshold results without the missing-policy disclosure", async () => {
    mockFetch([
      json(readiness),
      json([]),
      json({
        status: "READY",
        corpus_revisions: [{ id: "corpus-1", revision: "holdout-corpus-v1", sources: [{ id: "source-1", benchmark_source_id: "holdout-01", benchmark_split: "HOLDOUT", rights_status: "CLEARED_FOR_REPOSITORY_DISTRIBUTION", condition_tags: ["daytime"], checksum_verified: true }] }],
        source_count: 1,
        rights_coverage: { CLEARED_FOR_REPOSITORY_DISTRIBUTION: 1 },
        split_counts: { HOLDOUT: 1 },
        latest_run: { id: "benchmark-run-1", benchmark_source_id: "holdout-01", benchmark_split: "HOLDOUT", rights_status: "CLEARED_FOR_REPOSITORY_DISTRIBUTION", ground_truth_revision: "gt-1", evaluation_configuration_revision: "eval-1", status: "COMPLETED", qualification_status: "QUALIFIED_FOR_PILOT", source_fingerprint_sha256: "abc", scene_revision: "scene-1", code_commit_sha: "commit-1", configuration_hash: "config-1", started_at: "now" },
        benchmark_runs: [],
        limitations: []
      }),
      json({
        benchmark_run_id: "benchmark-run-1",
        metrics: { event_metrics: { precision: 1, recall: 1, f1: 1 }, count_metrics: {}, direction_metrics: {}, class_metrics: {}, timestamp_metrics: {}, fragmentation_metrics: {}, throughput_metrics: {}, duplicate_metrics: {} },
        qualification: { status: "QUALIFIED_FOR_PILOT", threshold_policy_present: true, policy: { policy_revision: "owner-policy-1" }, gates: { required_thresholds_pass: { passed: true, reason: "all mandatory threshold comparisons must pass" } }, threshold_evaluation: { results: [{ metric_path: "event_metrics.recall", observed_value: 1, required_value: 0.95, operator: "GTE", unit: "ratio", availability: "AVAILABLE", passed: true }] } }
      }),
      json({ benchmark_run_id: "benchmark-run-1", items: [], limit: 100, offset: 0, category: null }),
      json([])
    ]);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "EN" }));
    await userEvent.click(screen.getByRole("button", { name: "Benchmark workspace" }));
    expect(await screen.findByText("owner-policy-1")).toBeInTheDocument();
    expect(screen.getByText("event_metrics.recall")).toBeInTheDocument();
    expect(screen.queryByText("No owner-approved threshold policy is configured.")).not.toBeInTheDocument();
  });

  it("presents a provisional engineering summary with reconciliation and interval dimensions", async () => {
    const run = { id: "run_6b", result_ready: 1, processing_mode: "SYNTHETIC", state: "processing_complete" };
    const snapshot = {
      project: { id: "prj_6b", name: "Engineering result", location: "Bangkok", study_type: "intersection", language: "en", state: "needs_review", stale: 0, created_at: "now", version: 1 },
      source: { id: "src_6b", file_name: "synthetic.mp4", fingerprint_sha256: "abc", timezone_name: "Asia/Bangkok", analysis_start_pts_ms: 0, analysis_end_pts_ms: 900000 },
      scene: { id: "scn_6b", version: 1, geometry_json: JSON.stringify(defaultSceneGeometry()) },
      reference_frame: null,
      run,
      active_job: null,
      jobs: [],
      review: { unresolved_mandatory_qc: 0 },
      certification: null,
      export: null
    };
    const summary = {
      id: "eng_6b", run_id: "run_6b", result_version: "result-6b", result_status: "STRUCTURALLY_VALID", engineering_ready: true, stale: false,
      taxonomy_revision: "pilot-observable-taxonomy-v1", mapping_revision: "pilot-observable-mapping-v1", classification_policy_revision: "track-vote-policy-v1",
      source_time_status: "UNCONFIGURED", source_timezone_name: "Asia/Bangkok",
      disclosures: ["automatic_classification_is_provisional"],
      line_totals: [{ line_id: "line_main", line_name: "Main crossing", total: 2, a_to_b: 1, b_to_a: 1 }],
      line_class_totals: [{ line_id: "line_main", line_name: "Main crossing", engineering_class: "UNKNOWN", total: 1 }, { line_id: "line_main", line_name: "Main crossing", engineering_class: "AMBIGUOUS", total: 1 }],
      direction_class_matrix: [
        { line_id: "line_main", line_name: "Main crossing", direction: "A_TO_B", engineering_class: "UNKNOWN", total: 1 },
        { line_id: "line_main", line_name: "Main crossing", direction: "B_TO_A", engineering_class: "AMBIGUOUS", total: 1 }
      ],
      intervals: [{ interval_index: 0, source_relative_start_pts_ms: 0, source_relative_end_pts_ms: 900000, absolute_start: null, absolute_end: null, timezone_name: "Asia/Bangkok", display_label: "PTS 0–900000 ms", time_status: "UNCONFIGURED", partial: false }],
      interval_rows: [
        { interval_index: 0, line_id: "line_main", line_name: "Main crossing", direction: "A_TO_B", engineering_class: "UNKNOWN", count: 1 },
        { interval_index: 0, line_id: "line_main", line_name: "Main crossing", direction: "B_TO_A", engineering_class: "AMBIGUOUS", count: 1 }
      ],
      overall_event_total: 2, events_count: 2,
      reconciliation: { status: "STRUCTURALLY_VALID", engineering_ready: true, invariants: [], result_revision: "eng_6b" },
      provenance: { processing_mode: "SYNTHETIC" }
    };
    mockFetch([
      json(readiness), json([{ id: "prj_6b" }]), json(snapshot), json([]), json([]), json({ hourly_total: 2, fifteen_minute_counts: [2] }), json({ raw_total: 2, accepted_total: 0, excluded_total: 0, unresolved_total: 2 }), json(summary)
    ]);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "EN" }));
    expect(await screen.findByText("Provisional automatic classification")).toBeInTheDocument();
    expect(screen.getByText("Structurally valid")).toBeInTheDocument();
    expect(screen.getByText("Class × direction matrix")).toBeInTheDocument();
    expect(screen.getByText("Source-relative time")).toBeInTheDocument();
    expect(screen.getAllByText("UNKNOWN").length).toBeGreaterThan(0);
  });
});

function mockFetch(responses: Response[]) {
  const fetchMock = vi.spyOn(globalThis, "fetch");
  for (const response of responses) {
    fetchMock.mockResolvedValueOnce(response);
  }
}

function json(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init
  });
}

function defaultSceneGeometry() {
  return {
    schema_version: "scene-geometry-v1",
    coordinate_system: { origin: "top_left", units: "normalized_source_display" },
    counting_lines: [
      {
        id: "line_main",
        name: "Main crossing",
        active: true,
        start: { x: 0.2, y: 0.7 },
        end: { x: 0.8, y: 0.3 },
        direction_mode: "a_to_b",
        direction_a_label: "A side",
        direction_b_label: "B side",
        approach_name: "",
        movement_name: "",
        analyst_note: ""
      }
    ],
    rois: [
      {
        id: "roi_main",
        name: "Working area",
        active: true,
        vertices: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.9, y: 0.9 }, { x: 0.1, y: 0.9 }]
      }
    ]
  };
}
