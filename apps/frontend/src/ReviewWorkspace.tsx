import { Check, ChevronRight, Download, FileCheck2, History, RotateCcw, ShieldCheck, StickyNote, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api, ReviewQueue, ReviewSession } from "./api/client";
import type { Language } from "./copy";

type ReviewWorkspaceProps = {
  projectId: string;
  runId: string;
  language: Language;
  stale: boolean;
  onRefresh: () => void;
};

type ReviewItem = Record<string, unknown>;
type ReviewFilter = "ALL" | "UNREVIEWED" | "CONFIRMED" | "CORRECTED" | "REJECTED_FALSE_POSITIVE" | "DUPLICATE_SUPPRESSED" | "UNSCORABLE";

const CLASS_OPTIONS = [
  "PASSENGER_VEHICLE",
  "MOTORCYCLE",
  "BUS",
  "HEAVY_VEHICLE_UNSPECIFIED",
  "BICYCLE",
  "PEDESTRIAN",
  "OTHER",
  "UNKNOWN",
  "AMBIGUOUS"
];

const labels = (language: Language) => language === "th" ? {
  eyebrow: "Milestone 6E / การตรวจสอบที่ตรวจสอบย้อนกลับได้",
  title: "คิวตรวจสอบ แก้ไข และรับรองผล",
  body: "การแก้ไขทั้งหมดเป็น overlay แบบเพิ่มต่อ ส่วนผลอัตโนมัติและหลักฐานต้นฉบับยังคงไม่เปลี่ยนแปลง",
  scope: "ขอบเขต",
  reviewRevision: "revision การตรวจสอบ",
  progress: "ความคืบหน้า",
  coverage: "ความครอบคลุม",
  queue: "คิวเหตุการณ์",
  evidence: "หลักฐานเหตุการณ์",
  selectEvent: "เลือกเหตุการณ์เพื่อดูหลักฐานและการแก้ไข",
  pts: "PTS",
  origin: "ที่มา",
  automatic: "อัตโนมัติ",
  humanAdded: "เพิ่มโดยผู้ตรวจ",
  line: "เส้นนับ",
  direction: "ทิศทาง",
  classLabel: "คลาส",
  status: "สถานะ",
  filter: "กรองสถานะ",
  all: "ทั้งหมด",
  unreviewed: "ยังไม่ตรวจ",
  confirmed: "ยืนยัน",
  corrected: "แก้ไขแล้ว",
  rejected: "ตัด false positive",
  duplicate: "ซ้ำ",
  unscorable: "ให้คะแนนไม่ได้",
  reason: "เหตุผลที่บันทึก",
  reasonPlaceholder: "ระบุเหตุผลหรือสิ่งที่เห็นจากหลักฐาน",
  confirm: "ยืนยันเหตุการณ์",
  reject: "ตัดเหตุการณ์ false positive",
  changeClass: "บันทึกการเปลี่ยนคลาส",
  changeDirection: "บันทึกการเปลี่ยนทิศทาง",
  changeLine: "บันทึกการเปลี่ยนเส้นนับ",
  adjustTimestamp: "บันทึกการปรับเวลา",
  markDuplicate: "ทำเครื่องหมายเป็นรายการซ้ำ",
  duplicateTarget: "เหตุการณ์ต้นฉบับของรายการซ้ำ",
  timestamp: "เวลา crossing",
  markUnscorable: "ทำเครื่องหมายให้คะแนนไม่ได้",
  addNote: "เพิ่มหมายเหตุ",
  reverse: "ย้อนการกระทำล่าสุด",
  addMissed: "เพิ่มเหตุการณ์ที่พลาด",
  cancel: "ยกเลิก",
  missedPts: "PTS ของเหตุการณ์",
  missedLine: "เส้นนับของเหตุการณ์",
  evidenceReference: "การอ้างอิงหลักฐาน",
  manualNote: "บันทึกจากผู้ตรวจ",
  add: "เพิ่ม",
  automaticEvent: "เหตุการณ์อัตโนมัติ",
  noEvidence: "ยังไม่มีข้อมูลหลักฐาน",
  seek: "จุดเปิดดูวิดีโอ",
  actions: "การกระทำแบบเพิ่มต่อ",
  noActions: "ยังไม่มีการกระทำ",
  reconcile: "การกระทบยอด",
  pass: "ผ่าน",
  fail: "ไม่ผ่าน",
  unresolved: "ยังไม่ตรวจ",
  conflicts: "ความขัดแย้งที่บันทึก",
  complete: "ปิดการตรวจสอบ",
  certification: "การรับรอง",
  certify: "รับรอง revision นี้",
  certified: "รับรองแล้ว",
  certificationDisclosure: "การรับรองนี้ครอบคลุมขอบเขตที่เลือก และไม่ใช่การอ้างว่าตัวตรวจจับถูกต้องสมบูรณ์",
  export: "ส่งออกผลที่รับรอง",
  csv: "CSV",
  xlsx: "XLSX",
  json: "Audit JSON",
  exportReady: "สร้างไฟล์แล้ว",
  stale: "ผลล้าสมัย ห้ามแก้ไข รับรอง หรือส่งออก",
  loading: "กำลังโหลดคิวตรวจสอบ",
  error: "ไม่สามารถโหลดคิวตรวจสอบได้",
  retry: "ลองอีกครั้ง",
  actionSaved: "บันทึก action แล้ว",
  completed: "ปิดการตรวจสอบแล้ว",
  certCurrent: "สถานะใบรับรองปัจจุบัน",
  artifact: "ไฟล์ผลลัพธ์",
  download: "ดาวน์โหลด",
  projectionRevision: "revision ของ projection",
  previous: "ก่อนหน้า",
  next: "ถัดไป",
  refreshQueue: "โหลดคิวใหม่",
  conflict: "ข้อมูลตรวจสอบเปลี่ยนแล้ว กรุณาโหลดคิวใหม่",
  sourceStatusWarning: "ไฟล์นี้สร้างจากใบรับรองที่ไม่ใช่สถานะปัจจุบัน ดาวน์โหลดได้ตามนโยบายไฟล์ประวัติ",
  exportHistory: "ประวัติไฟล์ส่งออก",
  sourceEvidenceDisclosure: "วิดีโอจะถูกเปิดผ่าน route ของโครงการ ไม่เปิดเผย path ในเครื่อง"
} : {
  eyebrow: "Milestone 6E / auditable review overlay",
  title: "Review, correction and certification queue",
  body: "Corrections are append-only overlays. Automatic results and original evidence remain immutable.",
  scope: "Scope",
  reviewRevision: "Review revision",
  progress: "Review progress",
  coverage: "Coverage",
  queue: "Event queue",
  evidence: "Event evidence",
  selectEvent: "Select an event to inspect evidence and corrections.",
  pts: "PTS",
  origin: "Origin",
  automatic: "Automatic",
  humanAdded: "Human added",
  line: "Counting line",
  direction: "Direction",
  classLabel: "Class",
  status: "Status",
  filter: "Filter status",
  all: "All",
  unreviewed: "Unreviewed",
  confirmed: "Confirmed",
  corrected: "Corrected",
  rejected: "False positive",
  duplicate: "Duplicate",
  unscorable: "Unscorable",
  reason: "Recorded reason",
  reasonPlaceholder: "State what the evidence supports",
  confirm: "Confirm event",
  reject: "Reject false positive",
  changeClass: "Save class correction",
  changeDirection: "Save direction correction",
  changeLine: "Save counting-line correction",
  adjustTimestamp: "Save timestamp correction",
  markDuplicate: "Mark duplicate",
  duplicateTarget: "Duplicate-of event",
  timestamp: "Crossing timestamp",
  markUnscorable: "Mark unscorable",
  addNote: "Add note",
  reverse: "Reverse latest action",
  addMissed: "Add missed event",
  cancel: "Cancel",
  missedPts: "Event PTS",
  missedLine: "Event counting line",
  evidenceReference: "Evidence reference",
  manualNote: "Operator observation",
  add: "Add",
  automaticEvent: "Automatic event",
  noEvidence: "No evidence details are available.",
  seek: "Video seek point",
  actions: "Append-only actions",
  noActions: "No actions yet.",
  reconcile: "Reconciliation",
  pass: "Passed",
  fail: "Failed",
  unresolved: "Unreviewed",
  conflicts: "Recorded conflicts",
  complete: "Complete review",
  certification: "Certification",
  certify: "Certify this revision",
  certified: "Certified",
  certificationDisclosure: "Certification covers the selected scope and does not claim perfect detector accuracy.",
  export: "Export certified result",
  csv: "CSV",
  xlsx: "XLSX",
  json: "Audit JSON",
  exportReady: "Artifact created",
  stale: "Result is stale. Editing, certification and export are blocked.",
  loading: "Loading review queue",
  error: "Review queue could not be loaded.",
  retry: "Retry",
  actionSaved: "Action saved",
  completed: "Review completed",
  certCurrent: "Current certification status",
  artifact: "Output artifact",
  download: "Download",
  projectionRevision: "Projection revision",
  previous: "Previous",
  next: "Next",
  refreshQueue: "Refresh queue",
  conflict: "The review changed. Refresh the queue before retrying.",
  sourceStatusWarning: "This historical artifact was produced from a non-current certification; download remains available under the history policy.",
  exportHistory: "Export history",
  sourceEvidenceDisclosure: "Video opens through the project-scoped route; private local paths are not exposed."
};

