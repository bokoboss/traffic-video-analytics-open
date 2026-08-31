import { AlertTriangle, Check, ChevronLeft, Play, RotateCcw, Save, SlidersHorizontal, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "./api/client";
import type {
  ConfigurationValidationOut,
  CapabilityValidationOut,
  ConfigurationComparisonOut,
  ExpertProcessingOverrides,
  GuidedProcessingSettings,
  OperationalCandidateSelectionOut,
  PreviewRunOut,
  ProcessingConfigurationRevisionOut,
  ProcessingProfileOut
} from "./api/generated";

type Language = "th" | "en";

type Props = {
  language: Language;
  projectId: string | null;
  sceneVersion: number;
  source: Record<string, unknown> | null;
  projectStale: boolean;
  realVideoReady: boolean;
  realVideoBlockReason: string;
  onBack: () => void;
  onConfigurationSaved?: (revisionId: string | null) => void;
};

type PreviewMode = "SYNTHETIC" | "REAL_VIDEO";
type PreviewBlockCode =
  | "NO_SOURCE"
  | "STALE_PROJECT"
  | "NO_SCENE"
  | "SOURCE_NOT_READY"
  | "REAL_RUNTIME_UNAVAILABLE"
  | "UNSUPPORTED_SOURCE"
  | "INVALID_WINDOW";

type PreviewRoute = {
  mode: PreviewMode | null;
  startPtsMs: number;
  endPtsMs: number;
  fixtureId?: string;
  blockCode?: PreviewBlockCode;
};

const SYNTHETIC_FIXTURE_FILE = "approved-mock.mp4";
const SYNTHETIC_FIXTURE_FINGERPRINT = "mock-full-file-sha256";

export function resolvePreviewRoute({
  source,
  projectStale,
  sceneVersion,
  realVideoReady
}: Pick<Props, "source" | "projectStale" | "sceneVersion" | "realVideoReady">): PreviewRoute {
  const sourceType = String(source?.source_type ?? "");
  const isRealVideo = sourceType === "local_upload";
  const isSyntheticFixture = sourceType !== "local_upload"
    && source?.file_name === SYNTHETIC_FIXTURE_FILE
    && source?.fingerprint_sha256 === SYNTHETIC_FIXTURE_FINGERPRINT;
  const mode: PreviewMode | null = isRealVideo ? "REAL_VIDEO" : isSyntheticFixture ? "SYNTHETIC" : null;
  const startPtsMs = Number(source?.analysis_start_pts_ms ?? source?.analysis_window_start_ms ?? 0);
  const explicitEnd = Number(source?.analysis_end_pts_ms ?? source?.analysis_window_end_ms ?? NaN);
  const durationMs = Number(source?.duration_ms ?? NaN);
  const sourceEnd = Number.isFinite(explicitEnd) ? explicitEnd : Number.isFinite(durationMs) ? durationMs : startPtsMs;
  const endPtsMs = Math.min(sourceEnd, startPtsMs + 30_000);

  if (!source?.id || !source?.fingerprint_sha256) return { mode, startPtsMs, endPtsMs, blockCode: "NO_SOURCE" };
  if (projectStale) return { mode, startPtsMs, endPtsMs, blockCode: "STALE_PROJECT" };
  if (sceneVersion < 1) return { mode, startPtsMs, endPtsMs, blockCode: "NO_SCENE" };
  if (!Number.isFinite(startPtsMs) || !Number.isFinite(endPtsMs) || endPtsMs <= startPtsMs) {
    return { mode, startPtsMs, endPtsMs, blockCode: "INVALID_WINDOW" };
  }
  if (isRealVideo && source.readiness_state !== "media_ready") {
    return { mode, startPtsMs, endPtsMs, blockCode: "SOURCE_NOT_READY" };
  }
  if (isRealVideo && !realVideoReady) {
    return { mode, startPtsMs, endPtsMs, blockCode: "REAL_RUNTIME_UNAVAILABLE" };
  }
  if (!mode) return { mode, startPtsMs, endPtsMs, blockCode: "UNSUPPORTED_SOURCE" };
  return mode === "SYNTHETIC"
    ? { mode, startPtsMs, endPtsMs, fixtureId: "api-acceptance" }
    : { mode, startPtsMs, endPtsMs };
}

type ProfileCode =
  | "BALANCED"
  | "HIGH_ACCURACY"
  | "FAST_PROCESSING"
  | "SMALL_DISTANT_OBJECTS"
  | "DENSE_TRAFFIC"
  | "MOTORCYCLE_HEAVY"
  | "PEDESTRIAN_COUNTING"
  | "CUSTOM";

const defaultGuided: GuidedProcessingSettings = {};

const hardwareLabelsTh: Partial<Record<ProfileCode, string>> = {
  BALANCED: "CPU หรือ GPU ระดับเริ่มต้น",
  CUSTOM: "ขึ้นกับค่าที่ resolve แล้ว",
  DENSE_TRAFFIC: "แนะนำ GPU",
  FAST_PROCESSING: "ใช้ CPU ได้ แนะนำ GPU",
  HIGH_ACCURACY: "แนะนำ GPU",
  MOTORCYCLE_HEAVY: "CPU หรือ GPU",
  PEDESTRIAN_COUNTING: "CPU หรือ GPU",
  SMALL_DISTANT_OBJECTS: "แนะนำ GPU อย่างมาก"
};

const labelSets = {
  en: {
    eyebrow: "Operational processing",
    title: "Processing profiles and capability check",
    subtitle: "Resolve a bounded, auditable configuration before starting a preview or full run.",
    back: "Back to workspace",
    profiles: "Starting profiles",
    profileHelp: "Profiles are versioned starting points. They do not establish accuracy or pilot qualification.",
    guided: "Guided settings",
    expert: "Expert settings",
    expertHelp: "Only adapter-supported fields are available. Every value is validated and retained in the configuration revision.",
    showExpert: "Show expert settings",
    hideExpert: "Hide expert settings",
    quality: "Analysis quality",
    preference: "Processing preference",
    sensitivity: "Detection sensitivity",
    scene: "Scene type",
    occlusion: "Occlusion",
    objectSize: "Object size",
    device: "Device request",
    imageSize: "Image size",
    confidence: "Confidence threshold",
    stride: "Frame stride",
    allowlist: "Raw class allowlist",
    resolve: "Validate configuration",
    save: "Save immutable revision",
    previewReal: "Run real-video preview",
    previewSynthetic: "Run synthetic validation preview",
    previewUnavailable: "Preview unavailable",
    previewMode: "Preview mode",
    realPreview: "Real-video preview",
    syntheticPreview: "Synthetic validation preview",
    ptsWindow: "PTS window",
    sourceFingerprint: "Source fingerprint",
    sceneRevision: "Scene revision",
    modelRevision: "Detector / model revision",
    trackerRevision: "Tracker revision",
    runtimeDevice: "Runtime device",
    decodedFrames: "Decoded frames",
    processedFrames: "Processed frames",
    detections: "Detections",
    summary: "Resolved configuration",
    profileRevision: "Profile revision",
    configurationHash: "Configuration hash",
    resolvedDevice: "Resolved device",
    imageStride: "Image / stride",
    resourceSignal: "Resource signal",
    immutableRevision: "Immutable revision",
    warnings: "Warnings",
    lostTrackBuffer: "Lost track buffer",
    minimumTrackObservations: "Minimum track observations",
    crossingCooldown: "Crossing cooldown ms",
    sideStability: "Side stability frames",
    minimumClassObservations: "Minimum class observations",
    minimumWinningVoteShare: "Minimum winning vote share",
    noProject: "Create or open a project before configuring processing.",
    loading: "Loading operational profiles…",
    empty: "No processing profiles are available.",
    error: "The operational settings could not be loaded.",
    noValidation: "Validate the selected settings to see the resolved parameters.",
    invalid: "Configuration needs attention before it can be saved.",
    valid: "Configuration is valid for this source window.",
    saved: "Immutable configuration revision saved.",
    previewReady: "Preview completed. This is PREVIEW_ONLY and NOT_PRODUCTION_RESULT.",
    pending: "Preview is queued for the separate processing worker.",
    running: "Preview is running in the separate processing worker.",
    previewCancelled: "Preview cancelled. No production events were written.",
    previewError: "Preview could not be completed.",
    cancelPreview: "Cancel preview",
    stats: "Preview evidence",
    events: "Crossing events",
    tracks: "Tracks",
    processingRatio: "Processing ratio",
    notEstablished: "Not established",
    limitations: "No accuracy metric is shown without approved ground truth. Throughput does not establish real-time capability.",
    reset: "Reset to selected profile",
    profileBaseline: "No guided or expert overrides; using the selected profile baseline.",
    changedFromProfile: "Changed from profile baseline",
    compare: "Compare completed previews",
    comparison: "Configuration comparison",
    comparisonHelp: "Diagnostics are side-by-side only; no opaque ranking or accuracy claim is inferred.",
    candidate: "Mark operational candidate",
    candidateRecorded: "Operational candidate recorded",
    candidateDisclosure: "Operational candidate is a workflow state, not pilot qualification.",
    capabilitySynthetic: "Record synthetic capability evidence",
    capabilityReal: "Record real-media capability evidence",
    capabilityRecorded: "Capability evidence recorded",
    capabilityDisclosureSynthetic: "Synthetic evidence is engineering-only; real-media and benchmark validation remain pending.",
    capabilityDisclosureReal: "Real-media evidence remains PREVIEW_ONLY; benchmark validation and pilot qualification remain pending.",
    lineBreakdown: "By line",
    directionBreakdown: "By direction",
    classBreakdown: "By raw class",
    noComparison: "Run at least two completed previews with different immutable revisions to compare.",
    groupDetection: "Detector and sampling",
    groupTracking: "Tracking and crossing",
    groupClassification: "Classification policy",
    runtimeHash: "Runtime configuration hash",
    requestHash: "Request provenance hash",
    provenanceStatus: "Runtime provenance status",
    workerState: "Worker ownership state",
    attempt: "Worker attempt",
    requestFingerprint: "Preview request fingerprint",
    runtimeEquivalent: "Runtime-equivalent inputs",
    runtimeEquivalentYes: "Yes — normalized worker inputs match; request provenance may differ.",
    runtimeEquivalentNo: "No — runtime identity differs or is unresolved.",
    previewBlockNoSource: "Upload a source before running a preview.",
    previewBlockStale: "The project is stale. Save the current source, scene, and configuration before previewing.",
    previewBlockNoScene: "Save the current scene before running a preview.",
    previewBlockSourceNotReady: "The uploaded source is not media-ready. Re-upload it or inspect media readiness.",
    previewBlockRealRuntime: "Real-video preview is unavailable.",
    previewBlockRealHeartbeat: "Real-video preview is unavailable: start or restart the processing worker and wait for a fresh heartbeat.",
    previewBlockRealRemediation: "Real-video preview is unavailable: check worker, media runtime, detector weights, tracker, and device readiness.",
    previewBlockUnsupportedSource: "This source is neither a managed upload nor the approved synthetic fixture.",
    previewBlockInvalidWindow: "Configure a positive source analysis window before previewing."
  },
  th: {
    running: "พรีวิวกำลังทำงานโดย processing worker แยกจาก API",
    previewCancelled: "ยกเลิกพรีวิวแล้ว และไม่มี event ของ production ถูกเขียน",
    cancelPreview: "ยกเลิกพรีวิว",
    runtimeHash: "Runtime configuration hash",
    requestHash: "Request provenance hash",
    provenanceStatus: "สถานะ runtime provenance",
    workerState: "สถานะ ownership ของ worker",
    attempt: "ครั้งที่ worker ทำงาน",
    requestFingerprint: "Preview request fingerprint",
    runtimeEquivalent: "อินพุต runtime เทียบเท่ากัน",
    runtimeEquivalentYes: "ใช่ — ค่า worker ที่ normalize แล้วตรงกัน แต่ provenance ของคำขออาจต่างกัน",
    runtimeEquivalentNo: "ไม่ใช่ — runtime identity ต่างกันหรือยังระบุไม่ได้",
    previewBlockNoSource: "อัปโหลดแหล่งวิดีโอก่อนเริ่มพรีวิว",
    previewBlockStale: "โครงการมีข้อมูลล้าสมัย โปรดบันทึกแหล่งวิดีโอ ฉาก และการตั้งค่าปัจจุบันก่อนพรีวิว",
    previewBlockNoScene: "บันทึกฉากปัจจุบันก่อนเริ่มพรีวิว",
    previewBlockSourceNotReady: "วิดีโอที่อัปโหลดยังไม่พร้อม โปรดอัปโหลดใหม่หรือตรวจสถานะสื่อ",
    previewBlockRealRuntime: "ระบบพรีวิววิดีโอจริงยังไม่พร้อม",
    previewBlockRealHeartbeat: "ระบบพรีวิววิดีโอจริงยังไม่พร้อม โปรดเริ่มหรือเริ่ม processing worker ใหม่และรอ heartbeat ล่าสุด",
    previewBlockRealRemediation: "ระบบพรีวิววิดีโอจริงยังไม่พร้อม โปรดตรวจ worker, media runtime, detector weights, tracker และอุปกรณ์",
    previewBlockUnsupportedSource: "แหล่งนี้ไม่ใช่วิดีโอที่ระบบจัดเก็บหรือ fixture สังเคราะห์ที่อนุมัติ",
    previewBlockInvalidWindow: "กำหนดช่วงวิเคราะห์วิดีโอที่มีระยะเวลามากกว่าศูนย์ก่อนพรีวิว",
    eyebrow: "การประมวลผลเชิงปฏิบัติการ",
    title: "โปรไฟล์การประมวลผลและการตรวจสอบความสามารถ",
    subtitle: "กำหนดค่าที่ตรวจสอบย้อนกลับได้ก่อนเริ่มพรีวิวหรือประมวลผลเต็มช่วง",
    back: "กลับไปพื้นที่ทำงาน",
    profiles: "โปรไฟล์เริ่มต้น",
    profileHelp: "โปรไฟล์เป็นจุดเริ่มต้นแบบมีเวอร์ชัน ไม่ใช่หลักฐานความแม่นยำหรือการรับรองนำร่อง",
    guided: "การตั้งค่าแบบแนะนำ",
    expert: "การตั้งค่าผู้เชี่ยวชาญ",
    expertHelp: "แสดงเฉพาะค่าที่อะแดปเตอร์รองรับ ทุกค่าจะถูกตรวจสอบและเก็บใน revision แบบไม่เปลี่ยนแปลง",
    showExpert: "แสดงการตั้งค่าผู้เชี่ยวชาญ",
    hideExpert: "ซ่อนการตั้งค่าผู้เชี่ยวชาญ",
    quality: "คุณภาพการวิเคราะห์",
    preference: "ความเร็วการประมวลผล",
    sensitivity: "ความไวการตรวจจับ",
    scene: "ประเภทฉาก",
    occlusion: "การบัง",
    objectSize: "ขนาดวัตถุ",
    device: "อุปกรณ์ที่ร้องขอ",
    imageSize: "ขนาดภาพ",
    confidence: "ค่า confidence",
    stride: "ระยะข้ามเฟรม",
    allowlist: "รายการ class ดิบที่อนุญาต",
    resolve: "ตรวจสอบการตั้งค่า",
    save: "บันทึก revision แบบไม่เปลี่ยนแปลง",
    previewReal: "เริ่มพรีวิววิดีโอจริง",
    previewSynthetic: "เริ่มพรีวิวตรวจสอบแบบสังเคราะห์",
    previewUnavailable: "ยังพรีวิวไม่ได้",
    previewMode: "โหมดพรีวิว",
    realPreview: "พรีวิววิดีโอจริง",
    syntheticPreview: "พรีวิวตรวจสอบแบบสังเคราะห์",
    ptsWindow: "ช่วงเวลา PTS",
    sourceFingerprint: "ลายนิ้วมือแหล่งวิดีโอ",
    sceneRevision: "รุ่นของฉาก",
    modelRevision: "รุ่นของตัวตรวจจับ / โมเดล",
    trackerRevision: "รุ่นของตัวติดตาม",
    runtimeDevice: "อุปกรณ์ที่ใช้จริง",
    decodedFrames: "เฟรมที่ถอดรหัส",
    processedFrames: "เฟรมที่ประมวลผล",
    detections: "วัตถุที่ตรวจพบ",
    summary: "ค่าที่ resolve แล้ว",
    profileRevision: "revision ของโปรไฟล์",
    configurationHash: "แฮชของ configuration",
    resolvedDevice: "อุปกรณ์ที่ resolve แล้ว",
    imageStride: "ขนาดภาพ / ระยะข้ามเฟรม",
    resourceSignal: "สัญญาณการใช้ทรัพยากร",
    immutableRevision: "revision แบบไม่เปลี่ยนแปลง",
    warnings: "คำเตือน",
    lostTrackBuffer: "บัฟเฟอร์แทร็กที่หายไป",
    minimumTrackObservations: "จำนวน observation ขั้นต่ำของแทร็ก",
    crossingCooldown: "ช่วงพักการข้ามเส้น (มิลลิวินาที)",
    sideStability: "จำนวนเฟรมที่ยืนยันด้าน",
    minimumClassObservations: "จำนวน observation ขั้นต่ำของ class",
    minimumWinningVoteShare: "สัดส่วนคะแนนชนะขั้นต่ำ",
    noProject: "สร้างหรือเปิดโครงการก่อนตั้งค่าการประมวลผล",
    loading: "กำลังโหลดโปรไฟล์การประมวลผล…",
    empty: "ยังไม่มีโปรไฟล์การประมวลผล",
    error: "ไม่สามารถโหลดการตั้งค่าการประมวลผลได้",
    noValidation: "ตรวจสอบการตั้งค่าเพื่อดูค่าที่ resolve แล้ว",
    invalid: "ต้องแก้ไขการตั้งค่าก่อนบันทึก",
    valid: "การตั้งค่านี้ใช้ได้กับช่วงเวลาของแหล่งข้อมูล",
    saved: "บันทึก configuration revision แบบไม่เปลี่ยนแปลงแล้ว",
    previewReady: "พรีวิวเสร็จแล้ว ผลลัพธ์นี้เป็น PREVIEW_ONLY และ NOT_PRODUCTION_RESULT",
    pending: "พรีวิวอยู่ในคิวของ processing worker แยกจาก API",
    previewError: "ไม่สามารถทำพรีวิวให้เสร็จได้",
    stats: "หลักฐานจากพรีวิว",
    events: "เหตุการณ์ข้ามเส้น",
    tracks: "แทร็ก",
    processingRatio: "อัตราการประมวลผล",
    notEstablished: "ยังไม่ยืนยัน",
    limitations: "ไม่แสดงค่าความแม่นยำหากไม่มี ground truth ที่อนุมัติ และ throughput ไม่ใช่หลักฐานความสามารถแบบ real-time",
    reset: "คืนค่าเป็นโปรไฟล์ที่เลือก",
    profileBaseline: "ไม่มี guided หรือ expert override และใช้ค่าพื้นฐานของโปรไฟล์ที่เลือก",
    changedFromProfile: "ค่าที่เปลี่ยนจากโปรไฟล์",
    compare: "เปรียบเทียบพรีวิวที่เสร็จแล้ว",
    comparison: "การเปรียบเทียบ configuration",
    comparisonHelp: "แสดงการวินิจฉัยแบบเคียงข้างกัน โดยไม่จัดอันดับแบบทึบหรืออนุมานความแม่นยำ",
    candidate: "บันทึกเป็น operational candidate",
    candidateRecorded: "บันทึก operational candidate แล้ว",
    candidateDisclosure: "Operational candidate เป็นสถานะของ workflow ไม่ใช่การรับรองนำร่อง",
    capabilitySynthetic: "บันทึกหลักฐานความสามารถแบบสังเคราะห์",
    capabilityReal: "บันทึกหลักฐานความสามารถจากสื่อจริง",
    capabilityRecorded: "บันทึกหลักฐานความสามารถแล้ว",
    capabilityDisclosureSynthetic: "หลักฐานแบบสังเคราะห์ใช้เพื่อวิศวกรรมเท่านั้น การตรวจสอบด้วยสื่อจริงและ benchmark ยังรอดำเนินการ",
    capabilityDisclosureReal: "หลักฐานจากสื่อจริงนี้ยังเป็น PREVIEW_ONLY การตรวจ benchmark และคุณสมบัติ pilot ยังรอดำเนินการ",
    lineBreakdown: "แยกตามเส้น",
    directionBreakdown: "แยกตามทิศทาง",
    classBreakdown: "แยกตาม raw class",
    noComparison: "ต้องมีพรีวิวที่เสร็จแล้วอย่างน้อยสองรายการจาก revision ที่ต่างกันเพื่อเปรียบเทียบ",
    groupDetection: "ตัวตรวจจับและการสุ่มตัวอย่าง",
    groupTracking: "การติดตามและการข้ามเส้น",
    groupClassification: "นโยบายการจำแนก"
  }
} as const;

function cleanOverrides(values: ExpertProcessingOverrides): ExpertProcessingOverrides {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null)) as ExpertProcessingOverrides;
}

