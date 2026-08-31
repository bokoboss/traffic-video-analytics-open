import {
  AlertTriangle,
  ArrowLeftRight,
  Command,
  Download,
  FileVideo,
  Image,
  Languages,
  ListChecks,
  MousePointer2,
  Play,
  Redo2,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Settings2,
  Trash2,
  Undo2
} from "lucide-react";
import { ChangeEvent, CSSProperties, PointerEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, ProjectSnapshot, ReadinessResponse } from "./api/client";
import { BenchmarkWorkspace } from "./BenchmarkWorkspace";
import { OperationalSettingsWorkspace } from "./OperationalSettingsWorkspace";
import { ReviewWorkspace } from "./ReviewWorkspace";
import { copy, Language } from "./copy";
import { enforceMinimumLineLength, viewportPointToNormalized } from "./sceneGeometry";
import type { BenchmarkOverviewOut, EngineeringSummaryOut } from "./api/generated";

type LoadState = "loading" | "ready" | "empty" | "error" | "saving" | "stale" | "blocked";
type SelectedObject = { type: "roi"; id: string } | { type: "line"; id: string };
type DragTarget =
  | { type: "line-point"; id: string; part: "start" | "end" }
  | { type: "roi-vertex"; id: string; index: number }
  | null;
type Point = { x: number; y: number };
type CountingLine = {
  id: string;
  name: string;
  active: boolean;
  start: Point;
  end: Point;
  direction_mode: "A_TO_B" | "B_TO_A" | "BIDIRECTIONAL";
  side_a_name: string;
  side_b_name: string;
  approach_name: string;
  movement_name: string;
  analyst_note: string;
};
type Roi = { id: string; name: string; active: boolean; vertices: Point[] };
type Geometry = {
  schema_version: "scene-geometry-v2";
  coordinate_system: { origin: "top_left"; units: "normalized_source_display" };
  counting_lines: CountingLine[];
  rois: Roi[];
};
type ProjectSummary = Record<string, unknown>;
type ProgressStage = { label: string; detail: string; busy: boolean } | null;
type StartupPhaseState = "waiting" | "ready" | "degraded" | "failed";
type StartupPhase = { id: string; label: string; detail: string; state: StartupPhaseState };
type ProcessingJob = Record<string, unknown>;
type ProcessingMode = "SYNTHETIC" | "REAL_VIDEO";
const ACTIVE_JOB_STATES = new Set(["CREATED", "QUEUED", "STARTING", "RUNNING", "CANCELLATION_REQUESTED"]);
const TERMINAL_JOB_STATES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

export function takeSelectedUpload(input: HTMLInputElement): File | null {
  const file = input.files?.[0] ?? null;
  if (file) input.value = "";
  return file;
}

