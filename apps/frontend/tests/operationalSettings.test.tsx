import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api/client";
import { OperationalSettingsWorkspace } from "../src/OperationalSettingsWorkspace";

const syntheticSource = {
  id: "source-synthetic",
  file_name: "approved-mock.mp4",
  fingerprint_sha256: "mock-full-file-sha256",
  analysis_start_pts_ms: 0,
  analysis_end_pts_ms: 120_000
};

const realSource = {
  id: "source-real",
  source_type: "local_upload",
  readiness_state: "media_ready",
  file_name: "operator-video.mp4",
  fingerprint_sha256: "real-source-hash",
  analysis_start_pts_ms: 5_000,
  analysis_end_pts_ms: 125_000
};

const baseProps = {
  language: "en" as const,
  projectId: "prj_1",
  sceneVersion: 1,
  projectStale: false,
  realVideoReady: false,
  realVideoBlockReason: "The processing worker is offline.",
  onBack: vi.fn()
};

const balancedProfile = {
  id: "profile-balanced",
  profile_code: "BALANCED",
  profile_revision: "balanced-r1",
  display_name_en: "Balanced",
  display_name_th: "สมดุล",
  description_en: "Mixed traffic starting point.",
  description_th: "จุดเริ่มต้น",
  intended_use: "preview",
  resolved_parameters: { image_size: 640, frame_stride: 1 },
  supported_domains: ["vehicle", "pedestrian"],
  hardware_expectation: "CPU or GPU",
  known_tradeoffs: [],
  created_by: "system",
  created_at: "now",
  content_hash: "hash",
  status: "ACTIVE",
  schema_revision: "processing-profile-v1"
} as never;