type MessageKey = "error" | "invalid" | "valid" | "saved" | "previewReady" | "pending" | "previewError" | "previewCancelled" | "candidateRecorded" | "capabilityRecorded";
type WorkspaceMessage = { key: MessageKey } | { text: string };

export function OperationalSettingsWorkspace({
  language,
  projectId,
  sceneVersion,
  source,
  projectStale,
  realVideoReady,
  realVideoBlockReason,
  onBack,
  onConfigurationSaved = () => undefined
}: Props) {
  const t = labelSets[language];
  const [state, setState] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [profiles, setProfiles] = useState<ProcessingProfileOut[]>([]);
  const [profileCode, setProfileCode] = useState<ProfileCode>("BALANCED");
  const [guided, setGuided] = useState<GuidedProcessingSettings>(defaultGuided);
  const [expert, setExpert] = useState<ExpertProcessingOverrides>({});
  const [showExpert, setShowExpert] = useState(false);
  const [validation, setValidation] = useState<ConfigurationValidationOut | null>(null);
  const [revision, setRevision] = useState<ProcessingConfigurationRevisionOut | null>(null);
  const [preview, setPreview] = useState<PreviewRunOut | null>(null);
  const [previewHistory, setPreviewHistory] = useState<PreviewRunOut[]>([]);
  const [comparison, setComparison] = useState<ConfigurationComparisonOut | null>(null);
  const [candidate, setCandidate] = useState<OperationalCandidateSelectionOut | null>(null);
  const [capability, setCapability] = useState<CapabilityValidationOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [messageState, setMessageState] = useState<WorkspaceMessage | null>(null);
  const message = messageState ? ("key" in messageState ? t[messageState.key] : messageState.text) : "";

  useEffect(() => {
    let active = true;
    setState("loading");
    void api.processingProfiles()
      .then((items) => {
        if (!active) return;
        setProfiles(items);
        setState(items.length ? "ready" : "empty");
      })
      .catch(() => {
        if (active) setState("error");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!projectId) {
      setPreviewHistory([]);
      return;
    }
    void api.listPreviewRuns(projectId)
      .then((items) => {
        setPreviewHistory(items.filter((item) => item.status === "COMPLETED"));
        const activePreview = items.find((item) => item.status === "QUEUED" || item.status === "RUNNING");
        if (activePreview) setPreview(activePreview);
      })
      .catch(() => setPreviewHistory([]));
  }, [projectId]);

  useEffect(() => {
    if (!preview || (preview.status !== "QUEUED" && preview.status !== "RUNNING")) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api.getPreviewRun(preview.id).then((next) => {
        if (!active) return;
        setPreview(next);
        if (next.status === "COMPLETED") {
          setPreviewHistory((current) => [next, ...current.filter((item) => item.id !== next.id)]);
        }
      }).catch(() => undefined);
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [preview?.id, preview?.status]);

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.profile_code === profileCode) ?? null,
    [profileCode, profiles]
  );
  const previewRoute = resolvePreviewRoute({ source, projectStale, sceneVersion, realVideoReady });
  const previewBlockMessage = previewRoute.blockCode ? previewBlockMessageFor(previewRoute.blockCode, t, language, realVideoBlockReason) : "";
  const previewActionLabel = previewRoute.mode === "REAL_VIDEO"
    ? t.previewReal
    : previewRoute.mode === "SYNTHETIC"
      ? t.previewSynthetic
      : t.previewUnavailable;
  const profileParameters = (selectedProfile?.resolved_parameters ?? {}) as unknown as Record<string, unknown>;
  const resolvedParameters = (validation?.resolved_parameters ?? {}) as unknown as Record<string, unknown>;
  const profileDiff = validation
    ? Object.entries(resolvedParameters).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(profileParameters[key]))
    : [];
  const completedPreviews = useMemo(() => {
    const unique = new Map<string, PreviewRunOut>();
    for (const item of previewHistory) {
      if (item.status === "COMPLETED") unique.set(item.processing_configuration_revision_id, item);
    }
    return Array.from(unique.values());
  }, [previewHistory]);

  function updateGuided<K extends keyof GuidedProcessingSettings>(key: K, value: GuidedProcessingSettings[K]) {
    setGuided((current) => ({ ...current, [key]: value }));
    setValidation(null);
    setRevision(null);
    onConfigurationSaved(null);
  }

  function updateExpert<K extends keyof ExpertProcessingOverrides>(key: K, value: ExpertProcessingOverrides[K]) {
    setExpert((current) => ({ ...current, [key]: value }));
    setValidation(null);
    setRevision(null);
    onConfigurationSaved(null);
  }

  function resetToProfile() {
    setGuided({});
    setExpert({});
    setValidation(null);
    setRevision(null);
    onConfigurationSaved(null);
    setPreview(null);
    setComparison(null);
    setCandidate(null);
    setCapability(null);
    setMessageState(null);
  }

  async function resolve() {
    if (!projectId) return;
    setBusy(true);
    setMessageState(null);
    try {
      const result = await api.resolveProcessingConfiguration(projectId, {
        profile_code: profileCode,
        guided_settings: guided,
        expert_overrides: cleanOverrides(expert),
        expected_scene_version: sceneVersion || undefined,
        created_by: "operator"
      });
      setValidation(result);
      setMessageState({ key: result.valid ? "valid" : "invalid" });
    } catch (error) {
      setMessageState(error instanceof ApiError ? { text: error.message } : { key: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function saveRevision() {
    if (!projectId) return;
    setBusy(true);
    setMessageState(null);
    try {
      const result = await api.createProcessingConfiguration(projectId, {
        profile_code: profileCode,
        guided_settings: guided,
        expert_overrides: cleanOverrides(expert),
        expected_scene_version: sceneVersion || undefined,
        created_by: "operator"
      });
      setRevision(result);
      setValidation(result);
      onConfigurationSaved(result.id);
      setMessageState({ key: "saved" });
    } catch (error) {
      setMessageState(error instanceof ApiError ? { text: error.message } : { key: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function startPreview() {
    if (!projectId || previewRoute.blockCode || !previewRoute.mode) return;
    setBusy(true);
    setMessageState(null);
    try {
      let configuration = revision;
      if (!configuration) {
        configuration = await api.createProcessingConfiguration(projectId, {
          profile_code: profileCode,
          guided_settings: guided,
          expert_overrides: cleanOverrides(expert),
          expected_scene_version: sceneVersion || undefined,
          created_by: "operator"
        });
        setRevision(configuration);
        setValidation(configuration);
      }
      onConfigurationSaved(configuration.id);
      const result = await api.createPreviewRun(projectId, {
        mode: previewRoute.mode,
        processing_configuration_revision_id: configuration.id,
        start_pts_ms: previewRoute.startPtsMs,
        end_pts_ms: previewRoute.endPtsMs,
        expected_scene_version: sceneVersion || undefined,
        ...(previewRoute.fixtureId ? { fixture_id: previewRoute.fixtureId } : {}),
        created_by: "operator"
      });
      setPreview(result);
      if (result.status === "COMPLETED") {
        setPreviewHistory((current) => [result, ...current.filter((item) => item.id !== result.id)]);
      }
      setMessageState({ key: result.status === "COMPLETED" ? "previewReady" : "pending" });
    } catch (error) {
      setMessageState(error instanceof ApiError ? { text: error.message } : { key: "previewError" });
    } finally {
      setBusy(false);
    }
  }

  async function cancelPreview() {
    if (!preview || (preview.status !== "QUEUED" && preview.status !== "RUNNING")) return;
    setBusy(true);
    try {
      const result = await api.cancelPreviewRun(preview.id, "Cancelled by operator from operational settings.");
      setPreview(result);
      setMessageState({ key: "previewCancelled" });
    } catch (error) {
      setMessageState(error instanceof ApiError ? { text: error.message } : { key: "previewError" });
    } finally {
      setBusy(false);
    }
  }

  async function comparePreviews() {
    if (!projectId || completedPreviews.length < 2) return;
    setBusy(true);
    try {
      const selected = completedPreviews.slice(0, 2);
      const result = await api.compareProcessingConfigurations(projectId, {
        processing_configuration_revision_ids: selected.map((item) => item.processing_configuration_revision_id),
        preview_run_ids: selected.map((item) => item.id),
        start_pts_ms: previewRoute.startPtsMs,
        end_pts_ms: previewRoute.endPtsMs,
        expected_scene_version: sceneVersion || undefined,
        created_by: "operator"
      });
      setComparison(result);
    } catch (error) {
      setMessageState(error instanceof ApiError ? { text: error.message } : { key: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function markCandidate() {
    if (!projectId || !revision) return;
    setBusy(true);
    try {
      const result = await api.selectOperationalCandidate(projectId, {
        processing_configuration_revision_id: revision.id,
        selection_status: "OPERATOR_SELECTED",
        rationale: "Operator selected after bounded preview; no accuracy or pilot qualification is implied.",
        created_by: "operator"
      });
      setCandidate(result);
      setMessageState({ key: "candidateRecorded" });
    } catch (error) {
      setMessageState(error instanceof ApiError ? { text: error.message } : { key: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function recordCapability() {
    if (!projectId || !revision) return;
    setBusy(true);
    try {
      const result = await api.createCapabilityValidation(projectId, {
        processing_configuration_revision_id: revision.id,
        preview_run_id: preview?.id,
        validation_type: preview?.mode === "REAL_VIDEO" ? "REAL_MEDIA" : "SYNTHETIC",
        created_by: "operator"
      });
      setCapability(result);
      setMessageState({ key: "capabilityRecorded" });
    } catch (error) {
      setMessageState(error instanceof ApiError ? { text: error.message } : { key: "error" });
    } finally {
      setBusy(false);
    }
  }

  const statusTone = validation?.valid ? "success" : validation ? "warning" : "info";
  const comparisonRows = comparison && Array.isArray(comparison.comparison?.rows) ? comparison.comparison.rows : [];
  const comparisonRuntimeHashes = comparisonRows
    .map((row) => asRecord(row).runtime_configuration_hash)
    .filter((value): value is string => typeof value === "string" && value.length > 0);
  const runtimeEquivalent = comparisonRuntimeHashes.length >= 2 && new Set(comparisonRuntimeHashes).size === 1;

  return (
    <section className="operational-settings" aria-labelledby="operational-settings-title">
      <div className="settings-header">
        <div>
          <p className="eyebrow">{t.eyebrow}</p>
          <h1 id="operational-settings-title">{t.title}</h1>
          <p className="settings-subtitle">{t.subtitle}</p>
        </div>
        <button className="secondary" onClick={onBack}><ChevronLeft size={16} aria-hidden="true" />{t.back}</button>
      </div>

      {!projectId && <div className="settings-state settings-state-warning"><AlertTriangle size={18} aria-hidden="true" /><span>{t.noProject}</span></div>}
      {state === "loading" && <div className="settings-state"><span>{t.loading}</span></div>}
      {state === "empty" && <div className="settings-state"><span>{t.empty}</span></div>}
      {state === "error" && <div className="settings-state settings-state-warning"><AlertTriangle size={18} aria-hidden="true" /><span>{t.error}</span></div>}

      {state === "ready" && projectId && (
        <div className="settings-layout">
          <div className="settings-main">
            <section className="settings-section" aria-labelledby="profile-heading">
              <div className="section-heading compact">
                <div><h2 id="profile-heading">{t.profiles}</h2><p>{t.profileHelp}</p></div>
              </div>
              <div className="profile-grid">
                {profiles.map((profile) => (
                  <button
                    key={profile.profile_code}
                    className={`profile-card ${profile.profile_code === profileCode ? "selected" : ""}`}
                    aria-pressed={profile.profile_code === profileCode}
                    onClick={() => { setProfileCode(profile.profile_code as ProfileCode); resetToProfile(); }}
                  >
                    <span className="profile-card-code">{profile.profile_code}</span>
                    <strong>{language === "th" ? profile.display_name_th : profile.display_name_en}</strong>
                    <small>{language === "th" ? profile.description_th : profile.description_en}</small>
                    <span className="profile-card-meta">{language === "th" ? hardwareLabelsTh[profile.profile_code as ProfileCode] ?? profile.hardware_expectation : profile.hardware_expectation}</span>
                  </button>
                ))}
              </div>
            </section>

            <section className="settings-section" aria-labelledby="guided-heading">
              <div className="section-heading compact"><div><h2 id="guided-heading">{t.guided}</h2></div><button className="secondary" onClick={resetToProfile} disabled={busy}><RotateCcw size={16} aria-hidden="true" />{t.reset}</button></div>
              <div className="settings-form-grid">
                <SettingSelect label={t.quality} value={guided.analysis_quality ?? profileGuidedValue(selectedProfile, "analysis_quality")} options={["STANDARD", "HIGH", "VERY_HIGH"]} onChange={(value) => updateGuided("analysis_quality", value as GuidedProcessingSettings["analysis_quality"])} />
                <SettingSelect label={t.preference} value={guided.processing_preference ?? profileGuidedValue(selectedProfile, "processing_preference")} options={["SPEED", "BALANCED", "DETAIL"]} onChange={(value) => updateGuided("processing_preference", value as GuidedProcessingSettings["processing_preference"])} />
                <SettingSelect label={t.sensitivity} value={guided.detection_sensitivity ?? profileGuidedValue(selectedProfile, "detection_sensitivity")} options={["CONSERVATIVE", "BALANCED", "SENSITIVE"]} onChange={(value) => updateGuided("detection_sensitivity", value as GuidedProcessingSettings["detection_sensitivity"])} />
                <SettingSelect label={t.scene} value={guided.scene_type ?? profileGuidedValue(selectedProfile, "scene_type")} options={["GENERAL_TRAFFIC", "DENSE_TRAFFIC", "MOTORCYCLE_HEAVY", "PEDESTRIAN", "MIXED_TRAFFIC", "SMALL_DISTANT_OBJECTS"]} onChange={(value) => updateGuided("scene_type", value as GuidedProcessingSettings["scene_type"])} />
                <SettingSelect label={t.occlusion} value={guided.occlusion ?? profileGuidedValue(selectedProfile, "occlusion")} options={["LOW", "MEDIUM", "HIGH"]} onChange={(value) => updateGuided("occlusion", value as GuidedProcessingSettings["occlusion"])} />
                <SettingSelect label={t.objectSize} value={guided.object_size ?? profileGuidedValue(selectedProfile, "object_size")} options={["NORMAL", "SMALL", "VERY_SMALL"]} onChange={(value) => updateGuided("object_size", value as GuidedProcessingSettings["object_size"])} />
              </div>
            </section>

            <section className="settings-section" aria-labelledby="expert-heading">
              <div className="section-heading compact">
                <div><h2 id="expert-heading">{t.expert}</h2><p>{t.expertHelp}</p></div>
                <button className="secondary" onClick={() => setShowExpert((current) => !current)}><SlidersHorizontal size={16} aria-hidden="true" />{showExpert ? t.hideExpert : t.showExpert}</button>
              </div>
              {showExpert && (
                <div className="expert-groups">
                  <fieldset><legend>{t.groupDetection}</legend><div className="settings-form-grid">
                    <SettingSelect label={t.device} value={expert.device_mode ?? "AUTO"} options={["AUTO", "CPU", "CUDA"]} onChange={(value) => updateExpert("device_mode", value as ExpertProcessingOverrides["device_mode"])} />
                    <SettingInput label={t.imageSize} type="number" value={expert.image_size ?? ""} placeholder={String(selectedProfile?.resolved_parameters.image_size ?? 640)} onChange={(value) => updateExpert("image_size", value ? Number(value) as ExpertProcessingOverrides["image_size"] : undefined)} />
                    <SettingInput label={t.confidence} type="number" min="0" max="1" step="0.01" value={expert.confidence_threshold ?? ""} placeholder="0.25" onChange={(value) => updateExpert("confidence_threshold", value ? Number(value) : undefined)} />
                    <SettingInput label={t.stride} type="number" min="1" max="8" value={expert.frame_stride ?? ""} placeholder="1" onChange={(value) => updateExpert("frame_stride", value ? Number(value) : undefined)} />
                  </div></fieldset>
                  <fieldset><legend>{t.groupTracking}</legend><div className="settings-form-grid">
                    <SettingInput label={t.lostTrackBuffer} type="number" min="1" max="300" value={expert.lost_track_buffer ?? ""} placeholder="30" onChange={(value) => updateExpert("lost_track_buffer", value ? Number(value) : undefined)} />
                    <SettingInput label={t.minimumTrackObservations} type="number" min="1" max="100" value={expert.minimum_track_observations ?? ""} placeholder="2" onChange={(value) => updateExpert("minimum_track_observations", value ? Number(value) : undefined)} />
                    <SettingInput label={t.crossingCooldown} type="number" min="0" max="10000" value={expert.duplicate_crossing_cooldown_ms ?? ""} placeholder="250" onChange={(value) => updateExpert("duplicate_crossing_cooldown_ms", value ? Number(value) : undefined)} />
                    <SettingInput label={t.sideStability} type="number" min="1" max="60" value={expert.minimum_side_stability_frames ?? ""} placeholder="1" onChange={(value) => updateExpert("minimum_side_stability_frames", value ? Number(value) : undefined)} />
                  </div></fieldset>
                  <fieldset><legend>{t.groupClassification}</legend><div className="settings-form-grid">
                    <SettingInput label={t.minimumClassObservations} type="number" min="1" max="100" value={expert.classification_min_observations ?? ""} placeholder="2" onChange={(value) => updateExpert("classification_min_observations", value ? Number(value) : undefined)} />
                    <SettingInput label={t.minimumWinningVoteShare} type="number" min="0.5" max="1" step="0.01" value={expert.classification_min_winning_vote_share ?? ""} placeholder="0.60" onChange={(value) => updateExpert("classification_min_winning_vote_share", value ? Number(value) : undefined)} />
                    <label><span>{t.allowlist}</span><select multiple value={(expert.class_allowlist as string[] | undefined) ?? ["car", "motorcycle", "person"]} onChange={(event) => updateExpert("class_allowlist", Array.from(event.currentTarget.selectedOptions, (option) => option.value) as ExpertProcessingOverrides["class_allowlist"])}><option value="bicycle">bicycle</option><option value="bus">bus</option><option value="car">car</option><option value="motorcycle">motorcycle</option><option value="person">person</option><option value="truck">truck</option></select></label>
                  </div></fieldset>
                </div>
              )}
            </section>

            {message && <div className={`settings-state settings-state-${statusTone}`} role="status">{validation?.valid ? <Check size={18} aria-hidden="true" /> : <AlertTriangle size={18} aria-hidden="true" />}<span>{message}</span></div>}
            {previewBlockMessage && <div className="settings-state settings-state-warning" role="alert"><AlertTriangle size={18} aria-hidden="true" /><span>{previewBlockMessage}</span></div>}
            <div className="settings-actions"><button className="secondary" onClick={() => void resolve()} disabled={busy}><Check size={16} aria-hidden="true" />{t.resolve}</button><button className="primary" onClick={() => void saveRevision()} disabled={busy || validation?.valid === false}><Save size={16} aria-hidden="true" />{t.save}</button><button className="secondary" onClick={() => void startPreview()} disabled={busy || Boolean(previewRoute.blockCode)} title={previewBlockMessage || undefined}><Play size={16} aria-hidden="true" />{previewActionLabel}</button><button className="secondary" onClick={() => void comparePreviews()} disabled={busy || completedPreviews.length < 2}>{t.compare} ({completedPreviews.length})</button></div>
          </div>

           <aside className="settings-summary" aria-labelledby="summary-heading">
             {validation && <div className="settings-provenance-strip"><span>{t.runtimeHash}</span><code>{validation.runtime_configuration_hash ?? "—"}</code><span>{t.provenanceStatus}</span><strong>{validation.runtime_provenance_status}</strong><span>{t.requestHash}</span><code>{validation.request_provenance_hash ?? "—"}</code></div>}
            <div className="section-heading compact"><div><h2 id="summary-heading">{t.summary}</h2></div></div>
            {!validation && <p className="settings-muted">{t.noValidation}</p>}
            {validation && <>
              <dl className="settings-definition-list"><div><dt>{t.profileRevision}</dt><dd className="mono">{validation.profile_revision}</dd></div><div><dt>{t.configurationHash}</dt><dd className="mono">{validation.configuration_hash}</dd></div><div><dt>{t.resolvedDevice}</dt><dd>{validation.resolved_device}</dd></div><div><dt>{t.imageStride}</dt><dd>{validation.resolved_parameters.image_size} px / {validation.resolved_parameters.frame_stride}</dd></div><div><dt>{t.resourceSignal}</dt><dd>{String(validation.estimated_resource_impact.estimated_memory_class ?? "—")}</dd></div></dl>
               {profileDiff.length ? <div className="settings-message-list settings-diff"><strong>{t.changedFromProfile}</strong><ul>{profileDiff.slice(0, 8).map(([key, value]) => <li key={key}><span className="mono">{key}</span>: {formatSettingValue(value)}</li>)}</ul></div> : <p className="settings-muted">{t.profileBaseline}</p>}
               {validation.errors?.length ? <div className="settings-message-list settings-message-list-warning"><strong>{t.invalid}</strong><ul>{validation.errors.slice(0, 5).map((item, index) => <li key={`${String(item.code)}-${index}`}>{String(item.field ?? item.code)}: {String(item.detail ?? "invalid")}</li>)}</ul></div> : null}
              {validation.warnings?.length ? <div className="settings-message-list"><strong>{t.warnings}</strong><ul>{validation.warnings.slice(0, 5).map((item, index) => <li key={`${String(item.code)}-${index}`}>{String(item.detail ?? item.code)}</li>)}</ul></div> : null}
            </>}
            {revision && <div className="settings-revision"><span>{t.immutableRevision}</span><strong className="mono">{revision.id}</strong></div>}
             {preview && (
               <div className="settings-preview-result">
                 <div className="section-heading compact"><div><h2>{t.stats}</h2></div></div>
                 <p className="settings-disclosure">{preview.status === "COMPLETED" ? t.previewReady : preview.status === "CANCELLED" ? t.previewCancelled : preview.status === "RUNNING" ? t.running : preview.status === "QUEUED" ? t.pending : t.previewError}</p>
                 <dl className="settings-definition-list">
                   <div><dt>{t.previewMode}</dt><dd>{preview.mode === "REAL_VIDEO" ? t.realPreview : t.syntheticPreview}</dd></div>
                   <div><dt>{t.ptsWindow}</dt><dd className="mono">[{preview.start_pts_ms}, {preview.end_pts_ms}) ms</dd></div>
                   <div><dt>{t.sourceFingerprint}</dt><dd className="mono">{preview.source_fingerprint_sha256}</dd></div>
                   <div><dt>{t.sceneRevision}</dt><dd>{preview.scene_revision}</dd></div>
                   <div><dt>{t.immutableRevision}</dt><dd className="mono">{preview.processing_configuration_revision_id}</dd></div>
                   <div><dt>{t.modelRevision}</dt><dd className="mono">{statValue(preview.statistics?.model_revision)}</dd></div>
                   <div><dt>{t.trackerRevision}</dt><dd className="mono">{statValue(preview.statistics?.tracker_revision)}</dd></div>
                   <div><dt>{t.runtimeDevice}</dt><dd>{statValue(preview.statistics?.device)}</dd></div>
                   <div><dt>{t.decodedFrames}</dt><dd>{statValue(preview.statistics?.decoded_frames)}</dd></div>
                   <div><dt>{t.processedFrames}</dt><dd>{statValue(preview.statistics?.processed_frames)}</dd></div>
                   <div><dt>{t.detections}</dt><dd>{statValue(preview.statistics?.detection_count ?? preview.statistics?.detections)}</dd></div>
                   <div><dt>{t.tracks}</dt><dd>{statValue(preview.statistics?.track_count)}</dd></div>
                   <div><dt>{t.events}</dt><dd>{statValue(preview.statistics?.event_count ?? (preview.status === "COMPLETED" ? preview.events?.length : undefined))}</dd></div>
                   <div><dt>{t.processingRatio}</dt><dd>{preview.statistics?.processing_ratio ? String(preview.statistics.processing_ratio) : t.notEstablished}</dd></div>
                   <div><dt>{t.workerState}</dt><dd>{preview.ownership_state}</dd></div>
                   <div><dt>{t.attempt}</dt><dd>{preview.attempt_number}</dd></div>
                   <div><dt>{t.provenanceStatus}</dt><dd>{preview.provenance_status}</dd></div>
                   <div><dt>{t.runtimeHash}</dt><dd className="mono">{preview.runtime_configuration_hash ?? "—"}</dd></div>
                 </dl>
                 <p className="settings-disclosure"><span>{t.requestFingerprint}: </span><span className="mono">{preview.request_fingerprint ?? "—"}</span></p>
                 {preview.status === "COMPLETED" && <div className="settings-breakdowns"><Breakdown label={t.lineBreakdown} values={preview.statistics?.by_line} /><Breakdown label={t.directionBreakdown} values={preview.statistics?.by_direction} /><Breakdown label={t.classBreakdown} values={preview.statistics?.by_class} /></div>}
                 <div className="settings-actions">{(preview.status === "QUEUED" || preview.status === "RUNNING") && <button className="secondary" onClick={() => void cancelPreview()} disabled={busy}><XCircle size={16} aria-hidden="true" />{t.cancelPreview}</button>}<button className="secondary" onClick={() => void markCandidate()} disabled={busy || !revision || preview.status !== "COMPLETED"}>{t.candidate}</button><button className="secondary" onClick={() => void recordCapability()} disabled={busy || !revision || preview.status !== "COMPLETED"}>{preview.mode === "REAL_VIDEO" ? t.capabilityReal : t.capabilitySynthetic}</button></div>
                 {candidate && <p className="settings-disclosure">{t.candidateRecorded}: {candidate.candidate_status}. {t.candidateDisclosure}</p>}
                 {capability && <p className="settings-disclosure">{t.capabilityRecorded}: {capability.status}. {preview.mode === "REAL_VIDEO" ? t.capabilityDisclosureReal : t.capabilityDisclosureSynthetic}</p>}
               </div>
             )}
             {comparison && <div className="settings-comparison"><div className="section-heading compact"><div><h2>{t.comparison}</h2><p>{t.comparisonHelp}</p></div></div><p className="settings-disclosure"><strong>{t.runtimeEquivalent}:</strong> {runtimeEquivalent ? t.runtimeEquivalentYes : t.runtimeEquivalentNo}</p>{comparisonRows.map((row, index) => { const item = asRecord(row); const stats = asRecord(item.statistics); return <div className="settings-comparison-row" key={String(item.configuration_revision_id ?? index)}><strong>{String(item.profile_revision ?? item.configuration_revision_id ?? "—")}</strong><span className="mono">{String(item.runtime_configuration_hash ?? item.configuration_hash ?? "—")}</span><span>{t.provenanceStatus}: {String(item.runtime_provenance_status ?? "LEGACY_UNRESOLVED")}</span><span>{t.events}: {String(stats.event_count ?? stats.crossing_event_count ?? "—")}</span><span>{t.processingRatio}: {String(stats.processing_ratio ?? t.notEstablished)}</span></div>; })}<p className="settings-disclosure">{t.limitations}</p></div>}
             {!previewHistory.length && !preview && <p className="settings-muted">{t.noComparison}</p>}
             <p className="settings-limitations">{t.limitations}</p>
          </aside>
        </div>
      )}
    </section>
  );
}

function SettingSelect({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return <label><span>{label}</span><select value={value} onChange={(event) => onChange(event.currentTarget.value)}>{options.map((option) => <option key={option} value={option}>{option}</option>)}</select></label>;
}

function SettingInput({ label, type, value, placeholder, min, max, step, onChange }: { label: string; type: string; value: string | number; placeholder?: string; min?: string; max?: string; step?: string; onChange: (value: string) => void }) {
  return <label><span>{label}</span><input type={type} value={value} placeholder={placeholder} min={min} max={max} step={step} onChange={(event) => onChange(event.currentTarget.value)} /></label>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function previewBlockMessageFor(
  code: PreviewBlockCode,
  labels: (typeof labelSets)[Language],
  language: Language,
  realVideoBlockReason: string
): string {
  if (code === "NO_SOURCE") return labels.previewBlockNoSource;
  if (code === "STALE_PROJECT") return labels.previewBlockStale;
  if (code === "NO_SCENE") return labels.previewBlockNoScene;
  if (code === "SOURCE_NOT_READY") return labels.previewBlockSourceNotReady;
  if (code === "REAL_RUNTIME_UNAVAILABLE") {
    if (/heartbeat|worker/i.test(realVideoBlockReason)) return labels.previewBlockRealHeartbeat;
    if (language === "th") return labels.previewBlockRealRemediation;
    return `${labels.previewBlockRealRuntime} ${realVideoBlockReason}`.trim();
  }
  if (code === "INVALID_WINDOW") return labels.previewBlockInvalidWindow;
  return labels.previewBlockUnsupportedSource;
}

function statValue(value: unknown): string {
  return value === null || value === undefined ? "—" : String(value);
}

function formatSettingValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Breakdown({ label, values }: { label: string; values: unknown }) {
  const entries = Object.entries(asRecord(values));
  if (!entries.length) return null;
  return <div className="settings-breakdown"><strong>{label}</strong><ul>{entries.slice(0, 8).map(([key, value]) => <li key={key}><span>{key}</span><span>{String(value)}</span></li>)}</ul></div>;
}

function profileGuidedValue(profile: ProcessingProfileOut | null, key: keyof GuidedProcessingSettings): string {
  const parameters = (profile?.resolved_parameters ?? {}) as Record<string, unknown>;
  if (!profile) return defaultGuided[key] ?? "";
  if (key === "analysis_quality") {
    const imageSize = Number(parameters.image_size ?? 640);
    return imageSize >= 1280 ? "VERY_HIGH" : imageSize >= 960 ? "HIGH" : "STANDARD";
  }
  if (key === "processing_preference") return Number(parameters.frame_stride ?? 1) >= 2 ? "SPEED" : "BALANCED";
  if (key === "detection_sensitivity") {
    const confidence = Number(parameters.confidence_threshold ?? 0.25);
    return confidence >= 0.35 ? "CONSERVATIVE" : confidence <= 0.2 ? "SENSITIVE" : "BALANCED";
  }
  if (key === "scene_type") {
    if (profile.profile_code === "DENSE_TRAFFIC") return "DENSE_TRAFFIC";
    if (profile.profile_code === "MOTORCYCLE_HEAVY") return "MOTORCYCLE_HEAVY";
    if (profile.profile_code === "PEDESTRIAN_COUNTING") return "PEDESTRIAN";
    if (profile.profile_code === "SMALL_DISTANT_OBJECTS") return "SMALL_DISTANT_OBJECTS";
    const allowlist = Array.isArray(parameters.class_allowlist) ? parameters.class_allowlist.map(String) : [];
    if (allowlist.length === 1 && allowlist[0] === "person") return "PEDESTRIAN";
    if (allowlist.includes("motorcycle") && !allowlist.includes("bus") && !allowlist.includes("truck")) return "MOTORCYCLE_HEAVY";
    if (Number(parameters.image_size ?? 640) >= 1536) return "SMALL_DISTANT_OBJECTS";
    if (Number(parameters.lost_track_buffer ?? 30) >= 60 && Number(parameters.minimum_track_observations ?? 2) >= 3) return "DENSE_TRAFFIC";
    return allowlist.length > 4 ? "MIXED_TRAFFIC" : "GENERAL_TRAFFIC";
  }
  if (key === "occlusion") {
    const buffer = Number(parameters.lost_track_buffer ?? 30);
    return buffer >= 60 ? "HIGH" : buffer <= 20 ? "LOW" : "MEDIUM";
  }
  const imageSize = Number(parameters.image_size ?? 640);
  return imageSize >= 1536 ? "VERY_SMALL" : imageSize >= 960 ? "SMALL" : "NORMAL";
}