function reviewError(cause: unknown, fallback: string, conflict: string): string {
  if (!(cause instanceof ApiError)) return fallback;
  return cause.code === "REVIEW_REVISION_CONFLICT" ? conflict : cause.message;
}

function isReviewRevisionConflict(cause: unknown): boolean {
  return cause instanceof ApiError && cause.status === 409 && cause.code === "REVIEW_REVISION_CONFLICT";
}

export function ReviewWorkspace({ projectId, runId, language, stale, onRefresh }: ReviewWorkspaceProps) {
  const text = labels(language);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [session, setSession] = useState<ReviewSession | null>(null);
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [actions, setActions] = useState<ReviewItem[]>([]);
  const [progress, setProgress] = useState<ReviewItem | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [evidence, setEvidence] = useState<ReviewItem | null>(null);
  const [filter, setFilter] = useState<ReviewFilter>("ALL");
  const [classValue, setClassValue] = useState("UNKNOWN");
  const [directionValue, setDirectionValue] = useState("A_TO_B");
  const [lineValue, setLineValue] = useState("line_main");
  const [timestampValue, setTimestampValue] = useState("");
  const [duplicateTarget, setDuplicateTarget] = useState("");
  const [reason, setReason] = useState("");
  const [addMode, setAddMode] = useState(false);
  const [addPts, setAddPts] = useState("");
  const [addLine, setAddLine] = useState("line_main");
  const [addClass, setAddClass] = useState("UNKNOWN");
  const [addDirection, setAddDirection] = useState("A_TO_B");
  const [addEvidence, setAddEvidence] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [certification, setCertification] = useState<ReviewItem | null>(null);
  const [exports, setExports] = useState<ReviewItem[]>([]);
  const [artifact, setArtifact] = useState<ReviewItem | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const [queueOffset, setQueueOffset] = useState(0);
  const [conflictDetected, setConflictDetected] = useState(false);

  const loadSession = useCallback(async (sessionId: string, nextFilter: ReviewFilter = "ALL", nextOffset = 0) => {
    const [nextSession, nextQueue, nextProgress, nextActions] = await Promise.all([
      api.getReviewSession(sessionId),
      api.getReviewQueue(sessionId, {
        limit: 50,
        offset: nextOffset,
        order_by: "timestamp",
        review_status: nextFilter === "ALL" ? undefined : nextFilter
      }),
      api.getReviewProgress(sessionId),
      api.listReviewActions(sessionId)
    ]);
    setSession(nextSession);
    setQueue(nextQueue);
    setQueueOffset(nextOffset);
    setProgress(nextProgress);
    setActions(nextActions);
    setConflictDetected(false);
    setSelectedId((current) => nextQueue.items.some((item) => String(item.reviewed_event_id) === current)
      ? current
      : String(nextQueue.items[0]?.reviewed_event_id ?? ""));
    const certifications = await api.listReviewCertifications(projectId);
    const currentCertification = certifications.find((item) => String(item.review_session_id) === sessionId);
    setCertification(currentCertification ?? null);
    if (currentCertification) {
      setExports(await api.listCertifiedExports(String(currentCertification.id)));
    } else {
      setExports([]);
    }
    setState("ready");
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      setState("loading");
      setError("");
      setMessage("");
      try {
        const sessions = await api.listReviewSessions(projectId);
        let current = sessions.find((item) => item.processing_run_id === runId && item.review_scope_type === "FULL_RESULT" && item.review_status !== "SUPERSEDED");
        if (!current) {
          current = await api.createReviewSession(projectId, {
            processing_run_id: runId,
            review_scope_type: "FULL_RESULT",
            created_by: "operator"
          });
        }
        if (cancelled) return;
        await loadSession(current.id, "ALL");
      } catch (cause) {
        if (cancelled) return;
        setState("error");
        setConflictDetected(isReviewRevisionConflict(cause));
        setError(reviewError(cause, "review_queue_failed", text.conflict));
      }
    }
    void bootstrap();
    return () => { cancelled = true; };
  }, [loadSession, projectId, retryNonce, runId]);

  const selected = useMemo(
    () => queue?.items.find((item) => String(item.reviewed_event_id) === selectedId) ?? null,
    [queue, selectedId]
  );

  useEffect(() => {
    if (!selected) return;
    setClassValue(String(selected.effective_class ?? "UNKNOWN"));
    setDirectionValue(String(selected.effective_direction ?? "A_TO_B"));
    setLineValue(String(selected.effective_line_id ?? "line_main"));
    setTimestampValue(String(selected.effective_crossing_pts_ms ?? ""));
    setAddLine(String(selected.effective_line_id ?? "line_main"));
    setAddDirection(String(selected.effective_direction ?? "A_TO_B"));
    setAddPts(String(selected.effective_crossing_pts_ms ?? ""));
    const firstDuplicateCandidate = queue?.items.find((item) => String(item.origin) === "AUTOMATIC" && String(item.source_event_id ?? "") !== String(selected.source_event_id ?? ""));
    setDuplicateTarget((current) => current && queue?.items.some((item) => String(item.source_event_id ?? "") === current && String(item.source_event_id ?? "") !== String(selected.source_event_id ?? ""))
      ? current
      : String(firstDuplicateCandidate?.source_event_id ?? ""));
  }, [queue, selected]);

  async function refresh(nextFilter = filter, nextOffset = 0) {
    if (!session) return;
    setBusy(true);
    setError("");
    setConflictDetected(false);
    try {
      await loadSession(session.id, nextFilter, nextOffset);
    } catch (cause) {
      setConflictDetected(isReviewRevisionConflict(cause));
      setError(reviewError(cause, "review_queue_failed", text.conflict));
    } finally {
      setBusy(false);
    }
  }

  async function appendAction(actionType: string, payload: Record<string, unknown> = {}, target = selected) {
    if (!session || (actionType !== "ADD_MISSED_EVENT" && !target?.source_event_id)) return;
    setBusy(true);
    setError("");
    setConflictDetected(false);
    try {
      const action = await api.appendScopedReviewAction(session.id, {
        action_type: actionType,
        target_event_id: target?.source_event_id ? String(target.source_event_id) : undefined,
        payload,
        reason_code: reason.trim() || (actionType === "CONFIRM_EVENT" ? "reviewer_confirmed_against_evidence" : undefined),
        comment: actionType === "ADD_NOTE" ? reason.trim() : undefined,
        reviewer_id: "operator",
        expected_review_revision: Number(session.review_revision),
        client_request_id: `ui-${actionType}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      });
      setMessage(`${text.actionSaved}: ${String(action.action_type)}`);
      setReason("");
      setAddMode(false);
      await loadSession(session.id, filter);
    } catch (cause) {
      setConflictDetected(isReviewRevisionConflict(cause));
      setError(reviewError(cause, "review_action_failed", text.conflict));
    } finally {
      setBusy(false);
    }
  }

  async function reverseLatest() {
    if (!session || !selected?.source_event_id) return;
    const targetActions = actions.filter((item) => String(item.target_event_id ?? "") === String(selected.source_event_id));
    const latest = targetActions[targetActions.length - 1];
    if (!latest) {
      setMessage(text.noActions);
      return;
    }
    await appendAction("REVERSE_ACTION", { reverses_action_id: String(latest.id) }, selected);
  }

  async function loadEvidence() {
    if (!session || !selected) return;
    setBusy(true);
    try {
      setEvidence(await api.getReviewEvidence(session.id, String(selected.source_event_id ?? selected.reviewed_event_id)));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "evidence_unavailable");
    } finally {
      setBusy(false);
    }
  }

  async function completeReview() {
    if (!session) return;
    setBusy(true);
    setConflictDetected(false);
    try {
      await api.completeReviewSession(session.id, { completed_by: "operator", expected_review_revision: session.review_revision });
      setMessage(text.completed);
      await refresh(filter);
      onRefresh();
    } catch (cause) {
      setConflictDetected(isReviewRevisionConflict(cause));
      setError(reviewError(cause, "review_completion_failed", text.conflict));
    } finally {
      setBusy(false);
    }
  }

  async function certifyReview() {
    if (!session) return;
    setBusy(true);
    try {
      const result = await api.createReviewCertification(session.id, {
        certified_by: "operator",
        qualification_disclosure: text.certificationDisclosure,
        rights_disclosure: "Rights status is operator-supplied and is not inferred by this application."
      });
      setCertification(result);
      setMessage(text.certified);
      onRefresh();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "certification_failed");
    } finally {
      setBusy(false);
    }
  }

  async function exportCertified(format: "CSV" | "XLSX" | "JSON") {
    if (!certification) return;
    setBusy(true);
    try {
      const result = await api.requestCertifiedExport(String(certification.id), {
        format,
        language,
        created_by: "operator",
        client_request_id: `ui-export-${format}-${Date.now()}`
      });
      setArtifact(result.artifact as ReviewItem | null);
      setExports((current) => [result, ...current.filter((item) => String(item.id) !== String(result.id))]);
      setMessage(`${text.exportReady}: ${format}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "export_failed");
    } finally {
      setBusy(false);
    }
  }

  if (!runId) return null;
  if (state === "loading") {
    return <section className="review-workspace review-workspace-state" aria-live="polite"><strong>{text.loading}</strong></section>;
  }
  if (state === "error" || !session || !queue) {
    return (
      <section className="review-workspace review-workspace-state" role="alert">
        <strong>{text.error}</strong><span>{error || text.error}</span>
        <button className="secondary" onClick={() => setRetryNonce((value) => value + 1)}>{text.retry}</button>
      </section>
    );
  }

  const reconciliation = (progress?.reconciliation as ReviewItem | undefined) ?? {};
  const sessionProgress = (session.progress as ReviewItem | undefined) ?? {};
  const sessionStale = String(session.stale_status) === "STALE";
  const unreviewed = Number(progress?.unreviewed_count ?? sessionProgress.unreviewed_count ?? 0);
  const coverage = Number(progress?.review_coverage_percent ?? sessionProgress.review_coverage_percent ?? 0);
  const reviewComplete = session.review_status === "REVIEW_COMPLETE";
  const certCurrent = certification && String(certification.status) === "CERTIFIED";
  const canCertify = reviewComplete && String(reconciliation.status) === "PASSED" && !stale && !sessionStale;
  const selectedActions = selected?.source_event_id
    ? actions.filter((item) => String(item.target_event_id ?? "") === String(selected.source_event_id))
    : [];

  return (
    <section className="review-workspace" aria-labelledby="review-workspace-title">
      <div className="section-heading review-workspace-heading">
        <div>
          <p className="eyebrow">{text.eyebrow}</p>
          <h2 id="review-workspace-title">{text.title}</h2>
          <p className="review-workspace-body">{text.body}</p>
        </div>
        <ReviewBadge status={stale || sessionStale ? "STALE" : session.review_status} />
      </div>
      {(stale || sessionStale) && <div className="review-alert" role="alert"><X size={16} />{text.stale}</div>}
      {error && <div className="review-alert" role="alert"><span>{error}</span>{conflictDetected && <button className="ghost" onClick={() => void refresh(filter, 0)} disabled={busy}>{text.refreshQueue}</button>}</div>}
      {message && <div className="review-message" role="status">{message}</div>}

      <div className="review-audit-strip" aria-label={text.progress}>
        <span><small>{text.scope}</small><strong>{session.review_scope_type}</strong></span>
        <span><small>{text.reviewRevision}</small><strong className="mono">{session.review_revision}</strong></span>
        <span><small>{text.projectionRevision}</small><strong className="mono">{String(queue.reviewed_projection_revision_id)}</strong></span>
        <span><small>{text.coverage}</small><strong>{coverage.toFixed(0)}%</strong></span>
        <span><small>{text.unresolved}</small><strong>{unreviewed}</strong></span>
        <span><small>{text.reconcile}</small><strong>{String(reconciliation.status) === "PASSED" ? text.pass : text.fail}</strong></span>
        <span><small>{text.conflicts}</small><strong>{String(sessionProgress.conflict_count ?? 0)}</strong></span>
      </div>

      <div className="review-workspace-grid">
        <div className="review-queue-panel">
          <div className="section-heading compact">
            <h3>{text.queue}</h3>
            <label className="review-filter">{text.filter}
              <select value={filter} onChange={(event) => { const next = event.target.value as ReviewFilter; setFilter(next); void refresh(next); }}>
                <option value="ALL">{text.all}</option>
                <option value="UNREVIEWED">{text.unreviewed}</option>
                <option value="CONFIRMED">{text.confirmed}</option>
                <option value="CORRECTED">{text.corrected}</option>
                <option value="REJECTED_FALSE_POSITIVE">{text.rejected}</option>
                <option value="DUPLICATE_SUPPRESSED">{text.duplicate}</option>
                <option value="UNSCORABLE">{text.unscorable}</option>
              </select>
            </label>
          </div>
          <div className="review-queue-list" role="list" aria-label={text.queue}>
            {queue.items.length === 0 && <p className="review-empty">{text.noEvidence}</p>}
            {queue.items.map((item) => {
              const id = String(item.reviewed_event_id);
              return (
                <button
                  key={id}
                  className={`review-queue-row ${id === selectedId ? "selected" : ""}`}
                  onClick={() => { setSelectedId(id); setEvidence(null); }}
                  aria-pressed={id === selectedId}
                  role="listitem"
                >
                  <span className="review-queue-time mono">{Number(item.effective_crossing_pts_ms ?? 0).toLocaleString()} ms</span>
                  <span className="review-queue-main"><strong>{String(item.effective_line_name || item.effective_line_id)}</strong><span>{String(item.effective_direction)} · {String(item.effective_class)}</span></span>
                  <ReviewBadge status={String(item.review_status)} />
                  <ChevronRight size={16} aria-hidden="true" />
                </button>
              );
            })}
          </div>
          <div className="review-pagination" aria-label={text.queue}>
            <button className="ghost" disabled={busy || queueOffset === 0} onClick={() => void refresh(filter, Math.max(0, queueOffset - queue.limit))}>{text.previous}</button>
            <span className="mono">{queue.total === 0 ? 0 : queueOffset + 1}-{Math.min(queueOffset + queue.items.length, queue.total)} / {queue.total}</span>
            <button className="ghost" disabled={busy || queueOffset + queue.items.length >= queue.total} onClick={() => void refresh(filter, queueOffset + queue.limit)}>{text.next}</button>
          </div>
          <button className="secondary review-add-button" disabled={busy || stale || sessionStale} onClick={() => setAddMode(true)}><StickyNote size={16} />{text.addMissed}</button>
        </div>

        <div className="review-detail-panel">
          {!selected ? <p className="review-empty">{text.selectEvent}</p> : (
            <>
              <div className="review-detail-heading">
                <div><p className="eyebrow">{text.evidence}</p><h3>{String(selected.effective_line_name || selected.effective_line_id)}</h3></div>
                <ReviewBadge status={String(selected.review_status)} />
              </div>
              <dl className="review-detail-meta">
                <dt>{text.pts}</dt><dd className="mono">{String(selected.effective_crossing_pts_ms)} ms</dd>
                <dt>{text.origin}</dt><dd>{String(selected.origin) === "HUMAN_ADDED" ? text.humanAdded : text.automatic}</dd>
                <dt>{text.direction}</dt><dd>{String(selected.effective_direction)}</dd>
                <dt>{text.classLabel}</dt><dd>{String(selected.effective_class)}</dd>
                <dt>{text.status}</dt><dd>{String(selected.review_status)}</dd>
              </dl>
              <div className="review-action-row">
                <button className="secondary" disabled={busy || stale || sessionStale || !selected.source_event_id} onClick={() => void loadEvidence()}>{text.evidence}</button>
                <button className="ghost" disabled={busy || stale || sessionStale || !selected.source_event_id} onClick={() => void reverseLatest()}><RotateCcw size={16} />{text.reverse}</button>
              </div>
              {evidence && <div className="review-evidence-card">
                <strong>{text.automaticEvent}</strong>
                <span>{text.seek}: <b className="mono">{String((evidence.source_video as ReviewItem | undefined)?.seek_pts_ms ?? selected.effective_crossing_pts_ms)} ms</b></span>
                <span>{text.sourceEvidenceDisclosure}</span>
              </div>}
              <div className="review-correction-form">
                <label>{text.classLabel}
                  <select value={classValue} disabled={busy || stale || sessionStale} onChange={(event) => setClassValue(event.target.value)}>
                    {CLASS_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
                  </select>
                </label>
                <label>{text.direction}
                  <select value={directionValue} disabled={busy || stale || sessionStale} onChange={(event) => setDirectionValue(event.target.value)}>
                    <option value="A_TO_B">A_TO_B</option><option value="B_TO_A">B_TO_A</option>
                  </select>
                </label>
                <label>{text.line}<input value={lineValue} disabled={busy || stale || sessionStale} onChange={(event) => setLineValue(event.target.value)} /></label>
                <label>{text.timestamp}<input type="number" min="0" value={timestampValue} disabled={busy || stale || sessionStale} onChange={(event) => setTimestampValue(event.target.value)} /></label>
                <label>{text.duplicateTarget}
                  <select value={duplicateTarget} disabled={busy || stale || sessionStale || !selected.source_event_id} onChange={(event) => setDuplicateTarget(event.target.value)}>
                    <option value="">—</option>
                    {queue.items.filter((item) => String(item.origin) === "AUTOMATIC" && String(item.source_event_id ?? "") !== String(selected.source_event_id ?? "")).map((item) => <option key={String(item.source_event_id)} value={String(item.source_event_id)}>{String(item.source_event_id)} · {String(item.effective_line_name || item.effective_line_id)}</option>)}
                  </select>
                </label>
                <label className="review-reason">{text.reason}<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder={text.reasonPlaceholder} disabled={busy || stale || sessionStale} /></label>
                <div className="review-action-row">
                  <button className="primary" disabled={busy || stale || sessionStale || !reason.trim()} onClick={() => void appendAction("CONFIRM_EVENT")}><Check size={16} />{text.confirm}</button>
                  <button className="secondary destructive" disabled={busy || stale || sessionStale || !reason.trim()} onClick={() => void appendAction("REJECT_FALSE_POSITIVE")}><X size={16} />{text.reject}</button>
                  <button className="secondary" disabled={busy || stale || sessionStale || !reason.trim()} onClick={() => void appendAction("CHANGE_CLASS", { engineering_class: classValue })}>{text.changeClass}</button>
                  <button className="secondary" disabled={busy || stale || sessionStale || !reason.trim()} onClick={() => void appendAction("CHANGE_DIRECTION", { direction: directionValue })}>{text.changeDirection}</button>
                  <button className="secondary" disabled={busy || stale || sessionStale || !reason.trim() || !lineValue.trim()} onClick={() => void appendAction("CHANGE_LINE", { counting_line_id: lineValue.trim() })}>{text.changeLine}</button>
                  <button className="secondary" disabled={busy || stale || sessionStale || !reason.trim() || !timestampValue} onClick={() => void appendAction("ADJUST_TIMESTAMP", { crossing_pts_ms: Number(timestampValue) })}>{text.adjustTimestamp}</button>
                  <button className="secondary" disabled={busy || stale || sessionStale || !reason.trim() || !duplicateTarget} onClick={() => void appendAction("MARK_DUPLICATE", { duplicate_of: duplicateTarget })}>{text.markDuplicate}</button>
                  <button className="ghost" disabled={busy || stale || sessionStale || !reason.trim()} onClick={() => void appendAction("MARK_UNSCORABLE")}>{text.markUnscorable}</button>
                  <button className="ghost" disabled={busy || stale || sessionStale || !reason.trim()} onClick={() => void appendAction("ADD_NOTE", { note: reason.trim() })}><StickyNote size={16} />{text.addNote}</button>
                </div>
              </div>
              <div className="review-action-history">
                <div className="section-heading compact"><h4>{text.actions}</h4><History size={16} aria-hidden="true" /></div>
                {selectedActions.length === 0 ? <p>{text.noActions}</p> : selectedActions.map((item) => <div className="review-history-row" key={String(item.id)}><span className="mono">#{String(item.action_order)}</span><span>{String(item.action_type)}</span><span>{String(item.reason_code || item.comment || "—")}</span></div>)}
              </div>
            </>
          )}
        </div>
      </div>

      {addMode && <div className="review-add-panel">
        <div className="section-heading compact"><div><h3>{text.addMissed}</h3><p>{text.manualNote}</p></div><button className="icon-button" aria-label={text.cancel} onClick={() => setAddMode(false)}><X size={16} /></button></div>
        <div className="review-add-grid">
          <label>{text.missedPts}<input type="number" value={addPts} onChange={(event) => setAddPts(event.target.value)} /></label>
          <label>{text.missedLine}<input value={addLine} onChange={(event) => setAddLine(event.target.value)} /></label>
          <label>{text.direction}<select value={addDirection} onChange={(event) => setAddDirection(event.target.value)}><option value="A_TO_B">A_TO_B</option><option value="B_TO_A">B_TO_A</option></select></label>
          <label>{text.classLabel}<select value={addClass} onChange={(event) => setAddClass(event.target.value)}>{CLASS_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}</select></label>
          <label>{text.evidenceReference}<input value={addEvidence} onChange={(event) => setAddEvidence(event.target.value)} placeholder="operator-observed" /></label>
          <label>{text.reason}<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder={text.reasonPlaceholder} /></label>
        </div>
        <div className="review-action-row"><button className="primary" disabled={busy || stale || sessionStale || !addPts || !addLine || !addEvidence || !reason.trim()} onClick={() => void appendAction("ADD_MISSED_EVENT", { counting_line_id: addLine, direction: addDirection, crossing_pts_ms: Number(addPts), engineering_class: addClass, evidence_reference: addEvidence, manual_observation_note: reason.trim(), reason: reason.trim() }, null)}>{text.add}</button><button className="ghost" onClick={() => setAddMode(false)}>{text.cancel}</button></div>
      </div>}

      <div className="review-certification-panel">
        <div><p className="eyebrow">{text.certification}</p><strong>{certification ? `${text.certCurrent}: ${String(certification.status)}` : text.certificationDisclosure}</strong></div>
        <div className="review-certification-actions">
          <button className="secondary" disabled={busy || stale || sessionStale || reviewComplete || unreviewed > 0 || String(reconciliation.status) !== "PASSED"} onClick={() => void completeReview()}><FileCheck2 size={16} />{text.complete}</button>
          <button className="primary" disabled={busy || !canCertify || Boolean(certCurrent)} onClick={() => void certifyReview()}><ShieldCheck size={16} />{text.certify}</button>
          {certCurrent && <><button className="secondary" disabled={busy} onClick={() => void exportCertified("CSV")}><Download size={16} />{text.csv}</button><button className="secondary" disabled={busy} onClick={() => void exportCertified("XLSX")}>{text.xlsx}</button><button className="secondary" disabled={busy} onClick={() => void exportCertified("JSON")}>{text.json}</button></>}
        </div>
        {certification && !certCurrent && <div className="review-source-warning" role="alert">{text.sourceStatusWarning}</div>}
        {artifact && <div className="review-artifact"><span>{text.artifact}: <strong>{String(artifact.safe_filename)}</strong></span><a href={api.exportArtifactDownloadUrl(String(artifact.artifact_id))} download>{text.download}</a></div>}
        {exports.length > 0 && <div className="review-export-history">
          <strong>{text.exportHistory}</strong>
          {exports.map((item) => {
            const exportArtifact = item.artifact as ReviewItem | null;
            const effectiveStatus = String(item.effective_export_status ?? item.status ?? "UNKNOWN");
            return <div className="review-export-row" key={String(item.id)}>
              <span>{String(item.format)} · <strong>{effectiveStatus}</strong></span>
              {effectiveStatus !== "COMPLETED" && <span className="review-source-warning">{text.sourceStatusWarning}</span>}
              {Boolean(exportArtifact?.artifact_id) && <a href={api.exportArtifactDownloadUrl(String(exportArtifact?.artifact_id))} download>{text.download}</a>}
            </div>;
          })}
        </div>}
      </div>
    </section>
  );
}

function ReviewBadge({ status }: { status: string }) {
  const tone = ["CONFIRMED", "CORRECTED", "REVIEW_COMPLETE", "CERTIFIED", "PASSED", "CURRENT"].includes(status) ? "success" : ["STALE", "STALE_SOURCE", "REVOKED", "REVOKED_SOURCE", "UNREVIEWED", "BLOCKED", "REJECTED_FALSE_POSITIVE", "DUPLICATE_SUPPRESSED", "UNSCORABLE", "FAILED"].includes(status) ? "warning" : "info";
  return <span className={`badge ${tone}`}><span aria-hidden="true" />{status}</span>;
}