function validConfiguration(id: string) {
  return {
    id,
    project_id: "prj_1",
    valid: true,
    errors: [],
    warnings: [],
    guided_settings: {},
    requested_guided_settings: {},
    requested_expert_overrides: {},
    resolved_parameters: { image_size: 640, frame_stride: 1 },
    normalizations: [],
    estimated_resource_impact: {},
    configuration_hash: "runtime-hash",
    runtime_configuration_hash: "runtime-hash",
    request_provenance_hash: "request-hash",
    runtime_provenance_status: "CONFIGURED_EXPECTED_WEIGHT_SHA",
    runtime_provenance: {},
    model_revision: "pypi:8.4.103",
    weight_sha256: "weight-hash",
    tracker_revision: "pypi:2.5.0.post0",
    crossing_policy_revision: "canonical-crossing-policy-v2",
    classification_policy_revision: "track-vote-policy-v1",
    device_request: "AUTO",
    parameter_schema_revision: "processing-parameter-schema-v1",
    resolved_device: "cpu",
    adapter_support: {},
    profile_code: "BALANCED",
    profile_revision: "balanced-r1",
    profile_id: "BALANCED",
    configuration_schema_revision: "processing-configuration-v1",
    created_by: "operator",
    created_at: "now",
    content_hash: "content-hash",
    status: "VALID"
  } as never;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("operational settings workspace", () => {
  it("keeps expert controls collapsed until requested and shows the profile catalog", async () => {
    vi.spyOn(api, "processingProfiles").mockResolvedValue([
      {
        id: "profile-balanced",
        profile_code: "BALANCED",
        profile_revision: "balanced-r1",
        display_name_en: "Balanced",
        display_name_th: "สมดุล",
        description_en: "Mixed traffic starting point.",
        description_th: "จุดเริ่มต้น",
        intended_use: "preview",
        resolved_parameters: { image_size: 640, frame_stride: 1 } as never,
        supported_domains: ["vehicle", "pedestrian"],
        hardware_expectation: "CPU or GPU",
        known_tradeoffs: [],
        created_by: "system",
        created_at: "now",
        content_hash: "hash",
        status: "ACTIVE",
        schema_revision: "processing-profile-v1"
      }
    ]);
    vi.spyOn(api, "listPreviewRuns").mockResolvedValue([]);
    render(<OperationalSettingsWorkspace {...baseProps} source={syntheticSource} />);
    expect(await screen.findByRole("heading", { name: "Processing profiles and capability check" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show expert settings" })).toBeInTheDocument();
    expect(screen.queryByText("Detector and sampling")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show expert settings" }));
    expect(screen.getByText("Detector and sampling")).toBeInTheDocument();
    expect(screen.getByText("No accuracy metric is shown without approved ground truth. Throughput does not establish real-time capability.")).toBeInTheDocument();
  });

  it("shows a selected profile baseline and clears expert overrides on reset", async () => {
    vi.spyOn(api, "processingProfiles").mockResolvedValue([
      {
        id: "profile-high",
        profile_code: "HIGH_ACCURACY",
        profile_revision: "high_accuracy-r1",
        display_name_en: "High accuracy investigation",
        display_name_th: "ตรวจสอบเชิงลึก",
        description_en: "Higher detail.",
        description_th: "รายละเอียดสูง",
        intended_use: "investigation",
        resolved_parameters: { image_size: 1280, frame_stride: 1, confidence_threshold: 0.2, lost_track_buffer: 60, minimum_track_observations: 3, class_allowlist: ["car"] } as never,
        supported_domains: ["vehicle"],
        hardware_expectation: "GPU recommended",
        known_tradeoffs: [],
        created_by: "system",
        created_at: "now",
        content_hash: "hash-high",
        status: "ACTIVE",
        schema_revision: "processing-profile-v1"
      }
    ]);
    vi.spyOn(api, "listPreviewRuns").mockResolvedValue([]);
    render(<OperationalSettingsWorkspace {...baseProps} source={syntheticSource} />);
    await userEvent.click(await screen.findByRole("button", { name: /High accuracy investigation/ }));
    expect((screen.getByLabelText("Analysis quality") as HTMLSelectElement).value).toBe("VERY_HIGH");
    await userEvent.click(screen.getByRole("button", { name: "Show expert settings" }));
    await userEvent.type(screen.getByLabelText("Image size"), "1536");
    await userEvent.click(screen.getByRole("button", { name: "Reset to selected profile" }));
    expect((screen.getByLabelText("Image size") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Analysis quality") as HTMLSelectElement).value).toBe("VERY_HIGH");
  });

  it("invalidates the saved revision when an operator changes a setting", async () => {
    vi.spyOn(api, "processingProfiles").mockResolvedValue([balancedProfile]);
    vi.spyOn(api, "listPreviewRuns").mockResolvedValue([]);
    vi.spyOn(api, "createProcessingConfiguration").mockResolvedValue(validConfiguration("config-1"));
    const onConfigurationSaved = vi.fn();

    render(<OperationalSettingsWorkspace {...baseProps} source={syntheticSource} onConfigurationSaved={onConfigurationSaved} />);
    await userEvent.click(await screen.findByRole("button", { name: "Save immutable revision" }));

    expect(onConfigurationSaved).toHaveBeenLastCalledWith("config-1");
    await userEvent.selectOptions(screen.getByLabelText("Analysis quality"), "HIGH");
    expect(onConfigurationSaved).toHaveBeenLastCalledWith(null);
  });

  it("shows a queued preview's ownership and sends cooperative cancellation", async () => {
    vi.spyOn(api, "processingProfiles").mockResolvedValue([
      {
        id: "profile-balanced",
        profile_code: "BALANCED",
        profile_revision: "balanced-r1",
        display_name_en: "Balanced",
        display_name_th: "สมดุล",
        description_en: "Mixed traffic starting point.",
        description_th: "จุดเริ่มต้น",
        intended_use: "preview",
        resolved_parameters: { image_size: 640, frame_stride: 1 } as never,
        supported_domains: ["vehicle", "pedestrian"],
        hardware_expectation: "CPU or GPU",
        known_tradeoffs: [],
        created_by: "system",
        created_at: "now",
        content_hash: "hash",
        status: "ACTIVE",
        schema_revision: "processing-profile-v1"
      }
    ]);
    vi.spyOn(api, "listPreviewRuns").mockResolvedValue([]);
    const configuration = {
      id: "config-1",
      project_id: "prj_1",
      valid: true,
      errors: [],
      warnings: [],
      guided_settings: {},
      requested_guided_settings: {},
      requested_expert_overrides: {},
      resolved_parameters: { image_size: 640, frame_stride: 1 },
      normalizations: [],
      estimated_resource_impact: {},
      configuration_hash: "runtime-hash",
      runtime_configuration_hash: "runtime-hash",
      request_provenance_hash: "request-hash",
      runtime_provenance_status: "CONFIGURED_EXPECTED_WEIGHT_SHA",
      runtime_provenance: {},
      model_revision: "pypi:8.4.103",
      weight_sha256: "weight-hash",
      tracker_revision: "pypi:2.5.0.post0",
      crossing_policy_revision: "canonical-crossing-policy-v2",
      classification_policy_revision: "track-vote-policy-v1",
      device_request: "AUTO",
      parameter_schema_revision: "processing-parameter-schema-v1",
      resolved_device: "cpu",
      adapter_support: {},
      profile_code: "BALANCED",
      profile_revision: "balanced-r1",
      profile_id: "BALANCED",
      configuration_schema_revision: "processing-configuration-v1",
      created_by: "operator",
      created_at: "now",
      content_hash: "content-hash",
      status: "VALID"
    } as never;
    const queued = {
      id: "preview-1",
      project_id: "prj_1",
      source_id: "source-1",
      source_fingerprint_sha256: "source-hash",
      scene_revision: "1",
      scene_semantic_hash: "scene-hash",
      start_pts_ms: 0,
      end_pts_ms: 30_000,
      processing_configuration_revision_id: "config-1",
      runtime_configuration_hash: "runtime-hash",
      request_fingerprint: "request-fingerprint",
      run_type: "PREVIEW_ONLY",
      mode: "REAL_VIDEO",
      status: "QUEUED",
      created_by: "operator",
      created_at: "now",
      completed_at: null,
      promoted_full_run_id: null,
      ownership_state: "QUEUED",
      attempt_number: 0,
      heartbeat_at: null,
      lease_expires_at: null,
      cancellation_reason: null,
      provenance_status: "CONFIGURED_EXPECTED_WEIGHT_SHA",
      statistics: null,
      warnings: [],
      evidence: [],
      events: [],
      disclosures: ["PREVIEW_ONLY", "NOT_PRODUCTION_RESULT"]
    } as never;
    const cancelled = { ...(queued as unknown as Record<string, unknown>), status: "CANCELLED", ownership_state: "TERMINAL", cancellation_reason: "operator stop" } as never;
    vi.spyOn(api, "createProcessingConfiguration").mockResolvedValue(configuration);
    vi.spyOn(api, "createPreviewRun").mockResolvedValue(queued);
    vi.spyOn(api, "getPreviewRun").mockResolvedValue(queued);
    const cancel = vi.spyOn(api, "cancelPreviewRun").mockResolvedValue(cancelled);

    render(<OperationalSettingsWorkspace {...baseProps} source={realSource} realVideoReady />);
    await userEvent.click(await screen.findByRole("button", { name: "Run real-video preview" }));
    expect(await screen.findByRole("button", { name: "Cancel preview" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel preview" }));
    expect(cancel).toHaveBeenCalledWith("preview-1", "Cancelled by operator from operational settings.");
    expect((await screen.findAllByText("Preview cancelled. No production events were written.")).length).toBeGreaterThan(0);
  });

  it("routes a managed upload to a bounded REAL_VIDEO preview without fixture_id", async () => {
    vi.spyOn(api, "processingProfiles").mockResolvedValue([balancedProfile]);
    vi.spyOn(api, "listPreviewRuns").mockResolvedValue([]);
    vi.spyOn(api, "createProcessingConfiguration").mockResolvedValue(validConfiguration("config-real"));
    const createPreview = vi.spyOn(api, "createPreviewRun").mockResolvedValue({
      id: "preview-real",
      project_id: "prj_1",
      source_id: "source-real",
      source_fingerprint_sha256: "real-source-hash",
      scene_revision: "1",
      scene_semantic_hash: "scene-hash",
      start_pts_ms: 5_000,
      end_pts_ms: 35_000,
      processing_configuration_revision_id: "config-real",
      runtime_configuration_hash: "runtime-hash",
      request_fingerprint: "request-fingerprint",
      run_type: "PREVIEW_ONLY",
      mode: "REAL_VIDEO",
      status: "QUEUED",
      created_by: "operator",
      created_at: "now",
      ownership_state: "QUEUED",
      attempt_number: 0,
      provenance_status: "CONFIGURED_EXPECTED_WEIGHT_SHA",
      warnings: [],
      evidence: [],
      events: [],
      disclosures: ["PREVIEW_ONLY", "NOT_PRODUCTION_RESULT"]
    } as never);

    render(<OperationalSettingsWorkspace {...baseProps} source={realSource} realVideoReady />);
    await userEvent.click(await screen.findByRole("button", { name: "Run real-video preview" }));

    const payload = createPreview.mock.calls[0]?.[1];
    expect(payload).toMatchObject({
      mode: "REAL_VIDEO",
      processing_configuration_revision_id: "config-real",
      start_pts_ms: 5_000,
      end_pts_ms: 35_000,
      expected_scene_version: 1,
      created_by: "operator"
    });
    expect(payload).not.toHaveProperty("fixture_id");
    expect(await screen.findByText("Real-video preview")).toBeInTheDocument();
  });

  it("blocks a real-video preview when the authoritative runtime is unavailable", async () => {
    vi.spyOn(api, "processingProfiles").mockResolvedValue([balancedProfile]);
    vi.spyOn(api, "listPreviewRuns").mockResolvedValue([]);
    const createPreview = vi.spyOn(api, "createPreviewRun");

    render(<OperationalSettingsWorkspace {...baseProps} source={realSource} />);

    const button = await screen.findByRole("button", { name: "Run real-video preview" });
    expect(button).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Real-video preview is unavailable: start or restart the processing worker and wait for a fresh heartbeat.");
    expect(createPreview).not.toHaveBeenCalled();
  });

  it("keeps the approved fixture on the explicit SYNTHETIC preview path", async () => {
    vi.spyOn(api, "processingProfiles").mockResolvedValue([balancedProfile]);
    vi.spyOn(api, "listPreviewRuns").mockResolvedValue([]);
    vi.spyOn(api, "createProcessingConfiguration").mockResolvedValue(validConfiguration("config-synthetic"));
    const createPreview = vi.spyOn(api, "createPreviewRun").mockResolvedValue({
      id: "preview-synthetic",
      project_id: "prj_1",
      source_id: "source-synthetic",
      source_fingerprint_sha256: "mock-full-file-sha256",
      scene_revision: "1",
      scene_semantic_hash: "scene-hash",
      start_pts_ms: 0,
      end_pts_ms: 30_000,
      processing_configuration_revision_id: "config-synthetic",
      run_type: "PREVIEW_ONLY",
      mode: "SYNTHETIC",
      status: "COMPLETED",
      created_by: "operator",
      created_at: "now",
      completed_at: "now",
      ownership_state: "TERMINAL",
      statistics: { event_count: 1 },
      warnings: [],
      evidence: [],
      events: [],
      disclosures: ["PREVIEW_ONLY", "NOT_PRODUCTION_RESULT", "SYNTHETIC_ENGINEERING_ONLY"]
    } as never);

    render(<OperationalSettingsWorkspace {...baseProps} source={syntheticSource} />);
    await userEvent.click(await screen.findByRole("button", { name: "Run synthetic validation preview" }));

    expect(createPreview.mock.calls[0]?.[1]).toMatchObject({
      mode: "SYNTHETIC",
      fixture_id: "api-acceptance",
      processing_configuration_revision_id: "config-synthetic",
      start_pts_ms: 0,
      end_pts_ms: 30_000
    });
  });
});