export function App() {
  const [language, setLanguage] = useState<Language>("th");
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [benchmarkView, setBenchmarkView] = useState(false);
  const [operationalSettingsView, setOperationalSettingsView] = useState(false);
  const [benchmarkState, setBenchmarkState] = useState<"loading" | "ready" | "empty" | "error">("empty");
  const [benchmarkOverview, setBenchmarkOverview] = useState<BenchmarkOverviewOut | null>(null);
  const [benchmarkError, setBenchmarkError] = useState("");
  const [message, setMessage] = useState("");
  const [projectId, setProjectId] = useState<string | null>(() => window.localStorage.getItem("tva.projectId"));
  const [projectList, setProjectList] = useState<ProjectSummary[]>([]);
  const [snapshot, setSnapshot] = useState<ProjectSnapshot | null>(null);
  const [crossingEvents, setCrossingEvents] = useState<Record<string, unknown>[]>([]);
  const [aggregate, setAggregate] = useState<Record<string, unknown> | null>(null);
  const [reviewedTotals, setReviewedTotals] = useState<Record<string, unknown> | null>(null);
  const [engineering, setEngineering] = useState<EngineeringSummaryOut | null>(null);
  const [activeProcessingJob, setActiveProcessingJob] = useState<ProcessingJob | null>(null);
  const [processingMode, setProcessingMode] = useState<ProcessingMode>("SYNTHETIC");
  const [processingConfigurationRevisionId, setProcessingConfigurationRevisionId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState("Rama IX traffic study");
  const [location, setLocation] = useState("Bangkok");
  const [timezoneName, setTimezoneName] = useState("Asia/Bangkok");
  const [startTime, setStartTime] = useState("2026-01-01T07:00");
  const [analysisStart, setAnalysisStart] = useState(0);
  const [analysisEnd, setAnalysisEnd] = useState(3_600_000);
  const [fileName, setFileName] = useState("");
  const [runtime, setRuntime] = useState<Record<string, unknown> | null>(null);
  const [startupReadiness, setStartupReadiness] = useState<ReadinessResponse | null>(null);
  const [geometry, setGeometry] = useState<Geometry>(() => defaultGeometry());
  const [selectedObject, setSelectedObject] = useState<SelectedObject | null>({ type: "line", id: "line_main" });
  const [history, setHistory] = useState<Geometry[]>([]);
  const [redoStack, setRedoStack] = useState<Geometry[]>([]);
  const [sceneDirty, setSceneDirty] = useState(false);
  const [sceneMessage, setSceneMessage] = useState("");
  const [progressStage, setProgressStage] = useState<ProgressStage>(null);
  const loadSequence = useRef(0);
  const mediaRequestAbort = useRef<AbortController | null>(null);
  const mediaRequestSequence = useRef(0);
  const historyCapturedForDrag = useRef(false);
  const dragTargetRef = useRef<DragTarget>(null);
  const dragStartGeometry = useRef<Geometry | null>(null);
  const dragPoint = useRef<Point | null>(null);
  const dragAnimationFrame = useRef<number | null>(null);
  const dragMetrics = useRef({ startedAt: 0, pointerEvents: 0, renderedUpdates: 0, coalescedUpdates: 0, maxUpdateDelayMs: 0, lastEventAt: 0 });
  const jobPollInFlight = useRef(false);
  const analysisStartInputRef = useRef<HTMLInputElement>(null);
  const analysisEndInputRef = useRef<HTMLInputElement>(null);
  const t = copy[language];

  const stale = Boolean(snapshot?.project.stale);
  const hasActiveProject = Boolean(projectId && snapshot);
  const busy = loadState === "saving" || (loadState === "loading" && hasActiveProject) || Boolean(progressStage?.busy);
  const runId = snapshot?.run?.id && snapshot.run.result_ready ? String(snapshot.run.id) : undefined;
  const latestJobs = snapshot?.jobs ?? [];
  const unresolved = snapshot?.review.unresolved_mandatory_qc ?? 0;
  const certified = Boolean(snapshot?.certification);
  const exported = Boolean(snapshot?.export);
  const referenceFrameId = snapshot?.reference_frame?.id as string | undefined;
  const previewUrl = projectId && referenceFrameId ? api.referenceFramePreviewUrl(projectId, referenceFrameId) : "";
  const videoUrl = projectId && snapshot?.source?.source_type === "local_upload" ? api.sourceVideoUrl(projectId) : "";
  const referenceFrameWidth = Number(snapshot?.reference_frame?.preview_width ?? snapshot?.source?.width ?? 16);
  const referenceFrameHeight = Number(snapshot?.reference_frame?.preview_height ?? snapshot?.source?.height ?? 9);
  const frameAspectRatio = referenceFrameWidth > 0 && referenceFrameHeight > 0 ? `${referenceFrameWidth} / ${referenceFrameHeight}` : "16 / 9";
  const sceneVersion = Number(snapshot?.scene?.version ?? 0);
  const selectedLine = selectedObject?.type === "line" ? geometry.counting_lines.find((line) => line.id === selectedObject.id) ?? null : null;
  const selectedRoi = selectedObject?.type === "roi" ? geometry.rois.find((roi) => roi.id === selectedObject.id) ?? null : null;
  const showStartupShell = (loadState === "loading" || loadState === "error") && !hasActiveProject;

  const steps = useMemo(() => [t.create, t.source, t.scene, t.aiTrial, t.review, t.results], [t]);

  useEffect(() => {
    void load();
    return () => mediaRequestAbort.current?.abort();
  }, []);

  useEffect(() => {
    const jobId = activeProcessingJob?.id ? String(activeProcessingJob.id) : "";
    if (!projectId || !jobId || !ACTIVE_JOB_STATES.has(String(activeProcessingJob?.job_state))) return;
    let cancelled = false;
    const interval = window.setInterval(() => {
      if (jobPollInFlight.current) return;
      jobPollInFlight.current = true;
      void api.getProcessingJobProgress(jobId)
        .then(async (job) => {
          if (cancelled) return;
          setActiveProcessingJob(job);
          if (TERMINAL_JOB_STATES.has(String(job.job_state))) {
            window.clearInterval(interval);
            await load(projectId);
          }
        })
        .catch((error) => {
          if (!cancelled) setMessage(error instanceof ApiError ? error.message : "processing_poll_failed");
        })
        .finally(() => {
          jobPollInFlight.current = false;
        });
    }, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [activeProcessingJob?.id, activeProcessingJob?.job_state, projectId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const element = event.target as HTMLElement | null;
      if (element?.closest("input, textarea, select")) return;
      if ((event.key === "Delete" || event.key === "Backspace") && selectedObject) {
        event.preventDefault();
        deleteSelectedObject();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        undoGeometry();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
        event.preventDefault();
        redoGeometry();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedObject, geometry, history, redoStack]);

  async function load(id = projectId) {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoadState("loading");
    setMessage("");
    setStartupReadiness(null);
    try {
      const readiness = await api.readiness();
      if (sequence !== loadSequence.current) return;
      setStartupReadiness(readiness);
      setRuntime((readiness.processing.media_runtime as Record<string, unknown> | undefined) ?? null);
      const projects = await api.listProjects();
      setProjectList(projects);
      const selectedId = id ?? (projects[0]?.id as string | undefined);
      if (!selectedId) {
        if (sequence !== loadSequence.current) return;
        setProjectId(null);
        setSnapshot(null);
        setLoadState("empty");
        return;
      }
      const next = await api.getProject(selectedId);
      if (sequence !== loadSequence.current) return;
      setProjectId(selectedId);
      if (projectId !== selectedId) setProcessingConfigurationRevisionId(null);
      const persistedConfigurationId = next.active_job?.processing_configuration_revision_id ?? next.run?.processing_configuration_revision_id;
      setProcessingConfigurationRevisionId(persistedConfigurationId ? String(persistedConfigurationId) : null);
      window.localStorage.setItem("tva.projectId", selectedId);
      setSnapshot(next);
      setActiveProcessingJob((next.active_job as ProcessingJob | null) ?? null);
      const persistedMode = String(next.active_job?.processing_mode ?? next.run?.processing_mode ?? "SYNTHETIC") as ProcessingMode;
      if (persistedMode === "SYNTHETIC" || persistedMode === "REAL_VIDEO") setProcessingMode(persistedMode);
      setProjectName(String(next.project.name ?? ""));
      setLocation(String(next.project.location ?? ""));
      if (next.source) {
        setFileName(String(next.source.file_name ?? ""));
        setTimezoneName(String(next.source.timezone_name ?? "Asia/Bangkok"));
        setAnalysisStart(Number(next.source.analysis_start_pts_ms ?? 0));
        setAnalysisEnd(Number(next.source.analysis_end_pts_ms ?? 3_600_000));
      }
      if (next.run?.id && next.run.result_ready) {
        const nextRunId = String(next.run.id);
        // Keep the bounded automatic-event read in the load contract. The
        // 6E workspace consumes the engineering projection, while the legacy
        // result endpoints remain available for existing project snapshots.
        await api.listEvents(nextRunId);
        setCrossingEvents(await api.listCrossingEvents(nextRunId));
        setAggregate(await api.getAggregates(nextRunId));
        setReviewedTotals(await api.getReviewedTotals(nextRunId));
        try {
          setEngineering(await api.getEngineeringSummary(nextRunId));
        } catch (error) {
          if (!(error instanceof ApiError) || ![404, 409].includes(error.status)) throw error;
          setEngineering(null);
        }
      } else {
        setCrossingEvents([]);
        setAggregate(null);
        setReviewedTotals(null);
        setEngineering(null);
      }
      const parsedGeometry = parseGeometry(next.scene?.geometry_json);
      setGeometry(parsedGeometry);
      setSelectedObject(parsedGeometry.counting_lines[0] ? { type: "line", id: parsedGeometry.counting_lines[0].id } : parsedGeometry.rois[0] ? { type: "roi", id: parsedGeometry.rois[0].id } : null);
      setHistory([]);
      setRedoStack([]);
      setSceneDirty(false);
      setLoadState(next.project.stale ? "stale" : "ready");
    } catch (error) {
      setLoadState("error");
      setMessage(error instanceof ApiError ? error.message : "api_failure");
    }
  }

  async function loadBenchmarkOverview() {
    setBenchmarkState("loading");
    setBenchmarkError("");
    try {
      const overview = await api.benchmarkOverview();
      setBenchmarkOverview(overview);
      setBenchmarkState(overview.status === "EMPTY" ? "empty" : "ready");
    } catch (error) {
      setBenchmarkState("error");
      setBenchmarkError(error instanceof ApiError ? error.message : "benchmark_overview_failed");
    }
  }

  async function createProject() {
    await guardedSave(async () => {
      const project = await api.createProject({ name: projectName, location, study_type: "intersection", language });
      await load(String(project.id));
    });
  }

  async function openProject(id: string) {
    if (!id || id === projectId) return;
    if (sceneDirty && !window.confirm(t.abandonSceneChanges)) return;
    await load(id);
  }

  async function selectCurrentRun(nextRunId: string) {
    if (!projectId || !nextRunId || nextRunId === String(snapshot?.selected_run_id ?? snapshot?.run?.id ?? "")) return;
    await guardedSave(async () => {
      await api.selectCurrentProcessingRun(projectId, nextRunId, "operator");
      await load(projectId);
    });
  }

  async function saveProject() {
    if (!snapshot || !projectId) return;
    await guardedSave(async () => {
      const project = await api.updateProject(projectId, {
        name: projectName,
        location,
        study_type: String(snapshot.project.study_type ?? "intersection"),
        language,
        expected_version: Number(snapshot.project.version)
      });
      setSnapshot({ ...snapshot, project });
      setLoadState("ready");
    });
  }

  async function registerMockSource() {
    if (!projectId) return;
    const selectedAnalysisStart = Number(analysisStartInputRef.current?.value ?? analysisStart);
    const selectedAnalysisEnd = Number(analysisEndInputRef.current?.value ?? analysisEnd);
    await guardedSave(async () => {
      await api.registerSource({
        project_id: projectId,
        file_name: "approved-mock.mp4",
        fingerprint_sha256: "mock-full-file-sha256",
        source_started_at: new Date(startTime).toISOString(),
        timezone_name: timezoneName,
        analysis_start_pts_ms: selectedAnalysisStart,
        analysis_end_pts_ms: selectedAnalysisEnd
      });
      await load(projectId);
    });
  }

  async function uploadSource(event: ChangeEvent<HTMLInputElement>) {
    const file = takeSelectedUpload(event.currentTarget);
    if (!file || !projectId) return;
    await guardedSave(async () => {
      setProgressStage({ label: t.uploadingVideo, detail: file.name, busy: true });
      const source = await api.uploadSource(projectId, file);
      setFileName(file.name);
      await prepareReferenceImage(projectId, source, t.referencePreparingAfterUpload);
      await load(projectId);
    });
  }

  async function saveTimeConfiguration() {
    if (!projectId) return;
    const selectedAnalysisStart = Number(analysisStartInputRef.current?.value ?? analysisStart);
    const selectedAnalysisEnd = Number(analysisEndInputRef.current?.value ?? analysisEnd);
    setAnalysisStart(selectedAnalysisStart);
    setAnalysisEnd(selectedAnalysisEnd);
    if (selectedAnalysisEnd <= selectedAnalysisStart) {
      setMessage("analysis range must use a positive half-open [start, end) interval");
      setLoadState("error");
      return;
    }
    await guardedSave(async () => {
      setProgressStage({ label: t.validatingTime, detail: t.referencePreparingForWindow, busy: true });
      const next = await api.updateTimeConfiguration(projectId, {
        source_started_at: new Date(startTime).toISOString(),
        timezone_name: timezoneName,
        analysis_start_pts_ms: selectedAnalysisStart,
        analysis_end_pts_ms: selectedAnalysisEnd,
        source_offset_ms: 0
      });
      if (next.source) await prepareReferenceImage(projectId, next.source, t.referencePreparingForWindow, true);
      await load(projectId);
    });
  }

  async function extractReferenceFrame(force = false) {
    if (!projectId || !snapshot?.source) return;
    const source = snapshot.source;
    await guardedSave(async () => {
      await prepareReferenceImage(projectId, source, t.referencePreparingManual, force);
      await load(projectId);
    });
  }

  async function prepareReferenceImage(projectId: string, source: Record<string, unknown>, detail: string, force = false) {
    mediaRequestAbort.current?.abort();
    const controller = new AbortController();
    mediaRequestAbort.current = controller;
    const sequence = mediaRequestSequence.current + 1;
    mediaRequestSequence.current = sequence;
    const start = Number(source.analysis_start_pts_ms ?? source.analysis_window_start_ms ?? 0);
    const explicitEnd = Number(source.analysis_end_pts_ms ?? source.analysis_window_end_ms ?? NaN);
    const duration = Number(source.duration_ms ?? NaN);
    const end = Number.isFinite(explicitEnd) && explicitEnd > start ? explicitEnd : Number.isFinite(duration) ? duration : start;
    const midpoint = Math.max(0, Math.round(start + Math.max(0, end - start) / 2));
    setProgressStage({ label: t.preparingReferenceImage, detail, busy: true });
    try {
      await api.extractReferenceFrame(
        projectId,
        { mode: "timestamp", requested_pts_ms: midpoint, force_regenerate: force },
        { signal: controller.signal }
      );
    } catch (error) {
      if (error instanceof ApiError && error.message === "request_cancelled") return;
      throw error;
    }
    if (sequence !== mediaRequestSequence.current) return;
    setProgressStage({ label: t.referenceReady, detail: `${midpoint} PTS ms`, busy: false });
  }

  async function saveSceneGeometry() {
    if (!projectId || !referenceFrameId) return;
    await guardedSave(async () => {
      const validation = await api.validateSceneConfiguration(projectId, {
        expected_version: sceneVersion || undefined,
        reference_frame_id: referenceFrameId,
        template: "intersection",
        geometry
      });
      if (!validation.valid) {
        setSceneMessage(validation.errors.join("; "));
        throw new ApiError(422, "scene_validation_failed");
      }
      setSceneMessage("");
      const savedScene = await api.saveSceneConfiguration(projectId, {
        expected_version: sceneVersion || undefined,
        reference_frame_id: referenceFrameId,
        template: "intersection",
        geometry
      });
      setSceneDirty(false);
      const next = await api.getProject(projectId);
      setSnapshot({ ...next, scene: savedScene });
      setLoadState(next.project.stale ? "stale" : "ready");
    });
  }

  async function configureSceneAndRun(runAgain = false) {
    if (!projectId || !snapshot?.scene) return;
    await guardedSave(async () => {
      const realReadiness = startupReadiness?.processing?.real_inference as { ready?: boolean; detail?: string } | undefined;
      if (processingMode === "REAL_VIDEO" && !realReadiness?.ready) {
        setMessage(realReadiness?.detail ?? t.realVideoBlocked);
        return;
      }
      if (processingMode === "REAL_VIDEO" && !processingConfigurationRevisionId) {
        setMessage(t.realVideoConfigRequired);
        return;
      }
      const sourceId = String(snapshot.source?.id ?? "no-source");
      const sceneId = String(snapshot.scene?.id ?? "no-scene");
      const identity = processingMode === "REAL_VIDEO" ? processingConfigurationRevisionId : "api-acceptance";
      const stableKey = `${processingMode.toLowerCase()}:${projectId}:${sourceId}:${sceneId}:${identity}`;
      const payload: Record<string, unknown> = {
        mode: processingMode,
        expected_scene_version: sceneVersion || undefined,
        idempotency_key: runAgain ? `${stableKey}:${Date.now()}` : stableKey,
        run_again: runAgain,
        auto_start: processingMode === "SYNTHETIC"
      };
      if (processingMode === "REAL_VIDEO") {
        payload.processing_configuration_revision_id = processingConfigurationRevisionId;
      } else {
        payload.fixture_id = "api-acceptance";
      }
      const job = await api.createProcessingJob(projectId, payload);
      setActiveProcessingJob(job);
      setMessage(processingMode === "REAL_VIDEO" ? t.realVideoDisclaimer : t.syntheticProcessingDisclaimer);
      if (TERMINAL_JOB_STATES.has(String(job.job_state))) {
        await load(projectId);
      } else {
        const next = await api.getProject(projectId);
        setSnapshot(next);
      }
    });
  }

  async function cancelProcessingJob() {
    const jobId = activeProcessingJob?.id ? String(activeProcessingJob.id) : "";
    if (!jobId) return;
    await guardedSave(async () => {
      const job = await api.cancelProcessingJob(jobId, "operator_cancelled");
      setActiveProcessingJob(job);
      await load(projectId ?? undefined);
    });
  }

  async function certify() {
    if (!projectId || !runId) return;
    await guardedSave(async () => {
      await api.certify(projectId, runId);
      await load(projectId);
    });
  }

  async function exportManifest() {
    if (!projectId || !runId) return;
    await guardedSave(async () => {
      await api.exportManifest(projectId, runId, "csv");
      await load(projectId);
    });
  }

  async function guardedSave(action: () => Promise<void>) {
    setLoadState("saving");
    setMessage("");
    setProgressStage(null);
    try {
      await action();
    } catch (error) {
      const normalized = error instanceof ApiError ? error.message : "persistence_failure";
      setMessage(normalized);
      setLoadState(error instanceof ApiError && error.status === 409 ? "stale" : "error");
    } finally {
      setProgressStage((stage) => (stage?.busy ? null : stage));
    }
  }

  function replaceGeometry(next: Geometry, options: { record?: boolean; select?: SelectedObject | null } = {}) {
    if (options.record !== false) {
      setHistory((items) => [...items.slice(-19), geometry]);
      setRedoStack([]);
    }
    setGeometry(next);
    setSceneDirty(true);
    if (options.select !== undefined) setSelectedObject(options.select);
  }

  function addLine() {
    const id = `line_${Date.now()}`;
    replaceGeometry(
      {
        ...geometry,
        counting_lines: [
          ...geometry.counting_lines,
          {
            id,
            name: `${t.defaultLineName} ${geometry.counting_lines.length + 1}`,
            active: true,
            start: { x: 0.26, y: 0.64 },
            end: { x: 0.74, y: 0.36 },
            direction_mode: "BIDIRECTIONAL",
            side_a_name: t.sideALabelDefault,
            side_b_name: t.sideBLabelDefault,
            approach_name: "",
            movement_name: "",
            analyst_note: ""
          }
        ]
      },
      { select: { type: "line", id } }
    );
  }

  function replaceRoi() {
    const id = "roi_main";
    replaceGeometry(
      {
        ...geometry,
        rois: [
          {
            id,
            name: t.defaultRoiName,
            active: true,
            vertices: [{ x: 0.12, y: 0.16 }, { x: 0.88, y: 0.16 }, { x: 0.84, y: 0.84 }, { x: 0.16, y: 0.82 }]
          }
        ]
      },
      { select: { type: "roi", id } }
    );
  }

  function deleteSelectedObject() {
    if (!selectedObject) return;
    if (selectedObject.type === "line") {
      const nextLines = geometry.counting_lines.filter((line) => line.id !== selectedObject.id);
      replaceGeometry({ ...geometry, counting_lines: nextLines }, { select: nextLines[0] ? { type: "line", id: nextLines[0].id } : geometry.rois[0] ? { type: "roi", id: geometry.rois[0].id } : null });
    }
    if (selectedObject.type === "roi") {
      replaceGeometry({ ...geometry, rois: [] }, { select: geometry.counting_lines[0] ? { type: "line", id: geometry.counting_lines[0].id } : null });
    }
  }

  function updateSelectedLine(patch: Partial<CountingLine>) {
    if (!selectedLine) return;
    replaceGeometry({
      ...geometry,
      counting_lines: geometry.counting_lines.map((line) => (line.id === selectedLine.id ? { ...line, ...patch } : line))
    });
  }

  function updateSelectedRoi(patch: Partial<Roi>) {
    if (!selectedRoi) return;
    replaceGeometry({ ...geometry, rois: geometry.rois.map((roi) => (roi.id === selectedRoi.id ? { ...roi, ...patch } : roi)) });
  }

function swapSelectedDirections() {
    if (!selectedLine) return;
  updateSelectedLine({
      side_a_name: selectedLine.side_b_name,
      side_b_name: selectedLine.side_a_name
    });
  }

  function undoGeometry() {
    const previous = history.at(-1);
    if (!previous) return;
    setRedoStack((items) => [geometry, ...items.slice(0, 19)]);
    setHistory((items) => items.slice(0, -1));
    setGeometry(previous);
    setSceneDirty(true);
  }

  function redoGeometry() {
    const next = redoStack[0];
    if (!next) return;
    setHistory((items) => [...items.slice(-19), geometry]);
    setRedoStack((items) => items.slice(1));
    setGeometry(next);
    setSceneDirty(true);
  }

  function beginDrag(target: DragTarget) {
    dragTargetRef.current = target;
    dragStartGeometry.current = geometry;
    dragMetrics.current = { startedAt: performance.now(), pointerEvents: 0, renderedUpdates: 0, coalescedUpdates: 0, maxUpdateDelayMs: 0, lastEventAt: 0 };
    historyCapturedForDrag.current = false;
    if (target?.type === "line-point") setSelectedObject({ type: "line", id: target.id });
    if (target?.type === "roi-vertex") setSelectedObject({ type: "roi", id: target.id });
  }

  function moveHandle(event: PointerEvent<SVGSVGElement>) {
    const target = dragTargetRef.current;
    if (!target) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const eventAt = performance.now();
    dragMetrics.current.pointerEvents += 1;
    dragMetrics.current.lastEventAt = eventAt;
    dragPoint.current = viewportPointToNormalized({ x: event.clientX, y: event.clientY }, rect);
    if (dragAnimationFrame.current !== null) {
      dragMetrics.current.coalescedUpdates += 1;
      return;
    }
    dragAnimationFrame.current = window.requestAnimationFrame(() => {
      dragAnimationFrame.current = null;
      const updateDelay = performance.now() - dragMetrics.current.lastEventAt;
      dragMetrics.current.renderedUpdates += 1;
      dragMetrics.current.maxUpdateDelayMs = Math.max(dragMetrics.current.maxUpdateDelayMs, updateDelay);
      const nextPoint = dragPoint.current;
      const currentTarget = dragTargetRef.current;
      if (!nextPoint || !currentTarget) return;
      if (!historyCapturedForDrag.current) {
        setHistory((items) => [...items.slice(-19), dragStartGeometry.current ?? geometry]);
        setRedoStack([]);
        historyCapturedForDrag.current = true;
      }
      setSceneDirty(true);
      setGeometry((current) => applyDragGeometry(current, currentTarget, nextPoint));
    });
  }

  function endDrag() {
    if (dragAnimationFrame.current !== null) {
      window.cancelAnimationFrame(dragAnimationFrame.current);
      dragAnimationFrame.current = null;
      const target = dragTargetRef.current;
      const point = dragPoint.current;
      if (target && point) setGeometry((current) => applyDragGeometry(current, target, point));
    }
    if (dragMetrics.current.startedAt > 0) {
      recordFrontendDiagnostic("endpoint_drag_ended", {
        duration_ms: Math.round(performance.now() - dragMetrics.current.startedAt),
        pointer_events: dragMetrics.current.pointerEvents,
        rendered_updates: dragMetrics.current.renderedUpdates,
        coalesced_updates: dragMetrics.current.coalescedUpdates,
        max_update_delay_ms: Math.round(dragMetrics.current.maxUpdateDelayMs)
      });
    }
    dragTargetRef.current = null;
    dragStartGeometry.current = null;
    dragPoint.current = null;
    historyCapturedForDrag.current = false;
  }

  return (
    <div className="app" lang={language}>
      <header className="topbar">
        <div className="brand" aria-label={t.product}>
          <span className="brand-mark">TVA</span>
          <div>
            <strong>{t.product}</strong>
            <span>{t.pilotRelease} · {startupReadiness?.release?.release_version ?? "0.1.0-pilot"}</span>
            <span>{projectName || t.noProject}</span>
          </div>
        </div>
        <div className="topbar-state">
          <StatusBadge
            tone={benchmarkView ? (benchmarkState === "error" || benchmarkState === "empty" ? "warning" : benchmarkState === "loading" ? "info" : "success") : loadState === "error" || loadState === "blocked" || stale ? "warning" : "success"}
            label={benchmarkView ? benchmarkStateLabel(benchmarkState, t) : stateLabel(loadState, stale, sceneDirty, t)}
          />
          <span className="save-state"><Save size={16} aria-hidden="true" /> {loadState === "saving" ? t.saving : sceneDirty ? t.unsavedChanges : t.save}</span>
        </div>
        <div className="topbar-actions">
          <button className="icon-button" aria-label={t.command}><Command size={18} /></button>
          <button className="icon-button" aria-label="Search"><Search size={18} /></button>
          <button className="secondary" onClick={() => { setOperationalSettingsView(true); setBenchmarkView(false); }}><Settings2 size={16} aria-hidden="true" />{language === "th" ? "การตั้งค่าการประมวลผล" : "Operational settings"}</button>
          <button className="secondary" onClick={() => { setBenchmarkView(true); void loadBenchmarkOverview(); }}><ListChecks size={16} aria-hidden="true" />{t.benchmarkWorkspace}</button>
          <button className="secondary" onClick={() => setLanguage(language === "th" ? "en" : "th")}><Languages size={16} aria-hidden="true" />{language === "th" ? "EN" : "TH"}</button>
          <button className="primary" onClick={() => void load()} disabled={busy}><RefreshCw size={16} aria-hidden="true" />{t.retry}</button>
        </div>
      </header>

      <nav className="rail" aria-label="Workflow">
        {steps.map((step, index) => <button key={step} className={`rail-step ${stepClass(index, snapshot, unresolved, certified, exported)}`}><span className="step-index">{index + 1}</span><span>{step}</span></button>)}
      </nav>

      <main className="workspace">
        {benchmarkView ? (
          <BenchmarkWorkspace
            t={t}
            overview={benchmarkOverview}
            state={benchmarkState}
            error={benchmarkError}
            onReload={() => void loadBenchmarkOverview()}
            onBack={() => { setBenchmarkView(false); setOperationalSettingsView(false); }}
          />
        ) : operationalSettingsView ? (
          <OperationalSettingsWorkspace
            language={language}
            projectId={projectId}
            sceneVersion={sceneVersion}
            source={snapshot?.source ?? null}
            projectStale={stale}
            realVideoReady={Boolean((startupReadiness?.processing?.real_inference as { ready?: boolean } | undefined)?.ready)}
            realVideoBlockReason={String((startupReadiness?.processing?.real_inference as { detail?: string } | undefined)?.detail ?? t.realVideoBlocked)}
            onBack={() => setOperationalSettingsView(false)}
            onConfigurationSaved={setProcessingConfigurationRevisionId}
          />
        ) : (
          <>
            {message && <StateBanner title={t.errorTitle} body={message} action={<button className="secondary" onClick={() => void load()} disabled={busy}>{t.retry}</button>} />}
            {showStartupShell && (
              <StartupShell
                t={t}
                phases={startupPhases(startupReadiness, loadState === "error" ? message : "", t)}
                failed={loadState === "error"}
                onRetry={() => void load()}
                busy={busy}
              />
            )}
            {loadState === "loading" && hasActiveProject && <StateBanner title={t.loading} body={t.loadingBody} />}
            {progressStage && <StateBanner title={progressStage.label} body={progressStage.detail} />}
            {stale && <StateBanner title={t.stateStale} body={t.staleBody} />}

            {!hasActiveProject && !showStartupShell ? (
              <Onboarding
                t={t}
                busy={busy}
                projectName={projectName}
                location={location}
                projects={projectList}
                setProjectName={setProjectName}
                setLocation={setLocation}
                createProject={createProject}
                openProject={openProject}
              />
            ) : hasActiveProject ? (
              <>
            <section className="evidence" aria-labelledby="workspace-title">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">PTS {analysisStart} - {analysisEnd} ms / {timezoneName}</p>
                  <h1 id="workspace-title">{t.workspaceTitle}</h1>
                </div>
                <StatusBadge tone={referenceFrameId ? "success" : "warning"} label={referenceFrameId ? t.referenceReady : t.referenceMissing} />
              </div>
              <div className="synthetic-disclaimer"><strong>{processingMode === "REAL_VIDEO" ? t.realVideoMode : t.synthetic}</strong><span>{processingMode === "REAL_VIDEO" ? t.realVideoDisclaimer : t.syntheticDisclaimer}</span></div>
              <div className="setup-grid">
                <div className="setup-form">
                  <label>{t.projectName}<input value={projectName} onChange={(event) => setProjectName(event.target.value)} /></label>
                  <label>{t.location}<input value={location} onChange={(event) => setLocation(event.target.value)} /></label>
                  <div className="action-row">
                    <button className="primary" onClick={createProject} disabled={busy}>{t.create}</button>
                    <button className="secondary" disabled={busy || !snapshot} onClick={saveProject}>{t.saveProject}</button>
                    <select aria-label={t.openProject} value={projectId ?? ""} onChange={(event) => void openProject(event.target.value)}>
                      {projectList.map((project) => <option key={String(project.id)} value={String(project.id)}>{String(project.name ?? project.id)}</option>)}
                    </select>
                  </div>
                  <label className="file-control"><FileVideo size={18} />{t.selectVideo}<input type="file" accept="video/*" disabled={busy || !projectId} onChange={(event) => void uploadSource(event)} /></label>
                  <button className="secondary" disabled={busy || !projectId} onClick={registerMockSource}>{t.mockSource}</button>
                </div>
                <div className="setup-form">
                  <label>{t.startTime}<input type="datetime-local" value={startTime} onChange={(event) => setStartTime(event.target.value)} /></label>
                  <label>{t.timezone}<input value={timezoneName} onChange={(event) => setTimezoneName(event.target.value)} /></label>
                  <label>{t.analysisStart}<input ref={analysisStartInputRef} type="number" value={analysisStart} onChange={(event) => setAnalysisStart(Number(event.target.value))} /></label>
                  <label>{t.analysisEnd}<input ref={analysisEndInputRef} type="number" value={analysisEnd} onChange={(event) => setAnalysisEnd(Number(event.target.value))} /></label>
                  <button className="primary" disabled={busy || !snapshot?.source} onClick={saveTimeConfiguration}>{t.confirmTime}</button>
                </div>
              </div>
            </section>

            <section className="scene-editor" aria-labelledby="scene-title">
              <div className="section-heading compact">
                <div>
                  <p className="eyebrow">{t.sceneCoordinateContract}</p>
                  <h2 id="scene-title">{t.sceneEditorTitle}</h2>
                </div>
                <StatusBadge tone={sceneDirty ? "warning" : snapshot?.scene ? "success" : "info"} label={sceneDirty ? t.unsavedChanges : snapshot?.scene ? t.sceneSaved : t.sceneNeedsSetup} />
              </div>
              {sceneMessage && <StateBanner title={t.errorTitle} body={sceneMessage} />}
              {!runtime?.reference_frame_extraction_available && <StateBanner title={t.mediaRuntimeUnavailable} body={t.mediaRuntimeBody} />}
              <div className="scene-layout">
                <div className="scene-stage">
                  <div className="scene-toolbar" aria-label={t.sceneTools}>
                    <button className="secondary" disabled={busy || !snapshot?.source} onClick={() => void extractReferenceFrame(true)}><Image size={16} />{t.refreshReferenceImage}</button>
                    <button className="secondary" disabled={busy || !referenceFrameId} onClick={replaceRoi}><MousePointer2 size={16} />{geometry.rois[0] ? t.replaceDetectionArea : t.createDetectionArea}</button>
                    <button className="secondary" disabled={busy || !referenceFrameId} onClick={addLine}><ListChecks size={16} />{t.addLine}</button>
                    <button className="secondary" disabled={busy || history.length === 0} onClick={undoGeometry}><Undo2 size={16} />{t.undo}</button>
                    <button className="secondary" disabled={busy || redoStack.length === 0} onClick={redoGeometry}><Redo2 size={16} />{t.redo}</button>
                    <button className="secondary destructive" disabled={busy || !selectedObject} onClick={deleteSelectedObject}><Trash2 size={16} />{t.deleteSelected}</button>
                    <button className="primary" disabled={busy || !referenceFrameId || geometry.rois.length === 0 || geometry.counting_lines.length === 0} onClick={saveSceneGeometry}><Save size={16} />{t.sceneAction}</button>
                  </div>
                  <div className="reference-frame" style={{ "--frame-aspect": frameAspectRatio } as CSSProperties}>
                    {videoUrl ? (
                      <video
                        src={videoUrl}
                        poster={previewUrl || undefined}
                        preload="metadata"
                        controls
                        muted
                        aria-label={t.videoPreview}
                        onLoadedMetadata={(event) => recordFrontendDiagnostic("media_ready", { duration_seconds: event.currentTarget.duration })}
                      />
                    ) : previewUrl ? (
                      <img src={previewUrl} alt={t.referenceFrameAlt} onLoad={(event) => recordFrontendDiagnostic("image_decode_complete", { natural_width: event.currentTarget.naturalWidth, natural_height: event.currentTarget.naturalHeight })} />
                    ) : (
                      <div className="frame-placeholder">{t.referenceMissing}</div>
                    )}
                    <SceneOverlay geometry={geometry} selectedObject={selectedObject} beginDrag={beginDrag} moveHandle={moveHandle} endDrag={endDrag} setSelectedObject={setSelectedObject} t={t} />
                  </div>
                </div>
                <SceneInspector
                  t={t}
                  geometry={geometry}
                  selectedObject={selectedObject}
                  selectedLine={selectedLine}
                  selectedRoi={selectedRoi}
                  sceneDirty={sceneDirty}
                  setSelectedObject={setSelectedObject}
                  updateSelectedLine={updateSelectedLine}
                  updateSelectedRoi={updateSelectedRoi}
                  swapSelectedDirections={swapSelectedDirections}
                  deleteSelectedObject={deleteSelectedObject}
                />
              </div>
            </section>

            <ReviewAndResults
              t={t}
              busy={busy}
              crossingEvents={crossingEvents}
              aggregate={aggregate}
              reviewedTotals={reviewedTotals}
              engineering={engineering}
              activeProcessingJob={activeProcessingJob}
              jobs={latestJobs}
              snapshot={snapshot}
              processingMode={processingMode}
              setProcessingMode={setProcessingMode}
              realReady={Boolean((startupReadiness?.processing?.real_inference as { ready?: boolean } | undefined)?.ready)}
              realReadyDetail={String((startupReadiness?.processing?.real_inference as { detail?: string } | undefined)?.detail ?? t.realVideoBlocked)}
              configureSceneAndRun={configureSceneAndRun}
              cancelProcessingJob={cancelProcessingJob}
            />
            {projectId && runId && <ReviewWorkspace projectId={projectId} runId={runId} language={language} stale={stale} onRefresh={() => void load(projectId)} />}
              </>
            ) : null}
          </>
        )}
      </main>

      <aside className="inspector" aria-label={benchmarkView ? t.benchmarkWorkspace : "Context inspector"}>
        {benchmarkView ? (
          <>
            <h2>{t.benchmarkWorkspace}</h2>
            <dl>
              <dt>{t.benchmarkCorpus}</dt><dd className="mono">{benchmarkOverview?.corpus_revisions?.[0]?.revision ?? "unavailable"}</dd>
              <dt>{t.benchmarkSources}</dt><dd>{benchmarkOverview?.source_count ?? 0}</dd>
              <dt>{t.benchmarkRunStatus}</dt><dd>{benchmarkOverview?.latest_run?.status ?? t.benchmarkNoRun}</dd>
              <dt>{t.benchmarkQualificationStatus}</dt><dd>{benchmarkOverview?.latest_run?.qualification_status ?? "NOT_RUN"}</dd>
            </dl>
            {!benchmarkOverview?.latest_run && <p className="benchmark-disclosure">{t.benchmarkNoPolicy}</p>}
          </>
        ) : (
          <>
            <h2>{t.inspectorTitle}</h2>
            <dl>
              <dt>Project ID</dt><dd className="mono">{projectId ?? "none"}</dd>
              <dt>{t.selectVideo}</dt><dd>{fileName || t.evidenceUnavailable}</dd>
              <dt>Fingerprint</dt><dd className="mono">{String(snapshot?.source?.fingerprint_short ?? snapshot?.source?.fingerprint_sha256 ?? "unavailable")}</dd>
              <dt>{t.referenceImage}</dt><dd>{referenceFrameId ? t.referenceReady : t.referenceMissing}</dd>
              <dt>{t.scene}</dt><dd>{snapshot?.scene ? `${t.sceneSaved} v${sceneVersion}` : t.sceneNeedsSetup}</dd>
            </dl>
            {snapshot?.processing_runs && snapshot.processing_runs.length > 0 && (
              <div className="run-selector">
                <label htmlFor="current-processing-run">{t.currentRun}</label>
                <select
                  id="current-processing-run"
                  value={String(snapshot.selected_run_id ?? snapshot.run?.id ?? "")}
                  onChange={(event) => void selectCurrentRun(event.target.value)}
                  disabled={busy}
                >
                  {snapshot.processing_runs.map((item) => {
                    const itemId = String(item.id ?? "");
                    const ready = Boolean(item.result_ready);
                    return <option key={itemId} value={itemId} disabled={!ready}>{itemId} · {String(item.processing_mode ?? item.engine_mode ?? "run")} · {ready ? t.selectedRun : t.runNotReady}</option>;
                  })}
                </select>
                <p className="field-help">{t.runSelectionHelp}</p>
              </div>
            )}
            {snapshot?.stale_warnings?.map((warning) => <p className="field-help warning-text" key={warning}>{t.stateStale}: {warning}</p>)}
            {runId ? (
              <p className="inspector-workflow-note">{language === "th" ? "การรับรองและส่งออกอยู่ในพื้นที่ตรวจสอบด้านล่าง" : "Certification and export are controlled in the review workspace below."}</p>
            ) : (
              <>
                <button className="primary wide" onClick={certify} disabled={busy || !runId || unresolved > 0 || stale}><ShieldCheck size={16} />{t.certifyAction}</button>
                <button className="secondary wide" onClick={exportManifest} disabled={busy || !certified || stale}><Download size={16} />{t.exportAction}</button>
                {exported && <StatusBadge tone="success" label="manifest_created" />}
              </>
            )}
          </>
        )}
      </aside>

      <footer className="timeline" aria-label="Timeline">
        {benchmarkView ? (
          <><span className="mono">6C</span><div className="timeline-track"><span style={{ left: "25%" }} /><span style={{ left: "75%" }} /></div><span className="mono">source-relative PTS</span></>
        ) : (
          <><button className="icon-button" aria-label="Play"><Play size={18} /></button><div className="timeline-track"><span style={{ left: "25%" }} /><span style={{ left: "50%" }} /><span style={{ left: "75%" }} /><strong style={{ left: "50%" }} /></div><span className="mono">{timezoneName}</span></>
        )}
      </footer>
    </div>
  );
}

function StartupShell({ t, phases, failed, onRetry, busy }: {
  t: typeof copy[Language];
  phases: StartupPhase[];
  failed: boolean;
  onRetry: () => void;
  busy: boolean;
}) {
  return (
    <section className="startup-shell" aria-labelledby="startup-title" aria-live="polite">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t.startupEyebrow}</p>
          <h1 id="startup-title">{failed ? t.startupFailedTitle : t.startupTitle}</h1>
        </div>
        <StatusBadge tone={failed ? "warning" : "info"} label={failed ? t.startupFailed : t.startupChecking} />
      </div>
      <div className="startup-phase-list">
        {phases.map((phase) => (
          <div key={phase.id} className={`startup-phase ${phase.state}`}>
            <StatusBadge tone={phase.state === "ready" ? "success" : phase.state === "waiting" ? "info" : "warning"} label={startupStateLabel(phase.state, t)} />
            <div>
              <strong>{phase.label}</strong>
              <span>{phase.detail}</span>
            </div>
          </div>
        ))}
      </div>
      {failed && <button className="secondary" onClick={onRetry} disabled={busy}><RefreshCw size={16} />{t.retry}</button>}
    </section>
  );
}

function startupPhases(readiness: ReadinessResponse | null, failure: string, t: typeof copy[Language]): StartupPhase[] {
  if (!readiness) {
    return [
      { id: "frontend", label: t.startupFrontend, detail: t.startupFrontendReady, state: "ready" },
      {
        id: "backend",
        label: t.startupBackend,
        detail: failure || t.startupBackendWaiting,
        state: failure ? "failed" : "waiting"
      },
      { id: "storage", label: t.startupStorage, detail: t.startupStorageWaiting, state: "waiting" },
      { id: "runtime", label: t.startupVideoRuntime, detail: t.startupVideoRuntimeWaiting, state: "waiting" },
      { id: "ai", label: t.startupAiRuntime, detail: t.startupAiRuntimeWaiting, state: "waiting" },
      { id: "worker", label: t.startupWorker, detail: t.startupWorkerHeartbeatWaiting, state: "waiting" },
      { id: "processing", label: t.startupProcessing, detail: t.startupProcessingWaiting, state: "waiting" }
    ];
  }
  const mediaReady = Boolean(readiness.components?.ffmpeg?.ready && readiness.components?.ffprobe?.ready);
  const frontend = readiness.components?.frontend;
  const frontendState: StartupPhaseState = frontend?.ready ? "ready" : "degraded";
  const worker = readiness.processing.worker as { state?: string; detail?: string } | undefined;
  const workerComponent = readiness.components?.worker;
  const realInference = readiness.components?.real_inference;
  const applicationReady = Boolean(readiness.application_ready);
  const processingState: StartupPhaseState = readiness.processing_status === "READY" && applicationReady
    ? "ready"
    : readiness.processing_status === "READY_WITH_WARNINGS" && applicationReady
      ? "degraded"
      : "degraded";
  return [
    { id: "frontend", label: t.startupFrontend, detail: frontend?.detail ?? t.startupFrontendReady, state: frontendState },
    { id: "backend", label: t.startupBackend, detail: readiness.liveness.backend?.state ?? "alive", state: "ready" },
    { id: "storage", label: t.startupStorage, detail: readiness.core_status ?? readiness.core.project_storage?.state ?? "ready", state: readiness.core_status === "BLOCKED" ? "degraded" : "ready" },
    {
      id: "runtime",
      label: t.startupVideoRuntime,
      detail: String(readiness.processing.media_runtime?.readiness_state ?? "unavailable"),
      state: mediaReady ? "ready" : "degraded"
    },
    {
      id: "ai",
      label: t.startupAiRuntime,
      detail: realInference?.detail ?? t.startupAiRuntimeWaiting,
      state: realInference?.ready ? "ready" : "degraded"
    },
    {
      id: "worker",
      label: t.startupWorker,
      detail: workerComponent?.detail ?? worker?.detail ?? worker?.state ?? "not_started",
      state: workerComponent?.ready ? "ready" : "degraded"
    },
    {
      id: "processing",
      label: t.startupProcessing,
      detail: `${readiness.processing_status ?? "BLOCKED"}: ${readiness.processing.synthetic_ready ? "synthetic capability available" : t.startupProcessingWaiting}`,
      state: processingState
    }
  ];
}

function startupStateLabel(state: StartupPhaseState, t: typeof copy[Language]) {
  if (state === "ready") return t.startupReady;
  if (state === "degraded") return t.startupDegraded;
  if (state === "failed") return t.startupFailed;
  return t.startupWaiting;
}

function recordFrontendDiagnostic(event: string, payload: Record<string, unknown>) {
  if (window.localStorage.getItem("tva.diagnostics") !== "1") return;
  const target = window as Window & { __tvaInteractionDiagnostics?: Record<string, unknown>[] };
  target.__tvaInteractionDiagnostics = target.__tvaInteractionDiagnostics ?? [];
  target.__tvaInteractionDiagnostics.push({ event, ...payload, at: new Date().toISOString() });
}

function Onboarding({ t, busy, projectName, location, projects, setProjectName, setLocation, createProject, openProject }: {
  t: typeof copy[Language];
  busy: boolean;
  projectName: string;
  location: string;
  projects: ProjectSummary[];
  setProjectName: (value: string) => void;
  setLocation: (value: string) => void;
  createProject: () => Promise<void>;
  openProject: (id: string) => Promise<void>;
}) {
  return (
    <section className="onboarding" aria-labelledby="onboarding-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t.onboardingEyebrow}</p>
          <h1 id="onboarding-title">{t.onboardingTitle}</h1>
        </div>
        <StatusBadge tone="info" label={t.noProject} />
      </div>
      <div className="onboarding-flow" aria-label={t.onboardingFlow}>
        {[t.create, t.selectVideo, t.referenceImage, t.confirmTime, t.createDetectionArea, t.addLine, t.sceneAction].map((step, index) => (
          <span key={step}><strong>{index + 1}</strong>{step}</span>
        ))}
      </div>
      <div className="setup-grid">
        <div className="setup-form">
          <h2>{t.createProjectFirst}</h2>
          <p>{t.createProjectFirstBody}</p>
          <label>{t.projectName}<input value={projectName} onChange={(event) => setProjectName(event.target.value)} /></label>
          <label>{t.location}<input value={location} onChange={(event) => setLocation(event.target.value)} /></label>
          <button className="primary" onClick={() => void createProject()} disabled={busy}>{t.create}</button>
        </div>
        <div className="setup-form">
          <h2>{t.openProject}</h2>
          <p>{projects.length ? t.openProjectBody : t.noExistingProjects}</p>
          {projects.map((project) => (
            <button key={String(project.id)} className="project-row" onClick={() => void openProject(String(project.id))}>
              <strong>{String(project.name ?? project.id)}</strong>
              <span>{String(project.location ?? "")}</span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function SceneOverlay({ geometry, selectedObject, beginDrag, moveHandle, endDrag, setSelectedObject, t }: {
  geometry: Geometry;
  selectedObject: SelectedObject | null;
  beginDrag: (target: DragTarget) => void;
  moveHandle: (event: PointerEvent<SVGSVGElement>) => void;
  endDrag: () => void;
  setSelectedObject: (value: SelectedObject) => void;
  t: typeof copy[Language];
}) {
  return (
    <svg viewBox="0 0 1 1" preserveAspectRatio="none" onPointerMove={moveHandle} onPointerUp={endDrag} onPointerCancel={endDrag} aria-label={t.sceneOverlay} onDragStart={(event) => event.preventDefault()}>
      {geometry.rois.map((roi) => {
        const selected = selectedObject?.type === "roi" && selectedObject.id === roi.id;
        return (
          <g key={roi.id} className={`${roi.active ? "" : "muted"} ${selected ? "selected" : ""}`}>
            <polygon points={roi.vertices.map((point) => `${point.x},${point.y}`).join(" ")} onPointerDown={() => setSelectedObject({ type: "roi", id: roi.id })} />
            <text className="roi-label" x={roi.vertices[0]?.x ?? 0.12} y={(roi.vertices[0]?.y ?? 0.16) - 0.02}>{roi.name}</text>
            {roi.vertices.map((point, index) => <circle key={`${roi.id}-${index}`} cx={point.x} cy={point.y} r="0.012" onPointerDown={(event) => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); beginDrag({ type: "roi-vertex", id: roi.id, index }); }} />)}
          </g>
        );
      })}
      {geometry.counting_lines.map((line) => {
        const selected = selectedObject?.type === "line" && selectedObject.id === line.id;
        const mid = midpoint(line);
        const normal = sideLabelOffsets(line);
        return (
          <g key={line.id} className={`${line.active ? "" : "muted"} ${selected ? "selected" : ""}`}>
            <line className="count-line" x1={line.start.x} y1={line.start.y} x2={line.end.x} y2={line.end.y} onPointerDown={() => setSelectedObject({ type: "line", id: line.id })} />
            <text className="line-name" x={mid.x} y={clamp(mid.y - 0.075)}>{line.name}</text>
            <text className="side-label" x={clamp(mid.x + normal.a.x)} y={clamp(mid.y + normal.a.y)}>{line.side_a_name || t.sideALabelDefault}</text>
            <text className="side-label" x={clamp(mid.x + normal.b.x)} y={clamp(mid.y + normal.b.y)}>{line.side_b_name || t.sideBLabelDefault}</text>
            {(["start", "end"] as const).map((part) => <circle key={`${line.id}-${part}`} cx={line[part].x} cy={line[part].y} r="0.014" onPointerDown={(event) => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); beginDrag({ type: "line-point", id: line.id, part }); }} />)}
          </g>
        );
      })}
    </svg>
  );
}

function applyDragGeometry(geometry: Geometry, target: Exclude<DragTarget, null>, point: Point): Geometry {
  if (target.type === "line-point") {
    return {
      ...geometry,
      counting_lines: geometry.counting_lines.map((line) => {
        if (line.id !== target.id) return line;
        const fixed = target.part === "start" ? line.end : line.start;
        const nextPoint = enforceMinimumLineLength(fixed, point);
        return { ...line, [target.part]: nextPoint };
      })
    };
  }
  return {
    ...geometry,
    rois: geometry.rois.map((roi) =>
      roi.id === target.id ? { ...roi, vertices: roi.vertices.map((vertex, index) => (index === target.index ? point : vertex)) } : roi
    )
  };
}

function SceneInspector(props: {
  t: typeof copy[Language];
  geometry: Geometry;
  selectedObject: SelectedObject | null;
  selectedLine: CountingLine | null;
  selectedRoi: Roi | null;
  sceneDirty: boolean;
  setSelectedObject: (value: SelectedObject) => void;
  updateSelectedLine: (patch: Partial<CountingLine>) => void;
  updateSelectedRoi: (patch: Partial<Roi>) => void;
  swapSelectedDirections: () => void;
  deleteSelectedObject: () => void;
}) {
  const { t, geometry, selectedObject, selectedLine, selectedRoi, sceneDirty, setSelectedObject, updateSelectedLine, updateSelectedRoi, swapSelectedDirections, deleteSelectedObject } = props;
  return (
    <div className="scene-inspector">
      <div className="inspector-heading">
        <h3>{t.geometryObjects}</h3>
        <StatusBadge tone={sceneDirty ? "warning" : "info"} label={sceneDirty ? t.unsavedChanges : t.sceneSaved} />
      </div>
      <div className="object-list" aria-label={t.geometryObjects}>
        {geometry.rois.map((roi) => <button key={roi.id} className={selectedObject?.type === "roi" && selectedObject.id === roi.id ? "selected" : ""} onClick={() => setSelectedObject({ type: "roi", id: roi.id })}><span>{t.detectionArea}</span><strong>{roi.name}</strong></button>)}
        {geometry.counting_lines.map((line, index) => <button key={line.id} className={selectedObject?.type === "line" && selectedObject.id === line.id ? "selected" : ""} onClick={() => setSelectedObject({ type: "line", id: line.id })}><span>{t.countingLine} {index + 1}</span><strong>{line.name}</strong><small>{line.side_a_name || t.sideALabelDefault} / {line.side_b_name || t.sideBLabelDefault}</small></button>)}
      </div>
      {selectedRoi && (
        <div className="object-editor">
          <h3>{t.detectionArea}</h3>
          <p>{t.oneRoiPilot}</p>
          <label>{t.areaName}<input value={selectedRoi.name} onChange={(event) => updateSelectedRoi({ name: event.target.value })} /></label>
          <button className="secondary destructive" onClick={deleteSelectedObject}><Trash2 size={16} />{t.deleteDetectionArea}</button>
        </div>
      )}
      {selectedLine && (
        <div className="object-editor">
          <h3>{t.countingLine}</h3>
          <label>{t.lineName}<input value={selectedLine.name} onChange={(event) => updateSelectedLine({ name: event.target.value })} /></label>
          <label>{t.countingMode}
            <select value={selectedLine.direction_mode} onChange={(event) => updateSelectedLine({ direction_mode: event.target.value as CountingLine["direction_mode"] })}>
              <option value="A_TO_B">{t.countOneDirection}</option>
              <option value="B_TO_A">{t.countReverseDirection}</option>
              <option value="BIDIRECTIONAL">{t.countBothDirections}</option>
            </select>
          </label>
          <label>{t.sideAName}<input value={selectedLine.side_a_name} onChange={(event) => updateSelectedLine({ side_a_name: event.target.value })} /></label>
          <label>{t.sideBName}<input value={selectedLine.side_b_name} onChange={(event) => updateSelectedLine({ side_b_name: event.target.value })} /></label>
          <button className="secondary" onClick={swapSelectedDirections}><ArrowLeftRight size={16} />{t.swapDirections}</button>
          <label>{t.approachName}<input value={selectedLine.approach_name} onChange={(event) => updateSelectedLine({ approach_name: event.target.value })} /></label>
          <label>{t.movementName}<input value={selectedLine.movement_name} onChange={(event) => updateSelectedLine({ movement_name: event.target.value })} /></label>
          <label>{t.analystNote}<input value={selectedLine.analyst_note} onChange={(event) => updateSelectedLine({ analyst_note: event.target.value })} /></label>
          <button className="secondary destructive" onClick={deleteSelectedObject}><Trash2 size={16} />{t.deleteCountingLine}</button>
        </div>
      )}
      {!selectedObject && <p>{t.noGeometrySelected}</p>}
    </div>
  );
}

function ReviewAndResults({ t, busy, crossingEvents, aggregate, reviewedTotals, engineering, activeProcessingJob, jobs, snapshot, processingMode, setProcessingMode, realReady, realReadyDetail, configureSceneAndRun, cancelProcessingJob }: {
  t: typeof copy[Language];
  busy: boolean;
  crossingEvents: Record<string, unknown>[];
  aggregate: Record<string, unknown> | null;
  reviewedTotals: Record<string, unknown> | null;
  engineering: EngineeringSummaryOut | null;
  activeProcessingJob: ProcessingJob | null;
  jobs: ProcessingJob[];
  snapshot: ProjectSnapshot | null;
  processingMode: ProcessingMode;
  setProcessingMode: (mode: ProcessingMode) => void;
  realReady: boolean;
  realReadyDetail: string;
  configureSceneAndRun: (runAgain?: boolean) => Promise<void>;
  cancelProcessingJob: () => Promise<void>;
}) {
  const visibleJob = activeProcessingJob ?? jobs[0] ?? null;
  const jobState = String(visibleJob?.job_state ?? "");
  const jobActive = ACTIVE_JOB_STATES.has(jobState);
  const progress = Math.max(0, Math.min(100, Number(visibleJob?.progress_percent ?? 0)));
  const visibleMode = String(visibleJob?.processing_mode ?? visibleJob?.mode ?? processingMode) as ProcessingMode;
  const modeLabel = visibleMode === "REAL_VIDEO" ? t.realVideoMode : t.syntheticMode;
  return (
    <section className="review-results">
      <div className="review-pane">
        <div className="section-heading compact"><h2>{t.processingJob}</h2><StatusBadge tone={jobTone(jobState)} label={jobLabel(jobState, t)} /></div>
        <div className="processing-panel" aria-live="polite">
          <div className="processing-panel-heading">
            <div>
              <strong>{t.processingJob}</strong>
              <span>{visibleMode === "REAL_VIDEO" ? t.realVideoDisclaimer : t.syntheticProcessingDisclaimer}</span>
            </div>
            <StatusBadge tone={jobTone(jobState)} label={jobLabel(jobState, t)} />
          </div>
          {visibleJob ? (
            <>
              <div className="progress-meter" aria-label={t.processingJob}><span style={{ width: `${progress}%` }} /></div>
              <dl className="job-meta">
                <dt>{t.state}</dt><dd>{String(visibleJob.progress_phase ?? jobState)}</dd>
                <dt>PTS</dt><dd>{String(visibleJob.progress_message_code ?? "none")}</dd>
                <dt>ID</dt><dd className="mono">{String(visibleJob.id)}</dd>
              </dl>
              {jobState !== "COMPLETED" && <p className="blocked-note">{t.processingPartialBlocked}</p>}
            </>
          ) : (
            <p className="blocked-note">{t.noProcessingJob}</p>
          )}
          <div className="processing-mode-row">
            <label>{t.processingMode}
              <select value={processingMode} disabled={busy || jobActive} onChange={(event) => setProcessingMode(event.target.value as ProcessingMode)}>
                <option value="SYNTHETIC">{t.syntheticMode}</option>
                <option value="REAL_VIDEO" disabled={!realReady}>{t.realVideoMode}</option>
              </select>
            </label>
            <span className={`readiness-note ${realReady ? "ready" : "blocked"}`}>
              {processingMode === "REAL_VIDEO" ? (realReady ? t.realVideoReady : realReadyDetail) : t.syntheticReady}
            </span>
          </div>
          <div className="action-row">
            <button className="secondary" disabled={busy || !snapshot?.scene || jobActive} onClick={() => void configureSceneAndRun(false)}><Play size={16} />{t.aiTrial}</button>
            <button className="secondary" disabled={busy || !snapshot?.scene || jobActive} onClick={() => void configureSceneAndRun(true)}><RefreshCw size={16} />{t.rerunProcessing}</button>
            <button className="secondary destructive" disabled={busy || !jobActive} onClick={() => void cancelProcessingJob()}>{t.cancelProcessing}</button>
          </div>
        </div>
        <p className="blocked-note">{t.reviewTitle}: {t.reviewedTotals}</p>
      </div>
      <div className="results-pane">
        <div className="section-heading compact"><h2>{t.resultTitle}</h2><span className="mono">{modeLabel} · {visibleMode === "REAL_VIDEO" ? t.provisionalClass : t.validationOnly}</span></div>
        <div className="totals-strip" aria-label={t.reviewedTotals}>
          <span>{t.rawTotals}: <strong>{String(reviewedTotals?.raw_total ?? aggregate?.hourly_total ?? 0)}</strong></span>
          <span>{t.reviewedTotals}: <strong>{String(reviewedTotals?.accepted_total ?? 0)}</strong></span>
          <span>{t.excludedTotals}: <strong>{String(reviewedTotals?.excluded_total ?? 0)}</strong></span>
          <span>{t.unresolved}: <strong>{String(reviewedTotals?.unresolved_total ?? 0)}</strong></span>
        </div>
        <EngineeringResultView t={t} summary={engineering} />
        <table aria-label={t.eventLedger}>
          <thead><tr><th>{t.eventLedger}</th><th>{t.line}</th><th>{t.direction}</th><th>{visibleMode === "REAL_VIDEO" ? t.provisionalClass : t.classLabel}</th><th>{t.trackId}</th><th>{t.reportingBin}</th></tr></thead>
          <tbody>
            {crossingEvents.length === 0 && <tr><td colSpan={6}>{t.noEvents}</td></tr>}
            {crossingEvents.map((event) => <tr key={String(event.id)}><th scope="row" className="mono">{String(event.crossing_timestamp_ms)}</th><td>{String(event.counting_line_label)}</td><td>{String(event.readable_direction_label || event.crossing_direction)}</td><td>{String(event.provisional_class || event.synthetic_class)}</td><td className="mono">{String(event.track_id)}</td><td className="mono">{String(event.reporting_bin_start_ms)}</td></tr>)}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function EngineeringResultView({ t, summary }: { t: typeof copy[Language]; summary: EngineeringSummaryOut | null }) {
  const [selectedLineId, setSelectedLineId] = useState("");
  if (!summary) {
    return <div className="engineering-empty" role="status">{t.noEngineeringResult}</div>;
  }
  const lineIds = summary.line_totals.map((line) => line.line_id);
  const activeLineId = lineIds.includes(selectedLineId) ? selectedLineId : lineIds[0] ?? "";
  const matrixRows = summary.direction_class_matrix.filter((row) => row.line_id === activeLineId);
  const classes = [...new Set(matrixRows.map((row) => row.engineering_class))];
  const unknownCount = summary.line_class_totals.filter((row) => row.engineering_class === "UNKNOWN").reduce((total, row) => total + row.total, 0);
  const ambiguousCount = summary.line_class_totals.filter((row) => row.engineering_class === "AMBIGUOUS").reduce((total, row) => total + row.total, 0);
  const invalidDirectionCount = summary.reconciliation.invalid_direction_exclusions?.length ?? 0;
  const classificationConsistencyCount = summary.reconciliation.classification_exclusions?.length ?? 0;
  const intervalRows = summary.interval_rows.filter((row) => row.line_id === activeLineId && row.count > 0);
  const timeLabel = summary.source_time_status === "CONFIRMED" ? t.recordingLocalTime : t.sourceRelativeTime;
  return (
    <section className="engineering-results" aria-labelledby="engineering-results-title">
      <div className="engineering-disclosure" role="note">
        <strong id="engineering-results-title">{t.provisionalAutomaticClassification}</strong>
        <span>{t.unsupportedClassDisclosure}</span>
      </div>
      <div className="engineering-meta">
        <dl>
          <dt>{t.taxonomyRevision}</dt><dd className="mono">{summary.taxonomy_revision}</dd>
          <dt>{t.mappingRevision}</dt><dd className="mono">{summary.mapping_revision}</dd>
          <dt>{t.policyRevision}</dt><dd className="mono">{summary.classification_policy_revision}</dd>
          <dt>{timeLabel}</dt><dd>{summary.source_timezone_name ?? "PTS"} · {summary.source_time_status}</dd>
        </dl>
        <div className="engineering-statuses">
          <StatusBadge tone={summary.stale ? "warning" : summary.engineering_ready ? "success" : "warning"} label={summary.stale ? t.engineeringStale : summary.engineering_ready ? t.structurallyValid : t.structurallyInvalid} />
          <span>{t.unknownClass}: <strong>{unknownCount}</strong></span>
          <span>{t.ambiguousClass}: <strong>{ambiguousCount}</strong></span>
        </div>
      </div>
      {summary.stale && <p className="engineering-warning" role="alert">{t.engineeringStale}</p>}
      {invalidDirectionCount > 0 && <p className="engineering-warning" role="alert">{t.invalidDirectionWarning}: <strong>{invalidDirectionCount}</strong></p>}
      {classificationConsistencyCount > 0 && <p className="engineering-warning" role="alert">{t.classificationConsistencyWarning}: <strong>{classificationConsistencyCount}</strong></p>}
      {summary.line_totals.length > 1 && <p className="engineering-disclosure">{t.eventTotalDisclosure}</p>}
      <h3>{t.lineTotals}</h3>
      <div className="engineering-table-scroll">
        <table className="engineering-table">
          <thead><tr><th>{t.line}</th><th>{t.count}</th><th>A_TO_B</th><th>B_TO_A</th></tr></thead>
          <tbody>{summary.line_totals.map((line) => <tr key={line.line_id}><th scope="row">{line.line_name}</th><td>{line.total}</td><td>{line.a_to_b}</td><td>{line.b_to_a}</td></tr>)}</tbody>
        </table>
      </div>
      {lineIds.length > 0 && <label className="engineering-line-select">{t.line}
        <select value={activeLineId} onChange={(event) => setSelectedLineId(event.target.value)} aria-label={t.line}>
          {summary.line_totals.map((line) => <option key={line.line_id} value={line.line_id}>{line.line_name}</option>)}
        </select>
      </label>}
      <h3>{t.directionMatrix}</h3>
      <div className="engineering-table-scroll">
        <table className="engineering-table engineering-matrix">
          <thead><tr><th>{t.classTotal}</th><th>A_TO_B</th><th>B_TO_A</th><th>{t.count}</th></tr></thead>
          <tbody>{classes.map((code) => {
            const aToB = matrixRows.find((row) => row.engineering_class === code && row.direction === "A_TO_B")?.total ?? 0;
            const bToA = matrixRows.find((row) => row.engineering_class === code && row.direction === "B_TO_A")?.total ?? 0;
            return <tr key={code}><th scope="row">{code}</th><td>{aToB}</td><td>{bToA}</td><td>{aToB + bToA}</td></tr>;
          })}</tbody>
        </table>
      </div>
      <h3>{t.intervalTable}</h3>
      <p className="engineering-provenance">{t.intervalNonZeroDisclosure}</p>
      <div className="engineering-table-scroll">
        <table className="engineering-table">
          <thead><tr><th>{t.interval}</th><th>{t.canonicalDirection}</th><th>{t.classLabel}</th><th>{t.count}</th></tr></thead>
          <tbody>{summary.intervals.length === 0 || summary.overall_event_total === 0 || intervalRows.length === 0 ? <tr><td colSpan={4}>{t.noIntervalEvents}</td></tr> : intervalRows.map((row) => {
            const interval = summary.intervals.find((item) => item.interval_index === row.interval_index);
            return <tr key={`${row.interval_index}-${row.direction}-${row.engineering_class}`}><th scope="row">{interval?.display_label ?? row.interval_index}</th><td>{row.direction}</td><td>{row.engineering_class}</td><td>{row.count}</td></tr>;
          })}</tbody>
        </table>
      </div>
      <p className="engineering-provenance">{String(summary.provenance.processing_mode ?? "UNKNOWN")} · {summary.overall_event_total} {t.eventLedger} · {summary.reconciliation.status}</p>
    </section>
  );
}

function jobLabel(state: string, t: typeof copy[Language]) {
  if (state === "COMPLETED") return t.processingComplete;
  if (state === "FAILED") return t.processingFailed;
  if (state === "CANCELLED") return t.processingCancelled;
  if (state === "QUEUED" || state === "CREATED") return t.processingQueued;
  if (state === "STARTING" || state === "RUNNING" || state === "CANCELLATION_REQUESTED") return t.processingRunning;
  return t.noProcessingJob;
}

function jobTone(state: string): "success" | "warning" | "info" {
  if (state === "COMPLETED") return "success";
  if (state === "FAILED" || state === "CANCELLED" || state === "CANCELLATION_REQUESTED") return "warning";
  return "info";
}

function defaultGeometry(): Geometry {
  return {
    schema_version: "scene-geometry-v2",
    coordinate_system: { origin: "top_left", units: "normalized_source_display" },
    counting_lines: [
      {
        id: "line_main",
        name: "Main crossing",
        active: true,
        start: { x: 0.22, y: 0.68 },
        end: { x: 0.78, y: 0.32 },
        direction_mode: "BIDIRECTIONAL",
        side_a_name: "Side A",
        side_b_name: "Side B",
        approach_name: "",
        movement_name: "",
        analyst_note: ""
      }
    ],
    rois: [{ id: "roi_main", name: "Detection area", active: true, vertices: [{ x: 0.12, y: 0.16 }, { x: 0.88, y: 0.16 }, { x: 0.84, y: 0.84 }, { x: 0.16, y: 0.82 }] }]
  };
}

function parseGeometry(serialized: unknown): Geometry {
  if (!serialized) return defaultGeometry();
  try {
    const parsed = JSON.parse(String(serialized)) as Partial<Geometry>;
    if (!Array.isArray(parsed.counting_lines) || !Array.isArray(parsed.rois)) return defaultGeometry();
    return {
      schema_version: "scene-geometry-v2",
      coordinate_system: { origin: "top_left", units: "normalized_source_display" },
      rois: parsed.rois,
      counting_lines: parsed.counting_lines.map(normalizeLine)
    };
  } catch {
    return defaultGeometry();
  }
}

function normalizeLine(line: Partial<CountingLine> & Record<string, unknown>): CountingLine {
  const mode = String(line.direction_mode ?? "BIDIRECTIONAL");
  const modeMap: Record<string, CountingLine["direction_mode"]> = {
    a_to_b: "A_TO_B",
    b_to_a: "B_TO_A",
    bidirectional: "BIDIRECTIONAL",
    A_TO_B: "A_TO_B",
    B_TO_A: "B_TO_A",
    BIDIRECTIONAL: "BIDIRECTIONAL"
  };
  return {
    id: String(line.id ?? `line_${Date.now()}`),
    name: String(line.name ?? "Counting line"),
    active: line.active !== false,
    start: line.start ?? { x: 0.25, y: 0.55 },
    end: line.end ?? { x: 0.75, y: 0.55 },
    direction_mode: modeMap[mode] ?? "BIDIRECTIONAL",
    side_a_name: String(line.side_a_name ?? line["direction_a_label"] ?? ""),
    side_b_name: String(line.side_b_name ?? line["direction_b_label"] ?? ""),
    approach_name: String(line.approach_name ?? ""),
    movement_name: String(line.movement_name ?? ""),
    analyst_note: String(line.analyst_note ?? "")
  };
}

function clamp(value: number) {
  return Math.max(0, Math.min(1, value));
}

function midpoint(line: CountingLine): Point {
  return { x: (line.start.x + line.end.x) / 2, y: (line.start.y + line.end.y) / 2 };
}

function sideLabelOffsets(line: CountingLine) {
  const dx = line.end.x - line.start.x;
  const dy = line.end.y - line.start.y;
  const length = Math.hypot(dx, dy) || 1;
  const nx = -dy / length * 0.1;
  const ny = dx / length * 0.1;
  return { a: { x: nx, y: ny }, b: { x: -nx, y: -ny } };
}

function StatusBadge({ tone, label }: { tone: "success" | "warning" | "info"; label: string }) {
  return <span className={`badge ${tone}`}><span aria-hidden="true" />{label}</span>;
}

function StateBanner({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return <div className="banner warning"><AlertTriangle size={18} /><div><strong>{title}</strong><span>{body}</span></div>{action}</div>;
}

function stateLabel(loadState: LoadState, stale: boolean, dirty: boolean, t: typeof copy[Language]) {
  if (loadState === "saving") return t.saving;
  if (loadState === "loading") return t.loading;
  if (loadState === "error") return t.backendUnavailable;
  if (stale) return t.stateStale;
  if (dirty) return t.unsavedChanges;
  if (loadState === "blocked") return t.aiTrial;
  return t.stateCurrent;
}

function benchmarkStateLabel(state: "loading" | "ready" | "empty" | "error", t: typeof copy[Language]) {
  if (state === "loading") return t.loading;
  if (state === "error") return t.backendUnavailable;
  if (state === "empty") return t.benchmarkEmptyTitle;
  return t.stateCurrent;
}

function stepClass(index: number, snapshot: ProjectSnapshot | null, unresolved: number, certified: boolean, exported: boolean) {
  const complete = [
    Boolean(snapshot?.project),
    Boolean(snapshot?.source),
    Boolean(snapshot?.scene),
    Boolean(snapshot?.run),
    Boolean(snapshot?.run) && unresolved === 0,
    Boolean(snapshot?.run),
    certified,
    exported
  ][index];
  return complete ? "complete" : index === 0 ? "active" : "blocked";
}
